#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
QR Kod Okuma ve Parse Test Script - Komple Test
"""

import cv2
from pyzbar import pyzbar
import re
import os

def detect_qr_with_preprocessing(frame):
    """
    Görüntü ön işleme teknikleri kullanarak QR kod tespit et.
    """
    # 1. Orijinal frame'de dene
    decoded_objects = pyzbar.decode(frame)
    if decoded_objects:
        return decoded_objects, "orijinal"

    # 2. Gri tonlama
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    decoded_objects = pyzbar.decode(gray)
    if decoded_objects:
        return decoded_objects, "gri"

    # 3. CLAHE
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8,8))
    enhanced = clahe.apply(gray)
    decoded_objects = pyzbar.decode(enhanced)
    if decoded_objects:
        return decoded_objects, "CLAHE"

    # 4. Otsu threshold
    _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    decoded_objects = pyzbar.decode(thresh)
    if decoded_objects:
        return decoded_objects, "threshold"

    # 5. Adaptive threshold
    adaptive = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                     cv2.THRESH_BINARY, 11, 2)
    decoded_objects = pyzbar.decode(adaptive)
    if decoded_objects:
        return decoded_objects, "adaptive"

    # 6. Histogram eşitleme
    equalized = cv2.equalizeHist(gray)
    decoded_objects = pyzbar.decode(equalized)
    if decoded_objects:
        return decoded_objects, "equalized"

    # 7. Gaussian Blur + Threshold
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    _, thresh_blur = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    decoded_objects = pyzbar.decode(thresh_blur)
    if decoded_objects:
        return decoded_objects, "blur+threshold"

    # 8. Negatif görüntü
    inverted = cv2.bitwise_not(gray)
    decoded_objects = pyzbar.decode(inverted)
    if decoded_objects:
        return decoded_objects, "inverted"

    return [], None


def parse_qr_data(qr_data):
    """QR kod verisini parse et"""
    if not qr_data:
        return None, "QR kod verisi boş"

    qr_data = qr_data.strip()

    # AP Mode
    ap_pattern = r'^APMODE(5g|2\.4g)ch(\d+)$'
    ap_match = re.match(ap_pattern, qr_data, re.IGNORECASE)

    if ap_match:
        band = ap_match.group(1).lower()
        channel = int(ap_match.group(2))

        if band == "5g":
            hw_mode = "a"
            valid_channels = [36, 40, 44, 48, 52, 56, 60, 64, 100, 104, 108, 112, 116, 120, 124, 128, 132, 136, 140, 149, 153, 157, 161, 165]
        elif band == "2.4g":
            hw_mode = "g"
            valid_channels = list(range(1, 15))
        else:
            return None, f"Geçersiz band: {band}"

        if channel not in valid_channels:
            return None, f"Geçersiz kanal: {channel}"

        config = {
            'mode': 'ap',
            'band': band,
            'hw_mode': hw_mode,
            'channel': channel
        }
        return config, None

    # WiFi QR
    if qr_data.upper().startswith('WIFI:'):
        if not qr_data.endswith(';;'):
            if qr_data.endswith(';'):
                qr_data += ';'
            else:
                qr_data += ';;'

        def unescape_wifi(s):
            s = s.replace(r'\;', '\x00')
            s = s.replace(r'\:', '\x01')
            s = s.replace(r'\,', '\x02')
            s = s.replace(r'\\', '\x03')
            return s

        def restore_wifi(s):
            s = s.replace('\x00', ';')
            s = s.replace('\x01', ':')
            s = s.replace('\x02', ',')
            s = s.replace('\x03', '\\')
            return s

        params = {}
        try:
            content = qr_data[5:]
            if content.endswith(';;'):
                content = content[:-2]
            elif content.endswith(';'):
                content = content[:-1]

            content_escaped = unescape_wifi(content)
            parts = content_escaped.split(';')

            for part in parts:
                if not part.strip():
                    continue
                if ':' in part:
                    key, value = part.split(':', 1)
                    key = key.strip()
                    value = restore_wifi(value.strip())
                    params[key] = value

            if 'T' not in params:
                return None, "WiFi QR eksik parametre: T"
            if 'S' not in params:
                return None, "WiFi QR eksik parametre: S"

            security = params['T'].upper()
            ssid = params['S']
            password = params.get('P', '')
            hidden = params.get('H', 'false').lower() == 'true'

            if not ssid:
                return None, "SSID boş olamaz"

            if security == 'NOPASS':
                password = ''

            config = {
                'mode': 'sta',
                'ssid': ssid,
                'password': password,
                'security': security,
                'hidden': hidden
            }
            return config, None

        except Exception as e:
            return None, f"WiFi QR parse hatası: {e}"

    return None, "Tanınmayan QR formatı"


def test_qr_image(image_path):
    """QR kod resmini test et"""
    print(f"\n{'='*70}")
    print(f"Test: {image_path}")
    print(f"{'='*70}")

    if not os.path.exists(image_path):
        print(f"❌ Dosya bulunamadı")
        return None

    # Resmi oku
    img = cv2.imread(image_path)
    if img is None:
        print(f"❌ Resim okunamadı")
        return None

    print(f"✓ Resim boyutu: {img.shape}")

    # QR kod tespit et
    decoded_objects, method = detect_qr_with_preprocessing(img)

    if not decoded_objects:
        print(f"❌ QR kod tespit edilemedi")
        return None

    # Her tespit edilen QR için
    for obj in decoded_objects:
        qr_data = obj.data.decode('utf-8')
        print(f"\n✓ QR Kod bulundu ({method} yöntemi ile)")
        print(f"  Ham veri: {qr_data}")
        print(f"  Tip: {obj.type}")
        print(f"  Konum: {obj.rect}")

        # Parse et
        config, error = parse_qr_data(qr_data)

        if error:
            print(f"  ❌ Parse hatası: {error}")
            return None

        print(f"\n  ✓ Parse başarılı!")
        print(f"  Yapılandırma:")
        for key, value in config.items():
            if key == 'password' and value:
                print(f"    {key}: ***")
            else:
                print(f"    {key}: {value}")

        return config

    return None


if __name__ == "__main__":
    # Test edilecek QR kod resimleri
    test_images = [
        "simcleverY.png",
        "simclever.png",
        "5gch36.png",
        "2_4gch6.png",
        "deneme.png"
    ]

    results = {}

    for img_name in test_images:
        config = test_qr_image(img_name)
        results[img_name] = config

    # Özet
    print(f"\n\n{'='*70}")
    print("TEST SONUÇLARI ÖZETİ")
    print(f"{'='*70}")

    success_count = 0
    fail_count = 0

    for img_name, config in results.items():
        if config:
            status = "✓ BAŞARILI"
            success_count += 1
            mode = config.get('mode', 'Unknown')
            if mode == 'ap':
                detail = f"{config['band']} kanal {config['channel']}"
            elif mode == 'sta':
                detail = f"SSID: {config['ssid']}"
            else:
                detail = ""
        else:
            status = "❌ BAŞARISIZ"
            fail_count += 1
            detail = ""

        print(f"{status:15} {img_name:20} {detail}")

    print(f"\n{'='*70}")
    print(f"Toplam: {len(test_images)} test")
    print(f"Başarılı: {success_count}")
    print(f"Başarısız: {fail_count}")
    print(f"Başarı oranı: {100*success_count/len(test_images):.1f}%")
    print(f"{'='*70}")

