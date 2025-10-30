# -*- coding: utf-8 -*-
"""
Mobil Uygulama için REST API Endpoint'leri
===========================================
Bu modül, mobil uygulamaların video kayıtlarına erişmesi, yönetmesi ve
kayıt kontrolü yapması için JSON tabanlı REST API sağlar.

Tüm endpoint'ler sabit token bazlı kimlik doğrulama gerektirir.
"""

import os
import time
import logging
from functools import wraps
from typing import Optional, List, Dict
from datetime import datetime
import json

from flask import Blueprint, request, jsonify, send_file, session

# recordsVideo modülünden gerekli fonksiyonları import et
try:
    import recordsVideo
    from recordsVideo import (
        RECORDS_DIR, SESSION_NAME, SESSION_DIR,
        _recording_flag, _writer, _current_file,
        _list_sessions, _list_files, _safe_session, _safe_name,
        RECORD_FPS, FRAME_SIZE, _writer_fps
    )
    RECORDS_MODULE_AVAILABLE = True
except Exception as e:
    RECORDS_MODULE_AVAILABLE = False
    logging.error(f"recordsVideo modülü yüklenemedi: {e}")

# Sabit API Token
API_TOKEN = "ZUqwfoe1uyxZvSf2lYzH8fVDRdPP3UO3"

LOG = logging.getLogger(__name__)

# Blueprint oluştur
mobile_api_bp = Blueprint("mobile_api", __name__, url_prefix="/api/v1")


# ==================== Yardımcı Fonksiyonlar ====================

def verify_token(token: str) -> bool:
    """Token'ı sabit token ile karşılaştır"""
    return token == API_TOKEN


def token_required(f):
    """API endpoint'leri için token kontrolü decorator"""
    @wraps(f)
    def decorated(*args, **kwargs):
        token = None

        # 1. Authorization header'dan token al
        if "Authorization" in request.headers:
            auth_header = request.headers["Authorization"]
            try:
                # "Bearer TOKEN" veya sadece "TOKEN" formatını destekle
                parts = auth_header.split(" ")
                token = parts[1] if len(parts) > 1 else parts[0]
            except IndexError:
                pass

        # 2. Query parameter'dan token al (?token=xxx)
        if not token:
            token = request.args.get("token")

        # 3. URL path'inden token al (ilk argüman session_name yerine token olabilir)
        if not token and args:
            # Eğer ilk argüman token uzunluğundaysa ve token'a eşitse
            if len(args) > 0 and args[0] == API_TOKEN:
                token = args[0]
                # args'ı güncelle (token'ı kaldır)
                args = args[1:]
                kwargs["_token_from_path"] = True

        if not token:
            return jsonify({"success": False, "error": "Token bulunamadı"}), 401

        if not verify_token(token):
            return jsonify({"success": False, "error": "Token geçersiz"}), 401

        # Basit kullanıcı bilgisi ekle
        request.current_user = {"username": "rise"}
        return f(*args, **kwargs)

    return decorated


def format_size(size_bytes: int) -> str:
    """Byte cinsinden boyutu okunabilir formata çevir"""
    for unit in ["B", "KB", "MB", "GB"]:
        if size_bytes < 1024.0:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.2f} TB"


# ==================== Kimlik Doğrulama Endpoint'leri ====================

@mobile_api_bp.route("/auth/login", methods=["POST"])
def api_login():
    """
    Kullanıcı girişi yap ve sabit token döndür

    Request Body (JSON):
    {
        "username": "rise",
        "password": "simclever12345"
    }

    Response:
    {
        "success": true,
        "token": "ZUqwfoe1uyxZvSf2lYzH8fVDRdPP3UO3",
        "user": {
            "username": "rise"
        }
    }
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "JSON body gerekli"}), 400

        username = data.get("username", "").strip()
        password = data.get("password", "")

        if not username or not password:
            return jsonify({"success": False, "error": "Kullanıcı adı ve şifre gerekli"}), 400

        # User modelini import et (main.py'den)
        try:
            from main import User, verify_password
        except Exception as e:
            LOG.error(f"User modeli import edilemedi: {e}")
            return jsonify({"success": False, "error": "Sunucu hatası"}), 500

        user = User.query.filter_by(username=username).first()
        if not user or not verify_password(user.password, password):
            return jsonify({"success": False, "error": "Kullanıcı adı veya şifre hatalı"}), 401

        # Sabit token döndür
        return jsonify({
            "success": True,
            "token": API_TOKEN,
            "user": {
                "username": user.username
            }
        }), 200

    except Exception as e:
        LOG.error(f"Login hatası: {e}")
        return jsonify({"success": False, "error": "Sunucu hatası"}), 500


@mobile_api_bp.route("/auth/verify", methods=["GET"])
@token_required
def api_verify_token():
    """
    Mevcut token'ın geçerliliğini kontrol et

    Headers:
        Authorization: Bearer <token>

    Response:
    {
        "success": true,
        "user": {
            "username": "rise"
        }
    }
    """
    return jsonify({
        "success": True,
        "user": request.current_user
    }), 200


# ==================== Oturum (Session) Yönetimi ====================

@mobile_api_bp.route("/sessions", methods=["GET"])
@token_required
def api_list_sessions():
    """
    Tüm kayıt oturumlarını listele

    Response:
    {
        "success": true,
        "active_session": "oturum5",
        "sessions": [
            {
                "name": "oturum5",
                "file_count": 3,
                "total_size": 15728640,
                "total_size_formatted": "15.00 MB",
                "last_modified": 1698765432.123,
                "last_modified_formatted": "2024-10-31 14:30:32"
            }
        ]
    }
    """
    try:
        if not RECORDS_MODULE_AVAILABLE:
            return jsonify({"success": False, "error": "Kayıt modülü kullanılamıyor"}), 503

        sessions = _list_sessions()

        result = []
        for s in sessions:
            result.append({
                "name": s["name"],
                "file_count": s["count"],
                "total_size": s["size"],
                "total_size_formatted": format_size(s["size"]),
                "last_modified": s["mtime"],
                "last_modified_formatted": datetime.fromtimestamp(s["mtime"]).strftime("%Y-%m-%d %H:%M:%S")
            })

        return jsonify({
            "success": True,
            "active_session": SESSION_NAME,
            "sessions": result
        }), 200

    except Exception as e:
        LOG.error(f"Oturum listesi hatası: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@mobile_api_bp.route("/sessions/<session_name>", methods=["GET"])
@token_required
def api_session_detail(session_name):
    """
    Belirli bir oturumdaki dosyaları listele

    Response:
    {
        "success": true,
        "session": "oturum5",
        "is_active": true,
        "files": [
            {
                "name": "rec_20241031_143022.avi",
                "size": 5242880,
                "size_formatted": "5.00 MB",
                "modified": 1698765422.123,
                "modified_formatted": "2024-10-31 14:30:22",
                "download_url": "/api/v1/files/oturum5/rec_20241031_143022.avi"
            }
        ]
    }
    """
    try:
        if not RECORDS_MODULE_AVAILABLE:
            return jsonify({"success": False, "error": "Kayıt modülü kullanılamıyor"}), 503

        # Güvenlik kontrolü
        try:
            safe_session = _safe_session(session_name)
        except Exception as e:
            return jsonify({"success": False, "error": "Geçersiz oturum"}), 400

        files = _list_files(safe_session)

        result = []
        for f in files:
            result.append({
                "name": f["name"],
                "size": f["size"],
                "size_formatted": format_size(f["size"]),
                "modified": f["mtime"],
                "modified_formatted": datetime.fromtimestamp(f["mtime"]).strftime("%Y-%m-%d %H:%M:%S"),
                "download_url": f"/api/v1/files/{safe_session}/{f['name']}"
            })

        is_active = (safe_session == SESSION_NAME)

        return jsonify({
            "success": True,
            "session": safe_session,
            "is_active": is_active,
            "files": result
        }), 200

    except Exception as e:
        LOG.error(f"Oturum detay hatası: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@mobile_api_bp.route("/sessions/<session_name>", methods=["DELETE"])
@token_required
def api_delete_session(session_name):
    """
    Bir oturumu ve içindeki tüm dosyaları sil

    Response:
    {
        "success": true,
        "message": "Oturum silindi"
    }
    """
    try:
        if not RECORDS_MODULE_AVAILABLE:
            return jsonify({"success": False, "error": "Kayıt modülü kullanılamıyor"}), 503

        try:
            safe_session = _safe_session(session_name)
        except Exception:
            return jsonify({"success": False, "error": "Geçersiz oturum"}), 400

        # Aktif oturum kontrolü
        is_active = (safe_session == SESSION_NAME) and (_recording_flag.is_set() or (_writer is not None))
        if is_active:
            return jsonify({"success": False, "error": "Aktif oturum silinemez"}), 400

        import shutil
        target = os.path.join(RECORDS_DIR, safe_session)
        shutil.rmtree(target)

        return jsonify({
            "success": True,
            "message": "Oturum silindi"
        }), 200

    except Exception as e:
        LOG.error(f"Oturum silme hatası: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


# ==================== Dosya Yönetimi ====================

@mobile_api_bp.route("/files/<session_name>/<path:filename>", methods=["GET"])
@token_required
def api_download_file(session_name, filename):
    """
    Belirli bir dosyayı indir

    Response: Video dosyası (binary)
    """
    try:
        if not RECORDS_MODULE_AVAILABLE:
            return jsonify({"success": False, "error": "Kayıt modülü kullanılamıyor"}), 503

        try:
            safe_session = _safe_session(session_name)
            safe_filename = _safe_name(filename)
        except Exception:
            return jsonify({"success": False, "error": "Geçersiz dosya/oturum"}), 400

        file_path = os.path.join(RECORDS_DIR, safe_session, safe_filename)

        if not os.path.exists(file_path):
            return jsonify({"success": False, "error": "Dosya bulunamadı"}), 404

        return send_file(file_path, as_attachment=True, download_name=safe_filename)

    except Exception as e:
        LOG.error(f"Dosya indirme hatası: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@mobile_api_bp.route("/files/<session_name>/<path:filename>", methods=["DELETE"])
@token_required
def api_delete_file(session_name, filename):
    """
    Belirli bir dosyayı sil

    Response:
    {
        "success": true,
        "message": "Dosya silindi"
    }
    """
    try:
        if not RECORDS_MODULE_AVAILABLE:
            return jsonify({"success": False, "error": "Kayıt modülü kullanılamıyor"}), 503

        try:
            safe_session = _safe_session(session_name)
            safe_filename = _safe_name(filename)
        except Exception:
            return jsonify({"success": False, "error": "Geçersiz dosya/oturum"}), 400

        file_path = os.path.join(RECORDS_DIR, safe_session, safe_filename)

        if not os.path.exists(file_path):
            return jsonify({"success": False, "error": "Dosya bulunamadı"}), 404

        os.remove(file_path)

        return jsonify({
            "success": True,
            "message": "Dosya silindi"
        }), 200

    except Exception as e:
        LOG.error(f"Dosya silme hatası: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@mobile_api_bp.route("/files/<session_name>/<path:filename>/rename", methods=["POST"])
@token_required
def api_rename_file(session_name, filename):
    """
    Dosya adını değiştir

    Request Body (JSON):
    {
        "new_name": "yeni_isim.avi"
    }

    Response:
    {
        "success": true,
        "message": "Dosya adı değiştirildi",
        "new_name": "yeni_isim.avi"
    }
    """
    try:
        if not RECORDS_MODULE_AVAILABLE:
            return jsonify({"success": False, "error": "Kayıt modülü kullanılamıyor"}), 503

        data = request.get_json()
        if not data or "new_name" not in data:
            return jsonify({"success": False, "error": "new_name gerekli"}), 400

        try:
            safe_session = _safe_session(session_name)
            safe_old = _safe_name(filename)
            new_name = data["new_name"]

            # Uzantıyı koru
            _, ext = os.path.splitext(safe_old)
            if not new_name.endswith(ext):
                new_name = new_name + ext

            safe_new = _safe_name(new_name)
        except Exception:
            return jsonify({"success": False, "error": "Geçersiz dosya adı"}), 400

        old_path = os.path.join(RECORDS_DIR, safe_session, safe_old)
        new_path = os.path.join(RECORDS_DIR, safe_session, safe_new)

        if not os.path.exists(old_path):
            return jsonify({"success": False, "error": "Dosya bulunamadı"}), 404

        if os.path.exists(new_path):
            return jsonify({"success": False, "error": "Hedef dosya zaten var"}), 400

        os.rename(old_path, new_path)

        return jsonify({
            "success": True,
            "message": "Dosya adı değiştirildi",
            "new_name": safe_new
        }), 200

    except Exception as e:
        LOG.error(f"Dosya adı değiştirme hatası: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


# ==================== Kayıt Kontrolü ====================

@mobile_api_bp.route("/recording/status", methods=["GET"])
@token_required
def api_recording_status():
    """
    Kayıt durumunu sorgula

    Response:
    {
        "success": true,
        "recording": true,
        "current_file": "rec_20241031_143022.avi",
        "current_session": "oturum5",
        "fps": 18.5,
        "resolution": [1920, 1080]
    }
    """
    try:
        if not RECORDS_MODULE_AVAILABLE:
            return jsonify({"success": False, "error": "Kayıt modülü kullanılamıyor"}), 503

        is_recording = _recording_flag.is_set()

        resolution = None
        if FRAME_SIZE:
            resolution = list(FRAME_SIZE)  # (width, height)

        return jsonify({
            "success": True,
            "recording": is_recording,
            "current_file": _current_file,
            "current_session": SESSION_NAME,
            "fps": _writer_fps if is_recording else RECORD_FPS,
            "resolution": resolution
        }), 200

    except Exception as e:
        LOG.error(f"Kayıt durumu hatası: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@mobile_api_bp.route("/recording/start", methods=["POST"])
@token_required
def api_start_recording():
    """
    Kaydı manuel olarak başlat (GPIO yerine API ile kontrol)
    Not: GPIO kontrolü aktifse bu endpoint çalışmayabilir

    Response:
    {
        "success": true,
        "message": "Kayıt başlatıldı"
    }
    """
    try:
        if not RECORDS_MODULE_AVAILABLE:
            return jsonify({"success": False, "error": "Kayıt modülü kullanılamıyor"}), 503

        if _recording_flag.is_set():
            return jsonify({
                "success": False,
                "error": "Kayıt zaten aktif"
            }), 400

        _recording_flag.set()

        return jsonify({
            "success": True,
            "message": "Kayıt başlatıldı"
        }), 200

    except Exception as e:
        LOG.error(f"Kayıt başlatma hatası: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@mobile_api_bp.route("/recording/stop", methods=["POST"])
@token_required
def api_stop_recording():
    """
    Kaydı manuel olarak durdur

    Response:
    {
        "success": true,
        "message": "Kayıt durduruldu"
    }
    """
    try:
        if not RECORDS_MODULE_AVAILABLE:
            return jsonify({"success": False, "error": "Kayıt modülü kullanılamıyor"}), 503

        if not _recording_flag.is_set():
            return jsonify({
                "success": False,
                "error": "Kayıt zaten durmuş"
            }), 400

        _recording_flag.clear()

        return jsonify({
            "success": True,
            "message": "Kayıt durduruldu"
        }), 200

    except Exception as e:
        LOG.error(f"Kayıt durdurma hatası: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


# ==================== Sistem Bilgileri ====================

@mobile_api_bp.route("/system/info", methods=["GET"])
@token_required
def api_system_info():
    """
    Genel sistem bilgilerini getir

    Response:
    {
        "success": true,
        "system": {
            "total_sessions": 5,
            "total_files": 23,
            "total_size": 157286400,
            "total_size_formatted": "150.00 MB",
            "records_directory": "/path/to/clary/records",
            "active_session": "oturum5"
        }
    }
    """
    try:
        if not RECORDS_MODULE_AVAILABLE:
            return jsonify({"success": False, "error": "Kayıt modülü kullanılamıyor"}), 503

        sessions = _list_sessions()
        total_files = sum(s["count"] for s in sessions)
        total_size = sum(s["size"] for s in sessions)

        return jsonify({
            "success": True,
            "system": {
                "total_sessions": len(sessions),
                "total_files": total_files,
                "total_size": total_size,
                "total_size_formatted": format_size(total_size),
                "records_directory": RECORDS_DIR,
                "active_session": SESSION_NAME
            }
        }), 200

    except Exception as e:
        LOG.error(f"Sistem bilgisi hatası: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


# ==================== Error Handlers ====================

@mobile_api_bp.errorhandler(404)
def api_not_found(e):
    return jsonify({"success": False, "error": "Endpoint bulunamadı"}), 404


@mobile_api_bp.errorhandler(500)
def api_server_error(e):
    return jsonify({"success": False, "error": "Sunucu hatası"}), 500
