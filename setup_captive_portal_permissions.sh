#!/usr/bin/env bash
# setup_captive_portal_permissions.sh
# Tüm captive portal scriptlerini çalıştırılabilir yapar

set -euo pipefail

echo "Captive Portal script izinleri ayarlanıyor..."

# Ana dizindeki scriptler
chmod +x install_captive_portal.sh
chmod +x quick_test_captive_portal.sh
chmod +x captive_portal_dns_config.sh
chmod +x captive_portal_iptables.sh
chmod +x fake_internet_server.py

echo "✓ Script izinleri ayarlandı"
echo ""
echo "Kurulum için:"
echo "  sudo bash install_captive_portal.sh"
echo ""
echo "Test için:"
echo "  sudo bash quick_test_captive_portal.sh"

