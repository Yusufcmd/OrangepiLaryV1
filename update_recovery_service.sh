#!/bin/bash
# Recovery Monitor Servis Güncelleme ve Test Scripti

echo "=========================================="
echo "RECOVERY MONITOR SERVİS GÜNCELLEME"
echo "=========================================="
echo ""

# Renk kodları
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# 1. Servisi durdur
echo -e "${YELLOW}1. Servis durduruluyor...${NC}"
sudo systemctl stop recovery-monitor.service
sleep 2
echo -e "${GREEN}✓ Servis durduruldu${NC}"
echo ""

# 2. Dosyaları kopyala
echo -e "${YELLOW}2. Güncellenmiş dosyalar kopyalanıyor...${NC}"
CLARY_DIR="/home/rise/clary"

# recovery_gpio_monitor.py
if [ -f "recovery_gpio_monitor.py" ]; then
    sudo cp recovery_gpio_monitor.py $CLARY_DIR/
    echo -e "${GREEN}✓ recovery_gpio_monitor.py kopyalandı${NC}"
else
    echo -e "${RED}✗ recovery_gpio_monitor.py bulunamadı!${NC}"
    exit 1
fi

# recovery_gpio_monitor.service
if [ -f "recovery_gpio_monitor.service" ]; then
    sudo cp recovery_gpio_monitor.service /etc/systemd/system/recovery-monitor.service
    echo -e "${GREEN}✓ recovery-monitor.service kopyalandı${NC}"
else
    echo -e "${RED}✗ recovery_gpio_monitor.service bulunamadı!${NC}"
    exit 1
fi

# test_gpio_access.py (opsiyonel)
if [ -f "test_gpio_access.py" ]; then
    sudo cp test_gpio_access.py $CLARY_DIR/
    sudo chmod +x $CLARY_DIR/test_gpio_access.py
    echo -e "${GREEN}✓ test_gpio_access.py kopyalandı${NC}"
fi

echo ""

# 3. İzinleri ayarla
echo -e "${YELLOW}3. Dosya izinleri ayarlanıyor...${NC}"
sudo chmod +x $CLARY_DIR/recovery_gpio_monitor.py
sudo chown rise:rise $CLARY_DIR/recovery_gpio_monitor.py
echo -e "${GREEN}✓ İzinler ayarlandı${NC}"
echo ""

# 4. Systemd'yi yeniden yükle
echo -e "${YELLOW}4. Systemd daemon yeniden yükleniyor...${NC}"
sudo systemctl daemon-reload
echo -e "${GREEN}✓ Daemon reload tamamlandı${NC}"
echo ""

# 5. GPIO test
echo -e "${YELLOW}5. GPIO erişim testi yapılıyor...${NC}"
echo ""
if [ -f "$CLARY_DIR/test_gpio_access.py" ]; then
    cd $CLARY_DIR
    sudo /home/rise/clary/venv/bin/python3 test_gpio_access.py
    TEST_RESULT=$?
    echo ""

    if [ $TEST_RESULT -eq 0 ]; then
        echo -e "${GREEN}✓ GPIO testi başarılı${NC}"
        echo ""

        # 6. Servisi başlat
        echo -e "${YELLOW}6. Servis başlatılıyor...${NC}"
        sudo systemctl start recovery-monitor.service
        sleep 2

        # 7. Durum kontrol
        echo ""
        echo -e "${YELLOW}7. Servis durumu kontrol ediliyor...${NC}"
        echo ""
        sudo systemctl status recovery-monitor.service --no-pager -l
        echo ""

        if sudo systemctl is-active --quiet recovery-monitor.service; then
            echo -e "${GREEN}=========================================="
            echo -e "✓ SERVİS BAŞARIYLA BAŞLATILDI"
            echo -e "==========================================${NC}"
            echo ""
            echo "Log dosyaları:"
            echo "  - Uygulama logu: /home/rise/clary/recoverylog/recovery.log"
            echo "  - Sistem logu: journalctl -u recovery-monitor.service -f"
            echo ""
            echo "Komutlar:"
            echo "  - Durumu göster: sudo systemctl status recovery-monitor.service"
            echo "  - Logları izle: sudo journalctl -u recovery-monitor.service -f"
            echo "  - Durdur: sudo systemctl stop recovery-monitor.service"
            echo "  - Başlat: sudo systemctl start recovery-monitor.service"
            echo "  - Yeniden başlat: sudo systemctl restart recovery-monitor.service"
        else
            echo -e "${RED}=========================================="
            echo -e "✗ SERVİS BAŞLATILAMADI"
            echo -e "==========================================${NC}"
            echo ""
            echo "Detaylı log için:"
            echo "  sudo journalctl -u recovery-monitor.service -n 50"
            exit 1
        fi
    else
        echo -e "${RED}✗ GPIO testi başarısız!${NC}"
        echo ""
        echo "SIGBUS hatası muhtemelen GPIO erişim sorunlarından kaynaklanıyor."
        echo "Lütfen şunları kontrol edin:"
        echo "  1. Sistemi yeniden başlatın: sudo reboot"
        echo "  2. GPIO driver'ları: dmesg | grep gpio"
        echo "  3. Kernel modülleri: lsmod | grep gpio"
        echo "  4. GPIO cihaz izinleri: ls -l /dev/gpiochip*"
        echo ""
        echo "Servis başlatılmadı."
        exit 1
    fi
else
    echo -e "${YELLOW}⚠ Test scripti bulunamadı, doğrudan servis başlatılıyor...${NC}"
    echo ""

    # 6. Servisi başlat
    echo -e "${YELLOW}6. Servis başlatılıyor...${NC}"
    sudo systemctl start recovery-monitor.service
    sleep 2

    # 7. Durum kontrol
    echo ""
    echo -e "${YELLOW}7. Servis durumu:${NC}"
    echo ""
    sudo systemctl status recovery-monitor.service --no-pager -l
fi

echo ""

