#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
QR Kod Okuma Test Script
"""

import cv2
from pyzbar import pyzbar
import numpy as np
from PIL import Image

def read_qr_from_image(image_path):
    """Resim dosyasından QR kod oku"""
    print(f"\n{'='*60}")
    print(f"Test ediliyor: {image_path}")
    print(f"{'='*60}")

    try:
        # OpenCV ile oku
        img = cv2.imread(image_path)
        if img is None:
            print(f"❌ Resim okunamadı: {image_path}")
            return None

        print(f"✓ Resim boyutu: {img.shape}")

        # Orijinal resimde QR kod ara
        decoded_objects = pyzbar.decode(img)
        if decoded_objects:
            for obj in decoded_objects:
                data = obj.data.decode('utf-8')
                print(f"✓ QR Kod bulundu (orijinal): {data}")
                print(f"  Tip: {obj.type}")
                print(f"  Konum: {obj.rect}")
                return data

        print("⚠ Orijinal resimde QR kod bulunamadı, farklı yöntemler deneniyor...")

        # Gri tonlama yap
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        decoded_objects = pyzbar.decode(gray)
        if decoded_objects:
            for obj in decoded_objects:
                data = obj.data.decode('utf-8')
                print(f"✓ QR Kod bulundu (gri): {data}")
                return data

        # CLAHE ile kontrast iyileştirme
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8,8))
        enhanced = clahe.apply(gray)
        decoded_objects = pyzbar.decode(enhanced)
        if decoded_objects:
            for obj in decoded_objects:
                data = obj.data.decode('utf-8')
                print(f"✓ QR Kod bulundu (CLAHE): {data}")
                return data

        # Threshold
        _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        decoded_objects = pyzbar.decode(thresh)
        if decoded_objects:
            for obj in decoded_objects:
                data = obj.data.decode('utf-8')
                print(f"✓ QR Kod bulundu (threshold): {data}")
                return data

        # Adaptive threshold
        adaptive = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                         cv2.THRESH_BINARY, 11, 2)
        decoded_objects = pyzbar.decode(adaptive)
        if decoded_objects:
            for obj in decoded_objects:
                data = obj.data.decode('utf-8')
                print(f"✓ QR Kod bulundu (adaptive): {data}")
                return data

        # Histogram eşitleme
        equalized = cv2.equalizeHist(gray)
        decoded_objects = pyzbar.decode(equalized)
        if decoded_objects:
            for obj in decoded_objects:
                data = obj.data.decode('utf-8')
                print(f"✓ QR Kod bulundu (equalized): {data}")
                return data

        # Blur + threshold
        blurred = cv2.GaussianBlur(gray, (5, 5), 0)
        _, thresh_blur = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        decoded_objects = pyzbar.decode(thresh_blur)
        if decoded_objects:
            for obj in decoded_objects:
                data = obj.data.decode('utf-8')
                print(f"✓ QR Kod bulundu (blur+threshold): {data}")
                return data

        # Ters çevir (negatif)
        inverted = cv2.bitwise_not(gray)
        decoded_objects = pyzbar.decode(inverted)
        if decoded_objects:
            for obj in decoded_objects:
                data = obj.data.decode('utf-8')
                print(f"✓ QR Kod bulundu (inverted): {data}")
                return data

        print("❌ Hiçbir yöntemle QR kod okunamadı!")
        return None

    except Exception as e:
        print(f"❌ Hata: {e}")
        import traceback
        traceback.print_exc()
        return None

if __name__ == "__main__":
    import os

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
        if os.path.exists(img_name):
            result = read_qr_from_image(img_name)
            results[img_name] = result
        else:
            print(f"\n❌ Dosya bulunamadı: {img_name}")
            results[img_name] = None

    # Özet
    print(f"\n\n{'='*60}")
    print("SONUÇLAR ÖZETİ")
    print(f"{'='*60}")
    for img_name, result in results.items():
        status = "✓" if result else "❌"
        print(f"{status} {img_name}: {result}")

