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
import sys
import time
import warnings
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


def read(J):
    """Đọc cửa sổ jxtdAuto một lần -> Snapshot. J = cfg["jxtd"]."""
    import win32gui
    snap = Snapshot(ts=time.time(), found=False)
    try:
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
        if not hits:
            snap.process_running = process_running(J["process_name"])
            return snap
        hwnd = hits[0]
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

    def report_text(self):
        if not self.last_snap:
            return "🎮 Chưa đọc được jxtdAuto lần nào, chờ chút."
        return build_report(self.get_cfg(), self.last_snap, self.last_missing)

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
            if J["task_change"] and r.task != c.get("task") and alerts.event(f"task:{r.name}", cd, now):
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
