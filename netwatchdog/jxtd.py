"""Theo dõi nhân vật trong jxtdAuto (tool auto Võ Lâm) — báo cáo và cảnh báo qua Telegram của NetWatchdog.

CHỈ ĐỌC giao diện cửa sổ jxtdAuto qua lớp trợ năng của Windows (UI Automation / MSAA):
không bấm, không gõ, không gửi thông điệp điều khiển, không đọc/ghi bộ nhớ tiến trình nào.

Mỗi dòng của bảng nhân vật (TListView) có thuộc tính MSAA "Description" dạng
"Tác vụ: Luyện công, EXP/giờ: 1.8m/h, Thu nhập: 13.0 vạn, ..., Thẻ tháng: 359h / 359h"
— đầy đủ, không bị cắt như trên màn hình. Trạng thái tick lấy từ cờ STATE_SYSTEM_CHECKED.
Cấu hình nằm ở khóa "jxtd" của config.json, trạng thái ở khóa "jxtd" của state.json.
"""
import csv
import ctypes
import ctypes.wintypes as wt
import logging
import os
import re
import struct
import sys
import time
import unicodedata
import warnings
import zlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta

log = logging.getLogger("netwatchdog")

DEFAULT_CONFIG = {
    "enabled": False,
    "window_title_prefix": "jxtdAuto",
    "window_class": "jxtdAutoWnd",
    "process_name": "jxtdAuto.exe",
    "poll_sec": 60,
    "report_min": 60,
    "report_on_start": True,
    "history_keep_days": 90,
    "cooldown_min": 30,
    "startup_grace_min": 5,
    "missing_confirm_reads": 2,
    "remind_window_missing": True,
    "char_missing": True,
    "unticked": True,
    "negative_income": True,
    "task_change": True,
    "death": True,
    "month_card": True,
    "month_card_warn_hours": 24,
    "license_warn_days": 7,
    "license_cooldown_hours": 24,
    "forget_missing_char_hours": 12,
}

# Tên cột trong jxtdAuto -> thuộc tính của Row
COLUMNS = {"Tác vụ": "task", "EXP/giờ": "exp", "Thu nhập": "income", "Ngân lượng": "money",
           "Cấp/EXP": "level", "Phù/chết": "deaths", "Thẻ tháng": "card"}

STATE_SYSTEM_SELECTED = 0x2
STATE_SYSTEM_CHECKED = 0x10


def hm(ts=None):
    return datetime.fromtimestamp(ts or time.time()).strftime("%H:%M %d/%m")


def fmt_duration(sec):
    m = int(max(0, sec) // 60)
    if m < 60:
        return f"{m} phút"
    h, m = divmod(m, 60)
    return f"{h} giờ {m} phút" if h < 24 else f"{h // 24} ngày {h % 24} giờ"


# ---------------------------------------------------------------- Đọc jxtdAuto
@dataclass
class Row:
    name: str
    checked: bool = False
    selected: bool = False
    task: str = ""
    exp: str = ""
    income: str = ""
    money: str = ""
    level: str = ""
    deaths: str = ""
    card: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def income_negative(self):
        return self.income.strip().startswith("-")

    @property
    def death_count(self):
        """Số sau dấu '/' của cột Phù/chết ("3 / 0" -> 0); None nếu không đọc được."""
        parts = self.deaths.split("/")
        try:
            return int(parts[1].strip()) if len(parts) == 2 else None
        except ValueError:
            return None

    @property
    def card_hours(self):
        """Số giờ thẻ tháng còn lại = phần trước dấu '/' ("359h / 359h" -> 359); None nếu không đọc được."""
        units = {"d": 24, "h": 1, "m": 1 / 60, "": 1}
        parts = re.findall(r"(\d+(?:\.\d+)?)\s*([dhm]?)", self.card.split("/")[0].lower())
        return sum(float(n) * units[u] for n, u in parts) if parts else None


@dataclass
class Snapshot:
    ts: float
    found: bool
    process_running: bool = False
    title: str = ""
    license_days: int = None
    rows: list = field(default_factory=list)
    error: str = ""


def parse_description(desc, headers):
    """Tách "Tác vụ: A, EXP/giờ: B, ..." theo đúng tên cột (giá trị có thể chứa dấu phẩy)."""
    cols = [h for h in headers[1:] if h] if headers else []
    if not cols:  # không đọc được header: tách thô theo ", "
        out = {}
        for part in desc.split(", "):
            k, sep, v = part.partition(": ")
            if sep:
                out[k.strip()] = v.strip()
        return out
    found, pos = [], 0
    for c in cols:
        i = desc.find(c + ": ", pos)
        if i >= 0:
            found.append((i, c))
            pos = i + len(c) + 2
    out = {}
    for n, (i, c) in enumerate(found):
        end = found[n + 1][0] if n + 1 < len(found) else len(desc)
        out[c] = desc[i + len(c) + 2:end].rstrip().rstrip(",").strip()
    return out


TASK_PROGRESS_RE = re.compile(r"\s*\(\s*\d+\s*/\s*\d+\s*\)\s*$")


def task_key(task):
    """Tên tác vụ bỏ bộ đếm tiến độ ở cuối: "NV Mặc Thạch (55 / 100)" -> "NV Mặc Thạch".
    Bộ đếm tăng liên tục khi làm nhiệm vụ, không coi là đổi tác vụ."""
    return TASK_PROGRESS_RE.sub("", task or "")


def make_row(name, desc, state, headers):
    r = Row(name=name.strip(), checked=bool(state & STATE_SYSTEM_CHECKED),
            selected=bool(state & STATE_SYSTEM_SELECTED))
    for col, val in parse_description(desc or "", headers).items():
        if col in COLUMNS:
            setattr(r, COLUMNS[col], val)
        else:
            r.extra[col] = val
    return r


def process_running(exe_name):
    """Liệt kê tiến trình bằng Toolhelp32 (chỉ đọc tên, không mở tiến trình)."""
    class PE(ctypes.Structure):
        _fields_ = [("dwSize", wt.DWORD), ("cntUsage", wt.DWORD), ("th32ProcessID", wt.DWORD),
                    ("th32DefaultHeapID", ctypes.c_void_p), ("th32ModuleID", wt.DWORD),
                    ("cntThreads", wt.DWORD), ("th32ParentProcessID", wt.DWORD),
                    ("pcPriClassBase", ctypes.c_long), ("dwFlags", wt.DWORD),
                    ("szExeFile", ctypes.c_wchar * 260)]
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateToolhelp32Snapshot.restype = wt.HANDLE
    snap = k32.CreateToolhelp32Snapshot(0x2, 0)
    if not snap or snap == wt.HANDLE(-1).value:
        return False
    try:
        e = PE()
        e.dwSize = ctypes.sizeof(PE)
        ok = k32.Process32FirstW(snap, ctypes.byref(e))
        while ok:
            if e.szExeFile.lower() == exe_name.lower():
                return True
            ok = k32.Process32NextW(snap, ctypes.byref(e))
        return False
    finally:
        k32.CloseHandle(snap)


def com_init():
    """Khởi tạo COM cho luồng hiện tại (UI Automation cần; gọi lại nhiều lần không sao)."""
    if not hasattr(sys, "coinit_flags"):
        sys.coinit_flags = 0  # COINIT_MULTITHREADED — UIA khuyến nghị; cũng là mặc định pywinauto muốn
    warnings.filterwarnings("ignore", message="Apply externally defined coinit_flags")
    try:
        import comtypes
        comtypes.CoInitializeEx()
    except OSError:
        pass


def find_window(J):
    """HWND cửa sổ chính của jxtdAuto (theo đầu tiêu đề + tên lớp), hoặc None."""
    import win32gui
    prefix, cls = J["window_title_prefix"], J.get("window_class") or ""
    hits = []

    def cb(h, _):
        try:
            if win32gui.GetWindowText(h).startswith(prefix) and (not cls or win32gui.GetClassName(h) == cls):
                hits.append(h)
        except Exception:  # noqa: BLE001
            pass
        return True
    win32gui.EnumWindows(cb, None)
    return hits[0] if hits else None


def read(J):
    """Đọc cửa sổ jxtdAuto một lần -> Snapshot. J = cfg["jxtd"]."""
    import win32gui
    snap = Snapshot(ts=time.time(), found=False)
    try:
        hwnd = find_window(J)
        if not hwnd:
            snap.process_running = process_running(J["process_name"])
            return snap
        snap.process_running = True
        snap.title = win32gui.GetWindowText(hwnd)
        m = re.search(r"Còn lại\s*(\d+)", snap.title)
        snap.license_days = int(m.group(1)) if m else None
        lv = win32gui.FindWindowEx(hwnd, 0, "TListView", None)
        if not lv:
            snap.error = "không thấy bảng nhân vật (TListView)"
            return snap

        from pywinauto.controls.uiawrapper import UIAWrapper
        from pywinauto.uia_element_info import UIAElementInfo
        headers, rows = [], []
        for c in UIAWrapper(UIAElementInfo(lv)).children():
            ct = c.element_info.control_type
            if ct == "Header":
                headers = [h.element_info.name for h in c.children()]
            elif ct in ("CheckBox", "ListItem"):
                lp = c.legacy_properties()
                rows.append((lp.get("Name") or c.element_info.name, lp.get("Description") or "",
                             int(lp.get("State") or 0)))
        snap.rows = [make_row(n, d, s, headers) for n, d, s in rows]
        snap.found = True
    except Exception as e:  # noqa: BLE001
        snap.error = f"{type(e).__name__}: {e}"
    return snap


# ---------------------------------------------------------------- Định dạng tin nhắn
def fmt_row(r):
    mark = "✅" if r.checked else "⬜"
    return (f"{mark} {r.name}\n"
            f"   {r.task or '?'} · {r.exp or '?'}\n"
            f"   💰 {r.income or '?'} · NL {r.money or '?'}\n"
            f"   📈 {r.level or '?'} · Phù/chết {r.deaths or '?'}" +
            (f"\n   🎫 Thẻ tháng {r.card}" if r.card else ""))


def header_line(cfg, icon, text):
    name = (cfg.get("machine_name") or "").strip()
    return f"{icon} {text}" + (f" [{name}]" if name else "")


def build_report(cfg, snap, missing=()):
    lines = [header_line(cfg, "🎮", f"jxtdAuto · {hm(snap.ts)}")]
    if not snap.found:
        why = "tiến trình vẫn chạy nhưng không đọc được" if snap.process_running else "jxtdAuto đã tắt"
        lines.append(f"🔴 Không thấy cửa sổ jxtdAuto ({why})")
        return "\n".join(lines)
    if snap.license_days is not None:
        lines.append(f"🔑 License còn {snap.license_days} ngày")
    lines.append(f"👥 {len(snap.rows)} nhân vật ({sum(r.checked for r in snap.rows)} đang tick)")
    for r in snap.rows:
        lines += ["", fmt_row(r)]
    if missing:
        lines += ["", "❓ Mất khỏi danh sách: " + ", ".join(missing)]
    return "\n".join(lines)


def summary(snap):
    """Một dòng tóm tắt cho báo cáo tình trạng máy."""
    if not snap.found:
        return "🔴 không thấy cửa sổ" + ("" if snap.process_running else " (đã tắt)")
    neg = sum(r.income_negative for r in snap.rows)
    return (f"{len(snap.rows)} nhân vật, {sum(r.checked for r in snap.rows)} đang tick"
            + (f", {neg} thu nhập âm" if neg else ""))


def norm(s):
    """Bỏ dấu tiếng Việt + chữ thường, để tìm tên nhân vật không cần gõ dấu."""
    s = unicodedata.normalize("NFD", s.replace("đ", "d").replace("Đ", "D"))
    return "".join(ch for ch in s if unicodedata.category(ch) != "Mn").lower()


def filter_rows(rows, query):
    """Nhân vật có tên chứa `query` (không phân biệt dấu/hoa thường)."""
    q = norm(query.strip())
    return [r for r in rows if q in norm(r.name)]


def build_short(cfg, snap, missing=()):
    """Bản rút gọn: mỗi nhân vật một dòng."""
    lines = [header_line(cfg, "🎮", f"jxtdAuto · {hm(snap.ts)}")]
    if not snap.found:
        return build_report(cfg, snap)
    if snap.license_days is not None:
        lines[0] += f" · license {snap.license_days} ngày"
    for r in snap.rows:
        lines.append(f"{'✅' if r.checked else '⬜'} {r.name} · {r.task or '?'} · {r.exp or '?'} · {r.income or '?'}")
    if missing:
        lines.append("❓ Mất khỏi danh sách: " + ", ".join(missing))
    return "\n".join(lines)


# ---------------------------------------------------------------- Tổng kết trong ngày (từ CSV)
LEVEL_RE = re.compile(r"Lv\s*(\d+)\s*\(([\d.]+)%\)")


def _level(text):
    m = LEVEL_RE.search(text or "")
    return (int(m.group(1)), float(m.group(2))) if m else None


def _deaths(text):
    return Row(name="", deaths=text or "").death_count


def level_change(a, b):
    """"Lv109 (39.6%)" -> "Lv109 (41.0%)" thành "Lv109 +1.4%" / "Lv109 → Lv110 (+1 cấp)"; "" nếu không đọc được."""
    la, lb = _level(a), _level(b)
    if not (la and lb):
        return ""
    if la[0] == lb[0]:
        return f"Lv{lb[0]} {lb[1] - la[1]:+.1f}%"
    return f"Lv{la[0]} → Lv{lb[0]} ({lb[0] - la[0]:+d} cấp)"


def day_summary(cfg, folder, day=None):
    """Tổng kết một ngày cho từng nhân vật: EXP/cấp, thu nhập, ngân lượng, số lần chết, đổi tác vụ, gián đoạn."""
    day = day or datetime.now()
    path = os.path.join(folder, day.strftime("%Y-%m-%d") + ".csv")
    try:
        with open(path, encoding="utf-8-sig", newline="") as f:
            data = list(csv.DictReader(f))
    except FileNotFoundError:
        return f"📅 Chưa có dữ liệu jxtdAuto ngày {day:%d/%m} (đã bật theo dõi trong Cài đặt chưa?)."
    if not data:
        return f"📅 Chưa có dữ liệu jxtdAuto ngày {day:%d/%m}."
    chars, gaps = {}, 0
    for d in data:
        name = d.get("nhan_vat") or ""
        if not name:
            gaps += "không thấy" in (d.get("ghi_chu") or "")
            continue
        c = chars.get(name)
        if c is None:
            chars[name] = c = {"first": d, "tasks": 0, "deaths": 0}
        else:
            if task_key(d.get("tac_vu")) != task_key(c["last"].get("tac_vu")):
                c["tasks"] += 1
            d0, d1 = _deaths(c["last"].get("phu_chet")), _deaths(d.get("phu_chet"))
            if d0 is not None and d1 is not None and d1 > d0:
                c["deaths"] += d1 - d0
        c["last"] = d

    t0, t1 = data[0]["thoi_gian"][11:16], data[-1]["thoi_gian"][11:16]
    head = f"📅 jxtdAuto ngày {day:%d/%m} ({t0} → {t1})"
    name = (cfg.get("machine_name") or "").strip()
    lines = [head + (f" [{name}]" if name else "")]
    if gaps:
        poll = cfg.get("jxtd", {}).get("poll_sec", 60)
        lines.append(f"🔴 Không thấy jxtdAuto ≈ {fmt_duration(gaps * poll)}")
    for n, c in chars.items():
        a, b = c["first"], c["last"]
        la, lb = _level(a.get("cap_exp")), _level(b.get("cap_exp"))
        if la and lb:
            if lb[0] == la[0]:
                exp = f"Lv{lb[0]}: {la[1]:g}% → {lb[1]:g}% ({lb[1] - la[1]:+.1f}%)"
            else:
                exp = f"Lv{la[0]} {la[1]:g}% → Lv{lb[0]} {lb[1]:g}% ({lb[0] - la[0]:+d} cấp)"
        else:
            exp = f"{a.get('cap_exp') or '?'} → {b.get('cap_exp') or '?'}"
        mark = "✅" if b.get("tick") == "1" else "⬜"
        lines += ["", f"{mark} {n}",
                  f"   📈 {exp}",
                  f"   💰 {a.get('thu_nhap') or '?'} → {b.get('thu_nhap') or '?'}",
                  f"   🏦 NL {a.get('ngan_luong') or '?'} → {b.get('ngan_luong') or '?'}",
                  f"   💀 chết {c['deaths']} · 🔄 đổi tác vụ {c['tasks']} · đang: {b.get('tac_vu') or '?'}"]
    return "\n".join(lines)


# ---------------------------------------------------------------- Thống kê EXP / tiền vạn (từ CSV)
NUM_RE = re.compile(r"(-?\d[\d,]*(?:\.\d+)?)\s*([^\d\s/]*)")
EXP_UNITS = {"": 1, "k": 1e3, "m": 1e6, "b": 1e9, "t": 1e12}
VAN_UNITS = {"vạn": 1, "v": 1, "lượng": 1e-4, "": 1e-4, "ức": 1e4, "triệu": 100, "tr": 100}


def _num(text, units):
    m = NUM_RE.search(text or "")
    if not m:
        return None
    unit = unicodedata.normalize("NFC", m.group(2)).lower()
    if unit not in units:
        return None
    return float(m.group(1).replace(",", "")) * units[unit]


def parse_exp_rate(text):
    """"1.8m/h" -> 1800000.0 (EXP mỗi giờ); None nếu không đọc được."""
    return _num(text, EXP_UNITS)


def parse_van(text):
    """Tiền quy ra vạn: "225.0 vạn" -> 225.0, "-4214 lượng" -> -0.4214; None nếu không đọc được."""
    return _num(text, VAN_UNITS)


def fmt_exp(v):
    a = abs(v)
    for div, suf in ((1e9, "b"), (1e6, "m"), (1e3, "k")):
        if a >= div:
            return f"{v / div:.2f}".rstrip("0").rstrip(".") + suf
    return f"{v:.0f}"


def fmt_van(v, sign=True):
    s = f"{v:+.1f}" if sign else f"{v:.1f}"
    return "0.0" if s in ("+0.0", "-0.0") else s


def history_days(folder):
    """Các ngày có file lịch sử, mới nhất trước."""
    try:
        files = os.listdir(folder)
    except OSError:
        return []
    days = []
    for fn in files:
        try:
            days.append(datetime.strptime(fn[:10], "%Y-%m-%d").date())
        except ValueError:
            pass
    return sorted(set(days), reverse=True)


def load_history(folder, start, end):
    """Các dòng CSV (có tên nhân vật) từ ngày `start` đến `end` (date, gồm cả 2 đầu), kèm khóa "t" = datetime."""
    out, d = [], start
    while d <= end:
        try:
            with open(os.path.join(folder, d.strftime("%Y-%m-%d") + ".csv"), encoding="utf-8-sig", newline="") as f:
                for rec in csv.DictReader(f):
                    if not rec.get("nhan_vat"):
                        continue
                    try:
                        rec["t"] = datetime.strptime(rec["thoi_gian"], "%Y-%m-%d %H:%M:%S")
                    except (KeyError, TypeError, ValueError):
                        continue
                    out.append(rec)
        except OSError:
            pass
        d += timedelta(days=1)
    out.sort(key=lambda r: r["t"])
    return out


def match_names(names, query):
    """Lọc tên theo chuỗi "tên1, tên2" (không cần dấu); chuỗi rỗng = tất cả."""
    raw = [q.strip() for q in (query or "").split(",") if q.strip()]
    exact = [q for q in raw if q in names]  # tên đầy đủ (từ nút bấm) thì khớp đúng tên đó
    qs = [norm(q) for q in raw if q not in names]
    return [n for n in names if not raw or n in exact or any(q in norm(n) for q in qs)]


def compute_stats(records, names=None, by="hour", start=None, end=None, poll_sec=60):
    """Gộp EXP và tiền vạn kiếm được theo giờ (by="hour") hoặc theo ngày (by="day").

    - EXP: cộng dồn EXP/giờ × thời gian giữa 2 lần đọc liên tiếp (bỏ qua khoảng gián đoạn dài) — ước tính.
    - Tiền: chênh lệch Ngân lượng giữa 2 lần đọc liên tiếp, quy ra vạn (tiêu/chuyển tiền cũng bị trừ).
    - names: danh sách nhân vật được chọn (None = tất cả).
    Trả về dict: buckets [(datetime, {"exp", "van", "chars"})], chars {tên: {...}}, total_exp, total_van, active_hours.
    """
    gap_max = max(3 * poll_sec, 300)
    sel = None if names is None else set(names)
    trunc = (lambda t: t.replace(minute=0, second=0, microsecond=0)) if by == "hour" else \
        (lambda t: t.replace(hour=0, minute=0, second=0, microsecond=0))
    buckets, chars, prev, active = {}, {}, {}, set()
    for rec in records:
        name, t = rec["nhan_vat"], rec["t"]
        if sel is not None and name not in sel:
            continue
        rate, van = parse_exp_rate(rec.get("exp_gio")), parse_van(rec.get("ngan_luong"))
        c = chars.get(name)
        if c is None:
            chars[name] = c = {"exp": 0.0, "van": 0.0, "seconds": 0.0,
                               "level_first": rec.get("cap_exp") or ""}
        c.update(level_last=rec.get("cap_exp") or c.get("level_last", ""), task=rec.get("tac_vu") or "",
                 tick=rec.get("tick") == "1", van_last=van if van is not None else c.get("van_last"))
        key = trunc(t)
        b = buckets.setdefault(key, {"exp": 0.0, "van": 0.0, "chars": set()})
        b["chars"].add(name)
        active.add(t.replace(minute=0, second=0, microsecond=0))
        p = prev.get(name)
        if p:
            dt = (t - p["t"]).total_seconds()
            if 0 < dt <= gap_max:
                c["seconds"] += dt
                if rate:
                    e = rate * dt / 3600
                    c["exp"] += e
                    b["exp"] += e
            if van is not None and p["van"] is not None:
                c["van"] += van - p["van"]
                b["van"] += van - p["van"]
        prev[name] = {"t": t, "van": van if van is not None else (p or {}).get("van")}

    # Điền các mốc trống để biểu đồ liền mạch
    keys = sorted(buckets)
    if by == "day" and start and end:
        lo, hi = datetime.combine(start, datetime.min.time()), datetime.combine(end, datetime.min.time())
    elif keys:
        lo, hi = keys[0], keys[-1]
    else:
        lo = hi = None
    step = timedelta(hours=1) if by == "hour" else timedelta(days=1)
    out, k = [], lo
    while k is not None and k <= hi:
        out.append((k, buckets.get(k, {"exp": 0.0, "van": 0.0, "chars": set()})))
        k += step
    return {"buckets": out, "chars": chars, "by": by,
            "total_exp": sum(c["exp"] for c in chars.values()),
            "total_van": sum(c["van"] for c in chars.values()),
            "active_hours": len(active)}


def stats_range(by, day=None, days=7):
    """(start, end) dạng date: theo giờ = một ngày, theo ngày = `days` ngày gần nhất."""
    day = day or datetime.now().date()
    return (day, day) if by == "hour" else (day - timedelta(days=days - 1), day)


def build_stats(cfg, folder, by="hour", query="", day=None, days=7):
    """Bảng thống kê cho Telegram (HTML). Trả về (text, tên các nhân vật có dữ liệu trong khoảng)."""
    import html
    start, end = stats_range(by, day, days)
    records = load_history(folder, start, end)
    all_names = sorted({r["nhan_vat"] for r in records})
    title = (f"jxtdAuto theo giờ · {start:%d/%m}" if by == "hour"
             else f"jxtdAuto theo ngày · {start:%d/%m} → {end:%d/%m}")
    head = html.escape(header_line(cfg, "📊" if by == "hour" else "📈", title))
    if not records:
        return head + "\nChưa có dữ liệu (đã bật theo dõi jxtdAuto trong Cài đặt chưa?).", all_names
    names = match_names(all_names, query)
    if not names:
        return head + "\n" + html.escape(f"Không thấy nhân vật “{query}”. Có: " + ", ".join(all_names)), all_names
    s = compute_stats(records, names, by, start, end, cfg.get("jxtd", {}).get("poll_sec", 60))
    who = f"👤 {names[0]}" if len(names) == 1 else (
        f"👥 Tất cả ({len(names)} nhân vật)" if len(names) == len(all_names) else "👥 " + ", ".join(names))
    rows = [("Giờ" if by == "hour" else "Ngày", "EXP", "Vạn")]
    for k, b in s["buckets"]:
        if by == "hour" and not b["chars"]:
            continue
        rows.append((f"{k:%H}h" if by == "hour" else f"{k:%d/%m}", fmt_exp(b["exp"]) if b["chars"] else "-",
                     fmt_van(b["van"]) if b["chars"] else "-"))
    rows.append(("Tổng", fmt_exp(s["total_exp"]), fmt_van(s["total_van"])))
    w = [max(len(r[i]) for r in rows) for i in range(3)]
    table = "\n".join(f"{a:<{w[0]}}  {b:>{w[1]}}  {c:>{w[2]}}" for a, b, c in rows)
    lines = [head, html.escape(who), f"<pre>{html.escape(table)}</pre>"]
    if s["active_hours"]:
        lines.append(f"⏱ TB mỗi giờ: {fmt_exp(s['total_exp'] / s['active_hours'])} EXP · "
                     f"{fmt_van(s['total_van'] / s['active_hours'])} vạn")
    if len(names) > 1:
        lines.append("")
        for n, c in sorted(s["chars"].items(), key=lambda kv: -kv[1]["exp"]):
            lc = level_change(c["level_first"], c["level_last"])
            lines.append(html.escape(f"{'✅' if c['tick'] else '⬜'} {n}: {fmt_exp(c['exp'])} EXP · "
                                     f"{fmt_van(c['van'])} vạn" + (f" · {lc}" if lc else "")))
    else:
        c = s["chars"].get(names[0])
        if c:
            lc = level_change(c["level_first"], c["level_last"])
            lines.append(html.escape(f"📈 {c['level_first'] or '?'} → {c['level_last'] or '?'}"
                                     + (f" ({lc})" if lc else "")))
            lines.append(html.escape(f"🏦 NL hiện tại: {fmt_van(c['van_last'], sign=False)} vạn · đang: {c['task'] or '?'}")
                         if c.get("van_last") is not None else html.escape(f"Đang: {c['task'] or '?'}"))
    lines.append("<i>EXP ước tính từ EXP/giờ; tiền = chênh lệch Ngân lượng.</i>")
    return "\n".join(lines), all_names


# ---------------------------------------------------------------- Chụp cửa sổ
def encode_png(width, height, bgrx):
    """Ảnh 32 bit BGRX (hàng từ trên xuống) -> PNG RGB, chỉ dùng zlib."""
    stride, raw = width * 4, bytearray()
    for y in range(height):
        row = bgrx[y * stride:(y + 1) * stride]
        rgb = bytearray(width * 3)
        rgb[0::3], rgb[1::3], rgb[2::3] = row[2::4], row[1::4], row[0::4]
        raw += b"\x00" + rgb

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(raw), 6)) + chunk(b"IEND", b""))


def capture_png(J):
    """Chụp cửa sổ jxtdAuto bằng PrintWindow (chụp thụ động, chụp được cả khi bị che).
    Trả về (png_bytes, "") hoặc (None, lý do)."""
    import win32gui
    import win32ui
    hwnd = find_window(J)
    if not hwnd:
        return None, "Không thấy cửa sổ jxtdAuto."
    if win32gui.IsIconic(hwnd):
        return None, "Cửa sổ jxtdAuto đang thu nhỏ (minimize) nên không chụp được. Mở lại cửa sổ là được (bị cửa sổ khác che cũng không sao)."
    user32 = ctypes.WinDLL("user32")
    set_ctx = getattr(user32, "SetThreadDpiAwarenessContext", None)
    old = None
    if set_ctx:  # chụp đúng kích thước thật trên màn hình có scale (Windows 10 1607+)
        set_ctx.restype, set_ctx.argtypes = ctypes.c_void_p, [ctypes.c_void_p]
        old = set_ctx(ctypes.c_void_p(-4))  # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
    hdc = src = mem = bmp = None
    try:
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        w, h = right - left, bottom - top
        hdc = win32gui.GetWindowDC(hwnd)
        src = win32ui.CreateDCFromHandle(hdc)
        mem = src.CreateCompatibleDC()
        bmp = win32ui.CreateBitmap()
        bmp.CreateCompatibleBitmap(src, w, h)
        mem.SelectObject(bmp)
        if not user32.PrintWindow(hwnd, mem.GetSafeHdc(), 2):  # PW_RENDERFULLCONTENT
            return None, "PrintWindow thất bại."
        bits = bmp.GetBitmapBits(True)
        if len(bits) != w * h * 4:
            return None, f"Màn hình không ở chế độ màu 32 bit ({len(bits)} byte cho {w}x{h})."
        return encode_png(w, h, bits), ""
    except Exception as e:  # noqa: BLE001
        return None, f"Lỗi chụp cửa sổ: {type(e).__name__}: {e}"
    finally:
        if mem:
            mem.DeleteDC()
        if src:
            src.DeleteDC()
        if hdc:
            win32gui.ReleaseDC(hwnd, hdc)
        if bmp:
            win32gui.DeleteObject(bmp.GetHandle())
        if old:
            set_ctx(old)


# ---------------------------------------------------------------- Lịch sử CSV
CSV_FIELDS = ["thoi_gian", "nhan_vat", "tick", "dang_chon", "tac_vu", "exp_gio", "thu_nhap",
              "ngan_luong", "cap_exp", "phu_chet", "the_thang", "ghi_chu"]


def write_history(snap, folder, keep_days):
    os.makedirs(folder, exist_ok=True)
    day = datetime.fromtimestamp(snap.ts)
    path = os.path.join(folder, day.strftime("%Y-%m-%d") + ".csv")
    new = not os.path.exists(path)
    t = day.strftime("%Y-%m-%d %H:%M:%S")
    with open(path, "a", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        if new:
            w.writerow(CSV_FIELDS)
        if not snap.found:
            w.writerow([t] + [""] * (len(CSV_FIELDS) - 2) +
                       ["không thấy jxtdAuto" + (f" ({snap.error})" if snap.error else "")])
        for r in snap.rows:
            w.writerow([t, r.name, int(r.checked), int(r.selected), r.task, r.exp, r.income,
                        r.money, r.level, r.deaths, r.card, ""])
    if new:  # sang ngày mới thì xóa file quá hạn
        cutoff = (day - timedelta(days=keep_days)).strftime("%Y-%m-%d")
        for fn in os.listdir(folder):
            if fn.endswith(".csv") and fn[:10] < cutoff:
                try:
                    os.remove(os.path.join(folder, fn))
                except OSError:
                    pass


# ---------------------------------------------------------------- Cảnh báo
class Alerts:
    """Cảnh báo theo điều kiện, chống spam.

    - Khi điều kiện xuất hiện: gửi ngay, trừ khi cùng loại vừa gửi trong `cooldown` giây
      (khi đó chờ hết thời gian chờ, nếu vẫn còn lỗi mới gửi).
    - Khi hết lỗi: gửi tin "trở lại bình thường" (chỉ khi đã báo lỗi đó).
    - `remind`: nhắc lại mỗi `cooldown` giây khi lỗi vẫn còn.
    """

    def __init__(self, st, send):
        self.st = st
        self.send = send

    def update(self, key, active, onset, recover, cooldown, remind=False, now=None):
        now = now or time.time()
        a = self.st.setdefault(key, {"active": False, "since": 0, "sent": False, "last_sent": 0})
        if active:
            if not a["active"]:
                a.update(active=True, since=now, sent=False)
            if not a["sent"] and now - a["last_sent"] >= cooldown:
                self.send(onset() if callable(onset) else onset)
                a.update(sent=True, last_sent=now)
            elif a["sent"] and remind and now - a["last_sent"] >= cooldown:
                self.send(f"🔁 Vẫn còn ({fmt_duration(now - a['since'])}):\n" +
                          (onset() if callable(onset) else onset))
                a["last_sent"] = now
        elif a["active"]:
            if a["sent"] and recover:
                dur = fmt_duration(now - a["since"])
                self.send(recover(dur) if callable(recover) else recover)
            a.update(active=False, sent=False)

    def event(self, key, cooldown, now=None):
        """Sự kiện một lần (đổi tác vụ, chết): True nếu được phép gửi lúc này."""
        now = now or time.time()
        a = self.st.setdefault(key, {"active": False, "since": 0, "sent": False, "last_sent": 0})
        if now - a["last_sent"] >= cooldown:
            a["last_sent"] = now
            return True
        return False


# ---------------------------------------------------------------- Bộ theo dõi
class JxMonitor:
    """get_cfg() trả về config đầy đủ của NetWatchdog; state là netwatchdog.State; send(text) gửi Telegram."""

    def __init__(self, get_cfg, state, send, history_dir, reader=None):
        self.get_cfg = get_cfg
        self.state = state
        self.send = send
        self.history_dir = history_dir
        self.reader = reader or read
        self.started = time.time()
        self.misses = 0
        self.reported_this_run = False
        self.last_snap = None
        self.last_missing = []

    def report_text(self, query=""):
        """Bảng đầy đủ (lệnh /jx); có `query` thì chỉ các nhân vật khớp tên."""
        if not self.last_snap:
            return "🎮 Chưa đọc được jxtdAuto lần nào, chờ chút."
        snap, cfg = self.last_snap, self.get_cfg()
        if not query.strip() or not snap.found:
            return build_report(cfg, snap, self.last_missing)
        rows = filter_rows(snap.rows, query)
        if not rows:
            return f"Không thấy nhân vật “{query.strip()}”. Đang có: " + ", ".join(r.name for r in snap.rows)
        return "\n\n".join([header_line(cfg, "🎮", f"jxtdAuto · {hm(snap.ts)}")] + [fmt_row(r) for r in rows])

    def short_text(self):
        """Bản rút gọn (lệnh /jx_gon)."""
        if not self.last_snap:
            return "🎮 Chưa đọc được jxtdAuto lần nào, chờ chút."
        return build_short(self.get_cfg(), self.last_snap, self.last_missing)

    def tick(self, now=None):
        now = now or time.time()
        cfg = self.get_cfg()
        J = cfg["jxtd"]
        snap = self.reader(J)
        snap.ts = now
        if snap.error:
            log.warning("Đọc jxtdAuto lỗi: %s", snap.error)
        try:
            write_history(snap, self.history_dir, J["history_keep_days"])
        except OSError as e:
            log.error("Không ghi được lịch sử jxtdAuto: %s", e)

        msgs = []
        with self.state.lock:
            st = self.state.data.setdefault("jxtd", {})
            alerts = Alerts(st.setdefault("alerts", {}), msgs.append)
            cd = J["cooldown_min"] * 60
            self.misses = 0 if snap.found else self.misses + 1
            in_grace = now - self.started < J["startup_grace_min"] * 60
            alerts.update(
                "window", self.misses >= J["missing_confirm_reads"] and not in_grace,
                lambda: (header_line(cfg, "🔴", "Không thấy cửa sổ jxtdAuto") + "\n" +
                         ("Tiến trình vẫn chạy nhưng không đọc được bảng." if snap.process_running
                          else "jxtdAuto đã bị tắt.") + f"\n🕒 {hm(now)}"),
                lambda dur: header_line(cfg, "🟢", f"jxtdAuto đã hoạt động lại (gián đoạn {dur})"),
                cd, remind=J["remind_window_missing"], now=now)

            missing = []
            if snap.found:
                missing = self._check_chars(cfg, st, alerts, snap, now, cd)
                if snap.license_days is not None:
                    alerts.update(
                        "license", snap.license_days <= J["license_warn_days"],
                        header_line(cfg, "🔑", f"License jxtdAuto chỉ còn {snap.license_days} ngày"),
                        header_line(cfg, "🟢", f"License jxtdAuto đã gia hạn: còn {snap.license_days} ngày"),
                        J["license_cooldown_hours"] * 3600, remind=True, now=now)
            self.last_snap, self.last_missing = snap, missing
            st["summary"] = f"{summary(snap)} ({hm(now)})"

            # Lúc mới khởi động chờ jxtdAuto lên (hoặc hết thời gian ân hạn) rồi mới báo cáo
            if snap.found or not in_grace:
                first = J["report_on_start"] and not self.reported_this_run
                due = J["report_min"] > 0 and now - st.get("last_report", 0) >= J["report_min"] * 60
                if first or due:
                    msgs.append(build_report(cfg, snap, missing))
                    st["last_report"] = now
                    self.reported_this_run = True
            self.state.save()
        for m in msgs:  # gửi ngoài khóa state (gửi có thể chậm khi mạng kém)
            self.send(m)
        return snap

    def _check_chars(self, cfg, st, alerts, snap, now, cd):
        J = cfg["jxtd"]
        chars = st.setdefault("chars", {})
        present = {r.name: r for r in snap.rows}
        for r in snap.rows:
            c = chars.setdefault(r.name, {"task": r.task, "deaths": r.death_count})
            c["last_seen"] = now
            if J["char_missing"]:
                alerts.update(f"missing:{r.name}", False, "", lambda d, r=r: header_line(
                    cfg, "🟢", f"{r.name} đã trở lại danh sách (vắng {d})") + "\n\n" + fmt_row(r), cd, now=now)
            if J["unticked"]:
                alerts.update(
                    f"unticked:{r.name}", not r.checked,
                    lambda r=r: header_line(cfg, "⬜", f"{r.name} bị bỏ tick") + "\n\n" + fmt_row(r),
                    lambda d, r=r: header_line(cfg, "🟢", f"{r.name} đã được tick lại (sau {d})"),
                    cd, now=now)
            if J["negative_income"]:
                alerts.update(
                    f"income:{r.name}", r.income_negative,
                    lambda r=r: header_line(cfg, "📉", f"{r.name} thu nhập âm: {r.income}") + "\n\n" + fmt_row(r),
                    lambda d, r=r: header_line(cfg, "🟢", f"{r.name} thu nhập hết âm: {r.income}"),
                    cd, now=now)
            if J["task_change"] and task_key(r.task) != task_key(c.get("task")) and alerts.event(
                    f"task:{r.name}", cd, now):
                alerts.send(header_line(cfg, "🔄", f"{r.name} đổi tác vụ") +
                            f"\n{c.get('task') or '?'} → {r.task or '?'}\n\n" + fmt_row(r))
                c["task"] = r.task
            dc = r.death_count
            if dc is not None:
                old = c.get("deaths")
                if old is None or dc < old:  # lần đầu / bộ đếm reset
                    c["deaths"] = dc
                elif dc > old and not J["death"]:
                    c["deaths"] = dc
                elif dc > old and alerts.event(f"death:{r.name}", cd, now):
                    alerts.send(header_line(cfg, "💀", f"{r.name} chết thêm {dc - old} lần (tổng {dc})")
                                + "\n\n" + fmt_row(r))
                    c["deaths"] = dc
            if J["month_card"]:
                self._check_card(cfg, alerts, r, now, cd)

        missing = []
        forget = J["forget_missing_char_hours"] * 3600
        for name in list(chars):
            if name in present:
                continue
            gone_for = now - chars[name].get("last_seen", now)
            if gone_for > forget:
                log.info("jxtdAuto: ngừng theo dõi %s (vắng quá %s)", name, fmt_duration(gone_for))
                del chars[name]
                alerts.st.pop(f"missing:{name}", None)
                continue
            missing.append(name)
            if J["char_missing"]:
                alerts.update(f"missing:{name}", True,
                              lambda n=name: header_line(cfg, "❓", f"{n} biến khỏi danh sách nhân vật"),
                              "", cd, now=now)
        return missing

    def _check_card(self, cfg, alerts, r, now, cd):
        """Thẻ tháng: "sắp hết" khi còn <= ngưỡng giờ, thêm "đã hết" khi về 0, "đã gia hạn" khi tăng lại."""
        h = r.card_hours
        if h is None:
            return
        low_key = f"card:{r.name}"
        was_active = alerts.st.get(low_key, {}).get("active", False)
        alerts.update(
            low_key, h <= cfg["jxtd"]["month_card_warn_hours"],
            lambda: header_line(cfg, "⛔" if h <= 0 else "🎫",
                                f"{r.name} đã HẾT thẻ tháng" if h <= 0 else f"{r.name} thẻ tháng sắp hết: còn {h:g}h")
            + "\n\n" + fmt_row(r),
            lambda d: header_line(cfg, "🟢", f"{r.name} đã gia hạn thẻ tháng: {r.card}"),
            cd, now=now)
        low = alerts.st[low_key]
        if low["active"] and not was_active:
            low["onset_hours"] = h
        # đã báo "sắp hết" từ trước rồi mới về 0 -> báo thêm "đã hết"; gia hạn thì tin ở trên lo
        alerts.update(
            f"card_out:{r.name}", h <= 0 and low.get("onset_hours", 0) > 0,
            lambda: header_line(cfg, "⛔", f"{r.name} đã HẾT thẻ tháng") + "\n\n" + fmt_row(r),
            "", cd, now=now)
