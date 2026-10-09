"""Cửa sổ Cài đặt NetWatchdog: tổng quan, Telegram, Wi-Fi, card mạng, ứng dụng, kiểm tra mạng.

Lưu vào config.json — NetWatchdog đang chạy sẽ tự nạp lại, không cần khởi động lại.
Giao diện tự theo chế độ Sáng/Tối của Windows. Chạy: NetWatchdog.exe (bản cài) hoặc pythonw app.py
"""
import ctypes
import os
import socket
import sys
import threading
import time
import tkinter as tk
from datetime import datetime
from tkinter import filedialog, font as tkfont, messagebox, ttk

import jxtd
import sysops
import updater
from netwatchdog import (CONFIG_PATH, FROZEN, INSTANCE_PORT, JX_HISTORY_DIR, LOG_PATH, STATE_PATH, State, Telegram, check_online,
                         fmt_duration, load_config, machine_name, public_ip, save_json, status_report)
from version import __version__

psutil = sysops.psutil

LIGHT = {
    "bg": "#f3f4f8", "side": "#ffffff", "surface": "#ffffff", "surface2": "#f7f8fb", "hover": "#eef0f5",
    "border": "#e3e6ee", "text": "#1b1f2a", "muted": "#6b7280", "input": "#ffffff",
    "accent": "#4f46e5", "accent_hover": "#4338ca", "accent_soft": "#eef0ff", "on_accent": "#ffffff",
    "seg": "#eceef3", "seg_sel": "#ffffff", "switch_off": "#c5cad6", "scroll": "#cfd4de",
    "success": "#16a34a", "success_soft": "#e7f7ed", "danger": "#dc2626", "danger_soft": "#fdecec",
}
DARK = {
    "bg": "#0f1117", "side": "#151821", "surface": "#181b24", "surface2": "#1e222d", "hover": "#252a37",
    "border": "#2a2f3d", "text": "#e6e8ee", "muted": "#9098a9", "input": "#11141b",
    "accent": "#6d6af8", "accent_hover": "#7f7cff", "accent_soft": "#25264a", "on_accent": "#ffffff",
    "seg": "#11141b", "seg_sel": "#2a2f3d", "switch_off": "#3a4050", "scroll": "#3a4050",
    "success": "#22c55e", "success_soft": "#143222", "danger": "#ef4444", "danger_soft": "#3a1717",
}

ICONS = {"home": "", "send": "", "wifi": "", "adapter": "",
         "restart": "", "check": "", "refresh": "", "game": "", "folder": "",
         "chart": ""}


def windows_dark():
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            return winreg.QueryValueEx(k, "AppsUseLightTheme")[0] == 0
    except (OSError, ImportError):
        return False


def service_running():
    """NetWatchdog giữ cổng INSTANCE_PORT khi đang chạy → bind thất bại nghĩa là đang chạy."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", INSTANCE_PORT))
        return False
    except OSError:
        return True
    finally:
        s.close()


def collect_status():
    cfg = load_config(CONFIG_PATH)
    state = State(STATE_PATH)
    online, detail = check_online(cfg)
    d = {"online": online, "detail": detail, "running": service_running(), "ssid": sysops.current_ssid(),
         "lan": sysops.local_ip(), "wan": public_ip() if online and cfg["report"].get("public_ip") else "",
         "uptime": fmt_duration(sysops.uptime_sec()), "machine": machine_name(cfg),
         "cpu": None, "ram": None, "ram_sub": "", "bat": None, "bat_sub": "", "disk": None, "disk_sub": "",
         "outbox": len(state.get("outbox") or []), "last_outage": state.get("last_outage") or "",
         "reboots": len([t for t in state.get("reboots") or [] if t > time.time() - 86400]),
         "max_reboots": cfg["recovery"].get("max_reboots_per_day", 3)}
    if psutil:
        d["cpu"] = psutil.cpu_percent(interval=0.5)
        vm = psutil.virtual_memory()
        d["ram"], d["ram_sub"] = vm.percent, f"{vm.used / 2**30:.1f} / {vm.total / 2**30:.1f} GB"
        bat = psutil.sensors_battery() if hasattr(psutil, "sensors_battery") else None
        if bat:
            d["bat"], d["bat_sub"] = bat.percent, "Đang sạc" if bat.power_plugged else "Đang dùng pin"
    du = sysops.disk_usage()
    if du:
        d["disk"] = du.used * 100 / du.total
        d["disk_sub"] = f"Trống {du.free / 2**30:.0f} / {du.total / 2**30:.0f} GB"
    return d


# ================================================================ Widget tùy biến
class Toggle(tk.Canvas):
    """Công tắc bật/tắt kiểu Windows 11, gắn với BooleanVar."""

    def __init__(self, master, variable, c, scale=1.0):
        self.w, self.h = int(44 * scale), int(22 * scale)
        super().__init__(master, width=self.w, height=self.h, bg=master["bg"], highlightthickness=0, bd=0,
                         cursor="hand2", takefocus=1)
        self.var, self.c, self.pad = variable, c, max(3, int(4 * scale))
        self.bind("<Button-1>", self.toggle)
        self.bind("<space>", self.toggle)
        variable.trace_add("write", lambda *a: self.draw())
        self.draw()

    def toggle(self, _=None):
        self.var.set(not self.var.get())

    def draw(self):
        self.delete("all")
        on, w, h, p = self.var.get(), self.w - 1, self.h - 1, self.pad
        fill = self.c["accent"] if on else self.c["switch_off"]
        self.create_oval(0, 0, h, h, fill=fill, outline="")
        self.create_oval(w - h, 0, w, h, fill=fill, outline="")
        self.create_rectangle(h / 2, 0, w - h / 2, h + 1, fill=fill, outline="")
        d = h - 2 * p
        x = w - p - d if on else p
        self.create_oval(x, p, x + d, p + d, fill="#ffffff", outline="")


class Segmented(tk.Frame):
    """Nhóm nút chọn một (thay cho Radiobutton)."""

    def __init__(self, master, app, variable, options):
        c = app.c
        super().__init__(master, bg=c["seg"], padx=3, pady=3)
        self.app, self.var, self.btns = app, variable, {}
        for val, txt in options:
            lb = tk.Label(self, text=txt, padx=14, pady=5, cursor="hand2")
            lb.pack(side="left", padx=1)
            lb.bind("<Button-1>", lambda e, v=val: variable.set(v))
            self.btns[val] = lb
        variable.trace_add("write", lambda *a: self.draw())
        self.draw()

    def draw(self):
        c = self.app.c
        for val, lb in self.btns.items():
            sel = val == self.var.get()
            lb.config(bg=c["seg_sel"] if sel else c["seg"],
                      fg=c["text"] if sel else c["muted"], font=self.app.f_bold if sel else self.app.f_body)


class Card(tk.Frame):
    """Khung nền trắng có viền; add_row() tạo dòng cài đặt kiểu Windows Settings."""

    def __init__(self, master, app, title=None, desc=None):
        c = app.c
        super().__init__(master, bg=c["surface"], highlightthickness=1, highlightbackground=c["border"],
                         highlightcolor=c["border"])
        self.app, self.rows = app, 0
        if title:
            tk.Label(self, text=title, font=app.f_h2, bg=c["surface"], fg=c["text"]).pack(
                anchor="w", padx=20, pady=(16, 0))
            if desc:
                tk.Label(self, text=desc, font=app.f_small, bg=c["surface"], fg=c["muted"], justify="left",
                         wraplength=app.S(720)).pack(anchor="w", padx=20, pady=(2, 0))
            tk.Frame(self, height=8, bg=c["surface"]).pack()

    def add_row(self, title, desc=None):
        c, app = self.app.c, self.app
        if self.rows:
            tk.Frame(self, bg=c["border"], height=1).pack(fill="x", padx=20)
        self.rows += 1
        r = tk.Frame(self, bg=c["surface"])
        r.pack(fill="x", padx=20, pady=12)
        txt = tk.Frame(r, bg=c["surface"])
        txt.pack(side="left", fill="x", expand=True)
        tk.Label(txt, text=title, font=app.f_bold, bg=c["surface"], fg=c["text"]).pack(anchor="w")
        if desc:
            tk.Label(txt, text=desc, font=app.f_small, bg=c["surface"], fg=c["muted"], justify="left",
                     wraplength=app.S(440)).pack(anchor="w")
        ctl = tk.Frame(r, bg=c["surface"])
        ctl.pack(side="right", padx=(16, 0))
        return ctl


class Tile(tk.Frame):
    """Ô số liệu trên trang Tổng quan, có thanh phần trăm tùy chọn."""

    def __init__(self, master, app, title, bar=False):
        c = app.c
        super().__init__(master, bg=c["surface"], highlightthickness=1, highlightbackground=c["border"],
                         padx=18, pady=14)
        self.app = app
        tk.Label(self, text=title.upper(), font=app.f_tiny, bg=c["surface"], fg=c["muted"]).pack(anchor="w")
        self.value = tk.Label(self, text="—", font=app.f_big, bg=c["surface"], fg=c["text"])
        self.value.pack(anchor="w", pady=(4, 0))
        self.sub = tk.Label(self, text="", font=app.f_small, bg=c["surface"], fg=c["muted"])
        self.sub.pack(anchor="w")
        self.bar, self.pct = None, None
        if bar:
            self.bar = tk.Canvas(self, height=app.S(6), bg=c["surface"], highlightthickness=0, bd=0)
            self.bar.pack(fill="x", pady=(10, 0))
            self.bar.bind("<Configure>", lambda e: self.draw_bar())

    def set(self, value, sub="", pct=None):
        self.value.config(text=value)
        self.sub.config(text=sub)
        self.pct = pct
        self.draw_bar()

    def draw_bar(self):
        if not self.bar:
            return
        c = self.app.c
        self.bar.delete("all")
        w, h = self.bar.winfo_width(), int(self.bar["height"])
        self.bar.create_rectangle(0, 0, w, h, fill=c["seg"], outline="")
        if self.pct is not None:
            color = c["danger"] if self.pct >= 90 else c["accent"]
            self.bar.create_rectangle(0, 0, w * min(self.pct, 100) / 100, h, fill=color, outline="")


class BarChart(tk.Canvas):
    """Biểu đồ cột một chuỗi số liệu (có số âm), lưới mờ, di chuột để xem giá trị từng cột."""

    def __init__(self, master, app, fmt, height=200):
        c = app.c
        super().__init__(master, height=app.S(height), bg=c["surface"], highlightthickness=0, bd=0)
        self.app, self.fmt = app, fmt
        self.labels, self.values, self.tips, self.hover = [], [], [], None
        self.bind("<Configure>", lambda e: self.draw())
        self.bind("<Motion>", self._motion)
        self.bind("<Leave>", lambda e: self._set_hover(None))

    def set(self, labels, values, tips=None):
        self.labels, self.values, self.tips, self.hover = labels, values, tips or labels, None
        self.draw()

    def _geom(self):
        S = self.app.S
        w, h = self.winfo_width(), self.winfo_height()
        return S(64), S(12), w - S(12), h - S(26)  # trái, trên, phải, dưới của vùng vẽ

    def _set_hover(self, i):
        if i != self.hover:
            self.hover = i
            self.draw()

    def _motion(self, e):
        if not self.values:
            return
        x0, _, x1, _ = self._geom()
        slot = (x1 - x0) / len(self.values)
        i = int((e.x - x0) // slot) if slot > 0 else -1
        self._set_hover(i if 0 <= i < len(self.values) and x0 <= e.x <= x1 else None)

    def draw(self):
        c, S, app = self.app.c, self.app.S, self.app
        self.delete("all")
        x0, y0, x1, y1 = self._geom()
        if x1 <= x0 or y1 <= y0:
            return
        if not self.values:
            self.create_text((x0 + x1) / 2, (y0 + y1) / 2, text="Chưa có dữ liệu", fill=c["muted"], font=app.f_small)
            return
        hi, lo = max(0.0, max(self.values)), min(0.0, min(self.values))
        if hi == lo:
            hi = 1.0
        nice = _nice_step((hi - lo) / 4)
        hi, lo = nice * -(-hi // nice), nice * (lo // nice)
        y = lambda v: y1 - (v - lo) / (hi - lo) * (y1 - y0)  # noqa: E731
        v = lo
        while v <= hi + nice / 2:  # lưới + nhãn trục
            yy = y(v)
            self.create_line(x0, yy, x1, yy, fill=c["border"] if v else c["muted"], width=1)
            self.create_text(x0 - S(8), yy, text=self.fmt(v), anchor="e", fill=c["muted"], font=app.f_tiny)
            v += nice
        n = len(self.values)
        slot = (x1 - x0) / n
        bw = max(2, min(S(28), slot - S(4)))
        every = max(1, int(S(44) // slot) + 1)  # nhãn trục X thưa ra để không chồng nhau
        for i, val in enumerate(self.values):
            cx = x0 + slot * (i + .5)
            if i == self.hover:
                self.create_rectangle(x0 + slot * i, y0, x0 + slot * (i + 1), y1, fill=c["surface2"], outline="")
            if val:
                color = c["accent_hover"] if i == self.hover else (c["accent"] if val > 0 else c["danger"])
                self.create_rectangle(cx - bw / 2, min(y(val), y(0)), cx + bw / 2, max(y(val), y(0)),
                                      fill=color, outline="")
            if i % every == 0:
                self.create_text(cx, y1 + S(6), text=self.labels[i], anchor="n", fill=c["muted"], font=app.f_tiny)
        self.create_line(x0, y(0), x1, y(0), fill=c["muted"])
        if self.hover is not None:
            i = self.hover
            cx = x0 + slot * (i + .5)
            txt = f"{self.tips[i]}:  {self.fmt(self.values[i])}"
            t = self.create_text(0, 0, text=txt, anchor="nw", fill=c["text"], font=app.f_bold)
            bx0, by0, bx1, by1 = self.bbox(t)
            tw, th = bx1 - bx0, by1 - by0
            tx = min(max(x0, cx - tw / 2), x1 - tw)
            self.coords(t, tx, y0)
            bg = self.create_rectangle(tx - S(8), y0 - S(4), tx + tw + S(8), y0 + th + S(4), fill=c["surface"],
                                       outline=c["border"])
            self.tag_raise(t, bg)


def _nice_step(raw):
    """Bước lưới "đẹp" (1, 2, 5 × 10^n) gần với raw."""
    import math
    if raw <= 0:
        return 1.0
    p = 10 ** math.floor(math.log10(raw))
    return next(m * p for m in (1, 2, 5, 10) if m * p >= raw)


class OrderedPicker(tk.Frame):
    """Hai danh sách: bên trái các mục có sẵn, bên phải các mục đã chọn (có thứ tự ưu tiên)."""

    def __init__(self, master, app, left_title, right_title, on_change=None):
        c = app.c
        super().__init__(master, bg=c["surface"])
        self.app, self.on_change = app, on_change or (lambda: None)
        self.left, self.left_count = self._box(left_title, 0)
        self.right, self.right_count = self._box(right_title, 2)
        mid = tk.Frame(self, bg=c["surface"])
        mid.grid(row=1, column=1, padx=12)
        for txt, cmd in (("Thêm  ›", self.add), ("‹  Bỏ", self.remove), ("▲  Lên", lambda: self.move(-1)),
                         ("▼  Xuống", lambda: self.move(1))):
            ttk.Button(mid, text=txt, command=cmd, width=9).pack(pady=3, fill="x")
        self.columnconfigure(0, weight=1, uniform="col")
        self.columnconfigure(2, weight=1, uniform="col")
        self.rowconfigure(1, weight=1)
        self.left.bind("<Double-Button-1>", lambda e: self.add())
        self.right.bind("<Double-Button-1>", lambda e: self.remove())

    def _box(self, title, col):
        c, app = self.app.c, self.app
        head = tk.Frame(self, bg=c["surface"])
        head.grid(row=0, column=col, sticky="we", pady=(0, 6))
        tk.Label(head, text=title, font=app.f_bold, bg=c["surface"], fg=c["text"]).pack(side="left")
        count = tk.Label(head, text="0", font=app.f_tiny, bg=c["seg"], fg=c["muted"], padx=7)
        count.pack(side="left", padx=8)
        box = tk.Frame(self, bg=c["input"], highlightthickness=1, highlightbackground=c["border"])
        box.grid(row=1, column=col, sticky="nsew")
        lb = tk.Listbox(box, selectmode=tk.EXTENDED, height=10, exportselection=False, activestyle="none",
                        bg=c["input"], fg=c["text"], selectbackground=c["accent"], selectforeground=c["on_accent"],
                        highlightthickness=0, relief="flat", bd=0, font=app.f_body)
        sb = ttk.Scrollbar(box, orient="vertical", command=lb.yview)
        lb.configure(yscrollcommand=lambda a, b: (sb.set(a, b), sb.pack(side="right", fill="y", padx=2, pady=2)
                                                  if (float(a), float(b)) != (0.0, 1.0) else sb.pack_forget()))
        lb.pack(side="left", fill="both", expand=True, padx=(4, 0), pady=4)
        return lb, count

    def _changed(self, user=True):
        self.left_count.config(text=str(self.left.size()))
        self.right_count.config(text=str(self.right.size()))
        if user:
            self.on_change()

    def set_items(self, available, selected):
        self.left.delete(0, tk.END)
        self.right.delete(0, tk.END)
        for s in selected:
            self.right.insert(tk.END, "  " + s)
        for a in available:
            if a not in selected:
                self.left.insert(tk.END, "  " + a)
        self._changed(user=False)

    def get(self):
        return [s.strip() for s in self.right.get(0, tk.END)]

    def add(self):
        for i in self.left.curselection()[::-1]:
            self.right.insert(tk.END, self.left.get(i))
            self.left.delete(i)
        self._changed()

    def remove(self):
        for i in self.right.curselection()[::-1]:
            self.left.insert(tk.END, self.right.get(i))
            self.right.delete(i)
        self._changed()

    def move(self, d):
        sel = self.right.curselection()
        if len(sel) != 1:
            return
        i, j = sel[0], sel[0] + d
        if 0 <= j < self.right.size():
            v = self.right.get(i)
            self.right.delete(i)
            self.right.insert(j, v)
            self.right.selection_set(j)
            self._changed()


class Page(tk.Frame):
    """Trang có tiêu đề và vùng nội dung cuộn được (self.body)."""

    def __init__(self, master, app, title, subtitle):
        c = app.c
        super().__init__(master, bg=c["bg"])
        self.app = app
        self.canvas = tk.Canvas(self, bg=c["bg"], highlightthickness=0, bd=0)
        self.sb = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.sb.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.inner = tk.Frame(self.canvas, bg=c["bg"], padx=32, pady=24)
        self.win = self.canvas.create_window(0, 0, window=self.inner, anchor="nw")
        tk.Label(self.inner, text=title, font=app.f_title, bg=c["bg"], fg=c["text"]).pack(anchor="w")
        tk.Label(self.inner, text=subtitle, font=app.f_body, bg=c["bg"], fg=c["muted"], justify="left").pack(
            anchor="w", pady=(2, 18))
        self.body = tk.Frame(self.inner, bg=c["bg"])
        self.body.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda e: self.relayout())

    def relayout(self):
        self.update_idletasks()
        w, h = self.canvas.winfo_width(), self.canvas.winfo_height()
        need = self.inner.winfo_reqheight()
        self.canvas.itemconfigure(self.win, width=w, height=max(h, need))
        self.canvas.configure(scrollregion=(0, 0, w, max(h, need)))
        if need > h + 1:
            self.sb.pack(side="right", fill="y")
        else:
            self.sb.pack_forget()
            self.canvas.yview_moveto(0)

    def scroll(self, units):
        if self.sb.winfo_ismapped():
            self.canvas.yview_scroll(units, "units")


# ================================================================ Cửa sổ chính
class SettingsApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.c = DARK if windows_dark() else LIGHT
        self.dpi = self.winfo_fpixels("1i") / 96
        self.title("NetWatchdog — Cài đặt")
        w, h = self.S(1100), self.S(740)
        self.geometry(f"{w}x{h}+{(self.winfo_screenwidth() - w) // 2}+{max(0, (self.winfo_screenheight() - h) // 2 - 30)}")
        self.minsize(self.S(940), self.S(600))
        self.configure(bg=self.c["bg"])
        ico = os.path.join(getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__))),
                           "packaging", "netwatchdog.ico")
        if os.path.exists(ico):
            self.iconbitmap(default=ico)
        self.cfg = load_config(CONFIG_PATH)
        self.vars = {}
        self.dirty = False
        self.busy_status = False
        self._init_fonts()
        self._init_style()
        self._dark_titlebar()

        self.side = tk.Frame(self, bg=self.c["side"], width=self.S(250), highlightthickness=0)
        self.side.pack(side="left", fill="y")
        self.side.pack_propagate(False)
        tk.Frame(self, bg=self.c["border"], width=1).pack(side="left", fill="y")
        main = tk.Frame(self, bg=self.c["bg"])
        main.pack(side="left", fill="both", expand=True)
        self._build_footer(main)
        self.pages_box = tk.Frame(main, bg=self.c["bg"])
        self.pages_box.pack(fill="both", expand=True)
        self.pages_box.rowconfigure(0, weight=1)
        self.pages_box.columnconfigure(0, weight=1)

        self.pages, self.nav = {}, {}
        for key, icon, text, builder in (
                ("home", "home", "Tổng quan", self.page_home),
                ("telegram", "send", "Telegram", self.page_telegram),
                ("wifi", "wifi", "Wi-Fi dự phòng", self.page_wifi),
                ("adapters", "adapter", "Card mạng", self.page_adapters),
                ("reboot", "restart", "Khởi động lại & Ứng dụng", self.page_reboot),
                ("jxtd", "game", "jxtdAuto", self.page_jxtd),
                ("stats", "chart", "Thống kê jxtdAuto", self.page_stats),
                ("check", "check", "Kiểm tra mạng", self.page_check)):
            page = builder()
            page.grid(row=0, column=0, sticky="nsew")
            self.pages[key] = page
            self.nav[key] = (icon, text)
        self._build_sidebar()

        for v in self.vars.values():
            v.trace_add("write", lambda *a: self.mark_dirty())
        self.launch_mode.trace_add("write", lambda *a: self.mark_dirty())
        self.bind_all("<MouseWheel>", self._on_wheel)
        self.bind("<Control-s>", lambda e: self.save())
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.show("home")
        self.after(100, self.refresh_lists)
        self.after(150, self.refresh_status)
        if FROZEN:
            self.after(3000, self.check_update)
        self.after(1000, self._tick)

    # ------------------------------------------------ nền tảng
    def S(self, px):
        return int(px * self.dpi)

    def _init_fonts(self):
        fams = set(tkfont.families(self))
        ui = "Segoe UI Variable Text" if "Segoe UI Variable Text" in fams else "Segoe UI"
        semi = "Segoe UI Semibold" if "Segoe UI Semibold" in fams else ui
        disp = "Segoe UI Variable Display Semib" if "Segoe UI Variable Display Semib" in fams else semi
        icon = next((f for f in ("Segoe Fluent Icons", "Segoe MDL2 Assets") if f in fams), None)
        self.f_body = tkfont.Font(self, family=ui, size=10)
        self.f_bold = tkfont.Font(self, family=semi, size=10)
        self.f_small = tkfont.Font(self, family=ui, size=9)
        self.f_tiny = tkfont.Font(self, family=semi, size=8)
        self.f_h2 = tkfont.Font(self, family=semi, size=12)
        self.f_big = tkfont.Font(self, family=disp, size=17)
        self.f_title = tkfont.Font(self, family=disp, size=22)
        self.f_hero = tkfont.Font(self, family=disp, size=19)
        self.f_icon = tkfont.Font(self, family=icon, size=12) if icon else None
        self.f_icon_big = tkfont.Font(self, family=icon, size=22) if icon else None
        self.option_add("*Font", self.f_body)

    def _init_style(self):
        c = self.c
        st = ttk.Style(self)
        st.theme_use("clam")
        st.configure(".", background=c["surface"], foreground=c["text"], font=self.f_body,
                     bordercolor=c["border"], lightcolor=c["surface"], darkcolor=c["surface"],
                     troughcolor=c["surface2"], focuscolor=c["accent"], selectbackground=c["accent"],
                     selectforeground=c["on_accent"], insertcolor=c["text"], fieldbackground=c["input"])
        st.configure("TEntry", fieldbackground=c["input"], foreground=c["text"], bordercolor=c["border"],
                     lightcolor=c["input"], darkcolor=c["input"], padding=(10, 6), insertcolor=c["text"])
        st.map("TEntry", bordercolor=[("focus", c["accent"])], lightcolor=[("focus", c["accent"])],
               darkcolor=[("focus", c["input"])])
        for name, bg, fg, hover, border in (
                ("TButton", c["surface"], c["text"], c["hover"], c["border"]),
                ("Accent.TButton", c["accent"], c["on_accent"], c["accent_hover"], c["accent"])):
            st.configure(name, background=bg, foreground=fg, bordercolor=border, lightcolor=bg, darkcolor=bg,
                         focuscolor=bg, padding=(14, 6), relief="solid", borderwidth=1, font=self.f_bold,
                         anchor="center")
            st.map(name, background=[("disabled", c["seg"]), ("pressed", hover), ("active", hover)],
                   lightcolor=[("pressed", hover), ("active", hover)], darkcolor=[("pressed", hover), ("active", hover)],
                   foreground=[("disabled", c["muted"])], bordercolor=[("disabled", c["seg"])])
        st.configure("TCombobox", fieldbackground=c["input"], background=c["surface"], foreground=c["text"],
                     arrowcolor=c["muted"], bordercolor=c["border"], lightcolor=c["input"], darkcolor=c["input"],
                     padding=(10, 5), selectbackground=c["input"], selectforeground=c["text"])
        st.map("TCombobox", fieldbackground=[("readonly", c["input"])], foreground=[("readonly", c["text"])],
               bordercolor=[("focus", c["accent"])])
        self.option_add("*TCombobox*Listbox.background", c["input"])
        self.option_add("*TCombobox*Listbox.foreground", c["text"])
        self.option_add("*TCombobox*Listbox.selectBackground", c["accent"])
        self.option_add("*TCombobox*Listbox.selectForeground", c["on_accent"])
        st.configure("Treeview", background=c["surface"], fieldbackground=c["surface"], foreground=c["text"],
                     rowheight=self.S(34), borderwidth=0, relief="flat")
        st.map("Treeview", background=[("selected", c["accent_soft"])], foreground=[("selected", c["text"])])
        st.configure("Treeview.Heading", background=c["surface2"], foreground=c["muted"], relief="flat",
                     borderwidth=0, font=self.f_tiny, padding=(10, 8), lightcolor=c["surface2"],
                     darkcolor=c["surface2"], bordercolor=c["border"])
        st.map("Treeview.Heading", background=[("active", c["surface2"])])
        st.layout("Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
        st.layout("Vertical.TScrollbar", [("Vertical.Scrollbar.trough", {"sticky": "ns", "children": [
            ("Vertical.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"})]})])
        st.configure("Vertical.TScrollbar", background=c["scroll"], troughcolor=c["bg"], bordercolor=c["bg"],
                     lightcolor=c["scroll"], darkcolor=c["scroll"], gripcount=0, arrowsize=self.S(9))
        st.map("Vertical.TScrollbar", background=[("active", c["muted"])])

    def _dark_titlebar(self):
        if self.c is not DARK or sys.platform != "win32":
            return
        try:
            self.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(self.winfo_id())
            val = ctypes.c_int(1)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(val), ctypes.sizeof(val))
        except (AttributeError, OSError):
            pass

    def _on_wheel(self, e):
        if isinstance(e.widget, (tk.Listbox, ttk.Treeview)):
            return
        self.pages[self.current].scroll(-1 if e.delta > 0 else 1)

    # ------------------------------------------------ khung
    def _build_sidebar(self):
        c, s = self.c, self.side
        brand = tk.Frame(s, bg=c["side"])
        brand.pack(fill="x", padx=18, pady=(22, 24))
        size = self.S(36)
        logo = tk.Canvas(brand, width=size, height=size, bg=c["side"], highlightthickness=0)
        logo.pack(side="left")
        logo.create_oval(0, 0, size - 1, size - 1, fill=c["accent"], outline="")
        if self.f_icon:
            logo.create_text(size / 2, size / 2 + 1, text=ICONS["wifi"], font=self.f_icon, fill=c["on_accent"])
        t = tk.Frame(brand, bg=c["side"])
        t.pack(side="left", padx=10)
        tk.Label(t, text="NetWatchdog", font=self.f_h2, bg=c["side"], fg=c["text"]).pack(anchor="w")
        tk.Label(t, text=machine_name(self.cfg), font=self.f_small, bg=c["side"], fg=c["muted"]).pack(anchor="w")

        self.nav_items = {}
        for key, (icon, text) in self.nav.items():
            row = tk.Frame(s, bg=c["side"], cursor="hand2")
            row.pack(fill="x", padx=10, pady=1)
            bar = tk.Frame(row, bg=c["side"], width=3)
            bar.pack(side="left", fill="y", pady=8)
            ic = tk.Label(row, text=ICONS[icon] if self.f_icon else "", font=self.f_icon or self.f_body,
                          bg=c["side"], fg=c["muted"], width=2)
            ic.pack(side="left", padx=(8, 4), pady=9)
            lb = tk.Label(row, text=text, bg=c["side"], fg=c["text"], font=self.f_body, anchor="w")
            lb.pack(side="left", fill="x", expand=True)
            for w in (row, ic, lb, bar):
                w.bind("<Button-1>", lambda e, k=key: self.show(k))
                w.bind("<Enter>", lambda e, k=key: self._nav_paint(k, hover=True))
                w.bind("<Leave>", lambda e, k=key: self._nav_paint(k))
            self.nav_items[key] = (row, bar, ic, lb)

        ver = tk.Frame(s, bg=c["side"])
        ver.pack(side="bottom", fill="x", padx=18, pady=(0, 18))
        tk.Label(ver, text=f"Phiên bản {__version__}", font=self.f_small, bg=c["side"], fg=c["muted"]).pack(side="left")
        upd = tk.Label(ver, text="Kiểm tra cập nhật", font=self.f_small, bg=c["side"], fg=c["accent"], cursor="hand2")
        upd.pack(side="right")
        upd.bind("<Button-1>", lambda e: self.check_update(manual=True))
        foot = tk.Frame(s, bg=c["side"])
        foot.pack(side="bottom", fill="x", padx=18, pady=(0, 10))
        self.svc_dot = tk.Canvas(foot, width=10, height=10, bg=c["side"], highlightthickness=0)
        self.svc_dot.pack(side="left")
        self.svc_lbl = tk.Label(foot, text="Đang kiểm tra dịch vụ…", font=self.f_small, bg=c["side"], fg=c["muted"])
        self.svc_lbl.pack(side="left", padx=8)

    def _nav_paint(self, key, hover=False):
        c = self.c
        row, bar, ic, lb = self.nav_items[key]
        active = key == getattr(self, "current", None)
        bg = c["accent_soft"] if active else (c["hover"] if hover else c["side"])
        for w in (row, ic, lb):
            w.config(bg=bg)
        bar.config(bg=c["accent"] if active else bg)
        ic.config(fg=c["accent"] if active else c["muted"])
        lb.config(fg=c["accent"] if active else c["text"], font=self.f_bold if active else self.f_body)

    def show(self, key):
        self.current = key
        self.pages[key].tkraise()
        self.pages[key].relayout()
        for k in self.nav_items:
            self._nav_paint(k)
        if key == "home":
            self.refresh_status()
        elif key == "stats":
            self.stats_load()

    def _build_footer(self, main):
        c = self.c
        bar = tk.Frame(main, bg=c["surface"], padx=24, pady=12)
        bar.pack(fill="x", side="bottom")
        tk.Frame(main, bg=c["border"], height=1).pack(fill="x", side="bottom")
        self.save_btn = ttk.Button(bar, text="Lưu thay đổi", style="Accent.TButton", command=self.save)
        self.save_btn.pack(side="right")
        ttk.Button(bar, text="Đóng", command=self.close).pack(side="right", padx=8)
        self.status_lbl = tk.Label(bar, text="", font=self.f_small, bg=c["surface"], fg=c["muted"])
        self.status_lbl.pack(side="left")
        self.set_status_text()

    def set_status_text(self, text=None, color=None):
        self.status_lbl.config(text=text or f"Cấu hình: {CONFIG_PATH}", fg=color or self.c["muted"])

    def mark_dirty(self):
        if not self.dirty:
            self.dirty = True
            self.mark_dirty_text()

    # ------------------------------------------------ helpers
    def entry(self, parent, key, value, width=34, show=None, numeric=False):
        v = tk.StringVar(value=str(value))
        opts = {}
        if numeric:
            opts = {"validate": "key", "validatecommand": (self.register(lambda s: s == "" or s.isdigit()), "%P"),
                    "justify": "right"}
        e = ttk.Entry(parent, textvariable=v, width=width, show=show, **opts)
        e.pack(side="left")
        self.vars[key] = v
        return e

    def number(self, card, title, desc, key, value, unit=""):
        ctl = card.add_row(title, desc)
        self.entry(ctl, key, value, width=7, numeric=True)
        if unit:
            tk.Label(ctl, text=unit, font=self.f_small, bg=self.c["surface"], fg=self.c["muted"], width=6,
                     anchor="w").pack(side="left", padx=(8, 0))

    def toggle(self, card, title, desc, key, value):
        ctl = card.add_row(title, desc)
        v = tk.BooleanVar(value=bool(value))
        self.vars[key] = v
        Toggle(ctl, v, self.c, self.dpi).pack()
        return v

    def gap(self, parent, h=16):
        tk.Frame(parent, bg=self.c["bg"], height=h).pack(fill="x")

    def note(self, parent, text, bg=None):
        tk.Label(parent, text=text, font=self.f_small, bg=bg or self.c["bg"], fg=self.c["muted"],
                 justify="left", wraplength=self.S(760)).pack(anchor="w")

    # ------------------------------------------------ trang
    def page_home(self):
        c = self.c
        p = Page(self.pages_box, self, "Tổng quan", "Tình trạng mạng và máy tính lúc này.")
        self.update_banner = tk.Frame(p.body, bg=c["accent_soft"], padx=20, pady=14)
        ub = self.update_banner
        self.update_title = tk.Label(ub, text="", font=self.f_bold, bg=c["accent_soft"], fg=c["accent"])
        self.update_title.pack(side="left")
        self.update_sub = tk.Label(ub, text="", font=self.f_small, bg=c["accent_soft"], fg=c["muted"])
        self.update_sub.pack(side="left", padx=12)
        self.update_btn = ttk.Button(ub, text="Cập nhật ngay", style="Accent.TButton", command=self.install_update)
        self.update_btn.pack(side="right")
        hero = Card(p.body, self)
        hero.pack(fill="x")
        self.hero_card = hero
        inner = tk.Frame(hero, bg=c["surface"], padx=22, pady=20)
        inner.pack(fill="x")
        size = self.S(56)
        self.hero_icon = tk.Canvas(inner, width=size, height=size, bg=c["surface"], highlightthickness=0)
        self.hero_icon.pack(side="left")
        txt = tk.Frame(inner, bg=c["surface"])
        txt.pack(side="left", padx=16, fill="x", expand=True)
        self.hero_title = tk.Label(txt, text="Đang kiểm tra…", font=self.f_hero, bg=c["surface"], fg=c["text"])
        self.hero_title.pack(anchor="w")
        self.hero_sub = tk.Label(txt, text="", font=self.f_body, bg=c["surface"], fg=c["muted"], justify="left")
        self.hero_sub.pack(anchor="w")
        btns = tk.Frame(inner, bg=c["surface"])
        btns.pack(side="right")
        self.refresh_btn = ttk.Button(btns, text="Làm mới", command=self.refresh_status)
        self.refresh_btn.pack(side="right")
        ttk.Button(btns, text="Mở log", command=self.open_log).pack(side="right", padx=8)
        self.svc_btn = ttk.Button(btns, text="Bật chạy nền", command=self.toggle_service)
        self.svc_btn.pack(side="right")
        self.pill = tk.Label(btns, text="", font=self.f_tiny, padx=10, pady=4)
        self.pill.pack(side="right", padx=8)
        self._paint_hero(None)

        self.gap(p.body)
        grid = tk.Frame(p.body, bg=c["bg"])
        grid.pack(fill="x")
        self.tiles = {}
        specs = (("uptime", "Thời gian chạy", False), ("cpu", "CPU", True), ("ram", "Bộ nhớ RAM", True),
                 ("disk", "Ổ hệ thống", True), ("lan", "IP nội bộ", False), ("wan", "IP công cộng", False),
                 ("bat", "Pin", True), ("reboots", "Reboot do mất mạng (24h)", False),
                 ("outbox", "Tin Telegram chờ gửi", False))
        for i, (key, title, bar) in enumerate(specs):
            t = Tile(grid, self, title, bar)
            t.grid(row=i // 3, column=i % 3, sticky="nsew", padx=(0 if i % 3 == 0 else 8, 0), pady=(0, 8))
            self.tiles[key] = t
        for col in range(3):
            grid.columnconfigure(col, weight=1, uniform="tile")
        self.updated_lbl = tk.Label(p.body, text="", font=self.f_small, bg=c["bg"], fg=c["muted"])
        self.updated_lbl.pack(anchor="w", pady=(4, 0))
        return p

    def page_telegram(self):
        p = Page(self.pages_box, self, "Telegram", "Nhận thông báo và điều khiển máy từ xa qua bot Telegram.")
        t = self.cfg["telegram"]
        card = Card(p.body, self, "Kết nối bot")
        card.pack(fill="x")
        self.entry(card.add_row("Tên máy", "Hiển thị trong tin nhắn. Để trống = tên máy tính."), "machine_name",
                   self.cfg.get("machine_name", ""))
        ctl = card.add_row("Bot token", "Lấy từ @BotFather → /newbot.")
        tok = self.entry(ctl, "tg_token", t.get("bot_token", ""), width=30, show="•")
        eye = ttk.Button(ctl, text="Hiện", width=5)
        eye.config(command=lambda: (tok.config(show="" if tok.cget("show") else "•"),
                                    eye.config(text="Ẩn" if not tok.cget("show") else "Hiện")))
        eye.pack(side="left", padx=(8, 0))
        ctl = card.add_row("Chat ID", "Mở bot, gửi /start rồi bấm Lấy Chat ID.")
        self.entry(ctl, "tg_chat", t.get("chat_id", ""), width=22)
        ttk.Button(ctl, text="Lấy Chat ID", command=self.fetch_chat_id).pack(side="left", padx=(8, 0))
        ctl = card.add_row("Kiểm tra kết nối", "Gửi một tin kèm báo cáo tình trạng máy.")
        ttk.Button(ctl, text="Gửi tin thử", style="Accent.TButton", command=self.test_telegram).pack()

        self.gap(p.body)
        card = Card(p.body, self, "Thông báo & lệnh")
        card.pack(fill="x")
        self.toggle(card, "Nhận lệnh điều khiển", "Lệnh và nút bấm (/menu) trên Telegram — chỉ từ Chat ID trên.",
                    "tg_cmds", t.get("commands", True))
        self.number(card, "Báo cáo định kỳ", "Gửi tình trạng máy theo chu kỳ. 0 = tắt.", "heartbeat",
                    self.cfg["report"].get("heartbeat_min", 360), "phút")
        return p

    def page_wifi(self):
        p = Page(self.pages_box, self, "Wi-Fi dự phòng",
                 "Khi mất mạng sẽ thử kết nối lần lượt các mạng đã chọn, từ trên xuống.")
        card = Card(p.body, self, "Danh sách mạng",
                    "Chỉ dùng được mạng đã từng kết nối và lưu mật khẩu trên máy. Nhấp đúp để chuyển qua lại.")
        card.pack(fill="both", expand=True)
        self.wifi_picker = OrderedPicker(card, self, "Đã lưu trên máy", "Sẽ thử (theo thứ tự)", self.mark_dirty)
        self.wifi_picker.pack(fill="both", expand=True, padx=20, pady=(4, 16))
        tb = tk.Frame(card, bg=self.c["surface"])
        tb.pack(fill="x", padx=20, pady=(0, 16))
        ttk.Button(tb, text="Làm mới danh sách", command=self.refresh_lists).pack(side="left")
        self.gap(p.body)
        card = Card(p.body, self)
        card.pack(fill="x")
        self.entry(card.add_row("Card Wi-Fi sử dụng", "Tên card trong netsh. Để trống = mặc định."), "wifi_iface",
                   self.cfg["recovery"].get("wifi_interface", ""), 26)
        return p

    def page_adapters(self):
        p = Page(self.pages_box, self, "Card mạng",
                 "Nếu đổi Wi-Fi không được sẽ tắt/bật lại các card đã chọn (cần quyền Administrator).")
        card = Card(p.body, self, "Card sẽ khởi động lại")
        card.pack(fill="both", expand=True)
        self.adapter_picker = OrderedPicker(card, self, "Card trên máy", "Sẽ khởi động lại", self.mark_dirty)
        self.adapter_picker.pack(fill="both", expand=True, padx=20, pady=(4, 16))
        self.gap(p.body)
        self.adapter_card = Card(p.body, self, "Trạng thái card mạng")
        self.adapter_card.pack(fill="x")
        self.adapter_rows = tk.Frame(self.adapter_card, bg=self.c["surface"])
        self.adapter_rows.pack(fill="x", padx=20, pady=(0, 8))
        tb = tk.Frame(self.adapter_card, bg=self.c["surface"])
        tb.pack(fill="x", padx=20, pady=(4, 16))
        ttk.Button(tb, text="Làm mới danh sách", command=self.refresh_lists).pack(side="left")
        return p

    def page_reboot(self):
        c = self.c
        p = Page(self.pages_box, self, "Khởi động lại & Ứng dụng",
                 "Bước cuối khi không khôi phục được mạng, và các ứng dụng cần mở lại sau đó.")
        r = self.cfg["recovery"]
        card = Card(p.body, self, "Khởi động lại máy")
        card.pack(fill="x")
        self.toggle(card, "Cho phép khởi động lại", "Khi đã thử hết Wi-Fi và card mạng mà vẫn mất mạng.",
                    "reboot_on", r.get("reboot_enabled", True))
        self.number(card, "Đếm ngược", "Hủy bằng shutdown /a hoặc lệnh /cancel trên Telegram.", "reboot_delay",
                    r.get("reboot_delay_sec", 60), "giây")
        self.number(card, "Tối đa mỗi 24 giờ", "Tránh vòng lặp reboot khi nhà mạng sập lâu.", "max_reboots",
                    r.get("max_reboots_per_day", 3), "lần")
        self.number(card, "Thời gian chạy tối thiểu", "Máy vừa bật chưa đủ thời gian này thì không reboot.",
                    "min_uptime", r.get("min_uptime_before_reboot_min", 15), "phút")

        self.gap(p.body)
        card = Card(p.body, self, "Ứng dụng tự mở",
                    "Để ứng dụng tự mở sau khi khởi động lại, Windows cần TỰ ĐĂNG NHẬP (xem README).")
        card.pack(fill="x")
        self.launch_mode = tk.StringVar(value=self.cfg.get("launch_apps", "after_reboot"))
        Segmented(card.add_row("Khi nào mở"), self, self.launch_mode,
                  (("after_reboot", "Sau khi tự reboot"), ("every_start", "Mỗi lần mở máy"), ("never", "Không"))).pack()
        tk.Frame(card, bg=c["border"], height=1).pack(fill="x", padx=20)
        box = tk.Frame(card, bg=c["surface"], highlightthickness=1, highlightbackground=c["border"])
        box.pack(fill="x", padx=20, pady=(14, 0))
        self.apps_tree = ttk.Treeview(box, columns=("path", "args"), show="headings", height=6)
        self.apps_tree.heading("path", text="ỨNG DỤNG", anchor="w")
        self.apps_tree.heading("args", text="THAM SỐ", anchor="w")
        self.apps_tree.column("path", width=self.S(480))
        self.apps_tree.column("args", width=self.S(180))
        self.apps_tree.pack(fill="x")
        self.apps_tree.bind("<Double-Button-1>", lambda e: self.edit_args())
        for app in self.cfg.get("apps") or []:
            self.apps_tree.insert("", tk.END, values=(app.get("path", ""), app.get("args", "")))
        self.apps_empty = tk.Label(box, text="Chưa có ứng dụng nào — bấm “Thêm ứng dụng…”.", font=self.f_small,
                                   bg=c["surface"], fg=c["muted"])
        tb = tk.Frame(card, bg=c["surface"])
        tb.pack(fill="x", padx=20, pady=14)
        ttk.Button(tb, text="Thêm ứng dụng…", style="Accent.TButton", command=self.add_app).pack(side="left")
        ttk.Button(tb, text="Sửa tham số", command=self.edit_args).pack(side="left", padx=8)
        ttk.Button(tb, text="Xóa", command=self.del_apps).pack(side="left")
        self._apps_changed(user=False)
        return p

    def page_check(self):
        p = Page(self.pages_box, self, "Kiểm tra mạng", "Cách NetWatchdog xác định mất mạng và nhịp khôi phục.")
        ch, r = self.cfg["check"], self.cfg["recovery"]
        card = Card(p.body, self, "Đích kiểm tra")
        card.pack(fill="x")
        self.entry(card.add_row("Máy chủ TCP", "host:port, cách nhau dấu phẩy. Có mạng khi kết nối được 1 đích."),
                   "targets", ", ".join(ch.get("targets", [])), 40)
        self.entry(card.add_row("URL HTTP dự phòng", "Dùng khi mọi đích TCP đều lỗi."), "http_url",
                   ch.get("http_url", ""), 40)
        self.gap(p.body)
        card = Card(p.body, self, "Nhịp kiểm tra & khôi phục")
        card.pack(fill="x")
        self.number(card, "Chu kỳ kiểm tra", None, "interval", ch.get("interval_sec", 30), "giây")
        self.number(card, "Ngưỡng mất mạng", "Số lần lỗi liên tiếp mới coi là mất mạng.", "threshold",
                    ch.get("fail_threshold", 3), "lần")
        self.number(card, "Chờ sau mỗi thao tác", "Thời gian chờ mạng lên sau khi đổi Wi-Fi / bật card.",
                    "wait_after", r.get("wait_after_action_sec", 25), "giây")
        self.number(card, "Số vòng thử", "Số lần lặp Wi-Fi + card mạng trước khi reboot.", "rounds",
                    r.get("rounds", 2), "vòng")
        self.number(card, "Nghỉ giữa các lần thất bại", "Khi khôi phục thất bại mà không reboot.", "cooldown",
                    r.get("retry_cooldown_min", 10), "phút")
        return p

    def page_jxtd(self):
        c = self.c
        p = Page(self.pages_box, self, "jxtdAuto",
                 "Theo dõi nhân vật trong jxtdAuto, báo cáo và cảnh báo qua Telegram.")
        J = self.cfg["jxtd"]
        card = Card(p.body, self, "Theo dõi",
                    "Chỉ ĐỌC bảng nhân vật trong cửa sổ jxtdAuto qua lớp trợ năng của Windows: "
                    "không bấm, không gõ, không đọc bộ nhớ của jxtdAuto hay game.")
        card.pack(fill="x")
        self.toggle(card, "Bật theo dõi jxtdAuto", "Đọc bảng nhân vật mỗi phút và lưu lịch sử CSV theo ngày.",
                    "jx_on", J.get("enabled"))
        self.number(card, "Báo cáo định kỳ", "Gửi bảng nhân vật theo chu kỳ. 0 = tắt.", "jx_report",
                    J.get("report_min", 60), "phút")
        self.number(card, "Giãn cách cảnh báo", "Mỗi loại cảnh báo của một nhân vật gửi tối đa 1 lần trong khoảng này.",
                    "jx_cooldown", J.get("cooldown_min", 30), "phút")

        self.gap(p.body)
        card = Card(p.body, self, "Cảnh báo ngay khi",
                    "Luôn báo khi jxtdAuto bị tắt / không thấy cửa sổ. Hết lỗi sẽ có tin “🟢 trở lại”.")
        card.pack(fill="x")
        self.toggle(card, "Nhân vật biến khỏi danh sách", None, "jx_missing", J.get("char_missing", True))
        self.toggle(card, "Nhân vật bị bỏ tick", None, "jx_untick", J.get("unticked", True))
        self.toggle(card, "Thu nhập chuyển sang âm", None, "jx_neg", J.get("negative_income", True))
        self.toggle(card, "Nhân vật đứng chơi / gặp lỗi",
                    "jxtdAuto báo Lỗi (đầy hành trang…) hoặc Nghỉ do về thành liên tục. Đổi nhiệm vụ thì không báo.",
                    "jx_stuck", J.get("stuck", True))
        self.number(card, "Không làm gì quá", "Mất kết nối, Treo, không có tác vụ… kéo dài quá mức này mới báo.",
                    "jx_idle", J.get("idle_min", 5), "phút")
        self.toggle(card, "Nhân vật chết", "Số sau dấu “/” ở cột Phù/chết tăng.", "jx_death", J.get("death", True))
        self.toggle(card, "Thẻ tháng sắp hết / đã hết", None, "jx_card", J.get("month_card", True))
        self.number(card, "Ngưỡng thẻ tháng", "Báo “sắp hết” khi số giờ còn lại ≤ ngưỡng này.", "jx_card_h",
                    J.get("month_card_warn_hours", 24), "giờ")
        self.number(card, "Ngưỡng license jxtdAuto", "Theo số ngày “Còn lại” trên tiêu đề cửa sổ.", "jx_lic",
                    J.get("license_warn_days", 7), "ngày")

        self.gap(p.body)
        card = Card(p.body, self, "Đọc thử", f"Lịch sử lưu tại {JX_HISTORY_DIR}")
        card.pack(fill="x")
        box = tk.Frame(card, bg=c["surface"], highlightthickness=1, highlightbackground=c["border"])
        box.pack(fill="x", padx=20, pady=(6, 0))
        cols = (("name", "NHÂN VẬT", 125), ("task", "TÁC VỤ", 140), ("exp", "EXP/GIỜ", 65),
                ("income", "THU NHẬP", 85), ("money", "NGÂN LƯỢNG", 85), ("level", "CẤP/EXP", 95),
                ("deaths", "PHÙ/CHẾT", 60), ("card", "THẺ THÁNG", 90))
        self.jx_tree = ttk.Treeview(box, columns=[k for k, _, _ in cols], show="headings", height=5)
        for k, title, w in cols:
            self.jx_tree.heading(k, text=title, anchor="w")
            self.jx_tree.column(k, width=self.S(w), stretch=k == "task")
        self.jx_tree.pack(fill="x")
        tb = tk.Frame(card, bg=c["surface"])
        tb.pack(fill="x", padx=20, pady=14)
        self.jx_btn = ttk.Button(tb, text="Đọc ngay", style="Accent.TButton", command=self.jx_read)
        self.jx_btn.pack(side="left")
        self.jx_info = tk.Label(tb, text="", font=self.f_small, bg=c["surface"], fg=c["muted"])
        self.jx_info.pack(side="left", padx=12)
        return p

    def jx_read(self):
        self.jx_btn.state(["disabled"])
        self.jx_info.config(text="Đang đọc…")
        J = load_config(CONFIG_PATH)["jxtd"]

        def work():
            jxtd.com_init()
            snap = jxtd.read(J)
            self.after(0, lambda: self._jx_fill(snap))
        threading.Thread(target=work, daemon=True).start()

    def _jx_fill(self, snap):
        self.jx_btn.state(["!disabled"])
        self.jx_tree.delete(*self.jx_tree.get_children())
        if not snap.found:
            self.jx_info.config(text="Không thấy cửa sổ jxtdAuto" + (f" ({snap.error})" if snap.error else ""),
                                fg=self.c["danger"])
            return
        for r in snap.rows:
            self.jx_tree.insert("", tk.END, values=(("✓ " if r.checked else "✗ ") + r.name, r.task, r.exp_text,
                                                    r.income, r.money, r.level, r.deaths, r.card))
        lic = f" · license còn {snap.license_days} ngày" if snap.license_days is not None else ""
        self.jx_info.config(text=f"{len(snap.rows)} nhân vật lúc {time.strftime('%H:%M:%S')}{lic}",
                            fg=self.c["muted"])

    # ------------------------------------------------ thống kê jxtdAuto
    def page_stats(self):
        c = self.c
        p = Page(self.pages_box, self, "Thống kê jxtdAuto",
                 "EXP và tiền vạn các nhân vật kiếm được theo giờ hoặc theo ngày, tính từ lịch sử jxtdAuto.")
        self.st_records, self.st_names, self.st_sel = [], [], None
        self.st_loading = self.st_reload = False
        self.st_by = tk.StringVar(value="hour")
        self.st_range = tk.StringVar(value="7")
        self.st_day = tk.StringVar()
        self.st_days = {}

        flt = Card(p.body, self)
        flt.pack(fill="x")
        row = tk.Frame(flt, bg=c["surface"])
        row.pack(fill="x", padx=20, pady=(16, 10))
        Segmented(row, self, self.st_by, (("hour", "Theo giờ"), ("day", "Theo ngày"))).pack(side="left")
        self.st_opts = tk.Frame(row, bg=c["surface"])
        self.st_opts.pack(side="left", padx=16)
        self.st_day_box = ttk.Combobox(self.st_opts, textvariable=self.st_day, state="readonly", width=20)
        self.st_range_seg = Segmented(self.st_opts, self, self.st_range,
                                      (("7", "7 ngày"), ("14", "14 ngày"), ("30", "30 ngày")))
        self.st_btn = ttk.Button(row, text="Làm mới", command=self.stats_load)
        self.st_btn.pack(side="right")
        self.st_info = tk.Label(row, text="", font=self.f_small, bg=c["surface"], fg=c["muted"])
        self.st_info.pack(side="right", padx=12)
        tk.Frame(flt, bg=c["border"], height=1).pack(fill="x", padx=20)
        crow = tk.Frame(flt, bg=c["surface"])
        crow.pack(fill="x", padx=20, pady=(10, 4))
        tk.Label(crow, text="NHÂN VẬT", font=self.f_tiny, bg=c["surface"], fg=c["muted"]).pack(side="left")
        allb = tk.Label(crow, text="Chọn tất cả", font=self.f_small, bg=c["surface"], fg=c["accent"], cursor="hand2")
        allb.pack(side="left", padx=12)
        allb.bind("<Button-1>", lambda e: self._stats_pick(None))
        self.st_chips = tk.Frame(flt, bg=c["surface"])
        self.st_chips.pack(fill="x", padx=20, pady=(2, 6))
        tk.Label(flt, text="Bấm một tên để xem riêng nhân vật đó, bấm thêm tên khác để gộp nhiều nhân vật.",
                 font=self.f_small, bg=c["surface"], fg=c["muted"]).pack(anchor="w", padx=20, pady=(0, 14))

        self.gap(p.body)
        grid = tk.Frame(p.body, bg=c["bg"])
        grid.pack(fill="x")
        self.st_tiles = {}
        for i, (key, title) in enumerate((("exp", "EXP kiếm được"), ("van", "Tiền kiếm được"),
                                          ("exp_h", "EXP trung bình / giờ"), ("van_h", "Tiền trung bình / giờ"))):
            t = Tile(grid, self, title)
            t.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 8, 0))
            grid.columnconfigure(i, weight=1, uniform="st")
            self.st_tiles[key] = t

        self.st_charts = {}
        for key, title, fmt in (("exp", "EXP kiếm được", jxtd.fmt_exp),
                                ("van", "Tiền kiếm được (vạn)", lambda v: jxtd.fmt_van(v, sign=False))):
            self.gap(p.body)
            card = Card(p.body, self, title)
            card.pack(fill="x")
            ch = BarChart(card, self, fmt)
            ch.pack(fill="x", padx=20, pady=(4, 16))
            self.st_charts[key] = (card, ch)

        self.gap(p.body)
        card = Card(p.body, self, "Chi tiết theo thời gian")
        card.pack(fill="x")
        self.st_tree = self._stats_tree(card, (("t", "THỜI GIAN", 150), ("exp", "EXP", 110), ("van", "TIỀN (VẠN)", 110),
                                               ("n", "SỐ NHÂN VẬT", 110)))
        self.gap(p.body)
        card = Card(p.body, self, "Theo nhân vật")
        card.pack(fill="x")
        self.st_char_tree = self._stats_tree(card, (
            ("name", "NHÂN VẬT", 130), ("exp", "EXP", 85), ("van", "TIỀN (VẠN)", 85), ("lv", "CẤP", 190),
            ("hours", "GIỜ CHẠY", 80), ("money", "NGÂN LƯỢNG", 105), ("task", "TÁC VỤ CUỐI", 120)))
        self.note(p.body, "EXP = chênh lệch cột “EXP tích lũy” giữa các lần đọc; dữ liệu cũ chưa có cột đó thì ước tính bằng EXP/giờ × thời gian. "
                          "Tiền = chênh lệch Ngân lượng, nên tiêu hoặc chuyển tiền cũng bị trừ vào.")

        self.st_by.trace_add("write", lambda *a: self._stats_mode())
        self.st_range.trace_add("write", lambda *a: self.stats_load())
        self.st_day_box.bind("<<ComboboxSelected>>", lambda e: self.stats_load())
        self._stats_mode(load=False)
        return p

    def _stats_tree(self, card, cols):
        box = tk.Frame(card, bg=self.c["surface"], highlightthickness=1, highlightbackground=self.c["border"])
        box.pack(fill="x", padx=20, pady=(6, 16))
        tree = ttk.Treeview(box, columns=[k for k, _, _ in cols], show="headings", height=3)
        for k, title, w in cols:
            num = k in ("exp", "van", "n", "hours", "money")
            tree.heading(k, text=title, anchor="e" if num else "w")
            tree.column(k, width=self.S(w), anchor="e" if num else "w", stretch=k in ("t", "task", "name"))
        tree.pack(fill="x")
        return tree

    def _stats_mode(self, load=True):
        hour = self.st_by.get() == "hour"
        (self.st_range_seg.pack_forget if hour else self.st_day_box.pack_forget)()
        (self.st_day_box if hour else self.st_range_seg).pack(side="left")
        unit = "giờ" if hour else "ngày"
        self.st_charts["exp"][0].winfo_children()[0].config(text=f"EXP kiếm được theo {unit}")
        self.st_charts["van"][0].winfo_children()[0].config(text=f"Tiền kiếm được theo {unit} (vạn)")
        if load:
            self.stats_load()

    def _stats_fill_days(self):
        today = datetime.now().date()
        days = jxtd.history_days(JX_HISTORY_DIR)
        if today not in days:
            days.insert(0, today)
        self.st_days = {(f"{d:%d/%m/%Y}" + (" (hôm nay)" if d == today else "")): d for d in days}
        self.st_day_box.config(values=list(self.st_days))
        if self.st_day.get() not in self.st_days:
            self.st_day.set(next(iter(self.st_days)))

    def _stats_period(self):
        if self.st_by.get() == "hour":
            return jxtd.stats_range("hour", self.st_days.get(self.st_day.get()))
        return jxtd.stats_range("day", days=int(self.st_range.get()))

    def stats_load(self):
        if self.st_loading:  # đang đọc dở: đọc lại khi xong (đã đổi ngày/chế độ)
            self.st_reload = True
            return
        self.st_reload = False
        self._stats_fill_days()
        self.st_loading = True
        self.st_btn.state(["disabled"])
        self.st_info.config(text="Đang đọc lịch sử…")
        start, end = self._stats_period()

        def work():
            try:
                recs, err = jxtd.load_history(JX_HISTORY_DIR, start, end), ""
            except Exception as e:  # noqa: BLE001
                recs, err = [], str(e)
            self.after(0, lambda: self._stats_loaded(recs, err))
        threading.Thread(target=work, daemon=True).start()

    def _stats_loaded(self, recs, err):
        self.st_loading = False
        self.st_btn.state(["!disabled"])
        if self.st_reload:
            self.stats_load()
            return
        self.st_records = recs
        names = sorted({r["nhan_vat"] for r in recs if not r.get("seed")})
        if names != self.st_names:
            self.st_names = names
            if self.st_sel is not None:
                self.st_sel &= set(names)
                if not self.st_sel:
                    self.st_sel = None
            self._stats_chips()
        if err:
            self.st_info.config(text=f"Lỗi đọc lịch sử: {err}", fg=self.c["danger"])
        elif not recs:
            self.st_info.config(text="Chưa có dữ liệu — bật theo dõi ở trang jxtdAuto", fg=self.c["muted"])
        else:
            self.st_info.config(text=f"Cập nhật lúc {time.strftime('%H:%M:%S')}", fg=self.c["muted"])
        self.stats_render()

    def _stats_chips(self):
        c = self.c
        for w in self.st_chips.winfo_children():
            w.destroy()
        if not self.st_names:
            tk.Label(self.st_chips, text="(chưa có nhân vật nào trong khoảng thời gian này)", font=self.f_small,
                     bg=c["surface"], fg=c["muted"]).grid(row=0, column=0, sticky="w")
            return
        per_row = 6
        for i, n in enumerate(self.st_names):
            on = self.st_sel is None or n in self.st_sel
            lb = tk.Label(self.st_chips, text=("✓ " if on else "") + n, padx=12, pady=4, cursor="hand2",
                          font=self.f_bold if on else self.f_body,
                          bg=c["accent_soft"] if on else c["seg"], fg=c["accent"] if on else c["muted"])
            lb.grid(row=i // per_row, column=i % per_row, sticky="w", padx=(0, 6), pady=3)
            lb.bind("<Button-1>", lambda e, n=n: self._stats_pick(n))
        self.pages["stats"].relayout()

    def _stats_pick(self, name):
        """None = tất cả; đang xem tất cả mà bấm một tên = xem riêng tên đó; sau đó bấm để thêm/bớt."""
        if name is None:
            self.st_sel = None
        elif self.st_sel is None:
            self.st_sel = {name}
        else:
            self.st_sel ^= {name}
            if not self.st_sel or self.st_sel == set(self.st_names):
                self.st_sel = None
        self._stats_chips()
        self.stats_render()

    def stats_render(self):
        by = self.st_by.get()
        start, end = self._stats_period()
        poll = self.cfg.get("jxtd", {}).get("poll_sec", 60)
        names = None if self.st_sel is None else sorted(self.st_sel)
        s = jxtd.compute_stats(self.st_records, names, by, start, end, poll)
        n_chars = len(s["chars"])
        hours = s["active_hours"]
        who = "tất cả nhân vật" if names is None else (names[0] if len(names) == 1 else f"{len(names)} nhân vật")
        period = f"{start:%d/%m}" if by == "hour" else f"{start:%d/%m} → {end:%d/%m}"
        t = self.st_tiles
        if s["exp_hours"] or not n_chars:
            t["exp"].set(jxtd.fmt_exp(s["total_exp"]), period)
        else:  # có chạy nhưng không có số EXP nào được lưu: không phải "kiếm được 0"
            t["exp"].set("—", f"{period} · chưa có số EXP")
        t["van"].set(jxtd.fmt_van(s["total_van"]) + " vạn", who if n_chars else "không có dữ liệu")
        exp_hours = s["exp_hours"]  # giờ có số EXP (jxtdAuto đổi cột có thể làm thiếu số EXP một thời gian)
        t["exp_h"].set(jxtd.fmt_exp(s["total_exp"] / exp_hours) if exp_hours else "—",
                       f"trên {exp_hours} giờ có số EXP")
        t["van_h"].set((jxtd.fmt_van(s["total_van"] / hours) + " vạn") if hours else "—", f"trên {hours} giờ có chạy")

        bk = s["buckets"]
        if by == "hour":
            labels = [f"{k:%H}h" for k, _ in bk]
            tips = [f"{k:%H}:00–{k:%H}:59" for k, _ in bk]
        else:
            labels = [f"{k:%d/%m}" for k, _ in bk]
            tips = [f"Ngày {k:%d/%m/%Y}" for k, _ in bk]
        self.st_charts["exp"][1].set(labels, [b["exp"] for _, b in bk], tips)
        self.st_charts["van"][1].set(labels, [b["van"] for _, b in bk], tips)

        tree = self.st_tree
        tree.delete(*tree.get_children())
        for (k, b), tip in zip(bk, tips):
            if b["chars"]:
                tree.insert("", tk.END, values=(tip, jxtd.fmt_exp(b["exp"]), jxtd.fmt_van(b["van"]), len(b["chars"])))
        if tree.get_children():
            tree.insert("", tk.END, values=("Tổng", jxtd.fmt_exp(s["total_exp"]), jxtd.fmt_van(s["total_van"]), n_chars))
        tree.configure(height=max(1, len(tree.get_children())))

        tree = self.st_char_tree
        tree.delete(*tree.get_children())
        for n, ch in sorted(s["chars"].items(), key=lambda kv: -kv[1]["exp"]):
            lv = jxtd.level_change(ch["level_first"], ch["level_last"]) or ch["level_last"]
            money = ch.get("van_last")
            tree.insert("", tk.END, values=(("✓ " if ch["tick"] else "✗ ") + n, jxtd.fmt_exp(ch["exp"]),
                                            jxtd.fmt_van(ch["van"]), lv, f"{ch['seconds'] / 3600:.1f}",
                                            "" if money is None else jxtd.fmt_van(money, sign=False),
                                            ch["task"]))
        tree.configure(height=max(1, len(tree.get_children())))
        self.pages["stats"].relayout()

    # ------------------------------------------------ tổng quan
    def _paint_hero(self, online):
        c = self.c
        color = c["muted"] if online is None else (c["success"] if online else c["danger"])
        soft = c["seg"] if online is None else (c["success_soft"] if online else c["danger_soft"])
        cv, size = self.hero_icon, self.S(56)
        cv.delete("all")
        cv.create_oval(0, 0, size - 1, size - 1, fill=soft, outline="")
        if self.f_icon_big:
            cv.create_text(size / 2, size / 2 + 1, text=ICONS["wifi"], font=self.f_icon_big, fill=color)
        else:
            cv.create_oval(size * .35, size * .35, size * .65, size * .65, fill=color, outline="")

    def _paint_service(self, running):
        c = self.c
        color = c["success"] if running else c["danger"]
        self.svc_dot.delete("all")
        self.svc_dot.create_oval(1, 1, 9, 9, fill=color, outline="")
        self.svc_lbl.config(text="Đang chạy nền" if running else "Chưa chạy nền")
        if hasattr(self, "pill"):
            self.pill.config(text="● ĐANG CHẠY NỀN" if running else "● CHƯA CHẠY NỀN", fg=color,
                             bg=c["success_soft"] if running else c["danger_soft"])
            if not getattr(self, "svc_busy", False):
                self.svc_btn.config(text="Tắt chạy nền" if running else "Bật chạy nền")

    def refresh_status(self):
        if self.busy_status:
            return
        self.busy_status = True
        self.refresh_btn.state(["disabled"])
        if self.hero_title.cget("text") == "Đang kiểm tra…":
            self._paint_hero(None)

        def work():
            try:
                d = collect_status()
            except Exception as e:  # noqa: BLE001
                d = {"error": str(e)}
            self.after(0, lambda: self._fill_status(d))
        threading.Thread(target=work, daemon=True).start()

    def _fill_status(self, d):
        self.busy_status = False
        self.refresh_btn.state(["!disabled"])
        if "error" in d:
            self.hero_title.config(text="Không đọc được tình trạng")
            self.hero_sub.config(text=d["error"])
            return
        online = d["online"]
        self._paint_hero(online)
        self._paint_service(d["running"])
        self.hero_title.config(text="Đang có Internet" if online else "Mất kết nối Internet")
        sub = [f"Wi-Fi: {d['ssid']}" if d["ssid"] else "Không dùng Wi-Fi",
               f"Kiểm tra qua {d['detail']}" if online else d["detail"]]
        if d["last_outage"]:
            sub.append(f"Lần mất mạng gần nhất: {d['last_outage']}")
        self.hero_sub.config(text="  ·  ".join(sub[:2]) + ("\n" + sub[2] if len(sub) > 2 else ""))
        pct = lambda v: "—" if v is None else f"{v:.0f}%"  # noqa: E731
        t = self.tiles
        t["uptime"].set(d["uptime"], "Kể từ lần mở máy gần nhất")
        t["cpu"].set(pct(d["cpu"]), "Mức sử dụng", d["cpu"])
        t["ram"].set(pct(d["ram"]), d["ram_sub"], d["ram"])
        t["disk"].set(pct(d["disk"]), d["disk_sub"], d["disk"])
        t["lan"].set(d["lan"] or "—", "Địa chỉ trong mạng LAN")
        t["wan"].set(d["wan"] or "—", "Địa chỉ ra Internet" if d["wan"] else "Không lấy được / đã tắt")
        t["bat"].set(pct(d["bat"]), d["bat_sub"] or "Máy không có pin", d["bat"])
        t["reboots"].set(str(d["reboots"]), f"Giới hạn {d['max_reboots']} lần / 24 giờ")
        t["outbox"].set(str(d["outbox"]), "Sẽ gửi bù khi có mạng" if d["outbox"] else "Không có tin tồn")
        self.updated_lbl.config(text=f"Cập nhật lúc {time.strftime('%H:%M:%S')} · tự làm mới mỗi 30 giây")
        self.pages["home"].relayout()

    def _tick(self):
        self._tick_n = getattr(self, "_tick_n", 0) + 1
        if self._tick_n % 30 == 0 and self.current == "home":
            self.refresh_status()
        elif self._tick_n % 60 == 0 and self.current == "stats" and self._stats_period()[1] == datetime.now().date():
            self.stats_load()
        elif self._tick_n % 10 == 0:
            self._paint_service(service_running())
        self.after(1000, self._tick)

    def toggle_service(self):
        if getattr(self, "svc_busy", False):
            return
        running = service_running()
        self.svc_busy = True
        self.svc_btn.state(["disabled"])
        self.svc_btn.config(text="Đang tắt…" if running else "Đang bật…")

        def work():
            if running:
                ok, out = sysops.stop_task()
                ok = not service_running()
            else:
                ok, out = True, ""
                if not sysops.task_exists():
                    import app
                    ok, out = sysops.install_task(*app.service_command())
                if ok:
                    ok, out = sysops.start_task()
                for _ in range(20):  # chờ tiến trình nền chiếm cổng khóa
                    if not ok or service_running():
                        break
                    time.sleep(0.5)
            self.after(0, lambda: self._service_done(running, ok, out))
        threading.Thread(target=work, daemon=True).start()

    def _service_done(self, was_running, ok, out):
        self.svc_busy = False
        self.svc_btn.state(["!disabled"])
        self._paint_service(service_running())
        if not ok:
            messagebox.showerror("NetWatchdog", f"Không {'tắt' if was_running else 'bật'} được chạy nền "
                                 f"(cần quyền Administrator).\n\n{out}")
        else:
            self.flash("✓  Đã tắt chạy nền — sẽ tự chạy lại ở lần đăng nhập sau" if was_running
                       else "✓  NetWatchdog đã chạy nền")

    # ------------------------------------------------ cập nhật
    def check_update(self, manual=False):
        def work():
            try:
                rel, err = updater.latest_release(), None
            except Exception as e:  # noqa: BLE001
                rel, err = None, str(e)
            self.after(0, lambda: self._update_checked(rel, err, manual))
        threading.Thread(target=work, daemon=True).start()

    def _update_checked(self, rel, err, manual):
        if rel and updater.is_newer(rel["version"]):
            self.release = rel
            self.update_title.config(text=f"Có phiên bản mới {rel['version']}")
            self.update_sub.config(text=f"Bạn đang dùng {__version__}. Cập nhật mất khoảng 1 phút, giữ nguyên cấu hình.")
            self.update_btn.config(text="Cập nhật ngay" if FROZEN else "Xem trên GitHub")
            self.update_banner.pack(fill="x", pady=(0, 16), before=self.hero_card)
            if manual:
                self.show("home")
            self.pages["home"].relayout()
        elif manual:
            if err:
                messagebox.showerror("Cập nhật", f"Không kiểm tra được bản mới:\n{err}")
            else:
                messagebox.showinfo("Cập nhật", f"Bạn đang dùng bản mới nhất ({__version__}).")

    def install_update(self):
        rel = getattr(self, "release", None)
        if not rel:
            return
        if not FROZEN:
            os.startfile(rel["page"])
            return
        if self.dirty and not messagebox.askyesno("Cập nhật", "Có thay đổi chưa lưu sẽ bị bỏ. Vẫn cập nhật?"):
            return
        self.update_btn.state(["disabled"])

        def progress(done, total):
            pct = f"{done * 100 // total}%" if total else f"{done // 2**20} MB"
            self.after(0, lambda: self.update_sub.config(text=f"Đang tải bản cài đặt… {pct}"))

        def work():
            try:
                path = updater.download(rel["url"], progress)
            except Exception as e:  # noqa: BLE001
                msg = str(e)
                self.after(0, lambda: (self.update_btn.state(["!disabled"]),
                                       messagebox.showerror("Cập nhật", f"Tải bản cài đặt lỗi:\n{msg}")))
                return
            self.after(0, lambda: self._launch_installer(path))
        threading.Thread(target=work, daemon=True).start()

    def _launch_installer(self, path):
        self.update_sub.config(text="Đang cài đặt, NetWatchdog sẽ tự mở lại…")
        try:
            updater.run_installer(path)
        except OSError as e:
            self.update_btn.state(["!disabled"])
            messagebox.showerror("Cập nhật", f"Không chạy được bộ cài:\n{e}")
            return
        self.after(800, self.destroy)

    def open_log(self):
        path = LOG_PATH if os.path.exists(LOG_PATH) else os.path.dirname(LOG_PATH)
        try:
            os.startfile(path if os.path.exists(path) else os.path.dirname(CONFIG_PATH))
        except OSError as e:
            messagebox.showerror("Log", f"Không mở được: {e}")

    # ------------------------------------------------ danh sách
    def refresh_lists(self):
        self.config(cursor="watch")

        def work():
            profiles = sysops.list_wifi_profiles()
            adapters = sysops.list_adapters()
            self.after(0, lambda: self._fill_lists(profiles, adapters))
        threading.Thread(target=work, daemon=True).start()

    def _fill_lists(self, profiles, adapters):
        c = self.c
        r = self.cfg["recovery"]
        loaded = getattr(self, "_lists_loaded", False)
        sel_w = self.wifi_picker.get() if loaded else list(r.get("wifi_profiles") or [])
        sel_a = self.adapter_picker.get() if loaded else list(r.get("adapters") or [])
        self._lists_loaded = True
        self.wifi_picker.set_items(profiles, sel_w)
        self.adapter_picker.set_items([a["name"] for a in adapters], sel_a)
        for w in self.adapter_rows.winfo_children():
            w.destroy()
        if not adapters:
            self.note(self.adapter_rows, "Không đọc được danh sách card mạng.", c["surface"])
        for i, a in enumerate(adapters):
            if i:
                tk.Frame(self.adapter_rows, bg=c["border"], height=1).pack(fill="x")
            row = tk.Frame(self.adapter_rows, bg=c["surface"])
            row.pack(fill="x", pady=8)
            up = a["status"].lower() == "up"
            color = c["success"] if up else (c["danger"] if "disabled" in a["status"].lower() else c["muted"])
            dot = tk.Canvas(row, width=10, height=10, bg=c["surface"], highlightthickness=0)
            dot.create_oval(1, 1, 9, 9, fill=color, outline="")
            dot.pack(side="left", padx=(0, 12))
            txt = tk.Frame(row, bg=c["surface"])
            txt.pack(side="left", fill="x", expand=True)
            tk.Label(txt, text=a["name"], font=self.f_bold, bg=c["surface"], fg=c["text"]).pack(anchor="w")
            tk.Label(txt, text=a["description"], font=self.f_small, bg=c["surface"], fg=c["muted"]).pack(anchor="w")
            tk.Label(row, text=a["status"], font=self.f_tiny, bg=c["seg"], fg=color, padx=10, pady=3).pack(side="right")
        self.config(cursor="")
        self.pages[self.current].relayout()

    # ------------------------------------------------ ứng dụng
    def _apps_changed(self, user=True):
        if self.apps_tree.get_children():
            self.apps_empty.place_forget()
        else:
            self.apps_empty.place(relx=0.5, rely=0.55, anchor="center")
        if user:
            self.mark_dirty()

    def add_app(self):
        path = filedialog.askopenfilename(title="Chọn ứng dụng", filetypes=[
            ("Ứng dụng", "*.exe *.lnk *.bat *.cmd"), ("Tất cả", "*.*")])
        if path:
            self.apps_tree.insert("", tk.END, values=(os.path.normpath(path), ""))
            self._apps_changed()

    def del_apps(self):
        for i in self.apps_tree.selection():
            self.apps_tree.delete(i)
        self._apps_changed()

    def edit_args(self):
        sel = self.apps_tree.selection()
        if not sel:
            return
        c = self.c
        path, args = self.apps_tree.item(sel[0], "values")
        win = tk.Toplevel(self, bg=c["surface"], padx=24, pady=20)
        win.title("Tham số dòng lệnh")
        win.transient(self)
        win.resizable(False, False)
        tk.Label(win, text=os.path.basename(path), font=self.f_h2, bg=c["surface"], fg=c["text"]).pack(anchor="w")
        tk.Label(win, text=path, font=self.f_small, bg=c["surface"], fg=c["muted"]).pack(anchor="w", pady=(0, 12))
        v = tk.StringVar(value=args)
        e = ttk.Entry(win, textvariable=v, width=60)
        e.pack(fill="x")
        e.focus()

        def ok(_=None):
            self.apps_tree.item(sel[0], values=(path, v.get()))
            self.mark_dirty()
            win.destroy()
        b = tk.Frame(win, bg=c["surface"])
        b.pack(fill="x", pady=(16, 0))
        ttk.Button(b, text="OK", style="Accent.TButton", command=ok).pack(side="right")
        ttk.Button(b, text="Hủy", command=win.destroy).pack(side="right", padx=8)
        win.bind("<Return>", ok)
        win.bind("<Escape>", lambda _: win.destroy())
        win.grab_set()

    # ------------------------------------------------ Telegram
    def _telegram(self):
        cfg = {"telegram": {"bot_token": self.vars["tg_token"].get().strip(),
                            "chat_id": self.vars["tg_chat"].get().strip()}}
        return Telegram(lambda: cfg, State(STATE_PATH))

    def fetch_chat_id(self):
        try:
            ups = self._telegram().call("getUpdates", {"timeout": 0})
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("Telegram", f"Lỗi: {e}\nKiểm tra lại bot token.")
            return
        chats = {}
        for u in ups or []:
            chat = (u.get("message") or {}).get("chat") or {}
            if chat.get("id"):
                chats[chat["id"]] = chat.get("title") or chat.get("username") or chat.get("first_name") or ""
        if not chats:
            messagebox.showinfo("Telegram", "Chưa thấy tin nhắn nào. Hãy mở bot, gửi /start rồi bấm lại.")
            return
        cid, name = list(chats.items())[-1]
        self.vars["tg_chat"].set(str(cid))
        self.flash(f"✓  Đã lấy Chat ID {cid} ({name})")

    def test_telegram(self):
        try:
            tg = self._telegram()
            cfg = load_config(CONFIG_PATH)
            tg.send_now("✅ NetWatchdog kết nối Telegram thành công!\n\n" + status_report(cfg, tg.state))
            self.flash("✓  Đã gửi tin thử lên Telegram")
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("Telegram", f"Gửi lỗi: {e}")

    # ------------------------------------------------ lưu
    def flash(self, text):
        self.set_status_text(text, self.c["success"])
        self.after(4000, lambda: self.set_status_text() if not self.dirty else self.mark_dirty_text())

    def mark_dirty_text(self):
        self.set_status_text("●  Có thay đổi chưa lưu — Ctrl+S để lưu", self.c["accent"])

    def _int(self, key, default):
        try:
            return max(0, int(float(self.vars[key].get())))
        except ValueError:
            return default

    def save(self):
        c = self.cfg
        c["machine_name"] = self.vars["machine_name"].get().strip()
        c["telegram"].update(bot_token=self.vars["tg_token"].get().strip(),
                             chat_id=self.vars["tg_chat"].get().strip(),
                             commands=self.vars["tg_cmds"].get())
        c["report"]["heartbeat_min"] = self._int("heartbeat", 360)
        c["check"].update(
            targets=[t.strip() for t in self.vars["targets"].get().split(",") if t.strip()],
            http_url=self.vars["http_url"].get().strip(),
            interval_sec=max(5, self._int("interval", 30)),
            fail_threshold=max(1, self._int("threshold", 3)))
        c["recovery"].update(
            wifi_profiles=self.wifi_picker.get(),
            wifi_interface=self.vars["wifi_iface"].get().strip(),
            adapters=self.adapter_picker.get(),
            wait_after_action_sec=max(5, self._int("wait_after", 25)),
            rounds=max(1, self._int("rounds", 2)),
            retry_cooldown_min=max(1, self._int("cooldown", 10)),
            reboot_enabled=self.vars["reboot_on"].get(),
            reboot_delay_sec=max(10, self._int("reboot_delay", 60)),
            max_reboots_per_day=self._int("max_reboots", 3),
            min_uptime_before_reboot_min=self._int("min_uptime", 15))
        c["launch_apps"] = self.launch_mode.get()
        c["jxtd"].update(
            enabled=self.vars["jx_on"].get(),
            report_min=self._int("jx_report", 60),
            cooldown_min=max(1, self._int("jx_cooldown", 30)),
            char_missing=self.vars["jx_missing"].get(),
            unticked=self.vars["jx_untick"].get(),
            negative_income=self.vars["jx_neg"].get(),
            stuck=self.vars["jx_stuck"].get(),
            idle_min=max(1, self._int("jx_idle", 5)),
            death=self.vars["jx_death"].get(),
            month_card=self.vars["jx_card"].get(),
            month_card_warn_hours=self._int("jx_card_h", 24),
            license_warn_days=self._int("jx_lic", 7))
        old = {a.get("path"): a for a in c.get("apps") or []}
        c["apps"] = []
        for iid in self.apps_tree.get_children():
            path, args = self.apps_tree.item(iid, "values")
            app = dict(old.get(path) or {"skip_if_running": True})
            app.update(path=path, args=args)
            c["apps"].append(app)
        try:
            save_json(CONFIG_PATH, c)
        except OSError as e:
            messagebox.showerror("Lưu", f"Không lưu được: {e}")
            return False
        self.dirty = False
        self.flash(f"✓  Đã lưu lúc {time.strftime('%H:%M:%S')} — NetWatchdog sẽ tự áp dụng cấu hình mới")
        return True

    def close(self):
        if self.dirty:
            ans = messagebox.askyesnocancel("NetWatchdog", "Bạn có thay đổi chưa lưu. Lưu trước khi đóng?")
            if ans is None or (ans and not self.save()):
                return
        self.destroy()


def _dpi_aware():
    if sys.platform == "win32":
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass


def run():
    _dpi_aware()
    SettingsApp().mainloop()


if __name__ == "__main__":
    run()
