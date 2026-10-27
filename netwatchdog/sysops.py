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


def session_locked():
    """True nếu phiên Windows hiện tại đang khóa (màn hình khóa, kể cả khi Windows tự đăng nhập lại sau khi
    khởi động rồi khóa ngay), False nếu đang mở, None nếu không xác định được."""
    if not IS_WIN:
        return False
    try:  # cờ khóa của phiên do Windows quản lý (WTSSessionInfoEx)
        wts = ctypes.WinDLL("wtsapi32")
        wts.WTSQuerySessionInformationW.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int,
                                                    ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_ulong)]
        wts.WTSFreeMemory.argtypes = [ctypes.c_void_p]
        buf, size = ctypes.c_void_p(), ctypes.c_ulong()
        if wts.WTSQuerySessionInformationW(None, 0xFFFFFFFF, 25, ctypes.byref(buf), ctypes.byref(size)) and buf.value:
            try:
                # WTSINFOEXW: Level (DWORD) @0, rồi WTSINFOEX_LEVEL1_W @8: SessionId, SessionState, SessionFlags @16
                level = ctypes.c_ulong.from_address(buf.value).value
                flags = ctypes.c_long.from_address(buf.value + 16).value
            finally:
                wts.WTSFreeMemory(buf)
            if level == 1 and flags in (0, 1):  # WTS_SESSIONSTATE_LOCK = 0, WTS_SESSIONSTATE_UNLOCK = 1
                return flags == 0
    except (OSError, AttributeError, ValueError):
        pass
    try:  # dự phòng: khi khóa, màn hình nhận phím/chuột là màn hình Winlogon nên không mở được
        user32 = ctypes.WinDLL("user32")
        user32.OpenInputDesktop.restype = ctypes.c_void_p
        desk = user32.OpenInputDesktop(0, False, 0x0001)
        if not desk:
            return True
        user32.CloseDesktop(ctypes.c_void_p(desk))
        return False
    except (OSError, AttributeError):
        return None


# ---------------------------------------------------------------- Tự đăng nhập Windows
WINLOGON = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon"
PASSWORDLESS = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\PasswordLess\Device"
AUTOLOGON_SECRET = "DefaultPassword"   # tên bí mật LSA mà Winlogon đọc khi AutoAdminLogon=1


class _LsaString(ctypes.Structure):
    _fields_ = [("Length", ctypes.c_ushort), ("MaximumLength", ctypes.c_ushort), ("Buffer", ctypes.c_wchar_p)]


class _LsaAttrs(ctypes.Structure):
    _fields_ = [("Length", ctypes.c_ulong), ("RootDirectory", ctypes.c_void_p), ("ObjectName", ctypes.c_void_p),
                ("Attributes", ctypes.c_ulong), ("SecurityDescriptor", ctypes.c_void_p),
                ("SecurityQualityOfService", ctypes.c_void_p)]


def _lsa_str(s):
    if s is None:
        return None
    u = _LsaString()
    u.Buffer = s
    u.Length = len(s) * 2
    u.MaximumLength = u.Length + 2
    return u


def _lsa(key, value=None, retrieve=False):
    """Lưu (value; None = xóa) hoặc kiểm tra có (retrieve=True) bí mật LSA `key`.
    Trả về (ok, thông báo) khi lưu; (ok, có_dữ_liệu) khi kiểm tra — không bao giờ trả về nội dung."""
    adv = ctypes.WinDLL("advapi32")
    adv.LsaOpenPolicy.argtypes = [ctypes.c_void_p, ctypes.POINTER(_LsaAttrs), ctypes.c_ulong, ctypes.POINTER(ctypes.c_void_p)]
    adv.LsaStorePrivateData.argtypes = [ctypes.c_void_p, ctypes.POINTER(_LsaString), ctypes.POINTER(_LsaString)]
    adv.LsaRetrievePrivateData.argtypes = [ctypes.c_void_p, ctypes.POINTER(_LsaString),
                                           ctypes.POINTER(ctypes.POINTER(_LsaString))]
    adv.LsaClose.argtypes = [ctypes.c_void_p]
    adv.LsaFreeMemory.argtypes = [ctypes.c_void_p]
    adv.LsaNtStatusToWinError.argtypes = [ctypes.c_ulong]
    attrs = _LsaAttrs()
    attrs.Length = ctypes.sizeof(_LsaAttrs)
    policy = ctypes.c_void_p()
    st = adv.LsaOpenPolicy(None, ctypes.byref(attrs), 0x20 | 0x04, ctypes.byref(policy))  # CREATE_SECRET | GET_PRIVATE_INFO
    if st:
        return False, f"LsaOpenPolicy lỗi {adv.LsaNtStatusToWinError(st & 0xFFFFFFFF)} (cần quyền Administrator)"
    try:
        k = _lsa_str(key)
        if retrieve:
            out = ctypes.POINTER(_LsaString)()
            st = adv.LsaRetrievePrivateData(policy, ctypes.byref(k), ctypes.byref(out))
            if st:
                return True, False                 # chưa có bí mật này
            has = bool(out) and out.contents.Length > 0
            adv.LsaFreeMemory(out)                 # chỉ biết là có, không đọc nội dung
            return True, has
        v = _lsa_str(value)
        st = adv.LsaStorePrivateData(policy, ctypes.byref(k), ctypes.byref(v) if v is not None else None)
        return (st == 0), ("OK" if st == 0 else f"LsaStorePrivateData lỗi {adv.LsaNtStatusToWinError(st & 0xFFFFFFFF)}")
    finally:
        adv.LsaClose(policy)


def check_password(user, domain, password):
    """Mật khẩu có đúng không: thử đăng nhập bằng LogonUser rồi đóng ngay. Trả về (ok, thông báo)."""
    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    adv.LogonUserW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_ulong, ctypes.c_ulong,
                               ctypes.POINTER(ctypes.c_void_p)]
    tok = ctypes.c_void_p()
    if adv.LogonUserW(user, domain or ".", password, 2, 0, ctypes.byref(tok)):  # LOGON32_LOGON_INTERACTIVE
        ctypes.windll.kernel32.CloseHandle(tok)
        return True, "OK"
    err = ctypes.get_last_error()
    return False, {1326: "sai tên đăng nhập hoặc mật khẩu", 1327: "tài khoản bị hạn chế đăng nhập",
                   1330: "mật khẩu đã hết hạn", 1331: "tài khoản đang bị vô hiệu"}.get(err, f"lỗi Windows {err}")


def autologon_status():
    """{enabled, user, domain, has_password, passwordless_only} — không bao giờ trả về mật khẩu."""
    st = {"enabled": False, "user": "", "domain": "", "has_password": False, "passwordless_only": False}
    if not IS_WIN:
        return st
    import winreg
    plaintext = False
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, WINLOGON) as k:
            def val(name):
                try:
                    return str(winreg.QueryValueEx(k, name)[0])
                except OSError:
                    return ""
            st.update(enabled=val("AutoAdminLogon") == "1", user=val("DefaultUserName"), domain=val("DefaultDomainName"))
            plaintext = bool(val("DefaultPassword"))
    except OSError:
        pass
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, PASSWORDLESS) as k:
            st["passwordless_only"] = winreg.QueryValueEx(k, "DevicePasswordLessBuildVersion")[0] == 2
    except OSError:
        pass
    ok, has = _lsa(AUTOLOGON_SECRET, retrieve=True)
    st["has_password"] = bool(ok and has) or plaintext
    return st


def enable_autologon(password, user=None, domain=None):
    """Bật tự đăng nhập Windows: kiểm tra mật khẩu, lưu vào bí mật LSA (mã hóa, chỉ hệ thống đọc được),
    đặt khóa Winlogon. Tắt yêu cầu "chỉ đăng nhập bằng Windows Hello" vì nó chặn tự đăng nhập.
    Mật khẩu không được ghi vào registry dạng chữ, config hay log. Trả về (ok, thông báo)."""
    import winreg
    user = user or os.environ.get("USERNAME", "")
    domain = domain or os.environ.get("USERDOMAIN", "") or "."
    if not password:
        return False, "Chưa nhập mật khẩu."
    ok, msg = check_password(user, domain, password)
    if not ok:
        return False, f"Mật khẩu không đúng cho {domain}\\{user}: {msg}."
    if DRY_RUN:
        return _dry(f"bật tự đăng nhập cho {domain}\\{user}")
    ok, msg = _lsa(AUTOLOGON_SECRET, password)
    if not ok:
        return False, msg
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, WINLOGON, 0, winreg.KEY_SET_VALUE | winreg.KEY_WOW64_64KEY) as k:
            winreg.SetValueEx(k, "AutoAdminLogon", 0, winreg.REG_SZ, "1")
            winreg.SetValueEx(k, "DefaultUserName", 0, winreg.REG_SZ, user)
            winreg.SetValueEx(k, "DefaultDomainName", 0, winreg.REG_SZ, domain)
            for name in ("DefaultPassword", "AutoLogonCount"):   # không để mật khẩu dạng chữ / giới hạn số lần
                try:
                    winreg.DeleteValue(k, name)
                except OSError:
                    pass
        with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, PASSWORDLESS, 0, winreg.KEY_SET_VALUE | winreg.KEY_WOW64_64KEY) as k:
            winreg.SetValueEx(k, "DevicePasswordLessBuildVersion", 0, winreg.REG_DWORD, 0)
    except OSError as e:
        return False, f"Không ghi được registry: {e}"
    return True, f"Đã bật tự đăng nhập cho {domain}\\{user}."


def disable_autologon():
    """Tắt tự đăng nhập và xóa mật khẩu đã lưu. Trả về (ok, thông báo)."""
    import winreg
    if DRY_RUN:
        return _dry("tắt tự đăng nhập")
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, WINLOGON, 0, winreg.KEY_SET_VALUE | winreg.KEY_WOW64_64KEY) as k:
            winreg.SetValueEx(k, "AutoAdminLogon", 0, winreg.REG_SZ, "0")
            try:
                winreg.DeleteValue(k, "DefaultPassword")
            except OSError:
                pass
    except OSError as e:
        return False, f"Không ghi được registry: {e}"
    ok, msg = _lsa(AUTOLOGON_SECRET, None)              # giá trị NULL = xóa bí mật
    return True, "Đã tắt tự đăng nhập và xóa mật khẩu đã lưu." + ("" if ok else f" (xóa bí mật LSA: {msg})")


def reboot(delay_sec=60,message="NetWatchdog: khởi động lại do mất mạng"):
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


# ---------------------------------------------------------------- Tác vụ chạy nền (Task Scheduler)
TASK_NAME = "NetWatchdog"


def install_task(exe, args="--service"):
    """Tạo Scheduled Task chạy NetWatchdog với quyền cao nhất mỗi khi user hiện tại đăng nhập.

    Tự chạy lại nếu bị tắt. Cần quyền Administrator."""
    domain, name = os.environ.get("USERDOMAIN", ""), os.environ.get("USERNAME", "")
    user = f"{domain}\\{name}" if domain else name
    code, out = powershell(
        f"$a = New-ScheduledTaskAction -Execute {ps_quote(exe)} -Argument {ps_quote(args)} "
        f"-WorkingDirectory {ps_quote(os.path.dirname(exe))}; "
        f"$t = New-ScheduledTaskTrigger -AtLogOn -User {ps_quote(user)}; "
        f"$p = New-ScheduledTaskPrincipal -UserId {ps_quote(user)} -LogonType Interactive -RunLevel Highest; "
        "$s = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries "
        "-StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 "
        "-RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew; "
        f"Register-ScheduledTask -TaskName {ps_quote(TASK_NAME)} -Action $a -Trigger $t -Principal $p "
        "-Settings $s -Description 'Theo doi mang, tu khoi phuc, bao cao Telegram' -Force | Out-Null")
    return code == 0, out or "OK"


def uninstall_task():
    code, out = powershell(f"Stop-ScheduledTask -TaskName {ps_quote(TASK_NAME)} -ErrorAction SilentlyContinue; "
                           f"Unregister-ScheduledTask -TaskName {ps_quote(TASK_NAME)} -Confirm:$false "
                           "-ErrorAction SilentlyContinue")
    return code == 0, out or "OK"


def task_exists():
    code, _ = run(["schtasks", "/Query", "/TN", TASK_NAME])
    return code == 0


def start_task():
    code, out = run(["schtasks", "/Run", "/TN", TASK_NAME])
    return code == 0, out or "OK"


def stop_task():
    """Dừng tác vụ và mọi tiến trình NetWatchdog đang chạy nền (trừ tiến trình hiện tại)."""
    code, out = run(["schtasks", "/End", "/TN", TASK_NAME])
    if psutil:
        me = os.getpid()
        for p in psutil.process_iter(["pid", "cmdline"]):
            try:
                cmd = " ".join(p.info.get("cmdline") or []).lower()
                if p.info["pid"] != me and ("--service" in cmd and "netwatchdog" in cmd
                                            or "netwatchdog.py" in cmd and "settings_gui" not in cmd):
                    p.kill()
            except (psutil.Error, OSError):
                continue
    return code == 0, out or "OK"
