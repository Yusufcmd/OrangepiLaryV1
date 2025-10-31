#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import qrcode
from PIL import ImageTk  # Pillow, qrcode[pil] ile gelir

# ---------- QR yardımcıları ----------
def escape_wifi(s: str) -> str:
    # Wi-Fi QR standardı kaçışları
    return (s.replace('\\', '\\\\')
             .replace(';', r'\;')
             .replace(',', r'\,')
             .replace(':', r'\:'))

def build_ap_payload(band: str, ch: str, prefix: str = "APMODE") -> str:
    band_str = '5g' if band == '5' else '2.4g'
    return f"{prefix}{band_str}ch{ch}"

def build_wifi_payload(ssid: str, password: str, auth: str = 'WPA', hidden: bool = False) -> str:
    # WIFI:T:<WPA|WEP|nopass>;S:<ssid>;P:<password>;H:<true|false>;;
    t = 'nopass' if auth.lower() == 'nopass' else auth.upper()
    s_esc = escape_wifi(ssid)
    h = 'true' if hidden else 'false'
    if t == 'nopass':
        return f"WIFI:T:{t};S:{s_esc};H:{h};;"
    p_esc = escape_wifi(password)
    return f"WIFI:T:{t};S:{s_esc};P:{p_esc};H:{h};;"

def make_qr_image(data: str, box_size: int = 10, border: int = 4):
    qr = qrcode.QRCode(
        version=None,           # otomatik
        box_size=box_size,
        border=border           # varsayılan hata düzeltme M (uygun)
    )
    qr.add_data(data)
    qr.make(fit=True)
    return qr.make_image()

# ---------- Arayüz ----------
class WifiQRApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Orange Pi Wi-Fi QR Oluşturucu")
        self.geometry("680x520")
        self.resizable(False, False)

        # Durum
        self.mode_var   = tk.StringVar(value="STA")  # 'AP' veya 'STA'
        self.payload    = ""     # son üretilen metin
        self.preview_im = None   # ImageTk referansı tutulmalı

        # Üst: Mod seçimi
        top = ttk.Frame(self, padding=10)
        top.pack(fill="x")
        ttk.Label(top, text="Mod:", width=8).pack(side="left")
        ttk.Radiobutton(top, text="STA (Standart Wi-Fi QR)", value="STA",
                        variable=self.mode_var, command=self._switch_mode).pack(side="left")
        ttk.Radiobutton(top, text="AP (APMODE...)", value="AP",
                        variable=self.mode_var, command=self._switch_mode).pack(side="left")

        # Orta: Parametre panelleri
        mid = ttk.Frame(self, padding=(10, 0))
        mid.pack(fill="x", pady=(5, 0))

        # STA paneli
        self.sta_frame = ttk.LabelFrame(mid, text="STA (Telefonların Tanıdığı Wi-Fi QR)", padding=10)
        self._build_sta(self.sta_frame)
        self.sta_frame.pack(fill="x")

        # AP paneli
        self.ap_frame = ttk.LabelFrame(mid, text="AP (Özel APMODE formatı)", padding=10)
        self._build_ap(self.ap_frame)
        # AP başlangıçta gizli (mode STA)
        self.ap_frame.forget()

        # Aksiyon butonları
        actions = ttk.Frame(self, padding=10)
        actions.pack(fill="x", pady=(5, 0))
        ttk.Button(actions, text="QR Oluştur", command=self.generate_qr).pack(side="left")
        ttk.Button(actions, text="PNG Kaydet...", command=self.save_png).pack(side="left", padx=8)
        ttk.Button(actions, text="Payload'ı Kopyala", command=self.copy_payload).pack(side="left")

        # Alt: Önizleme ve payload
        bottom = ttk.Frame(self, padding=10)
        bottom.pack(fill="both", expand=True)

        self.preview_label = ttk.Label(bottom, text="Önizleme burada görünecek")
        self.preview_label.pack(side="left", padx=(0, 15))

        right = ttk.Frame(bottom)
        right.pack(side="left", fill="both", expand=True)
        ttk.Label(right, text="Üretilen Payload:").pack(anchor="w")
        self.payload_box = tk.Text(right, height=6, wrap="word")
        self.payload_box.pack(fill="both", expand=True)

    # -------- STA panel öğeleri --------
    def _build_sta(self, parent: ttk.LabelFrame):
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=3)
        ttk.Label(row, text="SSID:", width=14).pack(side="left")
        self.ssid_var = tk.StringVar(value="örnekwifi")
        ttk.Entry(row, textvariable=self.ssid_var, width=32).pack(side="left")

        row = ttk.Frame(parent)
        row.pack(fill="x", pady=3)
        ttk.Label(row, text="Şifre:", width=14).pack(side="left")
        self.pass_var = tk.StringVar(value="örnekşifre")
        ttk.Entry(row, textvariable=self.pass_var, width=32, show="•").pack(side="left")

        row = ttk.Frame(parent)
        row.pack(fill="x", pady=3)
        ttk.Label(row, text="Şifreleme:", width=14).pack(side="left")
        self.auth_var = tk.StringVar(value="WPA")
        auth_cb = ttk.Combobox(row, textvariable=self.auth_var, width=12, state="readonly",
                               values=["WPA", "WEP", "nopass"])
        auth_cb.pack(side="left")
        self.hidden_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(row, text="Gizli SSID", variable=self.hidden_var).pack(side="left", padx=10)

    # -------- AP panel öğeleri --------
    def _build_ap(self, parent: ttk.LabelFrame):
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=3)
        ttk.Label(row, text="Bant:", width=14).pack(side="left")
        self.band_var = tk.StringVar(value="5")
        band_cb = ttk.Combobox(row, textvariable=self.band_var, width=12, state="readonly",
                               values=["2.4", "5"])
        band_cb.bind("<<ComboboxSelected>>", self._band_changed)
        band_cb.pack(side="left")

        row = ttk.Frame(parent)
        row.pack(fill="x", pady=3)
        ttk.Label(row, text="Kanal:", width=14).pack(side="left")
        self.channel_var = tk.StringVar(value="36")
        # Düzenlenebilir combobox: önerileri var, istenirse serbest yazılabilir
        self.channel_cb = ttk.Combobox(row, textvariable=self.channel_var, width=12, state="normal")
        self._update_channel_suggestions()
        self.channel_cb.pack(side="left")

        row = ttk.Frame(parent)
        row.pack(fill="x", pady=3)
        ttk.Label(row, text="Önek (sabit):", width=14).pack(side="left")
        ttk.Label(row, text="APMODE").pack(side="left")

    # -------- Olaylar / Mantık --------
    def _switch_mode(self):
        if self.mode_var.get() == "STA":
            self.ap_frame.forget()
            self.sta_frame.pack(fill="x")
        else:
            self.sta_frame.forget()
            self.ap_frame.pack(fill="x")

    def _band_changed(self, _evt=None):
        self._update_channel_suggestions()

    def _update_channel_suggestions(self):
        band = self.band_var.get()
        if band == "2.4":
            suggestions = [str(x) for x in range(1, 14 + 1)]  # TR/EU için 1–13 yaygın, 14 Japonya
            default = "6"
        else:
            # DFS'siz yaygın kanallar (TR/EU)
            suggestions = ["36", "40", "44", "48"]
            default = "36"
        self.channel_cb.configure(values=suggestions)
        if self.channel_var.get().strip() == "":
            self.channel_var.set(default)

    def _build_payload(self) -> str:
        if self.mode_var.get() == "STA":
            ssid = self.ssid_var.get().strip()
            auth = self.auth_var.get()
            pwd  = self.pass_var.get()
            hidden = self.hidden_var.get()
            if not ssid:
                raise ValueError("SSID boş olamaz.")
            if auth != "nopass" and len(pwd) == 0:
                raise ValueError("WPA/WEP seçildi. Şifre boş olamaz.")
            return build_wifi_payload(ssid, pwd, auth, hidden)
        else:
            band = self.band_var.get()
            ch = self.channel_var.get().strip()
            if not ch.isdigit():
                raise ValueError("Kanal sayısal olmalı.")
            return build_ap_payload(band, ch)

    def generate_qr(self):
        try:
            payload = self._build_payload()
            img = make_qr_image(payload)
            # Önizleme için ölçek küçültme
            w, h = img.size
            max_side = 280
            scale = min(max_side / w, max_side / h)
            if scale < 1.0:
                img = img.resize((int(w * scale), int(h * scale)))
            self.preview_im = ImageTk.PhotoImage(img)
            self.preview_label.configure(image=self.preview_im, text="")
            self.payload = payload
            self.payload_box.delete("1.0", "end")
            self.payload_box.insert("1.0", payload)
        except Exception as e:
            messagebox.showerror("Hata", str(e))

    def save_png(self):
        if not self.payload:
            messagebox.showinfo("Bilgi", "Önce 'QR Oluştur' deyin.")
            return
        fp = filedialog.asksaveasfilename(
            defaultextension=".png",
            filetypes=[("PNG", "*.png")],
            initialfile=("sta_wifi.png" if self.mode_var.get() == "STA" else "ap_qr.png"),
            title="PNG olarak kaydet"
        )
        if not fp:
            return
        try:
            img = make_qr_image(self.payload)
            img.save(fp)
            messagebox.showinfo("Kaydedildi", f"PNG kaydedildi:\n{fp}")
        except Exception as e:
            messagebox.showerror("Hata", str(e))

    def copy_payload(self):
        if not self.payload:
            messagebox.showinfo("Bilgi", "Kopyalamak için önce payload üretin.")
            return
        self.clipboard_clear()
        self.clipboard_append(self.payload)
        self.update()
        messagebox.showinfo("Kopyalandı", "Payload panoya kopyalandı.")

# ---------- Çalıştır ----------
if __name__ == "__main__":
    app = WifiQRApp()
    app.mainloop()
