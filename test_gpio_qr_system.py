#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GPIO PWM Monitor Test Script
QR kod okuma ve WiFi yapılandırma testi
"""

import sys
import time

def test_imports():
    """Gerekli modüllerin import kontrolü"""
    print("=" * 60)
    print("MODÜL İMPORT TESTİ")
    print("=" * 60)

    modules = {
        "cv2": "opencv-python",
        "pyzbar": "pyzbar",
        "gpiod": "python3-libgpiod (sistem paketi)",
        "camera_lock": "camera_lock.py (lokal)"
    }

    failed = []

    for module, package in modules.items():
        try:
            if module == "pyzbar":
                from pyzbar import pyzbar
                print(f"✓ {module:20s} - OK ({package})")
            else:
                __import__(module)
                print(f"✓ {module:20s} - OK ({package})")
        except ImportError as e:
            print(f"✗ {module:20s} - HATA: {e}")
            failed.append((module, package))

    print()
    if failed:
        print("Eksik modüller için kurulum:")
        for module, package in failed:
            if "sistem paketi" in package:
                print(f"  sudo apt-get install -y {package.split()[0]}")
            else:
                print(f"  pip3 install {package}")
        return False
    else:
        print("✓ Tüm modüller yüklü")
        return True

def test_qr_parser():
    """QR kod parser testleri"""
    print("\n" + "=" * 60)
    print("QR KOD PARSER TESTİ")
    print("=" * 60)

    # Import parser function
    sys.path.insert(0, '.')
    from recovery_gpio_monitor import parse_qr_code

    test_cases = [
        ("APMODE5gch36", {"mode": "ap", "band": "5", "channel": "36"}),
        ("APMODE2.4gch6", {"mode": "ap", "band": "2.4", "channel": "6"}),
        ("WIFI:T:WPA;S:TestNetwork;P:TestPass123;H:false;;", {"mode": "sta", "ssid": "TestNetwork", "password": "TestPass123"}),
        ("WIFI:T:nopass;S:OpenNetwork;H:false;;", {"mode": "sta", "ssid": "OpenNetwork", "password": ""}),
    ]

    passed = 0
    failed = 0

    for qr_data, expected in test_cases:
        result = parse_qr_code(qr_data)

        if result and result.get("mode") == expected.get("mode"):
            print(f"✓ {qr_data[:40]:40s} - OK")
            passed += 1
        else:
            print(f"✗ {qr_data[:40]:40s} - HATA")
            print(f"  Beklenen: {expected}")
            print(f"  Alınan:   {result}")
            failed += 1

    print(f"\nSonuç: {passed} başarılı, {failed} başarısız")
    return failed == 0

def test_camera_lock():
    """Kamera kilit mekanizması testi"""
    print("\n" + "=" * 60)
    print("KAMERA KİLİT MEKANİZMASI TESTİ")
    print("=" * 60)

    import camera_lock

    # Test 1: Kilit alınması
    print("Test 1: Kilit alınması")
    if camera_lock.acquire_camera_lock():
        print("  ✓ Kilit başarıyla alındı")
    else:
        print("  ✗ Kilit alınamadı")
        return False

    # Test 2: Kilit kontrolü
    print("Test 2: Kilit kontrolü")
    if camera_lock.is_camera_locked():
        print("  ✓ Kilit aktif olarak algılandı")
    else:
        print("  ✗ Kilit algılanamadı")
        return False

    # Test 3: Kilit serbest bırakılması
    print("Test 3: Kilit serbest bırakılması")
    if camera_lock.release_camera_lock():
        print("  ✓ Kilit başarıyla serbest bırakıldı")
    else:
        print("  ✗ Kilit serbest bırakılamadı")
        return False

    # Test 4: Kilit kontrol (serbest olmalı)
    print("Test 4: Kilit kontrolü (serbest)")
    if not camera_lock.is_camera_locked():
        print("  ✓ Kilit serbest olarak algılandı")
    else:
        print("  ✗ Kilit hala aktif görünüyor")
        return False

    print("\n✓ Tüm kamera kilit testleri başarılı")
    return True

def test_qr_images():
    """QR kod görüntülerini okuma testi"""
    print("\n" + "=" * 60)
    print("QR KOD GÖRÜNTÜ OKUMA TESTİ")
    print("=" * 60)

    try:
        import cv2
        from pyzbar import pyzbar
        from recovery_gpio_monitor import parse_qr_code

        qr_files = ['simclever.png', '5gch36.png', 'deneme.png', '2_4gch6.png']

        found = 0

        for qr_file in qr_files:
            try:
                img = cv2.imread(qr_file)
                if img is None:
                    print(f"⚠ {qr_file:20s} - Dosya bulunamadı veya okunamadı")
                    continue

                result = pyzbar.decode(img)
                if result:
                    qr_data = result[0].data.decode('utf-8')
                    config = parse_qr_code(qr_data)

                    if config:
                        mode = config.get('mode')
                        if mode == 'ap':
                            info = f"AP Mode - Band: {config.get('band')}GHz, Ch: {config.get('channel')}"
                        elif mode == 'sta':
                            info = f"STA Mode - SSID: {config.get('ssid')}"
                        else:
                            info = "Unknown mode"

                        print(f"✓ {qr_file:20s} - {info}")
                        found += 1
                    else:
                        print(f"✗ {qr_file:20s} - Parse hatası: {qr_data}")
                else:
                    print(f"⚠ {qr_file:20s} - QR kod algılanamadı")
            except Exception as e:
                print(f"✗ {qr_file:20s} - Hata: {e}")

        print(f"\n{found}/{len(qr_files)} QR kod başarıyla okundu")
        return found > 0

    except ImportError as e:
        print(f"✗ Modül yükleme hatası: {e}")
        return False

def main():
    """Ana test fonksiyonu"""
    print("\n" + "=" * 60)
    print("GPIO PWM MONITOR - SİSTEM TESTİ")
    print("=" * 60)

    tests = [
        ("Modül İmport", test_imports),
        ("QR Parser", test_qr_parser),
        ("Kamera Kilit", test_camera_lock),
        ("QR Görüntü Okuma", test_qr_images),
    ]

    results = []

    for test_name, test_func in tests:
        try:
            result = test_func()
            results.append((test_name, result))
        except Exception as e:
            print(f"\n✗ {test_name} testi sırasında hata: {e}")
            results.append((test_name, False))

        time.sleep(0.5)

    # Özet
    print("\n" + "=" * 60)
    print("TEST SONUÇLARI")
    print("=" * 60)

    passed = sum(1 for _, result in results if result)
    total = len(results)

    for test_name, result in results:
        status = "✓ BAŞARILI" if result else "✗ BAŞARISIZ"
        print(f"{test_name:30s} - {status}")

    print("=" * 60)
    print(f"TOPLAM: {passed}/{total} test başarılı")
    print("=" * 60)

    if passed == total:
        print("\n✓ Tüm testler başarıyla tamamlandı!")
        return 0
    else:
        print(f"\n⚠ {total - passed} test başarısız oldu")
        return 1

if __name__ == "__main__":
    sys.exit(main())

