#!/usr/bin/env bash
# Network Monitor Kurulum Scripti
# Bu script network_monitor.py ve servisini otomatik olarak kurar

set -e

echo "================================================"
echo "  Network Monitor Kurulum Scripti"
echo "================================================"
echo ""

# Root kontrolü
if [ "$EUID" -ne 0 ]; then
    echo "HATA: Bu script root olarak çalıştırılmalıdır"
    echo "Kullanım: sudo bash install_network_monitor.sh"
    exit 1
fi

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
TARGET_DIR="/opt/lscope"
SERVICE_FILE="network-monitor.service"
PYTHON_SCRIPT="network_monitor.py"

echo "[1/6] Hedef dizini oluşturuluyor: $TARGET_DIR"
mkdir -p "$TARGET_DIR"
mkdir -p "$TARGET_DIR/bin"

echo "[2/6] Python script kopyalanıyor..."
if [ -f "$SCRIPT_DIR/$PYTHON_SCRIPT" ]; then
    cp "$SCRIPT_DIR/$PYTHON_SCRIPT" "$TARGET_DIR/$PYTHON_SCRIPT"
    chmod +x "$TARGET_DIR/$PYTHON_SCRIPT"
    echo "  ✓ $PYTHON_SCRIPT kopyalandı"
else
    echo "  ✗ HATA: $PYTHON_SCRIPT bulunamadı!"
    exit 1
fi

echo "[3/6] Systemd servis dosyası kuruluyor..."
if [ -f "$SCRIPT_DIR/$SERVICE_FILE" ]; then
    cp "$SCRIPT_DIR/$SERVICE_FILE" /etc/systemd/system/
    echo "  ✓ $SERVICE_FILE kopyalandı"
else
    echo "  ✗ HATA: $SERVICE_FILE bulunamadı!"
    exit 1
fi

echo "[4/6] Systemd yeniden yükleniyor..."
systemctl daemon-reload
echo "  ✓ daemon-reload tamamlandı"

echo "[5/6] Servis etkinleştiriliyor..."
systemctl enable network-monitor.service
echo "  ✓ Servis sistem başlangıcında otomatik başlayacak"

echo "[6/6] Servis başlatılıyor..."
systemctl start network-monitor.service
echo "  ✓ Servis başlatıldı"

echo ""
echo "================================================"
echo "  Kurulum Tamamlandı!"
echo "================================================"
echo ""
echo "Servis durumunu kontrol etmek için:"
echo "  sudo systemctl status network-monitor.service"
echo ""
echo "Logları izlemek için:"
echo "  sudo tail -f /var/log/network_monitor.log"
echo "  sudo journalctl -u network-monitor.service -f"
echo ""
echo "Servisi durdurmak için:"
echo "  sudo systemctl stop network-monitor.service"
echo ""
echo "Servisi devre dışı bırakmak için:"
echo "  sudo systemctl disable network-monitor.service"
echo ""

# Servis durumunu göster
sleep 2
echo "Mevcut servis durumu:"
echo "-------------------"
systemctl status network-monitor.service --no-pager -l || true

