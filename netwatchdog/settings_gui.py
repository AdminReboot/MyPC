"""Cửa sổ Cài đặt NetWatchdog: chọn Wi-Fi, card mạng, ứng dụng và cấu hình Telegram.

Lưu vào config.json — NetWatchdog đang chạy sẽ tự nạp lại, không cần khởi động lại.
Chạy: pythonw settings_gui.py
"""
import os
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import sysops
from netwatchdog import CONFIG_PATH, STATE_PATH, State, Telegram, load_config, save_json, status_report


class OrderedPicker(ttk.Frame):
    """Hai danh sách: bên trái các mục có sẵn, bên phải các mục đã chọn (có thứ tự ưu tiên)."""

    def __init__(self, master, left_title, right_title):
        super().__init__(master)
        self.left = self._box(left_title, 0)
        self.right = self._box(right_title, 2)
        mid = ttk.Frame(self)
        mid.grid(row=1, column=1, padx=6)
        for txt, cmd in (("Thêm →", self.add), ("← Bỏ", self.remove), ("▲ Lên", lambda: self.move(-1)),
                         ("▼ Xuống", lambda: self.move(1))):
            ttk.Button(mid, text=txt, command=cmd, width=10).pack(pady=2)
        self.columnconfigure(0, weight=1)
        self.columnconfigure(2, weight=1)
        self.rowconfigure(1, weight=1)
        self.left.bind("<Double-Button-1>", lambda e: self.add())
        self.right.bind("<Double-Button-1>", lambda e: self.remove())

    def _box(self, title, col):
        ttk.Label(self, text=title).grid(row=0, column=col, sticky="w")
        lb = tk.Listbox(self, selectmode=tk.EXTENDED, height=10, exportselection=False)
        lb.grid(row=1, column=col, sticky="nsew")
        return lb

    def set_items(self, available, selected):
        self.left.delete(0, tk.END)
        self.right.delete(0, tk.END)
        for s in selected:
            self.right.insert(tk.END, s)
        for a in available:
            if a not in selected:
                self.left.insert(tk.END, a)

    def get(self):
        return list(self.right.get(0, tk.END))

    def add(self):
        for i in self.left.curselection()[::-1]:
            self.right.insert(tk.END, self.left.get(i))
            self.left.delete(i)

    def remove(self):
        for i in self.right.curselection()[::-1]:
            self.left.insert(tk.END, self.right.get(i))
            self.right.delete(i)

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


class SettingsApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("NetWatchdog — Cài đặt")
        self.geometry("760x560")
        self.minsize(680, 500)
        self.cfg = load_config(CONFIG_PATH)
        self.vars = {}

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=8, pady=8)
        nb.add(self.tab_telegram(nb), text="Telegram")
        nb.add(self.tab_wifi(nb), text="Wi-Fi dự phòng")
        nb.add(self.tab_adapters(nb), text="Card mạng")
        nb.add(self.tab_reboot(nb), text="Khởi động lại & Ứng dụng")
        nb.add(self.tab_check(nb), text="Kiểm tra mạng")

        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=8, pady=(0, 8))
        ttk.Button(bar, text="Lưu", command=self.save).pack(side="right")
        ttk.Button(bar, text="Đóng", command=self.destroy).pack(side="right", padx=6)
        ttk.Label(bar, text=f"File cấu hình: {CONFIG_PATH}", foreground="gray").pack(side="left")
        self.after(100, self.refresh_lists)

    # ------------------------------------------------ helpers
    def entry(self, parent, row, label, key, value, width=50, show=None):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=3)
        v = tk.StringVar(value=str(value))
        ttk.Entry(parent, textvariable=v, width=width, show=show).grid(row=row, column=1, sticky="we", pady=3)
        self.vars[key] = v
        return v

    def check(self, parent, row, label, key, value):
        v = tk.BooleanVar(value=bool(value))
        ttk.Checkbutton(parent, text=label, variable=v).grid(row=row, column=0, columnspan=2, sticky="w", pady=3)
        self.vars[key] = v
        return v

    def frame(self, nb):
        f = ttk.Frame(nb, padding=12)
        f.columnconfigure(1, weight=1)
        return f

    # ------------------------------------------------ tabs
    def tab_telegram(self, nb):
        f = self.frame(nb)
        t = self.cfg["telegram"]
        self.entry(f, 0, "Tên máy (hiển thị):", "machine_name", self.cfg.get("machine_name", ""))
        self.entry(f, 1, "Bot token:", "tg_token", t.get("bot_token", ""), show="•")
        self.entry(f, 2, "Chat ID:", "tg_chat", t.get("chat_id", ""))
        self.check(f, 3, "Nhận lệnh điều khiển từ Telegram (/status, /fix, /reboot yes…)", "tg_cmds",
                   t.get("commands", True))
        self.entry(f, 4, "Báo cáo định kỳ (phút, 0 = tắt):", "heartbeat", self.cfg["report"].get("heartbeat_min", 360), 10)
        b = ttk.Frame(f)
        b.grid(row=5, column=0, columnspan=2, sticky="w", pady=10)
        ttk.Button(b, text="Lấy Chat ID", command=self.fetch_chat_id).pack(side="left")
        ttk.Button(b, text="Gửi tin thử", command=self.test_telegram).pack(side="left", padx=6)
        ttk.Label(f, justify="left", foreground="gray", text=(
            "Cách lấy: chat với @BotFather → /newbot → copy token vào ô trên.\n"
            "Mở bot vừa tạo, gửi 1 tin bất kỳ (vd. /start), rồi bấm 'Lấy Chat ID'.")).grid(
            row=6, column=0, columnspan=2, sticky="w")
        return f

    def tab_wifi(self, nb):
        f = ttk.Frame(nb, padding=12)
        ttk.Label(f, text="Khi mất mạng sẽ thử kết nối lần lượt các mạng bên phải (từ trên xuống).\n"
                          "Chỉ dùng được mạng đã từng kết nối và lưu mật khẩu trên máy.",
                  foreground="gray").pack(anchor="w")
        self.wifi_picker = OrderedPicker(f, "Mạng Wi-Fi đã lưu trên máy", "Mạng sẽ thử (theo thứ tự)")
        self.wifi_picker.pack(fill="both", expand=True, pady=6)
        g = ttk.Frame(f)
        g.pack(fill="x")
        g.columnconfigure(1, weight=1)
        self.entry(g, 0, "Card Wi-Fi dùng (để trống = mặc định):", "wifi_iface",
                   self.cfg["recovery"].get("wifi_interface", ""), 30)
        ttk.Button(f, text="Làm mới danh sách", command=self.refresh_lists).pack(anchor="w")
        return f

    def tab_adapters(self, nb):
        f = ttk.Frame(nb, padding=12)
        ttk.Label(f, text="Nếu đổi Wi-Fi không được sẽ tắt/bật lại các card mạng bên phải (cần quyền Admin).",
                  foreground="gray").pack(anchor="w")
        self.adapter_picker = OrderedPicker(f, "Card mạng trên máy", "Card sẽ khởi động lại")
        self.adapter_picker.pack(fill="both", expand=True, pady=6)
        self.adapter_info = ttk.Label(f, text="", foreground="gray", justify="left")
        self.adapter_info.pack(anchor="w")
        ttk.Button(f, text="Làm mới danh sách", command=self.refresh_lists).pack(anchor="w", pady=4)
        return f

    def tab_reboot(self, nb):
        f = self.frame(nb)
        r = self.cfg["recovery"]
        self.check(f, 0, "Cho phép khởi động lại máy khi không khôi phục được mạng", "reboot_on",
                   r.get("reboot_enabled", True))
        self.entry(f, 1, "Đếm ngược trước khi khởi động lại (giây):", "reboot_delay", r.get("reboot_delay_sec", 60), 10)
        self.entry(f, 2, "Tối đa số lần khởi động lại / 24h:", "max_reboots", r.get("max_reboots_per_day", 3), 10)
        self.entry(f, 3, "Máy phải chạy ít nhất (phút) mới được reboot:", "min_uptime",
                   r.get("min_uptime_before_reboot_min", 15), 10)
        ttk.Label(f, text="Mở ứng dụng:").grid(row=4, column=0, sticky="w", pady=(12, 3))
        self.launch_mode = tk.StringVar(value=self.cfg.get("launch_apps", "after_reboot"))
        modes = ttk.Frame(f)
        modes.grid(row=4, column=1, sticky="w", pady=(12, 3))
        for val, txt in (("after_reboot", "Sau khi NetWatchdog khởi động lại máy"),
                         ("every_start", "Mỗi lần mở máy"), ("never", "Không")):
            ttk.Radiobutton(modes, text=txt, value=val, variable=self.launch_mode).pack(side="left", padx=(0, 8))

        cols = ("path", "args")
        self.apps_tree = ttk.Treeview(f, columns=cols, show="headings", height=7)
        self.apps_tree.heading("path", text="Ứng dụng")
        self.apps_tree.heading("args", text="Tham số")
        self.apps_tree.column("path", width=460)
        self.apps_tree.column("args", width=160)
        self.apps_tree.grid(row=5, column=0, columnspan=2, sticky="nsew")
        f.rowconfigure(5, weight=1)
        for app in self.cfg.get("apps") or []:
            self.apps_tree.insert("", tk.END, values=(app.get("path", ""), app.get("args", "")))
        b = ttk.Frame(f)
        b.grid(row=6, column=0, columnspan=2, sticky="w", pady=6)
        ttk.Button(b, text="Thêm ứng dụng…", command=self.add_app).pack(side="left")
        ttk.Button(b, text="Sửa tham số", command=self.edit_args).pack(side="left", padx=6)
        ttk.Button(b, text="Xóa", command=lambda: [self.apps_tree.delete(i) for i in self.apps_tree.selection()]).pack(side="left")
        ttk.Label(f, foreground="gray", justify="left", text=(
            "Lưu ý: để ứng dụng tự mở sau khi khởi động lại, Windows cần TỰ ĐĂNG NHẬP (xem README).")).grid(
            row=7, column=0, columnspan=2, sticky="w")
        return f

    def tab_check(self, nb):
        f = self.frame(nb)
        c = self.cfg["check"]
        r = self.cfg["recovery"]
        self.entry(f, 0, "Đích kiểm tra (host:port, cách nhau dấu phẩy):", "targets", ", ".join(c.get("targets", [])))
        self.entry(f, 1, "URL kiểm tra HTTP (dự phòng):", "http_url", c.get("http_url", ""))
        self.entry(f, 2, "Chu kỳ kiểm tra (giây):", "interval", c.get("interval_sec", 30), 10)
        self.entry(f, 3, "Số lần lỗi liên tiếp = mất mạng:", "threshold", c.get("fail_threshold", 3), 10)
        self.entry(f, 4, "Chờ sau mỗi thao tác (giây):", "wait_after", r.get("wait_after_action_sec", 25), 10)
        self.entry(f, 5, "Số vòng thử Wi-Fi + card mạng:", "rounds", r.get("rounds", 2), 10)
        self.entry(f, 6, "Nghỉ giữa các lần khôi phục thất bại (phút):", "cooldown", r.get("retry_cooldown_min", 10), 10)
        return f

    # ------------------------------------------------ actions
    def refresh_lists(self):
        self.config(cursor="watch")

        def work():
            profiles = sysops.list_wifi_profiles()
            adapters = sysops.list_adapters()
            self.after(0, lambda: self._fill_lists(profiles, adapters))
        threading.Thread(target=work, daemon=True).start()

    def _fill_lists(self, profiles, adapters):
        r = self.cfg["recovery"]
        loaded = getattr(self, "_lists_loaded", False)
        sel_w = self.wifi_picker.get() if loaded else list(r.get("wifi_profiles") or [])
        sel_a = self.adapter_picker.get() if loaded else list(r.get("adapters") or [])
        self._lists_loaded = True
        self.wifi_picker.set_items(profiles, sel_w)
        self.adapter_picker.set_items([a["name"] for a in adapters], sel_a)
        self.adapter_info.config(text="\n".join(f"{a['name']}: {a['status']} — {a['description']}" for a in adapters))
        self.config(cursor="")

    def add_app(self):
        path = filedialog.askopenfilename(title="Chọn ứng dụng", filetypes=[
            ("Ứng dụng", "*.exe *.lnk *.bat *.cmd"), ("Tất cả", "*.*")])
        if path:
            self.apps_tree.insert("", tk.END, values=(os.path.normpath(path), ""))

    def edit_args(self):
        sel = self.apps_tree.selection()
        if not sel:
            return
        path, args = self.apps_tree.item(sel[0], "values")
        win = tk.Toplevel(self)
        win.title("Tham số dòng lệnh")
        v = tk.StringVar(value=args)
        ttk.Label(win, text=os.path.basename(path)).pack(padx=10, pady=(10, 2), anchor="w")
        e = ttk.Entry(win, textvariable=v, width=60)
        e.pack(padx=10, pady=4)
        e.focus()

        def ok():
            self.apps_tree.item(sel[0], values=(path, v.get()))
            win.destroy()
        ttk.Button(win, text="OK", command=ok).pack(pady=(0, 10))

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
        messagebox.showinfo("Telegram", f"Đã lấy Chat ID {cid} ({name}).")

    def test_telegram(self):
        try:
            tg = self._telegram()
            cfg = load_config(CONFIG_PATH)
            tg.send_now("✅ NetWatchdog kết nối Telegram thành công!\n\n" + status_report(cfg, tg.state))
            messagebox.showinfo("Telegram", "Đã gửi tin thử.")
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("Telegram", f"Gửi lỗi: {e}")

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
            return
        messagebox.showinfo("Lưu", "Đã lưu. NetWatchdog sẽ tự áp dụng cấu hình mới.")


if __name__ == "__main__":
    SettingsApp().mainloop()
