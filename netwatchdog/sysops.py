"""Thao tác hệ thống Windows cho NetWatchdog: card mạng, Wi-Fi, khởi động lại máy, mở ứng dụng.

Dùng netsh + PowerShell có sẵn trên Windows 10/11. Bật DRY_RUN = True để chỉ ghi log
mà không thực hiện hành động thật (dùng khi chạy thử).
"""
import ctypes
import json
import logging
import os
import re
import shutil
import socket
import subprocess
import time

try:
    import psutil
except ImportError:  # psutil là tùy chọn (CPU/RAM, kiểm tra app đang chạy)
    psutil = None

log = logging.getLogger("netwatchdog")

IS_WIN = os.name == "nt"
NO_WINDOW = 0x08000000                 # CREATE_NO_WINDOW: không bật cửa sổ console
DETACHED = 0x00000008 | 0x00000200     # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
DRY_RUN = False


def run(cmd, timeout=60, encoding="oem"):
    """Chạy lệnh, trả về (returncode, output). Không bao giờ ném lỗi.

    netsh in ra theo code page OEM nên mặc định giải mã "oem".
    """
    try:
        p = subprocess.run(cmd, capture_output=True, encoding=encoding, errors="replace",
                           timeout=timeout, creationflags=NO_WINDOW if IS_WIN else 0)
        return p.returncode, ((p.stdout or "") + (p.stderr or "")).strip()
    except Exception as e:  # noqa: BLE001
        return -1, str(e)


def ps_quote(s):
    return "'" + str(s).replace("'", "''") + "'"


def powershell(script, timeout=90):
    script = ("[Console]::OutputEncoding=[Text.Encoding]::UTF8; "
              "$ProgressPreference='SilentlyContinue'; " + script)
    return run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                "-Command", script], timeout=timeout, encoding="utf-8")


def _dry(action):
    log.warning("[DRY-RUN] Bỏ qua: %s", action)
    return True, "dry-run"


# ---------------------------------------------------------------- Card mạng
def parse_adapters_json(out):
    out = (out or "").strip()
    start = min([i for i in (out.find("["), out.find("{")) if i >= 0], default=-1)
    if start < 0:
        return []
    try:
        data = json.loads(out[start:])
    except ValueError:
        return []
    if isinstance(data, dict):
        data = [data]
    return [{"name": d.get("Name", ""), "status": str(d.get("Status", "")),
             "description": d.get("InterfaceDescription", "")} for d in data if d.get("Name")]


def list_adapters():
    """Danh sách card mạng: [{name, status, description}] (gồm cả card đang tắt)."""
    code, out = powershell("Get-NetAdapter | Select-Object Name,Status,InterfaceDescription "
                           "| ConvertTo-Json -Compress")
    if code != 0:
        log.error("Không lấy được danh sách card mạng: %s", out)
        return []
    return parse_adapters_json(out)


def restart_adapter(name):
    """Tắt rồi bật lại card mạng (cần quyền Administrator)."""
    if DRY_RUN:
        return _dry(f"khởi động lại card mạng {name}")
    q = ps_quote(name)
    code, out = powershell(f"Disable-NetAdapter -Name {q} -Confirm:$false -ErrorAction Stop; "
                           f"Start-Sleep -Seconds 5; "
                           f"Enable-NetAdapter -Name {q} -Confirm:$false -ErrorAction Stop")
    if code != 0:  # lỡ tắt được mà bật lỗi → cố bật lại lần nữa để không mất card
        powershell(f"Enable-NetAdapter -Name {q} -Confirm:$false")
    return code == 0, out or "OK"


# ---------------------------------------------------------------- Wi-Fi
def parse_wifi_profiles(out):
    names = []
    for line in (out or "").splitlines():
        # "    All User Profile     : TenMang" — nhãn bị dịch theo ngôn ngữ Windows nên chỉ dựa vào " : "
        m = re.match(r"^\s{4}\S.*?\s:\s(.+)$", line)
        if m and m.group(1).strip() not in names:
            names.append(m.group(1).strip())
    return names


def list_wifi_profiles():
    """Các mạng Wi-Fi đã lưu mật khẩu trên máy (tên profile)."""
    code, out = run(["netsh", "wlan", "show", "profiles"])
    return parse_wifi_profiles(out)


def parse_visible_ssids(out):
    return [m.group(1).strip() for m in re.finditer(r"^SSID \d+[ \t]*:[ \t]*(.*)$", out or "", re.M)
            if m.group(1).strip()]


def visible_ssids():
    """Các SSID đang phát sóng quanh máy."""
    code, out = run(["netsh", "wlan", "show", "networks"])
    return parse_visible_ssids(out)


def connect_wifi(profile, interface=""):
    if DRY_RUN:
        return _dry(f"kết nối Wi-Fi {profile}")
    # Truyền nguyên chuỗi để netsh nhận đúng name="..." (list2cmdline sẽ quote sai)
    cmd = f'netsh wlan connect name="{profile.replace(chr(34), "")}"'
    if interface:
        cmd += f' interface="{interface.replace(chr(34), "")}"'
    return run(cmd, timeout=30)


def parse_current_ssid(out):
    m = re.search(r"^[ \t]*SSID[ \t]*:[ \t]*(.+)$", out or "", re.M)
    return m.group(1).strip() if m else ""


def current_ssid():
    code, out = run(["netsh", "wlan", "show", "interfaces"])
    return parse_current_ssid(out)


# ---------------------------------------------------------------- Hệ thống
def uptime_sec():
    if IS_WIN:
        f = ctypes.windll.kernel32.GetTickCount64
        f.restype = ctypes.c_ulonglong
        return f() / 1000.0
    return time.time() - psutil.boot_time() if psutil else 0.0


def reboot(delay_sec=60, message="NetWatchdog: khởi động lại do mất mạng"):
    if DRY_RUN:
        return _dry(f"khởi động lại máy sau {delay_sec}s")
    code, out = run(["shutdown", "/r", "/f", "/t", str(int(delay_sec)), "/c", message[:500]])
    return code == 0, out or "OK"


def cancel_reboot():
    if DRY_RUN:
        return _dry("hủy lệnh khởi động lại")
    code, out = run(["shutdown", "/a"])
    return code == 0, out or "OK"


def local_ip():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))  # UDP: không gửi gói thật, chỉ để chọn card ra
            return s.getsockname()[0]
    except OSError:
        return ""


def disk_usage():
    try:
        return shutil.disk_usage(os.environ.get("SystemDrive", "C:") + "\\")
    except OSError:
        return None


# ---------------------------------------------------------------- Ứng dụng
def is_running(path):
    if not psutil:
        return False
    exe = os.path.basename(path).lower()
    for p in psutil.process_iter(["name"]):
        try:
            if (p.info.get("name") or "").lower() == exe:
                return True
        except (psutil.Error, OSError):
            continue
    return False


def launch_app(app):
    """app = {path, args, workdir}. Trả về (ok, thông báo)."""
    path = (app.get("path") or "").strip()
    args = (app.get("args") or "").strip()
    if not path:
        return False, "thiếu đường dẫn"
    if DRY_RUN:
        return _dry(f"mở ứng dụng {path} {args}")
    cwd = app.get("workdir") or os.path.dirname(path) or None
    try:
        if not args and not path.lower().endswith((".exe", ".bat", ".cmd")):
            os.startfile(path)  # .lnk, tài liệu, URL…
        else:
            subprocess.Popen(f'"{path}" {args}'.strip(), cwd=cwd, creationflags=DETACHED, close_fds=True)
        return True, "OK"
    except Exception as e:  # noqa: BLE001
        return False, str(e)
