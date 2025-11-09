#!/usr/bin/env bash
# Wi-Fi yönetimi için sudoers izinlerini yapılandırma scripti
# Bu script Flask uygulamasının Wi-Fi modlarını yönetebilmesi için gerekli izinleri verir

set -e

echo "=========================================="
echo "Wi-Fi Yönetim İzinleri Kurulumu"
echo "=========================================="
echo ""

# Root kontrolü
if [ "$EUID" -ne 0 ]; then
    echo "HATA: Bu script root olarak çalıştırılmalıdır"
    echo "Kullanım: sudo bash setup_wifi_permissions.sh"
    exit 1
fi

# Flask uygulamasını çalıştıran kullanıcıyı tespit et
echo "Flask uygulamasını hangi kullanıcı çalıştırıyor?"
echo "Genellikle: www-data (systemd servisi) veya mevcut kullanıcınız"
echo ""
read -p "Kullanıcı adı [varsayılan: $SUDO_USER]: " FLASK_USER
FLASK_USER=${FLASK_USER:-$SUDO_USER}

if [ -z "$FLASK_USER" ]; then
    echo "HATA: Kullanıcı adı belirtilmedi"
    exit 1
fi

# Kullanıcının var olduğunu kontrol et
if ! id "$FLASK_USER" &>/dev/null; then
    echo "HATA: Kullanıcı '$FLASK_USER' bulunamadı"
    exit 1
fi

echo "Kullanıcı: $FLASK_USER"
echo ""

# Sudoers yapılandırması oluştur
SUDOERS_FILE="/etc/sudoers.d/orangepi-wifi-manager"

echo "Sudoers yapılandırması oluşturuluyor: $SUDOERS_FILE"

cat > "$SUDOERS_FILE" << EOF
# OrangePi Wi-Fi Yönetim İzinleri
# Flask uygulamasının Wi-Fi modlarını yönetebilmesi için gerekli izinler
# Kullanıcı: $FLASK_USER
# Oluşturma: $(date)

# Komut alias'ları
Cmnd_Alias WIFI_SCRIPTS = /opt/lscope/bin/sta_mode.sh, /opt/lscope/bin/ap_mode.sh, /usr/local/sbin/sta_mode.sh, /usr/local/sbin/ap_mode.sh
Cmnd_Alias SYSTEM_CMDS = /bin/systemctl restart hostapd, /bin/systemctl restart NetworkManager, /bin/systemctl restart dnsmasq, /bin/systemctl enable *, /bin/systemctl disable *, /bin/systemctl stop *, /bin/systemctl start *, /bin/systemctl daemon-reload, /bin/systemctl unmask *
Cmnd_Alias INSTALL_CMD = /usr/bin/install
Cmnd_Alias NETWORK_CMDS = /usr/bin/nmcli, /sbin/ip, /bin/chmod, /bin/mkdir, /bin/ln, /usr/sbin/hostapd

# İzinler (şifre sormadan)
$FLASK_USER ALL=(root) NOPASSWD: WIFI_SCRIPTS, SYSTEM_CMDS, INSTALL_CMD, NETWORK_CMDS
EOF

# Dosya izinlerini ayarla
chmod 0440 "$SUDOERS_FILE"

# Sudoers dosyasını doğrula
echo "Sudoers dosyası doğrulanıyor..."
if visudo -c -f "$SUDOERS_FILE"; then
    echo "✓ Sudoers yapılandırması başarıyla oluşturuldu"
else
    echo "✗ HATA: Sudoers dosyası geçersiz, siliniyor..."
    rm -f "$SUDOERS_FILE"
    exit 1
fi

echo ""

# /opt/lscope/bin dizinini oluştur
echo "Script dizini oluşturuluyor: /opt/lscope/bin"
mkdir -p /opt/lscope/bin
chmod 755 /opt/lscope/bin
echo "✓ Dizin oluşturuldu"

echo ""

# hostapd.conf için izinler (eğer yoksa oluştur)
HOSTAPD_CONF="/etc/hostapd/hostapd.conf"
if [ ! -f "$HOSTAPD_CONF" ]; then
    echo "hostapd.conf bulunamadı, temel yapılandırma oluşturuluyor..."
    mkdir -p /etc/hostapd
    cat > "$HOSTAPD_CONF" << 'HEOF'
interface=wlan0
driver=nl80211
ssid=OrangePiAP
hw_mode=g
channel=6
country_code=TR
ieee80211n=1
wmm_enabled=1
macaddr_acl=0
auth_algs=1
ignore_broadcast_ssid=0
wpa=2
wpa_passphrase=simclever123
wpa_key_mgmt=WPA-PSK
wpa_pairwise=TKIP
rsn_pairwise=CCMP
HEOF
    chmod 644 "$HOSTAPD_CONF"
    echo "✓ Temel hostapd.conf oluşturuldu"
else
    echo "✓ hostapd.conf mevcut: $HOSTAPD_CONF"
fi

echo ""
echo "=========================================="
echo "Kurulum Tamamlandı!"
echo "=========================================="
echo ""
echo "Flask uygulamanız artık Wi-Fi modlarını yönetebilir."
echo ""
echo "Test için:"
echo "  1. Flask uygulamasını yeniden başlatın"
echo "  2. Arayüzden STA veya AP moduna geçiş yapın"
echo ""
echo "Sorun yaşarsanız:"
echo "  - Flask uygulamasının '$FLASK_USER' kullanıcısıyla çalıştığından emin olun"
echo "  - Log dosyalarını kontrol edin: /var/log/wifi_mode.log"
echo "  - Manuel test: sudo /opt/lscope/bin/sta_mode.sh veya sudo /opt/lscope/bin/ap_mode.sh"
echo ""

