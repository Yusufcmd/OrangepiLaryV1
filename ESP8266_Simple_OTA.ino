/*  ESP8266 - Orange Pi AP ile Basit Web OTA
 *  ESP8266 Orange Pi'nin yaydığı AP'ye bağlanır
 *  10 kez butona basıldığında OTA modu açılır
 *  http://esp_ip/ adresinden firmware güncellenebilir
 */

#include <Arduino.h>
#include <ESP8266WiFi.h>
#include <ESP8266WebServer.h>
#include <ESP8266HTTPUpdateServer.h>

// ==== Orange Pi AP Bilgileri ====
const char* AP_SSID = "OrangePiAP";
const char* AP_PASS = "simclever123";

// ==== Web Server ====
ESP8266WebServer server(80);
ESP8266HTTPUpdateServer httpUpdater;

// ==== Pinler ====
const uint8_t PIN_LATCH   = 4;   // D2 - Güç kilidi
const uint8_t PIN_SENSE   = 5;   // D1 - Buton
const uint8_t PIN_LED     = 2;   // D4 - LED
const uint8_t RPI_PIN     = 14;  // D5 - RPi açma
const uint8_t RPIS_PIN    = 12;  // D6 - RPi shutdown
const uint8_t PIN_ACTION  = 16;  // D0 - Kayıt
const uint8_t PIN_RECOVERY= 13;  // D7 - Recovery
const uint8_t PWM_PIN     = 15;  // D8 - Batarya PWM
const uint8_t BatteryVal  = A0;  // ADC

// ==== Durum Değişkenleri ====
bool otaMode = false;
bool wifiConnected = false;
uint8_t buttonPressCount = 0;
uint32_t lastButtonTime = 0;
bool buttonState = false;
bool lastButtonState = false;
uint32_t buttonDebounce = 0;

// ==== Ana Sayfa HTML ====
const char* htmlPage = R"(
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>ESP8266 OTA</title>
    <style>
        body {
            font-family: Arial, sans-serif;
            max-width: 600px;
            margin: 50px auto;
            padding: 20px;
            background: #f0f0f0;
        }
        .container {
            background: white;
            padding: 30px;
            border-radius: 10px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
        }
        h1 {
            color: #333;
            text-align: center;
        }
        .info {
            background: #e3f2fd;
            padding: 15px;
            border-radius: 5px;
            margin: 20px 0;
        }
        .btn {
            display: block;
            width: 100%;
            padding: 15px;
            background: #2196F3;
            color: white;
            text-align: center;
            text-decoration: none;
            border-radius: 5px;
            font-size: 16px;
            margin: 10px 0;
        }
        .btn:hover {
            background: #1976D2;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>🔧 ESP8266 OTA Güncelleme</h1>
        <div class="info">
            <p><strong>IP Adresi:</strong> %IP%</p>
            <p><strong>Durum:</strong> Hazır</p>
            <p><strong>SSID:</strong> OrangePiAP</p>
        </div>
        <a href="/update" class="btn">📦 Firmware Güncelle</a>
        <p style="text-align:center; color:#666; margin-top:20px;">
            Rise Teknoloji © 2025
        </p>
    </div>
</body>
</html>
)";

void setup() {
    Serial.begin(115200);
    Serial.println("\n\nESP8266 Başlatılıyor...");

    // Pin ayarları
    pinMode(PIN_LATCH, OUTPUT);
    pinMode(RPI_PIN, OUTPUT);
    pinMode(RPIS_PIN, OUTPUT);
    pinMode(PIN_LED, OUTPUT);
    pinMode(PIN_SENSE, INPUT);
    pinMode(PIN_ACTION, OUTPUT);
    pinMode(PIN_RECOVERY, OUTPUT);
    pinMode(PWM_PIN, OUTPUT);

    // Başlangıç durumları
    digitalWrite(PIN_LATCH, HIGH);   // Güç açık
    digitalWrite(RPI_PIN, HIGH);      // RPi kapalı başlasın
    digitalWrite(RPIS_PIN, HIGH);     // Shutdown sinyali yok
    digitalWrite(PIN_LED, LOW);       // LED kapalı
    digitalWrite(PIN_ACTION, LOW);
    digitalWrite(PIN_RECOVERY, LOW);

    // RPi'yi başlat
    delay(100);
    digitalWrite(RPI_PIN, LOW);
    Serial.println("Orange Pi başlatıldı");

    // Batarya PWM başlat
    analogWriteFreq(1000);
    analogWriteRange(1000);
    analogWrite(PWM_PIN, 500); // %50 başlangıç

    Serial.println("Sistem hazır");
    Serial.println("10 kez butona basarak OTA modunu açabilirsiniz");
}

void connectToAP() {
    Serial.println("\nOrange Pi AP'sine bağlanılıyor...");
    WiFi.mode(WIFI_STA);
    WiFi.begin(AP_SSID, AP_PASS);

    digitalWrite(PIN_LED, HIGH); // LED yanar (bağlanıyor)

    int attempts = 0;
    while (WiFi.status() != WL_CONNECTED && attempts < 30) {
        delay(500);
        Serial.print(".");
        attempts++;
    }

    if (WiFi.status() == WL_CONNECTED) {
        wifiConnected = true;
        Serial.println("\n✓ Bağlandı!");
        Serial.print("IP: ");
        Serial.println(WiFi.localIP());

        // Web server başlat
        setupWebServer();

        // LED yanıp sönmeye başlasın (OTA hazır)
        for(int i=0; i<6; i++) {
            digitalWrite(PIN_LED, !digitalRead(PIN_LED));
            delay(200);
        }
        digitalWrite(PIN_LED, LOW);
    } else {
        Serial.println("\n✗ Bağlanamadı!");
        otaMode = false;
        digitalWrite(PIN_LED, LOW);
    }
}

void setupWebServer() {
    // Ana sayfa
    server.on("/", HTTP_GET, []() {
        String html = String(htmlPage);
        html.replace("%IP%", WiFi.localIP().toString());
        server.send(200, "text/html", html);
    });

    // OTA güncelleme endpoint'i
    httpUpdater.setup(&server, "/update");

    server.begin();
    Serial.println("Web server başlatıldı");
    Serial.println("Tarayıcıda http://" + WiFi.localIP().toString() + "/ adresine gidin");
}

void loop() {
    uint32_t now = millis();

    // Web server (OTA modundaysa)
    if (otaMode && wifiConnected) {
        server.handleClient();

        // LED yanıp sönsün (OTA aktif göstergesi)
        static uint32_t ledTime = 0;
        if (now - ledTime > 1000) {
            ledTime = now;
            digitalWrite(PIN_LED, !digitalRead(PIN_LED));
        }
    }

    // Buton okuma (debounce ile)
    bool currentButton = digitalRead(PIN_SENSE);

    if (currentButton != lastButtonState) {
        buttonDebounce = now;
    }

    if ((now - buttonDebounce) > 30) {
        if (currentButton != buttonState) {
            buttonState = currentButton;

            if (buttonState) { // Butona basıldı
                Serial.println("Buton basıldı");

                // 1 saniye içinde sayılsın
                if (now - lastButtonTime < 1000) {
                    buttonPressCount++;
                } else {
                    buttonPressCount = 1;
                }
                lastButtonTime = now;

                Serial.print("Basış sayısı: ");
                Serial.println(buttonPressCount);

                // 10 basışta OTA modu
                if (buttonPressCount == 10 && !otaMode) {
                    Serial.println("\n>>> OTA MODU AKTİF <<<");
                    otaMode = true;
                    connectToAP();
                    buttonPressCount = 0;
                }

                // 2 basışta kayıt aç
                else if (buttonPressCount == 2) {
                    digitalWrite(PIN_ACTION, HIGH);
                    Serial.println("Kayıt başladı");
                }

                // 3 basışta kayıt kapat
                else if (buttonPressCount == 3) {
                    digitalWrite(PIN_ACTION, LOW);
                    Serial.println("Kayıt durduruldu");
                    buttonPressCount = 0;
                }
            }
        }
    }

    lastButtonState = currentButton;

    // Sayacı 1 saniye sonra sıfırla
    if (buttonPressCount > 0 && (now - lastButtonTime) > 1000) {
        if (buttonPressCount < 10) {
            Serial.println("Basış sayacı sıfırlandı");
            buttonPressCount = 0;
        }
    }

    // Batarya PWM güncelle (her 500ms)
    static uint32_t battTime = 0;
    if (now - battTime > 500) {
        battTime = now;
        int adc = analogRead(BatteryVal);
        // ADC 750-1010 arası -> PWM 0-1000
        int pwm = map(constrain(adc, 750, 1010), 750, 1010, 0, 1000);
        analogWrite(PWM_PIN, pwm);
    }

    delay(5);
}

