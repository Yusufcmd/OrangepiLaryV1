#!/usr/bin/env bash
################################################################################
# DNSMASQ LOCAL-SERVICE PROBLEM FIX
# --local-service parametresini kaldırır ve düzgün yapılandırır
################################################################################

set -uo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

print_step() { echo -e "${BLUE}▶${NC} $1"; }
print_success() { echo -e "${GREEN}✓${NC} $1"; }
print_error() { echo -e "${RED}✗${NC} $1"; }
print_warning() { echo -e "${YELLOW}⚠${NC} $1"; }

if [ "$EUID" -ne 0 ]; then
    print_error "Bu script root olarak çalıştırılmalıdır!"
    echo "Kullanım: sudo bash $0"
    exit 1
fi

echo ""
echo -e "${BLUE}╔══════════════════════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║  DNSMASQ LOCAL-SERVICE SORUNU DÜZELTİLİYOR                      ║${NC}"
echo -e "${BLUE}╚══════════════════════════════════════════════════════════════════╝${NC}"
echo ""

################################################################################
# Problem Tespiti
################################################################################
print_step "Problem tespit ediliyor..."

DNSMASQ_CMDLINE=$(ps aux | grep dnsmasq | grep -v grep || true)
if echo "$DNSMASQ_CMDLINE" | grep -q -- "--local-service"; then
    print_error "dnsmasq --local-service parametresiyle çalışıyor!"
    print_warning "Bu parametre port 53'ü sadece local için dinler"
    print_warning "Network'ten gelen DNS isteklerini kabul etmez"
    echo ""
    echo "Mevcut komut satırı:"
    echo "$DNSMASQ_CMDLINE"
else
    print_warning "dnsmasq --local-service parametresi yok, ama yine de düzeltelim"
fi

echo ""

################################################################################
# Systemd Service Override Oluştur
################################################################################
print_step "Systemd service override oluşturuluyor..."

# Override dizinini oluştur
mkdir -p /etc/systemd/system/dnsmasq.service.d/

# Override dosyası oluştur
cat > /etc/systemd/system/dnsmasq.service.d/override.conf <<'EOF'
[Service]
# --local-service parametresini kaldır
# Bunun yerine kendi konfigürasyonumuzu kullan
ExecStart=
ExecStart=/usr/sbin/dnsmasq -k --conf-file=/etc/dnsmasq.conf
EOF

print_success "Systemd override oluşturuldu"

################################################################################
# Ana dnsmasq.conf'u düzenle
################################################################################
print_step "Ana dnsmasq.conf düzenleniyor..."

# Yedek al
cp /etc/dnsmasq.conf "/etc/dnsmasq.conf.backup.$(date +%Y%m%d_%H%M%S)"

# Yeni minimal config oluştur
cat > /etc/dnsmasq.conf <<'EOF'
# DNSmasq Configuration for Captive Portal
# Tüm network interface'lerinden dinle

# Temel ayarlar
domain-needed
bogus-priv
no-resolv
no-poll

# Upstream DNS sunucuları
server=8.8.8.8
server=8.8.4.4
server=1.1.1.1

# Interface ayarları - Belirli interface'lerde dinle
# wlan0 için (AP modu)
interface=wlan0
# Eğer eth0 da kullanılıyorsa
#interface=eth0

# DHCP ayarları (AP modu için)
dhcp-range=192.168.4.50,192.168.4.150,255.255.255.0,24h
dhcp-option=3,192.168.4.1
dhcp-option=6,192.168.4.1

# Log
log-queries
log-dhcp
log-facility=/var/log/dnsmasq.log

# Ek konfigürasyonlar
conf-dir=/etc/dnsmasq.d/,*.conf

# ÖNEMLI: local-service kullanma!
# bind-interfaces kullan (network'ten istekleri kabul eder)
bind-interfaces

# Cache boyutu
cache-size=10000
EOF

print_success "dnsmasq.conf güncellendi"

################################################################################
# Captive Portal Config Kontrolü
################################################################################
print_step "Captive portal config kontrol ediliyor..."

if [ -f /etc/dnsmasq.d/captive-portal.conf ]; then
    print_success "Captive portal config mevcut"

    # IP adresini al veya varsayılan kullan
    LOCAL_IP=$(ip -4 addr show wlan0 2>/dev/null | grep -oP '(?<=inet\s)\d+(\.\d+){3}' | head -n1 || echo "192.168.4.1")

    print_step "Captive portal config IP adresi güncelleniyor: $LOCAL_IP"

    # Config'i yeniden oluştur
    cat > /etc/dnsmasq.d/captive-portal.conf <<EOFCONF
# Captive Portal DNS Configuration
# Android (Google) Connectivity Checks
address=/connectivitycheck.gstatic.com/$LOCAL_IP
address=/www.google.com/$LOCAL_IP
address=/clients3.google.com/$LOCAL_IP
address=/play.googleapis.com/$LOCAL_IP
address=/android.clients.google.com/$LOCAL_IP

# Apple (iOS/macOS) Connectivity Checks
address=/captive.apple.com/$LOCAL_IP
address=/www.apple.com/$LOCAL_IP
address=/www.appleiphonecell.com/$LOCAL_IP
address=/www.itools.info/$LOCAL_IP

# Microsoft (Windows) Connectivity Checks
address=/www.msftconnecttest.com/$LOCAL_IP
address=/www.msftncsi.com/$LOCAL_IP
address=/ipv6.msftconnecttest.com/$LOCAL_IP

# Firefox Connectivity Checks
address=/detectportal.firefox.com/$LOCAL_IP

# Ubuntu/Linux Connectivity Checks
address=/connectivity-check.ubuntu.com/$LOCAL_IP
address=/nmcheck.gnome.org/$LOCAL_IP
EOFCONF

    print_success "Captive portal config güncellendi (IP: $LOCAL_IP)"
else
    print_warning "Captive portal config yok, oluşturuluyor..."

    LOCAL_IP="192.168.4.1"

    cat > /etc/dnsmasq.d/captive-portal.conf <<EOFCONF
# Captive Portal DNS Configuration
address=/connectivitycheck.gstatic.com/$LOCAL_IP
address=/captive.apple.com/$LOCAL_IP
address=/www.msftconnecttest.com/$LOCAL_IP
EOFCONF

    print_success "Minimal captive portal config oluşturuldu"
fi

################################################################################
# Syntax Kontrolü
################################################################################
print_step "Konfigürasyon syntax kontrolü..."

if dnsmasq --test 2>&1 | grep -q "syntax check OK"; then
    print_success "Konfigürasyon geçerli"
else
    print_error "Konfigürasyon syntax hatası:"
    dnsmasq --test 2>&1 | tail -10
    echo ""
    print_warning "Devam ediliyor..."
fi

################################################################################
# Systemd Daemon Reload
################################################################################
print_step "Systemd daemon-reload..."

systemctl daemon-reload
print_success "Systemd yenilendi"

################################################################################
# Servisi Restart Et
################################################################################
print_step "dnsmasq yeniden başlatılıyor..."

systemctl restart dnsmasq
sleep 3

################################################################################
# Durum Kontrolü
################################################################################
print_step "Durum kontrol ediliyor..."

echo ""
if systemctl is-active --quiet dnsmasq; then
    print_success "✓ dnsmasq ÇALIŞIYOR!"

    # Komut satırını kontrol et
    DNSMASQ_NEW_CMDLINE=$(ps aux | grep dnsmasq | grep -v grep || true)
    echo ""
    echo "Yeni komut satırı:"
    echo "$DNSMASQ_NEW_CMDLINE"
    echo ""

    # --local-service var mı kontrol et
    if echo "$DNSMASQ_NEW_CMDLINE" | grep -q -- "--local-service"; then
        print_error "⚠ --local-service hala var!"
        print_warning "Systemd override düzgün yüklenmemiş olabilir"
    else
        print_success "✓ --local-service parametresi kaldırıldı!"
    fi

    # Port 53 kontrolü
    echo ""
    print_step "Port 53 kontrolü..."

    if netstat -tulpn 2>/dev/null | grep -q "dnsmasq.*:53" || ss -tulpn 2>/dev/null | grep -q "dnsmasq.*:53"; then
        print_success "✓ Port 53 dnsmasq tarafından kullanılıyor"
        echo ""
        echo "Port 53 detayları:"
        netstat -tulpn 2>/dev/null | grep ":53" || ss -tulpn 2>/dev/null | grep ":53"
    else
        print_warning "⚠ Port 53 dnsmasq tarafından kullanılmıyor"
        echo ""
        echo "Tüm port 53 kullanımı:"
        netstat -tulpn 2>/dev/null | grep ":53" || ss -tulpn 2>/dev/null | grep ":53" || echo "  (port 53 boş)"
    fi

    # Log kontrolü
    echo ""
    print_step "Son log mesajları:"
    journalctl -u dnsmasq -n 10 --no-pager

else
    print_error "✗ dnsmasq BAŞLATILAMADI!"
    echo ""
    echo "Hata detayları:"
    journalctl -u dnsmasq -n 20 --no-pager
fi

################################################################################
# Özet
################################################################################
echo ""
echo -e "${BLUE}╔══════════════════════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║  ÖZET                                                           ║${NC}"
echo -e "${BLUE}╚══════════════════════════════════════════════════════════════════╝${NC}"
echo ""

echo "Yapılan değişiklikler:"
echo "  ✓ Systemd service override oluşturuldu (/etc/systemd/system/dnsmasq.service.d/override.conf)"
echo "  ✓ dnsmasq.conf yeniden yapılandırıldı (bind-interfaces)"
echo "  ✓ Captive portal config güncellendi"
echo "  ✓ --local-service parametresi kaldırıldı"
echo ""

echo "Kontrol komutları:"
echo "  sudo systemctl status dnsmasq"
echo "  sudo ps aux | grep dnsmasq"
echo "  sudo netstat -tulpn | grep :53"
echo "  sudo journalctl -u dnsmasq -f"
echo ""

echo "Test komutu (başka bir cihazdan):"
echo "  nslookup connectivitycheck.gstatic.com [ORANGE_PI_IP]"
echo ""

if systemctl is-active --quiet dnsmasq; then
    # Port 53 kontrolü
    if netstat -tulpn 2>/dev/null | grep -q "dnsmasq.*:53" || ss -tulpn 2>/dev/null | grep -q "dnsmasq.*:53"; then
        echo -e "${GREEN}✓✓✓ SORUN ÇÖZÜLDÜ! dnsmasq port 53'ü kullanıyor! ✓✓✓${NC}"
    else
        echo -e "${YELLOW}⚠⚠⚠ dnsmasq çalışıyor ama port 53'ü kullanmıyor ⚠⚠⚠${NC}"
        echo ""
        echo "Ek kontrol gereken durumlar:"
        echo "  1. wlan0 interface'i aktif mi?"
        echo "     ip addr show wlan0"
        echo ""
        echo "  2. Firewall kuralları port 53'ü engelliyor mu?"
        echo "     sudo iptables -L -n | grep 53"
        echo ""
        echo "  3. bind-interfaces çalışıyor mu?"
        echo "     sudo dnsmasq --test"
    fi
else
    echo -e "${RED}✗✗✗ dnsmasq başlatılamadı! ✗✗✗${NC}"
fi

echo ""
echo "Yedek dosyalar:"
echo "  /etc/dnsmasq.conf.backup.*"
echo ""

