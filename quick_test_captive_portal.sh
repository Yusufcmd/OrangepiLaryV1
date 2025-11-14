#!/usr/bin/env bash
# quick_test_captive_portal.sh
# Captive Portal sistemini hızlıca test eder

set -euo pipefail

echo "============================================================"
echo "Captive Portal Sistemi - Hızlı Test"
echo "============================================================"
echo ""

# Renk kodları
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

check_service() {
    local service=$1
    if systemctl is-active --quiet "$service"; then
        echo -e "${GREEN}✓${NC} $service: RUNNING"
        return 0
    else
        echo -e "${RED}✗${NC} $service: NOT RUNNING"
        return 1
    fi
}

check_port() {
    local port=$1
    if netstat -tlnp 2>/dev/null | grep -q ":$port "; then
        echo -e "${GREEN}✓${NC} Port $port: LISTENING"
        return 0
    else
        echo -e "${RED}✗${NC} Port $port: NOT LISTENING"
        return 1
    fi
}

check_file() {
    local file=$1
    if [ -f "$file" ]; then
        echo -e "${GREEN}✓${NC} $file: EXISTS"
        return 0
    else
        echo -e "${RED}✗${NC} $file: NOT FOUND"
        return 1
    fi
}

# 1. Dosya Kontrolleri
echo "1. Dosya Kontrolleri:"
echo "------------------------------------------------------------"
check_file "/opt/lscope/fake_internet_server.py"
check_file "/opt/lscope/bin/captive_portal_dns_config.sh"
check_file "/opt/lscope/bin/captive_portal_iptables.sh"
check_file "/etc/systemd/system/captive-portal-spoof.service"
check_file "/etc/systemd/system/captive-iptables.service"
check_file "/etc/dnsmasq.d/captive-portal.conf"
echo ""

# 2. Servis Kontrolleri
echo "2. Servis Kontrolleri:"
echo "------------------------------------------------------------"
check_service "captive-portal-spoof.service"
check_service "captive-iptables.service"
check_service "dnsmasq"
check_service "hostapd"
echo ""

# 3. Port Kontrolleri
echo "3. Port Kontrolleri:"
echo "------------------------------------------------------------"
check_port "80"   # Fake Internet Server
check_port "53"   # DNS (dnsmasq)
echo ""

# 4. Network Ayarları
echo "4. Network Ayarları:"
echo "------------------------------------------------------------"
WLAN0_IP=$(ip -4 addr show wlan0 2>/dev/null | grep -oP '(?<=inet\s)\d+(\.\d+){3}' | head -n1)
if [ -n "$WLAN0_IP" ]; then
    echo -e "${GREEN}✓${NC} wlan0 IP: $WLAN0_IP"
else
    echo -e "${RED}✗${NC} wlan0: NO IP ADDRESS"
fi

IP_FORWARD=$(cat /proc/sys/net/ipv4/ip_forward)
if [ "$IP_FORWARD" = "1" ]; then
    echo -e "${GREEN}✓${NC} IP Forwarding: ENABLED"
else
    echo -e "${YELLOW}⚠${NC} IP Forwarding: DISABLED"
fi
echo ""

# 5. iptables Kontrolleri
echo "5. iptables Kontrolleri:"
echo "------------------------------------------------------------"
if iptables -t nat -L CAPTIVE_PORTAL -n >/dev/null 2>&1; then
    RULE_COUNT=$(iptables -t nat -L CAPTIVE_PORTAL -n | grep -c "DNAT" || true)
    if [ "$RULE_COUNT" -gt 0 ]; then
        echo -e "${GREEN}✓${NC} CAPTIVE_PORTAL chain: EXISTS ($RULE_COUNT rules)"
    else
        echo -e "${YELLOW}⚠${NC} CAPTIVE_PORTAL chain: EXISTS (0 rules)"
    fi
else
    echo -e "${RED}✗${NC} CAPTIVE_PORTAL chain: NOT FOUND"
fi
echo ""

# 6. DNS Yönlendirme Testi
echo "6. DNS Yönlendirme Testi:"
echo "------------------------------------------------------------"
test_dns() {
    local domain=$1
    local result=$(nslookup "$domain" 2>/dev/null | grep "Address:" | tail -n1 | awk '{print $2}')
    if [ "$result" = "$WLAN0_IP" ]; then
        echo -e "${GREEN}✓${NC} $domain → $result"
        return 0
    else
        echo -e "${RED}✗${NC} $domain → $result (expected: $WLAN0_IP)"
        return 1
    fi
}

if [ -n "$WLAN0_IP" ]; then
    test_dns "connectivitycheck.gstatic.com"
    test_dns "captive.apple.com"
    test_dns "www.msftconnecttest.com"
else
    echo -e "${YELLOW}⚠${NC} DNS test skipped (no wlan0 IP)"
fi
echo ""

# 7. HTTP Endpoint Testi
echo "7. HTTP Endpoint Testi:"
echo "------------------------------------------------------------"
test_http() {
    local path=$1
    local expected_code=$2
    local response=$(curl -s -o /dev/null -w "%{http_code}" "http://localhost$path" 2>/dev/null)
    if [ "$response" = "$expected_code" ]; then
        echo -e "${GREEN}✓${NC} $path → HTTP $response"
        return 0
    else
        echo -e "${RED}✗${NC} $path → HTTP $response (expected: $expected_code)"
        return 1
    fi
}

if check_port "80" >/dev/null 2>&1; then
    test_http "/generate_204" "204"
    test_http "/hotspot-detect.html" "200"
    test_http "/ncsi.txt" "200"
else
    echo -e "${YELLOW}⚠${NC} HTTP test skipped (port 80 not listening)"
fi
echo ""

# 8. Özet
echo "============================================================"
echo "Test Özeti"
echo "============================================================"
echo ""

TOTAL_TESTS=15
PASSED_TESTS=0

# Basit sayım (gerçek testten geçenleri sayabiliriz)
if systemctl is-active --quiet captive-portal-spoof.service; then ((PASSED_TESTS++)); fi
if systemctl is-active --quiet captive-iptables.service; then ((PASSED_TESTS++)); fi
if systemctl is-active --quiet dnsmasq; then ((PASSED_TESTS++)); fi
if [ -f "/opt/lscope/fake_internet_server.py" ]; then ((PASSED_TESTS++)); fi
if [ -f "/etc/dnsmasq.d/captive-portal.conf" ]; then ((PASSED_TESTS++)); fi
if [ -n "$WLAN0_IP" ]; then ((PASSED_TESTS++)); fi
if [ "$IP_FORWARD" = "1" ]; then ((PASSED_TESTS++)); fi
if iptables -t nat -L CAPTIVE_PORTAL -n >/dev/null 2>&1; then ((PASSED_TESTS++)); fi

PERCENTAGE=$((PASSED_TESTS * 100 / 8))

if [ "$PERCENTAGE" -ge 90 ]; then
    echo -e "${GREEN}✓ Sistem Durumu: EXCELLENT ($PASSED_TESTS/8 test passed)${NC}"
elif [ "$PERCENTAGE" -ge 70 ]; then
    echo -e "${YELLOW}⚠ Sistem Durumu: GOOD ($PASSED_TESTS/8 test passed)${NC}"
else
    echo -e "${RED}✗ Sistem Durumu: POOR ($PASSED_TESTS/8 test passed)${NC}"
fi
echo ""

echo "Detaylı log için:"
echo "  journalctl -u captive-portal-spoof.service -n 50"
echo "  journalctl -u captive-iptables.service -n 50"
echo "  tail -f /var/log/captive_portal_setup.log"
echo ""

