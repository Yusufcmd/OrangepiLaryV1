#!/usr/bin/env python3
"""
Captive Portal / Connectivity Check Spoofing Server
AP modundayken cihazların internet kontrolü yaparken kullandıkları URL'lere
sahte yanıtlar dönerek internet varmış gibi gösterir.
"""

from flask import Flask, Response, request
import logging

app = Flask(__name__)
app.logger.setLevel(logging.INFO)

# Android connectivity check endpoints
ANDROID_ENDPOINTS = [
    '/generate_204',
    '/gen_204',
    '/ncsi.txt',
    '/connecttest.txt',
    '/redirect',
    '/hotspot-detect.html',
]

# iOS/Apple endpoints
APPLE_ENDPOINTS = [
    '/hotspot-detect.html',
    '/library/test/success.html',
    '/bag',
    '/captive',
]

# Windows endpoints
WINDOWS_ENDPOINTS = [
    '/ncsi.txt',
    '/connecttest.txt',
]

@app.route('/generate_204')
@app.route('/gen_204')
def android_204():
    """Android connectivity check - 204 No Content döner"""
    app.logger.info(f"Android 204 request from {request.remote_addr}")
    return Response('', status=204)

@app.route('/ncsi.txt')
def windows_ncsi():
    """Windows Network Connectivity Status Indicator"""
    app.logger.info(f"Windows NCSI request from {request.remote_addr}")
    return Response('Microsoft NCSI', status=200, mimetype='text/plain')

@app.route('/connecttest.txt')
def windows_connecttest():
    """Windows connectivity test"""
    app.logger.info(f"Windows connecttest from {request.remote_addr}")
    return Response('Microsoft Connect Test', status=200, mimetype='text/plain')

@app.route('/hotspot-detect.html')
@app.route('/library/test/success.html')
def apple_success():
    """iOS/Apple connectivity check - Success yanıtı"""
    app.logger.info(f"Apple connectivity check from {request.remote_addr}")
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

@app.route('/redirect')
@app.route('/canonical.html')
def generic_redirect():
    """Generic redirect endpoint"""
    app.logger.info(f"Generic redirect request from {request.remote_addr}")
    return Response('OK', status=200)

@app.route('/bag')
@app.route('/captive')
def apple_captive():
    """Apple captive portal check"""
    app.logger.info(f"Apple captive portal check from {request.remote_addr}")
    return Response('', status=200)

@app.route('/')
def index():
    """Root endpoint - Basit bilgi sayfası"""
    app.logger.info(f"Root request from {request.remote_addr}")
    html = """<!DOCTYPE html>
<html lang="tr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Orange Pi Network</title>
    <style>
        body {
            font-family: Arial, sans-serif;
            display: flex;
            justify-content: center;
            align-items: center;
            min-height: 100vh;
            margin: 0;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
        }
        .container {
            text-align: center;
            padding: 40px;
            background: rgba(255, 255, 255, 0.1);
            border-radius: 20px;
            backdrop-filter: blur(10px);
            box-shadow: 0 8px 32px 0 rgba(31, 38, 135, 0.37);
        }
        h1 { margin: 0 0 20px 0; font-size: 2.5em; }
        p { font-size: 1.2em; line-height: 1.6; }
        .status { 
            display: inline-block;
            padding: 10px 20px;
            background: rgba(76, 175, 80, 0.3);
            border-radius: 25px;
            margin-top: 20px;
            font-weight: bold;
        }
        .info {
            margin-top: 30px;
            font-size: 0.9em;
            opacity: 0.8;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>🍊 Orange Pi Network</h1>
        <p>Orange Pi yerel ağına bağlandınız.</p>
        <div class="status">✓ Bağlantı Başarılı</div>
        <div class="info">
            <p>Bu yerel bir ağdır. Internet erişimi yoktur.</p>
            <p>Cihazınız ağa bağlı kalacaktır.</p>
        </div>
    </div>
</body>
</html>"""
    return Response(html, status=200, mimetype='text/html')

@app.route('/status')
def status():
    """Status endpoint - JSON formatında durum bilgisi"""
    import json
    app.logger.info(f"Status request from {request.remote_addr}")
    status_data = {
        "status": "online",
        "network": "Orange Pi AP",
        "connectivity": "local",
        "internet": False,
        "message": "Captive portal active"
    }
    return Response(json.dumps(status_data), status=200, mimetype='application/json')

# Catch-all route for any other paths
@app.route('/<path:path>')
def catch_all(path):
    """Tüm diğer path'ler için genel yanıt"""
    app.logger.info(f"Catch-all request for {path} from {request.remote_addr}")
    # Eğer HTML içerik bekleniyorsa basit bir sayfa dön
    if 'html' in request.headers.get('Accept', ''):
        return Response('<!DOCTYPE html><html><body><h1>Orange Pi Network</h1><p>Connected</p></body></html>',
                       status=200, mimetype='text/html')
    return Response('OK', status=200)

if __name__ == '__main__':
    # Port 80'de dinle (captive portal için)
    print("=" * 60)
    print("Starting Connectivity Check Spoofing Server on port 80")
    print("=" * 60)
    print("This server will respond to connectivity checks from:")
    print("  - Android devices (generate_204)")
    print("  - iOS/Apple devices (hotspot-detect.html)")
    print("  - Windows devices (connecttest.txt)")
    print("  - Linux devices (connectivity-check.ubuntu.com)")
    print("=" * 60)
    print("Devices will think they have internet connectivity")
    print("and will stay connected to the Orange Pi network.")
    print("=" * 60)
    app.run(host='0.0.0.0', port=80, debug=False)

