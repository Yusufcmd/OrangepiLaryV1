#!/bin/bash
# Orange Pi izin düzeltme scripti
# Bu script, wifi_change.py ve ilgili servislerin /etc/hostapd/hostapd.conf
# dosyasına yazabilmesi için gerekli izinleri ayarlar.

set -e

echo "=== Orange Pi İzin Düzeltme Scripti ==="
echo ""

# Root kontrolü
if [ "$EUID" -ne 0 ]; then
   echo "HATA: Bu script root olarak çalıştırılmalı!"
   echo "Kullanım: sudo bash fix_permissions.sh"
   exit 1
fi

echo "[1/5] /etc/hostapd klasörü oluşturuluyor..."
mkdir -p /etc/hostapd
chmod 755 /etc/hostapd

echo "[2/5] hostapd.conf dosyası kontrol ediliyor..."
if [ ! -f /etc/hostapd/hostapd.conf ]; then
    echo "  hostapd.conf bulunamadı, varsayılan yapılandırma oluşturuluyor..."
    cat > /etc/hostapd/hostapd.conf <<'EOF'
interface=wlan0
driver=nl80211
ssid=RTCLARY20052
hw_mode=g
channel=6
ieee80211n=1
wmm_enabled=1
auth_algs=1
ignore_broadcast_ssid=0
wpa=2
wpa_passphrase=simclever12345
wpa_key_mgmt=WPA-PSK
rsn_pairwise=CCMP
EOF
else
    echo "  hostapd.conf mevcut"
fi

echo "[3/5] hostapd.conf dosya izinleri ayarlanıyor..."
chmod 644 /etc/hostapd/hostapd.conf
chown root:root /etc/hostapd/hostapd.conf

echo "[4/5] rise kullanıcısı için sudoers kuralı ekleniyor..."
SUDOERS_FILE="/etc/sudoers.d/clary-wifi"

cat > "$SUDOERS_FILE" <<'EOF'
# Clary projesi için gerekli sudo yetkileri (rise kullanıcısı)
# WiFi yönetimi ve servis kontrolü için

rise ALL=(ALL) NOPASSWD: /usr/bin/systemctl start hostapd
rise ALL=(ALL) NOPASSWD: /usr/bin/systemctl stop hostapd
rise ALL=(ALL) NOPASSWD: /usr/bin/systemctl restart hostapd
rise ALL=(ALL) NOPASSWD: /usr/bin/systemctl enable hostapd
rise ALL=(ALL) NOPASSWD: /usr/bin/systemctl disable hostapd
rise ALL=(ALL) NOPASSWD: /usr/bin/systemctl start dnsmasq
rise ALL=(ALL) NOPASSWD: /usr/bin/systemctl stop dnsmasq
rise ALL=(ALL) NOPASSWD: /usr/bin/systemctl restart dnsmasq
rise ALL=(ALL) NOPASSWD: /usr/bin/systemctl enable dnsmasq
rise ALL=(ALL) NOPASSWD: /usr/bin/systemctl disable dnsmasq
rise ALL=(ALL) NOPASSWD: /usr/bin/systemctl start NetworkManager
rise ALL=(ALL) NOPASSWD: /usr/bin/systemctl stop NetworkManager
rise ALL=(ALL) NOPASSWD: /usr/bin/systemctl restart NetworkManager
rise ALL=(ALL) NOPASSWD: /usr/bin/systemctl enable NetworkManager
rise ALL=(ALL) NOPASSWD: /usr/bin/systemctl disable NetworkManager
rise ALL=(ALL) NOPASSWD: /usr/bin/systemctl start wlan0-static.service
rise ALL=(ALL) NOPASSWD: /usr/bin/systemctl stop wlan0-static.service
rise ALL=(ALL) NOPASSWD: /usr/bin/systemctl restart wlan0-static.service
rise ALL=(ALL) NOPASSWD: /usr/bin/systemctl enable wlan0-static.service
rise ALL=(ALL) NOPASSWD: /usr/bin/systemctl disable wlan0-static.service
rise ALL=(ALL) NOPASSWD: /usr/bin/systemctl daemon-reload
rise ALL=(ALL) NOPASSWD: /usr/bin/systemctl unmask hostapd
rise ALL=(ALL) NOPASSWD: /usr/sbin/rfkill unblock wifi
rise ALL=(ALL) NOPASSWD: /usr/bin/nmcli *
rise ALL=(ALL) NOPASSWD: /usr/sbin/ip *
rise ALL=(ALL) NOPASSWD: /usr/bin/install -D -m * * /etc/hostapd/hostapd.conf
rise ALL=(ALL) NOPASSWD: /usr/bin/install -D -m * * /etc/default/hostapd
rise ALL=(ALL) NOPASSWD: /usr/bin/install -D -m * * /etc/dnsmasq.d/ap.conf
rise ALL=(ALL) NOPASSWD: /usr/bin/install -d -m * /opt/lscope/bin
rise ALL=(ALL) NOPASSWD: /usr/bin/install -D -m * * /opt/lscope/bin/*
rise ALL=(ALL) NOPASSWD: /usr/bin/cp * /etc/hostapd/hostapd.conf
rise ALL=(ALL) NOPASSWD: /usr/bin/cp * /etc/default/hostapd
rise ALL=(ALL) NOPASSWD: /opt/lscope/bin/sta_mode.sh
rise ALL=(ALL) NOPASSWD: /opt/lscope/bin/ap_mode.sh
rise ALL=(ALL) NOPASSWD: /opt/lscope/bin/ap7_mode.sh
rise ALL=(ALL) NOPASSWD: /usr/local/sbin/sta_mode.sh
rise ALL=(ALL) NOPASSWD: /usr/local/sbin/ap_mode.sh
rise ALL=(ALL) NOPASSWD: /usr/local/sbin/ap7_mode.sh
rise ALL=(ALL) NOPASSWD: /usr/bin/sed -i * /etc/hostapd/hostapd.conf
rise ALL=(ALL) NOPASSWD: /usr/bin/sed -i * /etc/default/hostapd
rise ALL=(ALL) NOPASSWD: /usr/bin/sed -i * /etc/NetworkManager/conf.d/unmanaged.conf
rise ALL=(ALL) NOPASSWD: /usr/bin/mkdir -p /etc/NetworkManager/conf.d
rise ALL=(ALL) NOPASSWD: /usr/bin/rm -f /etc/NetworkManager/conf.d/unmanaged.conf
EOF

chmod 440 "$SUDOERS_FILE"
chown root:root "$SUDOERS_FILE"

# Sudoers dosyasını kontrol et
if visudo -c -f "$SUDOERS_FILE" > /dev/null 2>&1; then
    echo "  ✓ Sudoers kuralı başarıyla eklendi: $SUDOERS_FILE"
else
    echo "  HATA: Sudoers dosyası geçersiz! Siliniyor..."
    rm -f "$SUDOERS_FILE"
    exit 1
fi

echo "[5/5] /opt/lscope/bin klasörü ve izinleri ayarlanıyor..."
mkdir -p /opt/lscope/bin
chmod 755 /opt/lscope/bin

# Eğer script dosyaları varsa izinlerini düzelt
for script in /opt/lscope/bin/*.sh; do
    if [ -f "$script" ]; then
        chmod 755 "$script"
        echo "  ✓ $script çalıştırılabilir yapıldı"
    fi
done

echo ""
echo "=== Tamamlandı! ==="
echo ""
echo "Yapılan değişiklikler:"
echo "  ✓ /etc/hostapd klasörü ve hostapd.conf oluşturuldu"
echo "  ✓ hostapd.conf izinleri ayarlandı (644)"
echo "  ✓ rise kullanıcısı için sudoers kuralları eklendi"
echo "  ✓ /opt/lscope/bin klasörü hazır"
echo ""
echo "Artık AP moduna geçebilirsiniz!"
echo "Test için: sudo -u rise sudo -n systemctl status hostapd"
echo ""

