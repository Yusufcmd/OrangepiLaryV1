#!/usr/bin/env bash
# Captive Portal Test Script
# Bu script captive portal kurulumunu test eder

set -e

echo "=========================================="
echo "Captive Portal Test Script"
echo "=========================================="
echo ""

# Renk kodları
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Test fonksiyonu
test_step() {
    local description="$1"
    local command="$2"

    echo -n "Testing: $description ... "
    if eval "$command" &>/dev/null; then
        echo -e "${GREEN}OK${NC}"
        return 0
    else
        echo -e "${RED}FAIL${NC}"
        return 1
    fi
}

# 1. Python3 kontrolü
test_step "Python3 yüklü mü" "command -v python3"

# 2. Flask kontrolü
test_step "Flask yüklü mü" "python3 -c 'import flask'"

# 3. Script dosyaları kontrolü
test_step "captive_portal_spoof.py var mı" "test -f captive_portal_spoof.py"
test_step "dnsmasq_ap_spoof.conf var mı" "test -f dnsmasq_ap_spoof.conf"
test_step "install_captive_portal.sh var mı" "test -f install_captive_portal.sh"

# 4. Servis dosyaları kontrolü (eğer kurulmuşsa)
if [ -f /etc/systemd/system/captive-portal-spoof.service ]; then
    echo -e "${GREEN}✓${NC} Captive portal servisi kurulu"

    # Servis durumu
    if systemctl is-active --quiet captive-portal-spoof.service; then
        echo -e "${GREEN}✓${NC} Servis çalışıyor"
    else
        echo -e "${YELLOW}⚠${NC} Servis durmuş (AP modunda değilsiniz?)"
    fi

    # Log dosyası kontrolü
    if [ -f /var/log/captive_portal_spoof.log ]; then
        echo -e "${GREEN}✓${NC} Log dosyası var"
        echo "  Son 5 satır:"
        tail -5 /var/log/captive_portal_spoof.log | sed 's/^/    /'
    fi
else
    echo -e "${YELLOW}⚠${NC} Captive portal servisi henüz kurulmamış"
    echo "  Kurmak için: sudo bash install_captive_portal.sh"
fi

# 5. dnsmasq kontrolü
if command -v dnsmasq &>/dev/null; then
    echo -e "${GREEN}✓${NC} dnsmasq yüklü"

    if [ -f /etc/dnsmasq.d/ap-spoof.conf ]; then
        echo -e "${GREEN}✓${NC} dnsmasq spoofing config kurulu"
    else
        echo -e "${YELLOW}⚠${NC} dnsmasq spoofing config kurulmamış"
    fi
else
    echo -e "${RED}✗${NC} dnsmasq yüklü değil"
fi

# 6. Port 80 kontrolü
if command -v netstat &>/dev/null || command -v ss &>/dev/null; then
    if ss -tuln 2>/dev/null | grep -q ':80 ' || netstat -tuln 2>/dev/null | grep -q ':80 '; then
        echo -e "${GREEN}✓${NC} Port 80 dinleniyor"
    else
        echo -e "${YELLOW}⚠${NC} Port 80 dinlenmiyor (AP modunda değilsiniz?)"
    fi
fi

# 7. Connectivity check test (eğer servis çalışıyorsa)
if systemctl is-active --quiet captive-portal-spoof.service 2>/dev/null; then
    echo ""
    echo "Connectivity check endpoint testleri:"

    # Test Android endpoint
    if curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1/generate_204 | grep -q "204"; then
        echo -e "${GREEN}✓${NC} Android endpoint (/generate_204) -> 204"
    else
        echo -e "${RED}✗${NC} Android endpoint başarısız"
    fi

    # Test Windows endpoint
    if curl -s http://127.0.0.1/ncsi.txt | grep -q "Microsoft"; then
        echo -e "${GREEN}✓${NC} Windows endpoint (/ncsi.txt) -> OK"
    else
        echo -e "${RED}✗${NC} Windows endpoint başarısız"
    fi

    # Test Apple endpoint
    if curl -s http://127.0.0.1/hotspot-detect.html | grep -q "Success"; then
        echo -e "${GREEN}✓${NC} Apple endpoint (/hotspot-detect.html) -> OK"
    else
        echo -e "${RED}✗${NC} Apple endpoint başarısız"
    fi
fi

echo ""
echo "=========================================="
echo "Test tamamlandı!"
echo "=========================================="
echo ""
echo "Kurulum için: sudo bash install_captive_portal.sh"
echo "Manuel başlatma: sudo systemctl start captive-portal-spoof.service"
echo "Log görüntüleme: sudo tail -f /var/log/captive_portal_spoof.log"
echo ""

