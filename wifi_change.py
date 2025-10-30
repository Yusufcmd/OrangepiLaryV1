#!/usr/bin/env python3
# Wi-Fi Paneli (Flask) — Orange Pi / Armbian (AP-only band/kanal ayarları)
from flask import Flask, render_template, request, redirect, url_for, session, flash
from flask_sqlalchemy import SQLAlchemy
from flask_wtf import CSRFProtect
# from werkzeug.security import check_password_hash  # Hash kullanılmıyor (projeyle uyum için düz metin)
import subprocess, shlex, os, pathlib, sys, tempfile, shutil

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

# DB yolu: db_util ile yazılabilir sqlite yolu (öncelik: site.db; eski data/site.db varsa köke taşınır)
try:
    import db_util
    DB_URI = db_util.resolve_sqlite_uri(BASE_DIR)
    DB_PATH = db_util.get_db_path(BASE_DIR)
except Exception:
    DB_PATH  = os.path.join(BASE_DIR, "site.db")
    DB_URI   = f"sqlite:///{DB_PATH}"

app = Flask(__name__, template_folder=os.path.join(BASE_DIR, "templates"))
app.config["SECRET_KEY"] = os.environ.get("APP_SECRET_KEY", "replace_me_very_secret")
app.config["SQLALCHEMY_DATABASE_URI"] = DB_URI
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
# Prod öneri:
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = False  # true yapabilirsiniz (HTTPS varsa)

db = SQLAlchemy(app)
csrf = CSRFProtect(app)

# ---- MODELLER ----
class User(db.Model):
    id       = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(20), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)  # PLAINTEXT (main.py ile tutarlı)

# ---- YARDIMCILAR ----
def login_required(fn):
    from functools import wraps
    @wraps(fn)
    def _wrap(*a, **k):
        if not session.get("uid"):
            return redirect(url_for("login"))
        return fn(*a, **k)
    return _wrap

# Basit parola doğrulama (projede hash kullanılmıyor)
def verify_password(stored: str, provided: str) -> bool:
    return (stored or "") == (provided or "")

# Basit ayrıcalık/sudo yardımcıları
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

def _sudo_install_file(tmp_path: str, dest_path: str) -> tuple[bool, str]:
    """sudo -n install -D -m 644 tmp dest ile dosyayı yerine koy.
    sudoers yoksa veya parola isteniyorsa False döner.
    """
    cmd = ["sudo", "-n", "install", "-D", "-m", "644", tmp_path, dest_path]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True)
        if p.returncode == 0:
            return True, ""
        return False, (p.stderr or p.stdout)
    except Exception as e:
        return False, str(e)

        pass
    if band == "2.4" and ch not in CHANNELS_24: ch = 6
    if band == "5"   and ch not in CHANNELS_5:  ch = 36
    return band, ch

def write_ap_band_channel(band: str, channel: int) -> tuple[bool, str]:
    """hostapd.conf içinde hw_mode ve channel güncelle.
    Başarı durumunda (True, mesaj), aksi halde (False, hata).
    """
    band = "5" if str(band).strip() in ("5","5.0","5ghz","a") else "2.4"
    try:
        channel = int(channel)
    except Exception:
        return False, f"Geçersiz kanal: {channel}"
    if band == "2.4" and channel not in CHANNELS_24:
        return False, f"2.4 GHz için geçersiz kanal: {channel}"
    if band == "5" and channel not in CHANNELS_5:
        return False, f"5 GHz için geçersiz kanal: {channel}"

    path = hostapd_conf_path()
    try:
        if not os.path.exists(path):
            # Basit bir başlangıç içeriği oluştur
            pathlib.Path(os.path.dirname(path) or "/etc/hostapd").mkdir(parents=True, exist_ok=True)
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
def _build_hostapd_updated_lines(existing_lines: list[str], band: str, channel: int) -> list[str]:
    out = []
    saw_mode = False; saw_chan = False
    for l in existing_lines:
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
    return out

def _atomic_write_with_sudo_fallback(dest_path: str, content: str) -> tuple[bool, str]:
    """dest_path'e atomik yaz. İzin hatasında sudo -n install -D ile dener."""
    # Önce tmp dosyaya yaz
    try:
        with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as tf:
            tmp_path = tf.name
            tf.write(content)
            tf.flush()
            os.fsync(tf.fileno())
    except Exception as e:
        return False, f"Geçici dosya yazılamadı: {e}"

    # Önce doğrudan replace dene (root ise başarılı olur)
    try:
        # Yedek (en iyi gayret)
        try:
            if os.path.exists(dest_path):
                shutil.copy2(dest_path, dest_path + ".bak")
        except Exception:
            pass
        os.makedirs(os.path.dirname(dest_path) or "/", exist_ok=True)
        os.replace(tmp_path, dest_path)
        return True, ""
    except PermissionError:
        # Sudo fallback
        if not _have_sudo_noninteractive():
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
            return False, "Yazma izni yok. Uygulamayı root olarak çalıştırın veya sudoers ile yetki verin."
        ok, emsg = _sudo_install_file(tmp_path, dest_path)
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
        if ok:
            return True, ""
        return False, f"sudo ile yazma başarısız: {emsg.strip()}"
    except Exception as e:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
        return False, f"Dosya yazma hatası: {e}"


@login_required
def index():
    band, ch = read_ap_band_channel()
    return render_template(
        "wifi_settings_simple.html",
        band=band,
        channel=ch,
        channels_24=CHANNELS_24,
        channels_5=CHANNELS_5,
    )

# Uygula: band/kanal yaz ve hostapd restart et
@app.route("/apply_band_channel", methods=["POST"])
@login_required
def apply_band_channel():
    band = (request.form.get("band") or "2.4").strip()
    try:
            # Dosya yoksa temel içerik oluştur; kalan anahtarlar korunur
        channel = 6
    ok, msg = write_ap_band_channel(band, channel)
    if not ok:
        flash(msg, "error")
        return redirect(url_for("index"))
    rok, rmsg = restart_hostapd()
    flash((msg + (" — " + rmsg if rmsg else "")), "success" if rok else "warning")
    return redirect(url_for("index"))
            existing = base
        else:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                existing = f.readlines()
        out_lines = _build_hostapd_updated_lines(existing, band, channel)
        out_text = "".join(out_lines)
        # Yazmayı atomik ve yetki dostu yap
        ok, emsg = _atomic_write_with_sudo_fallback(path, out_text)
        if not ok:
            # Kullanıcıya yol gösteren daha açıklayıcı mesaj
            hint = (
                "hostapd yazılamadı. Bu paneli root olarak çalıştırın (systemd servisi ile) "
                "veya aşağıdaki sudoers kuralını ekleyin: \n"
                "  echo 'www-data ALL=(root) NOPASSWD:/usr/bin/install, /bin/systemctl' | sudo tee /etc/sudoers.d/clary-wifi\n"
                "Ardından web servisini yeniden başlatın."
            )
            return False, f"hostapd yazılamadı ({path}): {emsg}. {hint}"
