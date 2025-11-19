#!/usr/bin/python3
# -*- coding: utf-8 -*-
import eventlet; eventlet.monkey_patch()

import os, sys, time, re, subprocess, logging, signal, threading, shlex, errno
from datetime import datetime
from collections import deque
from typing import Tuple, Union

import numpy as np
import cv2

from flask import Flask, render_template, Response, request, redirect, url_for, session, flash
from flask_sqlalchemy import SQLAlchemy
from flask_socketio import SocketIO
from flask_wtf import CSRFProtect
try:
    from flask_wtf.csrf import generate_csrf
except Exception:
    generate_csrf = None

# Merkezi loglama sistemi
try:
    import system_logger as syslog
except Exception as e:
    syslog = None
    print(f"Warning: system_logger yüklenemedi: {e}")

# mDNS broadcast desteği
try:
    import mdns_broadcast
except Exception as e:
    mdns_broadcast = None
    print(f"Warning: mdns_broadcast yüklenemedi: {e}")

# =========================== Flask & Eklentiler ===========================
BASE_DIR = os.path.abspath(os.path.dirname(__file__))

# Yazılabilir DB URI çözümle
try:
    import db_util
    DB_URI = db_util.resolve_sqlite_uri(BASE_DIR)
    DB_PATH = db_util.get_db_path(BASE_DIR)
except Exception:
    DB_PATH  = os.path.join(BASE_DIR, "site.db")
    DB_URI   = f"sqlite:///{DB_PATH}"

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("APP_SECRET_KEY", "change_me")
app.config["SQLALCHEMY_DATABASE_URI"] = DB_URI
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

csrf     = CSRFProtect(app)
db       = SQLAlchemy(app)
socketio = SocketIO(
    app,
    async_mode="eventlet",
    cors_allowed_origins="*",
    ping_timeout=60,
    ping_interval=25,
    logger=True,
    engineio_logger=True,
)

# recordsVideo blueprint kaydı ve Jinja filtre
try:
    import recordsVideo
    app.register_blueprint(recordsVideo.records_bp)
except Exception as _e:
    logging.getLogger(__name__).error(f"recordsVideo blueprint kaydı başarısız: {_e}")

# mobile_api blueprint kaydı (REST API for mobile apps)
try:
    import mobile_api
    csrf.exempt(mobile_api.mobile_api_bp)  # API'yi CSRF korumasından muaf tut
    app.register_blueprint(mobile_api.mobile_api_bp)
    logging.getLogger(__name__).info("mobile_api blueprint başarıyla kaydedildi")
except Exception as _e:
    logging.getLogger(__name__).error(f"mobile_api blueprint kaydı başarısız: {_e}")

@app.template_filter('fmt_ts')
def fmt_ts(value):
    try:
        return datetime.fromtimestamp(float(value)).strftime('%Y-%m-%d %H:%M:%S')
    except Exception:
        return str(value)

@app.context_processor
def inject_csrf():
    if generate_csrf:
        return dict(csrf_token=generate_csrf)
    return {}

# ================================ Loglama =================================
log_dir = os.path.join(BASE_DIR, "logs")
fallback_dir = "/home/rise/clary/clary_logs"
os.makedirs(log_dir, exist_ok=True)

def _make_handler(path):
    fmt = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    fh  = logging.FileHandler(path, mode="a", delay=True)
    fh.setFormatter(fmt); fh.setLevel(logging.DEBUG)
    return fh

try:
    log_path = os.path.join(log_dir, f"clary_{datetime.now().strftime('%Y-%m-%d')}.log")
    fh = _make_handler(log_path)
except Exception:
    os.makedirs(fallback_dir, exist_ok=True)
    log_path = os.path.join(fallback_dir, f"clary_{datetime.now().strftime('%Y-%m-%d')}.log")
    fh = _make_handler(log_path)

ch  = logging.StreamHandler()
fmt2 = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
ch.setFormatter(fmt2); ch.setLevel(logging.INFO)

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
logger.addHandler(fh); logger.addHandler(ch)
logger.info(f"Log dosyası: {log_path}")
logger.info(f"Veritabanı: {DB_PATH}")

try:
    cv2.utils.logging.setLogLevel(cv2.utils.logging.LOG_LEVEL_SILENT)
except Exception:
    pass

# ============================= GPIO / gpiod katmanı =======================
GPIO = None
_backend = None
_import_errors = []
led_pin = None
ready_led_pin = None  # Pin 258 için sistem hazır LED'i

USE_GPIOD = False
try:
    import gpiod  # python3-libgpiod
    USE_GPIOD = True
except Exception:
    USE_GPIOD = False

# Shutdown için GPIO ayarları
SHUTDOWN_GPIO_LINE = os.environ.get("SHUTDOWN_GPIO_LINE", "/dev/gpiochip1:272")
SHUTDOWN_DEBOUNCE_MS = int(os.environ.get("SHUTDOWN_DEBOUNCE_MS", "80"))

prefer = (os.environ.get("GPIO_BACKEND", "") or "").lower()

def _try_import_gpio():
    """GPIO alternatif backend (gerekirse)."""
    global GPIO, _backend, led_pin
    if prefer in ("", "safe_gpio"):
        try:
            from safe_gpio import GPIO as _G
            GPIO = _G; _backend = "safe_gpio"
        except Exception as e:
            _import_errors.append(f"safe_gpio: {e}")
            GPIO = None
    if GPIO is None and prefer in ("", "opi", "opigpio", "orangepi"):
        try:
            import OPi.GPIO as _G
            GPIO = _G; _backend = "OPi.GPIO"
        except Exception as e:
            _import_errors.append(f"OPi.GPIO: {e}")
            GPIO = None
    if GPIO is None and prefer in ("", "rpi", "rpi.gpio", "raspi"):
        try:
            import RPi.GPIO as _G
            GPIO = _G; _backend = "RPi.GPIO"
        except Exception as e:
            _import_errors.append(f"RPi.GPIO: {e}")
            GPIO = None

    if GPIO is None:
        logger.warning("GPIO katmanı yüklenemedi. Adaylar başarısız: " + " | ".join(_import_errors))
        return

    try:
        GPIO.setwarnings(False)
        mode_env = (os.environ.get("GPIO_MODE", "") or "BOARD").upper()
        GPIO.setmode(getattr(GPIO, mode_env))
        logger.info(f"GPIO backend: {_backend}, mode={mode_env}")
    except Exception as e:
        logger.warning(f"GPIO setmode hatası: {e}")

    try:
        led_pin = int(os.environ.get("LED_PIN", "31"))  # BOARD 31 (PI15)
        GPIO.setup(led_pin, GPIO.OUT, initial=GPIO.LOW)
    except Exception as e:
        logger.warning(f"LED pin init başarısız: {e}")
        led_pin = None

    # Pin 258 için sistem hazır LED'i
    try:
        ready_led_pin = 258
        GPIO.setup(ready_led_pin, GPIO.OUT, initial=GPIO.HIGH)  # LED direkt yansın
        logger.info(f"Sistem hazır LED pin {ready_led_pin} başlatıldı ve HIGH yapıldı")
    except Exception as e:
        logger.warning(f"Sistem hazır LED pin init başarısız: {e}")
        ready_led_pin = None

_try_import_gpio()

# =========================== Kamera / Global durum ========================
camera = None
camera_lock = threading.Lock()
qr_mode_active = False  # QR okuma modu aktif mi?
ever_connected = False
batt_value = 0
last_10_readings = deque(maxlen=50)
active_connections = set()

# Paylaşımlı kamera karesi - QR okuma için
shared_camera_frame = None
shared_frame_lock = threading.Lock()
shared_frame_timestamp = 0
shared_frame_file = "/home/rise/clary/clary_camera_frame.npy"  # Paylaşımlı dosya yolu

# QR modu sinyal dosyası
CAMERA_SIGNAL_FILE = "/tmp/clary_qr_mode.signal"
_qr_monitor_stop_evt = threading.Event()

def check_qr_mode_signal():
    """QR modu sinyalini kontrol et"""
    global qr_mode_active
    try:
        # Sinyal dosyasının varlığını kontrol et
        signal_exists = os.path.exists(CAMERA_SIGNAL_FILE)

        if signal_exists:
            # Sinyal dosyası varsa QR modu aktif
            if not qr_mode_active:
                logger.info("✓ QR modu sinyali algılandı - paylaşımlı kamera modu AÇIK")
                qr_mode_active = True
            return True
        else:
            # Sinyal dosyası yoksa QR modu pasif
            if qr_mode_active:
                logger.info("✓ QR modu sinyali temizlendi - normal kamera modu")
                qr_mode_active = False
            return False
    except Exception as e:
        logger.error(f"QR modu sinyal kontrolü hatası: {e}")
        return qr_mode_active

def qr_signal_monitor_loop():
    """QR modu sinyal dosyasını sürekli izleyen thread"""
    global qr_mode_active

    # Thread başlarken QR modunun kapalı olduğundan emin ol
    qr_mode_active = False
    logger.info("QR sinyal monitörü başlatıldı - QR modu başlangıçta KAPALI")

    while not _qr_monitor_stop_evt.is_set():
        try:
            check_qr_mode_signal()

            # QR modu aktifse ve henüz işlem başlatılmamışsa
            if qr_mode_active and not getattr(qr_signal_monitor_loop, '_qr_processing', False):
                qr_signal_monitor_loop._qr_processing = True
                # QR okuma işlemini başlat
                logger.info("QR modu aktif - QR tarama thread'i başlatılıyor")
                threading.Thread(target=process_qr_scan, daemon=True).start()

        except Exception as e:
            logger.error(f"QR sinyal monitör hatası: {e}")
        time.sleep(0.05)  # 50ms aralıklarla kontrol et (daha hızlı tepki)
    logger.info("QR sinyal monitörü durdu")

def process_qr_scan():
    """QR kod tarama işlemini yürütür"""
    global qr_mode_active

    # QR modu aktif değilse işlemi başlatma
    if not qr_mode_active:
        logger.warning("QR tarama işlemi iptal - QR modu aktif değil!")
        qr_signal_monitor_loop._qr_processing = False
        return

    logger.info("="*60)
    logger.info("QR TARAMA İŞLEMİ BAŞLATILDI (main.py)")
    logger.info("="*60)

    try:
        # Kamera görüntüsünden QR kod oku (60 saniye timeout)
        qr_data = read_qr_from_camera_frames(timeout=60)

        if not qr_data:
            logger.error("✗ QR kod okunamadı - timeout")
            return

        logger.info(f"✓ QR kod okundu: {qr_data[:100]}...")

        # QR verisini parse et
        config, error = parse_qr_data(qr_data)

        if error:
            logger.error(f"✗ QR parse hatası: {error}")
            return

        if not config:
            logger.error("✗ Geçersiz QR verisi")
            return

        # WiFi yapılandırmasını uygula
        success = apply_wifi_config(config)

        if success:
            logger.info(f"✓ WiFi yapılandırması başarılı ({config['mode'].upper()} mode)")
        else:
            logger.error(f"✗ WiFi yapılandırması başarısız")

    except Exception as e:
        logger.error(f"QR tarama işlemi hatası: {e}", exc_info=True)
    finally:
        # QR modunu kapat
        try:
            if os.path.exists(CAMERA_SIGNAL_FILE):
                try:
                    os.remove(CAMERA_SIGNAL_FILE)
                    logger.info("QR modu sinyali temizlendi")
                except PermissionError:
                    # İzin hatası varsa sudo ile sil
                    try:
                        import subprocess
                        subprocess.run(['sudo', 'rm', '-f', CAMERA_SIGNAL_FILE], check=False)
                        logger.info("QR modu sinyali sudo ile temizlendi")
                    except Exception as se:
                        logger.warning(f"Sinyal dosyası sudo ile silinemedi: {se}")
        except Exception as e:
            logger.warning(f"Sinyal dosyası temizleme hatası: {e}")

        qr_mode_active = False
        qr_signal_monitor_loop._qr_processing = False

def read_qr_from_camera_frames(timeout=60):
    """
    Mevcut kamera akışından QR kod okur.
    Kamerayı kapatmadan mevcut frame'leri kullanır.
    """
    global qr_mode_active

    # QR modu aktif değilse işlemi başlatma
    if not qr_mode_active:
        logger.warning("QR kod taraması iptal - QR modu aktif değil!")
        return None

    logger.info(f"QR kod taraması başlatıldı (Timeout: {timeout}s)")

    start_time = time.time()
    detector = cv2.QRCodeDetector()
    frame_count = 0

    while (time.time() - start_time) < timeout:
        try:
            # QR modunun hala aktif olduğunu kontrol et
            if not qr_mode_active:
                logger.info("QR kod taraması durduruluyor - QR modu devre dışı bırakıldı")
                return None

            # Global kamera frame'ini kullan
            with shared_frame_lock:
                if shared_camera_frame is None:
                    time.sleep(0.1)
                    continue

                frame = shared_camera_frame.copy()

            frame_count += 1

            # Her 3 frame'de bir kontrol et (performans için)
            if frame_count % 3 != 0:
                time.sleep(0.05)
                continue

            # QR kod tespiti
            data, bbox, _ = detector.detectAndDecode(frame)

            if data and len(data.strip()) > 0:
                logger.info(f"✓ QR kod bulundu! ({frame_count} frame işlendi)")
                return data.strip()

            # Her 30 frame'de bir log
            if frame_count % 30 == 0:
                elapsed = time.time() - start_time
                logger.debug(f"QR taraması devam ediyor... ({frame_count} frame, {elapsed:.1f}s)")

            time.sleep(0.05)

        except Exception as e:
            logger.error(f"QR okuma hatası (frame {frame_count}): {e}")
            time.sleep(0.1)

    logger.warning(f"QR kod bulunamadı - timeout ({frame_count} frame işlendi)")
    return None

def parse_qr_data(qr_data):
    """QR kod verisini parse et ve mod/parametreleri döndür"""
    try:
        qr_data = qr_data.strip()

        # AP Mode formatı: APMODE2.4gch6 veya APMODE5gch36
        if qr_data.startswith("APMODE"):
            band_channel = qr_data[6:]  # "2.4gch6" veya "5gch36"

            if band_channel.startswith("2.4gch"):
                band = "2.4"
                hw_mode = "g"
                channel = int(band_channel[6:])  # "2.4gch6" -> 6
            elif band_channel.startswith("5gch"):
                band = "5"
                hw_mode = "a"
                channel = int(band_channel[4:])  # "5gch36" -> 36
            else:
                return None, f"Geçersiz AP band formatı: {band_channel}"

            # Kanal doğrulama
            valid_24_channels = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]
            valid_5_channels = [36, 40, 44, 48, 149, 153, 157, 161, 165]

            if band == "2.4" and channel not in valid_24_channels:
                return None, f"Geçersiz 2.4GHz kanalı: {channel}"
            if band == "5" and channel not in valid_5_channels:
                return None, f"Geçersiz 5GHz kanalı: {channel}"

            return {
                'mode': 'ap',
                'band': band,
                'hw_mode': hw_mode,
                'channel': channel
            }, None

        # WiFi MECARD formatı: WIFI:T:WPA;S:ssid;P:password;;
        elif qr_data.startswith("WIFI:"):
            import re

            ssid_match = re.search(r'S:([^;]+);', qr_data)
            pass_match = re.search(r'P:([^;]+);', qr_data)

            if not ssid_match:
                return None, "WiFi QR kodu SSID içermiyor"

            ssid = ssid_match.group(1)
            password = pass_match.group(1) if pass_match else ""

            if len(ssid) == 0:
                return None, "WiFi QR kodu boş SSID içeriyor"

            return {
                'mode': 'sta',
                'ssid': ssid,
                'password': password
            }, None

        else:
            return None, f"Desteklenmeyen QR formatı: {qr_data[:20]}..."

    except Exception as e:
        logger.error(f"QR parse hatası: {e}", exc_info=True)
        return None, str(e)

def apply_wifi_config(config):
    """
    QR koddan okunan WiFi yapılandırmasını uygular.
    script.txt içindeki scriptleri kullanır.
    """
    try:
        if config['mode'] == 'ap':
            logger.info(f"AP Mode yapılandırması: {config['band']}GHz band, kanal {config['channel']}")
            return configure_ap_mode_via_script(config['band'], config['hw_mode'], config['channel'])

        elif config['mode'] == 'sta':
            logger.info(f"STA Mode yapılandırması: SSID={config['ssid']}")
            return configure_sta_mode_via_script(config['ssid'], config['password'])

        else:
            logger.error(f"Bilinmeyen WiFi modu: {config['mode']}")
            return False

    except Exception as e:
        logger.error(f"WiFi yapılandırma hatası: {e}", exc_info=True)
        return False

def configure_sta_mode_via_script(ssid, password):
    """
    STA mode script'ini dinamik olarak günceller ve çalıştırır.
    wifi_change.py kodundaki gibi çalışır.
    """
    try:
        # script.txt'i oku
        script_file = os.path.join(BASE_DIR, "script.txt")
        if not os.path.exists(script_file):
            logger.error(f"script.txt bulunamadı: {script_file}")
            return False

        with open(script_file, 'r', encoding='utf-8') as f:
            script_content = f.read()

        # SSID ve PSK değerlerini değiştir
        import re

        # STA_MODE bölümündeki SSID ve PSK'yi değiştir
        script_content = re.sub(
            r'(# --- STA MODE ---.*?SSID=")[^"]*(")',
            rf'\1{ssid}\2',
            script_content,
            flags=re.DOTALL
        )

        script_content = re.sub(
            r'(# --- STA MODE ---.*?PSK=")[^"]*(")',
            rf'\1{password}\2',
            script_content,
            flags=re.DOTALL
        )

        # Güncellenmiş script'i geçici dosyaya yaz
        temp_script = "/tmp/sta_mode_update.sh"
        with open(temp_script, 'w', encoding='utf-8') as f:
            f.write(script_content)

        # Script'i çalıştır
        logger.info("STA mode script'i çalıştırılıyor...")
        result = subprocess.run(
            ["sudo", "bash", temp_script],
            capture_output=True,
            text=True,
            timeout=60
        )

        if result.returncode == 0:
            logger.info("✓ STA mode script başarıyla tamamlandı")

            # STA mode script'ini çalıştır
            sta_script = "/opt/lscope/bin/sta_mode.sh"
            if os.path.exists(sta_script):
                logger.info("sta_mode.sh çalıştırılıyor...")
                result2 = subprocess.run(
                    ["sudo", "bash", sta_script],
                    capture_output=True,
                    text=True,
                    timeout=60
                )

                if result2.returncode == 0:
                    logger.info(f"✓ WiFi STA moduna geçildi: {ssid}")
                    return True
                else:
                    logger.error(f"sta_mode.sh hatası: {result2.stderr}")
                    return False
            else:
                logger.error(f"sta_mode.sh bulunamadı: {sta_script}")
                return False
        else:
            logger.error(f"Script güncelleme hatası: {result.stderr}")
            return False

    except Exception as e:
        logger.error(f"STA mode yapılandırma hatası: {e}", exc_info=True)
        return False

def configure_ap_mode_via_script(band, hw_mode, channel):
    """
    AP mode'a geçer.
    script.txt içindeki ap_mode.sh script'ini çalıştırır.
    """
    try:
        ap_script = "/opt/lscope/bin/ap_mode.sh"

        if not os.path.exists(ap_script):
            logger.error(f"ap_mode.sh bulunamadı: {ap_script}")
            return False

        logger.info(f"AP Mode geçişi: {band}GHz, kanal {channel}")
        logger.info("ap_mode.sh çalıştırılıyor...")

        result = subprocess.run(
            ["sudo", "bash", ap_script],
            capture_output=True,
            text=True,
            timeout=60
        )

        if result.returncode == 0:
            logger.info(f"✓ WiFi AP moduna geçildi: {band}GHz, kanal {channel}")
            return True
        else:
            logger.error(f"ap_mode.sh hatası: {result.stderr}")
            return False

    except Exception as e:
        logger.error(f"AP mode yapılandırma hatası: {e}", exc_info=True)
        return False

# ============================== Model & Auth ==============================
class User(db.Model):
    id       = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(20), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)  # PLAINTEXT

DEFAULT_USER = ("rise", "simclever12345")

def ensure_default_user():
    db.create_all()
    u = User.query.filter_by(username=DEFAULT_USER[0]).first()
    if not u:
        db.session.add(User(username=DEFAULT_USER[0], password=DEFAULT_USER[1]))
        db.session.commit()
        logger.info("Varsayılan kullanıcı oluşturuldu: %s", DEFAULT_USER[0])
    else:
        # Mevcut kullanıcının şifresini artık zorla güncellemiyoruz.
        logger.info("Varsayılan kullanıcı mevcut: %s (şifre korunuyor)", DEFAULT_USER[0])

def verify_password(stored: str, provided: str) -> bool:
    return stored == provided

def login_required(fn):
    from functools import wraps
    @wraps(fn)
    def _wrap(*a, **k):
        if not session.get("uid"):
            return redirect(url_for("login"))
        return fn(*a, **k)
    return _wrap

def get_shared_camera_frame():
    """
    QR okuma için paylaşımlı kamera karesini al.
    Returns: (frame, timestamp) veya (None, 0)
    """
    global shared_camera_frame, shared_frame_lock, shared_frame_timestamp
    with shared_frame_lock:
        if shared_camera_frame is not None:
            return shared_camera_frame.copy(), shared_frame_timestamp
        return None, 0

# =========================== Sinyal / Hızlı çıkış =========================
def _graceful_exit(signum, frame):
    print(f"[SYS] Stop signal {signum}, exiting fast.")
    sys.exit(0)
signal.signal(signal.SIGTERM, _graceful_exit)
signal.signal(signal.SIGINT,  _graceful_exit)

# ============================ AP (hostapd) Ayarları =====================
HOSTAPD_PATHS = [
    "/etc/hostapd/hostapd.conf",
    "/etc/hostapd.conf",
]

CHANNELS_24 = list(range(1, 14))  # 1-13 (TR)
CHANNELS_5  = [36, 40, 44, 48, 149, 153, 157, 161]  # DFS dışı yaygın kanallar

# Wi-Fi Mod Yönetimi
def get_wifi_mode() -> str:
    """Mevcut Wi-Fi modunu döndürür: 'ap' veya 'sta'"""
    try:
        # hostapd servisinin durumunu kontrol et
        r = subprocess.run(["systemctl", "is-active", "hostapd"], capture_output=True, text=True)
        if r.returncode == 0 and r.stdout.strip() == "active":
            return "ap"
        return "sta"
    except Exception:
        # Varsayılan olarak AP modu
        return "ap"

def set_wifi_mode(mode: str) -> Tuple[bool, str]:
    """Wi-Fi modunu değiştirir: 'ap' veya 'sta'"""
    mode = mode.lower().strip()
    if mode not in ("ap", "sta"):
        return False, "Geçersiz mod (ap veya sta olmalı)"

    try:
        if mode == "ap":
            # STA modunu durdur, AP modunu başlat
            subprocess.run(["systemctl", "stop", "wpa_supplicant"], capture_output=True)
            subprocess.run(["systemctl", "disable", "wpa_supplicant"], capture_output=True)
            subprocess.run(["systemctl", "enable", "hostapd"], capture_output=True)
            subprocess.run(["systemctl", "start", "hostapd"], capture_output=True)
            return True, "AP modu etkinleştirildi"
        else:  # sta
            # AP modunu durdur, STA modunu başlat
            subprocess.run(["systemctl", "stop", "hostapd"], capture_output=True)
            subprocess.run(["systemctl", "disable", "hostapd"], capture_output=True)
            subprocess.run(["systemctl", "enable", "wpa_supplicant"], capture_output=True)
            subprocess.run(["systemctl", "start", "wpa_supplicant"], capture_output=True)
            # wlan0'ı yeniden yapılandır
            subprocess.run(["wpa_cli", "-i", "wlan0", "reconfigure"], capture_output=True)
            return True, "STA modu etkinleştirildi"
    except Exception as e:
        return False, f"Mod değiştirme hatası: {e}"

def hostapd_conf_path() -> str:
    for p in HOSTAPD_PATHS:
        if os.path.exists(p):
            return p
    return HOSTAPD_PATHS[0]

def read_ap_band_channel() -> Tuple[str, int]:
    """Hostapd config'ten mevcut band ve channel oku. Yoksa varsayılan (2.4,6)."""
    path = hostapd_conf_path()
    band, ch = "2.4", 6
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                l = line.strip()
                if not l or l.startswith("#"): continue
                if l.startswith("hw_mode="):
                    val = l.split("=",1)[1].strip().lower()
                    if val == "a": band = "5"
                    elif val in ("g","b","n"): band = "2.4"
                elif l.startswith("channel="):
                    try:
                        ch = int(l.split("=",1)[1].strip())
                    except Exception:
                        pass
    except Exception as e:
        logger.warning(f"hostapd okunamadı ({path}): {e}")
    # Clamp kanalı mevcut banda göre
    if band == "2.4" and ch not in CHANNELS_24: ch = 6
    if band == "5"   and ch not in CHANNELS_5:  ch = 36
    return band, ch

def read_ap_password() -> str:
    """hostapd.conf'tan mevcut wpa_passphrase'i oku."""
    path = hostapd_conf_path()
    if not os.path.exists(path):
        return "simclever123"  # Varsayılan
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                ls = line.strip()
                if ls.startswith("wpa_passphrase="):
                    return ls.split("=", 1)[1].strip()
    except Exception as e:
        logger.warning(f"hostapd şifre okunamadı ({path}): {e}")
    return "simclever123"

def write_ap_band_channel(band: str, channel: int) -> Tuple[bool, str]:
    """hostapd.conf içinde hw_mode ve channel güncelle.
    Başarı durumunda (True, mesaj), aksi halde (False, hata).
    """
    band = "5" if str(band).strip() in ("5","5.0","5ghz","a") else "2.4"
    channel = int(channel)
    if band == "2.4" and channel not in CHANNELS_24:
        return False, f"2.4 GHz için geçersiz kanal: {channel}"
    if band == "5" and channel not in CHANNELS_5:
        return False, f"5 GHz için geçersiz kanal: {channel}"

    path = hostapd_conf_path()
    try:
        if not os.path.exists(path):
            # Basit bir başlangıç içeriği oluştur
            base = [
                "interface=wlan0\n",
                "driver=nl80211\n",
                "ssid=OrangePiAP\n",
                "country_code=TR\n",
                "wpa=2\n",
                "wpa_passphrase=simclever123\n",
            ]
            with open(path, "w", encoding="utf-8") as f:
                f.writelines(base)
        # Dosyayı oku ve satır bazlı güncelle
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
        out = []
        saw_mode = False; saw_chan = False
        for l in lines:
            ls = l.strip()
            if ls.startswith("hw_mode="):
                out.append(f"hw_mode={'a' if band=='5' else 'g'}\n"); saw_mode = True
            elif ls.startswith("channel="):
                out.append(f"channel={channel}\n"); saw_chan = True
            else:
                out.append(l)
        if not saw_mode:
            out.append(f"hw_mode={'a' if band=='5' else 'g'}\n")
        if not saw_chan:
            out.append(f"channel={channel}\n")
        # Yedek al ve yaz
        try:
            import shutil
            shutil.copy2(path, path + ".bak")
        except Exception:
            pass
        with open(path, "w", encoding="utf-8") as f:
            f.writelines(out)
        return True, f"AP ayarları güncellendi: band={band} channel={channel}"
    except Exception as e:
        return False, f"hostapd yazılamadı ({path}): {e}"

def write_ap_password(new_password: str) -> Tuple[bool, str]:
    """hostapd.conf içinde wpa_passphrase güncelle.
    Başarı durumunda (True, mesaj), aksi halde (False, hata).
    """
    # Şifre validasyonu
    if not new_password or len(new_password) < 8:
        return False, "Şifre en az 8 karakter olmalıdır"
    if len(new_password) > 63:
        return False, "Şifre en fazla 63 karakter olabilir"

    path = hostapd_conf_path()
    try:
        if not os.path.exists(path):
            # Basit bir başlangıç içeriği oluştur
            base = [
                "interface=wlan0\n",
                "driver=nl80211\n",
                "ssid=OrangePiAP\n",
                "country_code=TR\n",
                "wpa=2\n",
                f"wpa_passphrase={new_password}\n",
            ]
            with open(path, "w", encoding="utf-8") as f:
                f.writelines(base)
        else:
            # Dosyayı oku ve şifreyi güncelle
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()
            out = []
            saw_pass = False
            for line in lines:
                ls = line.strip()
                if ls.startswith("wpa_passphrase="):
                    out.append(f"wpa_passphrase={new_password}\n")
                    saw_pass = True
                else:
                    out.append(line)

            if not saw_pass:
                out.append(f"wpa_passphrase={new_password}\n")

            # Yedek al ve yaz
            try:
                import shutil
                shutil.copy2(path, path + ".bak")
            except Exception:
                pass
            with open(path, "w", encoding="utf-8") as f:
                f.writelines(out)

        return True, "Wi-Fi şifresi güncellendi"
    except Exception as e:
        return False, f"hostapd yazılamadı ({path}): {e}"

def restart_hostapd() -> Tuple[bool,str]:
    last = ""
    cmds = [
        ["systemctl","restart","hostapd"],
        ["service","hostapd","restart"],
        ["/etc/init.d/hostapd","restart"],
    ]
    for cmd in cmds:
        try:
            p = subprocess.run(cmd, capture_output=True, text=True)
            if p.returncode == 0:
                return True, "hostapd yeniden başlatıldı"
            last = p.stderr or p.stdout
        except Exception as e:
            last = str(e)
    return False, f"hostapd restart başarısız: {last}"

# ============================ Yardımcı Fonksiyonlar =======================
def run(cmd: str) -> subprocess.CompletedProcess:
    return subprocess.run(cmd.split(), capture_output=True, text=True)

def get_connected_ssid() -> str:
    r = run("iw dev wlan0 link")
    m = re.search(r"SSID:\s*(.+)", r.stdout)
    if m:
        return m.group(1).strip()
    r2 = run("iwgetid -r")
    return r2.stdout.strip()

def signal_to_quality(signal_dbm):
    if signal_dbm is None: return 1
    s = float(signal_dbm)
    if s >= -50: return 5
    if s >= -60: return 4
    if s >= -70: return 3
    if s >= -80: return 2
    return 1

def scan_networks():
    r = run("iw dev wlan0 scan")
    networks, ssid, sig = [], None, None
    for line in r.stdout.splitlines():
        t = line.strip()
        if t.startswith("BSS "):
            if ssid is not None:
                networks.append({"SSID": ssid, "Quality": signal_to_quality(sig), "dBm": sig})
            ssid, sig = None, None
        elif t.startswith("SSID:"):
            ssid = t.split("SSID:", 1)[1].strip()
        elif t.startswith("signal:"):
            try:
                sig = float(t.split("signal:", 1)[1].split()[0])
            except Exception:
                sig = None
    if ssid is not None:
        networks.append({"SSID": ssid, "Quality": signal_to_quality(sig), "dBm": sig})
    networks = [n for n in networks if n["SSID"]]
    best = {}
    for n in networks:
        ss = n["SSID"]
        if ss not in best or n["Quality"] > best[ss]["Quality"]:
            best[ss] = n
    return sorted(best.values(), key=lambda x: x["Quality"], reverse=True)

def escape_wpa(v: str) -> str:
    return v.replace("\\", "\\\\").replace('"', r'\"')

def wpa_conf_path() -> str:
    p1 = "/etc/wpa_supplicant/wpa_supplicant-wlan0.conf"
    p2 = "/etc/wpa_supplicant/wpa_supplicant.conf"
    return p1 if os.path.exists(p1) else p2

def write_wpa_conf(ssid: str, psk: str, country="TR"):
    conf = f"""country={country}
ctrl_interface=DIR=/var/run/wpa_supplicant GROUP=netdev
update_config=1

network={{
\tssid="{escape_wpa(ssid)}"
\tpsk="{escape_wpa(psk)}"
\tkey_mgmt=WPA-PSK
}}
"""
    path = wpa_conf_path()
    os.makedirs("/etc/wpa_supplicant", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(conf)
    subprocess.run(["wpa_cli", "-i", "wlan0", "reconfigure"], capture_output=True)

# ====================== Batarya / PWM (tek hat) Okuyucu ===================
# ESP8266 D6 → Orange Pi PI3 (offset 259) PWM. Duty% → batt_value
# ENV:
#   BATT_PWM_LINE="/dev/gpiochip1:259"
#   BATT_PWM_PRINT_SEC="0.5"       # raporlama penceresi/sn
#   BATT_PWM_FREQ_HZ="1000"        # beklenen PWM frekansı (periyot filtreleme için)

BATT_PWM_LINE = os.environ.get("BATT_PWM_LINE", "/dev/gpiochip1:259")
BATT_PWM_PRINT_SEC = float(os.environ.get("BATT_PWM_PRINT_SEC", "0.5"))
BATT_PWM_FREQ_HZ = int(os.environ.get("BATT_PWM_FREQ_HZ", "1000"))

_batt_stop_evt = threading.Event()
_shutdown_stop_evt = threading.Event()

def _set_batt_value(new_val: int):
    """0-100 clamp + basit hareketli ortalama + kullanıcı görünümü için dönüşüm.

    PWM okuma mantığı:
    - PWM 100% olduğunda 99 olarak sınırla (100'de sıfır görünüyor)
    - Gerçek batarya %15'i kullanıcıya %0 olarak göster
    - 15-99 aralığını 0-99 aralığına ölçeklendir
    """
    global batt_value

    # Önce 0-100 arası clamp yap
    v = max(0, min(100, int(new_val)))

    # PWM 100% ise 99 yap (100'de okuma sıfır görünüyor)
    if v >= 100:
        v = 99

    # Ham değeri kaydet
    last_10_readings.append(v)
    raw_average = int(round(sum(last_10_readings) / len(last_10_readings)))

    # Kullanıcı görünümü için dönüşüm yap:
    # 0-15 arası -> 0
    # 15-99 arası -> 0-99 arası lineer ölçekleme
    if raw_average <= 15:
        batt_value = 0
    else:
        # 15-99 aralığını 0-99 aralığına ölçekle
        # Formül: (değer - 15) * 99 / (99 - 15)
        batt_value = int(round((raw_average - 15) * 99 / 84))

# === Zaman yardımcıları (monotonic ns) ===
def _now_ns():
    return time.monotonic_ns()

def _ev_ns(ev):
    """libgpiod sürümlerine uyumlu şekilde event zamanını ns cinsinden döndür."""
    ts = getattr(ev, "timestamp", None)
    if ts is not None:
        try:
            return int(ts)  # libgpiod >= 2.x: ns
        except Exception:
            pass
    sec = getattr(ev, "sec", None)
    nsec = getattr(ev, "nsec", None)
    if sec is not None and nsec is not None:
        return int(sec) * 1_000_000_000 + int(nsec)
    return _now_ns()

# --- libgpiod sürüm farkları için güvenli bekleme/sorgu sarmalayıcıları ---
def _event_wait(line, timeout_sec: float):
    """Sürüm farkları için tolerant event_wait."""
    try:
        return bool(line.event_wait(timeout=timeout_sec))   # yeni API (saniye)
    except TypeError:
        pass
    try:
        return bool(line.event_wait(timeout_sec))           # pozisyonel saniye
    except TypeError:
        pass
    try:
        return bool(line.event_wait(int(timeout_sec * 1000)))  # eski API (ms)
    except TypeError:
        pass
    try:
        return bool(line.event_wait(0))
    except Exception:
        return False

def _event_available(line):
    """0 ms bekleme ile olay var mı (bloklamasız) kontrolü."""
    return _event_wait(line, 0.0)

def gpio_batt_reader_pwm_gpiod():
    """gpiod ile PWM duty ölçer → batt_value = duty_high(%).
    Periyot-temelli: TH (rise→fall) ve T (rise→rise) üzerinden ortalama duty."""
    if not USE_GPIOD:
        logger.error("gpiod yok—PWM okuyucu başlatılamadı.")
        return
    if ":" not in BATT_PWM_LINE:
        logger.error("BATT_PWM_LINE formatı '/dev/gpiochipX:OFFSET' olmalı.")
        return

    chip_name, off_s = BATT_PWM_LINE.split(":")
    off = int(off_s)
    try:
        chip = gpiod.Chip(chip_name)
        line = chip.get_line(off)
        line.request(consumer="clary-pwm", type=gpiod.LINE_REQ_EV_BOTH_EDGES)
    except Exception as e:
        logger.error(f"PWM line açılamadı: {e}")
        return

    logger.info(f"PWM dinleniyor: {BATT_PWM_LINE} window={BATT_PWM_PRINT_SEC}s (cycle-based duty)")

    try:
        # Başlangıç seviyesi
        try:
            last_level = line.get_value()
            if last_level not in (0, 1):
                last_level = 0
        except Exception:
            last_level = 0

        # Periyot-temelli ölçüm durumları
        last_rise_ns  = None   # son yükselen kenar zamanı
        last_fall_ns  = None   # son düşen kenar zamanı
        have_high_ns  = False  # mevcut periyotta TH ölçüldü mü?

        # Pencere akümülatörleri
        sum_duty = 0.0
        cycle_count = 0

        last_ts_ns   = _now_ns()
        next_emit_ns = last_ts_ns + int(BATT_PWM_PRINT_SEC * 1_000_000_000)
        last_hb_ns   = last_ts_ns

        # Beklenen periyot (toleranslı doğrulama)
        T_exp_ns = int(1_000_000_000 / max(1, BATT_PWM_FREQ_HZ))
        T_min_ns = int(T_exp_ns * 0.30)  # %30 .. %170 tolerans
        T_max_ns = int(T_exp_ns * 1.70)

        while not _batt_stop_evt.is_set():
            # Kuyrukta event var mı?
            if _event_available(line):
                drained = 0
                while _event_available(line) and drained < 1024:
                    ev = line.event_read()
                    drained += 1

                    t_ns = _ev_ns(ev)
                    # Seviyeye göre durum makinesi
                    if ev.type == gpiod.LineEvent.RISING_EDGE:
                        # Önceki periyot için T hesaplanabilecek mi?
                        if last_rise_ns is not None:
                            T_ns = t_ns - last_rise_ns
                            if T_min_ns <= T_ns <= T_max_ns and have_high_ns and last_fall_ns is not None:
                                TH_ns = last_fall_ns - last_rise_ns
                                if 0 < TH_ns < T_ns:
                                    sum_duty += (TH_ns / T_ns)
                                    cycle_count += 1
                        # Yeni periyot başlangıcı
                        last_rise_ns = t_ns
                        have_high_ns = False  # yeni periyotta TH henüz ölçülmedi
                        last_level = 1

                    else:  # FALLING
                        last_fall_ns = t_ns
                        # Bu düşüş, içinde bulunduğumuz periyodun TH'ını belirler (rise→fall)
                        if last_rise_ns is not None and last_fall_ns > last_rise_ns:
                            have_high_ns = True
                        last_level = 0

            else:
                # CPU dinlendir
                time.sleep(0.002)

            # Pencere sonu: sonucu üret
            now_ns = _now_ns()
            if now_ns >= next_emit_ns:
                if cycle_count > 0:
                    pct = (sum_duty / cycle_count) * 100.0
                    _set_batt_value(int(round(pct)))
                    logger.info(
                        f"PWM window: cycles={cycle_count}, avg_duty_high={pct:.1f}% -> batt={batt_value}% "
                        f"(Texp={T_exp_ns/1e6:.2f}ms)"
                    )
                else:
                    logger.info("PWM window: cycles=0 (geçerli periyot oluşmadı) — hat/chip/offset veya frekans filtresini kontrol edin.")

                # pencereyi sıfırla
                sum_duty    = 0.0
                cycle_count = 0
                next_emit_ns = now_ns + int(BATT_PWM_PRINT_SEC * 1_000_000_000)

            # 2 sn heartbeat
            if now_ns - last_hb_ns >= 2_000_000_000:
                logger.info(f"PWM hb: batt={batt_value}%, last_level={last_level}")
                last_hb_ns = now_ns

    except Exception as e:
        logger.error(f"PWM okuma döngüsü hatası: {e}")
    finally:
        try:
            line.release()
        except:
            pass
        try:
            chip.close()
        except:
            pass


def gpio_shutdown_watcher():
    """/dev/gpiochipX:OFFSET formatındaki hatta LOW algılandınca shutdown tetikler."""
    if not USE_GPIOD:
        logger.error("gpiod yok—shutdown watcher başlatılamadı.")
        return
    if ":" not in SHUTDOWN_GPIO_LINE:
        logger.error("SHUTDOWN_GPIO_LINE formatı '/dev/gpiochipX:OFFSET' olmalı.")
        return
    chip_name, off_s = SHUTDOWN_GPIO_LINE.split(":", 1)
    try:
        off = int(off_s)
    except Exception:
        logger.error(f"Geçersiz offset: {off_s}")
        return

    try:
        chip = gpiod.Chip(chip_name)
        line = chip.get_line(off)
        # Her iki kenarı dinle (falling ile ilgileneceğiz)
        line.request(consumer="clary-shutdown", type=gpiod.LINE_REQ_EV_BOTH_EDGES)
        try:
            init_val = line.get_value()
        except Exception:
            init_val = 1
        logger.info(f"Shutdown watcher aktif: {SHUTDOWN_GPIO_LINE} (init={init_val})")

        debounce_s = max(0, SHUTDOWN_DEBOUNCE_MS) / 1000.0
        while not _shutdown_stop_evt.is_set():
            if _event_available(line):
                drained = 0
                while _event_available(line) and drained < 256:
                    ev = line.event_read(); drained += 1
                    # 2.x ve 1.x API: falling sabit ismi farklı olabilir; tip üzerinden karar verelim
                    try:
                        is_falling = (ev.type == gpiod.LineEvent.FALLING_EDGE)
                    except Exception:
                        is_falling = True  # emniyet için
                    if is_falling:
                        # Debounce: kısa gecikme sonra değer hala LOW mu kontrol et
                        if debounce_s > 0:
                            time.sleep(debounce_s)
                        try:
                            val = line.get_value()
                        except Exception:
                            val = 0
                        if val == 0:
                            logger.warning("Shutdown GPIO LOW algılandı — sistem kapatılıyor…")
                            try:
                                os.system("sync")
                            except Exception:
                                pass
                            try:
                                subprocess.Popen(["sudo","shutdown","-h","now"])  # ayrışık çağrı
                            except Exception as e:
                                logger.error(f"shutdown çağrısı başarısız: {e}")
                            # İş parçacığını sonlandır
                            return
            else:
                time.sleep(0.01)
    except Exception as e:
        logger.error(f"Shutdown watcher hata: {e}")
    finally:
        try:
            line.release()
        except Exception:
            pass
        try:
            chip.close()
        except Exception:
            pass


# ============================== Kamera / Stream ===========================
def release_camera_for_qr():
    """QR okuma modu için kamerayı serbest bırak"""
    global camera, qr_mode_active
    with camera_lock:
        qr_mode_active = True
        if camera is not None and camera.isOpened():
            camera.release()
            camera = None
            logger.info("Kamera QR okuma modu için serbest bırakıldı")
            time.sleep(0.5)  # Kameranın tamamen serbest kalması için kısa bekleme
        return True

def reacquire_camera_after_qr():
    """QR okuma modundan sonra kamerayı yeniden başlat"""
    global qr_mode_active
    with camera_lock:
        qr_mode_active = False
        logger.info("QR okuma modu bitti, kamera yeniden başlatılıyor")
        time.sleep(0.5)  # QR okuma modunun kamerayı tamamen serbest bırakması için bekleme
        init_camera()

def init_camera():
    global camera, qr_mode_active

    # QR modu aktifse kamerayı açma
    if qr_mode_active:
        logger.info("QR modu aktif - kamera açılmıyor")
        return False

    if camera is not None and camera.isOpened():
        camera.release()
    camera = None
    for idx in range(3):
        try:
            # Her denemeden önce QR modunu kontrol et
            if qr_mode_active:
                logger.info("QR modu aktif oldu - kamera açma iptal edildi")
                return False

            cam = cv2.VideoCapture(idx)
            if cam.isOpened():
                ok, frame = cam.read()
                if ok and frame is not None and frame.size > 0:
                    cam.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                    camera = cam
                    logger.info(f"Kamera {idx} bağlandı.")
                    return True
                cam.release()
        except Exception as e:
            logger.error(f"Kamera {idx} açma hatası: {e}")
    logger.warning("Kamera bulunamadı.")
    return False

def create_placeholder(text):
    img = np.zeros((480, 640, 3), dtype=np.uint8)
    font = cv2.FONT_HERSHEY_SIMPLEX
    (tw, th), _ = cv2.getTextSize(text, font, 1, 2)
    x = (img.shape[1]-tw)//2; y = (img.shape[0]+th)//2
    cv2.putText(img, text, (x, y), font, 1, (255,255,255), 2)
    return img

def arkaplan_isi():
    logger.info("Arka plan batarya veri yayını başladı.")
    tmp = 0; error_count = 0; max_errors = 5
    while True:
        try:
            if active_connections:
                socketio.emit('adc_veri', {'deger': batt_value}, namespace='/adc')
            time.sleep(0.2); tmp += 1
            error_count = 0
        except Exception as e:
            error_count += 1
            logger.error(f"Batarya gönderim hatası: {e}")
            time.sleep(5 if error_count >= max_errors else 1)

def generate_frames():
    global camera, ever_connected, shared_camera_frame, shared_frame_lock, shared_frame_timestamp, shared_frame_file
    connection_retry_timer = 0
    error_count, max_errors = 0, 3
    last_ok = None; last_ts = 0
    frame_count = 0  # Kare sayacı

    while True:
        try:

            # Normal işlem
            if camera is None or not camera.isOpened():
                now = time.time()
                if now - connection_retry_timer >= 1:
                    connection_retry_timer = now
                    with camera_lock:
                        if not qr_mode_active:
                            init_camera()
                if camera is None or not camera.isOpened():
                    ph = create_placeholder("Kamera bekleniyor...")
                    _, buf = cv2.imencode(".jpg", ph)
                    yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + buf.tobytes() + b"\r\n"
                    eventlet.sleep(0.1); continue

            ok, frame = camera.read()
            now = time.time()
            if not ok and last_ok is not None and now - last_ts < 2:
                _, buf = cv2.imencode(".jpg", last_ok, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
                yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + buf.tobytes() + b"\r\n"
                eventlet.sleep(0.04); continue

            if not ok or frame is None:
                camera.release(); camera = None
                ph = create_placeholder("Kare yok — yeniden bağlanılıyor…")
                _, buf = cv2.imencode(".jpg", ph)
                yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + buf.tobytes() + b"\r\n"
                eventlet.sleep(0.1); continue

            # Kayıt modülüne son kareyi ilet
            try:
                if 'recordsVideo' in globals():
                    recordsVideo.push_frame(frame)
            except Exception as _e:
                logger.error(f"recordsVideo.push_frame hatası: {_e}")

            # Paylaşımlı kareyi güncelle (QR okuma için) - SADECE QR MODU AKTİFSE
            frame_count += 1
            if qr_mode_active:
                with shared_frame_lock:
                    shared_camera_frame = frame.copy()
                    shared_frame_timestamp = now

                    # Dosyaya da yaz (recovery_gpio_monitor.py için)
                    # Her 3 karede bir yaz (performans için)
                    if frame_count % 3 == 0:
                        try:
                            np.save(shared_frame_file, frame)
                            # İlk 3 başarılı yazımda log göster
                            if frame_count <= 9:
                                logger.info(f"✓ Paylaşımlı kare dosyasına yazıldı: {shared_frame_file} (kare #{frame_count})")
                        except Exception as save_err:
                            logger.warning(f"Paylaşımlı kare kaydetme hatası: {save_err}")

            ever_connected = True; error_count = 0
            last_ok = frame.copy(); last_ts = now

            # QR okuma modu aktifse stream'e placeholder göster, aksi halde normal kare göster
            if qr_mode_active:
                ph = create_placeholder("QR Kod Okunuyor...")
                _, buf = cv2.imencode(".jpg", ph, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
            else:
                _, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 85])

            yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + buf.tobytes() + b"\r\n"

        except Exception as e:
            error_count += 1; logger.error(f"generate_frames hata {error_count}/{max_errors}: {e}")
            if camera is not None:
                try:
                    camera.release()
                except Exception:
                    pass
            camera = None
            if error_count >= max_errors:
                logger.warning("Çok hata — 3 sn bekleme"); eventlet.sleep(3); error_count = 0
            ph = create_placeholder(f"Hata: {e}")
            _, buf = cv2.imencode(".jpg", ph)
            yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + buf.tobytes() + b"\r\n"
        eventlet.sleep(0.055)  # ~18 fps

# ================================= Rotalar ================================
@app.route("/")
def index():
    return render_template("flask_stream_index_socketio.html")

@socketio.on("connect", namespace="/adc")
def sock_connect():
    sid = request.sid
    active_connections.add(sid)
    logger.info(f"SocketIO bağlandı - SID: {sid}")

    # Bağlantı logu
    if syslog:
        try:
            ip_addr = request.remote_addr if hasattr(request, 'remote_addr') else 'Unknown'
            syslog.log_system_event("WEBSOCKET_CONNECT",
                                   f"WebSocket bağlantısı kuruldu",
                                   "INFO", sid=sid, ip_address=ip_addr)
        except Exception:
            pass

@socketio.on("disconnect", namespace="/adc")
def sock_disconnect():
    sid = request.sid
    if sid in active_connections: active_connections.remove(sid)
    logger.info(f"SocketIO ayrıldı - SID: {sid}")

    # Bağlantı kopma logu
    if syslog:
        try:
            syslog.log_system_event("WEBSOCKET_DISCONNECT",
                                   f"WebSocket bağlantısı koptu",
                                   "INFO", sid=sid)
        except Exception:
            pass

@app.route("/video_feed")
def video_feed():
    return Response(generate_frames(), mimetype="multipart/x-mixed-replace; boundary=frame")

# --- Kimlik ---
@app.route("/login", methods=["GET","POST"])
def login():
    if session.get("uid"):
        return redirect(url_for("control_wifi"))

    if request.method == "POST":
        uname = request.form.get("username","").strip()
        pwd   = request.form.get("password","").strip()
        u = User.query.filter_by(username=uname).first()

        # IP ve User-Agent bilgisi al
        ip_addr = request.remote_addr
        user_agent = request.headers.get('User-Agent', 'Unknown')

        if u and verify_password(u.password, pwd):
            session["uid"]  = u.id
            session["user"] = u.username
            flash("Giriş başarılı.", "success")

            # Başarılı giriş logu
            if syslog:
                try:
                    syslog.log_auth_attempt(uname, True, ip_addr, user_agent)
                except Exception:
                    pass

            return redirect(url_for("control_wifi"))

        # Başarısız giriş logu
        flash("Hatalı kullanıcı adı/şifre.", "danger")
        if syslog:
            try:
                reason = "User not found" if not u else "Wrong password"
                syslog.log_auth_attempt(uname, False, ip_addr, user_agent, reason)
            except Exception:
                pass

    return render_template("login_csrf.html")

@app.route("/logout")
def logout():
    username = session.get("user", "Unknown")
    ip_addr = request.remote_addr

    session.clear()
    flash("Çıkış yapıldı.", "info")

    # Logout logu
    if syslog:
        try:
            syslog.log_session_event(username, "LOGOUT", ip_addr)
        except Exception:
            pass

    return redirect(url_for("login"))

# ============================ Wi-Fi Script Yardımcıları ===================
def _is_posix() -> bool:
    return os.name == "posix"

def _is_root() -> bool:
    if not _is_posix():
        return False
    try:
        return os.geteuid() == 0  # type: ignore[attr-defined]
    except Exception:
        return False

def _have_sudo_noninteractive() -> bool:
    if not _is_posix():
        return False
    try:
        p = subprocess.run(["sudo", "-n", "true"], capture_output=True, text=True)
        return p.returncode == 0
    except Exception:
        return False

def _run3(cmd: Union[list, str], timeout: int = 30):
    try:
        if isinstance(cmd, list):
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        else:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, shell=True)
        return p.returncode, p.stdout, p.stderr
    except Exception as e:
        return 1, "", str(e)

def _wifi_ensure_dir(path: str) -> tuple[bool, str]:
    try:
        os.makedirs(path, exist_ok=True)
        return True, ""
    except PermissionError:
        if _have_sudo_noninteractive():
            code, out, err = _run3(["sudo", "-n", "install", "-d", "-m", "755", path])
            return (code == 0), (out or err)
        return False, "Klasör oluşturma izni yok"
    except Exception as e:
        return False, str(e)

def _wifi_atomic_write(dest_path: str, content: str) -> tuple[bool, str]:
    import tempfile, shutil as _sh
    dest_dir = os.path.dirname(dest_path) or "/"
    tmp = None

    # Önce hedef dizinde geçici dosya oluşturmayı dene (aynı dosya sistemi için)
    try:
        os.makedirs(dest_dir, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8", dir=dest_dir, prefix=".tmp-") as tf:
            tmp = tf.name
            tf.write(content)
            tf.flush()
            os.fsync(tf.fileno())
    except Exception:
        # Hedef dizinde başarısız olursa sistem tmp'de oluştur
        try:
            with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as tf:
                tmp = tf.name
                tf.write(content)
                tf.flush()
                os.fsync(tf.fileno())
        except Exception as e:
            return False, f"Geçici dosya yazılamadı: {e}"

    # Yedek al (best-effort)
    try:
        if os.path.exists(dest_path):
            _sh.copy2(dest_path, dest_path + ".bak")
    except Exception:
        pass

    # Atomik replace dene
    try:
        os.makedirs(dest_dir, exist_ok=True)
        os.replace(tmp, dest_path)
        return True, ""
    except PermissionError:
        if not _have_sudo_noninteractive():
            try:
                os.unlink(tmp)
            except Exception:
                pass
            return False, "Yazma izni yok (root veya sudoers gerekli)"
        code, out, err = _run3(["sudo", "-n", "install", "-D", "-m", "644", tmp, dest_path])
        try:
            os.unlink(tmp)
        except Exception:
            pass
        if code == 0:
            return True, ""
        return False, (err or out)
    except OSError as e:
        # Cross-device link hatası (EXDEV - errno 18)
        if e.errno == errno.EXDEV or e.errno == 18:
            # Farklı dosya sistemleri arası taşıma - sudo install kullan
            if _have_sudo_noninteractive():
                code, out, err = _run3(["sudo", "-n", "install", "-D", "-m", "644", tmp, dest_path])
                try:
                    os.unlink(tmp)
                except Exception:
                    pass
                if code == 0:
                    return True, ""
                return False, f"sudo ile yazma başarısız (EXDEV): {err or out}"

            # sudo yoksa manuel kopyalama dene
            try:
                with open(dest_path, "w", encoding="utf-8") as out_f, open(tmp, "r", encoding="utf-8") as in_f:
                    out_f.write(in_f.read())
                    out_f.flush()
                    os.fsync(out_f.fileno())
                try:
                    os.unlink(tmp)
                except Exception:
                    pass
                return True, ""
            except PermissionError:
                try:
                    os.unlink(tmp)
                except Exception:
                    pass
                return False, "Yazma izni yok (EXDEV). Root veya sudoers gerekli."
            except Exception as e2:
                try:
                    os.unlink(tmp)
                except Exception:
                    pass
                return False, f"Dosya yazma hatası (EXDEV fallback): {e2}"

        # EXDEV değilse genel hata
        try:
            os.unlink(tmp)
        except Exception:
            pass
        return False, f"Dosya yazma hatası: {e}"
    except Exception as e:
        try:
            if tmp:
                os.unlink(tmp)
        except Exception:
            pass
        return False, f"Dosya yazma hatası: {e}"

def _wifi_deploy_exec(path: str, content: str) -> tuple[bool, str]:
    ok, emsg = _wifi_atomic_write(path, content)
    if not ok:
        return False, emsg
    try:
        os.chmod(path, 0o755)
        return True, ""
    except PermissionError:
        if _have_sudo_noninteractive():
            code, out, err = _run3(["sudo", "-n", "chmod", "+x", path])
            return (code == 0), (err or out)
        return False, "chmod izni yok"
    except Exception as e:
        return False, str(e)

def _opt_noexec() -> bool:
    if not _is_posix():
        return False
    code, out, _ = _run3(["mount"])
    if code != 0:
        return False
    for line in out.splitlines():
        if " /opt " in line and "noexec" in line:
            return True
    return False

def _wifi_install_alt_and_symlink(src: str, alt: str) -> tuple[bool, str]:
    if _have_sudo_noninteractive():
        c1, o1, e1 = _run3(["sudo", "-n", "install", "-Dm755", src, alt])
        if c1 != 0:
            return False, (e1 or o1)
        c2, o2, e2 = _run3(["sudo", "-n", "ln", "-sf", alt, src])
        if c2 != 0:
            return False, (e2 or o2)
        return True, ""
    return False, "sudo yok"

# sed tek tırnak güvenli kaçış yardımı (shell single-quote içinde değer gömmek için)
# 'foo'bar' -> 'foo'"'"'bar'

def _sed_escape(val: str) -> str:
    # sed replacement için: & → \& (match tümcesi), sonra single-quote güvenli gömme
    s = str(val).replace("&", r"\&")
    return s.replace("'", "'\"'\"'")

def _read_ap_ssid_from_hostapd() -> str:
    path = hostapd_conf_path()
    if not os.path.exists(path):
        return "OrangePiAP"
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                ls = line.strip()
                if ls.startswith("ssid=") and not ls.startswith("ssid2="):
                    return ls.split("=", 1)[1].strip()
    except Exception:
        pass
    return "OrangePiAP"

def _sta_script_content(ssid: str, psk: str) -> str:
    return f"""#!/usr/bin/env bash
set -euo pipefail
LOG=/var/log/wifi_mode.log
SSID={shlex.quote(ssid)}
PSK={shlex.quote(psk)}

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

def _ap_script_content(ap_ssid: str, ap_psk: str, iface: str = "wlan0") -> str:
    ap_ssid_esc = _sed_escape(ap_ssid)
    ap_psk_esc  = _sed_escape(ap_psk)
    return f"""#!/usr/bin/env bash
set -euo pipefail
LOG=/var/log/wifi_mode.log
IFACE={shlex.quote(iface)}

echo "[ap_mode] $(date '+%F %T')" | tee -a "$LOG"

# NetworkManager wlan0'ı yönetmesin
systemctl stop NetworkManager || true
mkdir -p /etc/NetworkManager/conf.d
cat >/etc/NetworkManager/conf.d/unmanaged.conf <<EOF
[keyfile]
unmanaged-devices=interface-name:{shlex.quote(iface)}
EOF
systemctl restart NetworkManager || true

# hostapd.conf içeriğinde SSID/PSK'yi garanti et
if [ -f /etc/hostapd/hostapd.conf ]; then
  sed -i 's|^ssid=.*|ssid={ap_ssid_esc}|' /etc/hostapd/hostapd.conf || true
  sed -i 's|^wpa_passphrase=.*|wpa_passphrase={ap_psk_esc}|' /etc/hostapd/hostapd.conf || true
fi
if [ -f /etc/default/hostapd ]; then
  sed -i 's|^#\?DAEMON_CONF=.*|DAEMON_CONF="/etc/hostapd/hostapd.conf"|' /etc/default/hostapd || true
else
  echo 'DAEMON_CONF="/etc/hostapd/hostapd.conf"' >/etc/default/hostapd
fi

systemctl unmask hostapd || true
systemctl daemon-reload || true
systemctl enable --now wlan0-static.service || true
systemctl enable --now dnsmasq || true
systemctl enable --now hostapd || true

systemctl restart hostapd || true

echo "[ap_mode OK] $(date '+%F %T')  (SSID: {ap_ssid})" | tee -a "$LOG"
"""

def _run_script(path: str, timeout: int = 90) -> tuple[bool, str]:
    cmd = [path]
    if _is_root():
        pass
    elif _have_sudo_noninteractive():
        cmd = ["sudo", "-n", path]
    else:
        return False, "Root/sudo yetkisi yok (sudoers ekleyin)"
    code, out, err = _run3(cmd, timeout=timeout)
    if code == 0:
        return True, out.strip()
    return False, (err or out)

def _schedule_nm_restart(delay_seconds: int = 15) -> None:
    """NetworkManager'ı gecikmeli olarak yeniden başlat.
    Eventlet varsa eventlet.spawn_after ile, yoksa threading ile çalıştırır.
    Root ya da sudo -n erişimi varsa çalışır; aksi halde yalnızca log yazar.
    """
    try:
        if not _is_posix():
            logger.info("NM restart planlanmadı: POSIX değil")
            return

        def _job():
            try:
                logger.info(f"NM restart {delay_seconds}s sonra tetiklenecek…")
                time.sleep(delay_seconds)
                cmd = ["systemctl", "restart", "NetworkManager"]
                if _is_root():
                    pass
                elif _have_sudo_noninteractive():
                    cmd = ["sudo", "-n"] + cmd
                else:
                    logger.warning("NM restart atlandı: sudo/root izni yok")
                    return
                p = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                if p.returncode == 0:
                    logger.info("NM restart başarıyla tamamlandı")
                else:
                    logger.warning(f"NM restart başarısız: {p.stderr or p.stdout}")
            except Exception as e:
                logger.error(f"NM restart görevi hatası: {e}")

        # Eventlet varsa onun zamanlayıcısını kullan
        try:
            import eventlet as _ev
            _ev.spawn_n(_job)
        except Exception:
            # Fallback: klasik thread
            threading.Thread(target=_job, daemon=True).start()
    except Exception as e:
        logger.error(f"NM restart planlama hatası: {e}")

# --- Wi-Fi ---
@app.route("/control_wifi")
@login_required
def control_wifi():
    current_mode = get_wifi_mode()
    band, ch = read_ap_band_channel()
    current_password = read_ap_password()
    # STA sekmesi sadece form gösterir; ağ taraması yapılmaz
    return render_template(
        "wifi_settings_simple.html",
        mode=current_mode,
        band=band,
        channel=ch,
        channels_24=CHANNELS_24,
        channels_5=CHANNELS_5,
        current_password=current_password,
    )

@app.route("/apply_band_channel", methods=["POST"])
@login_required
def apply_band_channel():
    band = (request.form.get("band") or "2.4").strip()
    try:
        channel = int(request.form.get("channel") or 6)
    except Exception:
        channel = 6
    ok, msg = write_ap_band_channel(band, channel)
    if not ok:
        flash(msg, "danger")
        return redirect(url_for("control_wifi"))

    # Script'i hostapd ile senkronize et ve çalıştır
    if _is_posix():
        ap_ssid = _read_ap_ssid_from_hostapd()
        ap_psk  = read_ap_password()
        bin_dir = "/opt/lscope/bin"
        dok, dem = _wifi_ensure_dir(bin_dir)
        if dok:
            ap_path = f"{bin_dir}/ap_mode.sh"
            ok2, em2 = _wifi_deploy_exec(ap_path, _ap_script_content(ap_ssid, ap_psk))
            if ok2 and _opt_noexec():
                _wifi_install_alt_and_symlink(ap_path, "/usr/local/sbin/ap_mode.sh")
            ran, rmsg = _run_script(ap_path, timeout=60)
            # GECİKMELİ NM RESTART
            _schedule_nm_restart(15)
            flash((msg + (" — AP script: OK" if ran else f" — AP script hata: {rmsg}")), "success" if ran else "warning")
            return redirect(url_for("control_wifi"))
        else:
            flash(f"{msg} — Script klasörü oluşturulamadı: {dem}", "warning")
            return redirect(url_for("control_wifi"))

    # POSIX değilse klasik yol
    rok, rmsg = restart_hostapd()
    flash((msg + (" — " + rmsg if rmsg else "")), "success" if rok else "warning")
    return redirect(url_for("control_wifi"))

@app.route("/apply_password", methods=["POST"])
@login_required
def apply_password():
    new_password = request.form.get("password", "").strip()
    ok, msg = write_ap_password(new_password)
    if not ok:
        flash(msg, "danger")
        return redirect(url_for("control_wifi"))

    if _is_posix():
        ap_ssid = _read_ap_ssid_from_hostapd()
        bin_dir = "/opt/lscope/bin"
        dok, dem = _wifi_ensure_dir(bin_dir)
        if dok:
            ap_path = f"{bin_dir}/ap_mode.sh"
            ok2, em2 = _wifi_deploy_exec(ap_path, _ap_script_content(ap_ssid, new_password))
            if ok2 and _opt_noexec():
                _wifi_install_alt_and_symlink(ap_path, "/usr/local/sbin/ap_mode.sh")
            ran, rmsg = _run_script(ap_path, timeout=60)
            # GECİKMELİ NM RESTART
            _schedule_nm_restart(15)
            flash((msg + (" — AP script: OK" if ran else f" — AP script hata: {rmsg}")), "success" if ran else "warning")
            return redirect(url_for("control_wifi"))
        else:
            flash(f"{msg} — Script klasörü oluşturulamadı: {dem}", "warning")
            return redirect(url_for("control_wifi"))

    rok, rmsg = restart_hostapd()
    flash((msg + (" — " + rmsg if rmsg else "")), "success" if rok else "warning")
    return redirect(url_for("control_wifi"))

@app.route("/connect_sta_network", methods=["POST"])
@login_required
def connect_sta_network():
    """STA modunda bir ağa bağlan: script'i üret ve çalıştır."""
    ssid = request.form.get("ssid", "").strip()
    password = request.form.get("password", "").strip()

    if not ssid:
        flash("SSID boş olamaz", "danger")
        return redirect(url_for("control_wifi"))
    if len(password) < 8:
        flash("Şifre en az 8 karakter olmalıdır", "danger")
        return redirect(url_for("control_wifi"))

    if not _is_posix():
        flash("Bu işlem yalnızca cihaz üzerinde (Linux) desteklenir.", "danger")
        return redirect(url_for("control_wifi"))

    # wpa_supplicant'ı da güncelle (yedekli yaklaşım)
    try:
        write_wpa_conf(ssid, password)
    except Exception as e:
        logging.getLogger(__name__).warning(f"wpa_supplicant yazılamadı: {e}")

    bin_dir = "/opt/lscope/bin"
    dok, dem = _wifi_ensure_dir(bin_dir)
    if not dok:
        flash(f"Script klasörü oluşturulamadı: {dem}", "danger")
        return redirect(url_for("control_wifi"))

    sta_path = f"{bin_dir}/sta_mode.sh"
    ok2, em2 = _wifi_deploy_exec(sta_path, _sta_script_content(ssid, password))
    if not ok2:
        flash(f"sta_mode.sh yazılamadı: {em2}", "danger")
        return redirect(url_for("control_wifi"))

    if _opt_noexec():
        _wifi_install_alt_and_symlink(sta_path, "/usr/local/sbin/sta_mode.sh")

    ran, rmsg = _run_script(sta_path, timeout=90)
    # GECİKMELİ NM RESTART
    _schedule_nm_restart(15)
    if ran:
        flash(f"Ayarlar kaydedildi. {ssid} ağına bağlanılıyor…", "success")
    else:
        flash(f"Script çalıştırma hatası: {rmsg}", "danger")
    return redirect(url_for("control_wifi"))

# --- Bilgi/yardımcı ---
@app.route("/version")
def version():
    return "1.1.0-pwm"

@app.route("/batteryvalue")
def batteryvalue():
    return str(batt_value)

@app.route("/ip_and_device")
def ip_and_device():
    wlan0_ip, hostname = "—", "—"
    try:
        ip = subprocess.run(["ip","-4","addr","show","dev","wlan0"], capture_output=True, text=True).stdout
        m = re.search(r"inet\s+([\d\.]+)", ip); wlan0_ip = m.group(1) if m else "—"
        hostname = subprocess.run(["hostname"], capture_output=True, text=True).stdout.strip()
    except Exception as e:
        logger.error(f"IP alma hatası: {e}")
    return f"IP: {wlan0_ip}, Cihaz: {hostname}"

@app.route("/update")
def update_page():
    """Sistem güncelleme sayfası"""
    if "uid" not in session:
        return redirect(url_for("login"))
    return render_template("update.html")

@app.route("/update_system", methods=["POST"])
def update_system():
    """GitHub'dan güncellemeleri çek ve mevcut klasöre uygula"""
    if "uid" not in session:
        return {"success": False, "message": "Oturum gerekli"}, 401

    try:
        repo_url = "https://github.com/Yusufcmd/OrangepiLaryV1.git"
        target_dir = BASE_DIR  # Mevcut çalışma dizini

        logger.info(f"Güncelleme başlatıldı - Hedef: {target_dir}")

        # Git safe.directory ayarını ekle (güvenlik hatası önleme)
        try:
            subprocess.run(
                ["git", "config", "--global", "--add", "safe.directory", target_dir],
                capture_output=True,
                text=True,
                timeout=10
            )
            logger.info(f"Git safe.directory eklendi: {target_dir}")
        except Exception as e:
            logger.warning(f"Git safe.directory eklenemedi: {e}")

        # Git deposu var mı kontrol et
        git_dir = os.path.join(target_dir, ".git")

        if os.path.exists(git_dir):
            # Klasör zaten bir git deposuysa - normal güncelleme
            logger.info("Mevcut git deposu güncelleniyor...")

            # Remote kontrolü yap
            remote_check = subprocess.run(
                ["git", "-C", target_dir, "remote", "get-url", "origin"],
                capture_output=True,
                text=True,
                timeout=10
            )

            # Remote yoksa veya yanlışsa, doğru remote'u ekle/güncelle
            if remote_check.returncode != 0:
                logger.info("Origin remote ekleniyor...")
                subprocess.run(
                    ["git", "-C", target_dir, "remote", "add", "origin", repo_url],
                    capture_output=True,
                    text=True,
                    timeout=10
                )
            elif repo_url not in remote_check.stdout:
                logger.info("Origin remote güncelleniyor...")
                subprocess.run(
                    ["git", "-C", target_dir, "remote", "set-url", "origin", repo_url],
                    capture_output=True,
                    text=True,
                    timeout=10
                )

            # Fetch yap
            logger.info("Uzak depo bilgileri getiriliyor (fetch)...")
            fetch_result = subprocess.run(
                ["git", "-C", target_dir, "fetch", "origin"],
                capture_output=True,
                text=True,
                timeout=60
            )

            if fetch_result.returncode != 0:
                raise Exception(f"git fetch başarısız: {fetch_result.stderr}")

            # Default branch'i tespit et
            logger.info("Default branch tespit ediliyor...")
            remote_branch_check = subprocess.run(
                ["git", "-C", target_dir, "ls-remote", "--symref", "origin", "HEAD"],
                capture_output=True,
                text=True,
                timeout=10
            )

            default_branch = "main"
            if remote_branch_check.returncode == 0:
                for line in remote_branch_check.stdout.splitlines():
                    if "ref: refs/heads/" in line:
                        default_branch = line.split("refs/heads/")[1].split()[0]
                        logger.info(f"Uzak repoda tespit edilen default branch: {default_branch}")
                        break

            if default_branch == "main":
                branches_check = subprocess.run(
                    ["git", "-C", target_dir, "branch", "-r"],
                    capture_output=True,
                    text=True,
                    timeout=10
                )
                if "origin/master" in branches_check.stdout and "origin/main" not in branches_check.stdout:
                    default_branch = "master"
                    logger.info("origin/main yok, master kullanılacak")

            # Mevcut branch'i kontrol et
            branch_check = subprocess.run(
                ["git", "-C", target_dir, "rev-parse", "--abbrev-ref", "HEAD"],
                capture_output=True,
                text=True,
                timeout=10
            )

            current_branch = branch_check.stdout.strip()
            logger.info(f"Mevcut branch: {current_branch}")

            # Default branch'e geç (yoksa oluştur)
            if current_branch != default_branch:
                logger.info(f"{default_branch} branch'e geçiliyor...")
                checkout_result = subprocess.run(
                    ["git", "-C", target_dir, "checkout", "-B", default_branch, f"origin/{default_branch}"],
                    capture_output=True,
                    text=True,
                    timeout=30
                )
                if checkout_result.returncode != 0:
                    logger.warning(f"Checkout uyarısı: {checkout_result.stderr}")

            # Reset --hard ile güncelle
            logger.info(f"Güncelleme yapılıyor (reset --hard origin/{default_branch})...")
            result = subprocess.run(
                ["git", "-C", target_dir, "reset", "--hard", f"origin/{default_branch}"],
                capture_output=True,
                text=True,
                timeout=60
            )

        else:
            # Git deposu yok - mevcut dosyalarla git init yap
            logger.info("Git deposu bulunamadı, mevcut klasörde git başlatılıyor...")

            # 1. git init
            init_result = subprocess.run(
                ["git", "-C", target_dir, "init"],
                capture_output=True,
                text=True,
                timeout=30
            )

            if init_result.returncode != 0:
                raise Exception(f"git init başarısız: {init_result.stderr}")

            logger.info("Git deposu başlatıldı")

            # 2. remote ekle
            remote_result = subprocess.run(
                ["git", "-C", target_dir, "remote", "add", "origin", repo_url],
                capture_output=True,
                text=True,
                timeout=30
            )

            if remote_result.returncode != 0 and "already exists" not in remote_result.stderr:
                raise Exception(f"Remote ekleme başarısız: {remote_result.stderr}")

            logger.info("Remote origin eklendi")

            # 3. fetch
            logger.info("GitHub'dan dosyalar çekiliyor (fetch)...")
            fetch_result = subprocess.run(
                ["git", "-C", target_dir, "fetch", "origin"],
                capture_output=True,
                text=True,
                timeout=60
            )

            if fetch_result.returncode != 0:
                raise Exception(f"git fetch başarısız: {fetch_result.stderr}")

            logger.info("Fetch tamamlandı")

            # 4. Default branch'i tespit et
            remote_branch_check = subprocess.run(
                ["git", "-C", target_dir, "ls-remote", "--symref", "origin", "HEAD"],
                capture_output=True,
                text=True,
                timeout=10
            )

            default_branch = "main"
            if remote_branch_check.returncode == 0:
                for line in remote_branch_check.stdout.splitlines():
                    if "ref: refs/heads/" in line:
                        default_branch = line.split("refs/heads/")[1].split()[0]
                        logger.info(f"Default branch: {default_branch}")
                        break

            # Eğer tespit edilemezse branch'lere bak
            if default_branch == "main":
                branches_check = subprocess.run(
                    ["git", "-C", target_dir, "branch", "-r"],
                    capture_output=True,
                    text=True,
                    timeout=10
                )
                if "origin/master" in branches_check.stdout and "origin/main" not in branches_check.stdout:
                    default_branch = "master"
                    logger.info("origin/main yok, master kullanılacak")

            # 5. Mevcut dosyaları stash'le (varsa)
            logger.info("Mevcut dosyalar korunuyor...")
            stash_result = subprocess.run(
                ["git", "-C", target_dir, "add", "."],
                capture_output=True,
                text=True,
                timeout=30
            )

            # 6. checkout default branch
            logger.info(f"{default_branch} branch'e geçiliyor...")
            checkout_result = subprocess.run(
                ["git", "-C", "checkout", "-B", default_branch, f"origin/{default_branch}"],
                capture_output=True,
                text=True,
                timeout=30
            )

            if checkout_result.returncode != 0:
                # Eğer checkout başarısız olursa, reset --hard dene
                logger.warning(f"Checkout başarısız, reset deneniyor: {checkout_result.stderr}")
                checkout_result = subprocess.run(
                    ["git", "-C", target_dir, "reset", "--hard", f"origin/{default_branch}"],
                    capture_output=True,
                    text=True,
                    timeout=30
                )

            result = checkout_result
            logger.info("Git deposu kuruldu ve güncellendi")

        output = result.stdout + result.stderr
        logger.info(f"Git işlemi tamamlandı: {output}")

        if result.returncode == 0:
            return {
                "success": True,
                "message": "Güncelleme başarıyla tamamlandı",
                "output": output
            }
        else:
            logger.error(f"Git hatası: {output}")
            return {
                "success": False,
                "message": "Güncelleme başarısız oldu",
                "error": output
            }

    except subprocess.TimeoutExpired:
        logger.error("Git işlemi zaman aşımına uğradı")
        return {
            "success": False,
            "message": "İşlem zaman aşımına uğradı",
            "error": "Git komutu belirlenen süre içinde tamamlanamadı"
        }
    except Exception as e:
        logger.error(f"Güncelleme hatası: {e}")
        return {
            "success": False,
            "message": "Beklenmeyen bir hata oluştu",
            "error": str(e)
        }

# ================================ Temizlik ================================
def cleanup_resources():
    logger.info("Kapanış — kaynak temizleniyor.")
    try:
        if camera is not None and camera.isOpened(): camera.release()
    except Exception: pass
    try:
        if GPIO is not None: GPIO.cleanup()
    except Exception: pass
    # gpiod kaynakları PWM thread'inde kapanıyor.

# ================================ Çalıştır ================================
if __name__ == "__main__":
    # Başlangıçta sinyal dosyasını temizle
    try:
        if os.path.exists(CAMERA_SIGNAL_FILE):
            try:
                os.remove(CAMERA_SIGNAL_FILE)
                logger.info("Başlangıçta QR modu sinyal dosyası temizlendi")
            except PermissionError:
                # İzin hatası varsa sudo ile sil
                try:
                    import subprocess
                    subprocess.run(['sudo', 'rm', '-f', CAMERA_SIGNAL_FILE], check=False)
                    logger.info("QR modu sinyal dosyası sudo ile temizlendi")
                except Exception as se:
                    logger.warning(f"Sinyal dosyası sudo ile silinemedi: {se}")
        else:
            logger.info("Başlangıçta QR modu sinyal dosyası yok - Normal başlatılıyor")
    except Exception as e:
        logger.warning(f"Sinyal dosyası kontrolü hatası: {e}")

    with app.app_context():
        ensure_default_user()
    try:
        init_camera()
        socketio.start_background_task(arkaplan_isi)

        if USE_GPIOD and BATT_PWM_LINE:
            threading.Thread(target=gpio_batt_reader_pwm_gpiod, daemon=True).start()
        else:
            logger.error("gpiod yok veya BATT_PWM_LINE tanımsız — PWM okuyucu başlatılamadı.")

        # Shutdown watcher'ı başlat
        try:
            if USE_GPIOD and SHUTDOWN_GPIO_LINE:
                threading.Thread(target=gpio_shutdown_watcher, daemon=True).start()
            else:
                logger.warning("Shutdown watcher devre dışı (gpiod yok veya SHUTDOWN_GPIO_LINE tanımsız)")
        except Exception as _e:
            logger.error(f"gpio_shutdown_watcher başlatılamadı: {_e}")

        # Kayıt modülü arkaplan servislerini başlat
        try:
            if 'recordsVideo' in globals():
                recordsVideo.start_background()
        except Exception as _e:
            logger.error(f"recordsVideo.start_background hatası: {_e}")

        # QR modu sinyal monitörünü başlat
        try:
            threading.Thread(target=qr_signal_monitor_loop, daemon=True).start()
        except Exception as _e:
            logger.error(f"qr_signal_monitor_loop başlatılamadı: {_e}")

        # mDNS broadcast'i başlat
        if mdns_broadcast:
            try:
                port = int(os.environ.get("PORT", "7447"))
                hostname = os.environ.get("MDNS_HOSTNAME", None)  # None ise sistem hostname kullanılır
                service_name = os.environ.get("MDNS_SERVICE_NAME", "Clary Orange Pi")
                mdns_broadcast.start_mdns_broadcast(hostname=hostname, port=port, service_name=service_name)
            except Exception as _e:
                logger.error(f"mDNS broadcast başlatılamadı: {_e}")

        # Sistem hazır - Pin 258 LED'ini yak
        if GPIO is not None and ready_led_pin is not None:
            try:
                GPIO.output(ready_led_pin, GPIO.HIGH)
                logger.info(f"Sistem hazır! Pin {ready_led_pin} HIGH yapıldı (LED yanıyor)")
            except Exception as e:
                logger.error(f"Pin {ready_led_pin} HIGH yapılamadı: {e}")

        port = int(os.environ.get("PORT", "7447"))
        logger.info(f"Uygulama: http://0.0.0.0:{port}")
        socketio.run(app, host="0.0.0.0", port=port, allow_unsafe_werkzeug=True)
    finally:
        _batt_stop_evt.set()
        _shutdown_stop_evt.set()
        # Kayıt modülü servislerini durdur
        try:
            if 'recordsVideo' in globals():
                recordsVideo.stop_background()
        except Exception as _e:
            logger.error(f"recordsVideo.stop_background hatası: {_e}")

        # mDNS broadcast'i durdur
        if mdns_broadcast:
            try:
                mdns_broadcast.stop_mdns_broadcast()
            except Exception as _e:
                logger.error(f"mDNS broadcast durdurulamadı: {_e}")

        cleanup_resources()

        # Çıkışta sinyal dosyasını temizle
        try:
            if os.path.exists(CAMERA_SIGNAL_FILE):
                os.remove(CAMERA_SIGNAL_FILE)
        except Exception:
            pass

