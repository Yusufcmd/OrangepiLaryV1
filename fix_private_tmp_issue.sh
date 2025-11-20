#!/bin/bash
# QR Sinyal Dosyası İzin Düzeltme Scripti
# Bu script /var/run dizininde sinyal dosyası oluşturma iznini ayarlar

set -e

echo "=========================================="
echo "QR Sinyal Dosyası İzin Yapılandırması"
echo "=========================================="
echo ""

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

SIGNAL_FILE="/var/run/clary_qr_mode.signal"

echo "Sorun: systemd PrivateTmp=true nedeniyle /tmp izole edilmiş"
echo "Çözüm: Sinyal dosyası /var/run altına taşındı"
echo ""

# 1. Eski sinyal dosyasını temizle
echo "1. Eski sinyal dosyaları temizleniyor..."
sudo rm -f /tmp/clary_qr_mode.signal
sudo rm -f /var/run/clary_qr_mode.signal
echo -e "${GREEN}✓ Temizlendi${NC}"

# 2. /var/run izinlerini kontrol et
echo ""
echo "2. /var/run dizini kontrol ediliyor..."
ls -ld /var/run
echo ""

# 3. Test: rise kullanıcısı /var/run'a yazabilir mi?
echo "3. rise kullanıcısının /var/run'a yazma izni test ediliyor..."
if sudo -u rise touch /var/run/clary_test_file 2>/dev/null; then
    echo -e "${GREEN}✓ rise kullanıcısı /var/run'a yazabiliyor${NC}"
    sudo rm -f /var/run/clary_test_file
else
    echo -e "${YELLOW}⚠ rise kullanıcısı /var/run'a doğrudan yazamıyor${NC}"
    echo "  Alternatif: systemd-tmpfiles kullanılacak"
fi

# 4. systemd-tmpfiles yapılandırması oluştur
echo ""
echo "4. systemd-tmpfiles yapılandırması oluşturuluyor..."

TMPFILES_CONF="/etc/tmpfiles.d/clary-qr-signal.conf"

sudo tee "$TMPFILES_CONF" > /dev/null <<'EOF'
# Clary QR modu sinyal dosyası için izinler
# Tip  Yol                          Mod   Kullanıcı  Grup  Yaş  Argüman
d      /var/run/clary               0775  rise       rise  -    -
f      /var/run/clary_qr_mode.signal 0666 rise      rise  -    -
EOF

if [ -f "$TMPFILES_CONF" ]; then
    echo -e "${GREEN}✓ Yapılandırma oluşturuldu: $TMPFILES_CONF${NC}"
    cat "$TMPFILES_CONF"
else
    echo -e "${RED}✗ Yapılandırma oluşturulamadı${NC}"
    exit 1
fi

# 5. systemd-tmpfiles'ı çalıştır
echo ""
echo "5. systemd-tmpfiles uygulanıyor..."
sudo systemd-tmpfiles --create --prefix=/var/run/clary

if [ -e "$SIGNAL_FILE" ] || [ -d "/var/run/clary" ]; then
    echo -e "${GREEN}✓ Yapılandırma uygulandı${NC}"
    ls -la "$SIGNAL_FILE" 2>/dev/null || echo "  (Dosya henüz oluşturulmadı, oluşturulduğunda izinler otomatik ayarlanacak)"
else
    echo -e "${YELLOW}⚠ Dosya henüz oluşturulmadı ama izinler ayarlandı${NC}"
fi

# 6. Servisleri yeniden başlat
echo ""
echo "6. Servisleri yeniden başlatıyor..."
sudo systemctl restart recovery-monitor.service
sleep 2
sudo systemctl restart clary-main.service
sleep 2
echo -e "${GREEN}✓ Servisler yeniden başlatıldı${NC}"

# 7. Test
echo ""
echo "=========================================="
echo "TEST"
echo "=========================================="
echo ""

echo "Test 1: Sinyal dosyası manuel oluşturma..."
sudo -u rise bash -c "echo 'TEST' > $SIGNAL_FILE"
if [ -f "$SIGNAL_FILE" ]; then
    echo -e "${GREEN}✓ Dosya oluşturuldu${NC}"
    ls -la "$SIGNAL_FILE"
    cat "$SIGNAL_FILE"
    sudo rm -f "$SIGNAL_FILE"
else
    echo -e "${RED}✗ Dosya oluşturulamadı${NC}"
fi

echo ""
echo "Test 2: Servis durumları..."
echo "--- recovery-monitor.service ---"
if sudo systemctl is-active --quiet recovery-monitor.service; then
    echo -e "${GREEN}✓ Aktif${NC}"
else
    echo -e "${RED}✗ Aktif değil${NC}"
fi

echo ""
echo "--- clary-main.service ---"
if sudo systemctl is-active --quiet clary-main.service; then
    echo -e "${GREEN}✓ Aktif${NC}"
else
    echo -e "${RED}✗ Aktif değil${NC}"
fi

echo ""
echo "=========================================="
echo "KURULUM TAMAMLANDI"
echo "=========================================="
echo ""
echo "Değişiklikler:"
echo "  • Sinyal dosyası: /tmp/clary_qr_mode.signal → $SIGNAL_FILE"
echo "  • systemd-tmpfiles kullanılarak kalıcı izinler ayarlandı"
echo "  • Her iki servis de sinyal dosyasına erişebilir (PrivateTmp bypass)"
echo ""
echo "Logları izlemek için:"
echo "  sudo journalctl -u recovery-monitor.service -f"
echo "  sudo journalctl -u clary-main.service -f"
echo ""
echo "PWM %25 duty cycle sinyali göndererek QR modunu test edebilirsiniz."
echo ""

