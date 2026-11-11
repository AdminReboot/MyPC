"""Điều khiển máy khẩn cấp qua Telegram: xem ảnh màn hình có lưới ô, bấm chuột, gõ chữ, nhấn phím.

Không phải xem trực tiếp như UltraViewer/RustDesk: mỗi lệnh gửi lại một ảnh JPEG nhỏ, nên vẫn dùng được khi
mạng rất chậm. Dùng để cứu máy (đóng hộp thoại, mở lại ứng dụng...), không để làm việc lâu.

Bảo mật:
- Tắt sẵn; chỉ nhận lệnh từ chat_id đã cấu hình, chỉ trong chat riêng (không nhận trong nhóm).
- Phải gửi /dk <mã PIN> để mở một phiên; phiên tự hết hạn sau `session_min` phút không dùng.
  Tin nhắn chứa mã PIN bị xóa ngay. Mã PIN lưu dạng băm (PBKDF2), không lưu nguyên văn.
- Sai PIN `max_fail` lần thì khóa tính năng `lockout_min` phút và gửi cảnh báo.
- Mọi lệnh điều khiển được ghi log (nội dung chữ gõ chỉ ghi số ký tự).
- Màn hình khóa / UAC là màn hình bảo mật của Windows: chương trình không chụp và không bấm được vào đó.

Địa chỉ ô: màn hình chia lưới (mặc định 10 cột A..J). "D4" = ô cột D hàng 4; thêm chữ số để chọn ô con 3x3
theo bố cục bàn phím điện thoại (1 = trên trái, 5 = giữa, 9 = dưới phải): "D4 5", "D4 56" (chính xác dần).
Có thể dùng tọa độ điểm ảnh: "640 380".
"""
import ctypes
import ctypes.wintypes as wt
import hashlib
import logging
import os
import re
import secrets
import tempfile
import time
import uuid

log = logging.getLogger("netwatchdog")

DEFAULT_CONFIG = {
    "enabled": False,
    "pin_hash": "",          # PBKDF2-SHA256, đặt trong cửa sổ Cài đặt
    "pin_salt": "",
    "session_min": 10,       # phiên tự tắt sau số phút không có lệnh
    "max_fail": 5,           # sai PIN quá số lần này thì khóa tính năng...
    "lockout_min": 60,       # ...trong số phút này
    "cols": 10,              # số cột lưới ô trên ảnh
    "width": 1280,           # chiều rộng ảnh gửi về (điểm ảnh)
    "quality": 60,           # chất lượng JPEG
    "delay_ms": 800,         # chờ màn hình cập nhật sau mỗi thao tác rồi mới chụp
}

COMMANDS = ("/dk", "/dk_tat", "/mh", "/c", "/cp", "/c2", "/zoom", "/go", "/phim", "/cuon")

HELP = ("🕹 Điều khiển máy qua Telegram:\n"
        "/dk <mã PIN> — bật phiên điều khiển · /dk_tat — tắt\n"
        "/mh — ảnh màn hình có lưới ô\n"
        "/c D4 — bấm chuột trái giữa ô D4 · /c D4 5 — ô con số 5 (bố cục bàn phím điện thoại) · /c 640 380\n"
        "/cp D4 — chuột phải · /c2 D4 — bấm đúp\n"
        "/zoom D4 — phóng to ô D4 để chọn ô con chính xác hơn\n"
        "/go <chữ> — gõ chữ (có dấu được)\n"
        "/phim enter · /phim ctrl+c · /phim alt+f4 · /phim tab tab enter\n"
        "/cuon xuong 3 · /cuon len")


# ---------------------------------------------------------------- Mã PIN
def hash_pin(pin, salt=None):
    salt = salt or secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", str(pin).encode(), bytes.fromhex(salt), 200_000).hex()
    return h, salt


def check_pin(pin, R):
    if not R.get("pin_hash") or not R.get("pin_salt"):
        return False
    return secrets.compare_digest(hash_pin(pin, R["pin_salt"])[0], R["pin_hash"])


# ---------------------------------------------------------------- Lưới ô
class Grid:
    """Lưới ô trên màn hình ảo (tọa độ điểm ảnh thật, có thể âm khi có nhiều màn hình)."""

    def __init__(self, x, y, w, h, cols=10):
        self.x, self.y, self.w, self.h = x, y, w, h
        self.cols = max(2, min(26, int(cols)))
        self.cw = w / self.cols
        self.rows = max(1, round(h / self.cw))
        self.ch = h / self.rows

    def cell_name(self, c, r):
        return f"{chr(65 + c)}{r + 1}"

    def box(self, addr):
        """'D4', 'D4 56', 'd4.5' -> (x, y, w, h). Lỗi -> ValueError."""
        m = re.fullmatch(r"\s*([a-zA-Z])\s*(\d{1,2})(?:[\s.,]+([1-9][1-9\s]*))?\s*", addr or "")
        if not m:
            raise ValueError(f"ô “{addr}” không hợp lệ (vd. D4, D4 5)")
        c, r = ord(m.group(1).upper()) - 65, int(m.group(2)) - 1
        if not (0 <= c < self.cols and 0 <= r < self.rows):
            raise ValueError(f"không có ô {m.group(1).upper()}{r + 1} (lưới A1..{self.cell_name(self.cols - 1, self.rows - 1)})")
        x, y, w, h = self.x + c * self.cw, self.y + r * self.ch, self.cw, self.ch
        for d in (m.group(3) or "").replace(" ", ""):
            i, j = (int(d) - 1) % 3, (int(d) - 1) // 3
            w, h = w / 3, h / 3
            x, y = x + i * w, y + j * h
        return x, y, w, h

    def target(self, text):
        """Địa chỉ ô hoặc tọa độ 'x y' -> (x, y, nhãn)."""
        m = re.fullmatch(r"\s*(-?\d{1,5})[\s,]+(-?\d{1,5})\s*", text or "")
        if m:
            x, y = int(m.group(1)), int(m.group(2))
            if not (self.x <= x < self.x + self.w and self.y <= y < self.y + self.h):
                raise ValueError(f"tọa độ {x},{y} nằm ngoài màn hình")
            return x, y, f"{x},{y}"
        x, y, w, h = self.box(text)
        label = re.sub(r"[\s.,]+", " ", text.strip()).upper()
        return int(x + w / 2), int(y + h / 2), label


# ---------------------------------------------------------------- Phím
VK = {"enter": 0x0D, "esc": 0x1B, "tab": 0x09, "space": 0x20, "cach": 0x20, "backspace": 0x08, "xoa": 0x08,
      "del": 0x2E, "delete": 0x2E, "ins": 0x2D, "insert": 0x2D, "home": 0x24, "end": 0x23, "pgup": 0x21,
      "pgdn": 0x22, "up": 0x26, "len": 0x26, "down": 0x28, "xuong": 0x28, "left": 0x25, "trai": 0x25,
      "right": 0x27, "phai": 0x27, "ctrl": 0x11, "alt": 0x12, "shift": 0x10, "win": 0x5B, "menu": 0x5D,
      "printscreen": 0x2C, "prtsc": 0x2C}
VK.update({f"f{i}": 0x6F + i for i in range(1, 13)})
VK.update({ch: ord(ch.upper()) for ch in "abcdefghijklmnopqrstuvwxyz0123456789"})
EXTENDED = {0x2E, 0x2D, 0x24, 0x23, 0x21, 0x22, 0x26, 0x28, 0x25, 0x27, 0x5B, 0x5D}


def parse_keys(text):
    """'ctrl+c enter' -> [[0x11, 0x43], [0x0D]]. Lỗi -> ValueError."""
    combos = []
    for part in (text or "").lower().split():
        names = [n for n in part.split("+") if n]
        if {"ctrl", "alt"} <= set(names) and ({"del", "delete"} & set(names)):
            raise ValueError("Windows không cho chương trình gửi Ctrl+Alt+Del. Dùng RustDesk/UltraViewer, "
                             "hoặc /phim ctrl+shift+esc để mở Task Manager.")
        bad = [n for n in names if n not in VK]
        if bad or not names:
            raise ValueError(f"không biết phím “{'+'.join(bad) or part}”")
        combos.append([VK[n] for n in names])
    if not combos:
        raise ValueError("chưa ghi phím nào (vd. /phim enter)")
    return combos


# ---------------------------------------------------------------- Windows: nhập liệu
class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wt.LONG), ("dy", wt.LONG), ("mouseData", wt.DWORD), ("dwFlags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD), ("time", wt.DWORD),
                ("dwExtraInfo", ctypes.c_size_t)]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wt.DWORD), ("wParamL", wt.WORD), ("wParamH", wt.WORD)]


class _INPUTU(ctypes.Union):
    _fields_ = [("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT), ("hi", _HARDWAREINPUT)]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", wt.DWORD), ("u", _INPUTU)]


def _dpi_aware():
    """Cho luồng hiện tại dùng tọa độ điểm ảnh thật (màn hình có scale 125%, 150%...). Trả hàm khôi phục."""
    set_ctx = getattr(ctypes.windll.user32, "SetThreadDpiAwarenessContext", None)
    if not set_ctx:
        return lambda: None
    set_ctx.restype, set_ctx.argtypes = ctypes.c_void_p, [ctypes.c_void_p]
    old = set_ctx(ctypes.c_void_p(-4))                     # PER_MONITOR_AWARE_V2
    return lambda: set_ctx(ctypes.c_void_p(old)) if old else None


def _send(inputs):
    user32 = ctypes.windll.user32
    user32.SendInput.argtypes = [wt.UINT, ctypes.POINTER(_INPUT), ctypes.c_int]
    arr = (_INPUT * len(inputs))(*inputs)
    n = user32.SendInput(len(inputs), arr, ctypes.sizeof(_INPUT))
    if n != len(inputs):
        raise RuntimeError("Windows từ chối thao tác (máy đang khóa, hoặc cửa sổ bảo mật như UAC đang mở)")


def _mouse(flags, data=0):
    return _INPUT(type=0, u=_INPUTU(mi=_MOUSEINPUT(0, 0, data & 0xFFFFFFFF, flags, 0, 0)))


def _key(vk, up=False, scan=0, unicode=False):
    flags = (0x2 if up else 0) | (0x4 if unicode else 0) | (0x1 if vk in EXTENDED and not unicode else 0)
    if not unicode:
        scan = ctypes.windll.user32.MapVirtualKeyW(vk, 0)
    return _INPUT(type=1, u=_INPUTU(ki=_KEYBDINPUT(0 if unicode else vk, scan, flags, 0, 0)))


def screen_rect():
    restore = _dpi_aware()
    try:
        m = ctypes.windll.user32.GetSystemMetrics
        return m(76), m(77), m(78), m(79)                  # màn hình ảo: x, y, rộng, cao
    finally:
        restore()


def cursor_pos():
    restore = _dpi_aware()
    try:
        pt = wt.POINT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
        return pt.x, pt.y
    finally:
        restore()


def click(x, y, button="left", double=False):
    restore = _dpi_aware()
    try:
        if not ctypes.windll.user32.SetCursorPos(int(x), int(y)):
            raise RuntimeError("không di chuyển được chuột (máy đang khóa?)")
    finally:
        restore()
    time.sleep(0.05)
    down, up = (0x0002, 0x0004) if button == "left" else (0x0008, 0x0010)
    seq = [_mouse(down), _mouse(up)] * (2 if double else 1)
    _send(seq)


def scroll(steps):
    _send([_mouse(0x0800, 120 * steps)])                    # dương = lên


def press(combos):
    for keys in combos:
        _send([_key(k) for k in keys] + [_key(k, up=True) for k in reversed(keys)])
        time.sleep(0.05)


def type_text(text):
    for ch in text:
        if ch == "\n":
            _send([_key(0x0D), _key(0x0D, up=True)])
            continue
        units = ch.encode("utf-16-le")
        codes = [int.from_bytes(units[i:i + 2], "little") for i in range(0, len(units), 2)]
        _send([_key(0, scan=c, unicode=True) for c in codes] + [_key(0, up=True, scan=c, unicode=True) for c in codes])


# ---------------------------------------------------------------- Windows: chụp ảnh có lưới
def _gdi():
    u, g = ctypes.WinDLL("user32"), ctypes.WinDLL("gdi32")
    H, I = ctypes.c_void_p, ctypes.c_int
    sig = {
        (u, "GetDC"): ([H], H), (u, "ReleaseDC"): ([H, H], I), (u, "FillRect"): ([H, ctypes.c_void_p, H], I),
        (g, "CreateCompatibleDC"): ([H], H), (g, "CreateCompatibleBitmap"): ([H, I, I], H),
        (g, "SelectObject"): ([H, H], H), (g, "DeleteObject"): ([H], I), (g, "DeleteDC"): ([H], I),
        (g, "StretchBlt"): ([H, I, I, I, I, H, I, I, I, I, wt.DWORD], I), (g, "SetStretchBltMode"): ([H, I], I),
        (g, "SetBrushOrgEx"): ([H, I, I, ctypes.c_void_p], I), (g, "CreatePen"): ([I, I, wt.DWORD], H),
        (g, "MoveToEx"): ([H, I, I, ctypes.c_void_p], I), (g, "LineTo"): ([H, I, I], I),
        (g, "SetBkMode"): ([H, I], I), (g, "SetBkColor"): ([H, wt.DWORD], wt.DWORD),
        (g, "SetTextColor"): ([H, wt.DWORD], wt.DWORD), (g, "TextOutW"): ([H, I, I, wt.LPCWSTR, I], I),
        (g, "CreateSolidBrush"): ([wt.DWORD], H),
        (g, "CreateFontW"): ([I] * 5 + [wt.DWORD] * 8 + [wt.LPCWSTR], H),
        (g, "GetDIBits"): ([H, H, wt.UINT, wt.UINT, ctypes.c_void_p, ctypes.c_void_p, wt.UINT], I),
    }
    for (lib, name), (args, res) in sig.items():
        f = getattr(lib, name)
        f.argtypes, f.restype = args, res
    return u, g


YELLOW, RED, BLACK, CYAN = 0x0000FFFF, 0x000000FF, 0x00000000, 0x00FFFF00


def _jpeg(hbmp, quality):
    """HBITMAP -> JPEG bằng GDI+ có sẵn trong Windows (không cần thư viện ngoài)."""
    gp = ctypes.WinDLL("gdiplus")

    class GUID(ctypes.Structure):
        _fields_ = [("d1", ctypes.c_uint32), ("d2", ctypes.c_uint16), ("d3", ctypes.c_uint16), ("d4", ctypes.c_ubyte * 8)]

    class StartupInput(ctypes.Structure):
        _fields_ = [("ver", ctypes.c_uint32), ("cb", ctypes.c_void_p), ("nobg", wt.BOOL), ("noext", wt.BOOL)]

    class EncParam(ctypes.Structure):
        _fields_ = [("guid", GUID), ("n", ctypes.c_ulong), ("type", ctypes.c_ulong), ("value", ctypes.c_void_p)]

    class EncParams(ctypes.Structure):
        _fields_ = [("count", ctypes.c_uint), ("p", EncParam * 1)]

    def guid(s):
        return GUID.from_buffer_copy(uuid.UUID(s).bytes_le)

    token, img = ctypes.c_size_t(), ctypes.c_void_p()
    if gp.GdiplusStartup(ctypes.byref(token), ctypes.byref(StartupInput(1, None, 0, 0)), None):
        raise RuntimeError("GdiplusStartup lỗi")
    fd, path = tempfile.mkstemp(suffix=".jpg")
    os.close(fd)
    try:
        gp.GdipCreateBitmapFromHBITMAP.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        if gp.GdipCreateBitmapFromHBITMAP(hbmp, None, ctypes.byref(img)):
            raise RuntimeError("GdipCreateBitmapFromHBITMAP lỗi")
        q = ctypes.c_ulong(int(quality))
        params = EncParams(1, (EncParam * 1)(EncParam(guid("1d5be4b5-fa4a-452d-9cdd-5db35105e7eb"), 1, 4,
                                                      ctypes.cast(ctypes.byref(q), ctypes.c_void_p))))
        clsid = guid("557cf401-1a04-11d3-9a73-0000f81ef32e")       # bộ mã hóa JPEG
        gp.GdipSaveImageToFile.argtypes = [ctypes.c_void_p, wt.LPCWSTR, ctypes.c_void_p, ctypes.c_void_p]
        if gp.GdipSaveImageToFile(img, path, ctypes.byref(clsid), ctypes.byref(params)):
            raise RuntimeError("GdipSaveImageToFile lỗi")
        with open(path, "rb") as f:
            return f.read()
    finally:
        if img:
            gp.GdipDisposeImage(img)
        gp.GdiplusShutdown(token)
        try:
            os.remove(path)
        except OSError:
            pass


def render(grid, view=None, zoom_box=None, width=1280, quality=60, cursor=None):
    """Chụp vùng `view` (x, y, w, h; mặc định cả màn hình), thu nhỏ còn `width` điểm ảnh, vẽ lưới ô
    (hoặc ô con 1..9 khi có `zoom_box`) và vị trí chuột. Trả JPEG."""
    u, g = _gdi()
    vx, vy, vw, vh = [int(round(v)) for v in (view or (grid.x, grid.y, grid.w, grid.h))]
    ow = int(width)
    oh = max(1, int(round(vh * ow / vw)))
    sx, sy = ow / vw, oh / vh
    restore = _dpi_aware()
    scr = mem = bmp = old = None
    objs = []
    try:
        scr = u.GetDC(None)
        mem = g.CreateCompatibleDC(scr)
        bmp = g.CreateCompatibleBitmap(scr, ow, oh)
        old = g.SelectObject(mem, bmp)
        g.SetStretchBltMode(mem, 4 if ow <= vw else 3)        # HALFTONE khi thu nhỏ, COLORONCOLOR khi phóng to
        g.SetBrushOrgEx(mem, 0, 0, None)
        if not g.StretchBlt(mem, 0, 0, ow, oh, scr, vx, vy, vw, vh, 0x00CC0020 | 0x40000000):
            raise RuntimeError("không chụp được màn hình")

        def X(x):
            return int(round((x - vx) * sx))

        def Y(y):
            return int(round((y - vy) * sy))

        def pen(color, w=1):
            p = g.CreatePen(0, w, color)
            objs.append(p)
            g.SelectObject(mem, p)

        def line(x1, y1, x2, y2):
            g.MoveToEx(mem, x1, y1, None)
            g.LineTo(mem, x2, y2)

        def font(px):
            f = g.CreateFontW(-max(10, int(px)), 0, 0, 0, 700, 0, 0, 0, 1, 0, 0, 4, 0, "Segoe UI")
            objs.append(f)
            g.SelectObject(mem, f)

        def text(x, y, s, fg=YELLOW, bg=BLACK):
            g.SetBkMode(mem, 2)
            g.SetBkColor(mem, bg)
            g.SetTextColor(mem, fg)
            g.TextOutW(mem, x, y, s, len(s))

        if zoom_box is None:
            pen(YELLOW)
            for c in range(1, grid.cols):
                line(X(grid.x + c * grid.cw), 0, X(grid.x + c * grid.cw), oh)
            for r in range(1, grid.rows):
                line(0, Y(grid.y + r * grid.ch), ow, Y(grid.y + r * grid.ch))
            font(min(grid.cw * sx, grid.ch * sy) * 0.17)
            for c in range(grid.cols):
                for r in range(grid.rows):
                    text(X(grid.x + c * grid.cw) + 2, Y(grid.y + r * grid.ch) + 2, grid.cell_name(c, r))
        else:
            bx, by, bw, bh = zoom_box
            pen(YELLOW, 1)
            for i in (1, 2):
                line(X(bx + i * bw / 3), Y(by), X(bx + i * bw / 3), Y(by + bh))
                line(X(bx), Y(by + i * bh / 3), X(bx + bw), Y(by + i * bh / 3))
            pen(RED, 3)
            for (x1, y1, x2, y2) in ((bx, by, bx + bw, by), (bx + bw, by, bx + bw, by + bh),
                                     (bx, by + bh, bx + bw, by + bh), (bx, by, bx, by + bh)):
                line(X(x1), Y(y1), X(x2), Y(y2))
            font(bw * sx / 3 * 0.18)
            for d in range(9):
                i, j = d % 3, d // 3
                text(X(bx + i * bw / 3) + 3, Y(by + j * bh / 3) + 3, str(d + 1))
        if cursor and vx <= cursor[0] < vx + vw and vy <= cursor[1] < vy + vh:
            cx, cy = X(cursor[0]), Y(cursor[1])
            pen(RED, 2)
            line(cx - 12, cy, cx + 12, cy)
            line(cx, cy - 12, cx, cy + 12)
        g.SelectObject(mem, old)
        old = None
        try:
            return _jpeg(bmp, quality), "image/jpeg"
        except Exception as e:  # noqa: BLE001  (GDI+ lỗi: gửi PNG)
            log.warning("Không nén được JPEG (%s), gửi PNG", e)
            return _png(g, scr, bmp, ow, oh), "image/png"
    finally:
        if old:
            g.SelectObject(mem, old)
        for o in objs:
            g.DeleteObject(o)
        if bmp:
            g.DeleteObject(bmp)
        if mem:
            g.DeleteDC(mem)
        if scr:
            u.ReleaseDC(None, scr)
        restore()


def _png(g, dc, bmp, w, h):
    import jxtd

    class BIH(ctypes.Structure):
        _fields_ = [("size", wt.DWORD), ("w", wt.LONG), ("h", wt.LONG), ("planes", wt.WORD), ("bits", wt.WORD),
                    ("comp", wt.DWORD), ("img", wt.DWORD), ("xp", wt.LONG), ("yp", wt.LONG), ("used", wt.DWORD),
                    ("imp", wt.DWORD)]
    bih = BIH(ctypes.sizeof(BIH), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
    buf = ctypes.create_string_buffer(w * h * 4)
    g.GetDIBits(dc, bmp, 0, h, buf, ctypes.byref(bih), 0)
    return jxtd.encode_png(w, h, buf.raw)


# ---------------------------------------------------------------- Phiên điều khiển
class Reply(str):
    """Trả lời kèm nút bấm (giống netwatchdog.Reply, tách riêng để không phụ thuộc vòng)."""

    def __new__(cls, text, markup=None):
        obj = super().__new__(cls, text)
        obj.markup, obj.html, obj.edit = markup, False, False
        return obj


MARKUP = {"inline_keyboard": [
    [{"text": "🔄 Làm mới", "callback_data": "rc:mh"}, {"text": "↵ Enter", "callback_data": "rc:k:enter"},
     {"text": "Esc", "callback_data": "rc:k:esc"}, {"text": "Tab", "callback_data": "rc:k:tab"}],
    [{"text": "⬆️ Cuộn", "callback_data": "rc:w:3"}, {"text": "⬇️ Cuộn", "callback_data": "rc:w:-3"},
     {"text": "⌫ Xóa", "callback_data": "rc:k:backspace"}, {"text": "⏹ Kết thúc", "callback_data": "rc:end"}]]}


class RemoteControl:
    """Xử lý các lệnh điều khiển. Hàm hệ thống truyền vào để test được."""

    def __init__(self, get_cfg, notify, send_photo, delete_message=None, locked=None, clock=time.time,
                 label=None, system=None, sleep=time.sleep):
        import sysops
        self.get_cfg, self.notify, self.send_photo = get_cfg, notify, send_photo
        self.delete_message = delete_message or (lambda mid: None)
        self.locked = locked or sysops.session_locked
        self.clock, self.sleep = clock, sleep
        self.label = label or (lambda: "")
        self.sys = system or {"screen": screen_rect, "cursor": cursor_pos, "render": render, "click": click,
                              "scroll": scroll, "press": press, "type": type_text}
        self.until = 0.0                  # phiên đang mở đến thời điểm này
        self.fails, self.locked_until = [], 0.0

    def R(self):
        return self.get_cfg()["remote"]

    def active(self):
        return self.R().get("enabled") and self.clock() < self.until

    def status_line(self):
        R = self.R()
        if not R.get("enabled"):
            return ""
        if self.active():
            return f"🕹 Điều khiển qua Telegram: ĐANG MỞ đến {time.strftime('%H:%M', time.localtime(self.until))}"
        return "🕹 Điều khiển qua Telegram: sẵn sàng (/dk <mã PIN>)" if R.get("pin_hash") else \
            "🕹 Điều khiển qua Telegram: chưa đặt mã PIN"

    def tick(self):
        """Báo khi phiên hết hạn."""
        if self.until and self.clock() >= self.until:
            self.until = 0.0
            log.info("Điều khiển từ xa: phiên hết hạn")
            self.notify(f"🕹{self.label()} Đã tắt điều khiển qua Telegram (hết {self.R().get('session_min', 10)} phút không dùng).")

    def grid(self):
        return Grid(*self.sys["screen"](), cols=self.R().get("cols", 10))

    # ---------------------------------------------- lệnh
    def handle(self, cmd, rest, msg=None):
        msg = msg or {}
        R = self.R()
        if not R.get("enabled"):
            return "Chưa bật điều khiển qua Telegram (Cài đặt → Khởi động lại & Ứng dụng → Điều khiển máy qua Telegram)."
        if (msg.get("chat") or {}).get("type", "private") != "private":
            return "🕹 Chỉ điều khiển được trong chat riêng với bot, không nhận trong nhóm."
        if cmd == "/dk":
            return self._start(rest.strip(), msg)
        if cmd == "/dk_tat":
            self.until = 0.0
            log.info("Điều khiển từ xa: tắt theo lệnh")
            return "🕹 Đã tắt điều khiển qua Telegram."
        if not self.active():
            return "🔒 Gửi /dk <mã PIN> để bật điều khiển."
        self.until = self.clock() + R.get("session_min", 10) * 60
        if self.locked():
            return ("🔒 Máy đang khóa: lệnh qua Telegram không chụp/bấm được màn hình khóa của Windows. "
                    "Mở khóa bằng RustDesk hoặc UltraViewer.")
        try:
            return self._do(cmd, rest)
        except ValueError as e:
            return f"❓ {e}"
        except RuntimeError as e:
            return f"❌ {e}"

    def callback(self, data):
        op = data.split(":")
        if op[1:2] == ["end"]:
            return self.handle("/dk_tat", "")
        if op[1:2] == ["mh"]:
            return self.handle("/mh", "")
        if op[1:2] == ["k"] and len(op) > 2:
            return self.handle("/phim", op[2])
        if op[1:2] == ["w"] and len(op) > 2:
            n = int(op[2])
            return self.handle("/cuon", f"{'len' if n > 0 else 'xuong'} {abs(n)}")
        return ""

    def _start(self, pin, msg):
        R, now = self.R(), self.clock()
        if msg.get("message_id") and pin:
            try:
                self.delete_message(msg["message_id"])        # không để mã PIN nằm lại trong lịch sử chat
            except Exception as e:  # noqa: BLE001
                log.debug("Không xóa được tin chứa PIN: %s", e)
        if not R.get("pin_hash"):
            return "Chưa đặt mã PIN (Cài đặt → Khởi động lại & Ứng dụng → Điều khiển máy qua Telegram)."
        if now < self.locked_until:
            return f"⛔ Tạm khóa điều khiển do nhập sai PIN nhiều lần, thử lại sau {time.strftime('%H:%M', time.localtime(self.locked_until))}."
        if not pin:
            if self.active():
                self.until = now + R.get("session_min", 10) * 60
                return self._shot("🕹 Phiên điều khiển đang mở")
            return "Gửi /dk <mã PIN> để bật điều khiển."
        if not check_pin(pin, R):
            self.fails = [t for t in self.fails if now - t < 3600] + [now]
            log.warning("Điều khiển từ xa: sai mã PIN (%d lần)", len(self.fails))
            if len(self.fails) >= R.get("max_fail", 5):
                self.locked_until, self.fails = now + R.get("lockout_min", 60) * 60, []
                self.notify(f"⛔{self.label()} Nhập sai mã PIN điều khiển {R.get('max_fail', 5)} lần: tạm khóa "
                            f"điều khiển qua Telegram {R.get('lockout_min', 60)} phút. Nếu không phải bạn, hãy đổi "
                            f"bot token (/revoke với BotFather) và mã PIN.")
                return "⛔ Sai mã PIN quá nhiều lần, đã tạm khóa."
            return "❌ Sai mã PIN."
        self.fails = []
        self.until = now + R.get("session_min", 10) * 60
        log.info("Điều khiển từ xa: mở phiên")
        if self.locked():
            return (f"🕹 Đã bật điều khiển đến {time.strftime('%H:%M', time.localtime(self.until))}, nhưng máy đang "
                    f"khóa: mở khóa bằng RustDesk hoặc UltraViewer trước.")
        return self._shot(f"🕹 Đã bật điều khiển đến {time.strftime('%H:%M', time.localtime(self.until))} "
                          f"(tự tắt sau {R.get('session_min', 10)} phút không dùng). Bấm ô: /c D4")

    def _do(self, cmd, rest):
        S, g = self.sys, self.grid()
        arg = rest.strip()
        if cmd == "/mh":
            return self._shot("🖥 Màn hình", g)
        if cmd in ("/c", "/cp", "/c2"):
            if not arg:
                raise ValueError("ghi ô cần bấm, vd. /c D4 hoặc /c D4 5")
            x, y, label = g.target(arg)
            S["click"](x, y, "right" if cmd == "/cp" else "left", cmd == "/c2")
            log.info("Điều khiển từ xa: %s %s (%d,%d)", cmd, label, x, y)
            what = {"/c": "Đã bấm", "/cp": "Đã bấm chuột phải", "/c2": "Đã bấm đúp"}[cmd]
            return self._after(f"🖱 {what} {label}", g)
        if cmd == "/zoom":
            if not arg:
                raise ValueError("ghi ô cần phóng to, vd. /zoom D4")
            bx, by, bw, bh = g.box(arg)
            m = max(bw, bh) / 3
            vx, vy = max(g.x, bx - m), max(g.y, by - m)
            vw, vh = min(g.x + g.w, bx + bw + m) - vx, min(g.y + g.h, by + bh + m) - vy
            label = re.sub(r"[\s.,]+", " ", arg).upper()
            data = S["render"](g, view=(vx, vy, vw, vh), zoom_box=(bx, by, bw, bh), width=min(960, self.R().get("width", 1280)),
                               quality=self.R().get("quality", 60), cursor=S["cursor"]())
            sub = label if " " in label else label + " "
            return self._photo(data, f"🔍 {label} — bấm ô con: /c {sub}5 · phóng tiếp: /zoom {sub}5")
        if cmd == "/go":
            if not rest:
                raise ValueError("ghi chữ cần gõ, vd. /go xin chao")
            S["type"](rest)
            log.info("Điều khiển từ xa: gõ %d ký tự", len(rest))
            return self._after(f"⌨️ Đã gõ {len(rest)} ký tự", g)
        if cmd == "/phim":
            combos = parse_keys(arg)
            S["press"](combos)
            log.info("Điều khiển từ xa: phím %s", arg)
            return self._after(f"⌨️ Đã nhấn {arg}", g)
        if cmd == "/cuon":
            parts = arg.lower().split()
            up = not parts or parts[0] in ("len", "lên", "up")
            n = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 3
            S["scroll"](n if up else -n)
            log.info("Điều khiển từ xa: cuộn %s %d", "lên" if up else "xuống", n)
            return self._after(f"🖱 Đã cuộn {'lên' if up else 'xuống'} {n}", g)
        return HELP

    def _after(self, caption, g):
        self.sleep(self.R().get("delay_ms", 800) / 1000)
        return self._shot(caption, g)

    def _shot(self, caption, g=None):
        g = g or self.grid()
        R = self.R()
        data = self.sys["render"](g, width=min(R.get("width", 1280), g.w), quality=R.get("quality", 60),
                                  cursor=self.sys["cursor"]())
        return self._photo(data, f"{caption} · lưới A1..{g.cell_name(g.cols - 1, g.rows - 1)}")

    def _photo(self, data, caption):
        img, mime = data
        left = max(0, int((self.until - self.clock()) / 60 + 0.5))
        try:
            self.send_photo(img, f"{caption} · còn {left} phút", markup=MARKUP, mime=mime)
        except Exception as e:  # noqa: BLE001
            return f"❌ Gửi ảnh lỗi: {e}"
        return ""
