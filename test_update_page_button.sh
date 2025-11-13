#!/usr/bin/env bash
# Test: Güncelleme sayfası captive portal butonu

echo "=========================================="
echo "Güncelleme Sayfası Test"
echo "=========================================="
echo ""

# Renk kodları
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}1. Dosya kontrolleri...${NC}"

# templates/update.html kontrolü
if [ -f "templates/update.html" ]; then
    echo -e "${GREEN}✓${NC} update.html bulundu"

    # Captive Portal butonu var mı?
    if grep -q "captiveBtn" templates/update.html; then
        echo -e "${GREEN}✓${NC} Captive Portal butonu eklendi"
    else
        echo -e "${RED}✗${NC} Captive Portal butonu bulunamadı"
    fi

    # installCaptivePortal fonksiyonu var mı?
    if grep -q "installCaptivePortal" templates/update.html; then
        echo -e "${GREEN}✓${NC} JavaScript fonksiyonu eklendi"
    else
        echo -e "${RED}✗${NC} JavaScript fonksiyonu bulunamadı"
    fi
else
    echo -e "${RED}✗${NC} update.html bulunamadı"
fi

echo ""
echo -e "${BLUE}2. Backend endpoint kontrolleri...${NC}"

# main.py kontrolü
if [ -f "main.py" ]; then
    echo -e "${GREEN}✓${NC} main.py bulundu"

    # install_captive_portal endpoint'i var mı?
    if grep -q "/install_captive_portal" main.py; then
        echo -e "${GREEN}✓${NC} /install_captive_portal endpoint'i eklendi"
    else
        echo -e "${RED}✗${NC} Endpoint bulunamadı"
    fi

    # apt-get update komutu var mı?
    if grep -q "apt-get update" main.py; then
        echo -e "${GREEN}✓${NC} apt-get update komutu eklendi"
    else
        echo -e "${RED}✗${NC} apt-get update komutu bulunamadı"
    fi

    # Flask kurulum komutu var mı?
    if grep -q "python3-flask" main.py; then
        echo -e "${GREEN}✓${NC} Flask kurulum komutu eklendi"
    else
        echo -e "${RED}✗${NC} Flask kurulum komutu bulunamadı"
    fi

    # install_captive_portal.sh çağrısı var mı?
    if grep -q "install_captive_portal.sh" main.py; then
        echo -e "${GREEN}✓${NC} Kurulum script çağrısı eklendi"
    else
        echo -e "${RED}✗${NC} Kurulum script çağrısı bulunamadı"
    fi

    # systemctl start komutu var mı?
    if grep -q "systemctl start captive-portal-spoof" main.py; then
        echo -e "${GREEN}✓${NC} Servis başlatma komutu eklendi"
    else
        echo -e "${RED}✗${NC} Servis başlatma komutu bulunamadı"
    fi

    # systemctl enable komutu var mı?
    if grep -q "systemctl enable captive-portal-spoof" main.py; then
        echo -e "${GREEN}✓${NC} Otomatik başlatma komutu eklendi"
    else
        echo -e "${RED}✗${NC} Otomatik başlatma komutu bulunamadı"
    fi
else
    echo -e "${RED}✗${NC} main.py bulunamadı"
fi

echo ""
echo -e "${BLUE}3. Kurulum dosyaları kontrolleri...${NC}"

# install_captive_portal.sh kontrolü
if [ -f "install_captive_portal.sh" ]; then
    echo -e "${GREEN}✓${NC} install_captive_portal.sh bulundu"
else
    echo -e "${YELLOW}⚠${NC} install_captive_portal.sh bulunamadı"
fi

# captive_portal_spoof.py kontrolü
if [ -f "captive_portal_spoof.py" ]; then
    echo -e "${GREEN}✓${NC} captive_portal_spoof.py bulundu"
else
    echo -e "${YELLOW}⚠${NC} captive_portal_spoof.py bulunamadı"
fi

# dnsmasq_ap_spoof.conf kontrolü
if [ -f "dnsmasq_ap_spoof.conf" ]; then
    echo -e "${GREEN}✓${NC} dnsmasq_ap_spoof.conf bulundu"
else
    echo -e "${YELLOW}⚠${NC} dnsmasq_ap_spoof.conf bulunamadı"
fi

echo ""
echo "=========================================="
echo "Test Tamamlandı!"
echo "=========================================="
echo ""
echo -e "${BLUE}Kullanım:${NC}"
echo "1. Web arayüzüne giriş yapın"
echo "2. Güncelleme sayfasına gidin"
echo "3. '🛡️ Captive Portal Kur' butonuna tıklayın"
echo ""
echo -e "${BLUE}Manuel test:${NC}"
echo "curl -X POST http://localhost:5000/install_captive_portal \\"
echo "  -H 'Content-Type: application/json' \\"
echo "  -H 'Cookie: session=...' \\"
echo "  -d '{}'"
echo ""

