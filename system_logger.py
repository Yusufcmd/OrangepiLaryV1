#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Merkezi Sistem Loglama Modülü
==============================
Wifi değişiklikleri, bağlanan cihazlar, video kayıtları ve diğer
sistem olaylarını detaylı şekilde loglar.
"""

import os
import logging
import json
from datetime import datetime
from typing import Optional, Dict, Any
from functools import wraps
import threading

# Log dizini
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
LOG_DIR = os.path.join(BASE_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)

# Farklı kategoriler için ayrı log dosyaları
LOG_FILES = {
    "wifi": os.path.join(LOG_DIR, f"wifi_{datetime.now().strftime('%Y-%m')}.log"),
    "connections": os.path.join(LOG_DIR, f"connections_{datetime.now().strftime('%Y-%m')}.log"),
    "video": os.path.join(LOG_DIR, f"video_{datetime.now().strftime('%Y-%m')}.log"),
    "auth": os.path.join(LOG_DIR, f"auth_{datetime.now().strftime('%Y-%m')}.log"),
    "system": os.path.join(LOG_DIR, f"system_{datetime.now().strftime('%Y-%m')}.log"),
    "api": os.path.join(LOG_DIR, f"api_{datetime.now().strftime('%Y-%m')}.log"),
}

# Logger'lar
_loggers = {}
_lock = threading.Lock()


def get_logger(category: str = "system") -> logging.Logger:
    """Belirtilen kategori için logger döndürür"""
    with _lock:
        if category not in _loggers:
            log_file = LOG_FILES.get(category, LOG_FILES["system"])

            logger = logging.getLogger(f"clary.{category}")
            logger.setLevel(logging.DEBUG)
            logger.propagate = False

            # Dosya handler
            fh = logging.FileHandler(log_file, mode="a", encoding="utf-8")
            fh.setLevel(logging.DEBUG)

            # Detaylı format
            formatter = logging.Formatter(
                "%(asctime)s | %(levelname)-8s | %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S"
            )
            fh.setFormatter(formatter)

            logger.addHandler(fh)
            _loggers[category] = logger

        return _loggers[category]


def log_event(category: str, event_type: str, data: Dict[str, Any], level: str = "INFO"):
    """Olay loglar (JSON formatında)"""
    logger = get_logger(category)

    log_entry = {
        "event_type": event_type,
        "timestamp": datetime.now().isoformat(),
        "data": data
    }

    message = f"{event_type} | {json.dumps(data, ensure_ascii=False)}"

    if level == "DEBUG":
        logger.debug(message)
    elif level == "INFO":
        logger.info(message)
    elif level == "WARNING":
        logger.warning(message)
    elif level == "ERROR":
        logger.error(message)
    elif level == "CRITICAL":
        logger.critical(message)
    else:
        logger.info(message)


# ==================== WiFi İşlemleri ====================

def log_wifi_change(ssid: Optional[str] = None, password_changed: bool = False,
                    band: Optional[str] = None, channel: Optional[int] = None,
                    success: bool = True, error: Optional[str] = None,
                    user: Optional[str] = None):
    """WiFi yapılandırma değişikliklerini loglar"""
    data = {}

    if ssid:
        data["ssid"] = ssid
    if password_changed:
        data["password_changed"] = True
    if band:
        data["band"] = band
    if channel:
        data["channel"] = channel
    if user:
        data["user"] = user

    data["success"] = success
    if error:
        data["error"] = error

    level = "INFO" if success else "ERROR"
    log_event("wifi", "WIFI_CONFIG_CHANGE", data, level)


def log_hostapd_restart(success: bool, message: Optional[str] = None, user: Optional[str] = None):
    """hostapd yeniden başlatma işlemlerini loglar"""
    data = {
        "success": success,
        "service": "hostapd"
    }
    if message:
        data["message"] = message
    if user:
        data["user"] = user

    level = "INFO" if success else "ERROR"
    log_event("wifi", "HOSTAPD_RESTART", data, level)


def log_ap_client_connection(mac_address: str, ip_address: Optional[str] = None,
                             hostname: Optional[str] = None, connected: bool = True):
    """AP'ye bağlanan/ayrılan cihazları loglar"""
    data = {
        "mac_address": mac_address,
        "action": "CONNECTED" if connected else "DISCONNECTED"
    }
    if ip_address:
        data["ip_address"] = ip_address
    if hostname:
        data["hostname"] = hostname

    log_event("connections", "AP_CLIENT_EVENT", data, "INFO")


# ==================== Video Kayıt İşlemleri ====================

def log_video_recording_start(session_name: str, file_path: str,
                              resolution: Optional[tuple] = None, fps: Optional[float] = None):
    """Video kaydı başlangıcını loglar"""
    data = {
        "action": "START",
        "session": session_name,
        "file_path": file_path
    }
    if resolution:
        data["resolution"] = f"{resolution[0]}x{resolution[1]}"
    if fps:
        data["fps"] = fps

    log_event("video", "VIDEO_RECORDING", data, "INFO")


def log_video_recording_stop(session_name: str, file_path: str,
                             duration: Optional[float] = None, file_size: Optional[int] = None):
    """Video kaydı durdurulmasını loglar"""
    data = {
        "action": "STOP",
        "session": session_name,
        "file_path": file_path
    }
    if duration:
        data["duration_sec"] = round(duration, 2)
    if file_size:
        data["file_size_mb"] = round(file_size / (1024 * 1024), 2)

    log_event("video", "VIDEO_RECORDING", data, "INFO")


def log_video_file_operation(operation: str, file_path: str, success: bool,
                             user: Optional[str] = None, error: Optional[str] = None,
                             new_name: Optional[str] = None):
    """Video dosya işlemlerini loglar (silme, yeniden adlandırma, indirme)"""
    data = {
        "operation": operation,
        "file_path": file_path,
        "success": success
    }
    if user:
        data["user"] = user
    if error:
        data["error"] = error
    if new_name:
        data["new_name"] = new_name

    level = "INFO" if success else "ERROR"
    log_event("video", "VIDEO_FILE_OPERATION", data, level)


# ==================== Kimlik Doğrulama ====================

def log_auth_attempt(username: str, success: bool, ip_address: Optional[str] = None,
                    user_agent: Optional[str] = None, reason: Optional[str] = None):
    """Giriş denemelerini loglar"""
    data = {
        "username": username,
        "success": success
    }
    if ip_address:
        data["ip_address"] = ip_address
    if user_agent:
        data["user_agent"] = user_agent
    if reason:
        data["reason"] = reason

    level = "INFO" if success else "WARNING"
    log_event("auth", "LOGIN_ATTEMPT", data, level)


def log_session_event(username: str, event_type: str, ip_address: Optional[str] = None):
    """Oturum olaylarını loglar (logout, timeout vb.)"""
    data = {
        "username": username,
        "event": event_type
    }
    if ip_address:
        data["ip_address"] = ip_address

    log_event("auth", "SESSION_EVENT", data, "INFO")


# ==================== API İşlemleri ====================

def log_api_request(endpoint: str, method: str, success: bool,
                   ip_address: Optional[str] = None, user: Optional[str] = None,
                   response_time: Optional[float] = None, error: Optional[str] = None):
    """API isteklerini loglar"""
    data = {
        "endpoint": endpoint,
        "method": method,
        "success": success
    }
    if ip_address:
        data["ip_address"] = ip_address
    if user:
        data["user"] = user
    if response_time:
        data["response_time_ms"] = round(response_time * 1000, 2)
    if error:
        data["error"] = error

    level = "INFO" if success else "WARNING"
    log_event("api", "API_REQUEST", data, level)


# ==================== Sistem Olayları ====================

def log_system_event(event_type: str, message: str, level: str = "INFO", **extra_data):
    """Genel sistem olaylarını loglar"""
    data = {"message": message}
    data.update(extra_data)
    log_event("system", event_type, data, level)


def log_gpio_event(pin: int, state: str, event_type: str):
    """GPIO olaylarını loglar"""
    data = {
        "pin": pin,
        "state": state,
        "event": event_type
    }
    log_event("system", "GPIO_EVENT", data, "INFO")


def log_camera_event(event_type: str, success: bool, message: Optional[str] = None,
                    resolution: Optional[tuple] = None):
    """Kamera olaylarını loglar"""
    data = {
        "event": event_type,
        "success": success
    }
    if message:
        data["message"] = message
    if resolution:
        data["resolution"] = f"{resolution[0]}x{resolution[1]}"

    level = "INFO" if success else "ERROR"
    log_event("system", "CAMERA_EVENT", data, level)


# ==================== Dekoratörler ====================

def log_function_call(category: str = "system", log_args: bool = False):
    """Fonksiyon çağrılarını otomatik loglar"""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            func_name = func.__name__
            data = {"function": func_name}

            if log_args:
                # Hassas bilgileri filtrele
                safe_kwargs = {k: v for k, v in kwargs.items()
                              if k not in ["password", "token", "secret"]}
                if safe_kwargs:
                    data["kwargs"] = str(safe_kwargs)

            try:
                result = func(*args, **kwargs)
                data["status"] = "SUCCESS"
                log_event(category, "FUNCTION_CALL", data, "DEBUG")
                return result
            except Exception as e:
                data["status"] = "ERROR"
                data["error"] = str(e)
                log_event(category, "FUNCTION_CALL", data, "ERROR")
                raise

        return wrapper
    return decorator


# ==================== Log Analiz Fonksiyonları ====================

def get_recent_logs(category: str, limit: int = 100) -> list:
    """Belirtilen kategoriden son N log kaydını döndürür"""
    log_file = LOG_FILES.get(category)
    if not log_file or not os.path.exists(log_file):
        return []

    try:
        with open(log_file, "r", encoding="utf-8") as f:
            lines = f.readlines()
            return lines[-limit:]
    except Exception:
        return []


def get_logs_by_date(category: str, date: str) -> list:
    """Belirtilen tarihteki logları döndürür (YYYY-MM-DD formatında)"""
    log_file = LOG_FILES.get(category)
    if not log_file or not os.path.exists(log_file):
        return []

    try:
        with open(log_file, "r", encoding="utf-8") as f:
            lines = [line for line in f if date in line]
            return lines
    except Exception:
        return []


# Başlangıç logu
log_system_event("SYSTEM_START", "System logger modülü başlatıldı", "INFO")

