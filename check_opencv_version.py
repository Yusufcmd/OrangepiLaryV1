#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OpenCV Versiyon Kontrol ve Test Scripti
Bu script, OpenCV versiyonunuzu ve logging desteğini kontrol eder.
"""

import sys

def check_opencv():
    """OpenCV versiyonunu ve logging desteğini kontrol et"""
    print("=" * 60)
    print("OpenCV Versiyon ve Özellik Kontrolü")
    print("=" * 60)
    print()

    # OpenCV import kontrolü
    try:
        import cv2
        print(f"✓ OpenCV başarıyla yüklendi")
        print(f"  Versiyon: {cv2.__version__}")
    except ImportError as e:
        print(f"✗ OpenCV yüklenemedi: {e}")
        return False

    print()

    # cv2.utils kontrolü
    if hasattr(cv2, 'utils'):
        print("✓ cv2.utils mevcut")

        # cv2.utils.logging kontrolü
        if hasattr(cv2.utils, 'logging'):
            print("✓ cv2.utils.logging mevcut")

            try:
                # Mevcut log seviyesini al
                current_level = cv2.utils.logging.getLogLevel()
                print(f"  Mevcut log seviyesi: {current_level}")

                # Log seviyelerini test et
                print("\n  Log seviyeleri:")
                levels = {
                    'LOG_LEVEL_SILENT': 0,
                    'LOG_LEVEL_FATAL': 1,
                    'LOG_LEVEL_ERROR': 2,
                    'LOG_LEVEL_WARNING': 3,
                    'LOG_LEVEL_INFO': 4,
                    'LOG_LEVEL_DEBUG': 5,
                    'LOG_LEVEL_VERBOSE': 6
                }

                for level_name, level_value in levels.items():
                    if hasattr(cv2.utils.logging, level_name):
                        actual_value = getattr(cv2.utils.logging, level_name)
                        print(f"    ✓ {level_name} = {actual_value}")
                    else:
                        print(f"    ✗ {level_name} mevcut değil")

                # Test: Log seviyesini değiştir
                print("\n  Test: Log seviyesini değiştirme...")
                try:
                    cv2.utils.logging.setLogLevel(cv2.utils.logging.LOG_LEVEL_ERROR)
                    print("    ✓ Log seviyesi ERROR olarak ayarlandı")
                    cv2.utils.logging.setLogLevel(current_level)
                    print("    ✓ Log seviyesi eski haline döndürüldü")
                except Exception as e:
                    print(f"    ✗ Hata: {e}")

            except Exception as e:
                print(f"  ✗ cv2.utils.logging kullanılamıyor: {e}")
        else:
            print("✗ cv2.utils.logging MEVCUT DEĞİL")
            print("  → Bu OpenCV versiyonu logging API'sini desteklemiyor")
            print("  → Alternatif: OPENCV_LOG_LEVEL environment variable kullanılabilir")
    else:
        print("✗ cv2.utils MEVCUT DEĞİL")

    print()

    # Kamera testi (opsiyonel)
    print("Kamera testi yapılsın mı? (Bu biraz zaman alabilir)")
    response = input("(e/h): ").lower().strip()

    if response == 'e':
        print("\nKamera testi başlatılıyor...")
        import os

        # OpenCV loglarını kapat
        os.environ['OPENCV_LOG_LEVEL'] = 'ERROR'

        for idx in range(3):
            try:
                print(f"  Kamera {idx} test ediliyor...", end=" ")
                cap = cv2.VideoCapture(idx)
                if cap.isOpened():
                    ret, frame = cap.read()
                    if ret and frame is not None:
                        print(f"✓ BAŞARILI (Çözünürlük: {frame.shape[1]}x{frame.shape[0]})")
                    else:
                        print("✗ Frame okunamadı")
                    cap.release()
                else:
                    print("✗ Açılamadı")
            except Exception as e:
                print(f"✗ Hata: {e}")

    print()
    print("=" * 60)
    print("Kontrol tamamlandı!")
    print("=" * 60)

    return True

def main():
    """Ana fonksiyon"""
    try:
        check_opencv()
    except KeyboardInterrupt:
        print("\n\nKullanıcı tarafından iptal edildi.")
        sys.exit(0)
    except Exception as e:
        print(f"\nBeklenmeyen hata: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()

