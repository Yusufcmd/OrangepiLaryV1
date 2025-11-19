#!/bin/bash
# Paylaşımlı Dosya Çözümü - Kurulum Scripti

set -e  # Hata durumunda dur

echo "=========================================="
echo "Paylaşımlı Dosya QR Okuma - Kurulum"
echo "=========================================="
echo

# Renk kodları
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Çalışma dizini
CLARY_DIR="/home/rise/clary"
VENV_DIR="$CLARY_DIR/venv"
SERVICE_FILE="/etc/systemd/system/recovery-monitor.service"
SERVICE_NAME="recovery-monitor.service"

# Root kontrolü
if [ "$EUID" -ne 0 ]; then
   echo -e "${RED}✗ Bu script root olarak çalıştırılmalı${NC}"
   echo "  Kullanım: sudo bash kurulum_paylaşımlı_dosya.sh"
   exit 1
fi

echo -e "${GREEN}✓ Root yetkisi${NC}"
echo

# 1. Sanal ortam kontrolü
echo "1. Sanal ortam kontrolü..."
if [ ! -d "$VENV_DIR" ]; then
    echo -e "${YELLOW}! Sanal ortam bulunamadı, oluşturuluyor...${NC}"
    sudo -u rise python3 -m venv "$VENV_DIR"
    echo -e "${GREEN}✓ Sanal ortam oluşturuldu${NC}"
else
    echo -e "${GREEN}✓ Sanal ortam mevcut${NC}"
fi
echo

# 2. Gerekli paketleri yükle
echo "2. Gerekli paketler kontrol ediliyor..."
sudo -u rise "$VENV_DIR/bin/pip" install --quiet --upgrade pip

PACKAGES=(numpy opencv-python gpiod)
for pkg in "${PACKAGES[@]}"; do
    echo -n "   - $pkg: "
    if sudo -u rise "$VENV_DIR/bin/python3" -c "import ${pkg//-/_}" 2>/dev/null; then
        echo -e "${GREEN}✓${NC}"
    else
        echo -e "${YELLOW}Yükleniyor...${NC}"
        sudo -u rise "$VENV_DIR/bin/pip" install --quiet "$pkg"
        echo -e "${GREEN}     ✓ Yüklendi${NC}"
    fi
done
echo

# 3. Servis dosyasını kopyala
echo "3. Servis dosyası güncelleniyor..."
if [ -f "$CLARY_DIR/$SERVICE_FILE" ]; then
    cp "$CLARY_DIR/$SERVICE_FILE" "/etc/systemd/system/$SERVICE_NAME"
    echo -e "${GREEN}✓ Servis dosyası kopyalandı${NC}"
else
    echo -e "${RED}✗ Servis dosyası bulunamadı: $CLARY_DIR/$SERVICE_FILE${NC}"
    exit 1
fi
echo

# 4. Systemd yeniden yükle
echo "4. Systemd yenileniyor..."
systemctl daemon-reload
echo -e "${GREEN}✓ Systemd yenilendi${NC}"
echo

# 5. Servisi etkinleştir ve başlat
echo "5. Servis başlatılıyor..."
systemctl enable "$SERVICE_NAME"
systemctl restart "$SERVICE_NAME"
sleep 2
echo

# 6. Durum kontrolü
echo "6. Servis durumu:"
if systemctl is-active --quiet "$SERVICE_NAME"; then
    echo -e "${GREEN}✓ Servis ÇALIŞIYOR${NC}"
    systemctl status "$SERVICE_NAME" --no-pager -l | head -15
else
    echo -e "${RED}✗ Servis ÇALIŞMIYOR${NC}"
    echo
    echo "Son 20 satır log:"
    journalctl -u "$SERVICE_NAME" -n 20 --no-pager
    exit 1
fi
echo

# 7. /tmp izinlerini kontrol et
echo "7. /tmp dizin izinleri:"
TMP_PERMS=$(stat -c "%a" /tmp)
if [ "$TMP_PERMS" = "1777" ]; then
    echo -e "${GREEN}✓ /tmp izinleri doğru (1777)${NC}"
else
    echo -e "${YELLOW}! /tmp izinleri: $TMP_PERMS (düzeltiliyor...)${NC}"
    chmod 1777 /tmp
    echo -e "${GREEN}✓ /tmp izinleri düzeltildi${NC}"
fi
echo

# 8. Test
echo "8. Paylaşımlı dosya testi:"
echo "   10 saniye bekleniyor (main.py'nin dosyayı oluşturması için)..."
sleep 10

SHARED_FILE="/tmp/clary_camera_frame.npy"
if [ -f "$SHARED_FILE" ]; then
    FILE_SIZE=$(stat -c%s "$SHARED_FILE")
    FILE_AGE=$(($(date +%s) - $(stat -c%Y "$SHARED_FILE")))
    echo -e "${GREEN}✓ Paylaşımlı dosya mevcut${NC}"
    echo "   Dosya: $SHARED_FILE"
    echo "   Boyut: $FILE_SIZE bytes"
    echo "   Yaş: $FILE_AGE saniye önce güncellendi"

    if [ $FILE_AGE -lt 5 ]; then
        echo -e "${GREEN}✓ Dosya güncel (son 5 saniye içinde)${NC}"
    else
        echo -e "${YELLOW}! Dosya eski ($FILE_AGE saniye önce)${NC}"
        echo "   main.py çalışıyor mu kontrol edin"
    fi
else
    echo -e "${YELLOW}! Paylaşımlı dosya henüz yok${NC}"
    echo "   main.py çalışıyor mu kontrol edin:"
    echo "   ps aux | grep main.py"
fi
echo

# 9. Log dosyası
echo "9. Log dosyası:"
LOG_FILE="/home/rise/clary/recoverylog/recovery.log"
if [ -f "$LOG_FILE" ]; then
    echo -e "${GREEN}✓ Log dosyası: $LOG_FILE${NC}"
    echo "   Son 5 satır:"
    tail -5 "$LOG_FILE" | sed 's/^/   /'
else
    echo -e "${YELLOW}! Log dosyası henüz oluşmadı${NC}"
fi
echo

# Özet
echo "=========================================="
echo -e "${GREEN}✓ KURULUM TAMAMLANDI${NC}"
echo "=========================================="
echo
echo "Yararlı komutlar:"
echo "  • Servis durumu     : sudo systemctl status $SERVICE_NAME"
echo "  • Log izleme        : tail -f $LOG_FILE"
echo "  • Servis yeniden başlatma : sudo systemctl restart $SERVICE_NAME"
echo "  • QR test (manuel)  : touch /tmp/clary_qr_mode.signal"
echo "  • Paylaşımlı dosya  : ls -lh $SHARED_FILE"
echo
echo "Sorun giderme:"
echo "  • Tam log          : journalctl -u $SERVICE_NAME -f"
echo "  • Servis durumunu kontrol: systemctl status $SERVICE_NAME"
echo "  • main.py kontrolü : ps aux | grep main.py"
echo

