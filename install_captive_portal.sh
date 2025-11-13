#!/usr/bin/env bash
# Captive Portal Spoofing Service Installer
# Bu script connectivity check spoofing için gerekli servisleri kurar

set -euo pipefail

LOG=/var/log/captive_portal_setup.log
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "[Captive Portal Setup] $(date '+%F %T')" | tee -a "$LOG"

# Python3 ve Flask kontrol et
if ! command -v python3 &>/dev/null; then
    echo "Python3 yükleniyor..." | tee -a "$LOG"
    apt-get update
    apt-get install -y python3
fi

# Flask kurulu değilse sistem paketi olarak kur (externally-managed-environment hatası için)
if ! python3 -c "import flask" 2>/dev/null; then
    echo "Flask yükleniyor (sistem paketi)..." | tee -a "$LOG"
    apt-get update
    apt-get install -y python3-flask
fi

# Captive portal script'ini /opt/lscope/bin'e kopyala
mkdir -p /opt/lscope/bin
cp "$SCRIPT_DIR/captive_portal_spoof.py" /opt/lscope/bin/
chmod +x /opt/lscope/bin/captive_portal_spoof.py

# Systemd service dosyası oluştur
cat > /etc/systemd/system/captive-portal-spoof.service <<'EOF'
[Unit]
Description=Captive Portal Connectivity Check Spoofing Service
After=network.target wlan0-static.service
Wants=wlan0-static.service

[Service]
Type=simple
ExecStart=/usr/bin/python3 /opt/lscope/bin/captive_portal_spoof.py
Restart=always
RestartSec=5
StandardOutput=append:/var/log/captive_portal_spoof.log
StandardError=append:/var/log/captive_portal_spoof.log
User=root

[Install]
WantedBy=multi-user.target
EOF

# dnsmasq config dosyasını kopyala
if [ -f "$SCRIPT_DIR/dnsmasq_ap_spoof.conf" ]; then
    cp "$SCRIPT_DIR/dnsmasq_ap_spoof.conf" /etc/dnsmasq.d/ap-spoof.conf
    echo "dnsmasq spoofing config kopyalandı" | tee -a "$LOG"
fi

# Servisi etkinleştir ama başlatma (AP moduna geçilince başlayacak)
systemctl daemon-reload
systemctl enable captive-portal-spoof.service

# iptables kuralları - Port 80 ve 443'ü captive portal'a yönlendir
cat > /opt/lscope/bin/setup_captive_iptables.sh <<'EOF'
#!/usr/bin/env bash
# Captive portal için iptables kuralları

# Temizle
iptables -t nat -F PREROUTING
iptables -t nat -F POSTROUTING

# DNS isteklerini local DNS'e yönlendir (dnsmasq)
iptables -t nat -A PREROUTING -i wlan0 -p udp --dport 53 -j REDIRECT --to-port 53
iptables -t nat -A PREROUTING -i wlan0 -p tcp --dport 53 -j REDIRECT --to-port 53

# HTTP isteklerini captive portal'a yönlendir
iptables -t nat -A PREROUTING -i wlan0 -p tcp --dport 80 -j REDIRECT --to-port 80

# HTTPS için - sertifika olmadığı için sadece 204 döneceğiz
# iptables -t nat -A PREROUTING -i wlan0 -p tcp --dport 443 -j REDIRECT --to-port 443

# Masquerading - eğer gerçek internet paylaşımı yapılacaksa
# iptables -t nat -A POSTROUTING -o eth0 -j MASQUERADE

echo "Captive portal iptables kuralları uygulandı"
EOF

chmod +x /opt/lscope/bin/setup_captive_iptables.sh

# iptables servisini oluştur
cat > /etc/systemd/system/captive-iptables.service <<'EOF'
[Unit]
Description=Captive Portal iptables Rules
After=network.target wlan0-static.service captive-portal-spoof.service
Wants=wlan0-static.service

[Service]
Type=oneshot
ExecStart=/opt/lscope/bin/setup_captive_iptables.sh
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable captive-iptables.service

echo "[Captive Portal Setup Complete] $(date '+%F %T')" | tee -a "$LOG"
echo "Servisler AP moduna geçildiğinde otomatik başlayacak" | tee -a "$LOG"
echo "Manuel başlatmak için: sudo systemctl start captive-portal-spoof.service" | tee -a "$LOG"

