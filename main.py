#!/usr/bin/python3
# -*- coding: utf-8 -*-
import eventlet; eventlet.monkey_patch()

import os, sys, time, re, subprocess, logging, signal, threading
from datetime import datetime
from collections import deque
from typing import Tuple

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
fallback_dir = "/tmp/clary_logs"
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

_try_import_gpio()

# =========================== Kamera / Global durum ========================
camera = None
ever_connected = False
batt_value = 0
last_10_readings = deque(maxlen=50)
active_connections = set()

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

# =========================== Sinyal / Hızlı çıkış =========================
def _graceful_exit(signum, frame):
    print(f"[SYS] Stop signal {signum}, exiting fast.")
    sys.exit(0)
signal.signal(signal.SIGTERM, _graceful_exit)
signal.signal(signal.SIGINT,  _graceful_exit)

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

# ============================== AP (hostapd) Ayarları =====================
HOSTAPD_PATHS = [
    "/etc/hostapd/hostapd.conf",
    "/etc/hostapd.conf",
]

CHANNELS_24 = list(range(1, 14))  # 1-13 (TR)
CHANNELS_5  = [36, 40, 44, 48, 149, 153, 157, 161]  # DFS dışı yaygın kanallar


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


def restart_hostapd() -> Tuple[bool,str]:
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

# =========================== Sinyal / Hızlı çıkış =========================
def _graceful_exit(signum, frame):
    print(f"[SYS] Stop signal {signum}, exiting fast.")
    sys.exit(0)
signal.signal(signal.SIGTERM, _graceful_exit)
signal.signal(signal.SIGINT,  _graceful_exit)

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
    p2 = "/etc/wpa_sendant/wpa_supplicant.conf"
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
    """0-100 clamp + basit hareketli ortalama."""
    global batt_value
    v = max(0, min(100, int(new_val)))
    last_10_readings.append(v)
    batt_value = int(round(sum(last_10_readings) / len(last_10_readings)))

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
def init_camera():
    global camera
    if camera is not None and camera.isOpened():
        camera.release()
    camera = None
    for idx in range(3):
        try:
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
    global camera, ever_connected
    connection_retry_timer = 0
    error_count, max_errors = 0, 3
    last_ok = None; last_ts = 0

    while True:
        try:
            if camera is None or not camera.isOpened():
                now = time.time()
                if now - connection_retry_timer >= 1:
                    connection_retry_timer = now
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

            ever_connected = True; error_count = 0
            last_ok = frame.copy(); last_ts = now
            _, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
            yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + buf.tobytes() + b"\r\n"

        except Exception as e:
            error_count += 1; logger.error(f"generate_frames hata {error_count}/{max_errors}: {e}")
            if camera is not None: camera.release()
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
    sid = request.sid; active_connections.add(sid)
    logger.info(f"SocketIO bağlandı - SID: {sid}")

@socketio.on("disconnect", namespace="/adc")
def sock_disconnect():
    sid = request.sid
    if sid in active_connections: active_connections.remove(sid)
    logger.info(f"SocketIO ayrıldı - SID: {sid}")

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
        if u and verify_password(u.password, pwd):
            session["uid"]  = u.id
            session["user"] = u.username
            flash("Giriş başarılı.", "success")
            return redirect(url_for("control_wifi"))
        flash("Hatalı kullanıcı adı/şifre.", "danger")

    return render_template("login_csrf.html")

@app.route("/logout")
def logout():
    session.clear(); flash("Çıkış yapıldı.", "info")
    return redirect(url_for("login"))

# --- Wi-Fi ---
@app.route("/control_wifi")
@login_required
def control_wifi():
    band, ch = read_ap_band_channel()
    return render_template(
        "wifi_settings_simple.html",
        band=band,
        channel=ch,
        channels_24=CHANNELS_24,
        channels_5=CHANNELS_5,
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
    # Restart dene
    rok, rmsg = restart_hostapd()
    flash((msg + (" — " + rmsg if rmsg else "")), "success" if rok else "warning")
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
        cleanup_resources()
