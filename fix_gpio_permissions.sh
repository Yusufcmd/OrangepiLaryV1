#!/bin/bash

#############################################################################
# GPIO İzin Düzeltme Scripti
# Bu script, clary-main.service için GPIO izin sorunlarını çözer
#############################################################################

set -e

# Renkler
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}═══════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}    Clary GPIO İzin Düzeltme Scripti${NC}"
echo -e "${GREEN}═══════════════════════════════════════════════════════${NC}"
echo ""

# Root kontrolü
if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}✗ Bu scripti root olarak çalıştırmalısınız!${NC}"
    echo "  Kullanım: sudo bash $0"
    exit 1
fi

# Kullanıcıyı tespit et
WEB_USER="rise"
echo -e "${YELLOW}➤ Kullanıcı: $WEB_USER${NC}"

# 1. Kullanıcıyı gpio grubuna ekle
echo ""
echo -e "${YELLOW}[1/6] Kullanıcıyı gpio grubuna ekleme...${NC}"
if ! getent group gpio > /dev/null 2>&1; then
    echo "  gpio grubu oluşturuluyor..."
    groupadd gpio
fi

if groups $WEB_USER | grep -q gpio; then
    echo -e "  ${GREEN}✓ Kullanıcı zaten gpio grubunda${NC}"
else
    usermod -aG gpio $WEB_USER
    echo -e "  ${GREEN}✓ Kullanıcı gpio grubuna eklendi${NC}"
fi

# 2. Kullanıcıyı video grubuna ekle (kamera için)
echo ""
echo -e "${YELLOW}[2/6] Kullanıcıyı video grubuna ekleme...${NC}"
if groups $WEB_USER | grep -q video; then
    echo -e "  ${GREEN}✓ Kullanıcı zaten video grubunda${NC}"
else
    usermod -aG video $WEB_USER
    echo -e "  ${GREEN}✓ Kullanıcı video grubuna eklendi${NC}"
fi

# 3. GPIO cihazlarına izin ver
echo ""
echo -e "${YELLOW}[3/6] GPIO cihazlarına izin verme...${NC}"

# udev kuralı oluştur
UDEV_RULE_FILE="/etc/udev/rules.d/99-gpio.rules"
cat > "$UDEV_RULE_FILE" <<'EOF'
# GPIO access for gpio group
SUBSYSTEM=="gpio", GROUP="gpio", MODE="0660"
SUBSYSTEM=="gpiochip", GROUP="gpio", MODE="0660"

# GPIO character device access
SUBSYSTEM=="gpio", KERNEL=="gpiochip*", GROUP="gpio", MODE="0660"

# PWM access
SUBSYSTEM=="pwm", GROUP="gpio", MODE="0660"

# Video devices
SUBSYSTEM=="video4linux", GROUP="video", MODE="0660"
EOF

echo -e "  ${GREEN}✓ udev kuralları oluşturuldu: $UDEV_RULE_FILE${NC}"

# 4. Mevcut GPIO cihazlarına izin ver
echo ""
echo -e "${YELLOW}[4/6] Mevcut GPIO cihazlarına izin ayarlama...${NC}"

# /dev/gpiochip* cihazları
if ls /dev/gpiochip* > /dev/null 2>&1; then
    for chip in /dev/gpiochip*; do
        chgrp gpio "$chip" 2>/dev/null || true
        chmod 660 "$chip" 2>/dev/null || true
        echo "  ✓ $chip"
    done
fi

# /sys/class/gpio erişimi
if [ -d /sys/class/gpio ]; then
    chgrp -R gpio /sys/class/gpio 2>/dev/null || true
    chmod -R g+rw /sys/class/gpio 2>/dev/null || true
    echo "  ✓ /sys/class/gpio"
fi

# /sys/class/pwm erişimi
if [ -d /sys/class/pwm ]; then
    chgrp -R gpio /sys/class/pwm 2>/dev/null || true
    chmod -R g+rw /sys/class/pwm 2>/dev/null || true
    echo "  ✓ /sys/class/pwm"
fi

# Video cihazları
if ls /dev/video* > /dev/null 2>&1; then
    for video in /dev/video*; do
        chgrp video "$video" 2>/dev/null || true
        chmod 660 "$video" 2>/dev/null || true
        echo "  ✓ $video"
    done
fi

echo -e "  ${GREEN}✓ Cihaz izinleri ayarlandı${NC}"

# 5. udev kurallarını yükle
echo ""
echo -e "${YELLOW}[5/6] udev kurallarını yeniden yükleme...${NC}"
udevadm control --reload-rules
udevadm trigger
echo -e "  ${GREEN}✓ udev kuralları yüklendi${NC}"

# 6. Systemd servisini güncelle
echo ""
echo -e "${YELLOW}[6/6] Systemd servisini güncelleme...${NC}"

SERVICE_FILE="/etc/systemd/system/clary-main.service"
if [ -f "$SERVICE_FILE" ]; then
    # SupplementaryGroups ekle
    if grep -q "SupplementaryGroups=" "$SERVICE_FILE"; then
        echo -e "  ${GREEN}✓ SupplementaryGroups zaten mevcut${NC}"
    else
        # [Service] bölümüne SupplementaryGroups ekle
        sed -i '/^\[Service\]/a SupplementaryGroups=gpio video dialout' "$SERVICE_FILE"
        echo -e "  ${GREEN}✓ SupplementaryGroups eklendi${NC}"
    fi

    # PrivateTmp=false yap (gerekirse)
    if grep -q "^PrivateTmp=true" "$SERVICE_FILE"; then
        sed -i 's/^PrivateTmp=true/PrivateTmp=false/' "$SERVICE_FILE"
        echo -e "  ${GREEN}✓ PrivateTmp=false olarak ayarlandı${NC}"
    fi

    # AmbientCapabilities ekle (shutdown için)
    if ! grep -q "AmbientCapabilities=" "$SERVICE_FILE"; then
        sed -i '/^NoNewPrivileges=/d' "$SERVICE_FILE"
        sed -i '/^\[Service\]/a AmbientCapabilities=CAP_SYS_BOOT' "$SERVICE_FILE"
        echo -e "  ${GREEN}✓ AmbientCapabilities eklendi (shutdown yetkisi)${NC}"
    fi

    # Daemon reload
    systemctl daemon-reload
    echo -e "  ${GREEN}✓ Systemd daemon yenilendi${NC}"
else
    echo -e "  ${RED}✗ Servis dosyası bulunamadı: $SERVICE_FILE${NC}"
fi

# Özet
echo ""
echo -e "${GREEN}═══════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}✓ Tüm işlemler tamamlandı!${NC}"
echo -e "${GREEN}═══════════════════════════════════════════════════════${NC}"
echo ""
echo -e "${YELLOW}Yapılan değişiklikler:${NC}"
echo "  • Kullanıcı 'gpio' ve 'video' gruplarına eklendi"
echo "  • GPIO/PWM/Video cihazlarına grup izinleri verildi"
echo "  • udev kuralları oluşturuldu"
echo "  • Systemd servisi güncellendi"
echo ""
echo -e "${YELLOW}Servisi yeniden başlatın:${NC}"
echo "  sudo systemctl restart clary-main.service"
echo ""
echo -e "${YELLOW}Durumu kontrol edin:${NC}"
echo "  sudo systemctl status clary-main.service"
echo "  sudo journalctl -u clary-main.service -f"
echo ""

