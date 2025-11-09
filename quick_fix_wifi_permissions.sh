#!/usr/bin/env bash
# Hızlı Wi-Fi İzin Düzeltme Scripti
# Kullanım: sudo bash quick_fix_wifi_permissions.sh KULLANICI_ADI

set -e

if [ "$EUID" -ne 0 ]; then
    echo "HATA: Bu script root olarak çalıştırılmalıdır"
    echo "Kullanım: sudo bash quick_fix_wifi_permissions.sh [kullanici_adi]"
    exit 1
fi

# Kullanıcı adını al
FLASK_USER="${1:-$SUDO_USER}"

if [ -z "$FLASK_USER" ]; then
    echo "HATA: Kullanıcı adı belirtilmedi"
    echo "Kullanım: sudo bash quick_fix_wifi_permissions.sh KULLANICI_ADI"
    exit 1
fi

echo "=== Wi-Fi İzinleri Yapılandırılıyor ==="
echo "Kullanıcı: $FLASK_USER"

# Sudoers dosyasını oluştur
cat > /etc/sudoers.d/orangepi-wifi-manager << EOF
# OrangePi Wi-Fi Yönetim İzinleri
Cmnd_Alias WIFI_SCRIPTS = /opt/lscope/bin/*, /usr/local/sbin/sta_mode.sh, /usr/local/sbin/ap_mode.sh
Cmnd_Alias SYSTEM_CMDS = /bin/systemctl *, /usr/bin/systemctl *
Cmnd_Alias INSTALL_CMD = /usr/bin/install
Cmnd_Alias NETWORK_CMDS = /usr/bin/nmcli *, /sbin/ip *, /bin/chmod *, /bin/mkdir *, /bin/ln *, /usr/sbin/hostapd *

$FLASK_USER ALL=(root) NOPASSWD: WIFI_SCRIPTS, SYSTEM_CMDS, INSTALL_CMD, NETWORK_CMDS
EOF

chmod 0440 /etc/sudoers.d/orangepi-wifi-manager

# Doğrula
if visudo -c -f /etc/sudoers.d/orangepi-wifi-manager; then
    echo "✓ Sudoers yapılandırması oluşturuldu"
else
    echo "✗ HATA: Sudoers dosyası geçersiz"
    rm -f /etc/sudoers.d/orangepi-wifi-manager
    exit 1
fi

# Dizinleri oluştur
mkdir -p /opt/lscope/bin
chmod 755 /opt/lscope/bin
echo "✓ Script dizini hazır"

# hostapd.conf kontrol
if [ ! -f /etc/hostapd/hostapd.conf ]; then
    mkdir -p /etc/hostapd
    cat > /etc/hostapd/hostapd.conf << 'HEOF'
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
    chmod 644 /etc/hostapd/hostapd.conf
    echo "✓ hostapd.conf oluşturuldu"
fi

echo ""
echo "=== TAMAMLANDI ==="
echo "Flask uygulamanızı yeniden başlatın:"
echo "  sudo systemctl restart lscope"
echo "veya"
echo "  Uygulamayı manuel olarak durdurup yeniden başlatın"
echo ""
echo "Test: Arayüzden STA/AP moduna geçiş yapın"

