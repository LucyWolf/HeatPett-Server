#!/usr/bin/env python3
"""Headpat Server v2.3 — VRChat OSC <-> Headpat Dongle USB bridge"""

import tkinter as tk
from tkinter import ttk
import threading
import queue
import collections
import json
import re
import time
import os
import sys

try:
    import serial
    import serial.tools.list_ports
    SERIAL_OK = True
except ImportError:
    SERIAL_OK = False

try:
    from pythonosc import dispatcher, osc_server
    OSC_OK = True
except ImportError:
    OSC_OK = False

try:
    from PIL import Image, ImageTk
    PIL_OK = True
except ImportError:
    PIL_OK = False

# ── Colors ────────────────────────────────────────────────────────────────────
BG       = "#0d1117"
BG_TITLE = "#080c11"
BG_BTN   = "#0f2440"
BG_BTN_A = "#163460"
BORDER   = "#1a2235"
FG       = "#dde6f0"
FG_DIM   = "#4a5568"
ACCENT   = "#00b4ff"
GREEN    = "#00e5a0"
RED      = "#e5534b"
YELLOW   = "#c9a227"

# ── Config ────────────────────────────────────────────────────────────────────
BAUD          = 115200
OSC_RX_PORT   = 9001
OSC_HOST      = "127.0.0.1"
VRC_TIMEOUT   = 5.0
INFO_INTERVAL = 5.0
BAT_INTERVAL  = 30.0

_BASE     = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
ICON_PATH = os.path.join(_BASE, "icon.png")

if os.name == "nt":
    CONFIG_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "HeadpatServer")
else:
    CONFIG_DIR = os.path.join(os.environ.get("XDG_CONFIG_HOME", os.path.join(os.path.expanduser("~"), ".config")), "HeadpatServer")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.overrideredirect(True)
        self.configure(bg=BG_TITLE)
        self.resizable(False, False)

        self._ser           = None
        self._ser_lock      = threading.Lock()
        self._q             = queue.Queue()
        self._cfg           = self._load_config()
        self._intensity     = self._cfg.get("intensity", 50) / 100
        self._vrc_connected = False
        self._ble_connected = False
        self._last_osc      = 0.0
        self._drag_x        = 0
        self._drag_y        = 0
        self._logo_img      = None
        self._port_var       = tk.StringVar(value=self._cfg.get("port", ""))
        self._settings_open  = False
        self._osc_verbose    = bool(self._cfg.get("osc_verbose", False))
        self._console_win    = None
        self._console_text   = None
        self._log_buf        = collections.deque(maxlen=500)
        self._hp_version     = "?"
        self._dongle_version = "?"
        self._hp_ver_var     = tk.StringVar(value="?")
        self._dongle_ver_var = tk.StringVar(value="?")
        self._save_after_id  = None

        self._load_icon()
        self._build()
        self._int_var.set(self._cfg.get("intensity", 50))
        self.update_idletasks()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        w,  h  = self.winfo_width(),       self.winfo_height()
        wx, wy = self._cfg.get("win_x"), self._cfg.get("win_y")
        if wx is not None and wy is not None and 0 <= wx < sw and 0 <= wy < sh:
            self.geometry(f"+{wx}+{wy}")
        else:
            self.geometry(f"+{(sw-w)//2}+{(sh-h)//2}")
        self._refresh_ports()
        self._start_osc()
        self._tick()
        if self._cfg.get("auto_connect") and self._port_var.get():
            self.after(300, self._connect)
        if os.name == "posix":
            self.after(800, self._check_linux_serial_perms)

    # ── Linux serial port permissions ────────────────────────────────────────
    def _check_linux_serial_perms(self):
        udev_rule = "/etc/udev/rules.d/99-headpat.rules"
        if os.path.exists(udev_rule):
            return
        import grp, subprocess
        try:
            in_dialout = grp.getgrnam("dialout").gr_gid in os.getgroups()
        except KeyError:
            in_dialout = False
        if in_dialout:
            return
        if not tk.messagebox.askyesno(
            "Serieller Port",
            "Für den Dongle wird eine udev-Regel benötigt.\n\n"
            "Jetzt einrichten? (Einmalig, erfordert Admin-Passwort)\n\n"
            "Danach den Dongle neu einstecken.",
            parent=self
        ):
            return
        rule = 'SUBSYSTEM=="tty", ATTRS{idVendor}=="239a", TAG+="uaccess"\n'
        try:
            result = subprocess.run(
                ["pkexec", "sh", "-c",
                 f"tee {udev_rule} && udevadm control --reload-rules && udevadm trigger"],
                input=rule.encode(), capture_output=True
            )
            if result.returncode == 0:
                tk.messagebox.showinfo(
                    "Fertig",
                    "udev-Regel eingerichtet.\nBitte den Dongle neu einstecken.",
                    parent=self
                )
            else:
                tk.messagebox.showerror("Fehler", "Konnte udev-Regel nicht erstellen.", parent=self)
        except FileNotFoundError:
            tk.messagebox.showerror(
                "Fehler",
                "pkexec nicht gefunden.\nFühre manuell aus:\n"
                f'echo \'{rule.strip()}\' | sudo tee {udev_rule}\n'
                "sudo udevadm control --reload-rules && sudo udevadm trigger",
                parent=self
            )

    # ── Config persistence ───────────────────────────────────────────────────
    def _load_config(self):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _save_config(self):
        cfg = {
            "port":         self._port_var.get(),
            "intensity":    self._int_var.get(),
            "osc_verbose":  self._osc_verbose,
            "auto_connect": self._ser is not None,
            "win_x":        self.winfo_x(),
            "win_y":        self.winfo_y(),
        }
        try:
            os.makedirs(CONFIG_DIR, exist_ok=True)
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=2)
        except Exception:
            pass

    def _debounce_save(self):
        if self._save_after_id:
            self.after_cancel(self._save_after_id)
        self._save_after_id = self.after(800, self._save_config)

    def _on_close(self):
        self._save_config()
        self.destroy()

    # ── Icon ──────────────────────────────────────────────────────────────────
    def _load_icon(self):
        if not (PIL_OK and os.path.exists(ICON_PATH)):
            return
        try:
            img = Image.open(ICON_PATH).convert("RGBA")
            self._logo_img = ImageTk.PhotoImage(img.resize((20, 20), Image.LANCZOS))
            big = ImageTk.PhotoImage(img.resize((64, 64), Image.LANCZOS))
            self.wm_iconphoto(True, big)
            self._big_icon = big
        except Exception:
            pass

    # ── Build UI ──────────────────────────────────────────────────────────────
    def _build(self):
        # ── Title bar ─────────────────────────────────────────────────────────
        tb = tk.Frame(self, bg=BG_TITLE, height=44)
        tb.pack(fill="x")
        tb.pack_propagate(False)
        for w in (tb,):
            w.bind("<ButtonPress-1>", self._drag_start)
            w.bind("<B1-Motion>",     self._drag_move)

        if self._logo_img:
            ico_lbl = tk.Label(tb, image=self._logo_img, bg=BG_TITLE)
            ico_lbl.pack(side="left", padx=(12, 6), pady=10)
            ico_lbl.bind("<ButtonPress-1>", self._drag_start)
            ico_lbl.bind("<B1-Motion>",     self._drag_move)

        name_lbl = tk.Label(tb, text="Headpat Server v2.3",
                            bg=BG_TITLE, fg=FG, font=("Segoe UI", 11))
        name_lbl.pack(side="left", pady=10)
        name_lbl.bind("<ButtonPress-1>", self._drag_start)
        name_lbl.bind("<B1-Motion>",     self._drag_move)

        close = tk.Label(tb, text="×", bg=BG_TITLE, fg=FG_DIM,
                         font=("Segoe UI", 16), cursor="hand2", padx=12)
        close.pack(side="right", pady=4)
        close.bind("<Button-1>", lambda _: self._on_close())
        close.bind("<Enter>",    lambda _: close.config(fg=RED, bg="#3d1210"))
        close.bind("<Leave>",    lambda _: close.config(fg=FG_DIM, bg=BG_TITLE))

        gear = tk.Label(tb, text="⚙", bg=BG_TITLE, fg=FG_DIM,
                        font=("Segoe UI", 13), cursor="hand2", padx=10)
        gear.pack(side="right", pady=6)
        gear.bind("<Button-1>", lambda e: self._open_settings(e))
        gear.bind("<Enter>",    lambda _: gear.config(fg=ACCENT))
        gear.bind("<Leave>",    lambda _: gear.config(fg=FG_DIM))

        # Console / Log button
        self._log_btn = tk.Label(tb, text="≡", bg=BG_TITLE, fg=FG_DIM,
                                 font=("Segoe UI", 15), cursor="hand2", padx=10)
        self._log_btn.pack(side="right", pady=6)
        self._log_btn.bind("<Button-1>", lambda _: self._toggle_console())
        self._log_btn.bind("<Enter>",    lambda _: self._log_btn.config(fg=ACCENT))
        self._log_btn.bind("<Leave>",    lambda _: self._log_btn.config(
            fg=GREEN if (self._console_win and self._console_win.winfo_exists()) else FG_DIM))

        # ── Thin accent line under title ──────────────────────────────────────
        tk.Frame(self, bg=ACCENT, height=2).pack(fill="x")

        # ── Main card ─────────────────────────────────────────────────────────
        card = tk.Frame(self, bg=BG)
        card.pack(fill="both", expand=True)

        # ── Status row ────────────────────────────────────────────────────────
        status = tk.Frame(card, bg=BG)
        status.pack(fill="x", padx=20, pady=(22, 16))

        tk.Label(status, text="Headpat", bg=BG, fg=FG,
                 font=("Segoe UI", 11)).pack(side="left")
        self._hp_dot = self._dot(status, RED)
        self._hp_dot.pack(side="left", padx=(6, 0))

        tk.Label(status, text="  |  ", bg=BG, fg=FG_DIM,
                 font=("Segoe UI", 11)).pack(side="left")

        tk.Label(status, text="OSC", bg=BG, fg=FG,
                 font=("Segoe UI", 11)).pack(side="left")
        self._vrc_dot = self._dot(status, RED)
        self._vrc_dot.pack(side="left", padx=(6, 0))

        self._bat_lbl = tk.Label(status, text="🔋 ?%", bg=BG, fg=FG_DIM,
                                 font=("Segoe UI", 11))
        self._bat_lbl.pack(side="right")

        # ── Separator ─────────────────────────────────────────────────────────
        tk.Frame(card, bg=BORDER, height=1).pack(fill="x")

        # ── Intensity row ─────────────────────────────────────────────────────
        int_row = tk.Frame(card, bg=BG)
        int_row.pack(fill="x", padx=20, pady=(18, 12))

        tk.Label(int_row, text="Intensity", bg=BG, fg=FG,
                 font=("Segoe UI", 11)).pack(side="left")

        self._int_var = tk.DoubleVar(value=50)
        tk.Scale(int_row, from_=0, to=100, orient="horizontal",
                 variable=self._int_var, bg=BG, fg=ACCENT,
                 troughcolor="#1a1c24", highlightthickness=0,
                 activebackground=ACCENT, sliderlength=22, bd=0,
                 showvalue=False, length=230,
                 command=self._on_intensity_change
                 ).pack(side="right")

        # ── Separator ─────────────────────────────────────────────────────────
        tk.Frame(card, bg=BORDER, height=1).pack(fill="x")

        # ── Test row ──────────────────────────────────────────────────────────
        test_row = tk.Frame(card, bg=BG)
        test_row.pack(fill="x", padx=20, pady=(16, 22))

        tk.Label(test_row, text="Test", bg=BG, fg=FG,
                 font=("Segoe UI", 11)).pack(side="left")

        self._mkbtn(test_row, "R", self._pat_right).pack(side="right")
        self._mkbtn(test_row, "L", self._pat_left).pack(side="right", padx=(0, 10))

        # ── Settings overlay (hidden by default) ──────────────────────────────
        self._overlay = tk.Frame(self, bg=BG_TITLE)
        self._build_overlay()

    # ── Helpers ───────────────────────────────────────────────────────────────
    def _dot(self, parent, color):
        c = tk.Canvas(parent, width=12, height=12, bg=BG, highlightthickness=0)
        c.create_oval(1, 1, 11, 11, fill=color, outline="", tags="d")
        return c

    def _set_dot(self, canvas, color):
        canvas.itemconfig("d", fill=color)

    def _on_intensity_change(self, v):
        self._intensity = float(v) / 100
        self._debounce_save()

    def _mkbtn(self, parent, text, cmd):
        return tk.Button(parent, text=text, command=cmd,
                         bg=BG_BTN, fg=FG, activebackground=BG_BTN_A,
                         activeforeground=FG, bd=0, relief="flat",
                         font=("Segoe UI", 11, "bold"),
                         width=6, pady=8, cursor="hand2")

    # ── Drag ──────────────────────────────────────────────────────────────────
    def _drag_start(self, e):
        self._drag_x = e.x_root - self.winfo_x()
        self._drag_y = e.y_root - self.winfo_y()

    def _drag_move(self, e):
        self.geometry(f"+{e.x_root - self._drag_x}+{e.y_root - self._drag_y}")

    # ── Internal log ──────────────────────────────────────────────────────────
    def _log(self, text: str, tag: str = "info"):
        ts = time.strftime("%H:%M:%S")
        entry = (ts, tag, text)
        self._log_buf.append(entry)
        self._q.put(("log", entry))

    # ── Console window ────────────────────────────────────────────────────────
    def _toggle_console(self):
        if self._console_win and self._console_win.winfo_exists():
            self._console_win.destroy()
            self._console_win = None
            self._log_btn.config(fg=FG_DIM)
        else:
            self._open_console()

    def _open_console(self):
        win = tk.Toplevel(self)
        self._console_win = win
        win.title("Headpat Log")
        win.configure(bg=BG_TITLE)
        win.geometry("620x320")
        win.resizable(True, True)

        # Title bar of console
        top = tk.Frame(win, bg=BG_TITLE)
        top.pack(fill="x")

        tk.Label(top, text="Headpat Console", bg=BG_TITLE, fg=FG_DIM,
                 font=("Segoe UI", 9)).pack(side="left", padx=12, pady=8)

        # Verbose OSC toggle
        self._verb_btn = tk.Button(
            top, text="OSC: nur Headpat", command=self._toggle_verbose,
            bg=BG_BTN, fg=FG_DIM, activebackground=BG_BTN_A,
            activeforeground=FG, bd=0, relief="flat",
            font=("Segoe UI", 9), padx=10, pady=4, cursor="hand2")
        self._verb_btn.pack(side="left", padx=(0, 6), pady=6)

        clr = tk.Button(top, text="Clear", command=self._clear_console,
                        bg=BG_BTN, fg=FG_DIM, activebackground=BG_BTN_A,
                        activeforeground=FG, bd=0, relief="flat",
                        font=("Segoe UI", 9), padx=10, pady=4, cursor="hand2")
        clr.pack(side="left", pady=6)

        tk.Frame(win, bg=ACCENT, height=2).pack(fill="x")

        # Text area
        txt_frame = tk.Frame(win, bg=BG)
        txt_frame.pack(fill="both", expand=True, padx=0, pady=0)

        self._console_text = tk.Text(
            txt_frame, bg="#07090e", fg=FG_DIM,
            font=("Courier New", 9), state="disabled",
            wrap="none", selectbackground=BG_BTN_A,
            relief="flat", bd=0, insertbackground=FG
        )
        self._console_text.tag_config("PASS",  foreground=GREEN)
        self._console_text.tag_config("skip",  foreground=FG_DIM)
        self._console_text.tag_config("osc",   foreground="#2a4060")
        self._console_text.tag_config("info",  foreground=ACCENT)
        self._console_text.tag_config("warn",  foreground=YELLOW)
        self._console_text.tag_config("err",   foreground=RED)
        self._console_text.tag_config("serial",foreground="#5a8a6a")

        vsb = tk.Scrollbar(txt_frame, orient="vertical",
                           command=self._console_text.yview,
                           bg=BG, activebackground=BG_BTN, troughcolor=BG)
        hsb = tk.Scrollbar(txt_frame, orient="horizontal",
                           command=self._console_text.xview,
                           bg=BG, activebackground=BG_BTN, troughcolor=BG)
        self._console_text.configure(
            yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        vsb.pack(side="right", fill="y")
        hsb.pack(side="bottom", fill="x")
        self._console_text.pack(side="left", fill="both", expand=True)

        # Populate with existing history
        self._console_text.config(state="normal")
        for ts, tag, text in self._log_buf:
            self._console_text.insert("end", f"{ts}  {text}\n", tag)
        self._console_text.see("end")
        self._console_text.config(state="disabled")

        self._log_btn.config(fg=GREEN)
        win.protocol("WM_DELETE_WINDOW", self._toggle_console)

    def _toggle_verbose(self):
        self._osc_verbose = not self._osc_verbose
        if self._osc_verbose:
            self._verb_btn.config(text="OSC: alle", fg=YELLOW)
            self._log("OSC verbose ON — zeige alle Parameter", "warn")
        else:
            self._verb_btn.config(text="OSC: nur Headpat", fg=FG_DIM)
            self._log("OSC verbose OFF", "info")
        self._save_config()

    def _clear_console(self):
        if self._console_text:
            self._console_text.config(state="normal")
            self._console_text.delete("1.0", "end")
            self._console_text.config(state="disabled")
        self._log_buf.clear()

    # ── Settings overlay ──────────────────────────────────────────────────────
    def _build_overlay(self):
        ov = self._overlay

        tk.Frame(ov, bg=ACCENT, height=2).pack(fill="x")

        title_row = tk.Frame(ov, bg=BG_TITLE)
        title_row.pack(fill="x", padx=16, pady=(12, 4))
        tk.Label(title_row, text="Einstellungen", bg=BG_TITLE, fg=FG_DIM,
                 font=("Segoe UI", 9)).pack(side="left")
        x_lbl = tk.Label(title_row, text="×", bg=BG_TITLE, fg=FG_DIM,
                         font=("Segoe UI", 14), cursor="hand2")
        x_lbl.pack(side="right")
        x_lbl.bind("<Button-1>", lambda _: self._close_settings())

        # Port + connect row
        row = tk.Frame(ov, bg=BG_TITLE)
        row.pack(fill="x", padx=16, pady=(4, 12))

        s = ttk.Style()
        s.theme_use("clam")
        s.configure("P.TCombobox", fieldbackground=BG, background=BG,
                    foreground=FG, selectbackground="#1e2028",
                    selectforeground=FG, arrowcolor=ACCENT, bordercolor=BORDER,
                    insertcolor=FG)
        s.map("P.TCombobox",
              fieldbackground=[("focus", BG), ("!focus", BG)],
              foreground=[("focus", FG), ("!focus", FG)],
              background=[("active", BG), ("!active", BG)])

        self._port_cb = ttk.Combobox(row, textvariable=self._port_var,
                                     width=10, style="P.TCombobox")
        self._port_cb.pack(side="left")

        self._conn_btn = tk.Button(row, text="Connect", command=self._toggle_serial,
                                   bg=BG_BTN, fg=ACCENT, activebackground=BG_BTN_A,
                                   activeforeground=FG, bd=0, relief="flat",
                                   font=("Segoe UI", 10), padx=12, pady=6,
                                   cursor="hand2")
        self._conn_btn.pack(side="left", padx=(10, 0))

        # Version info
        tk.Frame(ov, bg=BORDER, height=1).pack(fill="x", padx=12)
        ver_frame = tk.Frame(ov, bg=BG_TITLE)
        ver_frame.pack(fill="x", padx=16, pady=(10, 12))

        for label, var, color in [
            ("Server",  tk.StringVar(value="v2.3"), ACCENT),
            ("Dongle",  self._dongle_ver_var,        FG),
            ("Headpat", self._hp_ver_var,             FG),
        ]:
            r = tk.Frame(ver_frame, bg=BG_TITLE)
            r.pack(fill="x", pady=3)
            tk.Label(r, text=label, bg=BG_TITLE, fg=FG_DIM,
                     font=("Segoe UI", 10)).pack(side="left")
            tk.Label(r, textvariable=var, bg=BG_TITLE, fg=color,
                     font=("Segoe UI", 10, "bold")).pack(side="right")

    def _open_settings(self, event=None):
        if self._settings_open:
            self._close_settings()
            return
        self._settings_open = True
        if SERIAL_OK:
            ports = [p.device for p in serial.tools.list_ports.comports()]
            self._port_cb["values"] = ports
            if ports and not self._port_var.get():
                self._port_var.set(ports[0])
        is_connected = self._ser is not None
        self._conn_btn.config(
            text="Disconnect" if is_connected else "Connect",
            fg=RED if is_connected else ACCENT
        )
        self._overlay.place(x=0, y=46, relwidth=1, relheight=1)
        self._overlay.lift()

    def _close_settings(self):
        self._settings_open = False
        self._overlay.place_forget()

    # ── Serial ────────────────────────────────────────────────────────────────
    def _refresh_ports(self):
        if not SERIAL_OK:
            return
        ports = [p.device for p in serial.tools.list_ports.comports()]
        if ports and not self._port_var.get():
            self._port_var.set(ports[0])

    def _toggle_serial(self):
        if self._ser:
            self._disconnect()
        else:
            self._connect()

    def _connect(self):
        port = self._port_var.get()
        if not port or not SERIAL_OK:
            return
        try:
            ser = serial.Serial(port, BAUD, timeout=1)
            with self._ser_lock:
                self._ser = ser
            self._log(f"Serial verbunden: {port}", "info")
            threading.Thread(target=self._serial_loop, daemon=True).start()
            self._save_config()
        except Exception as e:
            self._log(f"Serial Fehler: {e}", "err")

    def _disconnect(self):
        with self._ser_lock:
            ser, self._ser = self._ser, None
        if ser:
            try: ser.close()
            except: pass
        self._ble_connected = False
        self._set_dot(self._hp_dot, RED)
        self._bat_lbl.config(text="🔋 ?%", fg=FG_DIM)
        self._log("Serial getrennt", "warn")
        self._save_config()

    def _serial_loop(self):
        last_info = 0.0
        last_bat  = 0.0
        with self._ser_lock:
            ser = self._ser
        if ser:
            try: ser.write(b"info\n")
            except: pass

        while True:
            with self._ser_lock:
                ser = self._ser
            if ser is None:
                break
            try:
                now = time.time()
                if now - last_info >= INFO_INTERVAL:
                    ser.write(b"info\n")
                    last_info = now
                if self._ble_connected and now - last_bat >= BAT_INTERVAL:
                    ser.write(b"reqbat\n")
                    last_bat = now

                line = ser.readline().decode("utf-8", errors="ignore").strip()
                if not line:
                    continue

                self._log(line, "serial")

                m = re.search(r'\[BAT\]\s*(\d+)', line)
                if m:
                    self._q.put(("bat", int(m.group(1))))
                    continue

                m = re.search(r'\[VER\]\s*(Headpat\s+v[\d.]+)', line)
                if m:
                    self._q.put(("hp_ver", m.group(1)))
                    continue

                m = re.search(r'Headpat\s+Dongle\s+(v[\d.]+)', line)
                if m:
                    self._q.put(("dongle_ver", m.group(1)))
                    continue

                if re.search(r'\[BLE\]\s*Connected:', line):
                    self._q.put(("hp_ble", True))
                    try:
                        ser.write(b"reqbat\n")
                        ser.write(b"reqver\n")
                    except: pass
                    last_bat = now
                    continue

                if re.search(r'\[BLE\]\s*Disconnected', line):
                    self._q.put(("hp_ble", False))
                    continue

                if line.startswith("Connected:"):
                    up = "YES" in line
                    self._q.put(("hp_ble", up))
                    if up and not self._ble_connected:
                        try:
                            ser.write(b"reqbat\n")
                            ser.write(b"reqver\n")
                        except: pass
                        last_bat = now

            except Exception:
                self._q.put(("serial_lost", None))
                break

    def _send_motor(self, left_n: int, right_n: int):
        left_n  = max(0, min(15, left_n))
        right_n = max(0, min(15, right_n))
        with self._ser_lock:
            ser = self._ser
        if ser:
            try: ser.write(f"m:{(left_n << 4 | right_n):02X}\n".encode())
            except: pass

    def _pat_left(self):
        n = int(15 * self._intensity)
        self._send_motor(n, 0)
        self.after(400, lambda: self._send_motor(0, 0))

    def _pat_right(self):
        n = int(15 * self._intensity)
        self._send_motor(0, n)
        self.after(400, lambda: self._send_motor(0, 0))

    # ── OSC ──────────────────────────────────────────────────────────────────
    def _start_osc(self):
        if not OSC_OK:
            self._log("python-osc nicht installiert — OSC deaktiviert", "err")
            return
        self._log(f"OSC lauscht auf {OSC_HOST}:{OSC_RX_PORT}", "info")
        threading.Thread(target=self._osc_loop, daemon=True).start()

    def _osc_loop(self):
        try:
            d = dispatcher.Dispatcher()
            d.set_default_handler(self._osc_recv)
            osc_server.ThreadingOSCUDPServer((OSC_HOST, OSC_RX_PORT), d).serve_forever()
        except Exception as e:
            self._log(f"OSC Fehler: {e}", "err")

    def _osc_recv(self, address: str, *args):
        val_str = f"{float(args[0]):.3f}" if args else "?"

        # Determine filter status
        is_avatar = address.startswith("/avatar/parameters/")
        if is_avatar:
            pname = address.split("/")[-1].lower()
            is_hp = "headpat" in pname or "patstrap" in pname
            status = "PASS" if is_hp else "skip"
        else:
            status = "----"

        # Log to console
        if status == "PASS":
            self._log(f"[OSC] {address} = {val_str}", "PASS")
        elif self._osc_verbose:
            self._log(f"[OSC] {status} {address} = {val_str}",
                      "skip" if status == "skip" else "osc")

        if not is_avatar:
            return

        # Any avatar parameter proves VRChat OSC is alive
        self._last_osc = time.time()
        if not self._vrc_connected:
            self._vrc_connected = True
            self._q.put(("vrc", True))

        param = address.split("/")[-1].lower()
        if "headpat" not in param and "patstrap" not in param:
            return

        val = float(args[0]) if args else 0.0
        if val < 0.02:
            self._send_motor(0, 0)
            return
        nibble = max(0, min(15, int(val * 15 * self._intensity)))
        if "left"  in param: self._send_motor(nibble, 0)
        elif "right" in param: self._send_motor(0, nibble)
        else:                   self._send_motor(nibble, nibble)

    # ── Tick ─────────────────────────────────────────────────────────────────
    def _tick(self):
        try:
            while True:
                tag, val = self._q.get_nowait()
                if tag == "bat":
                    pct = int(val)
                    col = GREEN if pct >= 50 else YELLOW if pct >= 20 else RED
                    self._bat_lbl.config(text=f"🔋 {pct}%", fg=col)
                elif tag == "hp_ble":
                    self._ble_connected = val
                    self._set_dot(self._hp_dot, GREEN if val else RED)
                    if not val:
                        self._bat_lbl.config(text="🔋 ?%", fg=FG_DIM)
                elif tag == "vrc":
                    self._vrc_connected = val
                    self._set_dot(self._vrc_dot, GREEN if val else RED)
                elif tag == "hp_ver":
                    self._hp_version = val
                    self._hp_ver_var.set(val)
                elif tag == "dongle_ver":
                    self._dongle_version = val
                    self._dongle_ver_var.set(val)
                elif tag == "serial_lost":
                    self._disconnect()
                elif tag == "log":
                    if (self._console_win and self._console_win.winfo_exists()
                            and self._console_text):
                        ts, ltag, text = val
                        self._console_text.config(state="normal")
                        self._console_text.insert("end", f"{ts}  {text}\n", ltag)
                        n = int(self._console_text.index("end-1c").split(".")[0])
                        if n > 500:
                            self._console_text.delete("1.0", f"{n - 500}.0")
                        self._console_text.see("end")
                        self._console_text.config(state="disabled")
        except queue.Empty:
            pass

        if self._vrc_connected and time.time() - self._last_osc > VRC_TIMEOUT:
            self._vrc_connected = False
            self._set_dot(self._vrc_dot, RED)

        self.after(100, self._tick)


if __name__ == "__main__":
    App().mainloop()
