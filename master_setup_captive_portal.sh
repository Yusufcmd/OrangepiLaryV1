#!/usr/bin/env bash
################################################################################
# CAPTIVE PORTAL MASTER SETUP SCRIPT
# Orange Pi AP Captive Portal Sistemi - Otomatik Kurulum
#
# Bu script tüm captive portal bileşenlerini oluşturur ve kurar.
# Gereksinimler: root yetki, dnsmasq, iptables, python3, flask
#
# Kullanım: sudo bash master_setup_captive_portal.sh
#
# Versiyon: 1.0.0
# Tarih: 2025-11-14
# Geliştirici: Rise Arge Team
################################################################################

set -euo pipefail

# Renkli çıktı için ANSI kodları
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Log dosyası
MASTER_LOG="/var/log/captive_portal_master_setup.log"
INSTALL_DIR="/opt/lscope"
BIN_DIR="$INSTALL_DIR/bin"

################################################################################
# Yardımcı Fonksiyonlar
################################################################################

print_header() {
    echo -e "${CYAN}╔══════════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${CYAN}║${NC}  $1"
    echo -e "${CYAN}╚══════════════════════════════════════════════════════════════════╝${NC}"
}

print_step() {
    echo -e "${BLUE}▶${NC} $1"
}

print_success() {
    echo -e "${GREEN}✓${NC} $1"
}

print_error() {
    echo -e "${RED}✗${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}⚠${NC} $1"
}

log_message() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$MASTER_LOG"
}

check_root() {
    if [ "$EUID" -ne 0 ]; then
        print_error "Bu script root olarak çalıştırılmalıdır!"
        echo "Kullanım: sudo bash $0"
        exit 1
    fi
}

check_dependencies() {
    local missing_deps=()

    if ! command -v python3 &> /dev/null; then
        missing_deps+=("python3")
    fi

    if ! command -v systemctl &> /dev/null; then
        missing_deps+=("systemd")
    fi

    if ! command -v iptables &> /dev/null; then
        missing_deps+=("iptables")
    fi

    if [ ${#missing_deps[@]} -gt 0 ]; then
        print_error "Eksik bağımlılıklar: ${missing_deps[*]}"
        print_step "Kurmak için: apt-get install -y ${missing_deps[*]}"
        return 1
    fi

    # Flask kontrolü
    if ! python3 -c "import flask" 2>/dev/null; then
        print_warning "Flask kurulu değil. pip3 ile kurulacak..."
        pip3 install flask || print_warning "Flask kurulumu başarısız, manuel kurulum gerekebilir"
    fi

    return 0
}

################################################################################
# Dosya Oluşturma Fonksiyonları
################################################################################

create_fake_internet_server() {
    print_step "Fake Internet Server oluşturuluyor..."

    cat > "$INSTALL_DIR/fake_internet_server.py" <<'EOF'
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Fake Internet Connectivity Check Server
Bu server, tüm major işletim sistemlerinin internet bağlantı kontrolü için
kullandığı endpoint'lere yanıt vererek cihazların "internet var" algısını sağlar.

Desteklenen platformlar:
- Android (Google)
- iOS/macOS (Apple)
- Windows (Microsoft)
- Linux (Ubuntu, Fedora, Arch, etc.)
- Firefox
"""

from flask import Flask, Response, request
import logging

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ============================================================================
# Android Connectivity Checks (Google)
# ============================================================================
@app.route('/generate_204')
def android_generate_204():
    """Android cihazların kullandığı temel connectivity check"""
    logger.info(f"Android connectivity check: {request.user_agent}")
    return Response(status=204)

@app.route('/gen_204')
def android_gen_204():
    """Android alternatif endpoint"""
    logger.info(f"Android gen_204 check: {request.user_agent}")
    return Response(status=204)

# ============================================================================
# Apple (iOS/macOS) Connectivity Checks
# ============================================================================
@app.route('/hotspot-detect.html')
def apple_hotspot_detect():
    """iOS/macOS cihazların kullandığı captive portal check"""
    logger.info(f"Apple hotspot-detect: {request.user_agent}")
    html = """<!DOCTYPE html>
<html>
<head>
<title>Success</title>
</head>
<body>
Success
</body>
</html>"""
    return Response(html, status=200, mimetype='text/html')

@app.route('/library/test/success.html')
def apple_library_test():
    """iOS/macOS alternatif endpoint"""
    logger.info(f"Apple library/test check: {request.user_agent}")
    html = """<!DOCTYPE html>
<html>
<head>
<title>Success</title>
</head>
<body>
Success
</body>
</html>"""
    return Response(html, status=200, mimetype='text/html')

@app.route('/success.txt')
def apple_success_txt():
    """macOS için text response"""
    logger.info(f"Apple success.txt check: {request.user_agent}")
    return Response("Success", status=200, mimetype='text/plain')

# ============================================================================
# Microsoft Windows Connectivity Checks
# ============================================================================
@app.route('/ncsi.txt')
def windows_ncsi_txt():
    """Windows NCSI (Network Connectivity Status Indicator) - Text"""
    logger.info(f"Windows NCSI txt check: {request.user_agent}")
    return Response("Microsoft NCSI", status=200, mimetype='text/plain')

@app.route('/connecttest.txt')
def windows_connecttest_txt():
    """Windows 10/11 connectivity test"""
    logger.info(f"Windows connecttest check: {request.user_agent}")
    return Response("Microsoft Connect Test", status=200, mimetype='text/plain')

@app.route('/redirect')
def windows_redirect():
    """Windows redirect test - captive portal check"""
    logger.info(f"Windows redirect check: {request.user_agent}")
    return Response(status=200)

# ============================================================================
# Firefox Connectivity Checks
# ============================================================================
@app.route('/success.txt', subdomain='detectportal')
def firefox_detectportal():
    """Firefox captive portal detection"""
    logger.info(f"Firefox detectportal check: {request.user_agent}")
    return Response("success", status=200, mimetype='text/plain')

# ============================================================================
# Linux Connectivity Checks
# ============================================================================
@app.route('/check_network_status.txt')
def linux_ubuntu_check():
    """Ubuntu connectivity check"""
    logger.info(f"Ubuntu connectivity check: {request.user_agent}")
    return Response(status=204)

@app.route('/check_network')
def linux_generic_check():
    """Generic Linux network check"""
    logger.info(f"Linux generic check: {request.user_agent}")
    return Response(status=204)

# ============================================================================
# Catch-all routes
# ============================================================================
@app.route('/')
def index():
    """Root endpoint - genel kullanım"""
    logger.info(f"Root access: {request.user_agent}")
    return Response("Network Connected", status=200)

@app.route('/<path:path>')
def catch_all(path):
    """Bilinmeyen tüm istekleri yakala ve success dön"""
    logger.info(f"Catch-all: {path} from {request.user_agent}")
    if path.endswith('.txt'):
        return Response("OK", status=200, mimetype='text/plain')
    elif path.endswith('.html') or path.endswith('.htm'):
        return Response("<!DOCTYPE html><html><head><title>OK</title></head><body>OK</body></html>",
                       status=200, mimetype='text/html')
    else:
        return Response(status=204)

if __name__ == '__main__':
    logger.info("Starting Fake Internet Connectivity Server on port 80...")
    logger.info("This server will respond to connectivity checks from:")
    logger.info("  - Android devices")
    logger.info("  - iOS/macOS devices")
    logger.info("  - Windows devices")
    logger.info("  - Linux devices")
    logger.info("  - Firefox browser")
    app.run(host='0.0.0.0', port=80, debug=False)
EOF

    chmod +x "$INSTALL_DIR/fake_internet_server.py"
    print_success "Fake Internet Server oluşturuldu"
}

create_dns_config_script() {
    print_step "DNS yapılandırma scripti oluşturuluyor..."

    cat > "$BIN_DIR/captive_portal_dns_config.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

DNSMASQ_CONF="/etc/dnsmasq.d/captive-portal.conf"
LOG="/var/log/captive_portal_setup.log"

echo "[$(date '+%F %T')] Captive Portal DNS yapılandırması başlıyor..." | tee -a "$LOG"

# Yerel IP adresini al (wlan0 için)
LOCAL_IP=$(ip -4 addr show wlan0 2>/dev/null | grep -oP '(?<=inet\s)\d+(\.\d+){3}' | head -n1)

if [ -z "$LOCAL_IP" ]; then
    echo "[ERROR] wlan0 interface'inde IP adresi bulunamadı!" | tee -a "$LOG"
    exit 1
fi

echo "[INFO] Local IP: $LOCAL_IP" | tee -a "$LOG"

# dnsmasq captive portal konfigürasyonu oluştur
cat > "$DNSMASQ_CONF" <<EOFCONF
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
address=/www.itools.info/$LOCAL_IP
address=/www.ibook.info/$LOCAL_IP
address=/www.airport.us/$LOCAL_IP
address=/www.thinkdifferent.us/$LOCAL_IP

# Microsoft (Windows) Connectivity Checks
address=/www.msftconnecttest.com/$LOCAL_IP
address=/www.msftncsi.com/$LOCAL_IP
address=/ipv6.msftconnecttest.com/$LOCAL_IP
address=/dns.msftncsi.com/$LOCAL_IP

# Firefox Connectivity Checks
address=/detectportal.firefox.com/$LOCAL_IP

# Ubuntu/Linux Connectivity Checks
address=/connectivity-check.ubuntu.com/$LOCAL_IP
address=/nmcheck.gnome.org/$LOCAL_IP
address=/network-test.debian.org/$LOCAL_IP
address=/fedoraproject.org/$LOCAL_IP

# Genel ayarlar
cache-size=10000
dhcp-authoritative
log-queries
log-dhcp
no-resolv
no-poll
domain=local
local=/local/
EOFCONF

echo "[$(date '+%F %T')] dnsmasq konfigürasyonu oluşturuldu: $DNSMASQ_CONF" | tee -a "$LOG"
systemctl restart dnsmasq || { echo "[ERROR] dnsmasq restart başarısız!" | tee -a "$LOG"; exit 1; }
echo "[$(date '+%F %T')] Captive Portal DNS yapılandırması tamamlandı!" | tee -a "$LOG"
EOF

    chmod +x "$BIN_DIR/captive_portal_dns_config.sh"
    print_success "DNS yapılandırma scripti oluşturuldu"
}

create_iptables_script() {
    print_step "iptables scripti oluşturuluyor..."

    cat > "$BIN_DIR/captive_portal_iptables.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

LOG="/var/log/captive_portal_iptables.log"
IFACE="wlan0"

echo "[$(date '+%F %T')] Captive Portal iptables kuralları uygulanıyor..." | tee -a "$LOG"

LOCAL_IP=$(ip -4 addr show "$IFACE" 2>/dev/null | grep -oP '(?<=inet\s)\d+(\.\d+){3}' | head -n1)

if [ -z "$LOCAL_IP" ]; then
    echo "[ERROR] $IFACE interface'inde IP adresi bulunamadı!" | tee -a "$LOG"
    exit 1
fi

echo "[INFO] Interface: $IFACE, Local IP: $LOCAL_IP" | tee -a "$LOG"

# Eski kuralları temizle
iptables -t nat -D PREROUTING -i "$IFACE" -j CAPTIVE_PORTAL 2>/dev/null || true
iptables -t nat -F CAPTIVE_PORTAL 2>/dev/null || true
iptables -t nat -X CAPTIVE_PORTAL 2>/dev/null || true

# Yeni zincir oluştur
iptables -t nat -N CAPTIVE_PORTAL

# DNS, HTTP, HTTPS yönlendirme
iptables -t nat -A CAPTIVE_PORTAL -p udp --dport 53 -j DNAT --to-destination "$LOCAL_IP":53
iptables -t nat -A CAPTIVE_PORTAL -p tcp --dport 53 -j DNAT --to-destination "$LOCAL_IP":53
iptables -t nat -A CAPTIVE_PORTAL -p tcp --dport 80 -j DNAT --to-destination "$LOCAL_IP":80
iptables -t nat -A CAPTIVE_PORTAL -p tcp --dport 443 -j DNAT --to-destination "$LOCAL_IP":80

# Ana PREROUTING zincirine ekle
iptables -t nat -A PREROUTING -i "$IFACE" -j CAPTIVE_PORTAL

# IP forwarding aktif et
echo 1 > /proc/sys/net/ipv4/ip_forward
echo "net.ipv4.ip_forward=1" > /etc/sysctl.d/99-ip-forward.conf
sysctl -p /etc/sysctl.d/99-ip-forward.conf >/dev/null 2>&1

# FORWARD zincirinde traffic'e izin ver
iptables -A FORWARD -i "$IFACE" -j ACCEPT 2>/dev/null || true
iptables -A FORWARD -o "$IFACE" -j ACCEPT 2>/dev/null || true

echo "[$(date '+%F %T')] Captive Portal iptables yapılandırması tamamlandı!" | tee -a "$LOG"
EOF

    chmod +x "$BIN_DIR/captive_portal_iptables.sh"
    print_success "iptables scripti oluşturuldu"
}

create_systemd_services() {
    print_step "Systemd servisleri oluşturuluyor..."

    # Fake server servisi
    cat > "/etc/systemd/system/captive-portal-spoof.service" <<EOF
[Unit]
Description=Captive Portal Fake Internet Server
After=network.target wlan0-static.service
Wants=wlan0-static.service

[Service]
Type=simple
User=root
WorkingDirectory=$INSTALL_DIR
ExecStart=/usr/bin/python3 $INSTALL_DIR/fake_internet_server.py
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal
NoNewPrivileges=false
PrivateTmp=yes

[Install]
WantedBy=multi-user.target
EOF

    # iptables servisi
    cat > "/etc/systemd/system/captive-iptables.service" <<EOF
[Unit]
Description=Captive Portal iptables Rules
After=network.target wlan0-static.service
Wants=wlan0-static.service
Before=captive-portal-spoof.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=$BIN_DIR/captive_portal_iptables.sh
ExecStop=/usr/sbin/iptables -t nat -D PREROUTING -i wlan0 -j CAPTIVE_PORTAL
ExecStop=/usr/sbin/iptables -t nat -F CAPTIVE_PORTAL
ExecStop=/usr/sbin/iptables -t nat -X CAPTIVE_PORTAL
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

    systemctl daemon-reload
    print_success "Systemd servisleri oluşturuldu"
}

create_test_script() {
    print_step "Test scripti oluşturuluyor..."

    cat > "$INSTALL_DIR/quick_test_captive_portal.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo "============================================================"
echo "Captive Portal Sistemi - Hızlı Test"
echo "============================================================"
echo ""

check_service() {
    if systemctl is-active --quiet "$1"; then
        echo -e "${GREEN}✓${NC} $1: RUNNING"
        return 0
    else
        echo -e "${RED}✗${NC} $1: NOT RUNNING"
        return 1
    fi
}

check_port() {
    if netstat -tlnp 2>/dev/null | grep -q ":$1 "; then
        echo -e "${GREEN}✓${NC} Port $1: LISTENING"
        return 0
    else
        echo -e "${RED}✗${NC} Port $1: NOT LISTENING"
        return 1
    fi
}

echo "Servis Kontrolleri:"
check_service "captive-portal-spoof.service"
check_service "captive-iptables.service"
check_service "dnsmasq"
check_service "hostapd"
echo ""

echo "Port Kontrolleri:"
check_port "80"
check_port "53"
echo ""

WLAN0_IP=$(ip -4 addr show wlan0 2>/dev/null | grep -oP '(?<=inet\s)\d+(\.\d+){3}' | head -n1)
if [ -n "$WLAN0_IP" ]; then
    echo -e "${GREEN}✓${NC} wlan0 IP: $WLAN0_IP"
else
    echo -e "${RED}✗${NC} wlan0: NO IP ADDRESS"
fi
echo ""

echo "Test tamamlandı!"
EOF

    chmod +x "$INSTALL_DIR/quick_test_captive_portal.sh"
    print_success "Test scripti oluşturuldu"
}

################################################################################
# Ana Kurulum Fonksiyonu
################################################################################

main() {
    clear
    print_header "CAPTIVE PORTAL MASTER SETUP - Orange Pi AP Çözümü"
    echo ""
    log_message "Captive Portal Master Setup başlatıldı"

    # 1. Root kontrolü
    print_step "Root yetkileri kontrol ediliyor..."
    check_root
    print_success "Root yetkileri tamam"
    echo ""

    # 2. Bağımlılık kontrolü
    print_step "Bağımlılıklar kontrol ediliyor..."
    if check_dependencies; then
        print_success "Tüm bağımlılıklar mevcut"
    else
        print_error "Bağımlılık hatası! Lütfen eksik paketleri kurun."
        exit 1
    fi
    echo ""

    # 3. Dizinleri oluştur
    print_step "Dizinler oluşturuluyor..."
    mkdir -p "$INSTALL_DIR"
    mkdir -p "$BIN_DIR"
    mkdir -p /etc/dnsmasq.d
    mkdir -p /var/log
    print_success "Dizinler oluşturuldu"
    echo ""

    # 4. Dosyaları oluştur
    print_header "Dosyalar Oluşturuluyor"
    echo ""

    create_fake_internet_server
    create_dns_config_script
    create_iptables_script
    create_systemd_services
    create_test_script

    echo ""

    # 5. DNS yapılandırmasını çalıştır
    print_step "DNS yapılandırması yapılıyor..."
    if [ -f "$BIN_DIR/captive_portal_dns_config.sh" ]; then
        bash "$BIN_DIR/captive_portal_dns_config.sh" || print_warning "DNS yapılandırması başarısız"
    fi
    echo ""

    # 6. Servisleri etkinleştir
    print_step "Servisler etkinleştiriliyor..."
    systemctl enable captive-portal-spoof.service
    systemctl enable captive-iptables.service
    print_success "Servisler etkinleştirildi"
    echo ""

    # 7. Kurulum özeti
    print_header "Kurulum Tamamlandı!"
    echo ""
    print_success "Fake Internet Server oluşturuldu"
    print_success "DNS yapılandırması tamamlandı"
    print_success "iptables kuralları hazırlandı"
    print_success "Systemd servisleri etkinleştirildi"
    print_success "Test scripti oluşturuldu"
    echo ""

    print_header "Desteklenen Platformlar"
    echo "  ✓ Android (Google)"
    echo "  ✓ iOS/macOS (Apple)"
    echo "  ✓ Windows (Microsoft)"
    echo "  ✓ Linux (Ubuntu, Fedora, etc.)"
    echo "  ✓ Firefox Browser"
    echo ""

    print_header "Sonraki Adımlar"
    echo "  1. Test etmek için:"
    echo "     ${CYAN}sudo bash $INSTALL_DIR/quick_test_captive_portal.sh${NC}"
    echo ""
    echo "  2. Servisleri başlatmak için (AP modunda otomatik başlar):"
    echo "     ${CYAN}sudo systemctl start captive-iptables.service${NC}"
    echo "     ${CYAN}sudo systemctl start captive-portal-spoof.service${NC}"
    echo ""
    echo "  3. Durum kontrolü için:"
    echo "     ${CYAN}sudo systemctl status captive-portal-spoof.service${NC}"
    echo ""
    echo "  4. Log dosyaları:"
    echo "     - $MASTER_LOG"
    echo "     - /var/log/captive_portal_setup.log"
    echo "     - journalctl -u captive-portal-spoof.service"
    echo ""

    log_message "Captive Portal Master Setup başarıyla tamamlandı!"
}

################################################################################
# Script Başlangıç
################################################################################

main "$@"

