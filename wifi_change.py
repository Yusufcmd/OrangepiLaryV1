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

# ---- SABİTLER ----
CHANNELS_24 = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]
CHANNELS_5 = [36, 40, 44, 48, 149, 153, 157, 161, 165]

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

def hostapd_conf_path() -> str:
    """hostapd.conf dosyasının yolunu döndür."""
    return "/etc/hostapd/hostapd.conf"

def read_ap_band_channel() -> tuple[str, int]:
    """hostapd.conf'tan mevcut band ve kanalı oku."""
    path = hostapd_conf_path()
    band = "2.4"
    ch = 6
    if not os.path.exists(path):
        return band, ch
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                ls = line.strip()
                if ls.startswith("hw_mode="):
                    mode = ls.split("=", 1)[1].strip()
                    band = "5" if mode == "a" else "2.4"
                elif ls.startswith("channel="):
                    try:
                        ch = int(ls.split("=", 1)[1].strip())
                    except Exception:
                        pass
    except Exception:
        pass
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
    except Exception:
        pass
    return "simclever123"

def restart_hostapd() -> tuple[bool, str]:
    """hostapd servisini yeniden başlat."""
    if not _is_posix():
        return False, "Windows'ta desteklenmiyor"
    try:
        # Önce root kontrolü
        if _is_root():
            cmd = ["systemctl", "restart", "hostapd"]
        elif _have_sudo_noninteractive():
            cmd = ["sudo", "-n", "systemctl", "restart", "hostapd"]
        else:
            return False, "hostapd yeniden başlatma izni yok. Root veya sudoers gerekli."

        p = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if p.returncode == 0:
            return True, "hostapd yeniden başlatıldı"
        return False, f"hostapd restart başarısız: {p.stderr or p.stdout}"
    except Exception as e:
        return False, f"hostapd restart hatası: {e}"

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
        return True, f"Band: {band} GHz, Kanal: {channel} olarak ayarlandı"
    except Exception as e:
        return False, f"Beklenmeyen hata: {e}"

def write_ap_password(new_password: str) -> tuple[bool, str]:
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
            pathlib.Path(os.path.dirname(path) or "/etc/hostapd").mkdir(parents=True, exist_ok=True)
            base = [
                "interface=wlan0\n",
                "driver=nl80211\n",
                "ssid=OrangePiAP\n",
                "country_code=TR\n",
                "wpa=2\n",
                f"wpa_passphrase={new_password}\n",
            ]
            existing = base
        else:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                existing = f.readlines()

        # Şifreyi güncelle
        out = []
        saw_pass = False
        for line in existing:
            ls = line.strip()
            if ls.startswith("wpa_passphrase="):
                out.append(f"wpa_passphrase={new_password}\n")
                saw_pass = True
            else:
                out.append(line)

        if not saw_pass:
            out.append(f"wpa_passphrase={new_password}\n")

        out_text = "".join(out)
        ok, emsg = _atomic_write_with_sudo_fallback(path, out_text)
        if not ok:
            hint = (
                "hostapd yazılamadı. Bu paneli root olarak çalıştırın (systemd servisi ile) "
                "veya aşağıdaki sudoers kuralını ekleyin: \n"
                "  echo 'www-data ALL=(root) NOPASSWD:/usr/bin/install, /bin/systemctl' | sudo tee /etc/sudoers.d/clary-wifi\n"
                "Ardından web servisini yeniden başlatın."
            )
            return False, f"hostapd yazılamadı ({path}): {emsg}. {hint}"
        return True, "Wi-Fi şifresi güncellendi"
    except Exception as e:
        return False, f"Beklenmeyen hata: {e}"

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

# ---- ROUTES ----
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = User.query.filter_by(username=username).first()
        if user and verify_password(user.password, password):
            session["uid"] = user.id
            return redirect(url_for("index"))
        flash("Kullanıcı adı veya şifre hatalı", "error")
    return render_template("login_csrf.html")

@app.route("/logout")
def logout():
    session.pop("uid", None)
    return redirect(url_for("login"))

@app.route("/")
@login_required
def index():
    band, ch = read_ap_band_channel()
    current_password = read_ap_password()
    return render_template(
        "wifi_settings_simple.html",
        band=band,
        channel=ch,
        channels_24=CHANNELS_24,
        channels_5=CHANNELS_5,
        current_password=current_password,
    )

# Uygula: band/kanal yaz ve hostapd restart et
@app.route("/apply_band_channel", methods=["POST"])
@login_required
def apply_band_channel():
    band = (request.form.get("band") or "2.4").strip()
    try:
        channel = int(request.form.get("channel", "6"))
    except Exception:
        channel = 6
    ok, msg = write_ap_band_channel(band, channel)
    if not ok:
        flash(msg, "error")
        return redirect(url_for("index"))
    rok, rmsg = restart_hostapd()
    flash((msg + (" — " + rmsg if rmsg else "")), "success" if rok else "warning")
    return redirect(url_for("index"))

# Şifre değiştirme route'u
@app.route("/apply_password", methods=["POST"])
@login_required
def apply_password():
    new_password = request.form.get("password", "").strip()
    ok, msg = write_ap_password(new_password)
    if not ok:
        flash(msg, "error")
        return redirect(url_for("index"))
    rok, rmsg = restart_hostapd()
    flash((msg + (" — " + rmsg if rmsg else "")), "success" if rok else "warning")
    return redirect(url_for("index"))

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(host="0.0.0.0", port=5001, debug=True)
