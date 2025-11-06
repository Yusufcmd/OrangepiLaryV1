// Forward declare to satisfy Arduino auto-generated prototypes
enum ShutdownReason : unsigned char;

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
#include <Updater.h>       // ESP8266 için Update sınıfı burada
#include <ArduinoOTA.h>

// =================== Yapılandırma ===================
#define ENABLE_IDE_OTA 1  // Arduino IDE üzerinden OTA'yı da aktif etmek istemezsen 0 yap
#define UPDATE_BEGIN_UNKNOWN() Update.begin( (ESP.getFreeSketchSpace() - 0x1000) & 0xFFFFF000 )

// ==== Wi-Fi / OTA ayarları (10 basışta devreye girer) ====
const char* WIFI_SSID = "simclever";
const char* WIFI_PASS = "simclever54321";

const char* OTA_USER = "simclever";
const char* OTA_PASS = "simclever54321";

bool        otaModeActive = false;    // OTA mod bayrağı (10 basış)
bool        otaInitDone   = false;    // ArduinoOTA.begin() yapıldı mı
bool        otaControl    = false;
uint32_t    otaStartTs    = 0;
const uint32_t OTA_CONNECT_INFO_MS = 30000; // 30 sn bilgi logu

// --- RPi yeniden başlatma zamanlayıcısı (GRACE iptalinde) ---
const uint32_t RPI_START_DELAY_MS = 30000;  // 20 sn
bool     rpiStartPending = false;           // bekleyen açılış var mı
uint32_t rpiStartDueTs   = 0;               // açılış hedef zamanı (millis)

// --- RPi boot koruma (Orange Pi'nin boot olması için gereken süre) ---
const uint32_t RPI_BOOT_GUARD_MS = 30000;   // 30 saniye boot koruma süresi
uint32_t rpiBootStartTs = 0;                // RPi başlatma zamanı
bool     rpiBootGuardActive = false;        // Boot koruma aktif mi?

// === RECOVERY KİLİT MODU ===
bool        recoveryLockdownActive = false;
uint32_t    recoveryLockStartTs    = 0;
const uint32_t RECOVERY_LOCK_MS    = 180000; // 3 dakika

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
const uint8_t RPI_PIN    = 14;   // D5 (Raspberry güç kontrol / HIGH=kes, LOW=çalış)
const uint8_t RPIS_PIN   = 12;   // D6 (Raspberry shutdown sinyali / HIGH=normal)
const uint8_t BatteryVal = A0;
int shutdown_cooldown = 10;      // GRACE: 10 saniye

const uint8_t PIN_LED    = 2;    // LED çıkışı (AKTİF HIGH: HIGH=yanık, LOW=sönük)

// --- Kullanıcı aksiyonu için boştaki pin (iki/üç kısa basış) ---
const uint8_t PIN_ACTION = 16;   // D0 (GPIO16) – iki/üç basışa göre HIGH/LOW

// --- Recovery pin (PWM üretilecek) ---
const uint8_t PIN_RECOVERY = 13; // D7 (GPIO13) – 5 veya 20 kez basışta PWM

// ==== Zamanlamalar ====
const uint32_t DEBOUNCE_MS   = 30;
const uint32_t LONGPRESS_MS  = 5000;
const uint32_t PRE_OFF_MS    = 120;  // (artık blok kullanılmıyor, GRACE var)

// --- Çoklu basış takibi ---
const uint32_t MULTI_PRESS_WINDOW_MS = 1000;               // 1 sn içinde art arda basış
const uint32_t RECOVERY_SIGNAL_DURATION_MS = 15000;        // Varsayılan: 15 sn

// ==== Dahili durum ====
bool     btnState     = false;
bool     lastBtnState = false;
bool     recordState  = false;
uint32_t lastChange   = 0;
uint32_t pressStart   = 0;
uint8_t  pressCounter = 0;
uint32_t lastPressEdgeTs = 0;

// YENİ: Düşük batarya nedeniyle kapatmanın aktif olup olmadığını belirtir.
bool     lowBatteryShutdownActive = false;

// YENİ: Kapatma sırasında açma isteği gelirse bunu true yap.
bool     rebootRequested = false;

// YENİ: Boot sırasında kapatma isteği gelirse bunu true yap.
bool     shutdownRequestedDuringBoot = false;

// --- Recovery durum ---
bool     recoveryTriggered = false;
uint32_t recoveryTriggerTs = 0;
uint32_t recoveryDurationMs = RECOVERY_SIGNAL_DURATION_MS; // AKTİF PWM için süre

// ===================== ESP8266: PWM ile Yüzdelik SOC =====================

// --- Pin ve PWM konfig ---
const uint8_t  PWM_PIN   = 15;   // D8 (GPIO15) – SOC PWM çıkışı
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

// === SOC PWM'i durdur (GRACE sırasında) ===
void socPwmStop() {
  analogWrite(PWM_PIN, 0);
  // g_socAvgLatest sabit kalsın; tekrar başlatınca yeniden ayarlanacak
}

// PIN_RECOVERY üstünde belirtilen süre boyunca PWM üret
void startRecoveryPwm(uint16_t duty_0_1000, uint32_t duration_ms) {
  if (duty_0_1000 > PWM_RANGE) duty_0_1000 = PWM_RANGE;
  pinMode(PIN_RECOVERY, OUTPUT);
  analogWrite(PIN_RECOVERY, duty_0_1000);
  recoveryDurationMs = duration_ms;
  recoveryTriggered  = true;
  recoveryTriggerTs  = millis();      // referans zaman
  Serial.printf("[RECOVERY] PWM başladı: duty=%u/%u (~%u%%), süre=%lus\n",
                duty_0_1000, PWM_RANGE,
                (unsigned)((100UL * duty_0_1000) / PWM_RANGE),
                (unsigned)(duration_ms/1000));
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

// Kapatma işleminin sebebini netleştirmek için
enum ShutdownReason : uint8_t {
  USER_REQUEST, // Kullanıcı 5sn uzun basış yaptı
  LOW_BATTERY   // Sistem bataryanın düşük olduğunu tespit etti
};

// Led modları
enum LedMode : uint8_t { LED_NORMAL=0, LED_RECORD=1, LED_LOW_BATT=2 };

LedMode  ledMode = LED_NORMAL;
bool     ledStateOn = true;
uint32_t ledTs = 0;

// Düşük batarya alarmı için durum değişkenleri
uint8_t  lowBattBlinkCount = 0;
bool     lowBattInPause    = false;

// GRACE (kapanma bekleme) durumu
bool     shuttingDown = false;      // true => 20 sn GRACE penceresi aktif
uint32_t shutdownStartTs = 0;       // GRACE başlangıç zamanı

inline void ledSet(bool on) {
  digitalWrite(PIN_LED, on ? HIGH : LOW); // aktif HIGH
  ledStateOn = on;
}

void enterRecoveryLockdown() {
  if (recoveryLockdownActive) return;
  recoveryLockdownActive = true;
  recoveryLockStartTs = millis();
  Serial.println("[RECOVERY-LOCK] 3 dakikalık kilit modu BAŞLADI.");

  // Yan işlevleri anında devre dışı bırak
  if (otaModeActive) exitOtaMode();     // OTA/Web kapat
  // Kapanma sürecinde ise iptal et ve normale dön (ESP kapanmasın)
  if (shuttingDown) {
    abortShutdownGraceAndResume();      // LATCH HIGH kalır, Pi kapanmaz
  }

  // Kullanıcıya stabil durum göstergesi (isteğe göre değiştirilebilir)
  ledMode = LED_NORMAL;
  ledSet(true);
}

void exitRecoveryLockdown() {
  if (!recoveryLockdownActive) return;
  recoveryLockdownActive = false;
  Serial.println("[RECOVERY-LOCK] Kilit modu BİTTİ. Normal moda dönüldü.");

  // LED modunu mevcut duruma göre normalle
  ledMode = recordState ? LED_RECORD : LED_NORMAL;
  ledSet(true);
  ledTs = millis();
}

// --- YENİ: LED Zamanlamaları ---
// Kayıt modu (Yavaş "Nefes Alma")
const uint32_t REC_ON_MS  = 1000;
const uint32_t REC_OFF_MS = 500;

// OTA modu (Orta Hızda)
const uint32_t OTA_ON_MS  = 150;
const uint32_t OTA_OFF_MS = 150;

// Düşük Batarya Alarm Paterni (3 hızlı yanıp sön + 1 sn duraklama)
const uint32_t LB_BLINK_ON_MS  = 100;
const uint32_t LB_BLINK_OFF_MS = 100;
const uint32_t LB_PAUSE_MS     = 1000;

// ===================== LED DURUM MAKİNESİ (Yeniden Yazıldı) =====================
// DİKKAT: Bu fonksiyon, farklı LED modlarını ve önceliklerini yöneten bir durum makinesidir.
// 1. Öncelik: Kullanıcı tarafından başlatılan kapatma -> LED anında söner.
// 2. Öncelik: Düşük batarya alarmı -> Özel alarm paterni.
// 3. Öncelik: Diğer modlar (OTA, Kayıt, Normal).
void ledUpdate(uint32_t now) {
  // YENİ ÖNCELİK: Boot sırasında kapatma istenmişse, LED sönük kalmalı.
  if (rpiBootGuardActive && shutdownRequestedDuringBoot) {
    if (ledStateOn) {
      ledSet(false);
    }
    return; // Başka hiçbir LED mantığını işleme.
  }

  // 1. ÖNCELİK: Kapatma süreci yönetimi
  if (shuttingDown) {
    // Eğer yeniden başlatma istenmişse, LED'in YANIK kalmasını sağla.
    if (rebootRequested) {
      if (!ledStateOn) {
        ledSet(true);
      }
    }
    // Düşük batarya kapatması ise, alarm paterni devam etsin.
    else if (lowBatteryShutdownActive) {
      if (lowBattInPause) {
        // Duraklama fazındayız. Sürenin dolmasını bekle.
        if (now - ledTs >= LB_PAUSE_MS) {
          lowBattInPause = false;
          lowBattBlinkCount = 0;
          ledTs = now;
          ledSet(true); // Yeni yanıp sönme döngüsünü başlat.
        }
      } else {
        // Yanıp sönme fazındayız.
        uint32_t phase_duration = ledStateOn ? LB_BLINK_ON_MS : LB_BLINK_OFF_MS;
        if (now - ledTs >= phase_duration) {
          ledTs = now;
          ledSet(!ledStateOn);

          // Eğer LED'i şimdi söndürdüysek, bir yanıp sönme tamamlandı.
          if (!ledStateOn) {
            lowBattBlinkCount++;
            // 3 yanıp sönme tamamlandıysa, duraklama fazına geç.
            if (lowBattBlinkCount >= 3) {
              lowBattInPause = true;
            }
          }
        }
      }
    }
    // Normal kullanıcı kapatması ise, LED'in SÖNÜK kalmasını sağla.
    else {
      if (ledStateOn) {
        ledSet(false);
      }
    }
    return; // Kapatma sürecinde başka LED mantığı işleme.
  }

  // 2. ÖNCELİK: Düşük Batarya Alarm Modu
  if (ledMode == LED_LOW_BATT) {
    if (lowBattInPause) {
      // Duraklama fazındayız. Sürenin dolmasını bekle.
      if (now - ledTs >= LB_PAUSE_MS) {
        lowBattInPause = false;
        lowBattBlinkCount = 0;
        ledTs = now;
        ledSet(true); // Yeni yanıp sönme döngüsünü başlat.
      }
    } else {
      // Yanıp sönme fazındayız.
      uint32_t phase_duration = ledStateOn ? LB_BLINK_ON_MS : LB_BLINK_OFF_MS;
      if (now - ledTs >= phase_duration) {
        ledTs = now;
        ledSet(!ledStateOn);

        // Eğer LED'i şimdi söndürdüysek, bir yanıp sönme tamamlandı.
        if (!ledStateOn) {
          lowBattBlinkCount++;
          // 3 yanıp sönme tamamlandıysa, duraklama fazına geç.
          if (lowBattBlinkCount >= 3) {
            lowBattInPause = true;
          }
        }
      }
    }
    return;
  }

  // 3. ÖNCELİK: Kayıt ve OTA Modu
  if (ledMode == LED_RECORD) {
    uint32_t phase_on, phase_off;
    if (otaModeActive) {
      phase_on = OTA_ON_MS;
      phase_off = OTA_OFF_MS;
    } else {
      phase_on = REC_ON_MS;
      phase_off = REC_OFF_MS;
    }

    uint32_t phase_duration = ledStateOn ? phase_on : phase_off;
    if (now - ledTs >= phase_duration) {
      ledTs = now;
      ledSet(!ledStateOn);
    }
    return;
  }

  // Varsayılan Mod: Normal Çalışma
  if (ledMode == LED_NORMAL) {
    if (!ledStateOn) {
      ledSet(true); // LED sürekli yanık kalmalı.
    }
    return;
  }
}

// ===================== OTA (Wi-Fi + Web Sunucu) =====================
void enterOtaMode() {
  if (recoveryLockdownActive) {
    Serial.println("[RECOVERY-LOCK] OTA talebi yoksayıldı.");
    return;
  }
  if (otaModeActive || shuttingDown) return; // GRACE'te OTA'ya girme
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
      "button{padding:10px 12px;font-size:14px;cursor:pointer}.err{color:#b00020;margin:8px 0}</style></head><body>");
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
  html += String("<p>IP: ") + WiFi.localIP().toString() + "</p>";
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

// ===================== GRACEFUL SHUTDOWN YENİ MİMARİSİ =====================
// 5 sn uzun basış sonrası: 20 sn GRACE penceresi
// - LED sönük
// - ADC/SOC PWM ve diğer işlemler duraklatılır
// - ESP butonu okumaya devam eder
// - Bu süre içinde butona tekrar basılırsa iptal edilir ve sistem devam eder
// - Süre dolarsa RPi güç kesilir ve LATCH LOW ile ESP kapatılır

void beginShutdownGrace(ShutdownReason reason) {
  if (recoveryLockdownActive) {
    Serial.println("[RECOVERY-LOCK] GRACE isteği kilitte yoksayıldı.");
    return;
  }
  if (shuttingDown) return;
  shuttingDown = true;
  shutdownStartTs = millis();
  rebootRequested = false; // Her yeni kapatma sürecinde bu bayrağı sıfırla.

  if (reason == LOW_BATTERY) {
    lowBatteryShutdownActive = true;
  } else {
    lowBatteryShutdownActive = false;
  }

  rpiStartPending = false;

  Serial.println("=== GRACE Başladı ===");

  // KULLANICI GERİ BİLDİRİMİ: LED'i anında söndür.
  ledSet(false);

  if (otaModeActive) exitOtaMode();
  socPwmStop();
  if (recoveryTriggered) {
    analogWrite(PIN_RECOVERY, 0);
    digitalWrite(PIN_RECOVERY, LOW);
    recoveryTriggered = false;
  }

  recordState = false;
  digitalWrite(PIN_ACTION, LOW);

  if (!lowBatteryShutdownActive || !rpiBootGuardActive) {
      Serial.println("Raspberry Pi kapatma sinyali gönderiliyor (RPIS LOW)...");
      digitalWrite(RPIS_PIN, LOW);
  }
}

void abortShutdownGraceAndResume() {
  if (!shuttingDown) return;

  Serial.println("=== GRACE İptal Edildi: Sistem devam edecek ===");
  shuttingDown = false;

  // YENİ: Düşük batarya kapatma bayrağını temizle
  lowBatteryShutdownActive = false;

  // RPi'yi ŞİMDİ değil; 20 sn SONRA başlatacağız
  // (Önce RPIS'i normal çalışma seviyesine al)
  digitalWrite(RPIS_PIN, HIGH);

  // LED, SOC vb. hemen devreye girsin
  ledMode = recordState ? LED_RECORD : LED_NORMAL;
  ledSet(true);
  ledTs = millis();

  // SOC/PWM'i hızlı stabilize et
  primeAndReadInitialSOC();

  // 20 sn sonra power-cycle ile başlatmayı planla
  rpiStartPending = true;
  rpiStartDueTs   = millis() + RPI_START_DELAY_MS;
  Serial.println("[GRACE] RPi başlatma 20 sn ertelendi (power-cycle planlandı).");
}

void requestRebootDuringShutdown() {
  if (!shuttingDown || rebootRequested) return; // Sadece kapanırken ve istek zaten yoksa çalışsın.

  Serial.println("=== GRACE sırasında AÇMA isteği alındı ===");
  rebootRequested = true;

  // KULLANICI GERİ BİLDİRİMİ: LED'i anında yak.
  ledSet(true);
}

// ===================== Çoklu Basışı Ertelenmiş Değerlendirme =====================
void evaluatePressSequence() {
  if (recoveryLockdownActive) {
    // Recovery kilidi varken yeni sekanslar işlenmez
    pressCounter = 0;
    Serial.println("[RECOVERY-LOCK] Basış sekansı yoksayıldı.");
    return;
  }
  uint8_t n = pressCounter;
  pressCounter = 0;

  if (shuttingDown || recoveryTriggered) {
    Serial.printf("[PRESS] Değerlendirme atlandı (shutdown=%d, recovery=%d)\n",
                  shuttingDown, recoveryTriggered);
    return;
  }
  if (n == 0) return;

  Serial.printf("[PRESS] Sekans tamamlandı: %u kez\n", n);

  if (n == 20) {
    // 20x → %75 duty, 15 sn recovery PWM
    startRecoveryPwm((PWM_RANGE * 3) / 4, RECOVERY_SIGNAL_DURATION_MS);
    enterRecoveryLockdown(); // 3 dk kilit modu
  }
  else if (n == 10) {
    // 10x → OTA toggle
    if (otaModeActive) {
      Serial.println("[OTA] 10x -> OTA kapatılıyor");
      exitOtaMode();
    } else {
      Serial.println("[OTA] 10x -> OTA açılıyor");
      enterOtaMode();
    }
  }
  else if (n == 5) {
    // 5x → %25 duty, 5 sn recovery PWM
    if (!recoveryTriggered) startRecoveryPwm(PWM_RANGE / 4, 5000);
  }
  else if (n == 3) {
    // 3x → Kayıt kapat
    if (recordState) {
      recordState = false;
      digitalWrite(PIN_ACTION, LOW);
      Serial.println("[ACTION] 3x -> Kayıt Kapat");
    }
  }
  else if (n == 2) {
    // 2x → Kayıt aç
    if (!recordState) {
      recordState = true;
      digitalWrite(PIN_ACTION, HIGH);
      Serial.println("[ACTION] 2x -> Kayıt Aç");
    }
  }
  else {
    Serial.println("[PRESS] Eşleşen eylem yok (yoksayıldı).");
  }
}

// ===================== setup / loop =====================
void setup() {
  Serial.begin(9600);
  Serial.println("Sistem başlatılıyor...");

  // LATCH pini, sistemin açık kalması için ilk olarak HIGH yapılmalı.
  pinMode(PIN_LATCH, OUTPUT);
  digitalWrite(PIN_LATCH, HIGH);
  Serial.println("LATCH HIGH: LDO açık.");

  // --- DÜZELTME: RPi pinlerini doğru başlangıç durumuyla yapılandır ---
  // RPI_PIN'i doğrudan LOW (güç açık) olarak başlatarak kararsız güç döngüsünü engelle.
  pinMode(RPI_PIN, OUTPUT);
  digitalWrite(RPI_PIN, LOW);     // DOĞRU BAŞLANGIÇ: Güç kesintisiz olarak AÇIK.

  // RPIS_PIN (shutdown sinyali) normal çalışma durumu olan HIGH'da başlamalı.
  pinMode(RPIS_PIN, OUTPUT);
  digitalWrite(RPIS_PIN, HIGH);

  pinMode(PIN_LED, OUTPUT);
  pinMode(PIN_SENSE, INPUT);
  pinMode(BatteryVal, INPUT);

  pinMode(PIN_ACTION, OUTPUT);
  digitalWrite(PIN_ACTION, LOW);

  pinMode(PIN_RECOVERY, OUTPUT);
  digitalWrite(PIN_RECOVERY, LOW);

  ledSet(true);
  ledTs = millis();
  ledMode = LED_NORMAL;

  // DÜZELTME: PWM'i başlatmadan önce SOC'yi oku
  socPwmInit();
  float initialSoc = primeAndReadInitialSOC();
  Serial.printf("Başlangıç SOC=%.1f%%\n", initialSoc);

  // ÖNEMLİ: Eşiği voltaj düşüşüne karşı güvenlik payı içerecek şekilde %20'ye yükseltelim.
  if (initialSoc < 20.0f) {
    Serial.println("Başlangıç SOC %20'nin altında! RPi başlatılmayacak, düşük batarya uyarısı verilecek ve sistem kapanacak.");
    ledMode = LED_LOW_BATT;
    ledTs = millis();
    ledSet(true); // Yanıp sönmeye başlaması için ilk durumu ayarla

    // Uyarı LED'inin 5 saniye görünür olmasını sağla
    shutdown_cooldown = 5;

    // Kapatmanın düşük batarya kaynaklı olduğunu işaretle
    lowBatteryShutdownActive = true;

    // GRACE'e doğrudan gir
    beginShutdownGrace(LOW_BATTERY);
    // DİKKAT: return burada kalmalı ki RPi'ı başlatan kod çalışmasın.
    return;
  }

  Serial.println("Raspberry Pi başlatılıyor...");
  digitalWrite(RPI_PIN, LOW);
  delay(100);
  digitalWrite(RPIS_PIN, HIGH);
  delay(50);

  rpiBootGuardActive = true;
  rpiBootStartTs = millis();
  Serial.printf("Orange Pi boot koruması başladı (30 saniye). Boot tamamlanana kadar GRACE modu engellendi.\n");

  Serial.println("Sistem hazır.");
}

void loop() {
  uint32_t now = millis();

  // --- RECOVERY KİLİT denetimi (3 dk boyunca tüm yan işlevler engellenir) ---
  if (recoveryLockdownActive) {
    // Güvence: kilitte iken GRACE ve OTA kesinlikle kapalı kalsın
    if (shuttingDown) abortShutdownGraceAndResume();
    if (otaModeActive) exitOtaMode();

    // Kilit süresi doldu mu?
    if (now - recoveryLockStartTs >= RECOVERY_LOCK_MS) {
      exitRecoveryLockdown();
    }
  }

  // --- Boot koruma süresini kontrol et ---
  if (rpiBootGuardActive) {
    if ((now - rpiBootStartTs) >= RPI_BOOT_GUARD_MS) {
      rpiBootGuardActive = false;
      Serial.println("[BOOT GUARD] Orange Pi boot koruması sona erdi. Sistem normal çalışıyor.");

      // Boot sırasında istenen kapatma var mıydı?
      if (shutdownRequestedDuringBoot) {
        Serial.println("[BOOT GUARD] Boot sırasında istenen kapatma işlemi şimdi başlatılıyor.");
        shutdownRequestedDuringBoot = false; // Bayrağı temizle.
        beginShutdownGrace(USER_REQUEST);    // Normal kapatma sürecini başlat.
      }
    }
  }

  // --- OTA modu: sadece GRACE değil ve kilit yokken çalıştır ---
  if (!shuttingDown && !recoveryLockdownActive && otaModeActive) {
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
      static uint32_t lastLog = 0;
      if (now - otaStartTs < OTA_CONNECT_INFO_MS) {
        if (now - lastLog >= 1000) {
          lastLog = now;
          Serial.printf("[OTA] Wi-Fi durumu: %d (0=Idle,3=Bağlı)\n", WiFi.status());
        }
      }
    }
  }

  // --- PWM/SOC periyodik güncelle (GRACE değilken) ---
  if (!shuttingDown && (now - lastSocUpdate >= SOC_UPDATE_MS)) {
    updateSocPwm();
    lastSocUpdate = now;
  }

  // ==== LED mod seçim mantığı (GRACE değilken) ====
  if (!shuttingDown) {
    if (g_socAvgLatest < 10.0f) {
      if (ledMode != LED_LOW_BATT) {
        ledMode = LED_LOW_BATT;
        lowBattBlinkCount = 0;
        ledTs = now;
        ledSet(true);

        lowBatteryShutdownActive = true;
        shutdown_cooldown = 5;

        beginShutdownGrace(LOW_BATTERY);
      }
    } else {
      LedMode desired = otaModeActive ? LED_RECORD : (recordState ? LED_RECORD : LED_NORMAL);
      if (ledMode != desired) {
        ledMode = desired;
        ledTs = now;
        ledSet(true);
      }
    }
  }

  // DÜZELTME: LED güncelleme fonksiyonu HER ZAMAN çalışmalı.
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
        // Basış kenarı: SADECE SAY (kilitte sayma yapma)
        pressStart = now;
        lastPressEdgeTs = now;
        Serial.println("Butona basıldı.");

        if (rpiBootGuardActive && shutdownRequestedDuringBoot) {
          Serial.println("[BOOT GUARD] Sıraya alınmış kapatma isteği iptal edildi.");
          shutdownRequestedDuringBoot = false;
          ledSet(true);
          pressCounter = 0;
        }
        else if (shuttingDown) {
          requestRebootDuringShutdown();
          pressCounter = 0;
        } else {
          if (!recoveryLockdownActive) {
            pressCounter++;
            Serial.printf("pressCounter = %u\n", pressCounter);
          } else {
            Serial.println("[RECOVERY-LOCK] Basış görmezden gelindi.");
          }
        }
      } else {
        Serial.println("Buton bırakıldı.");
      }
    }
  }

  // Uzun basış (güvenli kapatma GRACE başlat) — kilitte devre dışı
  if (!shuttingDown && !recoveryLockdownActive && btnState &&
      (pressCounter <= 1) && (now - pressStart >= LONGPRESS_MS) && !recoveryTriggered) {
    if (rpiBootGuardActive) {
      if (!shutdownRequestedDuringBoot) {
        Serial.println("[BOOT GUARD] Kapatma isteği sıraya alındı. Boot bitince kapatılacak.");
        shutdownRequestedDuringBoot = true;
        ledSet(false);
      }
    } else {
      Serial.println("Uzun basış algılandı -> Kapatma GRACE başlatılıyor.");
      beginShutdownGrace(USER_REQUEST);
    }
    pressCounter = 0;
  }

  // 1 sn içinde yeni basış yoksa: SEKANSI DEĞERLENDİR — kilitte işlem yapma
  if (!shuttingDown && pressCounter > 0 &&
      (now - lastPressEdgeTs >= MULTI_PRESS_WINDOW_MS) &&
      !recoveryTriggered) {
    evaluatePressSequence();
  }

  // Recovery modu aktifse PWM'i süre bitince kapat (kilitten bağımsız)
  if (recoveryTriggered) {
    uint32_t elapsed = millis() - recoveryTriggerTs;
    if (elapsed >= recoveryDurationMs) {
      analogWrite(PIN_RECOVERY, 0);        // PWM duty 0
      digitalWrite(PIN_RECOVERY, LOW);     // hat üzerinde LOW
      recoveryTriggered = false;
      Serial.println("[RECOVERY] PWM süresi doldu, PIN_RECOVERY kapatıldı (LOW).");
    }
  }

  // GRACE akışı (kilitte GRACE başlatılamaz, fakat eğer önceden başladıysa burada tamamlanır)
  if (shuttingDown) {
    uint32_t elapsed = millis() - shutdownStartTs;

    // AŞAMA 1: Pi'nin kapanmasını bekle (shutdown_cooldown sn)
    if (elapsed >= (uint32_t)shutdown_cooldown * 1000UL) {

      // AŞAMA 2: Pi'nin gücünü kes ve 2 saniye bekle
      digitalWrite(RPI_PIN, HIGH);
      Serial.println("Raspberry Pi gücü kesildi (RPI_PIN HIGH).");
      delay(2000); // kritik bekleme

      // AŞAMA 3: Karar anı - yeniden başlat mı, tamamen kapat mı?
      if (rebootRequested) {
        Serial.println("Yeniden başlatma isteği var. Pi'ye güç veriliyor...");
        digitalWrite(RPI_PIN, LOW);
        digitalWrite(RPIS_PIN, HIGH); // Sinyal pinini normale döndür.

        // Sistemi normal çalışma moduna döndür.
        shuttingDown = false;
        rebootRequested = false;
        rpiBootGuardActive = true; // Yeniden boot korumasını başlat.
        rpiBootStartTs = millis();
        primeAndReadInitialSOC(); // SOC okumayı yeniden başlat.
        ledSet(true);
        ledMode = LED_NORMAL;

        Serial.println("Sistem yeniden başlatıldı.");

      } else {
        Serial.println("Tamamen kapatılıyor. LATCH LOW.");
        digitalWrite(PIN_LATCH, LOW);
        // Güç kesilir.
      }
    }
  }
}