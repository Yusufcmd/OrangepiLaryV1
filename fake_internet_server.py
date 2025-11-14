#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Fake Internet Connectivity Check Server
Bu server, tüm major işletim sistemlerinin internet bağlantı kontrolü için
kullandığı endpoint'lere yanıt vererek cihazların "internet var" algısını sağlar.

Desteklenen platformlar:
- Android (Google)
- iOS/macOS (Apple)
- Windows (Microsoft)
- Linux (Ubuntu, Fedora, Arch, etc.)
- Firefox
"""

from flask import Flask, Response, request
import logging

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ============================================================================
# Android Connectivity Checks (Google)
# ============================================================================
@app.route('/generate_204')
def android_generate_204():
    """Android cihazların kullandığı temel connectivity check"""
    logger.info(f"Android connectivity check: {request.user_agent}")
    return Response(status=204)

@app.route('/gen_204')
def android_gen_204():
    """Android alternatif endpoint"""
    logger.info(f"Android gen_204 check: {request.user_agent}")
    return Response(status=204)

# ============================================================================
# Apple (iOS/macOS) Connectivity Checks
# ============================================================================
@app.route('/hotspot-detect.html')
def apple_hotspot_detect():
    """iOS/macOS cihazların kullandığı captive portal check"""
    logger.info(f"Apple hotspot-detect: {request.user_agent}")
    html = """<!DOCTYPE html>
<html>
<head>
<title>Success</title>
</head>
<body>
Success
</body>
</html>"""
    return Response(html, status=200, mimetype='text/html')

@app.route('/library/test/success.html')
def apple_library_test():
    """iOS/macOS alternatif endpoint"""
    logger.info(f"Apple library/test check: {request.user_agent}")
    html = """<!DOCTYPE html>
<html>
<head>
<title>Success</title>
</head>
<body>
Success
</body>
</html>"""
    return Response(html, status=200, mimetype='text/html')

@app.route('/success.txt')
def apple_success_txt():
    """macOS için text response"""
    logger.info(f"Apple success.txt check: {request.user_agent}")
    return Response("Success", status=200, mimetype='text/plain')

# ============================================================================
# Microsoft Windows Connectivity Checks
# ============================================================================
@app.route('/ncsi.txt')
def windows_ncsi_txt():
    """Windows NCSI (Network Connectivity Status Indicator) - Text"""
    logger.info(f"Windows NCSI txt check: {request.user_agent}")
    return Response("Microsoft NCSI", status=200, mimetype='text/plain')

@app.route('/connecttest.txt')
def windows_connecttest_txt():
    """Windows 10/11 connectivity test"""
    logger.info(f"Windows connecttest check: {request.user_agent}")
    return Response("Microsoft Connect Test", status=200, mimetype='text/plain')

@app.route('/redirect')
def windows_redirect():
    """Windows redirect test - captive portal check"""
    logger.info(f"Windows redirect check: {request.user_agent}")
    return Response(status=200)

# ============================================================================
# Firefox Connectivity Checks
# ============================================================================
@app.route('/success.txt', subdomain='detectportal')
def firefox_detectportal():
    """Firefox captive portal detection"""
    logger.info(f"Firefox detectportal check: {request.user_agent}")
    return Response("success", status=200, mimetype='text/plain')

# ============================================================================
# Linux Connectivity Checks
# ============================================================================
@app.route('/check_network_status.txt')
def linux_ubuntu_check():
    """Ubuntu connectivity check"""
    logger.info(f"Ubuntu connectivity check: {request.user_agent}")
    return Response(status=204)

@app.route('/check_network')
def linux_generic_check():
    """Generic Linux network check"""
    logger.info(f"Linux generic check: {request.user_agent}")
    return Response(status=204)

# ============================================================================
# Catch-all routes
# ============================================================================
@app.route('/')
def index():
    """Root endpoint - genel kullanım"""
    logger.info(f"Root access: {request.user_agent}")
    return Response("Network Connected", status=200)

@app.route('/<path:path>')
def catch_all(path):
    """Bilinmeyen tüm istekleri yakala ve success dön"""
    logger.info(f"Catch-all: {path} from {request.user_agent}")
    # Eğer .txt uzantılı istek gelirse text dön
    if path.endswith('.txt'):
        return Response("OK", status=200, mimetype='text/plain')
    # HTML istekleri için
    elif path.endswith('.html') or path.endswith('.htm'):
        return Response("<!DOCTYPE html><html><head><title>OK</title></head><body>OK</body></html>",
                       status=200, mimetype='text/html')
    # Diğer her şey için 204 No Content
    else:
        return Response(status=204)

if __name__ == '__main__':
    # Port 80'de çalış (root gerektirir veya authbind kullan)
    logger.info("Starting Fake Internet Connectivity Server on port 80...")
    logger.info("This server will respond to connectivity checks from:")
    logger.info("  - Android devices")
    logger.info("  - iOS/macOS devices")
    logger.info("  - Windows devices")
    logger.info("  - Linux devices")
    logger.info("  - Firefox browser")
    app.run(host='0.0.0.0', port=80, debug=False)

