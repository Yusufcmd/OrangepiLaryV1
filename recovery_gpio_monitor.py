#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GPIO Recovery Monitor - ESP8266 Tabanlı - factoryctl ile
ESP8266'dan gelen recovery sinyalini (GPIO13) dinler.
ESP8266 butona 10 kez basıldığında PIN_RECOVERY'yi (GPIO13) 15 saniye HIGH yapar.
Bu sinyal Orange Pi'nin bir GPIO pinine bağlanır ve factoryctl ile AP modunda recovery yapar.
"""

import os
import sys
import time
import signal
import subprocess
import threading

try:
    import gpiod
except ImportError as e:
    raise SystemExit("gpiod modülü bulunamadı. 'sudo apt install -y python3-libgpiod gpiod'") from e

# ==================== YAPILANDIRMA ====================
GPIO_CHIP = "/dev/gpiochip1"
GPIO_OFFSET = 76  # Orange Pi'de ESP8266'ın PIN_RECOVERY (GPIO13) bağlanacak pin
ACTIVE_HIGH = True  # HIGH = recovery tetikle

TRIGGER_DURATION = 10.0  # 10 saniye boyunca HIGH (ESP8266 15 saniye gönderiyor)
POLL_INTERVAL = 0.05  # 50ms polling

# factoryctl ile recovery
FACTORYCTL_BIN = "/usr/local/sbin/factoryctl"
FACTORY_DIR = "/opt/factory"

# LED kontrolü için PI2 pini
GPIO_LED_CHIP = "/dev/gpiochip1"
GPIO_LED_OFFSET = 258  # PI2 pini (GPIO 258)
LED_BLINK_INTERVAL = 0.3  # 0.3 saniye HIGH, 0.3 saniye LOW

# ======================================================

# LED kontrolü için global değişkenler
_led_line = None
_led_chip = None
_led_blink_stop = threading.Event()
_led_blink_thread = None

def setup_led_gpio():
    """PI2 pinini çıkış olarak ayarla (recovery LED'i için)"""
    global _led_line, _led_chip
    try:
        _led_chip = gpiod.Chip(GPIO_LED_CHIP)
        _led_line = _led_chip.get_line(GPIO_LED_OFFSET)
        _led_line.request(consumer="recovery-led", type=gpiod.LINE_REQ_DIR_OUT, default_vals=[0])
        print(f"✓ LED GPIO (PI2) hazır: {GPIO_LED_CHIP}:{GPIO_LED_OFFSET}")
        return True
    except Exception as e:
        print(f"⚠ LED GPIO açılamadı: {e}")
        return False

def set_led(state: bool):
    """LED'i aç/kapa (PI2 pini HIGH/LOW)"""
    global _led_line
    if _led_line is None:
        return
    try:
        _led_line.set_value(1 if state else 0)
    except Exception as e:
        print(f"LED set hatası: {e}")

def cleanup_led_gpio():
    """LED GPIO kaynaklarını serbest bırak"""
    global _led_line, _led_chip
    try:
        if _led_line is not None:
            _led_line.set_value(0)  # LED'i kapat
            _led_line.release()
            _led_line = None
    except Exception:
        pass
    try:
        if _led_chip is not None:
            _led_chip.close()
            _led_chip = None
    except Exception:
        pass

def led_blink_loop():
    """Recovery sırasında LED'i yanıp söndür (0.3s HIGH, 0.3s LOW)"""
    while not _led_blink_stop.is_set():
        set_led(True)
        time.sleep(LED_BLINK_INTERVAL)
        if _led_blink_stop.is_set():
            break
        set_led(False)
        time.sleep(LED_BLINK_INTERVAL)
    # Döngüden çıkarken LED'i kapat
    set_led(False)

def start_led_blink():
    """LED yanıp sönme thread'ini başlat"""
    global _led_blink_thread, _led_blink_stop
    _led_blink_stop.clear()
    _led_blink_thread = threading.Thread(target=led_blink_loop, daemon=True)
    _led_blink_thread.start()
    print("LED yanıp sönme başladı (0.3s aralıklar)")

def stop_led_blink():
    """LED yanıp sönmeyi durdur"""
    global _led_blink_stop
    _led_blink_stop.set()
    if _led_blink_thread is not None:
        _led_blink_thread.join(timeout=1.0)
    set_led(False)

def open_chip(path):
    """GPIO chip'i aç"""
    try:
        return gpiod.Chip(path, gpiod.Chip.OPEN_BY_PATH)
    except Exception:
        return gpiod.Chip(path)

def request_input(chip, offset):
    """GPIO pinini input olarak ayarla"""
    line = chip.get_line(int(offset))
    line.request(consumer="recovery-monitor", type=gpiod.LINE_REQ_DIR_IN)
    return line

def trigger_recovery():
    """Recovery işlemini başlat - factoryctl ile AP modunda"""
    print("\n" + "="*60)
    print("RECOVERY TETIKLENDI - AP MODUNA GEÇİLECEK!")
    print("="*60)

    # LED yanıp sönmeyi başlat
    start_led_blink()

    try:
        # factoryctl kontrolü
        if not os.path.exists(FACTORYCTL_BIN):
            print(f"HATA: factoryctl bulunamadı: {FACTORYCTL_BIN}")
            print(f"\nfactoryctl'i kurmanız gerekiyor.")
            stop_led_blink()
            return False

        print(f"[1/3] factoryctl bulundu: {FACTORYCTL_BIN}")

        # Factory snapshot kontrolü
        if not os.path.exists(FACTORY_DIR):
            print(f"HATA: Factory dizini bulunamadı: {FACTORY_DIR}")
            print(f"\nÖnce factory snapshot oluşturun:")
            print(f"  sudo factoryctl save")
            stop_led_blink()
            return False

        manifest_file = os.path.join(FACTORY_DIR, "MANIFEST.txt")
        if os.path.exists(manifest_file):
            with open(manifest_file, 'r') as f:
                manifest_content = f.read().strip()
            print(f"[2/3] Factory snapshot bulundu:")
            print(f"      {manifest_content}")
        else:
            print(f"[2/3] Factory snapshot bulundu (manifest yok)")

        print(f"[3/3] Recovery modu: AP MODE (factoryctl restore --ap)")

        # Recovery başlatma bilgisi
        print(f"\nSistem fabrika ayarlarına geri yüklenecek...")
        print(f"Recovery sonrasında sistem AP modunda açılacak!")
        print(f"\n!!! FACTORY RESTORE BAŞLIYOR - AP MODE !!!\n")

        time.sleep(2)  # Kullanıcıya mesajı okuma fırsatı ver

        # factoryctl restore ile geri yükleme (AP modunda, onay beklemeden)
        # -y: onay beklemeden, --ap: AP modunda
        subprocess.run([FACTORYCTL_BIN, "restore", "-y", "--ap"], check=True)

        # Bu noktaya normal şartlarda ulaşılmaz (sistem reboot olur)
        print("\n✓ Factory restore tamamlandı.")
        stop_led_blink()

    except subprocess.CalledProcessError as e:
        print(f"\nHATA: factoryctl restore başarısız oldu: {e}")
        stop_led_blink()
        return False
    except Exception as e:
        print(f"\nHATA: Beklenmeyen hata: {e}")
        import traceback
        traceback.print_exc()
        stop_led_blink()
        return False

    return True

def main():
    """Ana döngü"""
    print("="*60)
    print("RECOVERY GPIO MONITOR - factoryctl Tabanlı")
    print("="*60)
    print(f"GPIO Chip: {GPIO_CHIP}")
    print(f"GPIO Offset (Pin): {GPIO_OFFSET}")
    print(f"Tetikleme süresi: {TRIGGER_DURATION} saniye")
    print(f"Active HIGH: {ACTIVE_HIGH}")
    print("-"*60)
    print(f"factoryctl: {FACTORYCTL_BIN}")
    print(f"Factory dizin: {FACTORY_DIR}")
    print(f"Recovery modu: AP MODE (--ap)")
    print("="*60)
    print()

    # Root kontrolü
    if os.geteuid() != 0:
        print("UYARI: Bu script root olarak çalıştırılmalı (sudo)")
        sys.exit(1)

    # factoryctl kontrolü
    if not os.path.exists(FACTORYCTL_BIN):
        print("⚠ UYARI: factoryctl kurulu değil!")
        print(f"\nfactoryctl'i {FACTORYCTL_BIN} konumuna kurmanız gerekiyor.")
        print("\nYine de GPIO izlemeye başlanıyor...\n")
    elif not os.path.exists(FACTORY_DIR):
        print("⚠ UYARI: Factory snapshot alınmamış!")
        print("\nLütfen önce factory snapshot oluşturun:")
        print("  sudo factoryctl save")
        print("\nYine de GPIO izlemeye başlanıyor...\n")
    else:
        print("✓ Factory snapshot hazır, recovery yapılabilir.\n")

    # GPIO setup
    try:
        chip = open_chip(GPIO_CHIP)
        line = request_input(chip, GPIO_OFFSET)
        print(f"✓ GPIO {GPIO_OFFSET} izleniyor...")
    except Exception as e:
        print(f"HATA: GPIO açılamadı: {e}")
        sys.exit(1)

    # Signal handler
    stop_flag = False
    def signal_handler(sig, frame):
        nonlocal stop_flag
        print("\nDurdurma sinyali alındı...")
        stop_flag = True

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # LED setup
    led_setup_success = setup_led_gpio()

    # Monitoring loop
    high_state = False
    high_start_time = 0.0

    print("İzleme başladı. Çıkmak için Ctrl+C...")
    print()

    try:
        while not stop_flag:
            try:
                value = line.get_value()
                is_high = (value == 1) if ACTIVE_HIGH else (value == 0)

                if is_high:
                    if not high_state:
                        # HIGH durumuna geçiş
                        high_state = True
                        high_start_time = time.time()
                        print(f"[{time.strftime('%H:%M:%S')}] GPIO {GPIO_OFFSET} HIGH algılandı - süre sayılıyor...")
                    else:
                        # HIGH durumu devam ediyor
                        elapsed = time.time() - high_start_time

                        # Her saniye güncelleme göster
                        if int(elapsed) != int(elapsed - POLL_INTERVAL):
                            remaining = TRIGGER_DURATION - elapsed
                            if remaining > 0:
                                print(f"  → HIGH süresi: {elapsed:.1f}s / {TRIGGER_DURATION}s (kalan: {remaining:.1f}s)")

                        # Tetikleme süresine ulaşıldı mı?
                        if elapsed >= TRIGGER_DURATION:
                            print()
                            print("="*60)
                            print(f"✓ GPIO {GPIO_OFFSET} {TRIGGER_DURATION} saniye boyunca HIGH!")
                            print("="*60)
                            print()

                            # Recovery'yi tetikle
                            trigger_recovery()

                            # Eğer buraya kadar geldiyse, recovery başarısız oldu
                            print("\nRecovery başlatılamadı. İzlemeye devam ediliyor...\n")
                            high_state = False
                else:
                    if high_state:
                        # HIGH durumundan çıkış
                        elapsed = time.time() - high_start_time
                        print(f"[{time.strftime('%H:%M:%S')}] GPIO {GPIO_OFFSET} LOW - süre sıfırlandı ({elapsed:.1f}s)")
                        high_state = False
                        high_start_time = 0.0

                time.sleep(POLL_INTERVAL)

            except Exception as e:
                print(f"HATA: GPIO okuma hatası: {e}")
                break

    finally:
        # Cleanup
        try:
            line.release()
            chip.close()
            print("\nGPIO kaynakları serbest bırakıldı.")
        except Exception:
            pass

    print("İzleme durduruldu.")

    # LED cleanup
    cleanup_led_gpio()

if __name__ == "__main__":
    main()
