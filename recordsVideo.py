# -*- coding: utf-8 -*-
"""
GPIO 260 yükselince video kaydını başlatır, düşünce durdurur.
Kayıtlar clary/records/oturumN klasörlerine AVI (MJPG) formatında kaydedilir.
Ayrıca dosyaları listeleme, indirme, isim değiştirme ve silme için
Flask Blueprint sağlar.

Main uygulamasıyla entegrasyon için:
- main.generate_frames içinde her kare için push_frame(frame) çağrılmalı.
- uygulama başlarken start_background() çağrılmalı.
- blueprint main üzerinde register_blueprint ile bağlanmalı.
"""
from __future__ import annotations
import os
import cv2
import time
import threading
import logging
from datetime import datetime
from typing import Optional, List, Dict
from queue import Queue, Empty
import http.client
from collections import deque
import shutil

from flask import Blueprint, render_template, request, redirect, url_for, flash, send_from_directory, session

# Merkezi loglama sistemi
try:
    import system_logger as syslog
except Exception:
    syslog = None

# ============================ Yapılandırma ==============================
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
RECORDS_DIR = os.path.join(BASE_DIR, "clary", "records")
os.makedirs(RECORDS_DIR, exist_ok=True)

# Oturum isimlendirme
SESSION_PREFIX = os.environ.get("SESSION_PREFIX", "oturum")
SESSION_NAME: Optional[str] = None
SESSION_DIR: Optional[str]  = None

# Local feed ayarı
_FEED_HOST = os.environ.get("FEED_HOST", "127.0.0.1")
_FEED_PORT = int(os.environ.get("PORT", os.environ.get("FEED_PORT", "7447")))
_FEED_PATH = os.environ.get("FEED_PATH", "/video_feed")

LOG = logging.getLogger(__name__)
if not LOG.handlers:
    LOG.setLevel(logging.INFO)
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    LOG.addHandler(ch)

# ============================ Frame Akışı ==============================
_last_frame_lock = threading.Lock()
_last_frame = None  # type: Optional[any]  # numpy.ndarray
_last_frame_ts = 0.0

RECORD_FPS = float(os.environ.get("RECORD_FPS", "18"))
RECORD_FPS_MIN = float(os.environ.get("RECORD_FPS_MIN", "8"))
RECORD_FPS_MAX = float(os.environ.get("RECORD_FPS_MAX", "30"))
FILL_MISSING_FRAMES = (os.environ.get("FILL_MISSING_FRAMES", "1").strip() not in ("0","false","False"))
FILL_MAX_GAP_SEC = float(os.environ.get("FILL_MAX_GAP_SEC", "10"))  # boşluk doldurma üst limiti

FRAME_SIZE = None  # son gelen gerçek frame boyutu (w,h)
WRITER_SIZE = None  # aktif writer hedef boyutu (w,h)

_recording_flag = threading.Event()
_manual_control_mode = threading.Lock()  # Manuel kontrol aktif mi?
_manual_control_active = False  # True ise GPIO watcher pasif
_stop_all = threading.Event()
_writer_lock = threading.Lock()
_writer = None  # type: Optional[cv2.VideoWriter]
_current_file = None  # type: Optional[str]
_writer_fps = RECORD_FPS  # aktif writer fps

# Yeni: kayıt için zaman damgalı kare kuyruğu (drop-on-full)
# Eleman: (frame: np.ndarray, ts: float)
_frame_q: Queue = Queue(maxsize=int(os.environ.get("RECORD_QUEUE_MAX", "300")))

# FPS ölçümü için kısa zaman geçmişi
_ts_hist = deque(maxlen=120)

# Video süresi kontrolü için (2 saniyeden kısa videoları silmek için)
_record_start_time = None
MIN_VIDEO_DURATION = float(os.environ.get("MIN_VIDEO_DURATION", "2.0"))  # Minimum video süresi (saniye)


# ----------------------- Oturum klasörü yönetimi ------------------------
import re

def _ensure_session_dir():
    """Her uygulama başlangıcında bir sonraki oturum klasörünü oluştur."""
    global SESSION_NAME, SESSION_DIR
    try:
        names = [n for n in os.listdir(RECORDS_DIR) if os.path.isdir(os.path.join(RECORDS_DIR, n))]
    except Exception:
        names = []
    pat = re.compile(rf"^{re.escape(SESSION_PREFIX)}(\d+)$")
    idxs = []
    for n in names:
        m = pat.match(n)
        if m:
            try:
                idxs.append(int(m.group(1)))
            except Exception:
                pass
    next_idx = (max(idxs) + 1) if idxs else 1
    SESSION_NAME = f"{SESSION_PREFIX}{next_idx}"
    SESSION_DIR = os.path.join(RECORDS_DIR, SESSION_NAME)
    try:
        os.makedirs(SESSION_DIR, exist_ok=True)
        LOG.info(f"Oturum klasörü hazır: {SESSION_NAME}")
    except Exception as e:
        LOG.error(f"Oturum klasörü oluşturulamadı: {e}")
        # geri dönüş: kök klasör
        SESSION_DIR = RECORDS_DIR


# Modül yüklendiğinde session'ı initialize et
# _ensure_session_dir()  # Bu satırı kaldırıyoruz - sadece start_background'da çağrılacak


def push_frame(frame):
    """Ana akıştan son kareyi paylaş. Frame kopyası alınır.
    Kayıt açıkken kare kuyruğuna (taşma olursa drop) zaman damgasıyla eklenir.
    """
    global _last_frame, _last_frame_ts, FRAME_SIZE
    try:
        if frame is None:
            return
        ts = time.time()
        # Son kareyi güncelle
        with _last_frame_lock:
            _last_frame = frame.copy()
            _last_frame_ts = ts
            h, w = _last_frame.shape[:2]
            FRAME_SIZE = (w, h)
        # fps ölçüm geçmişi
        _ts_hist.append(ts)
        # Kayıt açıkken kuyruğa ekle (non-blocking)
        if _recording_flag.is_set():
            try:
                _frame_q.put_nowait((_last_frame, ts))
            except Exception:
                # kuyruk dolu: kare düşür
                pass
    except Exception as e:
        LOG.error(f"push_frame hatası: {e}")


def _estimate_fps() -> float:
    """Son zaman damgalarından yaklaşık FPS tahmin et ve sınırla."""
    if len(_ts_hist) >= 10:
        dts = [b - a for a, b in zip(list(_ts_hist)[:-1], list(_ts_hist)[1:]) if b > a]
        if dts:
            avg_dt = sum(dts) / len(dts)
            if avg_dt > 0:
                fps = 1.0 / avg_dt
                return max(RECORD_FPS_MIN, min(RECORD_FPS_MAX, fps))
    return RECORD_FPS


def _open_writer(size: Optional[tuple]=None, fps: Optional[float]=None) -> Optional[cv2.VideoWriter]:
    """Yeni bir dosya aç ve writer döndür."""
    global _current_file, WRITER_SIZE, _writer_fps, _record_start_time
    if size is None:
        size = FRAME_SIZE or (640, 480)
    # FPS seçimi
    fps_use = float(fps if fps is not None else _estimate_fps())
    # Çok uç değerleri sınırlama
    fps_use = max(RECORD_FPS_MIN, min(RECORD_FPS_MAX, fps_use))

    timestr = datetime.now().strftime("%Y%m%d_%H%M%S")
    fname = f"rec_{timestr}.avi"
    # Oturum klasörü varsa oraya yaz
    target_dir = SESSION_DIR if SESSION_DIR else RECORDS_DIR
    try:
        os.makedirs(target_dir, exist_ok=True)
    except Exception:
        target_dir = RECORDS_DIR
    path = os.path.join(target_dir, fname)
    fourcc = cv2.VideoWriter_fourcc(*"MJPG")
    writer = cv2.VideoWriter(path, fourcc, fps_use, size)
    if not writer or not writer.isOpened():
        LOG.error("VideoWriter açılamadı; farklı codec/uzantı deneyin.")
        # Hata logu
        if syslog:
            try:
                syslog.log_video_recording_start(SESSION_NAME or "unknown", path, size, fps_use)
                syslog.log_system_event("VIDEO_WRITER_ERROR",
                                      "VideoWriter açılamadı", "ERROR",
                                      file=path, fps=fps_use, resolution=f"{size[0]}x{size[1]}")
            except Exception:
                pass
        return None
    WRITER_SIZE = size
    _current_file = fname
    _writer_fps = fps_use
    _record_start_time = time.time()  # Kayıt başlangıç zamanını kaydet
    LOG.info(f"Kayıt başladı: {fname} @ {_writer_fps:.2f}fps {size} -> {target_dir}")

    # Kayıt başlama logu
    if syslog:
        try:
            syslog.log_video_recording_start(SESSION_NAME or "unknown", path, size, fps_use)
        except Exception:
            pass

    # LED'i aç
    _set_led(True)

    return writer


def _close_writer():
    global _writer, _current_file, _record_start_time
    try:
        if _writer is not None:
            _writer.release()
            LOG.info(f"Kayıt durdu: {_current_file}")

            # Video süresini kontrol et
            if _record_start_time is not None and _current_file is not None:
                duration = time.time() - _record_start_time

                # 2 saniyeden kısa ise dosyayı sil
                if duration < MIN_VIDEO_DURATION:
                    target_dir = SESSION_DIR if SESSION_DIR else RECORDS_DIR
                    file_path = os.path.join(target_dir, _current_file)

                    try:
                        if os.path.exists(file_path):
                            os.remove(file_path)
                            LOG.info(f"Kısa video silindi: {_current_file} (süre: {duration:.2f}s < {MIN_VIDEO_DURATION}s)")

                            # Silme logu
                            if syslog:
                                try:
                                    syslog.log_system_event("SHORT_VIDEO_DELETED",
                                                          f"Kısa video otomatik silindi: {_current_file}",
                                                          "INFO",
                                                          file=_current_file,
                                                          duration=duration,
                                                          min_duration=MIN_VIDEO_DURATION)
                                except Exception:
                                    pass
                        else:
                            LOG.warning(f"Silinecek dosya bulunamadı: {file_path}")
                    except Exception as e:
                        LOG.error(f"Kısa video silinirken hata: {e}")
                else:
                    LOG.info(f"Video kaydedildi: {_current_file} (süre: {duration:.2f}s)")

                # Kayıt süresini sıfırla
                _record_start_time = None

            # LED'i kapat
            _set_led(False)
    except Exception as e:
        LOG.error(f"Writer kapatma hatası: {e}")
    finally:
        _writer = None
        _current_file = None
        _record_start_time = None


def _get_latest_frame():
    with _last_frame_lock:
        return (None if _last_frame is None else _last_frame.copy())


def _drain_queue():
    """Kuyruğu hızlıca boşalt (kayıt kapanırken)."""
    try:
        while True:
            _frame_q.get_nowait()
    except Exception:
        pass


def _writer_loop():
    """Zaman damgalarına göre kareleri yaz. Eksik aralıklarda önceki kareyi tekrar et."""
    global _writer
    last_emit_ts = None  # son yazılan kare zaman damgası
    prev_frame = None    # tekrar için elde tutulan kare
    period = 1.0 / max(1e-3, RECORD_FPS)  # writer açılana kadar varsayılan

    while not _stop_all.is_set():
        try:
            if not _recording_flag.is_set():
                # kayıt değilken writer kapalı tut ve kuyruğu boşalt
                if _writer is not None:
                    with _writer_lock:
                        _close_writer()
                _drain_queue()
                last_emit_ts = None
                prev_frame = None
                time.sleep(0.02)
                continue

            # Kayıt açık: puller'ı çalıştır
            _ensure_puller_running()

            # writer yoksa aç (mevcut frame boyutuna göre ve ölçülen fps ile)
            if _writer is None:
                frame_probe = _get_latest_frame()
                if frame_probe is not None:
                    ph_h, ph_w = frame_probe.shape[:2]
                    size = (ph_w, ph_h)
                else:
                    size = FRAME_SIZE or (640, 480)
                with _writer_lock:
                    if _writer is None:
                        est_fps = _estimate_fps()
                        _writer = _open_writer(size, fps=est_fps)
                        if _writer is None:
                            time.sleep(0.1)
                            continue
                        period = 1.0 / max(1e-3, (est_fps or RECORD_FPS))

            # Kuyruktan kare çek ve yaz
            try:
                item = _frame_q.get(timeout=0.5)
            except Empty:
                # Uzun süre kare gelmiyorsa döngüye devam (writer açık kalsın)
                continue

            if item is None:
                continue

            # paket çöz
            try:
                frame, ts = item
            except Exception:
                # Eski format (yalnızca frame) destekleniyorsa
                frame = item
                ts = time.time()

            # Boyut uyumu
            h, w = frame.shape[:2]
            target_w, target_h = (WRITER_SIZE or (w, h))
            if (w, h) != (target_w, target_h):
                frame = cv2.resize(frame, (target_w, target_h))

            # Zaman çizelgesini koru: arada kaçırılan periyotları önceki kareyle doldur
            if FILL_MISSING_FRAMES and last_emit_ts is not None and prev_frame is not None:
                # Güvenlik limiti: çok büyük boşlukları aşırı büyütmemek için sınırlı doldurma
                max_fill = max(0.0, FILL_MAX_GAP_SEC)
                while (ts - last_emit_ts) > (period * 1.1) and (ts - last_emit_ts) < (max_fill if max_fill > 0 else float("inf")):
                    try:
                        _writer.write(prev_frame)
                    except Exception as e:
                        LOG.error(f"Tekrar kare yazma hatası: {e}")
                        break
                    last_emit_ts += period

            # Mevcut kareyi yaz
            try:
                _writer.write(frame)
            except Exception as e:
                LOG.error(f"Frame yazma hatası: {e}")
            prev_frame = frame
            last_emit_ts = ts

        except Exception as e:
            LOG.error(f"writer_loop hata: {e}")
            time.sleep(0.1)


# ============================ GPIO İzleme (gpiod) ========================
USE_GPIOD = False
try:
    import gpiod
    USE_GPIOD = True
except Exception:
    USE_GPIOD = False

GPIO_RECORD_LINE = os.environ.get("RECORD_GPIO_LINE", "/dev/gpiochip1:260")

# LED kontrolü için PI2 pini (kayıt göstergesi)
GPIO_LED_CHIP = "/dev/gpiochip1"
GPIO_LED_OFFSET = 258  # PI2 pini (GPIO 258)
_led_line = None
_led_chip = None

def _setup_led_gpio():
    """PI2 pinini çıkış olarak ayarla (kayıt LED'i için)"""
    global _led_line, _led_chip
    if not USE_GPIOD:
        LOG.warning("gpiod yok - LED kontrolü devre dışı")
        return False
    try:
        _led_chip = gpiod.Chip(GPIO_LED_CHIP)
        _led_line = _led_chip.get_line(GPIO_LED_OFFSET)
        _led_line.request(consumer="clary-rec-led", type=gpiod.LINE_REQ_DIR_OUT, default_vals=[0])
        LOG.info(f"LED GPIO (PI2) hazır: {GPIO_LED_CHIP}:{GPIO_LED_OFFSET}")
        return True
    except Exception as e:
        LOG.error(f"LED GPIO açılamadı: {e}")
        return False

def _set_led(state: bool):
    """LED'i aç/kapa (PI2 pini HIGH/LOW)"""
    global _led_line
    if _led_line is None:
        return
    try:
        _led_line.set_value(1 if state else 0)
    except Exception as e:
        LOG.error(f"LED set hatası: {e}")

def _cleanup_led_gpio():
    """LED GPIO kaynaklarını serbest bırak"""
    global _led_line, _led_chip
    try:
        if _led_line is not None:
            _led_line.release()
            _led_line = None
    except Exception:
        pass
    try:
        if _led_chip is not None:
            _led_chip.close()
            _led_chip = None
    except Exception:
        pass


def _ev_ns(ev):
    ts = getattr(ev, "timestamp", None)
    if ts is not None:
        try:
            return int(ts)
        except Exception:
            pass
    sec = getattr(ev, "sec", None)
    nsec = getattr(ev, "nsec", None)
    if sec is not None and nsec is not None:
        return int(sec) * 1_000_000_000 + int(nsec)
    return int(time.monotonic() * 1_000_000_000)


def _event_wait(line, timeout_sec: float):
    try:
        return bool(line.event_wait(timeout=timeout_sec))
    except TypeError:
        pass
    try:
        return bool(line.event_wait(timeout_sec))
    except TypeError:
        pass
    try:
        return bool(line.event_wait(int(timeout_sec * 1000)))
    except TypeError:
        pass
    try:
        return bool(line.event_wait(0))
    except Exception:
        return False


def _event_available(line):
    return _event_wait(line, 0.0)


def _record_gpio_watcher():
    global _manual_control_active
    if not USE_GPIOD:
        LOG.error("gpiod yok — kayıt GPIO izleyici çalışmayacak.")
        return
    if ":" not in GPIO_RECORD_LINE:
        LOG.error("RECORD_GPIO_LINE formatı '/dev/gpiochipX:OFFSET' olmalı.")
        return
    chip_name, off_s = GPIO_RECORD_LINE.split(":")
    off = int(off_s)
    try:
        chip = gpiod.Chip(chip_name)
        line = chip.get_line(off)
        line.request(consumer="clary-rec", type=gpiod.LINE_REQ_EV_BOTH_EDGES)
        # Başlangıç seviyesi
        try:
            level = line.get_value()
        except Exception:
            level = 0

        # Manuel kontrol aktif değilse GPIO durumunu uygula
        with _manual_control_mode:
            if not _manual_control_active:
                if level == 1:
                    _recording_flag.set()
                    LOG.info("GPIO başlangıç HIGH — kayıt açık.")
                else:
                    _recording_flag.clear()
                    LOG.info("GPIO başlangıç LOW — kayıt kapalı.")
            else:
                LOG.info("GPIO başlangıç görmezden gelindi (manuel kontrol aktif).")
    except Exception as e:
        LOG.error(f"Kayıt GPIO açılamadı: {e}")
        return

    try:
        last_hb = time.time()
        while not _stop_all.is_set():
            # Manuel kontrol aktifse GPIO olaylarını görmezden gel
            with _manual_control_mode:
                if _manual_control_active:
                    time.sleep(0.5)
                    continue

            if _event_available(line):
                drained = 0
                while _event_available(line) and drained < 1024:
                    ev = line.event_read()
                    drained += 1

                    # Tekrar manuel kontrol kontrolü
                    with _manual_control_mode:
                        if _manual_control_active:
                            continue

                    if ev.type == gpiod.LineEvent.RISING_EDGE:
                        _recording_flag.set()
                        LOG.info("GPIO 260 RISING — kayıt BAŞLA")
                    else:
                        _recording_flag.clear()
                        LOG.info("GPIO 260 FALLING — kayıt DUR")
            else:
                time.sleep(0.01)
            # heartbeat
            now = time.time()
            if now - last_hb > 5:
                state = "ON" if _recording_flag.is_set() else "OFF"
                with _manual_control_mode:
                    mode = "MANUEL" if _manual_control_active else "GPIO"
                LOG.info(f"rec hb: state={state}, mode={mode}")
                last_hb = now
    except Exception as e:
        LOG.error(f"Kayıt GPIO döngü hatası: {e}")
    finally:
        try:
            line.release()
        except Exception:
            pass
        try:
            chip.close()
        except Exception:
            pass


# ============================ Web Blueprint ==============================
records_bp = Blueprint("records", __name__, url_prefix="/records")

# Context processor: template'lere session erişimi ekle
@records_bp.context_processor
def inject_session():
    """Template'lerde session objesine erişim sağla."""
    return dict(session=session)

def _safe_name(name: str) -> str:
    name = (name or "").strip()
    # Basit güvenlik: path ayracı yasak, sadece alfasayısal, tire, alt çizgi, nokta
    import re
    if not re.fullmatch(r"[\w\-\. ]{1,128}", name):
        raise ValueError("Geçersiz dosya adı")
    return name


def _safe_session(name: str) -> str:
    """Oturum klasörünün varlığını ve güvenliğini doğrula."""
    name = (name or "").strip()

    # Güvenlik kontrolü: path traversal saldırılarını önle
    # Sadece alfasayısal, tire, alt çizgi ve boşluk karakterlerine izin ver
    if not re.fullmatch(r"[\w\-\. ]{1,128}", name):
        raise ValueError("Geçersiz oturum adı")

    # Path traversal kontrolü
    if ".." in name or "/" in name or "\\" in name:
        raise ValueError("Geçersiz oturum")

    # Klasörün varlığını kontrol et
    path = os.path.join(RECORDS_DIR, name)
    if not os.path.isdir(path):
        raise FileNotFoundError("Oturum bulunamadı")

    return name


def _list_sessions() -> List[Dict]:
    items: List[Dict] = []
    try:
        for fn in os.listdir(RECORDS_DIR):
            p = os.path.join(RECORDS_DIR, fn)
            if not os.path.isdir(p):
                continue
            try:
                files = [f for f in os.listdir(p) if os.path.isfile(os.path.join(p, f))]
            except Exception:
                files = []
            size = 0
            mtime = 0
            for f in files:
                fp = os.path.join(p, f)
                try:
                    st = os.stat(fp)
                except Exception:
                    continue
                size += st.st_size
                mtime = max(mtime, st.st_mtime)
            if mtime == 0:
                try:
                    mtime = os.stat(p).st_mtime
                except Exception:
                    mtime = time.time()
            items.append({
                "name": fn,
                "count": len(files),
                "size": size,
                "mtime": mtime,
            })
    except Exception:
        pass
    # Yeni/son oturumlar üstte
    items.sort(key=lambda x: x["mtime"], reverse=True)
    return items


def _list_files(session_name: str) -> List[Dict]:
    items = []
    sess = _safe_session(session_name)
    sess_dir = os.path.join(RECORDS_DIR, sess)
    for fn in os.listdir(sess_dir):
        path = os.path.join(sess_dir, fn)
        if not os.path.isfile(path):
            continue
        stat = os.stat(path)
        items.append({
            "name": fn,
            "size": stat.st_size,
            "mtime": stat.st_mtime,
        })
    items.sort(key=lambda x: x["mtime"], reverse=True)
    return items


# Basit login gereksinimi

def _login_required(fn):
    from functools import wraps
    @wraps(fn)
    def _wrap(*a, **k):
        if not session.get("uid"):
            # login sayfasına gönder; kayıt sayfasından sonra geri dönmek için next kullanılabilir
            return redirect(url_for("login"))
        return fn(*a, **k)
    return _wrap


@records_bp.route("/", methods=["GET"])  # oturumları listele
@records_bp.route("", methods=["GET"])   # /records (trailing slash olmadan)
@_login_required
def list_records():
    sessions = _list_sessions()
    return render_template("records.html", sessions=sessions, active_session=SESSION_NAME)


@records_bp.route("/<session>", methods=["GET"])  # oturum içi dosyalar
@_login_required
def list_session(session):
    try:
        sess = _safe_session(session)
    except Exception:
        flash("Geçersiz oturum.", "danger")
        return redirect(url_for("records.list_records"))
    files = _list_files(sess)
    return render_template("records.html", files=files, current_session=sess, active_session=SESSION_NAME)


@records_bp.route("/<session>/rename", methods=["POST"])  # isim değiştirme
@_login_required
def rename_record(session):
    try:
        sess = _safe_session(session)
    except Exception:
        flash("Geçersiz oturum.", "danger")
        return redirect(url_for("records.list_records"))
    old = request.form.get("old_name", "")
    new = request.form.get("new_name", "")
    username = globals().get('session', {}).get("user", "Unknown")

    try:
        old = _safe_name(old)
        new = _safe_name(new)
        # uzantı koru (eski dosyanın uzantısı)
        _, ext = os.path.splitext(old)
        if not new.endswith(ext):
            new = new + ext
        src = os.path.join(RECORDS_DIR, sess, old)
        dst = os.path.join(RECORDS_DIR, sess, new)
        if not os.path.exists(src):
            flash("Dosya bulunamadı.", "warning")
            if syslog:
                try:
                    syslog.log_video_file_operation("RENAME", src, False, username, "Dosya bulunamadı")
                except Exception:
                    pass
        elif os.path.exists(dst):
            flash("Hedef isim zaten var.", "warning")
            if syslog:
                try:
                    syslog.log_video_file_operation("RENAME", src, False, username, "Hedef isim zaten var")
                except Exception:
                    pass
        else:
            os.rename(src, dst)
            flash("İsim değiştirildi.", "success")
            if syslog:
                try:
                    syslog.log_video_file_operation("RENAME", src, True, username, new_name=new)
                except Exception:
                    pass
    except Exception as e:
        flash(f"Hata: {e}", "danger")
        if syslog:
            try:
                syslog.log_video_file_operation("RENAME", old, False, username, str(e))
            except Exception:
                pass
    return redirect(url_for("records.list_session", session=sess))


@records_bp.route("/<session>/delete", methods=["POST"])  # silme
@_login_required
def delete_record(session):
    try:
        sess = _safe_session(session)
    except Exception:
        flash("Geçersiz oturum.", "danger")
        return redirect(url_for("records.list_records"))
    name = request.form.get("name", "")
    username = globals().get('session', {}).get("user", "Unknown")

    try:
        name = _safe_name(name)
        path = os.path.join(RECORDS_DIR, sess, name)
        if os.path.exists(path):
            # Dosya boyutunu al (log için)
            try:
                file_size = os.path.getsize(path)
            except Exception:
                file_size = None

            os.remove(path)
            flash("Silindi.", "success")

            if syslog:
                try:
                    syslog.log_video_file_operation("DELETE", path, True, username)
                except Exception:
                    pass
        else:
            flash("Dosya bulunamadı.", "warning")
            if syslog:
                try:
                    syslog.log_video_file_operation("DELETE", path, False, username, "Dosya bulunamadı")
                except Exception:
                    pass
    except Exception as e:
        flash(f"Hata: {e}", "danger")
        if syslog:
            try:
                syslog.log_video_file_operation("DELETE", name, False, username, str(e))
            except Exception:
                pass
    return redirect(url_for("records.list_session", session=sess))


@records_bp.route("/<session>/delete_session", methods=["POST"])  # tüm oturumu silme
@_login_required
def delete_session(session):
    try:
        sess = _safe_session(session)
    except Exception:
        flash("Geçersiz oturum.", "danger")
        return redirect(url_for("records.list_records"))

    username = globals().get('session', {}).get("user", "Unknown")

    # Aktif oturum kontrolü - aktif oturumu silmeye izin verme
    if sess == SESSION_NAME:
        flash("Aktif oturum silinemez.", "warning")
        return redirect(url_for("records.list_records"))

    try:
        session_path = os.path.join(RECORDS_DIR, sess)

        if not os.path.exists(session_path):
            flash("Oturum bulunamadı.", "warning")
            return redirect(url_for("records.list_records"))

        # Oturum içindeki dosya sayısını al (log için)
        try:
            file_count = len([f for f in os.listdir(session_path) if os.path.isfile(os.path.join(session_path, f))])
        except Exception:
            file_count = 0

        # Tüm oturum klasörünü sil
        shutil.rmtree(session_path)
        flash(f"Oturum '{sess}' ve içindeki {file_count} dosya silindi.", "success")

        # Silme logu
        if syslog:
            try:
                syslog.log_system_event("SESSION_DELETE",
                                      f"Oturum silindi: {sess} ({file_count} dosya)",
                                      "INFO",
                                      username=username,
                                      session_name=sess,
                                      file_count=file_count)
            except Exception:
                pass

    except Exception as e:
        flash(f"Oturum silinirken hata: {e}", "danger")
        if syslog:
            try:
                syslog.log_system_event("SESSION_DELETE_ERROR",
                                      f"Oturum silme hatası: {sess}",
                                      "ERROR",
                                      username=username,
                                      session_name=sess,
                                      error=str(e))
            except Exception:
                pass

    return redirect(url_for("records.list_records"))


@records_bp.route("/<session>/rename_session", methods=["POST"])  # oturum ismini değiştirme
@_login_required
def rename_session(session):
    """Bir oturumun adını değiştir"""
    try:
        sess = _safe_session(session)
    except Exception:
        flash("Geçersiz oturum.", "danger")
        return redirect(url_for("records.list_records"))

    new_name = request.form.get("new_session_name", "").strip()
    username = globals().get('session', {}).get("user", "Unknown")

    # Aktif oturum kontrolü - aktif oturumun adı değiştirilemez
    if sess == SESSION_NAME:
        flash("Aktif oturumun adı değiştirilemez.", "warning")
        return redirect(url_for("records.list_records"))

    if not new_name:
        flash("Yeni oturum adı boş olamaz.", "warning")
        return redirect(url_for("records.list_records"))

    try:
        # Yeni ismi güvenli hale getir
        new_name = _safe_name(new_name)

        # Kaynak ve hedef klasör yolları
        src_path = os.path.join(RECORDS_DIR, sess)
        dst_path = os.path.join(RECORDS_DIR, new_name)

        # Kontroller
        if not os.path.exists(src_path):
            flash("Oturum bulunamadı.", "warning")
            return redirect(url_for("records.list_records"))

        if os.path.exists(dst_path):
            flash("Bu isimde bir oturum zaten var.", "warning")
            return redirect(url_for("records.list_records"))

        # Oturum klasörünü yeniden adlandır
        os.rename(src_path, dst_path)
        flash(f"Oturum adı '{sess}' → '{new_name}' olarak değiştirildi.", "success")

        # Loglama
        if syslog:
            try:
                syslog.log_system_event("SESSION_RENAME",
                                      f"Oturum adı değiştirildi: {sess} → {new_name}",
                                      "INFO",
                                      username=username,
                                      old_name=sess,
                                      new_name=new_name)
            except Exception:
                pass

        LOG.info(f"Oturum yeniden adlandırıldı: {sess} → {new_name} (kullanıcı: {username})")

        # Yeni oturum adıyla oturum detay sayfasına yönlendir
        return redirect(url_for("records.list_session", session=new_name))

    except Exception as e:
        flash(f"Hata: {e}", "danger")
        LOG.error(f"Oturum yeniden adlandırma hatası: {e}")
        if syslog:
            try:
                syslog.log_system_event("SESSION_RENAME",
                                      f"Oturum adı değiştirme hatası: {sess}",
                                      "ERROR",
                                      username=username,
                                      error=str(e))
            except Exception:
                pass

    return redirect(url_for("records.list_records"))


@records_bp.route("/delete_all_sessions", methods=["POST"])  # tüm oturumları silme
@_login_required
def delete_all_sessions():
    """Aktif oturum hariç tüm oturumları sil"""
    username = globals().get('session', {}).get("user", "Unknown")

    try:
        sessions = _list_sessions()
        deleted_count = 0
        skipped_count = 0
        total_files = 0

        for sess in sessions:
            sess_name = sess["name"]

            # Aktif oturum kontrolü - aktif oturumu atlama
            if sess_name == SESSION_NAME:
                skipped_count += 1
                continue

            try:
                session_path = os.path.join(RECORDS_DIR, sess_name)

                if not os.path.exists(session_path):
                    continue

                # Oturum içindeki dosya sayısını al
                try:
                    file_count = len([f for f in os.listdir(session_path)
                                    if os.path.isfile(os.path.join(session_path, f))])
                    total_files += file_count
                except Exception:
                    file_count = 0

                # Oturum klasörünü sil
                shutil.rmtree(session_path)
                deleted_count += 1

                LOG.info(f"Oturum silindi: {sess_name} ({file_count} dosya)")

            except Exception as e:
                LOG.error(f"Oturum silme hatası ({sess_name}): {e}")

        # Başarı mesajı
        if deleted_count > 0:
            flash(f"{deleted_count} oturum ve toplam {total_files} video dosyası silindi.", "success")

            # Silme logu
            if syslog:
                try:
                    syslog.log_system_event("ALL_SESSIONS_DELETE",
                                          f"Toplu oturum silme: {deleted_count} oturum, {total_files} dosya",
                                          "INFO",
                                          username=username,
                                          deleted_sessions=deleted_count,
                                          deleted_files=total_files,
                                          skipped_sessions=skipped_count)
                except Exception:
                    pass
        else:
            flash("Silinecek oturum bulunamadı.", "warning")

        if skipped_count > 0:
            flash(f"{skipped_count} aktif oturum korundu.", "info")

    except Exception as e:
        flash(f"Oturumlar silinirken hata: {e}", "danger")
        LOG.error(f"Toplu oturum silme hatası: {e}")

        if syslog:
            try:
                syslog.log_system_event("ALL_SESSIONS_DELETE_ERROR",
                                      "Toplu oturum silme hatası",
                                      "ERROR",
                                      username=username,
                                      error=str(e))
            except Exception:
                pass

    return redirect(url_for("records.list_records"))


@records_bp.route("/<session>/download/<path:filename>", methods=["GET"])  # indirme
@_login_required
def download_record(session, filename):
    try:
        sess = _safe_session(session)
        filename = _safe_name(filename)
        username = globals().get('session', {}).get("user", "Unknown")
        file_path = os.path.join(RECORDS_DIR, sess, filename)

        # İndirme logu
        if syslog:
            try:
                syslog.log_video_file_operation("DOWNLOAD", file_path, True, username)
            except Exception:
                pass

    except Exception:
        flash("Geçersiz dosya/oturum.", "danger")
        return redirect(url_for("records.list_records"))
    return send_from_directory(os.path.join(RECORDS_DIR, sess), filename, as_attachment=True)


# ============================ Puller (video_feed tetikleyici) ============
_puller_thread = None
_puller_stop = threading.Event()


def _puller_loop():
    """Kayıt açıkken /video_feed'e bağlanıp akışı tetikler. Veri okunur ve atılır."""
    global _puller_thread
    backoff = 0.5
    while not _puller_stop.is_set():
        # Kayıt bekle
        if not _recording_flag.is_set():
            time.sleep(0.2)
            continue
        try:
            conn = http.client.HTTPConnection(_FEED_HOST, _FEED_PORT, timeout=5)
            conn.request("GET", _FEED_PATH, headers={"Connection": "keep-alive"})
            resp = conn.getresponse()
            if resp.status != 200:
                LOG.warning(f"puller: HTTP {resp.status}")
                conn.close(); time.sleep(backoff); backoff = min(5.0, backoff*1.5)
                continue
            LOG.info("puller: /video_feed bağlandı.")
            backoff = 0.5
            # Kayıt açık kaldıkça küçük parçalar oku ve at
            while _recording_flag.is_set() and not _puller_stop.is_set():
                chunk = resp.read(4096)
                if not chunk:
                    break
            try:
                conn.close()
            except Exception:
                pass
        except Exception as e:
            LOG.debug(f"puller: bağlanamadı: {e}")
            time.sleep(backoff)
            backoff = min(5.0, backoff*1.5)


def _ensure_puller_running():
    global _puller_thread
    if _puller_thread is None or not _puller_thread.is_alive():
        _puller_thread = threading.Thread(target=_puller_loop, name="rec-puller", daemon=True)
        _puller_thread.start()


# ============================ Başlat/Durdur ===============================
_threads_started = False

def start_background():
    global _threads_started
    if _threads_started:
        return
    # Oturum klasörünü oluştur
    _ensure_session_dir()
    # LED GPIO kurulumu
    _setup_led_gpio()
    # writer thread
    t1 = threading.Thread(target=_writer_loop, name="rec-writer", daemon=True)
    t1.start()
    # gpio watcher thread
    t2 = threading.Thread(target=_record_gpio_watcher, name="rec-gpio", daemon=True)
    t2.start()
    # puller thread (bekleme modunda başlar)
    _ensure_puller_running()
    _threads_started = True
    LOG.info("recordsVideo arkaplan servisleri başlatıldı.")


def stop_background():
    _stop_all.set()
    _recording_flag.clear()
    _puller_stop.set()
    # writer kapat
    with _writer_lock:
        _close_writer()
    LOG.info("recordsVideo servisleri durduruldu.")
