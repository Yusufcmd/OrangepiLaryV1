#!/bin/bash
# Orange Pi'de Çalıştırılacak Terminal Komutları
# WiFi NoNewPrivileges Hatası Çözümü

echo "=========================================="
echo "WiFi İzin Sorunları Düzeltme Komutları"
echo "=========================================="
echo ""
echo "Bu dosyayı Orange Pi cihazında çalıştırın."
echo ""

# Renk kodları
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${YELLOW}ÖNEMLİ: Bu komutları Orange Pi cihazında sırayla çalıştırın${NC}"
echo ""

# ==================== ADIM 1: Dosyaları Kopyala ====================
echo -e "${GREEN}[ADIM 1] Güncellenmiş dosyaları Orange Pi'ye kopyalayın${NC}"
echo ""
echo "Windows/Geliştirme bilgisayarında:"
echo "  cd C:\\Users\\RISE-ARGE-4\\PycharmProjects\\OrangepiLaryV1"
echo "  scp main.py recovery_gpio_monitor.py orangepi:/home/rise/clary/"
echo "  scp opt/lscope/bin/*.sh orangepi:/tmp/wifi_scripts/"
echo "  scp fix_wifi_permissions.sh test_qr_wifi.sh orangepi:/tmp/"
echo ""
echo "Orange Pi'de:"
echo "  mkdir -p /tmp/wifi_scripts"
echo "  sudo cp /tmp/wifi_scripts/*.sh /opt/lscope/bin/"
echo "  sudo chmod +x /opt/lscope/bin/*.sh"
echo ""
read -p "Bu adımı tamamladınız mı? (e/h): " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Ee]$ ]]; then
    echo "Önce dosyaları kopyalayın, sonra devam edin."
    exit 1
fi

# ==================== ADIM 2: İzinleri Düzelt ====================
echo ""
echo -e "${GREEN}[ADIM 2] GPIO ve WiFi izinlerini otomatik düzelt${NC}"
echo ""
echo "Komutlar:"
echo "  cd /home/rise/clary"
echo "  chmod +x fix_gpio_permissions.sh"
echo "  sudo bash fix_gpio_permissions.sh"
echo ""
read -p "Şimdi çalıştırılsın mı? (e/h): " -n 1 -r
echo
if [[ $REPLY =~ ^[Ee]$ ]]; then
    cd /home/rise/clary
    chmod +x fix_gpio_permissions.sh 2>/dev/null || true
    sudo bash fix_gpio_permissions.sh
fi

# ==================== ADIM 3: Ana Kodu Güncelle ====================
echo ""
echo -e "${GREEN}[ADIM 3] Ana uygulama kodunu güncelle${NC}"
echo ""
echo "Komut:"
echo "  sudo systemctl stop clary-main.service"
echo "  sudo cp /home/rise/clary/main.py /home/rise/clary/main.py.backup"
echo "  sudo cp /tmp/main.py /home/rise/clary/main.py"
echo "  sudo cp /tmp/recovery_gpio_monitor.py /home/rise/clary/"
echo ""
read -p "Şimdi çalıştırılsın mı? (e/h): " -n 1 -r
echo
if [[ $REPLY =~ ^[Ee]$ ]]; then
    sudo systemctl stop clary-main.service
    sudo cp /home/rise/clary/main.py /home/rise/clary/main.py.backup 2>/dev/null || true
    sudo cp /tmp/main.py /home/rise/clary/main.py 2>/dev/null || true
    sudo cp /tmp/recovery_gpio_monitor.py /home/rise/clary/ 2>/dev/null || true
    echo "✓ Dosyalar güncellendi"
fi

# ==================== ADIM 4: Servisi Yeniden Başlat ====================
echo ""
echo -e "${GREEN}[ADIM 4] Servisi yeniden başlat${NC}"
echo ""
echo "Komutlar:"
echo "  sudo systemctl daemon-reload"
echo "  sudo systemctl restart clary-main.service"
echo "  sudo systemctl status clary-main.service"
echo ""
read -p "Şimdi çalıştırılsın mı? (e/h): " -n 1 -r
echo
if [[ $REPLY =~ ^[Ee]$ ]]; then
    sudo systemctl daemon-reload
    sudo systemctl restart clary-main.service
    echo ""
    echo "Servis durumu:"
    sudo systemctl status clary-main.service --no-pager -l
fi

# ==================== ADIM 5: Test ====================
echo ""
echo -e "${GREEN}[ADIM 5] Sistemi test et${NC}"
echo ""
echo "Komut:"
echo "  cd /tmp"
echo "  chmod +x test_qr_wifi.sh"
echo "  ./test_qr_wifi.sh"
echo ""
read -p "Şimdi çalıştırılsın mı? (e/h): " -n 1 -r
echo
if [[ $REPLY =~ ^[Ee]$ ]]; then
    cd /tmp
    chmod +x test_qr_wifi.sh 2>/dev/null || true
    ./test_qr_wifi.sh
fi

# ==================== ADIM 6: Grup Değişikliği İçin Yeniden Giriş ====================
echo ""
echo -e "${YELLOW}[ÖNEMLİ] Kullanıcı gpio grubuna eklendi${NC}"
echo ""
echo "Grup değişikliğinin etkili olması için:"
echo "  1. SSH bağlantınızı kapatın"
echo "  2. Yeniden SSH ile bağlanın"
echo "  3. groups komutunu çalıştırıp 'gpio' grubunu görüp görmediğinizi kontrol edin"
echo ""
echo "Veya servisi root olarak çalıştırıyorsanız bu gerekli değil."
echo ""

# ==================== ADIM 7: Logları İzle ====================
echo ""
echo -e "${GREEN}[ADIM 7] Logları canlı izleyin${NC}"
echo ""
echo "Komut:"
echo "  sudo journalctl -u clary-main.service -f"
echo ""
echo "QR kod testi için:"
echo "  1. Recovery GPIO monitöründen %25 PWM gönderin"
echo "  2. QR kod gösterin (APMODE5gch36 veya STAMODE:SSID:PASSWORD)"
echo "  3. Logları izleyip WiFi değişimini gözlemleyin"
echo ""
read -p "Logları şimdi izlemek ister misiniz? (e/h): " -n 1 -r
echo
if [[ $REPLY =~ ^[Ee]$ ]]; then
    echo ""
    echo "Loglar açılıyor... (Çıkmak için Ctrl+C)"
    sleep 2
    sudo journalctl -u clary-main.service -f
fi

echo ""
echo "=========================================="
echo "✓ Tüm adımlar tamamlandı!"
echo "=========================================="

