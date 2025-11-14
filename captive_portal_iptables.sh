#!/usr/bin/env bash
# captive_portal_iptables.sh
# Bu script, tüm HTTP/HTTPS trafiğini local captive portal server'a yönlendirir

set -euo pipefail

LOG="/var/log/captive_portal_iptables.log"
IFACE="wlan0"

echo "[$(date '+%F %T')] Captive Portal iptables kuralları uygulanıyor..." | tee -a "$LOG"

# Yerel IP adresini al
LOCAL_IP=$(ip -4 addr show "$IFACE" 2>/dev/null | grep -oP '(?<=inet\s)\d+(\.\d+){3}' | head -n1)

if [ -z "$LOCAL_IP" ]; then
    echo "[ERROR] $IFACE interface'inde IP adresi bulunamadı!" | tee -a "$LOG"
    exit 1
fi

echo "[INFO] Interface: $IFACE, Local IP: $LOCAL_IP" | tee -a "$LOG"

# Eski kuralları temizle (sadece bizim zincirlerimizi)
iptables -t nat -D PREROUTING -i "$IFACE" -j CAPTIVE_PORTAL 2>/dev/null || true
iptables -t nat -F CAPTIVE_PORTAL 2>/dev/null || true
iptables -t nat -X CAPTIVE_PORTAL 2>/dev/null || true

# Yeni zincir oluştur
iptables -t nat -N CAPTIVE_PORTAL

# DNS trafiğini local DNS server'a yönlendir (port 53)
iptables -t nat -A CAPTIVE_PORTAL -p udp --dport 53 -j DNAT --to-destination "$LOCAL_IP":53
iptables -t nat -A CAPTIVE_PORTAL -p tcp --dport 53 -j DNAT --to-destination "$LOCAL_IP":53

# HTTP trafiğini local web server'a yönlendir (port 80)
iptables -t nat -A CAPTIVE_PORTAL -p tcp --dport 80 -j DNAT --to-destination "$LOCAL_IP":80

# HTTPS trafiği için özel işlem
# HTTPS'i doğrudan yönlendiremeyiz (SSL sertifika sorunu)
# Ancak connectivity check'ler genelde HTTP kullanır, bu yeterli
# İsterseniz HTTPS'i de HTTP'ye redirect edebiliriz
iptables -t nat -A CAPTIVE_PORTAL -p tcp --dport 443 -j DNAT --to-destination "$LOCAL_IP":80

# Ana PREROUTING zincirine ekle
iptables -t nat -A PREROUTING -i "$IFACE" -j CAPTIVE_PORTAL

echo "[$(date '+%F %T')] iptables kuralları başarıyla uygulandı" | tee -a "$LOG"

# IP forwarding'i aktif et (önemli!)
echo 1 > /proc/sys/net/ipv4/ip_forward
echo "net.ipv4.ip_forward=1" > /etc/sysctl.d/99-ip-forward.conf
sysctl -p /etc/sysctl.d/99-ip-forward.conf

# FORWARD zincirinde traffic'e izin ver
iptables -A FORWARD -i "$IFACE" -j ACCEPT
iptables -A FORWARD -o "$IFACE" -j ACCEPT

echo "[$(date '+%F %T')] IP forwarding aktif edildi" | tee -a "$LOG"

# Kuralları göster
echo "[INFO] Aktif iptables NAT kuralları:" | tee -a "$LOG"
iptables -t nat -L CAPTIVE_PORTAL -n -v | tee -a "$LOG"

echo "[$(date '+%F %T')] Captive Portal iptables yapılandırması tamamlandı!" | tee -a "$LOG"

