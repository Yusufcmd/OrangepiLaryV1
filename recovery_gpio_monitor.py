#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GPIO PWM Monitor - WiFi Yapılandırma
GPIO 76'dan gelen PWM sinyalini okur:
- %75 duty cycle: Recovery moduna geçer (factoryctl ile AP modu)
- %50 duty cycle: AP7 moduna geçer
- %25 duty cycle: QR okuma moduna geçer
"""

import os
import sys
import time
import signal
import subprocess
import threading
import logging
from logging.handlers import RotatingFileHandler
from typing import Optional
import cv2
from pyzbar import pyzbar
import camera_lock

try:
    import gpiod
except ImportError as e:
    raise SystemExit("gpiod modülü bulunamadı. 'sudo apt install -y python3-libgpiod gpiod'") from e


# ==================== LOGLAMA YAPILANDIRMA ====================
LOG_FILE = "/home/rise/clary/recoverylog/recovery.log"
LOG_MAX_SIZE = 10 * 1024 * 1024  # 10MB
LOG_BACKUP_COUNT = 5

# Logger oluştur
logger = logging.getLogger("PWM_Monitor")
logger.setLevel(logging.DEBUG)

# Log formatı
log_formatter = logging.Formatter(
    '%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

# Konsol handler (stdout)
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(log_formatter)
logger.addHandler(console_handler)

# Dosya handler (rotating file)
try:
    # Log dizinini oluştur
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)

    file_handler = RotatingFileHandler(
        LOG_FILE,
        maxBytes=LOG_MAX_SIZE,
        backupCount=LOG_BACKUP_COUNT
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(log_formatter)
    logger.addHandler(file_handler)
    logger.info(f"Log dosyası: {LOG_FILE}")
except (PermissionError, OSError) as e:
    logger.warning(f"/var/log'a yazılamadı, yerel log kullanılıyor")

# ==================== YAPILANDIRMA ====================
GPIO_CHIP = "/dev/gpiochip1"
GPIO_OFFSET = 76  # PWM sinyali gelecek pin (ESP8266 D6 → Orange Pi PI3 offset 76)
ACTIVE_HIGH = True

# PWM ölçüm parametreleri
PWM_SAMPLE_COUNT = 50  # PWM ölçümü için örnek sayısı
PWM_POLL_INTERVAL = 0.001  # 1ms polling (1kHz örnekleme)
PWM_TOLERANCE = 10  # %10 tolerans (örn: 75±10 = 65-85%)

# Duty cycle hedefleri
DUTY_RECOVERY = 75  # %75 ± tolerans → Recovery modu
DUTY_AP7_MODE = 50  # %50 ± tolerans → AP7 modu
DUTY_QR_MODE = 25   # %25 ± tolerans → QR okuma modu

# Recovery için factoryctl
FACTORYCTL_BIN = "/usr/local/sbin/factoryctl"
FACTORY_DIR = "/opt/factory"


# WiFi script yolları
AP_MODE_SCRIPT = "/opt/lscope/bin/ap_mode.sh"
STA_MODE_SCRIPT = "/opt/lscope/bin/sta_mode.sh"
AP7_MODE_SCRIPT = "/opt/lscope/bin/ap7_mode.sh"  # %50 duty için tetiklenecek script

# LED kontrolü (PI2 pini)
GPIO_LED_CHIP = "/dev/gpiochip1"
GPIO_LED_OFFSET = 258  # PI2 pini
LED_BLINK_INTERVAL = 0.3

# Kamera konfigürasyonu
CAMERA_DEVICE = 0  # /dev/video0
QR_COOLDOWN = 60  # QR okuma modu cooldown (saniye)


# ==================== LED KONTROLÜ ====================
_led_line: Optional[object] = None
_led_chip: Optional[object] = None
_led_blink_stop = threading.Event()
_led_blink_thread: Optional[threading.Thread] = None

def setup_led_gpio():
    """LED GPIO'sunu hazırla"""
    global _led_line, _led_chip
    try:
        _led_chip = gpiod.Chip(GPIO_LED_CHIP)
        _led_line = _led_chip.get_line(GPIO_LED_OFFSET)
        _led_line.request(consumer="pwm-monitor-led", type=gpiod.LINE_REQ_DIR_OUT, default_vals=[0])
        logger.info(f"✓ LED GPIO (PI2) hazır: {GPIO_LED_CHIP}:{GPIO_LED_OFFSET}")

        # LED'i başlangıçta aç (sürekli yanma modunda)
        set_led(True)
        logger.info("✓ LED sürekli yanma modunda")

        return True
    except Exception as e:
        logger.warning(f"⚠ LED GPIO açılamadı: {e}")
        return False

def set_led(state: bool):
    """LED'i aç/kapa"""
    global _led_line
    if _led_line:
        try:
            _led_line.set_value(1 if state else 0)
        except Exception as e:
            logger.debug(f"LED set hatası: {e}")

def cleanup_led_gpio():
    """LED GPIO kaynaklarını temizle"""
    global _led_line, _led_chip
    try:
        if _led_line:
            _led_line.set_value(0)
            _led_line.release()
            _led_line = None
    except Exception as e:
        logger.debug(f"LED cleanup hatası: {e}")
    try:
        if _led_chip:
            _led_chip.close()
            _led_chip = None
    except Exception as e:
        logger.debug(f"LED chip cleanup hatası: {e}")

def led_blink_loop():
    """LED yanıp sönme döngüsü"""
    while not _led_blink_stop.is_set():
        set_led(True)
        time.sleep(LED_BLINK_INTERVAL)
        if _led_blink_stop.is_set():
            break
        set_led(False)
        time.sleep(LED_BLINK_INTERVAL)
    set_led(False)

def start_led_blink():
    """LED yanıp sönmeyi başlat"""
    global _led_blink_thread, _led_blink_stop
    _led_blink_stop.clear()
    _led_blink_thread = threading.Thread(target=led_blink_loop, daemon=True)
    _led_blink_thread.start()
    logger.debug("LED yanıp sönme başladı")

def stop_led_blink():
    """LED yanıp sönmeyi durdur ve sürekli yanma moduna geç"""
    global _led_blink_stop
    _led_blink_stop.set()
    if _led_blink_thread:
        _led_blink_thread.join(timeout=1.0)
    # LED'i tekrar sürekli yanık duruma getir
    set_led(True)
    logger.debug("LED yanıp sönme durduruldu - sürekli yanma moduna geçildi")

# ==================== PWM ÖLÇÜMÜ ====================
def measure_pwm_duty_cycle(line, sample_count=PWM_SAMPLE_COUNT):
    """PWM duty cycle'ı ölç (0-100 arası değer döner)"""
    high_count = 0
    total_count = 0

    for _ in range(sample_count):
        try:
            value = line.get_value()
            is_high = (value == 1) if ACTIVE_HIGH else (value == 0)
            if is_high:
                high_count += 1
            total_count += 1
            time.sleep(PWM_POLL_INTERVAL)
        except Exception as e:
            logger.error(f"PWM okuma hatası: {e}")
            return None

    if total_count == 0:
        return None

    duty_cycle = (high_count / total_count) * 100
    return duty_cycle

def is_duty_in_range(duty, target, tolerance=PWM_TOLERANCE):
    """Duty cycle hedef aralıkta mı kontrol et"""
    if duty is None:
        return False
    return (target - tolerance) <= duty <= (target + tolerance)

# ==================== QR OKUMA VE WiFi YAPILANDIRMA ====================
def parse_qr_code(qr_data):
    """QR kod verisini parse et ve WiFi yapılandırma bilgilerini döndür"""
    logger.info(f"QR kod verisi: {qr_data}")

    # AP Mode QR formatı: "APMODE5gch36" veya "APMODE2.4gch6"
    if qr_data.startswith("APMODE"):
        mode_type = "ap"
        remaining = qr_data[6:]  # "APMODE" kısmını çıkar

        # Band ve kanal bilgisini ayıkla
        if remaining.startswith("5g"):
            band = "5"
            channel = remaining.replace("5gch", "").replace("ch", "")
        elif remaining.startswith("2.4g"):
            band = "2.4"
            channel = remaining.replace("2.4gch", "").replace("ch", "")
        else:
            logger.error(f"Geçersiz AP mode formatı: {qr_data}")
            return None

        logger.info(f"AP Mode algılandı - Band: {band}GHz, Kanal: {channel}")
        return {
            "mode": "ap",
            "band": band,
            "channel": channel
        }

    # WiFi STA Mode QR formatı: "WIFI:T:WPA;S:ssid;P:password;H:false;;"
    elif qr_data.startswith("WIFI:"):
        mode_type = "sta"

        # Parse WiFi QR format
        parts = qr_data.split(";")
        ssid = None
        password = None
        auth = "WPA"

        for part in parts:
            if part.startswith("S:"):
                ssid = part[2:].replace(r'\;', ';').replace(r'\,', ',').replace(r'\:', ':').replace(r'\\', '\\')
            elif part.startswith("P:"):
                password = part[2:].replace(r'\;', ';').replace(r'\,', ',').replace(r'\:', ':').replace(r'\\', '\\')
            elif part.startswith("T:"):
                auth = part[2:]

        if not ssid:
            logger.error(f"SSID bulunamadı: {qr_data}")
            return None

        logger.info(f"STA Mode algılandı - SSID: {ssid}, Auth: {auth}")
        return {
            "mode": "sta",
            "ssid": ssid,
            "password": password if password else "",
            "auth": auth
        }

    else:
        logger.error(f"Bilinmeyen QR format: {qr_data}")
        return None

def update_wifi_script(config):
    """WiFi yapılandırma scriptini güncelle ve çalıştır"""
    mode = config.get("mode")

    if mode == "ap":
        # AP Mode script güncelleme
        band = config.get("band")
        channel = config.get("channel")

        logger.info(f"AP Mode script güncelleniyor: Band={band}GHz, Kanal={channel}")

        # ap_mode.sh scriptini güncelle
        if not os.path.exists(AP_MODE_SCRIPT):
            logger.error(f"AP mode script bulunamadı: {AP_MODE_SCRIPT}")
            return False

        try:
            # Script içeriğini oku
            with open(AP_MODE_SCRIPT, 'r') as f:
                script_content = f.read()

            # Band ve kanal değişkenlerini güncelle
            # Örnek: BAND="5" ve CHANNEL="36"
            import re

            # BAND değişkenini güncelle
            if band == "5":
                script_content = re.sub(r'BAND=["\']\d+\.?\d*["\']', f'BAND="5"', script_content)
                script_content = re.sub(r'BAND=\d+\.?\d*', 'BAND=5', script_content)
            else:
                script_content = re.sub(r'BAND=["\']\d+\.?\d*["\']', f'BAND="2.4"', script_content)
                script_content = re.sub(r'BAND=\d+\.?\d*', 'BAND=2.4', script_content)

            # CHANNEL değişkenini güncelle
            script_content = re.sub(r'CHANNEL=["\']\d+["\']', f'CHANNEL="{channel}"', script_content)
            script_content = re.sub(r'CHANNEL=\d+', f'CHANNEL={channel}', script_content)

            # Güncellenmiş scripti yaz
            with open(AP_MODE_SCRIPT, 'w') as f:
                f.write(script_content)

            logger.info(f"✓ AP mode script güncellendi: {AP_MODE_SCRIPT}")

            # Scripti çalıştır
            logger.info("AP mode script çalıştırılıyor...")
            result = subprocess.run(["sudo", "bash", AP_MODE_SCRIPT],
                                  capture_output=True, text=True, timeout=45)

            if result.returncode == 0:
                logger.info("✓ AP mode başarıyla yapılandırıldı")
                if result.stdout:
                    logger.debug(f"Script çıktısı:\n{result.stdout}")
                return True
            else:
                logger.error(f"✗ AP mode hatası: {result.stderr or result.stdout}")
                return False

        except Exception as e:
            logger.error(f"AP mode script güncelleme hatası: {e}", exc_info=True)
            return False

    elif mode == "sta":
        # STA Mode script güncelleme
        ssid = config.get("ssid")
        password = config.get("password")

        logger.info(f"STA Mode script güncelleniyor: SSID={ssid}")

        # sta_mode.sh scriptini güncelle
        if not os.path.exists(STA_MODE_SCRIPT):
            logger.error(f"STA mode script bulunamadı: {STA_MODE_SCRIPT}")
            return False

        try:
            # Script içeriğini oku
            with open(STA_MODE_SCRIPT, 'r') as f:
                script_content = f.read()

            # SSID ve PASSWORD değişkenlerini güncelle
            import re

            # SSID değişkenini güncelle
            script_content = re.sub(r'SSID=["\'].*?["\']', f'SSID="{ssid}"', script_content)
            script_content = re.sub(r'WIFI_SSID=["\'].*?["\']', f'WIFI_SSID="{ssid}"', script_content)

            # PASSWORD değişkenini güncelle
            script_content = re.sub(r'PASSWORD=["\'].*?["\']', f'PASSWORD="{password}"', script_content)
            script_content = re.sub(r'WIFI_PASSWORD=["\'].*?["\']', f'WIFI_PASSWORD="{password}"', script_content)
            script_content = re.sub(r'PSK=["\'].*?["\']', f'PSK="{password}"', script_content)

            # Güncellenmiş scripti yaz
            with open(STA_MODE_SCRIPT, 'w') as f:
                f.write(script_content)

            logger.info(f"✓ STA mode script güncellendi: {STA_MODE_SCRIPT}")

            # Scripti çalıştır
            logger.info("STA mode script çalıştırılıyor...")
            result = subprocess.run(["sudo", "bash", STA_MODE_SCRIPT],
                                  capture_output=True, text=True, timeout=45)

            if result.returncode == 0:
                logger.info("✓ STA mode başarıyla yapılandırıldı")
                if result.stdout:
                    logger.debug(f"Script çıktısı:\n{result.stdout}")
                return True
            else:
                logger.error(f"✗ STA mode hatası: {result.stderr or result.stdout}")
                return False

        except Exception as e:
            logger.error(f"STA mode script güncelleme hatası: {e}", exc_info=True)
            return False

    return False

def trigger_qr_mode():
    """QR okuma modunu tetikle ve kameradan QR kod oku"""
    logger.info("="*60)
    logger.info("QR OKUMA MODU TETIKLENDI")
    logger.info("="*60)

    start_led_blink()

    # Kamera kilidini al
    if not camera_lock.acquire_camera_lock():
        logger.error("Kamera kilidi alınamadı")
        stop_led_blink()
        return False

    cap = None
    try:
        # Kamerayı aç
        logger.info(f"Kamera açılıyor: /dev/video{CAMERA_DEVICE}")
        cap = cv2.VideoCapture(CAMERA_DEVICE)

        if not cap.isOpened():
            logger.error("Kamera açılamadı")
            camera_lock.release_camera_lock()
            stop_led_blink()
            return False

        logger.info("✓ Kamera başarıyla açıldı")
        logger.info("QR kod aranıyor...")

        # QR kod okuma döngüsü (maksimum 30 saniye)
        start_time = time.time()
        timeout = 30
        qr_found = False

        while (time.time() - start_time) < timeout and not qr_found:
            ret, frame = cap.read()

            if not ret:
                logger.warning("Frame okunamadı")
                time.sleep(0.1)
                continue

            # QR kod tara
            decoded_objects = pyzbar.decode(frame)

            if decoded_objects:
                for obj in decoded_objects:
                    qr_data = obj.data.decode('utf-8')
                    logger.info(f"✓ QR kod bulundu: {qr_data}")

                    # QR kodu parse et
                    config = parse_qr_code(qr_data)

                    if config:
                        # WiFi yapılandırmasını güncelle
                        success = update_wifi_script(config)

                        if success:
                            logger.info("✓ WiFi yapılandırması başarıyla güncellendi")
                            qr_found = True
                            break
                        else:
                            logger.error("✗ WiFi yapılandırması güncellenemedi")
                    else:
                        logger.error("✗ QR kod parse edilemedi")

            time.sleep(0.1)  # CPU kullanımını azalt

        if not qr_found:
            logger.warning(f"QR kod bulunamadı (timeout: {timeout}s)")
            camera_lock.release_camera_lock()
            stop_led_blink()
            return False

        camera_lock.release_camera_lock()
        stop_led_blink()
        return True

    except Exception as e:
        logger.error(f"QR okuma hatası: {e}", exc_info=True)
        camera_lock.release_camera_lock()
        stop_led_blink()
        return False
    finally:
        if cap is not None:
            cap.release()
            logger.info("Kamera kapatıldı")
        camera_lock.release_camera_lock()

# ==================== RECOVERY MODU ====================
def trigger_recovery():
    """Recovery modunu tetikle (factoryctl ile)"""
    logger.info("="*60)
    logger.info("RECOVERY MODU TETIKLENDI - AP MODUNA GEÇİLECEK!")
    logger.info("="*60)

    start_led_blink()

    try:
        if not os.path.exists(FACTORYCTL_BIN):
            logger.error(f"HATA: factoryctl bulunamadı: {FACTORYCTL_BIN}")
            stop_led_blink()
            return False

        if not os.path.exists(FACTORY_DIR):
            logger.error(f"HATA: Factory dizini bulunamadı: {FACTORY_DIR}")
            stop_led_blink()
            return False

        logger.info(f"✓ factoryctl bulundu: {FACTORYCTL_BIN}")
        logger.info(f"✓ Factory snapshot mevcut")

        # Manifest kontrol
        manifest_file = os.path.join(FACTORY_DIR, "MANIFEST.txt")
        if os.path.exists(manifest_file):
            with open(manifest_file, 'r') as f:
                manifest = f.read().strip()
                logger.debug(f"Factory manifest: {manifest}")

        logger.warning("!!! FACTORY RESTORE BAŞLIYOR - AP MODE !!!")

        time.sleep(2)

        logger.info("factoryctl restore çalıştırılıyor...")
        result = subprocess.run([FACTORYCTL_BIN, "restore", "-y", "--ap"],
                              capture_output=True, text=True)

        if result.returncode == 0:
            logger.info("✓ Factory restore tamamlandı.")
            if result.stdout:
                logger.debug(f"factoryctl çıktısı:\n{result.stdout}")

            stop_led_blink()

            # Recovery başarılı - Sistem yeniden başlatılıyor
            logger.info("="*60)
            logger.info("RECOVERY TAMAMLANDI - SİSTEM YENİDEN BAŞLATILIYOR...")
            logger.info("="*60)
            time.sleep(2)

            try:
                logger.info("Reboot komutu çalıştırılıyor...")
                subprocess.run(['sudo', 'reboot'], check=False)
                logger.info("✓ Reboot komutu gönderildi")
            except Exception as reboot_error:
                logger.error(f"Reboot komutu hatası: {reboot_error}")

            return True
        else:
            logger.error(f"factoryctl hatası: {result.stderr}")
            stop_led_blink()
            return False

    except Exception as e:
        logger.error(f"HATA: Recovery başarısız: {e}", exc_info=True)
        stop_led_blink()
        return False

# ==================== ANA DÖNGÜ ====================
def open_chip(path):
    """GPIO chip'i aç"""
    try:
        return gpiod.Chip(path, gpiod.Chip.OPEN_BY_PATH)
    except Exception as e:
        logger.debug(f"OPEN_BY_PATH hatası, standart yöntem deneniyor: {e}")
        return gpiod.Chip(path)

def request_input(chip, offset):
    """GPIO pinini input olarak ayarla"""
    line = chip.get_line(int(offset))

    # Eğer pin meşgulse, önce serbest bırakmayı dene
    try:
        line.request(consumer="pwm-monitor", type=gpiod.LINE_REQ_DIR_IN)
        return line
    except OSError as e:
        if e.errno == 16:  # Device or resource busy
            logger.warning(f"GPIO {offset} meşgul, serbest bırakılmaya çalışılıyor...")
            try:
                # Pin zaten başka bir consumer tarafından kullanılıyor
                # Önce o consumer'ı bulmaya çalış
                try:
                    line.release()
                except:
                    pass

                # Biraz bekle
                time.sleep(0.5)

                # Tekrar dene
                line = chip.get_line(int(offset))
                line.request(consumer="pwm-monitor", type=gpiod.LINE_REQ_DIR_IN)
                logger.info(f"✓ GPIO {offset} serbest bırakıldı ve yeniden ayarlandı")
                return line
            except OSError:
                # Hala meşgul - başka bir yöntem dene
                logger.warning("GPIO hala meşgul, alternatif yöntem deneniyor...")

                # Sistem genelinde GPIO kullanan işlemleri bul
                try:
                    result = subprocess.run(
                        ['lsof', f'/dev/gpiochip*'],
                        capture_output=True,
                        text=True,
                        timeout=3,
                        shell=False
                    )
                    if result.stdout:
                        logger.info(f"GPIO kullanan işlemler:\n{result.stdout}")
                except:
                    pass

                # gpioinfo ile pin durumunu kontrol et
                try:
                    result = subprocess.run(
                        ['gpioinfo', 'gpiochip1'],
                        capture_output=True,
                        text=True,
                        timeout=3
                    )
                    if result.stdout:
                        # Sadece bizim pinimizi göster
                        lines = result.stdout.split('\n')
                        for i, line_text in enumerate(lines):
                            if f'line {offset}:' in line_text or f'line  {offset}:' in line_text:
                                logger.info(f"GPIO {offset} durumu: {line_text}")
                                # Bir sonraki satırı da göster (detaylar)
                                if i + 1 < len(lines):
                                    logger.info(f"  {lines[i + 1]}")
                                break
                except FileNotFoundError:
                    logger.warning("gpioinfo komutu bulunamadı (gpiod paketi yükleyin)")
                except Exception as e:
                    logger.debug(f"gpioinfo hatası: {e}")

                raise OSError(f"GPIO {offset} meşgul ve serbest bırakılamıyor. "
                            f"Lütfen GPIO kullanan diğer işlemleri durdurun veya "
                            f"sistemi yeniden başlatın.") from e
        else:
            raise

def main():
    """Ana döngü"""
    logger.info("="*60)
    logger.info("PWM MONITOR - WiFi Yapılandırma")
    logger.info("="*60)
    logger.info(f"GPIO Chip: {GPIO_CHIP}")
    logger.info(f"GPIO Offset (Pin): {GPIO_OFFSET}")
    logger.info(f"PWM Ölçüm: {PWM_SAMPLE_COUNT} örnek, {PWM_TOLERANCE}% tolerans")
    logger.info(f"  - %{DUTY_RECOVERY}±{PWM_TOLERANCE} → Recovery Modu (factoryctl AP)")
    logger.info(f"  - %{DUTY_AP7_MODE}±3 → AP7 Modu")
    logger.info(f"  - %{DUTY_QR_MODE}±3 → QR Okuma Modu (WiFi Config)")
    logger.info("="*60)

    # Root kontrolü
    if os.geteuid() != 0:
        logger.error("UYARI: Bu script root olarak çalıştırılmalı (sudo)")
        sys.exit(1)

    # GPIO setup
    try:
        logger.debug("GPIO chip açılıyor...")
        chip = open_chip(GPIO_CHIP)
        line = request_input(chip, GPIO_OFFSET)
        logger.info(f"✓ GPIO {GPIO_OFFSET} hazır")
    except Exception as e:
        logger.error(f"HATA: GPIO açılamadı: {e}", exc_info=True)
        sys.exit(1)

    # LED setup
    logger.debug("LED GPIO yapılandırılıyor...")
    setup_led_gpio()

    # Signal handler
    stop_flag = False
    def signal_handler(sig, frame):
        nonlocal stop_flag
        logger.info("Durdurma sinyali alındı...")
        stop_flag = True

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    logger.info("İzleme başladı. Çıkmak için Ctrl+C...")

    last_trigger_time = 0
    TRIGGER_COOLDOWN = 30  # 30 saniye soğuma süresi (genel)

    # %50 duty için ayrı cooldown
    last_ap7_trigger_time = 0
    AP7_COOLDOWN = 60  # saniye
    AP7_TOLERANCE = 3  # %50 için ±3% tolerans

    # %25 duty için ayrı cooldown
    last_qr_trigger_time = 0
    QR_TOLERANCE = 3  # %25 için ±3% tolerans

    try:
        while not stop_flag:
            try:
                # PWM duty cycle ölç
                duty = measure_pwm_duty_cycle(line, PWM_SAMPLE_COUNT)

                if duty is not None:
                    current_time = time.time()

                    # Soğuma süresi kontrolü (genel)
                    if (current_time - last_trigger_time) < TRIGGER_COOLDOWN:
                        remaining = TRIGGER_COOLDOWN - (current_time - last_trigger_time)
                        logger.info(f"[{time.strftime('%H:%M:%S')}] Duty: {duty:.1f}% - Soğuma: {remaining:.0f}s")
                        time.sleep(1)
                        continue

                    # %50 duty → ap7_mode.sh (dar toleransla)
                    if abs(duty - 50.0) <= AP7_TOLERANCE:
                        if (current_time - last_ap7_trigger_time) >= AP7_COOLDOWN:
                            if os.path.exists(AP7_MODE_SCRIPT):
                                logger.warning(f"[{time.strftime('%H:%M:%S')}] ✓ PWM: {duty:.1f}% → AP7 MODE tetikleniyor")
                                try:
                                    # /opt noexec olsa bile çalışsın: bash ile çağır
                                    res = subprocess.run(["sudo", "bash", AP7_MODE_SCRIPT], capture_output=True, text=True, timeout=45)
                                    last_ap7_trigger_time = time.time()
                                    last_trigger_time = last_ap7_trigger_time  # genel cooldown'u da başlat
                                    if res.returncode == 0:
                                        logger.info("✓ ap7_mode.sh başarıyla çalıştı")
                                        if res.stdout:
                                            logger.debug(f"ap7 stdout:\n{res.stdout}")
                                    else:
                                        logger.error(f"✗ ap7_mode.sh hata: {res.stderr or res.stdout}")
                                except subprocess.TimeoutExpired:
                                    logger.error("ap7_mode.sh zaman aşımı")
                                except Exception as e:
                                    logger.error(f"ap7_mode.sh çağrı hatası: {e}")
                            else:
                                logger.error(f"ap7_mode.sh bulunamadı: {AP7_MODE_SCRIPT}")
                        else:
                            # AP7 özel cooldown bilgisi
                            remain = AP7_COOLDOWN - (current_time - last_ap7_trigger_time)
                            logger.info(f"[{time.strftime('%H:%M:%S')}] Duty: {duty:.1f}% - AP7 soğuma: {remain:.0f}s")

                    # %25 duty → QR okuma modu (dar toleransla)
                    elif abs(duty - 25.0) <= QR_TOLERANCE:
                        if (current_time - last_qr_trigger_time) >= QR_COOLDOWN:
                            logger.warning(f"[{time.strftime('%H:%M:%S')}] ✓ PWM: {duty:.1f}% → QR OKUMA MODU tetikleniyor")
                            try:
                                success = trigger_qr_mode()
                                last_qr_trigger_time = time.time()
                                last_trigger_time = last_qr_trigger_time  # genel cooldown'u da başlat
                                if success:
                                    logger.info("✓ QR okuma modu başarıyla tamamlandı")
                                else:
                                    logger.error("✗ QR okuma modu başarısız oldu")
                            except Exception as e:
                                logger.error(f"QR okuma modu hatası: {e}", exc_info=True)
                        else:
                            # QR özel cooldown bilgisi
                            remain = QR_COOLDOWN - (current_time - last_qr_trigger_time)
                            logger.info(f"[{time.strftime('%H:%M:%S')}] Duty: {duty:.1f}% - QR soğuma: {remain:.0f}s")

                    # Recovery modu kontrolü (%75)
                    elif is_duty_in_range(duty, DUTY_RECOVERY, PWM_TOLERANCE):
                        logger.warning(f"[{time.strftime('%H:%M:%S')}] ✓ PWM: {duty:.1f}% → RECOVERY MODU")
                        success = trigger_recovery()
                        last_trigger_time = time.time()
                        if success:
                            logger.info("Recovery modu başarıyla tamamlandı")
                        else:
                            logger.error("Recovery modu başarısız oldu")


                    else:
                        # Normal durum
                        logger.info(f"[{time.strftime('%H:%M:%S')}] Duty: {duty:.1f}%")
                else:
                    logger.warning(f"[{time.strftime('%H:%M:%S')}] PWM okunamadı")

                time.sleep(1)  # 1 saniye bekleme

            except Exception as e:
                logger.error(f"HATA: Döngü hatası: {e}", exc_info=True)
                time.sleep(1)

    finally:
        # Cleanup
        logger.info("Temizlik işlemleri yapılıyor...")
        try:
            line.release()
            chip.close()
            logger.info("GPIO kaynakları serbest bırakıldı.")
        except Exception as e:
            logger.error(f"GPIO cleanup hatası: {e}")

        cleanup_led_gpio()

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("Program kullanıcı tarafından sonlandırıldı")
        sys.exit(0)
    except Exception as e:
        logger.error(f"FATAL: Program hatası: {e}", exc_info=True)
        sys.exit(1)
