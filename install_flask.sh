#!/usr/bin/env bash
# Hızlı Flask Kurulum Script'i
# externally-managed-environment hatasını sistem paketi ile çözer

set -e

echo "=========================================="
echo "Flask Sistem Paketi Kurulumu"
echo "=========================================="

# Root kontrolü
if [ "$EUID" -ne 0 ]; then
    echo "Bu script'i root olarak çalıştırın: sudo bash $0"
    exit 1
fi

echo "Paket listesi güncelleniyor..."
apt-get update -qq

echo "Flask sistem paketi kuruluyor..."
apt-get install -y python3-flask

echo ""
echo "✓ Flask başarıyla kuruldu!"
echo ""

# Versiyon kontrolü
if python3 -c "import flask" 2>/dev/null; then
    VERSION=$(python3 -c "import flask; print(flask.__version__)")
    echo "Flask versiyonu: $VERSION"
    echo ""
    echo "Şimdi captive portal kurulumunu çalıştırabilirsiniz:"
    echo "  sudo bash install_captive_portal.sh"
else
    echo "✗ Hata: Flask kurulamadı!"
    exit 1
fi

