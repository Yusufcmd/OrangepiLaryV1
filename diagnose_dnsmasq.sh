#!/usr/bin/env bash
################################################################################
# DNSMASQ DEEP DIAGNOSTICS
# Detaylı dnsmasq teşhis scripti
################################################################################

set -uo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

print_header() {
    echo ""
    echo -e "${BLUE}═══════════════════════════════════════════════════════════════${NC}"
    echo -e "${BLUE}  $1${NC}"
    echo -e "${BLUE}═══════════════════════════════════════════════════════════════${NC}"
}

print_error() { echo -e "${RED}✗${NC} $1"; }
print_success() { echo -e "${GREEN}✓${NC} $1"; }
print_info() { echo -e "${BLUE}ℹ${NC} $1"; }
print_warning() { echo -e "${YELLOW}⚠${NC} $1"; }

if [ "$EUID" -ne 0 ]; then
    print_error "Bu script root olarak çalıştırılmalıdır!"
    exit 1
fi

print_header "DNSMASQ DETAYLI TEŞHİS BAŞLATIYOR"

################################################################################
# 1. Son Hata Mesajlarını Al
################################################################################
print_header "1. DNSMASQ SON HATA MESAJLARI"

echo "Son 30 satır dnsmasq log:"
journalctl -u dnsmasq -n 30 --no-pager | tail -20

echo ""
echo "Systemd durumu:"
systemctl status dnsmasq --no-pager -l || true

################################################################################
# 2. Konfigürasyon Dosyalarını Kontrol Et
################################################################################
print_header "2. KONFİGÜRASYON DOSYALARI"

echo "Ana dnsmasq.conf:"
if [ -f /etc/dnsmasq.conf ]; then
    print_success "/etc/dnsmasq.conf mevcut"
    echo "Aktif satırlar (comment olmayan):"
    grep -v "^#" /etc/dnsmasq.conf | grep -v "^$" || echo "  (hiç aktif satır yok)"
else
    print_error "/etc/dnsmasq.conf BULUNAMADI!"
fi

echo ""
echo "Captive portal config:"
if [ -f /etc/dnsmasq.d/captive-portal.conf ]; then
    print_success "/etc/dnsmasq.d/captive-portal.conf mevcut"
    echo "İçerik (ilk 20 satır):"
    head -20 /etc/dnsmasq.d/captive-portal.conf
else
    print_error "/etc/dnsmasq.d/captive-portal.conf BULUNAMADI!"
fi

echo ""
echo "/etc/dnsmasq.d/ dizini içeriği:"
ls -lah /etc/dnsmasq.d/ || print_error "Dizin bulunamadı"

################################################################################
# 3. Syntax Kontrolü
################################################################################
print_header "3. KONFİGÜRASYON SYNTAX KONTROLÜ"

echo "dnsmasq --test çıktısı:"
dnsmasq --test 2>&1 || true

################################################################################
# 4. Port 53 Kullanımı
################################################################################
print_header "4. PORT 53 KULLANIM DURUMU"

echo "netstat ile port 53:"
netstat -tulpn 2>/dev/null | grep ":53 " || echo "  (netstat ile port 53 boş)"

echo ""
echo "ss ile port 53:"
ss -tulpn 2>/dev/null | grep ":53 " || echo "  (ss ile port 53 boş)"

echo ""
echo "lsof ile port 53:"
lsof -i :53 2>/dev/null || echo "  (lsof ile port 53 boş)"

################################################################################
# 5. systemd-resolved Kontrolü
################################################################################
print_header "5. SYSTEMD-RESOLVED DURUMU"

if systemctl is-active --quiet systemd-resolved; then
    print_warning "systemd-resolved ÇALIŞIYOR - Bu port 53'ü kullanıyor olabilir!"

    echo ""
    echo "systemd-resolved durumu:"
    systemctl status systemd-resolved --no-pager || true

    echo ""
    echo "resolved.conf içeriği:"
    cat /etc/systemd/resolved.conf 2>/dev/null || echo "  (dosya yok)"

    echo ""
    echo "resolved.conf.d/ dizini:"
    ls -lah /etc/systemd/resolved.conf.d/ 2>/dev/null || echo "  (dizin yok)"

    if [ -f /etc/systemd/resolved.conf.d/disable-stub.conf ]; then
        echo "disable-stub.conf içeriği:"
        cat /etc/systemd/resolved.conf.d/disable-stub.conf
    fi
else
    print_success "systemd-resolved çalışmıyor"
fi

################################################################################
# 6. Network Interface Durumu
################################################################################
print_header "6. NETWORK INTERFACE DURUMU"

echo "Tüm interface'ler ve IP adresleri:"
ip -4 addr show || ifconfig || true

echo ""
echo "wlan0 özellikle:"
ip -4 addr show wlan0 2>/dev/null || echo "  wlan0 bulunamadı veya IP yok"

################################################################################
# 7. Dosya İzinleri
################################################################################
print_header "7. DOSYA İZİNLERİ"

echo "dnsmasq binary:"
ls -lh $(which dnsmasq) 2>/dev/null || print_error "dnsmasq bulunamadı"

echo ""
echo "Konfigürasyon dosyası izinleri:"
ls -lh /etc/dnsmasq.conf 2>/dev/null || echo "  dosya yok"
ls -lh /etc/dnsmasq.d/ 2>/dev/null || echo "  dizin yok"

################################################################################
# 8. Manuel Test (Debug Mode)
################################################################################
print_header "8. MANUEL DEBUG TEST"

echo "dnsmasq'ı debug modda başlatmayı deniyoruz (5 saniye)..."
echo "Hata mesajlarını dikkatle okuyun:"
echo ""

timeout 5 dnsmasq --no-daemon --log-queries --log-dhcp 2>&1 || true

echo ""
echo "(5 saniye sonra otomatik durduruldu)"

################################################################################
# 9. Çözüm Önerileri
################################################################################
print_header "9. OLASI SORUNLAR VE ÇÖZÜMLER"

# Syntax hatası kontrolü
if ! dnsmasq --test 2>&1 | grep -q "syntax check OK"; then
    print_error "KONFİGÜRASYON SYNTAX HATASI VAR!"
    echo ""
    echo "ÇÖZüM 1: Captive portal config'ini geçici olarak kaldır:"
    echo "  sudo mv /etc/dnsmasq.d/captive-portal.conf /tmp/"
    echo "  sudo systemctl restart dnsmasq"
    echo ""
    echo "ÇÖZÜM 2: Ana config'i kontrol et:"
    echo "  sudo nano /etc/dnsmasq.conf"
fi

# Port 53 çakışması kontrolü
if netstat -tulpn 2>/dev/null | grep -q ":53 " || ss -tulpn 2>/dev/null | grep -q ":53 "; then
    print_error "PORT 53 BAŞKA BİR SERVİS TARAFINDAN KULLANILIYOR!"
    echo ""
    echo "ÇÖZÜM: systemd-resolved'ı tamamen durdur:"
    echo "  sudo systemctl stop systemd-resolved"
    echo "  sudo systemctl disable systemd-resolved"
    echo "  sudo systemctl restart dnsmasq"
fi

# Interface kontrolü
if ! ip -4 addr show wlan0 2>/dev/null | grep -q "inet "; then
    print_warning "wlan0 interface'inde IP adresi yok"
    echo ""
    echo "NOT: Bu normal olabilir (AP modu aktif değilse)"
    echo "Captive portal config'inde varsayılan IP kullanılmış olabilir"
fi

# systemd-resolved kontrolü
if systemctl is-active --quiet systemd-resolved; then
    print_error "SYSTEMD-RESOLVED HALA ÇALIŞIYOR!"
    echo ""
    echo "ÇÖZÜM: systemd-resolved'ı tamamen kapat:"
    echo "  sudo systemctl stop systemd-resolved"
    echo "  sudo systemctl disable systemd-resolved"
    echo "  sudo rm -f /etc/resolv.conf"
    echo "  sudo echo 'nameserver 8.8.8.8' > /etc/resolv.conf"
fi

################################################################################
# 10. Acil Düzeltme Komutları
################################################################################
print_header "10. ACİL DÜZELTME KOMUTLARI"

echo "Aşağıdaki komutları SIRAYLA deneyin:"
echo ""
echo "# 1. systemd-resolved'ı tamamen kapat"
echo "sudo systemctl stop systemd-resolved"
echo "sudo systemctl disable systemd-resolved"
echo ""
echo "# 2. Port 53'ü kim kullanıyor, öldür"
echo "sudo lsof -ti:53 | xargs sudo kill -9"
echo ""
echo "# 3. Captive portal config'ini geçici kaldır"
echo "sudo mv /etc/dnsmasq.d/captive-portal.conf /tmp/captive-portal.conf.backup"
echo ""
echo "# 4. dnsmasq'ı başlat"
echo "sudo systemctl restart dnsmasq"
echo ""
echo "# 5. Çalışıyor mu kontrol et"
echo "sudo systemctl status dnsmasq"
echo ""
echo "# 6. Çalışıyorsa, captive config'i geri koy"
echo "sudo mv /tmp/captive-portal.conf.backup /etc/dnsmasq.d/captive-portal.conf"
echo "sudo systemctl restart dnsmasq"

################################################################################
# Özet
################################################################################
print_header "TEŞHİS TAMAMLANDI"

echo "Yukarıdaki çıktıları inceleyin ve önerilen çözümleri deneyin."
echo ""
echo "En yaygın sorunlar:"
echo "  1. systemd-resolved port 53'ü kullanıyor"
echo "  2. Konfigürasyon syntax hatası"
echo "  3. Port 53 başka bir servis tarafından kullanılıyor"
echo "  4. dnsmasq.conf veya captive-portal.conf hatalı"
echo ""
echo "Log dosyası: /tmp/dnsmasq_diagnosis_$(date +%Y%m%d_%H%M%S).log"

