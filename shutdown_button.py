#!/usr/bin/env python3
# Statik shutdown + latch kontrolü — Orange Pi Zero2W (Armbian)
# libgpiod (python3-libgpiod) gerekir.

import os, time, signal

try:
    import gpiod
except Exception as e:
    raise SystemExit("gpiod modülü bulunamadı. 'sudo apt install -y python3-libgpiod gpiod'") from e

# ---------- SABİTLER (gerekirse düzenleyin) ----------
# BUTON (uzun basış kapatma)
BTN_GPIOCHIP   = "/dev/gpiochip1"
BTN_OFFSET     = 269            # alternatif: 263
BTN_ACTIVE_LOW = True           # buton GND'ye çekiliyorsa True

LONG_PRESS_SEC = 2.0            # 2 saniye uzun basışta kapanış

# LATCH (güç tutma hattı)
LATCH_GPIOCHIP   = "/dev/gpiochip1"
LATCH_OFFSET     = 263          # alternatif/istediğiniz pin
LATCH_ACTIVE_HIGH= True         # 1=aktif (tutar), 0=pasif (bırakır)

# Kapatma sonrası latch'ı bırakma gecikmesi (OS'un durmasına fırsat ver)
LATCH_CUTOFF_DELAY_SEC = 6.0    # ihtiyaca göre 4–15 sn arası ayarlayın

POLL_INTERVAL_SEC = 0.02        # 50 Hz tarama
# -----------------------------------------------------

def _open_chip(path):
    try:
        return gpiod.Chip(path, gpiod.Chip.OPEN_BY_PATH)  # v2
    except Exception:
        return gpiod.Chip(path)                           # v1

def _request_input(chip, offset):
    line = chip.get_line(int(offset))
    # Basit giriş
    line.request(consumer="shutdown-btn", type=gpiod.LINE_REQ_DIR_IN)
    return line

def _request_output(chip, offset, initial):
    line = chip.get_line(int(offset))
    # Çıkış, başlangıç değeri ile
    try:
        line.request(consumer="latch-ctrl", type=gpiod.LINE_REQ_DIR_OUT, default_val=1 if initial else 0)
    except Exception:
        line.request(consumer="latch-ctrl", type=gpiod.LINE_REQ_DIR_OUT)
        line.set_value(1 if initial else 0)
    return line

def main():
    # --- LATCH: güç tutma hattını AKTİF seviyeye çek ---
    latch_chip = _open_chip(LATCH_GPIOCHIP)
    latch_line = None
    try:
        latch_initial = 1 if LATCH_ACTIVE_HIGH else 0
        latch_line = _request_output(latch_chip, LATCH_OFFSET, latch_initial)
        print(f"[OK] LATCH aktif edildi: {LATCH_GPIOCHIP} off {LATCH_OFFSET} -> {latch_initial}")
    except Exception as e:
        print(f"[WARN] LATCH hattı açılamadı ({LATCH_GPIOCHIP} off {LATCH_OFFSET}): {e}")

    # --- BUTON: uzun basış dinle ---
    btn_chip = _open_chip(BTN_GPIOCHIP)
    try:
        btn_line = _request_input(btn_chip, BTN_OFFSET)
    except Exception as e:
        print(f"[ERR] Buton hattı açılamadı ({BTN_GPIOCHIP} off {BTN_OFFSET}): {e} — root gerekebilir.")
        # latch hattını serbest bırakmadan çık
        try: latch_line.release()
        except Exception: pass
        try: latch_chip.close()
        except Exception: pass
        try: btn_chip.close()
        except Exception: pass
        return

    print(f"[OK] İzleniyor (buton): {BTN_GPIOCHIP} off {BTN_OFFSET} "
          f"(long={LONG_PRESS_SEC}s, active_low={BTN_ACTIVE_LOW})")

    pressed = False
    t0 = 0.0
    stop = False

    def _term(_s, _f):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, _term)
    signal.signal(signal.SIGTERM, _term)

    try:
        while not stop:
            try:
                v = btn_line.get_value()
            except Exception as e:
                print(f"[ERR] get_value: {e}")
                break

            is_pressed = (v == 0) if BTN_ACTIVE_LOW else (v == 1)

            if is_pressed and not pressed:
                pressed = True
                t0 = time.time()

            elif not is_pressed and pressed:
                # Kısa basış: göz ardı
                pressed = False

            elif pressed and (time.time() - t0) >= LONG_PRESS_SEC:
                print("[SYS] Uzun basma algılandı. Sistem kapatılıyor…")
                os.system("sync")  # diske yaz
                os.system("sudo shutdown -h now")

                # OS kapanırken biraz bekle, sonra LATCH'ı bırak
                time.sleep(LATCH_CUTOFF_DELAY_SEC)
                try:
                    if latch_line is not None:
                        release_val = 0 if LATCH_ACTIVE_HIGH else 1
                        latch_line.set_value(release_val)
                        print(f"[SYS] LATCH bırakıldı: {release_val}")
                except Exception as e:
                    print(f"[WARN] LATCH bırakılamadı: {e}")
                time.sleep(2)
                break

            time.sleep(POLL_INTERVAL_SEC)
    finally:
        # GPIO’ları serbest bırak
        try: btn_line.release()
        except Exception: pass
        try: btn_chip.close()
        except Exception: pass
        try: latch_line.release()
        except Exception: pass
        try: latch_chip.close()
        except Exception: pass

if __name__ == "__main__":
    main()
