/*  Soft-Latch Güç Yönetimi – ESP8266MOD + AP2112K
 *  LATCH: EN hattını tutar (HIGH = açık, LOW = kapalı)
 *  SENSE: Buton algısı (HIGH = basılı, LOW = bırakıldı)
 *  Yazar: simcleverY / Rise Teknoloji
 */

#include <Arduino.h>
#include <ESP8266WiFi.h>
#include <ESP8266WebServer.h>
#include <WiFiClient.h>
#include <WiFiUdp.h>
#include <Updater.h>
#include <ArduinoOTA.h>

// =================== Yapılandırma ===================
#define ENABLE_IDE_OTA 1  // Arduino IDE üzerinden OTA'yı da aktif etmek istemezsen 0 yap
#define UPDATE_BEGIN_UNKNOWN() Update.begin( (ESP.getFreeSketchSpace() - 0x1000) & 0xFFFFF000 )

// ==== Wi-Fi / OTA ayarları (10 basışta devreye girer) ====
const char* WIFI_SSID = "simcleverY";
const char* WIFI_PASS = "rise1234";

const char* OTA_USER = "simcleverY";
const char* OTA_PASS = "otaESPZUYzxZSf2lfq1uyVDH8v2";

bool        otaModeActive = false;    // OTA mod bayrağı (10 basış)
bool        otaInitDone   = false;    // ArduinoOTA.begin() yapıldı mı
bool        otaControl    = false;
uint32_t    otaStartTs    = 0;
const uint32_t OTA_CONNECT_INFO_MS = 30000; // 30 sn bilgi logu

// ==== Web Server & Session ====
ESP8266WebServer server(80);
bool   webStarted    = false;
String sessionToken;                  // basit session (Cookie: SID=...)

// ---- DÜZELTME: Cookie başlığını topla ----
static const char* HEADER_KEYS[] = { "Cookie" };
static const size_t HEADER_KEYS_COUNT = sizeof(HEADER_KEYS)/sizeof(HEADER_KEYS[0]);

// ==== Pin seçimleri (NodeMCU eşleşmesi: D1=GPIO5, D2=GPIO4) ====
const uint8_t PIN_LATCH  = 4;    // GPIO4 (D2)
const uint8_t PIN_SENSE  = 5;    // GPIO5 (D1)
const uint8_t RPI_PIN    = 14;   // D5
const uint8_t RPIS_PIN   = 12;   // D6 Shutdown pin
const uint8_t BatteryVal = A0;
int shutdown_cooldown = 20;

const uint8_t PIN_LED    = 2;    // LED çıkışı (AKTİF HIGH: HIGH=yanık, LOW=sönük)

// --- Kullanıcı aksiyonu için boştaki pin (iki/üç kısa basış) ---
const uint8_t PIN_ACTION = 16;   // D0 (GPIO16) – iki/üç basışa göre HIGH/LOW

// --- Recovery pin (20 kez basış) ---
const uint8_t PIN_RECOVERY = 13; // D7 (GPIO13) – 20 kez basışta HIGH

// ==== Zamanlamalar ====
const uint32_t DEBOUNCE_MS   = 30;
const uint32_t LONGPRESS_MS  = 5000;
const uint32_t PRE_OFF_MS    = 120;

// --- Çoklu basış takibi ---
const uint32_t MULTI_PRESS_WINDOW_MS = 1000;               // 1 sn içinde art arda basış
const uint32_t RECOVERY_SIGNAL_DURATION_MS = 15000;        // Recovery sinyali 15 saniye HIGH

// ==== Dahili durum ====
bool     btnState     = false;
bool     lastBtnState = false;
bool     recordState  = false;
uint32_t lastChange   = 0;
uint32_t pressStart   = 0;
uint8_t  pressCounter = 0;
uint32_t lastPressEdgeTs = 0;

// --- Recovery durum ---
bool     recoveryTriggered = false;
uint32_t recoveryTriggerTs = 0;

// ===================== ESP8266: PWM ile Yüzdelik SOC =====================

// --- Pin ve PWM konfig ---
const uint8_t  PWM_PIN   = 15;   // D8 (GPIO15)
#define ACTIVE_LOW 0             // 1: LOW=aktif (ters PWM), 0: HIGH=aktif

// --- PWM zaman tabanı ---
static const uint16_t PWM_RANGE = 1000;  // analogWriteRange(0..1000)
static const uint16_t PWM_FREQ  = 1000;  // 1 kHz

// --- ADC → %SOC kalibrasyonu ---
#define NUM_READINGS   10
#define MIN_ADC_VALUE  750
#define MAX_ADC_VALUE  1010

// SOC filtre periyodu
const uint32_t SOC_UPDATE_MS = 500;
uint32_t lastSocUpdate = 0;

// Hareketli ortalama tamponu
float    socReadings[NUM_READINGS] = {0.0f};
uint8_t  socIndex = 0;

// En son hesaplanmış ortalama SOC (LED mantığı için)
volatile float g_socAvgLatest = 100.0f;

// ===================== Yardımcılar =====================
static inline float clampf(float v, float lo, float hi) {
  if (v < lo) return lo;
  if (v > hi) return hi;
  return v;
}

// ADC (MIN..MAX) -> %0..100
float calcSOC_fromADC(int adcVal) {
  if (adcVal < MIN_ADC_VALUE) adcVal = MIN_ADC_VALUE;
  if (adcVal > MAX_ADC_VALUE) adcVal = MAX_ADC_VALUE;
  const float span = (float)(MAX_ADC_VALUE - MIN_ADC_VALUE);
  return (span <= 0.0f) ? 0.0f : ((adcVal - MIN_ADC_VALUE) * 100.0f) / span;
}

void socPwmInit() {
  analogWriteFreq(PWM_FREQ);
  analogWriteRange(PWM_RANGE);
  pinMode(PWM_PIN, OUTPUT);
  analogWrite(PWM_PIN, 0); // başlangıç: %0 duty
}

// === İlk açılışta güvenilir SOC okuması için tampon doldurma ===
float primeAndReadInitialSOC(uint8_t samples = NUM_READINGS, uint16_t settle_ms = 2) {
  float acc = 0.0f;
  for (uint8_t i = 0; i < samples; ++i) {
    int adc = analogRead(BatteryVal);
    float soc = calcSOC_fromADC(adc);
    acc += soc;
    delay(settle_ms);
  }
  float avg = acc / (float)samples;

  // Tamponu bu ortalama ile doldur
  for (uint8_t i = 0; i < NUM_READINGS; ++i) socReadings[i] = avg;
  socIndex = 0;
  g_socAvgLatest = avg;

  // PWM'i ayarla
  int duty = (int)((clampf(avg, 0.0f, 100.0f) / 100.0f) * 1000.0f + 0.5f);
  #if ACTIVE_LOW
    duty = 1000 - duty;
  #endif
  if (duty < 0) duty = 0;
  if (duty > 1000) duty = 999;
  analogWrite(PWM_PIN, duty);

  Serial.printf("[BOOT] Initial SOC(avg)=%.1f%%, duty=%d/1000\n", avg, duty);
  return avg;
}

// SOC periyodik güncellemesi
void updateSocPwm() {
  int adc = analogRead(BatteryVal);

  float soc = calcSOC_fromADC(adc);
  socReadings[socIndex] = soc;
  socIndex = (uint8_t)((socIndex + 1) % NUM_READINGS);

  float socAvg = 0.0f;
  for (int i = 0; i < NUM_READINGS; ++i) socAvg += socReadings[i];
  socAvg /= (float)NUM_READINGS;

  socAvg = clampf(socAvg, 0.0f, 100.0f);
  int duty = (int)((socAvg / 100.0f) * 1000.0f + 0.5f);

  #if ACTIVE_LOW
    duty = 1000 - duty;
  #endif

  if (duty < 0) duty = 0;
  if (duty > 1000) duty = 999;

  analogWrite(PWM_PIN, duty);
  g_socAvgLatest = socAvg;

  Serial.printf("ADC=%d | SOC(avg)=%.1f%% -> duty=%d/1000\n", adc, socAvg, duty);
}

// ===================== LED DURUM MAKİNESİ =====================
enum LedMode : uint8_t { LED_NORMAL=0, LED_RECORD=1, LED_LOW_BATT=2 };

LedMode  ledMode = LED_NORMAL;
bool     ledStateOn = true;
uint32_t ledTs = 0;
uint8_t  lowBattCycles = 0;
bool     shuttingDown = false;

inline void ledSet(bool on) {
  digitalWrite(PIN_LED, on ? HIGH : LOW); // aktif HIGH
  ledStateOn = on;
}

// Kayıt modu faz zamanları (ms)
const uint32_t REC_ON_MS  = 1000;
const uint32_t REC_OFF_MS = 500;

// Düşük batarya blink periyodu (ms)
const uint32_t LB_PHASE_MS = 300;

void ledUpdate(uint32_t now) {
  if (shuttingDown) return;

  if (ledMode == LED_LOW_BATT) {
    if (now - ledTs >= LB_PHASE_MS) {
      ledTs = now;
      ledSet(!ledStateOn);
      if (!ledStateOn) {
        lowBattCycles++;
        if (lowBattCycles >= 10) {
          shuttingDown = true;
          gracefulShutdown();
        }
      }
    }
    return;
  }

  if (ledMode == LED_RECORD) {
    uint32_t phase_duration = ledStateOn ? REC_ON_MS : REC_OFF_MS;
    if (now - ledTs >= phase_duration) {
      ledTs = now;
      ledSet(!ledStateOn);
    }
    return;
  }

  if (!ledStateOn) ledSet(true);
}

// ===================== Güvenli Kapatma =====================
void gracefulShutdown() {
  Serial.println("=== Graceful Shutdown Başladı ===");
  Serial.println("Uygulama kapanış işlemleri...");

  delay(10);
  Serial.println("LED uyarısı veriliyor...");
  ledSet(true);
  delay(60);
  ledSet(false);

  Serial.println("Raspberry Pi kapatma sinyali gönderiliyor...");
  digitalWrite(RPIS_PIN, LOW);
  delay(shutdown_cooldown * 1000);
  Serial.println("Raspberry Pi kapandı, güç kesiliyor...");

  digitalWrite(RPI_PIN, HIGH);
  delay(1000);

  Serial.println("AP2112K LATCH LOW -> Güç kesilecek!");
  digitalWrite(PIN_LATCH, LOW);

  Serial.println("=== Güç Kesildi ===");
}

// ===================== OTA (Wi-Fi + Web Sunucu) =====================
void enterOtaMode() {
  if (otaModeActive) return;

  otaModeActive = true;
  otaInitDone   = false;
  otaStartTs    = millis();

  Serial.println("\n[OTA] OTA modu açılıyor. Wi-Fi STA ile bağlanılıyor...");
  WiFi.persistent(false);
  WiFi.setAutoReconnect(true);
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);

  // LED’i “record” yanıp sönmeye al: OTA açık göstergesi
  ledMode = LED_RECORD;
  ledTs   = millis();
}

void exitOtaMode() {
  if (!otaModeActive) return;

  Serial.println("\n[OTA] OTA modu kapatılıyor, Wi-Fi kapatılıyor...");

  // Artık HTTP/OTA isteği kabul etmeyeceğiz
  otaModeActive = false;
  otaInitDone   = false;
  webStarted    = false;
  sessionToken  = "";

  // Tüm UDP soketlerini kapat (OTA dahil)
  WiFiUDP::stopAll();

  // Wi-Fi’yı kapat
  WiFi.setAutoReconnect(false);
  WiFi.disconnect(true);    // STA bağlantısını bırak
  WiFi.mode(WIFI_OFF);      // RF off

  // LED durumu: düşük batarya değilse eski moda dön
  if (!shuttingDown && g_socAvgLatest >= 10.0f) {
    ledMode = recordState ? LED_RECORD : LED_NORMAL;
    ledSet(true);
    ledTs = millis();
  }

  Serial.println("[OTA] Kapandı. RF OFF.");
}

// --- Basit session kontrolü (Cookie: SID=...) ---
bool isAuthed() {
  if (sessionToken.isEmpty()) return false;
  if (!server.hasHeader("Cookie")) return false;
  String cookie = server.header("Cookie"); // "SID=....; other=..."
  int i = cookie.indexOf("SID=");
  if (i < 0) return false;
  int j = cookie.indexOf(';', i);
  String sid = cookie.substring(i + 4, j < 0 ? cookie.length() : j);
  sid.trim();
  return sid == sessionToken;
}

void sendLoginPage(const String& msg = "") {
  String html =
    F("<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
      "<title>ESP OTA Giriş</title>"
      "<style>body{font-family:Arial;padding:24px;max-width:420px;margin:auto}"
      "h1{font-size:20px}form{display:flex;flex-direction:column;gap:8px}"
      "input[type=text],input[type=password]{padding:8px;font-size:14px}"
      "button{padding:10px 12px;font-size:14px;cursor:pointer}"
      ".err{color:#b00020;margin:8px 0}</style></head><body>");
  html += F("<h1>ESP8266 OTA Giriş</h1>");
  if (msg.length()) { html += "<div class='err'>" + msg + "</div>"; }
  html +=
    F("<form method='POST' action='/login'>"
      "<label>Kullanıcı Adı</label><input type='text' name='username' autofocus>"
      "<label>Şifre</label><input type='password' name='password'>"
      "<button type='submit'>Giriş</button>"
      "</form>"
      "</body></html>");
  server.sendHeader("Cache-Control","no-store");
  server.send(200, "text/html", html);
}

void sendOtaPage() {
  String html =
    F("<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
      "<title>ESP OTA</title>"
      "<style>body{font-family:Arial;padding:24px;max-width:520px;margin:auto}"
      "h1{font-size:20px}form{margin-top:12px}"
      "input[type=file]{margin:6px 0}"
      "button{padding:10px 12px;font-size:14px;cursor:pointer}</style></head><body>");
  html += F("<h1>Firmware Güncelle</h1>");
  html += "<p>IP: " + WiFi.localIP().toString() + "</p>";
  html +=
    F("<form method='POST' action='/update' enctype='multipart/form-data'>"
      "<input type='file' name='firmware' accept='.bin' required>"
      "<br><button type='submit'>Yükle ve Güncelle</button>"
      "</form>"
      "<p><a href='/logout'>Çıkış</a></p>"
      "</body></html>");
  server.sendHeader("Cache-Control","no-store");
  server.send(200, "text/html", html);
}

void redirectTo(const String& path) {
  server.sendHeader("Location", path, true);
  server.send(303, "text/plain", "");
}

void startOtaWeb() {
  if (webStarted) return;

  // ---- DÜZELTME: Cookie başlığını toplamak için zorunlu çağrı ----
  server.collectHeaders(HEADER_KEYS, HEADER_KEYS_COUNT);

  // Ana sayfa: login veya OTA
  server.on("/", HTTP_GET, []() {
    if (isAuthed()) redirectTo("/ota");
    else sendLoginPage();
  });

  // Login
  server.on("/login", HTTP_POST, []() {
    String u = server.arg("username");
    String p = server.arg("password");
    if (u == OTA_USER && p == OTA_PASS) {
      // Session token üret
      randomSeed(analogRead(A0) ^ micros());
      sessionToken = String(millis(), HEX) + String(random(0xFFFF), HEX);

      // Path=/, HttpOnly, Max-Age, SameSite
      String cookie = "SID=" + sessionToken + "; Path=/; HttpOnly; Max-Age=1800; SameSite=Lax";
      server.sendHeader("Set-Cookie", cookie);
      server.sendHeader("Cache-Control", "no-store");

      // 303 See Other: POST sonrası GET /ota
      server.sendHeader("Location", "/ota", true);
      server.send(303, "text/plain", "");
    } else {
      sendLoginPage("Hatalı kullanıcı adı veya şifre.");
    }
  });

  // OTA sayfası
  server.on("/ota", HTTP_GET, []() {
    if (!isAuthed()) { redirectTo("/"); return; }
    sendOtaPage();
  });

  // Logout
  server.on("/logout", HTTP_GET, []() {
    server.sendHeader("Set-Cookie", "SID=; Path=/; Max-Age=0; SameSite=Lax");
    server.sendHeader("Cache-Control", "no-store");
    server.sendHeader("Location", "/", true);
    server.send(303, "text/plain", "");
  });

  // Firmware upload (POST)
  server.on("/update", HTTP_POST,
    []() { // upload tamamlandığında
      if (!isAuthed()) { redirectTo("/"); return; }
      bool ok = !Update.hasError();
      server.sendHeader("Connection", "close");
      if (ok) {
        server.send(200, "text/plain", "OK. Rebooting...");
        delay(200);
        ESP.restart();
      } else {
        server.send(200, "text/plain", "Update Failed");
      }
    },
    []() { // upload chunk handler
      if (!isAuthed()) return;
      HTTPUpload& up = server.upload();
      if (up.status == UPLOAD_FILE_START) {
        Serial.setDebugOutput(true);
        WiFiUDP::stopAll();
        Serial.printf("[OTA-WEB] Update start: %s\n", up.filename.c_str());
        if (!UPDATE_BEGIN_UNKNOWN()) { // Sketch
          Update.printError(Serial);
        }
      } else if (up.status == UPLOAD_FILE_WRITE) {
        if (Update.write(up.buf, up.currentSize) != up.currentSize) {
          Update.printError(Serial);
        }
      } else if (up.status == UPLOAD_FILE_END) {
        if (Update.end(true)) {
          Serial.printf("[OTA-WEB] Update Success: %u bytes\n", up.totalSize);
        } else {
          Update.printError(Serial);
        }
        Serial.setDebugOutput(false);
      } else if (up.status == UPLOAD_FILE_ABORTED) {
        Update.end();
        Serial.println("[OTA-WEB] Update aborted");
      }
    }
  );

  // Yetkisiz istekleri login'e yönlendir
  server.onNotFound([]() {
    if (!isAuthed()) { redirectTo("/"); return; }
    server.send(404, "text/plain", "Not Found");
  });

  server.begin();
  webStarted = true;
  Serial.println("[OTA-WEB] HTTP sunucu basladi. URL: http://" + WiFi.localIP().toString() + "/");
}

// ===================== setup / loop =====================
void setup() {
  Serial.begin(9600);
  Serial.println("Sistem başlatılıyor...");

  pinMode(PIN_LATCH, OUTPUT);
  pinMode(RPI_PIN, OUTPUT);
  pinMode(RPIS_PIN, OUTPUT);
  pinMode(PIN_LED, OUTPUT);
  pinMode(PIN_SENSE, INPUT);
  pinMode(BatteryVal, INPUT);
  digitalWrite(RPI_PIN, HIGH);

  pinMode(PIN_ACTION, OUTPUT);
  digitalWrite(PIN_ACTION, LOW);

  pinMode(PIN_RECOVERY, OUTPUT);
  digitalWrite(PIN_RECOVERY, LOW);

  // LDO açık
  digitalWrite(PIN_LATCH, HIGH);
  Serial.println("LATCH HIGH: LDO açık.");

  // Açılışta LED sürekli yanık
  ledSet(true);
  ledTs = millis();
  ledMode = LED_NORMAL;

  // PWM/SOC başlat
  socPwmInit();

  // RPi'yi başlatmadan ÖNCE batarya kontrolü
  float initialSoc = primeAndReadInitialSOC();
  Serial.printf("Başlangıç SOC=%.1f%%\n", initialSoc);

  if (initialSoc < 15.0f) {
    Serial.println("Başlangıç SOC %15'in altında! RPi başlatılmayacak, düşük batarya uyarısı verilecek ve sistem kapanacak.");
    ledMode = LED_LOW_BATT;
    lowBattCycles = 0;
    ledTs = millis();
    ledSet(true);
    shutdown_cooldown = 3;
    return; // loop() düşük batarya akışını tamamlayacak
  }

  Serial.println("Raspberry Pi başlatılıyor...");
  digitalWrite(RPI_PIN, LOW);
  delay(100);
  digitalWrite(RPIS_PIN, HIGH);

  delay(50);
  Serial.println("Sistem hazır.");
}

void loop() {
  uint32_t now = millis();

  // --- OTA modu ise Wi-Fi bağlanınca OTA servislerini aç ---
  if (otaModeActive) {
    if (WiFi.status() == WL_CONNECTED) {
      if (!webStarted) {
        startOtaWeb();
      }
      #if ENABLE_IDE_OTA
      if (!otaInitDone) {
        ArduinoOTA.setHostname("ESP8266-OTA");
        ArduinoOTA.begin();
        otaInitDone = true;
        Serial.print("[OTA-IDE] Bağlandı. IP: ");
        Serial.println(WiFi.localIP());
      }
      if (otaInitDone) ArduinoOTA.handle();
      #endif
      server.handleClient();
    } else {
      // Bağlanma sürecinde bilgilendirme (30 sn kadar)
      if (now - otaStartTs < OTA_CONNECT_INFO_MS) {
        static uint32_t lastLog = 0;
        if (now - lastLog >= 1000) {
          lastLog = now;
          Serial.printf("[OTA] Wi-Fi durumu: %d (0=Idle,3=Bağlı)\n", WiFi.status());
        }
      }
    }
  }

  // --- PWM/SOC periyodik güncelle ---
  if (now - lastSocUpdate >= SOC_UPDATE_MS) {
    updateSocPwm();
    lastSocUpdate = now;
  }

  // ==== LED mod seçim mantığı ====
  if (!shuttingDown) {
    if (g_socAvgLatest < 10.0f) {
      if (ledMode != LED_LOW_BATT) {
        ledMode = LED_LOW_BATT;
        lowBattCycles = 0;
        ledTs = now;
        ledSet(true);
      }
    } else {
      // OTA aktifse LED_RECORD öncelikli, değilse recordState'e göre
      LedMode desired = otaModeActive ? LED_RECORD : (recordState ? LED_RECORD : LED_NORMAL);
      if (ledMode != desired) {
        ledMode = desired;
        ledTs = now;
        ledSet(true);
      }
    }
  }

  // LED çıktısını güncelle
  ledUpdate(now);

  // --- Buton okuma / debounce ---
  bool raw = digitalRead(PIN_SENSE);
  if (raw != lastBtnState) {
    lastChange   = now;
    lastBtnState = raw;
  }

  if ((now - lastChange) >= DEBOUNCE_MS) {
    if (btnState != raw) {
      btnState = raw;

      if (btnState) {
        // Basış kenarı
        pressStart = now;
        lastPressEdgeTs = now;
        Serial.println("Butona basıldı.");

        // Kısa basış sayacı
        pressCounter++;
        Serial.print("pressCounter = "); Serial.println(pressCounter);

        // ---- Önce yüksek eşikler: 20 -> Recovery, 10 -> OTA aç/kapa ----
        if (pressCounter == 20 && !recoveryTriggered) {
          recoveryTriggered = true;
          recoveryTriggerTs = now;
          digitalWrite(PIN_RECOVERY, HIGH);
          Serial.println("[RECOVERY] 20 kez basış -> PIN_RECOVERY = HIGH (15 saniye)");
          pressCounter = 0;
        }
        // === YENİ: 10 basış ve OTA AÇIK ise OTA'dan çık + Wi-Fi OFF
        else if (pressCounter == 10 && otaModeActive) {
          Serial.println("[OTA] 10 kez basış -> OTA kapatılıyor, Wi-Fi kapatılıyor.");
          exitOtaMode();
          pressCounter = 0;
        }
        // Mevcut: 10 basış ve OTA KAPALI ise OTA'ya gir
        else if (pressCounter == 10 && !otaModeActive) {
          Serial.println("[OTA] 10 kez basış algılandı -> OTA web moduna geçiliyor.");
          enterOtaMode();
          pressCounter = 0;
        }
        else if (pressCounter == 2 && recordState == false) {
          recordState = true;
          Serial.println("[ACTION] Iki kez basıldı -> PIN_ACTION = HIGH (Kayıt Aç)");
          digitalWrite(PIN_ACTION, HIGH);
        }
        else if (pressCounter == 3 && recordState == true) {
          recordState = false;
          digitalWrite(PIN_ACTION, LOW);
          Serial.println("[ACTION] Üç kez basıldı -> PIN_ACTION = LOW (Kayıt Kapat)");
        }


      } else {
        Serial.println("Buton bırakıldı.");
      }
    }
  }

  // Uzun basış (güvenli kapatma) - recovery sırasında devre dışı
  if (btnState && (now - pressStart >= LONGPRESS_MS) && !shuttingDown && !recoveryTriggered) {
    Serial.println("Uzun basış algılandı -> Kapatma işlemi başlatılıyor.");
    pressCounter = 0; // çakışmayı önle
    shuttingDown = true;
    gracefulShutdown();
  }

  // 1 sn içinde yeni basış yoksa sayaç sıfırla (recovery aktifse sıfırlama yapılmaz)
  if (pressCounter > 0 && (now - lastPressEdgeTs >= MULTI_PRESS_WINDOW_MS) && !recoveryTriggered) {
    Serial.println("Basış sayacı sıfırlandı (timeout).");
    pressCounter = 0;
  }

  // Recovery modu aktifse pini 15 sn HIGH tut, sonra kapat
  if (recoveryTriggered) {
    if (now - recoveryTriggerTs >= RECOVERY_SIGNAL_DURATION_MS) {
      digitalWrite(PIN_RECOVERY, LOW);
      recoveryTriggered = false;
      Serial.println("[RECOVERY] Recovery sinyali sonlandı (PIN_RECOVERY = LOW).");
    }
  }

  delay(5);
}
