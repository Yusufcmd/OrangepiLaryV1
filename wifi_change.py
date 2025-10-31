#!/usr/bin/env python3
# Wi-Fi Paneli (Flask) — Orange Pi / Armbian (AP-only band/kanal ayarları)
from flask import Flask, render_template, request, redirect, url_for, session, flash
from flask_sqlalchemy import SQLAlchemy
from flask_wtf import CSRFProtect
# from werkzeug.security import check_password_hash  # Hash kullanılmıyor (projeyle uyum için düz metin)
import subprocess, shlex, os, pathlib, sys, tempfile, shutil, errno
from typing import Union, List, Tuple

# CSRF token üretici (mevcutsa)
try:
    from flask_wtf.csrf import generate_csrf
except Exception:
    generate_csrf = None

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

# CSRF token’i Jinja ortamına enjekte et
@app.context_processor
def inject_csrf():
    if generate_csrf:
        return dict(csrf_token=generate_csrf)
    return {}

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

def _sudo_install_file(tmp_path: str, dest_path: str, mode: str = "755") -> tuple[bool, str]:
    """sudo -n install -D -m MODE tmp dest ile dosyayı yerine koy.
    sudoers yoksa veya parola isteniyorsa False döner.
    mode: dosya izinleri (varsayılan 755 - çalıştırılabilir)
    """
    cmd = ["sudo", "-n", "install", "-D", "-m", mode, tmp_path, dest_path]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True)
        if p.returncode == 0:
            return True, ""
        return False, (p.stderr or p.stdout)
    except Exception as e:
        return False, str(e)

# ---- Script üretim/deploy yardımcıları ----

def _run(cmd: Union[List[str], str], timeout: int = 15) -> Tuple[int, str, str]:
    """Küçük bir run helper.
    cmd: listeyse doğrudan, stringse shell=True ile çalıştırılır.
    """
    try:
        if isinstance(cmd, list):
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        else:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, shell=True)
        return p.returncode, p.stdout, p.stderr
    except Exception as e:
        return 1, "", str(e)


def _ensure_dir(path: str) -> tuple[bool, str]:
    """/opt/lscope/bin gibi klasörleri oluştur."""
    try:
        os.makedirs(path, exist_ok=True)
        return True, ""
    except PermissionError:
        if _have_sudo_noninteractive():
            code, out, err = _run(["sudo", "-n", "install", "-d", "-m", "755", path])
            return (code == 0), (out or err)
        return False, "Klasör oluşturma izni yok"
    except Exception as e:
        return False, str(e)


def _deploy_file_executable(dest_path: str, content: str) -> tuple[bool, str]:
    """İçeriği hedefe yaz ve çalıştırılabilir yap.
    _atomic_write_with_sudo_fallback kullanır (mode=755), ardından gerekirse chmod +x dener.
    """
    ok, emsg = _atomic_write_with_sudo_fallback(dest_path, content, mode="755")
    if not ok:
        return False, emsg

    # chmod +x (ek güvence - zaten 755 ile yazıldı ama kontrol)
    try:
        current_mode = os.stat(dest_path).st_mode
        if not (current_mode & 0o111):  # Çalıştırılabilir değilse
            os.chmod(dest_path, 0o755)
        return True, ""
    except PermissionError:
        if _have_sudo_noninteractive():
            code, out, err = _run(["sudo", "-n", "chmod", "+x", dest_path])
            if code == 0:
                return True, ""
            return False, (err or out)
        # Zaten 755 ile yazıldıysa sorun olmayabilir
        return True, ""
    except Exception as e:
        # Dosya yazıldı ama chmod başarısız - genelde sorun olmaz
        return True, ""


def _opt_noexec() -> bool:
    if not _is_posix():
        return False
    code, out, _ = _run(["mount"])
    if code != 0:
        return False
    for line in out.splitlines():
        if " /opt " in line and "noexec" in line:
            return True
    return False


def _install_alt_and_symlink(src_path: str, alt_path: str) -> tuple[bool, str]:
    """/opt noexec ise, alt_path'e kopyala ve /opt yoluna symlink bırak."""
    # kopyala
    if _is_posix() and _have_sudo_noninteractive():
        code1, out1, err1 = _run(["sudo", "-n", "install", "-Dm755", src_path, alt_path])
        if code1 != 0:
            return False, (err1 or out1)
        # symlink
        code2, out2, err2 = _run(["sudo", "-n", "ln", "-sf", alt_path, src_path])
        if code2 != 0:
            return False, (err2 or out2)
        return True, ""
    return False, "sudo erişimi yok"


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


def read_ap_ssid() -> str:
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


def _sed_escape(val: str) -> str:
    """sed replacement için güvenli kaçış: & ve tek tırnakları kaçır."""
    s = str(val).replace("&", r"\&")
    return s.replace("'", "'\"'\"'")


def _ap_script_content(ap_ssid: str, ap_psk: str, iface: str = "wlan0") -> str:
    # hostapd.conf içindeki ssid ve wpa_passphrase'yi mevcut değerlere sabitliyoruz
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

# Ortak: script çalıştır

def _run_script(script_path: str, timeout: int = 90) -> tuple[bool, str]:
    cmd = [script_path]
    if _is_root():
        pass
    elif _have_sudo_noninteractive():
        cmd = ["sudo", "-n", script_path]
    else:
        return False, "Root/sudo yetkisi yok. sudoers yapılandırın."
    code, out, err = _run(cmd, timeout=timeout)
    if code == 0:
        return True, out.strip()
    return False, (err or out)

# ---- HOSTAPD YARDIMCILARI ----

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

def _atomic_write_with_sudo_fallback(dest_path: str, content: str, mode: str = "644") -> tuple[bool, str]:
    """dest_path'e atomik yaz. İzin veya EXDEV durumunda sudo -n install ile dener.
    - Öncelik: hedef dizinde geçici dosya oluştur (aynı FS), os.replace ile atomik yaz.
    - Eğer hedef dizinde tmp oluşturulamazsa: sistem tmp'de oluştur, EXDEV olursa kopyalama veya sudo install ile tamamla.

    Args:
        dest_path: Hedef dosya yolu
        content: Yazılacak içerik
        mode: Dosya izinleri (varsayılan 644, çalıştırılabilir için 755)
    """
    dest_dir = os.path.dirname(dest_path) or "/"

    tmp_path = None
    tmp_in_dest_dir = False

    # 0) Mümkünse hedef dizinde geçici dosya oluştur (aynı dosya sistemi)
    try:
        os.makedirs(dest_dir, exist_ok=True)
        tf = tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8", dir=dest_dir, prefix=".tmp-")
        tmp_path = tf.name
        tmp_in_dest_dir = True
        tf.write(content)
        tf.flush(); os.fsync(tf.fileno()); tf.close()
    except Exception:
        # 1) Hedef dizinde başarısızsa, sistem tmp'de oluştur
        try:
            with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as tf2:
                tmp_path = tf2.name
                tf2.write(content)
                tf2.flush(); os.fsync(tf2.fileno())
        except Exception as e:
            return False, f"Geçici dosya yazılamadı: {e}"

    # 2) Yedek almaya çalış (best-effort)
    try:
        if os.path.exists(dest_path):
            shutil.copy2(dest_path, dest_path + ".bak")
    except Exception:
        pass

    # 3) Atomik replace dene
    try:
        os.replace(tmp_path, dest_path)
        # İzinleri ayarla
        try:
            os.chmod(dest_path, int(mode, 8))
        except Exception:
            pass
        return True, ""
    except PermissionError:
        # İzin yoksa sudo ile kopyala
        if not _have_sudo_noninteractive():
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
            return False, "Yazma izni yok. Uygulamayı root olarak çalıştırın veya sudoers ile yetki verin."
        ok, emsg = _sudo_install_file(tmp_path, dest_path, mode=mode)
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
        if ok:
            return True, ""
        return False, f"sudo ile yazma başarısız: {emsg.strip()}"
    except OSError as e:
        # Cross-device (EXDEV) veya diğer OS hataları
        # errno 18 = EXDEV (Invalid cross-device link)
        if e.errno == errno.EXDEV or e.errno == 18:
            # Farklı dosya sistemleri arası taşıma - sudo install kullan
            if _have_sudo_noninteractive():
                ok, emsg = _sudo_install_file(tmp_path, dest_path, mode=mode)
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass
                if ok:
                    return True, ""
                return False, f"sudo ile yazma başarısız (EXDEV): {emsg.strip()}"

            # sudo yoksa manuel kopyalama dene
            try:
                with open(dest_path, "w", encoding="utf-8") as out_f, open(tmp_path, "r", encoding="utf-8") as in_f:
                    out_f.write(in_f.read())
                    out_f.flush()
                    os.fsync(out_f.fileno())
                # İzinleri ayarla
                try:
                    os.chmod(dest_path, int(mode, 8))
                except Exception:
                    pass
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass
                return True, ""
            except PermissionError:
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass
                return False, "Yazma izni yok (EXDEV). Uygulamayı root olarak çalıştırın veya sudoers ile yetki verin."
            except Exception as e2:
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass
                return False, f"Dosya yazma hatası (EXDEV fallback): {e2}"
        # EXDEV değilse genel hata
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
        mode="ap",  # varsayılan görünüm AP sekmesi
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

    # AP script'i (ap_mode.sh) mevcut ssid/psk ile senkronize et ve çalıştır
    ap_ssid = read_ap_ssid()
    ap_psk = read_ap_password()
    if _is_posix():
        _sync_ap_script(ap_ssid, ap_psk)
        ran, rmsg = _run_script("/opt/lscope/bin/ap_mode.sh", timeout=60)
        flash((msg + (" — AP script: OK" if ran else f" — AP script hata: {rmsg}")), "success" if ran else "warning")
        return redirect(url_for("index"))

    # POSIX değilse sadece mesaj
    flash(msg, "success")
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

    # AP script senkronizasyonu ve çalıştırma
    if _is_posix():
        ap_ssid = read_ap_ssid()
        _sync_ap_script(ap_ssid, new_password)
        ran, rmsg = _run_script("/opt/lscope/bin/ap_mode.sh", timeout=60)
        flash((msg + (" — AP script: OK" if ran else f" — AP script hata: {rmsg}")), "success" if ran else "warning")
        return redirect(url_for("index"))

    flash(msg, "success")
    return redirect(url_for("index"))

# --- STA: SSID/Şifre kaydet ve script'i çalıştır ---
@app.route("/connect_sta_network", methods=["POST"])
@login_required
def connect_sta_network():
    if not _is_posix():
        flash("Bu işlem yalnızca cihaz üzerinde (Linux) desteklenir", "error")
        return redirect(url_for("index"))

    ssid = (request.form.get("ssid") or "").strip()
    psk  = (request.form.get("password") or "").strip()

    if not ssid:
        flash("SSID zorunludur", "error")
        return redirect(url_for("index"))
    if len(psk) < 8:
        flash("Şifre en az 8 karakter olmalıdır", "error")
        return redirect(url_for("index"))

    # 1) /opt/lscope/bin altını garanti et
    bin_dir = "/opt/lscope/bin"
    ok, emsg = _ensure_dir(bin_dir)
    if not ok:
        flash(f"Script klasörü oluşturulamadı: {emsg}", "error")
        return redirect(url_for("index"))

    # 2) sta_mode.sh içeriğini yaz
    sta_path = f"{bin_dir}/sta_mode.sh"
    content = _sta_script_content(ssid, psk)
    ok, emsg = _deploy_file_executable(sta_path, content)
    if not ok:
        flash(f"sta_mode.sh yazılamadı: {emsg}", "error")
        return redirect(url_for("index"))

    # 3) /opt noexec ise alternatif konuma taşı ve symlink bırak
    if _opt_noexec():
        alt = "/usr/local/sbin/sta_mode.sh"
        ok, emsg = _install_alt_and_symlink(sta_path, alt)
        if not ok:
            flash(f"noexec ortamında script taşınamadı: {emsg}", "warning")

    # 4) AP script'ini de mevcut AP SSID/PSK ile (hostapd.conf'tan) güncelle — ileride geri dönüşte kullanılır
    ap_ssid = read_ap_ssid()
    ap_psk  = read_ap_password()
    _sync_ap_script(ap_ssid, ap_psk)

    # 5) Script'i çalıştır
    ran, rmsg = _run_script(sta_path, timeout=90)
    if ran:
        flash("STA moduna geçiş başlatıldı. Cihaz ağa bağlanmayı deniyor.", "success")
    else:
        flash(f"Script çalıştırma hatası: {rmsg}", "error")
    return redirect(url_for("index"))


# --- Dahili: ap_mode.sh'yi güncelle ---

def _sync_ap_script(ap_ssid: str, ap_psk: str) -> None:
    try:
        if not _is_posix():
            return
        bin_dir = "/opt/lscope/bin"
        ok, _ = _ensure_dir(bin_dir)
        if not ok:
            return
        ap_path = f"{bin_dir}/ap_mode.sh"
        content = _ap_script_content(ap_ssid, ap_psk, iface="wlan0")
        ok, _ = _deploy_file_executable(ap_path, content)
        if not ok:
            return
        if _opt_noexec():
            _install_alt_and_symlink(ap_path, "/usr/local/sbin/ap_mode.sh")
    except Exception:
        pass

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(host="0.0.0.0", port=5001, debug=True)
