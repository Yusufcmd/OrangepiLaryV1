#!/bin/bash
# NoNewPrivileges ve WiFi script izinleri düzeltme scripti

set -euo pipefail

echo "=========================================="
echo "WiFi İzinleri Düzeltme Scripti"
echo "=========================================="
echo ""

# 1. Log dosyası izinlerini düzelt
echo "[1/5] Log dosyası izinleri düzenleniyor..."
sudo touch /var/log/wifi_mode.log
sudo chmod 666 /var/log/wifi_mode.log
echo "✓ /var/log/wifi_mode.log düzenlendi"
echo ""

# 2. Systemd servis dosyasını bul ve düzenle
echo "[2/5] Systemd servis dosyası aranıyor..."
SERVICE_FILE=$(systemctl show -p FragmentPath clary-main.service 2>/dev/null | cut -d= -f2)

if [ -z "$SERVICE_FILE" ] || [ "$SERVICE_FILE" == "" ]; then
    echo "⚠ clary-main.service bulunamadı. Manuel olarak servis dosyasını düzenlemeniz gerekiyor."
    echo ""
    echo "Servis dosyasını bulmak için:"
    echo "  systemctl status clary-main.service"
    echo ""
    echo "Servis dosyasına eklenecek satırlar:"
    echo "  [Service]"
    echo "  NoNewPrivileges=false"
    echo ""
else
    echo "✓ Servis dosyası bulundu: $SERVICE_FILE"

    # NoNewPrivileges ayarını kontrol et
    if grep -q "NoNewPrivileges" "$SERVICE_FILE"; then
        echo "  Mevcut NoNewPrivileges ayarı düzenleniyor..."
        sudo sed -i 's/NoNewPrivileges=.*/NoNewPrivileges=false/' "$SERVICE_FILE"
    else
        echo "  NoNewPrivileges=false ekleniyor..."
        # [Service] bölümüne ekle
        sudo sed -i '/\[Service\]/a NoNewPrivileges=false' "$SERVICE_FILE"
    fi

    echo "✓ Servis dosyası güncellendi"
    echo ""

    # Systemd'yi yenile
    echo "[3/5] Systemd yapılandırması yenileniyor..."
    sudo systemctl daemon-reload
    echo "✓ Systemd yapılandırması yenilendi"
    echo ""
fi

# 3. Kullanıcı gruplarını kontrol et
echo "[3/5] Kullanıcı grupları kontrol ediliyor..."
CURRENT_USER=$(whoami)

# GPIO erişimi için gpio grubu
if getent group gpio > /dev/null 2>&1; then
    if ! groups $CURRENT_USER | grep -q gpio; then
        echo "  $CURRENT_USER kullanıcısı gpio grubuna ekleniyor..."
        sudo usermod -a -G gpio $CURRENT_USER
        echo "✓ gpio grubuna eklendi (yeniden giriş yapın)"
    else
        echo "✓ $CURRENT_USER zaten gpio grubunda"
    fi
else
    echo "⚠ gpio grubu bulunamadı, oluşturuluyor..."
    sudo groupadd -f gpio
    sudo usermod -a -G gpio $CURRENT_USER
    echo "✓ gpio grubu oluşturuldu ve kullanıcı eklendi"
fi

# GPIO cihaz dosyalarına grup izni
for gpiochip in /dev/gpiochip*; do
    if [ -e "$gpiochip" ]; then
        sudo chgrp gpio "$gpiochip" || true
        sudo chmod g+rw "$gpiochip" || true
        echo "✓ $gpiochip izinleri düzenlendi"
    fi
done
echo ""

# 4. Sudoers dosyasına izinler ekle
echo "[4/5] Sudoers izinleri kontrol ediliyor..."
SUDOERS_FILE="/etc/sudoers.d/clary-wifi"

sudo mkdir -p /etc/sudoers.d

# Sudoers dosyası oluştur veya güncelle
cat << EOF | sudo tee "$SUDOERS_FILE" > /dev/null
# Clary WiFi yönetimi için sudo izinleri
$CURRENT_USER ALL=(ALL) NOPASSWD: /bin/bash /opt/lscope/bin/ap_mode.sh
$CURRENT_USER ALL=(ALL) NOPASSWD: /bin/bash /opt/lscope/bin/ap7_mode.sh
$CURRENT_USER ALL=(ALL) NOPASSWD: /bin/bash /opt/lscope/bin/sta_mode.sh
$CURRENT_USER ALL=(ALL) NOPASSWD: /bin/systemctl restart NetworkManager
$CURRENT_USER ALL=(ALL) NOPASSWD: /bin/systemctl stop NetworkManager
$CURRENT_USER ALL=(ALL) NOPASSWD: /bin/systemctl start NetworkManager
$CURRENT_USER ALL=(ALL) NOPASSWD: /bin/systemctl restart hostapd
$CURRENT_USER ALL=(ALL) NOPASSWD: /bin/systemctl enable hostapd
$CURRENT_USER ALL=(ALL) NOPASSWD: /bin/systemctl disable hostapd
$CURRENT_USER ALL=(ALL) NOPASSWD: /bin/systemctl restart dnsmasq
$CURRENT_USER ALL=(ALL) NOPASSWD: /bin/systemctl enable dnsmasq
$CURRENT_USER ALL=(ALL) NOPASSWD: /bin/systemctl disable dnsmasq
$CURRENT_USER ALL=(ALL) NOPASSWD: /bin/systemctl unmask hostapd
$CURRENT_USER ALL=(ALL) NOPASSWD: /bin/systemctl daemon-reload
$CURRENT_USER ALL=(ALL) NOPASSWD: /bin/systemctl restart wlan0-static.service
$CURRENT_USER ALL=(ALL) NOPASSWD: /bin/systemctl enable wlan0-static.service
$CURRENT_USER ALL=(ALL) NOPASSWD: /bin/systemctl restart captive-portal-spoof.service
$CURRENT_USER ALL=(ALL) NOPASSWD: /bin/systemctl enable captive-portal-spoof.service
$CURRENT_USER ALL=(ALL) NOPASSWD: /bin/systemctl restart captive-iptables.service
$CURRENT_USER ALL=(ALL) NOPASSWD: /bin/systemctl enable captive-iptables.service
$CURRENT_USER ALL=(ALL) NOPASSWD: /usr/bin/nmcli *
$CURRENT_USER ALL=(ALL) NOPASSWD: /bin/rm -f /var/run/clary_qr_mode.signal
$CURRENT_USER ALL=(ALL) NOPASSWD: /bin/mkdir -p /etc/NetworkManager/conf.d
$CURRENT_USER ALL=(ALL) NOPASSWD: /usr/bin/tee /etc/NetworkManager/conf.d/unmanaged.conf
$CURRENT_USER ALL=(ALL) NOPASSWD: /usr/bin/tee /etc/default/hostapd
$CURRENT_USER ALL=(ALL) NOPASSWD: /usr/bin/sed *
EOF

sudo chmod 0440 "$SUDOERS_FILE"

# Sudoers dosyasını doğrula
if sudo visudo -c -f "$SUDOERS_FILE"; then
    echo "✓ Sudoers dosyası başarıyla oluşturuldu: $SUDOERS_FILE"
else
    echo "✗ HATA: Sudoers dosyası geçersiz, siliniyor..."
    sudo rm -f "$SUDOERS_FILE"
    exit 1
fi
echo ""

# 4. Script dosyalarını kontrol et
echo "[5/7] Script dosyaları kontrol ediliyor..."
for script in /opt/lscope/bin/ap_mode.sh /opt/lscope/bin/ap7_mode.sh /opt/lscope/bin/sta_mode.sh; do
    if [ -f "$script" ]; then
        sudo chmod +x "$script"
        echo "✓ $script çalıştırılabilir yapıldı"
    else
        echo "⚠ $script bulunamadı"
    fi
done
echo ""

# 6. GPIO udev kuralları
echo "[6/7] GPIO udev kuralları kontrol ediliyor..."
UDEV_RULES_FILE="/etc/udev/rules.d/90-gpio.rules"

if [ ! -f "$UDEV_RULES_FILE" ]; then
    echo "  GPIO udev kuralları oluşturuluyor..."
    cat << EOF | sudo tee "$UDEV_RULES_FILE" > /dev/null
# GPIO cihazlarına gpio grubu için erişim izni
SUBSYSTEM=="gpio", KERNEL=="gpiochip*", GROUP="gpio", MODE="0660"
SUBSYSTEM=="gpio", KERNEL=="gpio*", GROUP="gpio", MODE="0660"
EOF

    echo "✓ udev kuralları oluşturuldu: $UDEV_RULES_FILE"

    # udev kurallarını yeniden yükle
    echo "  udev kuralları yeniden yükleniyor..."
    sudo udevadm control --reload-rules
    sudo udevadm trigger
    echo "✓ udev kuralları yeniden yüklendi"
else
    echo "✓ GPIO udev kuralları zaten mevcut"
fi
echo ""

# 7. QR sinyal dosyası dizini
echo "[7/7] QR sinyal dosyası dizini kontrol ediliyor..."
sudo mkdir -p /var/run
sudo chmod 1777 /var/run  # Sticky bit ile herkes yazabilir
sudo touch /var/run/clary_qr_mode.signal 2>/dev/null || true
sudo chmod 666 /var/run/clary_qr_mode.signal 2>/dev/null || true
echo "✓ /var/run izinleri düzenlendi"
echo ""

echo "=========================================="
echo "✓ Tüm izinler başarıyla düzenlendi!"
echo "=========================================="
echo ""
echo "Değişikliklerin etkili olması için servisi yeniden başlatın:"
echo "  sudo systemctl restart clary-main.service"
echo ""
echo "Servis durumunu kontrol etmek için:"
echo "  sudo systemctl status clary-main.service"
echo ""

