#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Kamera Kilit Mekanizması
QR okuma modu aktifken kamerayı kilitler
"""

import os
import time
import logging

LOCK_FILE = "/tmp/camera_qr_lock"
LOCK_TIMEOUT = 60  # saniye

logger = logging.getLogger("CameraLock")

def acquire_camera_lock():
    """Kamera kilidini al (QR okuma modu için)"""
    try:
        with open(LOCK_FILE, 'w') as f:
            f.write(str(os.getpid()))
        logger.info("Kamera kilidi alındı")
        return True
    except Exception as e:
        logger.error(f"Kamera kilidi alınamadı: {e}")
        return False

def release_camera_lock():
    """Kamera kilidini serbest bırak"""
    try:
        if os.path.exists(LOCK_FILE):
            os.remove(LOCK_FILE)
        logger.info("Kamera kilidi serbest bırakıldı")
        return True
    except Exception as e:
        logger.error(f"Kamera kilidi serbest bırakılamadı: {e}")
        return False

def is_camera_locked():
    """Kamera kilitli mi kontrol et"""
    if not os.path.exists(LOCK_FILE):
        return False

    try:
        # Kilit dosyasının yaşını kontrol et
        lock_age = time.time() - os.path.getmtime(LOCK_FILE)

        # Eski kilit dosyasını sil (timeout aşıldıysa)
        if lock_age > LOCK_TIMEOUT:
            logger.warning(f"Eski kilit dosyası siliniyor (yaş: {lock_age:.0f}s)")
            os.remove(LOCK_FILE)
            return False

        return True
    except Exception as e:
        logger.error(f"Kilit kontrolü hatası: {e}")
        return False

def wait_camera_unlock(timeout=30):
    """Kamera kilidinin açılmasını bekle"""
    start_time = time.time()
    while is_camera_locked():
        if (time.time() - start_time) > timeout:
            logger.warning("Kamera kilidi timeout")
            return False
        time.sleep(0.5)
    return True

