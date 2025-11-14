#!/usr/bin/env bash
################################################################################
# DNSMASQ FORCE FIX
# dnsmasq'ı zorla düzelten agresif script
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
echo -e "${BLUE}║  DNSMASQ FORCE FIX - Agresif Düzeltme                           ║${NC}"
echo -e "${BLUE}╚══════════════════════════════════════════════════════════════════╝${NC}"
echo ""

################################################################################
# Adım 1: Tüm çakışan servisleri durdur
################################################################################
print_step "Adım 1: Çakışan servisleri durduruluyor..."

# systemd-resolved'ı durdur
if systemctl is-active --quiet systemd-resolved; then
    print_warning "systemd-resolved durduruluyor..."
    systemctl stop systemd-resolved
    systemctl disable systemd-resolved
    print_success "systemd-resolved durduruldu"
else
    print_success "systemd-resolved zaten kapalı"
fi

# dnsmasq'ı durdur
if systemctl is-active --quiet dnsmasq; then
    print_warning "dnsmasq durduruluyor..."
    systemctl stop dnsmasq
    print_success "dnsmasq durduruldu"
fi

sleep 2

################################################################################
# Adım 2: Port 53'ü zorla temizle
################################################################################
print_step "Adım 2: Port 53 zorla temizleniyor..."

# Port 53'ü kullanan tüm processleri öldür
PORT_USERS=$(lsof -ti:53 2>/dev/null || true)
if [ -n "$PORT_USERS" ]; then
    print_warning "Port 53'ü kullanan processler öldürülüyor: $PORT_USERS"
    echo "$PORT_USERS" | xargs kill -9 2>/dev/null || true
    sleep 2
    print_success "Port 53 temizlendi"
else
    print_success "Port 53 zaten boş"
fi

################################################################################
# Adım 3: Captive portal config'ini geçici kaldır
################################################################################
print_step "Adım 3: Captive portal config geçici kaldırılıyor..."

if [ -f /etc/dnsmasq.d/captive-portal.conf ]; then
    cp /etc/dnsmasq.d/captive-portal.conf /tmp/captive-portal.conf.backup
    rm -f /etc/dnsmasq.d/captive-portal.conf
    print_success "Captive portal config yedeklendi ve kaldırıldı"
else
    print_warning "Captive portal config zaten yok"
fi

################################################################################
# Adım 4: Ana dnsmasq.conf'u kontrol et ve düzelt
################################################################################
print_step "Adım 4: Ana dnsmasq.conf kontrol ediliyor..."

MAIN_CONF="/etc/dnsmasq.conf"

# Yedek al
if [ -f "$MAIN_CONF" ]; then
    cp "$MAIN_CONF" "${MAIN_CONF}.backup.$(date +%Y%m%d_%H%M%S)"
    print_success "dnsmasq.conf yedeklendi"

    # Temel bir konfigürasyon oluştur
    cat > "$MAIN_CONF" <<'EOF'
# Minimal dnsmasq configuration
domain-needed
bogus-priv
no-resolv
no-poll

# DNS sunucuları
server=8.8.8.8
server=8.8.4.4

# Interface (AP mode için wlan0)
interface=wlan0
bind-interfaces

# DHCP ayarları (opsiyonel, AP modunda gerekli)
dhcp-range=192.168.4.50,192.168.4.150,255.255.255.0,24h
dhcp-option=3,192.168.4.1
dhcp-option=6,192.168.4.1

# Log
log-queries
log-dhcp

# Ek konfigürasyonlar için
conf-dir=/etc/dnsmasq.d/,*.conf
EOF
    print_success "Minimal dnsmasq.conf oluşturuldu"
else
    print_error "dnsmasq.conf bulunamadı!"
fi

################################################################################
# Adım 5: Syntax kontrolü
################################################################################
print_step "Adım 5: Konfigürasyon syntax kontrolü..."

if dnsmasq --test 2>&1 | grep -q "syntax check OK"; then
    print_success "Konfigürasyon geçerli"
else
    print_error "Konfigürasyon hatası:"
    dnsmasq --test 2>&1 | tail -10
    echo ""
    print_warning "Devam ediliyor..."
fi

################################################################################
# Adım 6: resolv.conf'u düzelt
################################################################################
print_step "Adım 6: /etc/resolv.conf düzeltiliyor..."

# Eğer systemd-resolved symbolic link ise kaldır
if [ -L /etc/resolv.conf ]; then
    print_warning "/etc/resolv.conf symbolic link, kaldırılıyor..."
    rm -f /etc/resolv.conf
fi

# Yeni resolv.conf oluştur
cat > /etc/resolv.conf <<'EOF'
nameserver 8.8.8.8
nameserver 8.8.4.4
EOF

print_success "/etc/resolv.conf düzeltildi"

################################################################################
# Adım 7: dnsmasq'ı başlat
################################################################################
print_step "Adım 7: dnsmasq başlatılıyor..."

systemctl enable dnsmasq
systemctl start dnsmasq

sleep 3

################################################################################
# Adım 8: Durum kontrolü
################################################################################
print_step "Adım 8: Durum kontrol ediliyor..."

if systemctl is-active --quiet dnsmasq; then
    print_success "✓ dnsmasq BAŞARIYLA BAŞLATILDI!"

    # Port 53 kontrolü
    if netstat -tulpn 2>/dev/null | grep -q "dnsmasq.*:53" || ss -tulpn 2>/dev/null | grep -q "dnsmasq.*:53"; then
        print_success "✓ Port 53 dnsmasq tarafından kullanılıyor"
    else
        print_warning "⚠ Port 53 dnsmasq tarafından kullanılmıyor"
    fi

    echo ""
    echo "dnsmasq durumu:"
    systemctl status dnsmasq --no-pager | head -15

else
    print_error "✗ dnsmasq BAŞLATILAMADI!"
    echo ""
    echo "Detaylı hata mesajı:"
    journalctl -u dnsmasq -n 30 --no-pager

    echo ""
    print_error "Manuel debug gerekli. Şu komutu çalıştırın:"
    echo "  sudo dnsmasq --no-daemon --log-queries"
    exit 1
fi

################################################################################
# Adım 9: Captive portal config'ini geri yükle
################################################################################
echo ""
print_step "Adım 9: Captive portal config geri yükleniyor..."

if [ -f /tmp/captive-portal.conf.backup ]; then
    # IP adresini al veya varsayılan kullan
    LOCAL_IP=$(ip -4 addr show wlan0 2>/dev/null | grep -oP '(?<=inet\s)\d+(\.\d+){3}' | head -n1 || echo "192.168.4.1")

    print_warning "Kullanılan IP: $LOCAL_IP"

    # Yeni captive portal config oluştur
    cat > /etc/dnsmasq.d/captive-portal.conf <<EOFCONF
# Captive Portal DNS Configuration
# Google (Android) Connectivity Checks
address=/connectivitycheck.gstatic.com/$LOCAL_IP
address=/www.google.com/$LOCAL_IP
address=/clients3.google.com/$LOCAL_IP
address=/play.googleapis.com/$LOCAL_IP
address=/android.clients.google.com/$LOCAL_IP

# Apple (iOS/macOS) Connectivity Checks
address=/captive.apple.com/$LOCAL_IP
address=/www.apple.com/$LOCAL_IP
address=/www.appleiphonecell.com/$LOCAL_IP

# Microsoft (Windows) Connectivity Checks
address=/www.msftconnecttest.com/$LOCAL_IP
address=/www.msftncsi.com/$LOCAL_IP

# Firefox Connectivity Checks
address=/detectportal.firefox.com/$LOCAL_IP

# Ubuntu/Linux Connectivity Checks
address=/connectivity-check.ubuntu.com/$LOCAL_IP
address=/nmcheck.gnome.org/$LOCAL_IP
EOFCONF

    print_success "Captive portal config oluşturuldu"

    # Syntax kontrolü
    if dnsmasq --test 2>&1 | grep -q "syntax check OK"; then
        print_success "Captive portal config geçerli"

        # Restart
        print_step "dnsmasq yeniden başlatılıyor..."
        systemctl restart dnsmasq
        sleep 2

        if systemctl is-active --quiet dnsmasq; then
            print_success "✓ dnsmasq captive portal config ile çalışıyor!"
        else
            print_error "✗ Captive portal config sonrası dnsmasq başlatılamadı"
            print_warning "Captive portal config kaldırılıyor..."
            rm -f /etc/dnsmasq.d/captive-portal.conf
            systemctl restart dnsmasq
        fi
    else
        print_error "Captive portal config hatalı, kaldırılıyor..."
        rm -f /etc/dnsmasq.d/captive-portal.conf
    fi
else
    print_warning "Captive portal config yedeği bulunamadı, yenisi oluşturuluyor..."

    LOCAL_IP="192.168.4.1"
    cat > /etc/dnsmasq.d/captive-portal.conf <<EOFCONF
# Captive Portal DNS Configuration (Minimal)
address=/connectivitycheck.gstatic.com/$LOCAL_IP
address=/captive.apple.com/$LOCAL_IP
address=/www.msftconnecttest.com/$LOCAL_IP
EOFCONF

    systemctl restart dnsmasq
fi

################################################################################
# Final Durum
################################################################################
echo ""
echo -e "${BLUE}╔══════════════════════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║  FİNAL DURUM RAPORU                                             ║${NC}"
echo -e "${BLUE}╚══════════════════════════════════════════════════════════════════╝${NC}"
echo ""

# dnsmasq durumu
if systemctl is-active --quiet dnsmasq; then
    print_success "dnsmasq Servisi: ÇALIŞIYOR ✓"
else
    print_error "dnsmasq Servisi: ÇALIŞMIYOR ✗"
fi

# Port 53 durumu
if netstat -tulpn 2>/dev/null | grep -q "dnsmasq.*:53" || ss -tulpn 2>/dev/null | grep -q "dnsmasq.*:53"; then
    print_success "Port 53: dnsmasq tarafından kullanılıyor ✓"
else
    print_error "Port 53: dnsmasq tarafından kullanılmıyor ✗"
fi

# systemd-resolved durumu
if systemctl is-active --quiet systemd-resolved; then
    print_warning "systemd-resolved: Hala çalışıyor ⚠"
else
    print_success "systemd-resolved: Kapalı ✓"
fi

echo ""
echo "Kontrol komutları:"
echo "  sudo systemctl status dnsmasq"
echo "  sudo netstat -tulpn | grep :53"
echo "  sudo journalctl -u dnsmasq -f"
echo ""

# Log bilgisi
echo "Yedek dosyalar:"
echo "  /etc/dnsmasq.conf yedekleri: /etc/dnsmasq.conf.backup.*"
echo "  Captive portal yedek: /tmp/captive-portal.conf.backup"
echo ""

if systemctl is-active --quiet dnsmasq; then
    echo -e "${GREEN}✓✓✓ DNSMASQ BAŞARIYLA DÜZELTİLDİ! ✓✓✓${NC}"
else
    echo -e "${RED}✗✗✗ DNSMASQ HALA BAŞLAMIYOR! ✗✗✗${NC}"
    echo ""
    echo "Manuel kontrol gerekli:"
    echo "  sudo dnsmasq --no-daemon --log-queries"
fi

