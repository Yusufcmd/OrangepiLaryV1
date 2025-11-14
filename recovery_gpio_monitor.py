#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GPIO PWM Monitor - QR Kod Tabanlı WiFi Yapılandırma
GPIO 76'dan gelen PWM sinyalini okur:
- %75 duty cycle: Recovery moduna geçer (factoryctl ile AP modu)
- %25 duty cycle: QR kod okuma moduna geçer ve WiFi yapılandırması yapar
"""

import os
import sys
import time
import signal
import subprocess
import threading
import re
import logging
from logging.handlers import RotatingFileHandler
from typing import Optional

try:
    import gpiod
except ImportError as e:
    raise SystemExit("gpiod modülü bulunamadı. 'sudo apt install -y python3-libgpiod gpiod'") from e

try:
    import cv2
    from pyzbar import pyzbar
except ImportError as e:
    print("⚠ UYARI: OpenCV veya pyzbar yüklü değil. QR okuma çalışmayacak.")
    print("  sudo apt-get install -y python3-opencv")
    print("  pip3 install pyzbar")
    cv2 = None
    pyzbar = None

# Kamera kontrol sinyali için dosya yolu
CAMERA_SIGNAL_FILE = "/tmp/clary_qr_mode.signal"
CAMERA_RELEASE_TIMEOUT = 10  # Kameranın serbest kalması için max bekleme süresi (saniye) - arttırıldı

# ==================== LOGLAMA YAPILANDIRMA ====================
LOG_FILE = "/home/rise/clary/recoverylog/recovery.log"
LOG_MAX_SIZE = 10 * 1024 * 1024  # 10MB
LOG_BACKUP_COUNT = 5

# Logger oluştur
logger = logging.getLogger("PWM_QR_Monitor")
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
    # Eğer /var/log'a yazamazsa, yerel dizine yaz
    LOG_FILE = os.path.join(os.path.dirname(__file__), "pwm_qr_monitor.log")
    try:
        file_handler = RotatingFileHandler(
            LOG_FILE,
            maxBytes=LOG_MAX_SIZE,
            backupCount=LOG_BACKUP_COUNT
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(log_formatter)
        logger.addHandler(file_handler)
        logger.warning(f"/var/log'a yazılamadı, yerel log kullanılıyor: {LOG_FILE}")
    except Exception as e2:
        logger.error(f"Log dosyası oluşturulamadı: {e2}")

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
DUTY_QR_MODE = 25   # %25 ± tolerans → QR okuma modu
DUTY_AP7_MODE = 50  # %50 ± tolerans → AP7 modu

# Recovery için factoryctl
FACTORYCTL_BIN = "/usr/local/sbin/factoryctl"
FACTORY_DIR = "/opt/factory"

# QR okuma için kamera
CAMERA_INDEX = 0  # /dev/video0
QR_READ_TIMEOUT = 30  # 30 saniye QR okuma timeout

# WiFi script yolları
AP_MODE_SCRIPT = "/opt/lscope/bin/ap_mode.sh"
STA_MODE_SCRIPT = "/opt/lscope/bin/sta_mode.sh"
AP7_MODE_SCRIPT = "/opt/lscope/bin/ap7_mode.sh"  # %50 duty için tetiklenecek script

# LED kontrolü (PI2 pini)
GPIO_LED_CHIP = "/dev/gpiochip1"
GPIO_LED_OFFSET = 258  # PI2 pini
LED_BLINK_INTERVAL = 0.3

# ==================== KAMERA SİNYAL FONKSİYONLARI ====================
def signal_qr_mode_start():
    """Main uygulamasına QR modunun başladığını bildir"""
    try:
        with open(CAMERA_SIGNAL_FILE, 'w') as f:
            f.write(f"{time.time()}\nQR_MODE_ACTIVE")
        logger.info(f"✓ QR modu sinyali gönderildi: {CAMERA_SIGNAL_FILE}")
        return True
    except Exception as e:
        logger.warning(f"QR modu sinyali gönderilemedi: {e}")
        return False

def signal_qr_mode_end():
    """Main uygulamasına QR modunun bittiğini bildir"""
    try:
        if os.path.exists(CAMERA_SIGNAL_FILE):
            os.remove(CAMERA_SIGNAL_FILE)
        logger.info("✓ QR modu sinyali temizlendi")
        return True
    except Exception as e:
        logger.warning(f"QR modu sinyali temizlenemedi: {e}")
        return False

def wait_for_camera_release():
    """Kameranın serbest kalmasını bekle"""
    logger.info("Kameranın serbest kalması bekleniyor...")
    start_time = time.time()

    # İlk önce main uygulamanın kamerayı serbest bırakması için yeterince bekle
    logger.debug("Ana uygulamanın kamerayı serbest bırakması için bekleniyor (5 saniye)...")
    time.sleep(5)

    attempts = 0
    max_attempts = 30  # Daha fazla deneme (15 saniye)
    elapsed = 0.0  # Başlangıç değeri

    while attempts < max_attempts:
        attempts += 1
        elapsed = time.time() - start_time

        # Kamerayı test et
        try:
            # OpenCV kaynaklarını temizle
            cv2.destroyAllWindows()
            time.sleep(0.2)

            test_cap = cv2.VideoCapture(CAMERA_INDEX)
            if test_cap.isOpened():
                # Kamera açılabildi, gerçekten kullanılabilir mi kontrol et
                test_cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                ret, frame = test_cap.read()
                test_cap.release()
                cv2.destroyAllWindows()

                if ret and frame is not None:
                    logger.info(f"✓ Kamera serbest ve kullanılabilir (bekleme: {elapsed:.1f}s)")
                    # Kameranın tamamen serbest kalması için ek bekleme
                    time.sleep(1)
                    return True
                else:
                    logger.debug(f"Kamera açıldı ama frame okunamadı (deneme {attempts}/{max_attempts})")
            else:
                test_cap.release()
                cv2.destroy_allWindows()
                logger.debug(f"Kamera açılamadı (deneme {attempts}/{max_attempts})")
        except Exception as e:
            logger.debug(f"Kamera test hatası: {e} (deneme {attempts}/{max_attempts})")

        time.sleep(0.5)

    # Timeout oldu - kamerayı zorla serbest bırakmayı dene
    logger.warning(f"⚠ Kamera serbest kalma timeout ({elapsed:.1f}s, {attempts} deneme)")
    logger.info("Kamerayı ZORLA serbest bırakma deneniyor...")

    video_device = f"/dev/video{CAMERA_INDEX}"

    # OpenCV kaynaklarını temizle
    try:
        cv2.destroyAllWindows()
        time.sleep(0.5)
        logger.debug("OpenCV kaynakları temizlendi")
    except Exception as e:
        logger.debug(f"OpenCV temizleme hatası: {e}")

    # Yöntem 1: lsof ile kamerayı kullanan işlemleri bul ve sonlandır
    try:
        result = subprocess.run(
            ['lsof', video_device],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0 and result.stdout:
            logger.info(f"Kamerayı kullanan işlemler:\n{result.stdout}")

            # PID'leri çıkar ve sonlandır
            lines = result.stdout.strip().split('\n')[1:]  # İlk satır başlık
            for line in lines:
                parts = line.split()
                if len(parts) >= 2:
                    pid = parts[1]
                    try:
                        logger.info(f"İşlem sonlandırılıyor: PID {pid}")
                        # Önce SIGTERM ile nazikçe dene
                        subprocess.run(['kill', '-15', pid], timeout=2)
                    except Exception as e:
                        logger.warning(f"PID {pid} sonlandırılamadı: {e}")

            time.sleep(3)  # İşlemlerin kapanması için bekle
    except subprocess.TimeoutExpired:
        logger.error("lsof komutu timeout oldu")
    except FileNotFoundError:
        logger.warning("lsof komutu bulunamadı - yüklenmesi önerilir: sudo apt install lsof")
    except Exception as e:
        logger.error(f"lsof hatası: {e}")

    # Yöntem 2: fuser ile tekrar dene (sudo olmadan)
    try:
        result = subprocess.run(
            ['fuser', '-v', video_device],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.stderr:  # fuser çıktısı stderr'de gelir
            logger.info(f"fuser çıktısı:\n{result.stderr}")

        # Şimdi sonlandır
        result = subprocess.run(
            ['fuser', '-k', video_device],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0 or result.returncode == 1:  # 1 = işlem bulunamadı (normal)
            logger.info(f"fuser ile işlem sonlandırma denendi")
            time.sleep(2)
    except FileNotFoundError:
        logger.warning("fuser komutu bulunamadı")
    except Exception as e:
        logger.debug(f"fuser hatası: {e}")

    # Yöntem 3: Video cihazını v4l2-ctl ile reset et
    try:
        logger.info("Video cihazını v4l2-ctl ile reset ediliyor...")
        # Önce v4l2-ctl'in varlığını kontrol et
        check_result = subprocess.run(
            ['which', 'v4l2-ctl'],
            capture_output=True,
            text=True,
            timeout=2
        )

        if check_result.returncode == 0:
            # v4l2-ctl mevcut, reset işlemini yap
            subprocess.run(
                ['v4l2-ctl', '--device', video_device, '--set-fmt-video=width=640,height=480,pixelformat=MJPG'],
                capture_output=True,
                timeout=5
            )
            time.sleep(1)
            logger.info("v4l2-ctl reset işlemi yapıldı")
        else:
            logger.warning("v4l2-ctl bulunamadı - yüklenmesi önerilir: sudo apt install v4l-utils")
    except Exception as e:
        logger.debug(f"v4l2-ctl hatası: {e}")

    # OpenCV kaynaklarını tekrar temizle
    try:
        cv2.destroyAllWindows()
        time.sleep(0.5)
    except Exception:
        pass

    # Son kontrol - daha fazla deneme ile
    logger.info("Son kontrol yapılıyor...")
    for final_attempt in range(10):  # 10 deneme
        try:
            cv2.destroyAllWindows()
            time.sleep(0.3)

            test_cap = cv2.VideoCapture(CAMERA_INDEX)
            if test_cap.isOpened():
                test_cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                ret, frame = test_cap.read()
                test_cap.release()
                cv2.destroyAllWindows()

                if ret and frame is not None:
                    logger.info(f"✓ Kamera zorla serbest bırakıldı ve kullanılabilir durumda (deneme {final_attempt + 1})")
                    time.sleep(0.5)
                    return True
            else:
                test_cap.release()
                cv2.destroy_allWindows()
        except Exception as e:
            logger.debug(f"Son kontrol hatası (deneme {final_attempt + 1}): {e}")
        time.sleep(1)

    logger.error("✗ Kamera serbest bırakılamadı - TÜM YÖNTEMLER BAŞARISIZ")
    logger.info("💡 Öneriler:")
    logger.info("   1. sudo apt install v4l-utils lsof")
    logger.info("   2. Main uygulamayı yeniden başlatın")
    logger.info("   3. Sistem yeniden başlatmayı deneyin")
    return False

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

# ==================== QR KOD OKUMA ====================
def detect_qr_with_preprocessing(frame):
    """
    Görüntü ön işleme teknikleri kullanarak QR kod tespit et.
    Farklı yöntemler sırasıyla denenir ve ilk başarılı sonuç döndürülür.
    """
    # 1. Orijinal frame'de dene
    decoded_objects = pyzbar.decode(frame)
    if decoded_objects:
        logger.debug("QR kod orijinal frame'de bulundu")
        return decoded_objects

    # 2. Gri tonlama
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    decoded_objects = pyzbar.decode(gray)
    if decoded_objects:
        logger.debug("QR kod gri tonlamalı frame'de bulundu")
        return decoded_objects

    # 3. CLAHE (Contrast Limited Adaptive Histogram Equalization) ile kontrast iyileştirme
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8,8))
    enhanced = clahe.apply(gray)
    decoded_objects = pyzbar.decode(enhanced)
    if decoded_objects:
        logger.debug("QR kod CLAHE uygulanmış frame'de bulundu")
        return decoded_objects

    # 4. Otsu threshold
    _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    decoded_objects = pyzbar.decode(thresh)
    if decoded_objects:
        logger.debug("QR kod threshold uygulanmış frame'de bulundu")
        return decoded_objects

    # 5. Adaptive threshold
    adaptive = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                     cv2.THRESH_BINARY, 11, 2)
    decoded_objects = pyzbar.decode(adaptive)
    if decoded_objects:
        logger.debug("QR kod adaptive threshold frame'de bulundu")
        return decoded_objects

    # 6. Histogram eşitleme
    equalized = cv2.equalizeHist(gray)
    decoded_objects = pyzbar.decode(equalized)
    if decoded_objects:
        logger.debug("QR kod histogram eşitlenmiş frame'de bulundu")
        return decoded_objects

    # 7. Gaussian Blur + Threshold
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    _, thresh_blur = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    decoded_objects = pyzbar.decode(thresh_blur)
    if decoded_objects:
        logger.debug("QR kod blur+threshold frame'de bulundu")
        return decoded_objects

    # 8. Negatif görüntü (ters çevirme)
    inverted = cv2.bitwise_not(gray)
    decoded_objects = pyzbar.decode(inverted)
    if decoded_objects:
        logger.debug("QR kod ters çevrilmiş frame'de bulundu")
        return decoded_objects

    return []


def read_qr_code_from_camera(timeout=QR_READ_TIMEOUT):
    """
    Kameradan QR kod oku - İyileştirilmiş görüntü işleme ile
    Farklı görüntü işleme teknikleri kullanarak QR kod okuma başarı oranını artırır.
    """
    if cv2 is None or pyzbar is None:
        logger.error("HATA: OpenCV veya pyzbar yüklü değil!")
        return None

    # QR modu başladığını bildir
    signal_qr_mode_start()

    # Kameranın serbest kalmasını bekle
    wait_for_camera_release()

    logger.info(f"Kamera açılıyor... (Timeout: {timeout}s)")

    # Birkaç kez deneme yap
    max_retries = 3
    for attempt in range(max_retries):
        try:
            cap = cv2.VideoCapture(CAMERA_INDEX)

            # Kamera ayarlarını optimize et
            if cap.isOpened():
                # Çözünürlük ayarla (yüksek çözünürlük QR okumayı iyileştirir)
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
                # Autofocus açık olsun (varsa)
                cap.set(cv2.CAP_PROP_AUTOFOCUS, 1)

                logger.info(f"✓ Kamera açıldı (deneme {attempt + 1}/{max_retries})")
                logger.debug(f"  Çözünürlük: {cap.get(cv2.CAP_PROP_FRAME_WIDTH)}x{cap.get(cv2.CAP_PROP_FRAME_HEIGHT)}")
                break

            cap.release()
            logger.warning(f"Kamera açılamadı, yeniden deneniyor... ({attempt + 1}/{max_retries})")
            time.sleep(1)

        except Exception as e:
            logger.warning(f"Kamera açma hatası (deneme {attempt + 1}): {e}")
            time.sleep(1)
    else:
        logger.error(f"HATA: Kamera açılamadı ({max_retries} deneme)")
        signal_qr_mode_end()
        return None

    logger.info("✓ Kamera açıldı. QR kod bekleniyor...")
    logger.info("  (Farklı görüntü işleme teknikleri kullanılarak tarama yapılıyor)")
    start_time = time.time()
    qr_data = None

    try:
        frame_count = 0
        skip_frames = 2  # İlk birkaç frame'i atla (kamera ayarlaması için)

        while (time.time() - start_time) < timeout:
            ret, frame = cap.read()
            if not ret:
                logger.warning("Kamera görüntü okuması başarısız")
                time.sleep(0.1)
                continue

            frame_count += 1

            # İlk birkaç frame'i atla
            if frame_count <= skip_frames:
                continue

            # Gelişmiş QR tespit fonksiyonunu kullan
            decoded_objects = detect_qr_with_preprocessing(frame)

            for obj in decoded_objects:
                try:
                    qr_data = obj.data.decode('utf-8')
                    logger.info(f"✓ QR kod başarıyla okundu!")
                    logger.info(f"  İçerik: {qr_data}")
                    logger.info(f"  Tip: {obj.type}")
                    logger.info(f"  Konum: {obj.rect}")
                    logger.info(f"  Frame sayısı: {frame_count}, Süre: {time.time() - start_time:.2f}s")
                    cap.release()
                    signal_qr_mode_end()
                    # Kameranın tekrar başlaması için kısa bekleme
                    time.sleep(1)
                    return qr_data
                except Exception as e:
                    logger.warning(f"QR kod decode hatası: {e}")
                    continue

            # Her 30 frame'de bir log (daha sık bilgilendirme)
            if frame_count % 30 == 0:
                elapsed = time.time() - start_time
                logger.debug(f"QR aranıyor... ({frame_count} frame işlendi, {elapsed:.1f}s geçti)")

            # CPU kullanımını azaltmak için hafif bekleme
            # Ancak çok fazla beklemiyoruz çünkü QR hızlı geçebilir
            time.sleep(0.05)

    except KeyboardInterrupt:
        logger.info("QR okuma kullanıcı tarafından iptal edildi")
    except Exception as e:
        logger.error(f"QR okuma sırasında beklenmeyen hata: {e}")
        import traceback
        logger.error(traceback.format_exc())
    finally:
        cap.release()
        logger.debug("Kamera kapatıldı")
        signal_qr_mode_end()
        # Kameranın tekrar başlaması için kısa bekleme
        time.sleep(1)

    logger.warning(f"⚠ Timeout: {timeout} saniye içinde QR kod okunamadı")
    logger.info(f"  Toplam {frame_count} frame işlendi")
    return None

def parse_qr_data(qr_data):
    """
    QR kod verisini parse et ve mod/parametreleri döndür

    Desteklenen formatlar:
    1. AP Mode: APMODE5gch36, APMODE2.4gch6
    2. WiFi QR: WIFI:T:WPA;S:SSID;P:Password;H:false;;

    Returns:
        tuple: (config_dict, error_message)
               config_dict: Başarılı ise yapılandırma, değilse None
               error_message: Hata varsa hata mesajı, yoksa None
    """
    if not qr_data:
        logger.warning("Boş QR data alındı")
        return None, "QR kod verisi boş"

    # QR verisini temizle (baştaki/sondaki boşlukları kaldır)
    qr_data = qr_data.strip()

    logger.info(f"QR kod parse ediliyor: {qr_data[:100]}...")  # İlk 100 karakter

    # ==================== AP MODE ====================
    # Format: APMODE5gch36 veya APMODE2.4gch6
    ap_pattern = r'^APMODE(5g|2\.4g)ch(\d+)$'
    ap_match = re.match(ap_pattern, qr_data, re.IGNORECASE)

    if ap_match:
        band = ap_match.group(1).lower()
        channel = int(ap_match.group(2))

        logger.info(f"✓ AP Mode QR kodu tespit edildi")
        logger.info(f"  Band: {band}")
        logger.info(f"  Kanal: {channel}")

        # Band doğrulama
        if band == "5g":
            hw_mode = "a"
            valid_channels = [36, 40, 44, 48, 52, 56, 60, 64, 100, 104, 108, 112, 116, 120, 124, 128, 132, 136, 140, 149, 153, 157, 161, 165]
            logger.debug(f"5GHz band seçildi, geçerli kanallar: {valid_channels}")
        elif band == "2.4g":
            hw_mode = "g"
            valid_channels = list(range(1, 15))  # 1-14 (Ülkeye göre değişir)
            logger.debug(f"2.4GHz band seçildi, geçerli kanallar: {valid_channels}")
        else:
            error_msg = f"Geçersiz band: {band} (5g veya 2.4g olmalı)"
            logger.error(error_msg)
            return None, error_msg

        # Kanal doğrulama
        if channel not in valid_channels:
            error_msg = f"Geçersiz kanal: {channel} (Geçerli kanallar: {valid_channels})"
            logger.error(error_msg)
            return None, error_msg

        config = {
            'mode': 'ap',
            'band': band,
            'hw_mode': hw_mode,
            'channel': channel
        }
        logger.info(f"✓ AP Mode yapılandırması hazır: {config}")
        return config, None

    # ==================== STA MODE (WiFi QR) ====================
    # Format: WIFI:T:WPA;S:MySSID;P:MyPassword;H:false;;
    # Standart WiFi QR kod formatı
    if qr_data.upper().startswith('WIFI:'):
        logger.info("✓ WiFi QR kodu tespit edildi")

        # Son ";;" kontrolü - bazı QR okuyucular farklı encode edebilir
        if not qr_data.endswith(';;'):
            # Tek ; ile bitiyorsa kabul et
            if qr_data.endswith(';'):
                qr_data += ';'
            else:
                qr_data += ';;'
            logger.debug(f"WiFi QR sonuna ;; eklendi: {qr_data}")

        # Kaçış karakterlerini çöz
        def unescape_wifi(s):
            """WiFi QR kaçış karakterlerini çöz"""
            # Önce \ ile escape edilmiş karakterleri geçici placeholder'a çevir
            s = s.replace(r'\;', '\x00')  # Escaped semicolon
            s = s.replace(r'\:', '\x01')  # Escaped colon
            s = s.replace(r'\,', '\x02')  # Escaped comma
            s = s.replace(r'\\', '\x03')  # Escaped backslash
            return s

        def restore_wifi(s):
            """Placeholder'ları gerçek karakterlere geri yükle"""
            s = s.replace('\x00', ';')
            s = s.replace('\x01', ':')
            s = s.replace('\x02', ',')
            s = s.replace('\x03', '\\')
            return s

        # Parametreleri parse et
        params = {}
        try:
            # WIFI: prefix ve ;; suffix'i kaldır
            content = qr_data[5:]  # "WIFI:" çıkar
            if content.endswith(';;'):
                content = content[:-2]  # ";;" çıkar
            elif content.endswith(';'):
                content = content[:-1]  # ";" çıkar

            logger.debug(f"WiFi QR içeriği: {content}")

            # Kaçış karakterlerini geçici olarak değiştir
            content_escaped = unescape_wifi(content)

            # Parametreleri ayır (kaçışsız ; ile)
            parts = content_escaped.split(';')

            logger.debug(f"WiFi QR parçaları: {parts}")

            for part in parts:
                if not part.strip():  # Boş parçaları atla
                    continue

                if ':' in part:
                    key, value = part.split(':', 1)
                    # Kaçış karakterlerini geri yükle
                    key = key.strip()
                    value = restore_wifi(value.strip())
                    params[key] = value
                    logger.debug(f"  {key} = {value}")

            logger.debug(f"Parse edilen WiFi parametreleri: {list(params.keys())}")

            # Zorunlu parametreleri kontrol et
            if 'T' not in params:
                error_msg = "WiFi QR eksik parametre: T (güvenlik tipi) bulunamadı"
                logger.error(error_msg)
                return None, error_msg

            if 'S' not in params:
                error_msg = "WiFi QR eksik parametre: S (SSID) bulunamadı"
                logger.error(error_msg)
                return None, error_msg

            security = params['T'].upper()
            ssid = params['S']
            password = params.get('P', '')  # Şifre opsiyonel (açık ağlar için)
            hidden = params.get('H', 'false').lower() == 'true'

            # SSID boş olamaz
            if not ssid:
                error_msg = "SSID boş olamaz"
                logger.error(error_msg)
                return None, error_msg

            # Güvenlik tipi kontrolü
            valid_security_types = ['WPA', 'WPA2', 'WEP', 'NOPASS', 'WPA3']
            if security not in valid_security_types:
                logger.warning(f"Standart dışı güvenlik tipi: {security} (WPA olarak kabul ediliyor)")
                # WPA olarak kabul et
                if security not in ['NOPASS', 'nopass']:
                    security = 'WPA'

            # nopass durumunda şifre boş olmalı
            if security == 'NOPASS':
                password = ''
                logger.debug("Açık ağ (şifresiz) tespit edildi")
            elif not password and security != 'NOPASS':
                logger.warning(f"Şifreli ağ ({security}) için şifre belirtilmemiş")

            logger.info(f"✓ WiFi yapılandırması:")
            logger.info(f"  SSID: {ssid}")
            logger.info(f"  Güvenlik: {security}")
            logger.info(f"  Şifre: {'***' if password else '(yok)'}")
            logger.info(f"  Gizli: {hidden}")

            config = {
                'mode': 'sta',
                'ssid': ssid,
                'password': password,
                'security': security,
                'hidden': hidden
            }
            return config, None

        except Exception as e:
            error_msg = f"WiFi QR parse hatası: {e}"
            logger.error(error_msg)
            logger.error(f"QR içeriği: {qr_data}")
            import traceback
            logger.debug(traceback.format_exc())
            return None, error_msg

    # ==================== BİLİNMEYEN FORMAT ====================
    error_msg = f"Tanınmayan QR formatı. Beklenen: 'APMODE...' veya 'WIFI:...'"
    logger.error(error_msg)
    logger.error(f"QR içeriği: {qr_data[:200]}")  # İlk 200 karakter
    return None, error_msg

# ==================== WiFi YAPILANDIRMA ====================
def configure_ap_mode(band, hw_mode, channel):
    """AP modunu yapılandır"""
    logger.info("="*60)
    logger.info("AP MODE YAPILANDIRMA")
    logger.info(f"  Band: {band}")
    logger.info(f"  HW Mode: {hw_mode}")
    logger.info(f"  Channel: {channel}")
    logger.info("="*60)

    # hostapd.conf dosyasını güncelle
    hostapd_conf = "/etc/hostapd/hostapd.conf"

    if not os.path.exists(hostapd_conf):
        logger.error(f"HATA: {hostapd_conf} bulunamadı!")
        return False

    try:
        # Mevcut yapılandırmayı oku
        logger.debug(f"{hostapd_conf} okunuyor...")
        with open(hostapd_conf, 'r') as f:
            config_lines = f.readlines()

        # hw_mode ve channel parametrelerini güncelle
        updated_lines = []
        hw_mode_updated = False
        channel_updated = False

        for line in config_lines:
            if line.strip().startswith('hw_mode='):
                updated_lines.append(f'hw_mode={hw_mode}\n')
                hw_mode_updated = True
                logger.debug(f"hw_mode güncellendi: {hw_mode}")
            elif line.strip().startswith('channel='):
                updated_lines.append(f'channel={channel}\n')
                channel_updated = True
                logger.debug(f"channel güncellendi: {channel}")
            else:
                updated_lines.append(line)

        # Eğer parametreler yoksa ekle
        if not hw_mode_updated:
            updated_lines.append(f'hw_mode={hw_mode}\n')
            logger.debug(f"hw_mode eklendi: {hw_mode}")
        if not channel_updated:
            updated_lines.append(f'channel={channel}\n')
            logger.debug(f"channel eklendi: {channel}")

        # Geçici dosyaya yaz
        import tempfile
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.conf') as tmp:
            tmp.writelines(updated_lines)
            tmp_path = tmp.name

        logger.debug(f"Geçici dosya oluşturuldu: {tmp_path}")

        # sudo ile kopyala
        logger.debug(f"hostapd.conf güncelleniyor...")
        result = subprocess.run(['sudo', 'cp', tmp_path, hostapd_conf],
                              capture_output=True, text=True)
        os.unlink(tmp_path)

        if result.returncode != 0:
            logger.error(f"hostapd.conf kopyalama hatası: {result.stderr}")
            return False

        logger.info(f"✓ {hostapd_conf} güncellendi")

        # AP mode script'i çalıştır
        if os.path.exists(AP_MODE_SCRIPT):
            logger.info(f"AP mode script çalıştırılıyor: {AP_MODE_SCRIPT}")
            result = subprocess.run(['sudo', AP_MODE_SCRIPT],
                                  capture_output=True, text=True, timeout=30)
            if result.returncode == 0:
                logger.info("✓ AP modu başarıyla başlatıldı")
                if result.stdout:
                    logger.debug(f"Script çıktısı:\n{result.stdout}")
                return True
            else:
                logger.error(f"HATA: AP mode script başarısız: {result.stderr}")
                return False
        else:
            # Manuel hostapd restart
            logger.warning("AP mode script bulunamadı, manuel restart yapılıyor...")
            result = subprocess.run(['sudo', 'systemctl', 'restart', 'hostapd'],
                                  capture_output=True, text=True)
            if result.returncode == 0:
                logger.info("✓ hostapd yeniden başlatıldı")
                return True
            else:
                logger.error(f"hostapd restart hatası: {result.stderr}")
                return False

    except Exception as e:
        logger.error(f"HATA: AP mode yapılandırma hatası: {e}", exc_info=True)
        return False

def configure_sta_mode(ssid, password):
    """STA modunu yapılandır"""
    logger.info("="*60)
    logger.info("STA MODE YAPILANDIRMA")
    logger.info(f"  SSID: {ssid}")
    logger.info(f"  Password: {'*' * len(password)}")
    logger.info("="*60)

    try:
        # STA mode script'i çalıştır
        if os.path.exists(STA_MODE_SCRIPT):
            logger.info(f"STA mode script çalıştırılıyor: {STA_MODE_SCRIPT}")
            # Script içinde SSID ve PSK parametreleri güncellenmeli
            # Önce script'i yeniden oluştur
            script_content = f"""#!/usr/bin/env bash
set -euo pipefail
LOG=/var/log/wifi_mode.log
SSID='{ssid}'
PSK='{password}'

echo "[sta_mode] $(date '+%F %T')" | tee -a "$LOG"
systemctl disable --now hostapd dnsmasq wlan0-static.service || true
ip addr flush dev wlan0 || true

mkdir -p /etc/NetworkManager/conf.d
if [ -f /etc/NetworkManager/conf.d/unmanaged.conf ]; then
  sed -i '/unmanaged-devices/d' /etc/NetworkManager/conf.d/unmanaged.conf || true
  [ -s /etc/NetworkManager/conf.d/unmanaged.conf ] || rm -f /etc/NetworkManager/conf.d/unmanaged.conf
fi

systemctl enable --now NetworkManager || true
rfkill unblock wifi || true
nmcli radio wifi on || true

# ÖNEMLİ: Hedef SSID dışındaki TÜM Wi-Fi bağlantılarını SİL (sadece güncel ağ kalsın)
echo "Deleting all other WiFi connections (keeping only target SSID)..." | tee -a "$LOG"
nmcli -t -f NAME,TYPE con show | grep ':802-11-wireless$' | cut -d: -f1 | while IFS= read -r conn_name; do
  if [ "$conn_name" != "$SSID" ]; then
    echo "  Deleting connection: $conn_name" | tee -a "$LOG"
    nmcli con delete "$conn_name" 2>&1 | tee -a "$LOG" || true
  fi
done

# Bağlantıyı oluştur/güncelle (autoconnect her zaman yes)
if nmcli -t -f NAME con show | grep -Fxq "$SSID"; then
  nmcli con modify "$SSID" wifi-sec.key-mgmt wpa-psk wifi-sec.psk "$PSK" connection.autoconnect yes ipv4.method auto || true
else
  nmcli con add type wifi ifname wlan0 con-name "$SSID" ssid "$SSID"
  nmcli con modify "$SSID" wifi-sec.key-mgmt wpa-psk wifi-sec.psk "$PSK" connection.autoconnect yes ipv4.method auto
fi

nmcli dev wifi rescan || true
nmcli con up "$SSID" || nmcli dev wifi connect "$SSID" password "$PSK"

systemctl restart NetworkManager || true

echo "[sta_mode OK] $(date '+%F %T')" | tee -a "$LOG"
"""
            # Geçici dosyaya yaz ve çalıştır
            import tempfile
            with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.sh') as tmp:
                tmp.write(script_content)
                tmp_path = tmp.name

            logger.debug(f"Geçici script oluşturuldu: {tmp_path}")
            os.chmod(tmp_path, 0o755)

            logger.debug("STA mode script çalıştırılıyor...")
            result = subprocess.run(['sudo', 'bash', tmp_path],
                                  capture_output=True, text=True, timeout=60)
            os.unlink(tmp_path)

            if result.returncode == 0:
                logger.info("✓ STA modu başarıyla yapılandırıldı")
                if result.stdout:
                    logger.debug(f"Script çıktısı:\n{result.stdout}")
                return True
            else:
                logger.error(f"HATA: STA mode yapılandırma başarısız: {result.stderr}")
                return False
        else:
            # NetworkManager ile doğrudan bağlan
            logger.warning("STA mode script bulunamadı, NetworkManager kullanılıyor...")

            # Hostapd'yi durdur
            logger.debug("hostapd durduruluyor...")
            subprocess.run(['sudo', 'systemctl', 'stop', 'hostapd'],
                         capture_output=True, check=False)

            # NetworkManager'ı başlat
            logger.debug("NetworkManager başlatılıyor...")
            subprocess.run(['sudo', 'systemctl', 'start', 'NetworkManager'],
                         capture_output=True, check=True)

            # WiFi bağlantısı oluştur veya güncelle
            logger.debug(f"WiFi bağlantısı kontrol ediliyor: {ssid}")
            result = subprocess.run(['nmcli', 'con', 'show', ssid],
                                  capture_output=True, text=True)

            if result.returncode == 0:
                # Mevcut bağlantıyı güncelle
                logger.debug(f"Mevcut bağlantı güncelleniyor: {ssid}")
                subprocess.run(['sudo', 'nmcli', 'con', 'modify', ssid,
                              'wifi-sec.psk', password], check=True)
            else:
                # Yeni bağlantı oluştur
                logger.debug(f"Yeni bağlantı oluşturuluyor: {ssid}")
                subprocess.run(['sudo', 'nmcli', 'dev', 'wifi', 'connect', ssid,
                              'password', password], check=True)

            logger.info("✓ STA modu başarıyla yapılandırıldı")
            return True

    except Exception as e:
        logger.error(f"HATA: STA mode yapılandırma hatası: {e}", exc_info=True)
        return False

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

# ==================== QR OKUMA MODU ====================
def trigger_qr_mode():
    """QR okuma modunu tetikle"""
    logger.info("="*60)
    logger.info("QR OKUMA MODU TETIKLENDI")
    logger.info("="*60)

    start_led_blink()

    try:
        # QR kod oku
        qr_data = read_qr_code_from_camera(timeout=QR_READ_TIMEOUT)

        if not qr_data:
            logger.error("HATA: QR kod okunamadı")
            stop_led_blink()
            return False

        # QR verisini parse et
        config, error = parse_qr_data(qr_data)

        if error:
            logger.error(f"HATA: QR parse hatası: {error}")
            stop_led_blink()
            return False

        if not config:
            logger.error("HATA: Geçersiz QR verisi")
            stop_led_blink()
            return False

        # Moda göre yapılandır
        success = False
        if config['mode'] == 'ap':
            logger.info(f"AP Mode yapılandırması başlatılıyor: {config['band']} band, kanal {config['channel']}")
            success = configure_ap_mode(config['band'], config['hw_mode'], config['channel'])
        elif config['mode'] == 'sta':
            logger.info(f"STA Mode yapılandırması başlatılıyor: SSID={config['ssid']}")
            success = configure_sta_mode(config['ssid'], config['password'])

        stop_led_blink()

        if success:
            logger.info(f"✓ WiFi yapılandırması başarılı ({config['mode'].upper()} mode)")
        else:
            logger.error(f"✗ WiFi yapılandırması başarısız")

        return success

    except Exception as e:
        logger.error(f"HATA: QR okuma modu hatası: {e}", exc_info=True)
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
    logger.info("PWM MONITOR - QR Kod Tabanlı WiFi Yapılandırma")
    logger.info("="*60)
    logger.info(f"GPIO Chip: {GPIO_CHIP}")
    logger.info(f"GPIO Offset (Pin): {GPIO_OFFSET}")
    logger.info(f"PWM Ölçüm: {PWM_SAMPLE_COUNT} örnek, {PWM_TOLERANCE}% tolerans")
    logger.info(f"  - %{DUTY_RECOVERY}±{PWM_TOLERANCE} → Recovery Modu (factoryctl AP)")
    logger.info(f"  - %{DUTY_QR_MODE}±{PWM_TOLERANCE} → QR Okuma Modu")
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

                    # Recovery modu kontrolü (%75)
                    elif is_duty_in_range(duty, DUTY_RECOVERY, PWM_TOLERANCE):
                        logger.warning(f"[{time.strftime('%H:%M:%S')}] ✓ PWM: {duty:.1f}% → RECOVERY MODU")
                        success = trigger_recovery()
                        last_trigger_time = time.time()
                        if success:
                            logger.info("Recovery modu başarıyla tamamlandı")
                        else:
                            logger.error("Recovery modu başarısız oldu")

                    # QR okuma modu kontrolü (%25)
                    elif is_duty_in_range(duty, DUTY_QR_MODE, PWM_TOLERANCE):
                        logger.warning(f"[{time.strftime('%H:%M:%S')}] ✓ PWM: {duty:.1f}% → QR OKUMA MODU")
                        success = trigger_qr_mode()
                        last_trigger_time = time.time()
                        if success:
                            logger.info("QR okuma modu başarıyla tamamlandı")
                        else:
                            logger.error("QR okuma modu başarısız oldu")

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
