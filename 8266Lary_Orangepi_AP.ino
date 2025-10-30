/*  Soft-Latch Güç Yönetimi – ESP8266MOD + AP2112K
 *  LATCH: EN hattını tutar (HIGH = açık, LOW = kapalı)
 *  SENSE: Buton algısı (HIGH = basılı, LOW = bırakıldı)
 *  Yazar: simcleverY / Rise Teknoloji
 */

#include <Arduino.h>

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

// --- Recovery pin (10 kez basış) ---
const uint8_t PIN_RECOVERY = 13; // D7 (GPIO13) – 10 kez basışta HIGH

// ==== Zamanlamalar ====
const uint32_t DEBOUNCE_MS   = 30;
const uint32_t LONGPRESS_MS  = 5000;
const uint32_t PRE_OFF_MS    = 120;

// --- Çoklu basış takibi ---
const uint32_t MULTI_PRESS_WINDOW_MS = 1000;  // 1 sn içinde art arda basış
const uint32_t RECOVERY_SIGNAL_DURATION_MS = 15000; // Recovery sinyali 15 saniye HIGH kalacak

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

// ===================== ESP8266: PWM ile Yüzdelik SOC (Eşitlenmiş) =====================
// 1 kHz PWM, 0..1000 aralık. ADC→%SOC→duty dönüşümü + hareketli ortalama.

// --- Pin ve PWM konfig ---
const uint8_t  PWM_PIN   = 15;   // D8 (GPIO15) — D6 (GPIO12) RPIS_PIN ile çakıştığı için D8
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

// Yardımcılar
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

// === YENİ: İlk açılışta güvenilir SOC okuması için tampon doldurma ===
float primeAndReadInitialSOC(uint8_t samples = NUM_READINGS, uint16_t settle_ms = 2) {
  float acc = 0.0f;
  for (uint8_t i = 0; i < samples; ++i) {
    int adc = analogRead(BatteryVal);
    float soc = calcSOC_fromADC(adc);
    acc += soc;
    delay(settle_ms);
  }
  float avg = acc / (float)samples;

  // Tamponu bu ortalama ile doldur ki ilk döngü stabil başlasın
  for (uint8_t i = 0; i < NUM_READINGS; ++i) socReadings[i] = avg;
  socIndex = 0;
  g_socAvgLatest = avg;

  // PWM'i de buna göre ayarla
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
  // 1) ADC oku
  int adc = analogRead(BatteryVal);

  // 2) %SOC hesapla + hareketli ortalama
  float soc = calcSOC_fromADC(adc);
  socReadings[socIndex] = soc;
  socIndex = (uint8_t)((socIndex + 1) % NUM_READINGS);

  float socAvg = 0.0f;
  for (int i = 0; i < NUM_READINGS; ++i) socAvg += socReadings[i];
  socAvg /= (float)NUM_READINGS;

  // 3) % → PWM duty (0..1000)
  socAvg = clampf(socAvg, 0.0f, 100.0f);
  int duty = (int)((socAvg / 100.0f) * 1000.0f + 0.5f);

  #if ACTIVE_LOW
    duty = 1000 - duty;
  #endif

  if (duty < 0) duty = 0;
  if (duty > 1000) duty = 999;

  // 4) PWM çıkışı uygula
  analogWrite(PWM_PIN, duty);

  // LED mantığı için son SOC'u paylaş
  g_socAvgLatest = socAvg;

  // Debug:
  Serial.printf("ADC=%d | SOC(avg)=%.1f%% -> duty=%d/1000\n", adc, socAvg, duty);
}

// ===================== LED DURUM MAKİNESİ =====================
// Modlar: NORMAL (sürekli yanık), RECORD_BLINK (2s on / 1s off),
// LOW_BATT_ALERT (10 kez 300ms on / 300ms off, sonra shutdown)
enum LedMode : uint8_t { LED_NORMAL=0, LED_RECORD=1, LED_LOW_BATT=2 };

LedMode  ledMode = LED_NORMAL;
bool     ledStateOn = true;              // LED mantıksal "yanık/sönük" durumu
uint32_t ledTs = 0;                      // son değişim zaman damgası
uint8_t  lowBattCycles = 0;              // tamamlanan on+off döngü sayısı
bool     shuttingDown = false;           // kapanma tetiklendi mi

// AKTİF HIGH LED için yardımcı (AÇIK=HIGH, KAPALI=LOW)
inline void ledSet(bool on) {
  digitalWrite(PIN_LED, on ? HIGH : LOW); // aktif HIGH
  ledStateOn = on;
}

// Kayıt modu faz zamanları (ms)
const uint32_t REC_ON_MS  = 1000;
const uint32_t REC_OFF_MS = 500;

// Düşük batarya blink periyodu (ms)
const uint32_t LB_PHASE_MS = 300;

// Her döngüde LED’i güncelle
void ledUpdate(uint32_t now) {
  if (shuttingDown) return;

  // Öncelik: düşük batarya uyarısı diğer modları ezer
  if (ledMode == LED_LOW_BATT) {
    // 300ms'de bir toggle; off fazı tamamlandığında 1 cycle say
    if (now - ledTs >= LB_PHASE_MS) {
      ledTs = now;
      ledSet(!ledStateOn);
      if (!ledStateOn) {
        // az önce sönük faza geçildi => bir on+off tamamlandı
        lowBattCycles++;
        if (lowBattCycles >= 10) {
          shuttingDown = true;
          gracefulShutdown();
        }
      }
    }
    return;
  }

  // LED_RECORD: Kayıt modunda yanıp sönme (1s ON / 0.5s OFF)
  if (ledMode == LED_RECORD) {
    uint32_t phase_duration = ledStateOn ? REC_ON_MS : REC_OFF_MS;
    if (now - ledTs >= phase_duration) {
      ledTs = now;
      ledSet(!ledStateOn);
    }
    return;
  }

  // LED_NORMAL: sürekli yanık
  if (!ledStateOn) ledSet(true);
}

// =====================================================================

void gracefulShutdown() {
  Serial.println("=== Graceful Shutdown Başladı ===");
  Serial.println("Uygulama kapanış işlemleri...");

  delay(10);
  Serial.println("LED uyarısı veriliyor...");
  // Kapanış uyarısı: kısa bir yanıp sönme
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

  // Açılışta LED sürekli yanık (AKTİF HIGH)
  ledSet(true);
  ledTs = millis();
  ledMode = LED_NORMAL;

  // PWM/SOC başlat
  socPwmInit();

  // === YENİ: RPi'yi başlatmadan ÖNCE batarya kontrolü ===
  float initialSoc = primeAndReadInitialSOC();   // tamponu doldur + SOC hesapla
  Serial.printf("Başlangıç SOC=%.1f%%\n", initialSoc);

  if (initialSoc < 15.0f) {
    Serial.println("Başlangıç SOC %15'in altında! RPi başlatılmayacak, düşük batarya uyarısı verilecek ve sistem kapanacak.");
    // Düşük batarya uyarı moduna geç; 10 döngü sonunda gracefulShutdown() çağrılır
    ledMode = LED_LOW_BATT;
    lowBattCycles = 0;
    ledTs = millis();
    ledSet(true);
    shutdown_cooldown = 3;
    // setup burada biter; loop() LED_LOW_BATT akışını çalıştırıp kapanışı tamamlar
    return;
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

  // --- PWM/SOC periyodik güncelle ---
  if (now - lastSocUpdate >= SOC_UPDATE_MS) {
    updateSocPwm();
    lastSocUpdate = now;
  }

  // ==== LED mod seçim mantığı ====
  // Düşük batarya %10 altına inince 10 kez uyar + shutdown
  if (!shuttingDown) {
    if (g_socAvgLatest < 10.0f) {
      if (ledMode != LED_LOW_BATT) {
        ledMode = LED_LOW_BATT;
        lowBattCycles = 0;
        ledTs = now;
        ledSet(true);
      }
    } else {
      // Kayıt moduna göre tercih: recordState -> RECORD, değilse NORMAL
      LedMode desired = recordState ? LED_RECORD : LED_NORMAL;
      if (ledMode != desired) {
        ledMode = desired;
        ledTs = now;
        if (ledMode == LED_RECORD) {
          ledSet(true);  // 2s ON ile başlasın
        } else {
          ledSet(true);  // NORMAL: sürekli yanık
        }
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

        // Recovery kontrolü - 10 kez basış
        if (pressCounter == 10 && !recoveryTriggered) {
          recoveryTriggered = true;
          recoveryTriggerTs = now;
          digitalWrite(PIN_RECOVERY, HIGH);
          Serial.println("[RECOVERY] 10 kez basış algılandı -> PIN_RECOVERY = HIGH (15 saniye)");
          pressCounter = 0; // Sayacı sıfırla
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

  // 1 sn içinde yeni basış yoksa sayaç sıfırla (ama recovery aktifse sıfırlama)
  if (pressCounter > 0 && (now - lastPressEdgeTs >= MULTI_PRESS_WINDOW_MS) && !recoveryTriggered) {
    Serial.println("Basış sayacı sıfırlandı (timeout).");
    pressCounter = 0;
  }

  // Recovery modu aktifse
  if (recoveryTriggered) {
    // Recovery pinini 15 saniye boyunca HIGH tut
    if (now - recoveryTriggerTs >= RECOVERY_SIGNAL_DURATION_MS) {
      digitalWrite(PIN_RECOVERY, LOW);
      recoveryTriggered = false;
      Serial.println("[RECOVERY] Recovery sinyali sonlandı (PIN_RECOVERY = LOW).");
    }
  }

  delay(5);
}
