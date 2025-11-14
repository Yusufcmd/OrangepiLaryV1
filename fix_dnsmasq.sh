#!/usr/bin/env bash
################################################################################
# DNSMASQ TROUBLESHOOTING SCRIPT
# Orange Pi - DNSmasq Sorun Giderme ve Kurulum Kontrol
#
# Bu script dnsmasq sorunlarını tespit eder ve çözer
#
# Kullanım: sudo bash fix_dnsmasq.sh
################################################################################

set -uo pipefail

# Renkli çıktı
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

print_header() {
    echo -e "${BLUE}╔══════════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${BLUE}║${NC}  $1"
    echo -e "${BLUE}╚══════════════════════════════════════════════════════════════════╝${NC}"
}

print_step() { echo -e "${BLUE}▶${NC} $1"; }
print_success() { echo -e "${GREEN}✓${NC} $1"; }
print_error() { echo -e "${RED}✗${NC} $1"; }
print_warning() { echo -e "${YELLOW}⚠${NC} $1"; }

# Root kontrolü
if [ "$EUID" -ne 0 ]; then
    print_error "Bu script root olarak çalıştırılmalıdır!"
    echo "Kullanım: sudo bash $0"
    exit 1
fi

print_header "DNSmasq Sorun Giderme - Başlatılıyor"
echo ""

################################################################################
# 1. DNSmasq Kurulum Kontrolü
################################################################################
print_step "1. DNSmasq kurulum durumu kontrol ediliyor..."

if command -v dnsmasq &> /dev/null; then
    DNSMASQ_VERSION=$(dnsmasq --version | head -n1)
    print_success "dnsmasq kurulu: $DNSMASQ_VERSION"
else
    print_error "dnsmasq kurulu değil!"
    echo ""
    print_step "dnsmasq kuruluyor..."
    apt update
    apt install -y dnsmasq
    print_success "dnsmasq kuruldu"
fi
echo ""

################################################################################
# 2. Servis Durumu Kontrolü
################################################################################
print_step "2. DNSmasq servis durumu kontrol ediliyor..."

if systemctl is-active --quiet dnsmasq; then
    print_success "dnsmasq servisi çalışıyor"
else
    print_warning "dnsmasq servisi çalışmıyor"

    # Servisin enable olup olmadığını kontrol et
    if systemctl is-enabled --quiet dnsmasq; then
        print_success "dnsmasq boot'ta başlayacak (enabled)"
    else
        print_warning "dnsmasq boot'ta başlamayacak (disabled)"
        print_step "dnsmasq enable ediliyor..."
        systemctl enable dnsmasq
        print_success "dnsmasq enable edildi"
    fi
fi
echo ""

################################################################################
# 3. Port 53 Kullanım Kontrolü
################################################################################
print_step "3. Port 53 kullanımı kontrol ediliyor..."

if netstat -tulpn 2>/dev/null | grep -q ":53 " || ss -tulpn 2>/dev/null | grep -q ":53 "; then
    print_warning "Port 53 kullanımda:"
    netstat -tulpn 2>/dev/null | grep ":53 " || ss -tulpn 2>/dev/null | grep ":53 " || true
    echo ""

    # systemd-resolved kontrolü (Ubuntu/Debian'da port 53'ü kullanır)
    if systemctl is-active --quiet systemd-resolved; then
        print_warning "systemd-resolved port 53'ü kullanıyor"
        print_step "systemd-resolved devre dışı bırakılıyor..."

        # DNSStubListener'ı devre dışı bırak
        mkdir -p /etc/systemd/resolved.conf.d/
        cat > /etc/systemd/resolved.conf.d/disable-stub.conf <<EOF
[Resolve]
DNSStubListener=no
EOF

        systemctl restart systemd-resolved
        print_success "systemd-resolved yapılandırıldı"
    fi
else
    print_success "Port 53 kullanılabilir"
fi
echo ""

################################################################################
# 4. Network Interface Kontrolü
################################################################################
print_step "4. Network interface'leri kontrol ediliyor..."

echo "Aktif interface'ler:"
for IFACE in wlan0 wlan1 ap0 eth0; do
    IP=$(ip -4 addr show "$IFACE" 2>/dev/null | grep -oP '(?<=inet\s)\d+(\.\d+){3}' | head -n1 || true)
    if [ -n "$IP" ]; then
        print_success "$IFACE: $IP"
    else
        echo "  $IFACE: IP adresi yok"
    fi
done
echo ""

################################################################################
# 5. DNSmasq Konfigürasyon Kontrolü
################################################################################
print_step "5. DNSmasq konfigürasyonu kontrol ediliyor..."

if [ -f /etc/dnsmasq.d/captive-portal.conf ]; then
    print_success "Captive portal konfigürasyonu mevcut"

    # Konfigürasyon testi
    print_step "Konfigürasyon test ediliyor..."
    if dnsmasq --test 2>&1 | grep -q "syntax check OK"; then
        print_success "Konfigürasyon geçerli"
    else
        print_error "Konfigürasyon hatalı:"
        dnsmasq --test 2>&1 || true
    fi
else
    print_warning "Captive portal konfigürasyonu bulunamadı"
    print_warning "master_setup_captive_portal.sh scriptini çalıştırın"
fi
echo ""

################################################################################
# 6. Ana DNSmasq Konfigürasyon Dosyası
################################################################################
print_step "6. Ana dnsmasq.conf kontrol ediliyor..."

MAIN_CONF="/etc/dnsmasq.conf"
if [ -f "$MAIN_CONF" ]; then
    # Çakışma olabilecek ayarları kontrol et
    CONFLICTS=0

    if grep -q "^port=" "$MAIN_CONF" 2>/dev/null; then
        print_warning "Ana konfigürasyonda port ayarı var"
        CONFLICTS=1
    fi

    if grep -q "^bind-interfaces" "$MAIN_CONF" 2>/dev/null; then
        print_warning "Ana konfigürasyonda bind-interfaces var"
        CONFLICTS=1
    fi

    if [ $CONFLICTS -eq 0 ]; then
        print_success "Ana konfigürasyon uyumlu"
    else
        print_warning "Ana konfigürasyonda çakışma olabilir"
    fi
else
    print_warning "Ana dnsmasq.conf bulunamadı"
fi
echo ""

################################################################################
# 7. DNSmasq Restart Denemesi
################################################################################
print_step "7. DNSmasq yeniden başlatılıyor..."

if systemctl restart dnsmasq 2>&1; then
    print_success "dnsmasq başarıyla yeniden başlatıldı"
    sleep 2

    if systemctl is-active --quiet dnsmasq; then
        print_success "dnsmasq şu anda çalışıyor"
    else
        print_error "dnsmasq başlatılamadı!"
    fi
else
    print_error "dnsmasq restart başarısız!"
    echo ""
    print_step "Detaylı hata log'u:"
    journalctl -u dnsmasq -n 20 --no-pager
fi
echo ""

################################################################################
# 8. Durum Özeti
################################################################################
print_header "Durum Özeti"
echo ""

# Servis durumu
if systemctl is-active --quiet dnsmasq; then
    print_success "DNSmasq Servisi: ÇALIŞIYOR"
else
    print_error "DNSmasq Servisi: ÇALIŞMIYOR"
fi

# Port 53 durumu
if netstat -tulpn 2>/dev/null | grep -q "dnsmasq.*:53" || ss -tulpn 2>/dev/null | grep -q "dnsmasq.*:53"; then
    print_success "Port 53: DNSmasq tarafından kullanılıyor"
else
    print_warning "Port 53: DNSmasq tarafından kullanılmıyor"
fi

echo ""
print_header "Öneriler"
echo ""

if ! systemctl is-active --quiet dnsmasq; then
    echo "1. Hata log'larını inceleyin:"
    echo "   ${BLUE}sudo journalctl -u dnsmasq -n 50${NC}"
    echo ""
    echo "2. systemd-resolved'ı tamamen durdurmayı deneyin:"
    echo "   ${BLUE}sudo systemctl stop systemd-resolved${NC}"
    echo "   ${BLUE}sudo systemctl disable systemd-resolved${NC}"
    echo ""
    echo "3. Ana dnsmasq.conf'u kontrol edin:"
    echo "   ${BLUE}cat /etc/dnsmasq.conf${NC}"
    echo ""
fi

echo "DNSmasq manuel başlatma:"
echo "  ${BLUE}sudo systemctl start dnsmasq${NC}"
echo ""

echo "DNSmasq durumu:"
echo "  ${BLUE}sudo systemctl status dnsmasq${NC}"
echo ""

echo "DNSmasq log'ları:"
echo "  ${BLUE}sudo journalctl -u dnsmasq -f${NC}"
echo ""

print_success "Sorun giderme tamamlandı!"

