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

# 3. Sudoers dosyasına izinler ekle
echo "[4/5] Sudoers izinleri kontrol ediliyor..."
SUDOERS_FILE="/etc/sudoers.d/clary-wifi"

# Mevcut kullanıcı adını al
CURRENT_USER=$(whoami)

sudo mkdir -p /etc/sudoers.d

# Sudoers dosyası oluştur veya güncelle
cat << EOF | sudo tee "$SUDOERS_FILE" > /dev/null
# Clary WiFi yönetimi için sudo izinleri
$CURRENT_USER ALL=(ALL) NOPASSWD: /bin/bash /opt/lscope/bin/ap_mode.sh
$CURRENT_USER ALL=(ALL) NOPASSWD: /bin/bash /opt/lscope/bin/ap7_mode.sh
$CURRENT_USER ALL=(ALL) NOPASSWD: /bin/bash /opt/lscope/bin/sta_mode.sh
$CURRENT_USER ALL=(ALL) NOPASSWD: /bin/systemctl restart NetworkManager
$CURRENT_USER ALL=(ALL) NOPASSWD: /bin/systemctl restart hostapd
$CURRENT_USER ALL=(ALL) NOPASSWD: /bin/systemctl restart dnsmasq
$CURRENT_USER ALL=(ALL) NOPASSWD: /usr/bin/nmcli *
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
echo "[5/5] Script dosyaları kontrol ediliyor..."
for script in /opt/lscope/bin/ap_mode.sh /opt/lscope/bin/ap7_mode.sh /opt/lscope/bin/sta_mode.sh; do
    if [ -f "$script" ]; then
        sudo chmod +x "$script"
        echo "✓ $script çalıştırılabilir yapıldı"
    else
        echo "⚠ $script bulunamadı"
    fi
done
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

