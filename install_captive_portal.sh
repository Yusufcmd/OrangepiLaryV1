#!/usr/bin/env bash
# install_captive_portal.sh
# Captive Portal sistemini Orange Pi'ye kurar
# Bu sistem, cihazların "internet yok" algısını engeller

set -euo pipefail

LOG="/var/log/captive_portal_install.log"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "============================================================" | tee -a "$LOG"
echo "[$(date '+%F %T')] Captive Portal Kurulumu Başlıyor..." | tee -a "$LOG"
echo "============================================================" | tee -a "$LOG"

# Root kontrolü
if [ "$EUID" -ne 0 ]; then
    echo "[ERROR] Bu script root olarak çalıştırılmalıdır!" | tee -a "$LOG"
    echo "Kullanım: sudo bash $0" | tee -a "$LOG"
    exit 1
fi

# ============================================================================
# 1. Gerekli dizinleri oluştur
# ============================================================================
echo "[1/7] Dizinler oluşturuluyor..." | tee -a "$LOG"
mkdir -p /opt/lscope
mkdir -p /opt/lscope/bin
mkdir -p /etc/dnsmasq.d
mkdir -p /var/log

# ============================================================================
# 2. Fake Internet Server'ı kopyala
# ============================================================================
echo "[2/7] Fake Internet Server kopyalanıyor..." | tee -a "$LOG"
cp -f "$SCRIPT_DIR/fake_internet_server.py" /opt/lscope/
chmod +x /opt/lscope/fake_internet_server.py
echo "  → /opt/lscope/fake_internet_server.py" | tee -a "$LOG"

# ============================================================================
# 3. Captive Portal scriptlerini kopyala
# ============================================================================
echo "[3/7] Captive Portal scriptleri kopyalanıyor..." | tee -a "$LOG"
cp -f "$SCRIPT_DIR/captive_portal_dns_config.sh" /opt/lscope/bin/
cp -f "$SCRIPT_DIR/captive_portal_iptables.sh" /opt/lscope/bin/
chmod +x /opt/lscope/bin/captive_portal_dns_config.sh
chmod +x /opt/lscope/bin/captive_portal_iptables.sh
echo "  → /opt/lscope/bin/captive_portal_dns_config.sh" | tee -a "$LOG"
echo "  → /opt/lscope/bin/captive_portal_iptables.sh" | tee -a "$LOG"

# ============================================================================
# 4. Systemd servislerini kopyala
# ============================================================================
echo "[4/7] Systemd servisleri kopyalan��yor..." | tee -a "$LOG"
cp -f "$SCRIPT_DIR/captive-portal-spoof.service" /etc/systemd/system/
cp -f "$SCRIPT_DIR/captive-iptables.service" /etc/systemd/system/
echo "  → /etc/systemd/system/captive-portal-spoof.service" | tee -a "$LOG"
echo "  → /etc/systemd/system/captive-iptables.service" | tee -a "$LOG"

# ============================================================================
# 5. DNS yapılandırmasını çalıştır
# ============================================================================
echo "[5/7] DNS yapılandırması yapılıyor..." | tee -a "$LOG"
bash /opt/lscope/bin/captive_portal_dns_config.sh || {
    echo "[WARNING] DNS yapılandırması başarısız oldu" | tee -a "$LOG"
}

# ============================================================================
# 6. Systemd daemon'ını yeniden yükle
# ============================================================================
echo "[6/7] Systemd daemon yeniden yükleniyor..." | tee -a "$LOG"
systemctl daemon-reload

# ============================================================================
# 7. Servisleri etkinleştir (ancak henüz başlatma)
# ============================================================================
echo "[7/7] Servisler etkinleştiriliyor..." | tee -a "$LOG"
systemctl enable captive-portal-spoof.service
systemctl enable captive-iptables.service
echo "  → captive-portal-spoof.service etkinleştirildi" | tee -a "$LOG"
echo "  → captive-iptables.service etkinleştirildi" | tee -a "$LOG"

# ============================================================================
# Kurulum Tamamlandı
# ============================================================================
echo "============================================================" | tee -a "$LOG"
echo "[$(date '+%F %T')] Captive Portal Kurulumu Tamamlandı!" | tee -a "$LOG"
echo "============================================================" | tee -a "$LOG"
echo "" | tee -a "$LOG"
echo "Kurulmuş Bileşenler:" | tee -a "$LOG"
echo "  ✓ Fake Internet Server (Flask)" | tee -a "$LOG"
echo "  ✓ DNS Yönlendirme (dnsmasq)" | tee -a "$LOG"
echo "  ✓ iptables Kuralları" | tee -a "$LOG"
echo "  ✓ Systemd Servisleri" | tee -a "$LOG"
echo "" | tee -a "$LOG"
echo "Desteklenen Platformlar:" | tee -a "$LOG"
echo "  ✓ Android (Google)" | tee -a "$LOG"
echo "  ✓ iOS/macOS (Apple)" | tee -a "$LOG"
echo "  ✓ Windows (Microsoft)" | tee -a "$LOG"
echo "  ✓ Linux (Ubuntu, Fedora, Arch, etc.)" | tee -a "$LOG"
echo "  ✓ Firefox Browser" | tee -a "$LOG"
echo "" | tee -a "$LOG"
echo "Servisler AP moduna geçildiğinde otomatik başlatılacak." | tee -a "$LOG"
echo "Manuel başlatmak için:" | tee -a "$LOG"
echo "  sudo systemctl start captive-iptables.service" | tee -a "$LOG"
echo "  sudo systemctl start captive-portal-spoof.service" | tee -a "$LOG"
echo "" | tee -a "$LOG"
echo "Durum kontrolü için:" | tee -a "$LOG"
echo "  sudo systemctl status captive-portal-spoof.service" | tee -a "$LOG"
echo "  sudo systemctl status captive-iptables.service" | tee -a "$LOG"
echo "" | tee -a "$LOG"
echo "Log dosyaları:" | tee -a "$LOG"
echo "  - $LOG" | tee -a "$LOG"
echo "  - /var/log/captive_portal_setup.log" | tee -a "$LOG"
echo "  - /var/log/captive_portal_iptables.log" | tee -a "$LOG"
echo "  - journalctl -u captive-portal-spoof.service" | tee -a "$LOG"
echo "" | tee -a "$LOG"

