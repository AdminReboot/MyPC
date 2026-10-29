"""Khóa máy khi không có người dùng, và cảnh báo khi có người thao tác TRỰC TIẾP tại máy.

- Tự khóa: không có thao tác chuột/bàn phím (kể cả qua UltraViewer) quá `idle_lock_min` phút thì khóa máy
  như bấm Win+L. Người điều khiển từ xa mở khóa bằng cách gõ mật khẩu Windows trên màn hình khóa.
- Cảnh báo thao tác tại máy: dùng Raw Input. Windows gửi bản sao mỗi sự kiện chuột/bàn phím kèm thiết bị phát
  ra nó; sự kiện do chương trình giả lập (UltraViewer, SendInput) có thiết bị = 0, sự kiện từ chuột/bàn phím
  thật có thiết bị khác 0. Raw Input không chặn luồng nhập nên không làm chậm chuột (khác với hook).
  Chỉ ĐẾM sự kiện, không ghi phím nào được gõ hay vị trí chuột.
- Khi máy đang khóa, thao tác trên màn hình khóa không đọc được; lần thao tác đầu tiên sau khi mở khóa
  tại máy sẽ được phát hiện.
"""
import ctypes
import ctypes.wintypes as wt
import logging
import threading
import time

log = logging.getLogger("netwatchdog")

DEFAULT_CONFIG = {
    "auto_lock": True,
    "idle_lock_min": 5,          # không có thao tác quá số phút này thì khóa máy
    "local_input_alert": True,   # báo khi có người dùng chuột/bàn phím trực tiếp tại máy
    "min_keys": 1,               # đủ số phím thật trong `window_sec` giây...
    "min_mouse": 15,             # ...hoặc đủ số sự kiện chuột thật (bỏ qua bàn bị rung, chuột trôi nhẹ)
    "window_sec": 10,
    "quiet_min": 10,             # không còn thao tác thật quá số phút này = hết lượt, lần sau báo lại
    "screenshot": True,          # gửi kèm ảnh màn hình lúc phát hiện
}


# ---------------------------------------------------------------- Raw Input (luồng riêng)
class RawInputWatcher(threading.Thread):
    """Nhận bản sao sự kiện chuột/bàn phím qua Raw Input trên một cửa sổ ẩn, đếm theo nguồn
    (phần cứng thật / giả lập). Không ghi nội dung."""

    def __init__(self):
        super().__init__(daemon=True, name="raw-input")
        self.lock = threading.Lock()
        self.counts = {"mouse_physical": 0, "key_physical": 0, "mouse_injected": 0, "key_injected": 0}
        self.last_physical = 0.0
        self.ready = threading.Event()
        self.error = ""

    def snapshot(self):
        with self.lock:
            return dict(self.counts), self.last_physical

    def _on_input(self, kind, physical):
        with self.lock:
            self.counts[f"{kind}_{'physical' if physical else 'injected'}"] += 1
            if physical:
                self.last_physical = time.time()

    def run(self):
        try:
            self._run()
        except Exception as e:  # noqa: BLE001
            self.error = str(e)
            log.exception("Raw Input dừng vì lỗi")
            self.ready.set()

    def _run(self):
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
        user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
        user32.DefWindowProcW.restype = ctypes.c_ssize_t
        user32.CreateWindowExW.restype = wt.HWND
        user32.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, ctypes.c_int, ctypes.c_int,
                                           ctypes.c_int, ctypes.c_int, wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID]
        user32.GetRawInputData.argtypes = [wt.HANDLE, wt.UINT, ctypes.c_void_p, ctypes.POINTER(wt.UINT), wt.UINT]

        class WNDCLASS(ctypes.Structure):
            _fields_ = [("style", wt.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                        ("cbWndExtra", ctypes.c_int), ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON),
                        ("hCursor", wt.HANDLE), ("hbrBackground", wt.HBRUSH), ("lpszMenuName", wt.LPCWSTR),
                        ("lpszClassName", wt.LPCWSTR)]

        class RID(ctypes.Structure):
            _fields_ = [("usUsagePage", wt.USHORT), ("usUsage", wt.USHORT), ("dwFlags", wt.DWORD), ("hwndTarget", wt.HWND)]

        class RIHEADER(ctypes.Structure):
            _fields_ = [("dwType", wt.DWORD), ("dwSize", wt.DWORD), ("hDevice", wt.HANDLE), ("wParam", wt.WPARAM)]

        hdr, size = RIHEADER(), wt.UINT()

        @WNDPROC
        def proc(hwnd, msg, w, l):
            if msg == 0x00FF:                                        # WM_INPUT
                size.value = ctypes.sizeof(RIHEADER)
                if user32.GetRawInputData(l, 0x10000005, ctypes.byref(hdr), ctypes.byref(size),
                                          ctypes.sizeof(RIHEADER)) > 0:  # RID_HEADER: chỉ đọc phần đầu
                    if hdr.dwType in (0, 1):                         # 0 = chuột, 1 = bàn phím
                        self._on_input("mouse" if hdr.dwType == 0 else "key", bool(hdr.hDevice))
            return user32.DefWindowProcW(hwnd, msg, w, l)

        self._proc = proc                                            # giữ tham chiếu, tránh bị thu hồi
        inst = ctypes.windll.kernel32.GetModuleHandleW(None)
        wc = WNDCLASS(lpfnWndProc=proc, lpszClassName="NetWatchdogRawInput", hInstance=inst)
        user32.RegisterClassW(ctypes.byref(wc))
        hwnd = user32.CreateWindowExW(0, "NetWatchdogRawInput", "", 0, 0, 0, 0, 0, wt.HWND(-3), None, inst, None)
        rids = (RID * 2)(RID(1, 2, 0x100, hwnd), RID(1, 6, 0x100, hwnd))  # chuột, bàn phím; RIDEV_INPUTSINK
        if not hwnd or not user32.RegisterRawInputDevices(rids, 2, ctypes.sizeof(RID)):
            self.error = f"không đăng ký được Raw Input (lỗi {ctypes.get_last_error()})"
            self.ready.set()
            return
        self.ready.set()
        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))


# ---------------------------------------------------------------- Tiện ích Windows
class _LII(ctypes.Structure):
    _fields_ = [("cbSize", wt.UINT), ("dwTime", wt.DWORD)]


def idle_seconds():
    """Số giây kể từ thao tác chuột/bàn phím gần nhất trong phiên này (kể cả qua UltraViewer)."""
    li = _LII(ctypes.sizeof(_LII))
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(li)):
        return 0.0
    return ((ctypes.windll.kernel32.GetTickCount() - li.dwTime) & 0xFFFFFFFF) / 1000.0


def lock_workstation():
    import sysops
    if sysops.DRY_RUN:
        return sysops._dry("khóa máy")
    return bool(ctypes.windll.user32.LockWorkStation()), "OK"


def capture_screen_png():
    """Chụp toàn bộ màn hình (mọi màn hình) -> PNG, hoặc None. Không dùng thư viện ngoài pywin32."""
    import win32con
    import win32gui
    import win32ui

    import jxtd
    user32 = ctypes.WinDLL("user32")
    set_ctx = getattr(user32, "SetThreadDpiAwarenessContext", None)
    old = None
    if set_ctx:
        set_ctx.restype, set_ctx.argtypes = ctypes.c_void_p, [ctypes.c_void_p]
        old = set_ctx(ctypes.c_void_p(-4))           # kích thước thật trên màn hình có scale
    hdc = src = mem = bmp = None
    try:
        x, y = user32.GetSystemMetrics(76), user32.GetSystemMetrics(77)
        w, h = user32.GetSystemMetrics(78), user32.GetSystemMetrics(79)
        hdc = win32gui.GetDC(0)
        src = win32ui.CreateDCFromHandle(hdc)
        mem = src.CreateCompatibleDC()
        bmp = win32ui.CreateBitmap()
        bmp.CreateCompatibleBitmap(src, w, h)
        mem.SelectObject(bmp)
        mem.BitBlt((0, 0), (w, h), src, (x, y), win32con.SRCCOPY)
        bits = bmp.GetBitmapBits(True)
        return jxtd.encode_png(w, h, bits) if len(bits) == w * h * 4 else None
    except Exception:  # noqa: BLE001
        log.exception("Không chụp được màn hình")
        return None
    finally:
        if mem:
            mem.DeleteDC()
        if src:
            src.DeleteDC()
        if hdc:
            win32gui.ReleaseDC(0, hdc)
        if bmp:
            win32gui.DeleteObject(bmp.GetHandle())
        if old:
            set_ctx(old)


# ---------------------------------------------------------------- Bộ theo dõi
class PresenceMonitor:
    """Gọi tick() vài giây một lần. Các hàm hệ thống truyền vào để test được."""

    def __init__(self, get_cfg, notify, send_photo=None, watcher=None, idle=idle_seconds, locked=None,
                 lock=lock_workstation, screenshot=capture_screen_png, clock=time.time, label=None):
        import sysops
        self.get_cfg, self.notify, self.send_photo = get_cfg, notify, send_photo
        self.watcher, self.idle, self.lock, self.screenshot, self.clock = watcher, idle, lock, screenshot, clock
        self.locked = locked or sysops.session_locked
        self.label = label or (lambda: "")
        self.prev = None                      # số đếm lần trước
        self.window = []                      # [(thời điểm, phím mới, chuột mới)] trong cửa sổ `window_sec`
        self.episode = None                   # {"start", "last", "keys", "mouse"} khi đang có người ở máy
        self.last_lock_try = 0.0
        self.last_alert = None

    def P(self):
        return self.get_cfg()["presence"]

    def tick(self):
        P, now, sent = self.P(), self.clock(), []
        is_locked = bool(self.locked())
        # 1) tự khóa khi không có ai dùng
        if P["auto_lock"] and not is_locked and now - self.last_lock_try >= 60:
            idle = self.idle()
            if idle >= P["idle_lock_min"] * 60:
                self.last_lock_try = now
                ok, out = self.lock()
                log.info("Tự khóa máy sau %.0f giây không thao tác: %s %s", idle, ok, out)
        # 2) thao tác trực tiếp tại máy
        if not (P["local_input_alert"] and self.watcher):
            return sent
        counts, _ = self.watcher.snapshot()
        if self.prev is None:
            self.prev = counts
            return sent
        dk = counts["key_physical"] - self.prev["key_physical"]
        dm = counts["mouse_physical"] - self.prev["mouse_physical"]
        self.prev = counts
        if dk or dm:
            self.window.append((now, dk, dm))
        self.window = [w for w in self.window if now - w[0] <= P["window_sec"]]
        keys, mouse = sum(w[1] for w in self.window), sum(w[2] for w in self.window)
        if self.episode:
            if dk or dm:
                self.episode.update(last=now, keys=self.episode["keys"] + dk, mouse=self.episode["mouse"] + dm)
            elif now - self.episode["last"] >= P["quiet_min"] * 60:
                e = self.episode
                self.episode = None
                msg = (f"ℹ️{self.label()} Hết thao tác trực tiếp tại máy (từ {time.strftime('%H:%M', time.localtime(e['start']))} "
                       f"đến {time.strftime('%H:%M', time.localtime(e['last']))}; {e['keys']} phím, {e['mouse']} sự kiện chuột).")
                self.notify(msg)
                sent.append(msg)
            return sent
        if keys >= P["min_keys"] or mouse >= P["min_mouse"]:
            self.episode = {"start": now, "last": now, "keys": keys, "mouse": mouse}
            self.window = []
            msg = (f"⚠️{self.label()} Có người dùng {'bàn phím' if keys else 'chuột'}"
                   f"{' và chuột' if keys and mouse else ''} TRỰC TIẾP tại máy lúc {time.strftime('%H:%M:%S', time.localtime(now))} "
                   f"(không phải qua điều khiển từ xa).")
            png = self.screenshot() if P["screenshot"] and not is_locked else None
            delivered = False
            if png and self.send_photo:
                try:
                    self.send_photo(png, msg)
                    delivered = True
                except Exception as e:  # noqa: BLE001
                    log.warning("Gửi ảnh màn hình lỗi: %s", e)
            if not delivered:
                self.notify(msg)
            self.last_alert = now
            sent.append(msg)
        return sent

    def status_line(self):
        P = self.P()
        parts = [f"🔒 Tự khóa sau {P['idle_lock_min']} phút không dùng" if P["auto_lock"] else "🔒 Tự khóa: tắt"]
        if P["local_input_alert"]:
            if self.watcher and self.watcher.error:
                parts.append(f"⚠️ giám sát tại chỗ lỗi: {self.watcher.error}")
            elif self.episode:
                parts.append("🖐 đang có người thao tác tại máy")
            elif self.last_alert:
                parts.append(f"🖐 thao tác tại máy gần nhất: {time.strftime('%H:%M %d/%m', time.localtime(self.last_alert))}")
            else:
                parts.append("🖐 giám sát thao tác tại máy: bật")
        return " · ".join(parts)
