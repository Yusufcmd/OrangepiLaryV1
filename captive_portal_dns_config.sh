#!/usr/bin/env bash
# captive_portal_dns_config.sh
# Bu script, dnsmasq'ı captive portal için yapılandırır
# Tüm connectivity check domain'lerini local IP'ye yönlendirir

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
cat > "$DNSMASQ_CONF" <<EOF
# Captive Portal DNS Configuration
# Bu dosya cihazların internet bağlantı kontrollerini local server'a yönlendirir

# ============================================================================
# Google (Android) Connectivity Checks
# ============================================================================
address=/connectivitycheck.gstatic.com/$LOCAL_IP
address=/www.google.com/$LOCAL_IP
address=/clients3.google.com/$LOCAL_IP
address=/play.googleapis.com/$LOCAL_IP
address=/android.clients.google.com/$LOCAL_IP

# ============================================================================
# Apple (iOS/macOS) Connectivity Checks
# ============================================================================
address=/captive.apple.com/$LOCAL_IP
address=/www.apple.com/$LOCAL_IP
address=/www.appleiphonecell.com/$LOCAL_IP
address=/www.itools.info/$LOCAL_IP
address=/www.ibook.info/$LOCAL_IP
address=/www.airport.us/$LOCAL_IP
address=/www.thinkdifferent.us/$LOCAL_IP

# ============================================================================
# Microsoft (Windows) Connectivity Checks
# ============================================================================
address=/www.msftconnecttest.com/$LOCAL_IP
address=/www.msftncsi.com/$LOCAL_IP
address=/ipv6.msftconnecttest.com/$LOCAL_IP
address=/dns.msftncsi.com/$LOCAL_IP

# ============================================================================
# Firefox Connectivity Checks
# ============================================================================
address=/detectportal.firefox.com/$LOCAL_IP

# ============================================================================
# Ubuntu/Linux Connectivity Checks
# ============================================================================
address=/connectivity-check.ubuntu.com/$LOCAL_IP
address=/nmcheck.gnome.org/$LOCAL_IP
address=/network-test.debian.org/$LOCAL_IP
address=/fedoraproject.org/$LOCAL_IP

# ============================================================================
# Genel ayarlar
# ============================================================================
# DNS cache boyutu artır
cache-size=10000

# DHCP için authoritative ol
dhcp-authoritative

# DNS günlüğü
log-queries
log-dhcp

# Upstream DNS sunucularını kullanma (tamamen local çalış)
no-resolv
no-poll

# Local domain
domain=local
local=/local/

EOF

echo "[$(date '+%F %T')] dnsmasq konfigürasyonu oluşturuldu: $DNSMASQ_CONF" | tee -a "$LOG"

# dnsmasq servisini yeniden başlat
echo "[$(date '+%F %T')] dnsmasq yeniden başlatılıyor..." | tee -a "$LOG"
systemctl restart dnsmasq || {
    echo "[ERROR] dnsmasq restart başarısız!" | tee -a "$LOG"
    exit 1
}

echo "[$(date '+%F %T')] dnsmasq başarıyla yeniden başlatıldı" | tee -a "$LOG"

# Durum kontrolü
if systemctl is-active --quiet dnsmasq; then
    echo "[SUCCESS] dnsmasq servisi aktif" | tee -a "$LOG"
else
    echo "[ERROR] dnsmasq servisi başlatılamadı!" | tee -a "$LOG"
    exit 1
fi

echo "[$(date '+%F %T')] Captive Portal DNS yapılandırması tamamlandı!" | tee -a "$LOG"

