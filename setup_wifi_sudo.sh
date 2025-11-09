#!/usr/bin/env bash
# WiFi değiştirme yetkilerini yapılandırma scripti
# Bu scripti sudo ile çalıştırın: sudo bash setup_wifi_sudo.sh

set -e

echo "======================================"
echo "WiFi Yetkilendirme Kurulumu"
echo "======================================"

# Web uygulamasının hangi kullanıcı ile çalıştığını bul
WEB_USER=""
if systemctl is-active --quiet clary-main.service 2>/dev/null; then
    WEB_USER=$(systemctl show -p User --value clary-main.service 2>/dev/null || echo "")
fi

# Alternatif servis adları
if [ -z "$WEB_USER" ] || [ "$WEB_USER" = "root" ]; then
    if systemctl is-active --quiet clary.service 2>/dev/null; then
        WEB_USER=$(systemctl show -p User --value clary.service 2>/dev/null || echo "")
    fi
fi

# Alternatif: nginx/apache/rise kullanıcıları
if [ -z "$WEB_USER" ] || [ "$WEB_USER" = "root" ]; then
    if id -u rise >/dev/null 2>&1; then
        WEB_USER="rise"
    elif id -u www-data >/dev/null 2>&1; then
        WEB_USER="www-data"
    elif id -u nginx >/dev/null 2>&1; then
        WEB_USER="nginx"
    elif id -u orangepi >/dev/null 2>&1; then
        WEB_USER="orangepi"
    else
        echo "HATA: Web servis kullanıcısı bulunamadı!"
        echo "Lütfen web servisinizin hangi kullanıcı ile çalıştığını kontrol edin."
        echo "Örnek: systemctl show -p User --value clary-main.service"
        exit 1
    fi
fi

echo "Web servis kullanıcısı: $WEB_USER"

# sudoers dosyası oluştur
SUDOERS_FILE="/etc/sudoers.d/clary-wifi"

echo "Sudoers kuralı oluşturuluyor: $SUDOERS_FILE"

cat > "$SUDOERS_FILE" << EOF
# Clary WiFi Panel yetkisi
# Web uygulamasının wifi scriptlerini ve sistem komutlarını çalıştırabilmesi için

# Kullanıcı: $WEB_USER
$WEB_USER ALL=(root) NOPASSWD: /usr/bin/install
$WEB_USER ALL=(root) NOPASSWD: /bin/install
$WEB_USER ALL=(root) NOPASSWD: /usr/bin/tee
$WEB_USER ALL=(root) NOPASSWD: /bin/tee
$WEB_USER ALL=(root) NOPASSWD: /bin/systemctl * NetworkManager*
$WEB_USER ALL=(root) NOPASSWD: /usr/bin/systemctl * NetworkManager*
$WEB_USER ALL=(root) NOPASSWD: /bin/systemctl * hostapd*
$WEB_USER ALL=(root) NOPASSWD: /usr/bin/systemctl * hostapd*
$WEB_USER ALL=(root) NOPASSWD: /bin/systemctl * dnsmasq*
$WEB_USER ALL=(root) NOPASSWD: /usr/bin/systemctl * dnsmasq*
$WEB_USER ALL=(root) NOPASSWD: /bin/systemctl * wlan0-static*
$WEB_USER ALL=(root) NOPASSWD: /usr/bin/systemctl * wlan0-static*
$WEB_USER ALL=(root) NOPASSWD: /bin/systemctl daemon-reload
$WEB_USER ALL=(root) NOPASSWD: /usr/bin/systemctl daemon-reload
$WEB_USER ALL=(root) NOPASSWD: /opt/lscope/bin/sta_mode.sh
$WEB_USER ALL=(root) NOPASSWD: /opt/lscope/bin/ap_mode.sh
$WEB_USER ALL=(root) NOPASSWD: /opt/lscope/bin/ap7_mode.sh
$WEB_USER ALL=(root) NOPASSWD: /usr/local/sbin/sta_mode.sh
$WEB_USER ALL=(root) NOPASSWD: /usr/local/sbin/ap_mode.sh
$WEB_USER ALL=(root) NOPASSWD: /usr/local/sbin/ap7_mode.sh
EOF

# Dosya izinlerini ayarla (440 - sadece root okuyabilir)
chmod 440 "$SUDOERS_FILE"

# Sudoers dosyasını doğrula
echo "Sudoers dosyası doğrulanıyor..."
if visudo -c -f "$SUDOERS_FILE"; then
    echo "✓ Sudoers dosyası başarıyla oluşturuldu ve doğrulandı"
else
    echo "✗ HATA: Sudoers dosyası hatalı! Siliniyor..."
    rm -f "$SUDOERS_FILE"
    exit 1
fi

# /etc/hostapd ve /opt/lscope/bin dizinlerinin varlığını kontrol et
echo ""
echo "Dizinler kontrol ediliyor..."
mkdir -p /etc/hostapd
mkdir -p /opt/lscope/bin
echo "✓ Gerekli dizinler hazır"

# /var/log/wifi_mode.log dosyasını oluştur ve izinlendir
echo ""
echo "Log dosyası ayarlanıyor..."
LOG_FILE="/var/log/wifi_mode.log"
touch "$LOG_FILE"
chown "$WEB_USER:$WEB_USER" "$LOG_FILE" 2>/dev/null || chown "$WEB_USER" "$LOG_FILE"
chmod 664 "$LOG_FILE"
echo "✓ Log dosyası oluşturuldu: $LOG_FILE"

# hostapd.conf dosyasının varlığını kontrol et
if [ ! -f /etc/hostapd/hostapd.conf ]; then
    echo ""
    echo "UYARI: /etc/hostapd/hostapd.conf bulunamadı!"
    echo "Örnek bir yapılandırma dosyası oluşturulsun mu? (y/n)"
    read -r response
    if [[ "$response" =~ ^[Yy]$ ]]; then
        cat > /etc/hostapd/hostapd.conf << 'HCONF'
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
rsn_pairwise=CCMP
HCONF
        chmod 644 /etc/hostapd/hostapd.conf
        echo "✓ Örnek hostapd.conf oluşturuldu"
    fi
fi

echo ""
echo "======================================"
echo "Kurulum tamamlandı!"
echo "======================================"
echo ""
echo "Web servisinizi yeniden başlatın:"
echo "  sudo systemctl restart clary-main.service"
echo ""
echo "Veya Python uygulamanızı yeniden çalıştırın."
echo ""
echo "Test için:"
echo "  sudo -u $WEB_USER sudo -n /opt/lscope/bin/sta_mode.sh"
echo ""
