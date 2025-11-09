#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Orange Pi cihaz adını değiştiren script.
Hostname, hostapd SSID ve diğer tüm adlandırmaları günceller.

Kullanım:
    sudo python3 change_device_name.py RTCLARY20054
    veya
    sudo python3 change_device_name.py --old RTCLARY20052 --new RTCLARY20054
"""

import sys
import os
import subprocess
import re
import argparse
from typing import Optional


def run_command(cmd: list, check: bool = True) -> tuple[bool, str]:
    """Komutu çalıştır ve sonucu döndür."""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=check
        )
        return True, result.stdout + result.stderr
    except subprocess.CalledProcessError as e:
        return False, f"Hata: {e.stdout} {e.stderr}"
    except Exception as e:
        return False, f"Hata: {str(e)}"


def get_current_hostname() -> str:
    """Mevcut hostname'i al."""
    try:
        with open("/etc/hostname", "r") as f:
            return f.read().strip()
    except Exception:
        success, output = run_command(["hostname"])
        if success:
            return output.strip()
        return "unknown"


def update_hostname(new_name: str) -> bool:
    """Hostname'i güncelle."""
    print(f"[1/5] Hostname güncelleniyor: {new_name}")

    # /etc/hostname dosyasını güncelle
    try:
        with open("/etc/hostname", "w") as f:
            f.write(new_name + "\n")
        print("  ✓ /etc/hostname güncellendi")
    except Exception as e:
        print(f"  ✗ /etc/hostname güncellenemedi: {e}")
        return False

    # hostnamectl ile ayarla
    success, output = run_command(["hostnamectl", "set-hostname", new_name], check=False)
    if success:
        print("  ✓ hostnamectl ile hostname ayarlandı")
    else:
        print(f"  ! hostnamectl uyarısı: {output}")

    # hostname komutunu çalıştır
    success, output = run_command(["hostname", new_name], check=False)
    if success:
        print("  ✓ hostname komutu çalıştırıldı")
    else:
        print(f"  ! hostname komutu uyarısı: {output}")

    return True


def update_hosts_file(old_name: str, new_name: str) -> bool:
    """/etc/hosts dosyasını güncelle."""
    print(f"[2/5] /etc/hosts dosyası güncelleniyor...")

    hosts_path = "/etc/hosts"
    try:
        with open(hosts_path, "r") as f:
            content = f.read()

        # Eski hostname'i yeni ile değiştir
        updated_content = content.replace(old_name, new_name)

        with open(hosts_path, "w") as f:
            f.write(updated_content)

        print(f"  ✓ /etc/hosts güncellendi")
        return True
    except Exception as e:
        print(f"  ✗ /etc/hosts güncellenemedi: {e}")
        return False


def update_hostapd_conf(old_name: str, new_name: str) -> bool:
    """hostapd.conf dosyasındaki SSID'yi güncelle."""
    print(f"[3/5] hostapd.conf (WiFi AP SSID) güncelleniyor...")

    hostapd_paths = [
        "/etc/hostapd/hostapd.conf",
        "/etc/hostapd.conf"
    ]

    updated = False
    for path in hostapd_paths:
        if not os.path.exists(path):
            continue

        try:
            with open(path, "r") as f:
                lines = f.readlines()

            new_lines = []
            for line in lines:
                # ssid satırını bul ve güncelle
                if line.strip().startswith("ssid=") and not line.strip().startswith("ssid2="):
                    # Eski SSID'yi yeni ile değiştir
                    if old_name in line:
                        new_lines.append(line.replace(old_name, new_name))
                        print(f"  ✓ {path} içinde SSID güncellendi")
                        updated = True
                    else:
                        # Doğrudan yeni ismi ayarla
                        new_lines.append(f"ssid={new_name}\n")
                        print(f"  ✓ {path} içinde SSID ayarlandı: {new_name}")
                        updated = True
                else:
                    new_lines.append(line)

            with open(path, "w") as f:
                f.writelines(new_lines)

        except Exception as e:
            print(f"  ✗ {path} güncellenemedi: {e}")
            continue

    if not updated:
        print("  ! hostapd.conf dosyası bulunamadı veya güncellenemedi")

    return updated


def restart_hostapd() -> bool:
    """hostapd servisini yeniden başlat."""
    print(f"[4/5] hostapd servisi yeniden başlatılıyor...")

    # Önce daemon-reload
    run_command(["systemctl", "daemon-reload"], check=False)

    # hostapd'yi restart et
    success, output = run_command(["systemctl", "restart", "hostapd"], check=False)
    if success:
        print("  ✓ hostapd yeniden başlatıldı")
        return True
    else:
        print(f"  ! hostapd yeniden başlatılamadı: {output}")
        return False


def update_avahi_if_exists(new_name: str) -> bool:
    """Avahi/mDNS yapılandırmasını güncelle (varsa)."""
    print(f"[5/5] Avahi/mDNS yapılandırması kontrol ediliyor...")

    avahi_path = "/etc/avahi/avahi-daemon.conf"
    if not os.path.exists(avahi_path):
        print("  - Avahi kurulu değil, atlanıyor")
        return True

    try:
        with open(avahi_path, "r") as f:
            lines = f.readlines()

        new_lines = []
        updated = False
        for line in lines:
            if line.strip().startswith("host-name="):
                new_lines.append(f"host-name={new_name}\n")
                updated = True
            else:
                new_lines.append(line)

        if updated:
            with open(avahi_path, "w") as f:
                f.writelines(new_lines)
            print("  ✓ Avahi yapılandırması güncellendi")

            # Avahi'yi yeniden başlat
            run_command(["systemctl", "restart", "avahi-daemon"], check=False)
        else:
            print("  - Avahi'de host-name bulunamadı")

        return True
    except Exception as e:
        print(f"  ! Avahi güncellenemedi: {e}")
        return True  # Kritik değil


def main():
    parser = argparse.ArgumentParser(
        description="Orange Pi cihaz adını değiştir (hostname, hostapd SSID vb.)"
    )
    parser.add_argument(
        "new_name",
        nargs="?",
        help="Yeni cihaz adı (örn: RTCLARY20054)"
    )
    parser.add_argument(
        "--old",
        help="Eski cihaz adı (belirtilmezse otomatik algılanır)"
    )
    parser.add_argument(
        "--new",
        dest="new_name_flag",
        help="Yeni cihaz adı (alternatif parametre)"
    )

    args = parser.parse_args()

    # Yeni adı belirle
    new_name = args.new_name or args.new_name_flag
    if not new_name:
        parser.print_help()
        print("\n❌ Hata: Yeni cihaz adı belirtilmedi!")
        print("Örnek kullanım: sudo python3 change_device_name.py RTCLARY20054")
        sys.exit(1)

    # Root kontrolü
    if os.geteuid() != 0:
        print("❌ Bu script root yetkisi ile çalıştırılmalıdır!")
        print(f"Lütfen şunu kullanın: sudo python3 {sys.argv[0]} {new_name}")
        sys.exit(1)

    # Eski adı belirle
    old_name = args.old or get_current_hostname()

    print("=" * 60)
    print("Orange Pi Cihaz Adı Değiştirme")
    print("=" * 60)
    print(f"Eski ad: {old_name}")
    print(f"Yeni ad: {new_name}")
    print("=" * 60)
    print()

    if old_name == new_name:
        print("⚠️  Eski ve yeni ad aynı, işlem yapılmadı.")
        sys.exit(0)

    # Onay al
    try:
        response = input(f"Devam etmek istiyor musunuz? (E/h): ").strip().lower()
        if response not in ["e", "evet", "yes", "y"]:
            print("İşlem iptal edildi.")
            sys.exit(0)
    except KeyboardInterrupt:
        print("\nİşlem iptal edildi.")
        sys.exit(0)

    print()

    # İşlemleri yap
    success = True

    success &= update_hostname(new_name)
    success &= update_hosts_file(old_name, new_name)
    success &= update_hostapd_conf(old_name, new_name)
    success &= restart_hostapd()
    success &= update_avahi_if_exists(new_name)

    print()
    print("=" * 60)
    if success:
        print("✅ Cihaz adı başarıyla değiştirildi!")
        print()
        print("⚠️  Değişikliklerin tam olarak etkili olması için cihazı")
        print("   yeniden başlatmanız önerilir:")
        print("   sudo reboot")
    else:
        print("⚠️  Bazı adımlar tamamlanamadı. Lütfen yukarıdaki hataları kontrol edin.")
    print("=" * 60)


if __name__ == "__main__":
    main()

