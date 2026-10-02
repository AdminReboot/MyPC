"""NetWatchdog — theo dõi mạng, tự khôi phục khi rớt mạng và báo cáo lên Telegram.

Trình tự khi mất mạng (sau `fail_threshold` lần kiểm tra lỗi liên tiếp):
  1. Thử kết nối lần lượt các mạng Wi-Fi đã chọn.
  2. Khởi động lại các card mạng đã chọn.
  (lặp lại 1–2 theo `rounds`)
  3. Vẫn không được → khởi động lại máy; sau khi lên lại sẽ mở các ứng dụng đã chọn.
Mọi sự kiện được gửi lên Telegram; tin nhắn lúc mất mạng được xếp hàng (lưu ra đĩa)
và gửi bù khi có mạng trở lại, kể cả sau khi khởi động lại máy.

Chạy:  pythonw netwatchdog.py            (chạy nền, không cửa sổ)
       python  netwatchdog.py --dry-run  (chạy thử: chỉ ghi log, không đổi mạng/khởi động lại)
       python  netwatchdog.py --status   (in báo cáo tình trạng máy rồi thoát)
"""
import argparse
import copy
import json
import logging
import os
import platform
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from logging.handlers import RotatingFileHandler

import sysops

psutil = sysops.psutil

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
STATE_PATH = os.path.join(BASE_DIR, "state.json")
LOG_PATH = os.path.join(BASE_DIR, "logs", "netwatchdog.log")
INSTANCE_PORT = 47831  # khóa chạy 1 tiến trình duy nhất

log = logging.getLogger("netwatchdog")

DEFAULT_CONFIG = {
    "machine_name": "",
    "telegram": {"bot_token": "", "chat_id": "", "commands": True},
    "check": {
        "targets": ["1.1.1.1:53", "8.8.8.8:53", "208.67.222.222:443"],
        "http_url": "http://www.msftconnecttest.com/connecttest.txt",
        "interval_sec": 30,
        "fail_threshold": 3,
        "timeout_sec": 4,
    },
    "recovery": {
        "wifi_profiles": [],
        "wifi_interface": "",
        "adapters": [],
        "wait_after_action_sec": 25,
        "rounds": 2,
        "retry_cooldown_min": 10,
        "reboot_enabled": True,
        "reboot_delay_sec": 60,
        "max_reboots_per_day": 3,
        "min_uptime_before_reboot_min": 15,
    },
    "apps": [],                      # [{"path": "...", "args": "", "workdir": "", "skip_if_running": true}]
    "launch_apps": "after_reboot",   # "after_reboot" | "every_start" | "never"
    "report": {"heartbeat_min": 360, "public_ip": True},
}


# ---------------------------------------------------------------- Tiện ích
def deep_merge(base, over):
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        out[k] = deep_merge(out[k], v) if isinstance(out.get(k), dict) and isinstance(v, dict) else v
    return out


def load_config(path=CONFIG_PATH):
    try:
        with open(path, encoding="utf-8-sig") as f:  # chịu được file có BOM do Notepad lưu
            return deep_merge(DEFAULT_CONFIG, json.load(f))
    except FileNotFoundError:
        return copy.deepcopy(DEFAULT_CONFIG)


def save_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def now_str(ts=None):
    return datetime.fromtimestamp(ts or time.time()).strftime("%d/%m/%Y %H:%M:%S")


def fmt_duration(sec):
    sec = int(max(0, sec))
    d, rem = divmod(sec, 86400)
    h, rem = divmod(rem, 3600)
    m, s = divmod(rem, 60)
    if d:
        return f"{d} ngày {h} giờ {m} phút"
    if h:
        return f"{h} giờ {m} phút"
    if m:
        return f"{m} phút {s} giây"
    return f"{s} giây"


def machine_name(cfg):
    return cfg.get("machine_name") or platform.node() or "PC"


def check_online(cfg):
    """Trả về (online, chi tiết). Online nếu kết nối TCP được tới 1 đích bất kỳ hoặc HTTP OK."""
    chk = cfg["check"]
    timeout = chk.get("timeout_sec", 4)
    for target in chk.get("targets") or []:
        host, _, port = str(target).rpartition(":")
        if not host:
            host, port = port, "443"
        try:
            with socket.create_connection((host, int(port)), timeout=timeout):
                return True, target
        except (OSError, ValueError):
            continue
    url = chk.get("http_url")
    if url:
        try:
            with urllib.request.urlopen(url, timeout=timeout) as r:
                if 200 <= r.status < 400:
                    return True, url
        except (OSError, urllib.error.URLError, ValueError):
            pass
    return False, "không kết nối được tới đích kiểm tra nào"


# ---------------------------------------------------------------- Trạng thái lưu đĩa
class State:
    """Lưu những thứ cần sống qua lần khởi động lại: hàng đợi tin nhắn, lịch sử reboot…"""

    def __init__(self, path=STATE_PATH):
        self.path = path
        self.lock = threading.RLock()
        self.data = {"outbox": [], "reboots": [], "pending_launch": False, "reboot_reason": "",
                     "offline_since": None, "last_outage": "", "tg_offset": 0}
        try:
            with open(path, encoding="utf-8") as f:
                self.data.update(json.load(f))
        except (FileNotFoundError, ValueError):
            pass

    def get(self, key, default=None):
        with self.lock:
            return self.data.get(key, default)

    def set(self, **kw):
        with self.lock:
            self.data.update(kw)
            self.save()

    def save(self):
        with self.lock:
            try:
                save_json(self.path, self.data)
            except OSError as e:
                log.error("Không ghi được state: %s", e)


# ---------------------------------------------------------------- Telegram
class Telegram:
    MAX_OUTBOX = 100

    def __init__(self, get_cfg, state):
        self.get_cfg = get_cfg
        self.state = state
        self.send_lock = threading.Lock()

    @property
    def token(self):
        return (self.get_cfg()["telegram"].get("bot_token") or "").strip()

    @property
    def chat_id(self):
        return str(self.get_cfg()["telegram"].get("chat_id") or "").strip()

    @property
    def enabled(self):
        return bool(self.token and self.chat_id)

    def call(self, method, params=None, timeout=20):
        url = f"https://api.telegram.org/bot{self.token}/{method}"
        body = urllib.parse.urlencode(params or {}).encode()
        with urllib.request.urlopen(urllib.request.Request(url, data=body), timeout=timeout) as r:
            res = json.loads(r.read().decode("utf-8"))
        if not res.get("ok"):
            raise RuntimeError(res.get("description", "Telegram lỗi"))
        return res.get("result")

    def send_now(self, text, chat_id=None):
        self.call("sendMessage", {"chat_id": chat_id or self.chat_id, "text": text[:4000],
                                  "disable_web_page_preview": "true"})

    def send(self, text):
        """Xếp tin vào hàng đợi (lưu đĩa) rồi thử gửi ngay."""
        log.info("Telegram << %s", text.replace("\n", " | "))
        with self.state.lock:
            outbox = self.state.data.setdefault("outbox", [])
            outbox.append({"ts": time.time(), "text": text})
            del outbox[:-self.MAX_OUTBOX]
            self.state.save()
        self.flush()

    def flush(self):
        """Gửi các tin đang chờ theo thứ tự; dừng ở tin lỗi đầu tiên (thường do mất mạng)."""
        if not self.enabled:
            return
        with self.send_lock:
            while True:
                with self.state.lock:
                    outbox = self.state.data.get("outbox") or []
                    if not outbox:
                        return
                    item = outbox[0]
                text = item["text"]
                if time.time() - item["ts"] > 120:
                    text = f"⏳ [gửi trễ — sự kiện lúc {now_str(item['ts'])}]\n{text}"
                try:
                    self.send_now(text)
                except Exception as e:  # noqa: BLE001
                    log.debug("Chưa gửi được Telegram: %s", e)
                    return
                with self.state.lock:
                    if self.state.data.get("outbox") and self.state.data["outbox"][0] is item:
                        self.state.data["outbox"].pop(0)
                        self.state.save()


# ---------------------------------------------------------------- Báo cáo tình trạng máy
def public_ip():
    try:
        with urllib.request.urlopen("https://api.ipify.org", timeout=5) as r:
            return r.read().decode().strip()
    except Exception:  # noqa: BLE001
        return ""


def status_report(cfg, state, online=None, detail=""):
    if online is None:
        online, detail = check_online(cfg)
    lines = [f"🖥 {machine_name(cfg)} — {now_str()}",
             f"⏱ Uptime: {fmt_duration(sysops.uptime_sec())}"]
    net = "🟢 Online" if online else "🔴 Offline"
    ssid = sysops.current_ssid()
    lines.append(f"🌐 Mạng: {net}" + (f" | Wi-Fi: {ssid}" if ssid else ""))
    ip = sysops.local_ip()
    wan = public_ip() if online and cfg["report"].get("public_ip") else ""
    if ip or wan:
        lines.append(f"📍 IP LAN: {ip or '-'}" + (f" | WAN: {wan}" if wan else ""))
    if psutil:
        vm = psutil.virtual_memory()
        lines.append(f"🧠 CPU {psutil.cpu_percent(interval=1):.0f}% | RAM {vm.percent:.0f}% "
                     f"({vm.used / 2**30:.1f}/{vm.total / 2**30:.1f} GB)")
        bat = psutil.sensors_battery() if hasattr(psutil, "sensors_battery") else None
        if bat:
            lines.append(f"🔋 Pin {bat.percent:.0f}% ({'đang sạc' if bat.power_plugged else 'dùng pin'})")
    du = sysops.disk_usage()
    if du:
        lines.append(f"💾 Ổ hệ thống: trống {du.free / 2**30:.0f}/{du.total / 2**30:.0f} GB "
                     f"(đã dùng {du.used * 100 / du.total:.0f}%)")
    day_ago = time.time() - 86400
    n_reboot = len([t for t in state.get("reboots", []) if t > day_ago])
    lines.append(f"🔁 Reboot do mất mạng (24h): {n_reboot}")
    if state.get("last_outage"):
        lines.append(f"📉 Lần mất mạng gần nhất: {state.get('last_outage')}")
    return "\n".join(lines)


# ---------------------------------------------------------------- Watchdog
class Watchdog:
    def __init__(self, config_path=CONFIG_PATH, state_path=STATE_PATH):
        self.config_path = config_path
        self._cfg_mtime = None
        self._cfg = None
        self.state = State(state_path)
        self.tg = Telegram(lambda: self.cfg, self.state)
        self.stop_event = threading.Event()
        self.online = None
        self.next_recovery_at = 0.0
        self.last_fix = ""
        self.last_heartbeat = time.time()
        self.recovering = threading.Lock()

    @property
    def cfg(self):
        """Tự nạp lại config.json khi file thay đổi (sửa trong cửa sổ Cài đặt không cần khởi động lại)."""
        try:
            mtime = os.path.getmtime(self.config_path)
        except OSError:
            mtime = None
        if self._cfg is None or mtime != self._cfg_mtime:
            self._cfg = load_config(self.config_path)
            self._cfg_mtime = mtime
            if self._cfg is not None and mtime is not None:
                log.info("Đã nạp cấu hình %s", self.config_path)
        return self._cfg

    def sleep(self, sec):
        return self.stop_event.wait(sec)

    # ------------------------------------------------ Khởi động
    def on_start(self):
        cfg = self.cfg
        msg = [f"🟢 NetWatchdog đã chạy trên {machine_name(cfg)}"]
        mode = cfg.get("launch_apps", "after_reboot")
        if self.state.get("pending_launch"):
            msg.append(f"🔁 Máy vừa được khởi động lại vì: {self.state.get('reboot_reason') or 'mất mạng'}")
            if mode != "never":
                msg.append(self.launch_apps())
            self.state.set(pending_launch=False)
            self.last_fix = "khởi động lại máy"
        elif mode == "every_start":
            msg.append(self.launch_apps())
        self.tg.send("\n".join(m for m in msg if m))

    def launch_apps(self):
        apps = [a for a in self.cfg.get("apps") or [] if a.get("path")]
        if not apps:
            return ""
        res = []
        for app in apps:
            name = os.path.basename(app["path"])
            if app.get("skip_if_running", True) and sysops.is_running(app["path"]):
                res.append(f"  • {name}: đang chạy sẵn")
                continue
            ok, out = sysops.launch_app(app)
            log.info("Mở ứng dụng %s: %s %s", app["path"], ok, out)
            res.append(f"  • {name}: {'✅ đã mở' if ok else '❌ lỗi ' + out[:200]}")
            time.sleep(2)
        return "🚀 Mở ứng dụng:\n" + "\n".join(res)

    # ------------------------------------------------ Vòng lặp chính
    def run(self):
        log.info("NetWatchdog bắt đầu (dry-run=%s)", sysops.DRY_RUN)
        self.on_start()
        if self.cfg["telegram"].get("commands", True):
            threading.Thread(target=self.command_loop, daemon=True, name="tg-commands").start()
        fails = 0
        while not self.stop_event.is_set():
            cfg = self.cfg
            online, detail = check_online(cfg)
            self.online = online
            if online:
                if fails:
                    log.info("Có mạng trở lại (%s)", detail)
                fails = 0
                self.on_online()
            else:
                fails += 1
                log.warning("Mất mạng (%d/%d): %s", fails, cfg["check"]["fail_threshold"], detail)
                if not self.state.get("offline_since"):
                    self.state.set(offline_since=time.time())
                    self.last_fix = ""
                if fails >= cfg["check"]["fail_threshold"] and time.time() >= self.next_recovery_at:
                    self.recover()
                    fails = 0
                    continue
            self.sleep(cfg["check"]["interval_sec"])

    def on_online(self):
        since = self.state.get("offline_since")
        if since:
            dur = fmt_duration(time.time() - since)
            how = self.last_fix or "tự có mạng lại"
            self.state.set(offline_since=None, last_outage=f"{now_str(since)}, kéo dài {dur} ({how})")
            self.tg.send(f"🟢 {machine_name(self.cfg)}: CÓ MẠNG TRỞ LẠI\n"
                         f"Mất mạng từ {now_str(since)}, kéo dài {dur}.\nKhôi phục: {how}\n\n"
                         + status_report(self.cfg, self.state, True))
            self.last_heartbeat = time.time()
        self.tg.flush()
        hb = self.cfg["report"].get("heartbeat_min") or 0
        if hb and time.time() - self.last_heartbeat >= hb * 60:
            self.last_heartbeat = time.time()
            self.tg.send("📊 Báo cáo định kỳ\n" + status_report(self.cfg, self.state, True))

    def wait_online(self, sec):
        deadline = time.time() + sec
        while True:
            ok, detail = check_online(self.cfg)
            if ok or time.time() >= deadline or self.stop_event.is_set():
                return ok
            self.sleep(min(3, max(0.1, deadline - time.time())))

    # ------------------------------------------------ Khôi phục
    def recover(self):
        with self.recovering:
            return self._recover()

    def _recover(self):
        cfg = self.cfg
        rec = cfg["recovery"]
        steps = []
        since = self.state.get("offline_since")
        if since:  # chỉ vào hàng đợi, gửi được khi có mạng
            self.tg.send(f"🔴 {machine_name(cfg)}: MẤT MẠNG lúc {now_str(since)}. Bắt đầu tự khôi phục…")
        for rnd in range(1, max(1, int(rec.get("rounds", 1))) + 1):
            for prof in rec.get("wifi_profiles") or []:
                if self.stop_event.is_set():
                    return False
                ok, out = sysops.connect_wifi(prof, rec.get("wifi_interface", ""))
                log.info("[vòng %d] Kết nối Wi-Fi '%s': %s %s", rnd, prof, ok, out)
                steps.append(f"Wi-Fi '{prof}': {'ok' if ok else 'lỗi'}")
                if self.wait_online(rec["wait_after_action_sec"] if ok else 3):
                    return self._recovered(f"chuyển sang Wi-Fi '{prof}'", steps)
            for adapter in rec.get("adapters") or []:
                if self.stop_event.is_set():
                    return False
                ok, out = sysops.restart_adapter(adapter)
                log.info("[vòng %d] Khởi động lại card '%s': %s %s", rnd, adapter, ok, out)
                steps.append(f"Restart card '{adapter}': {'ok' if ok else 'lỗi: ' + out[:120]}")
                if self.wait_online(rec["wait_after_action_sec"]):
                    return self._recovered(f"khởi động lại card mạng '{adapter}'", steps)
        return self._try_reboot(steps)

    def _recovered(self, how, steps):
        self.last_fix = how + (f" (sau {len(steps)} thao tác)" if len(steps) > 1 else "")
        self.next_recovery_at = 0
        log.info("Đã khôi phục mạng: %s", how)
        return True

    def _try_reboot(self, steps):
        cfg = self.cfg
        if check_online(cfg)[0]:  # mạng tự về trong lúc thử (hoặc /fix khi đang có mạng)
            return self._recovered("tự có mạng lại", steps)
        rec = cfg["recovery"]
        cooldown = rec.get("retry_cooldown_min", 10) * 60
        tried = "\n".join("  • " + s for s in steps) or "  (chưa chọn Wi-Fi/card mạng nào để thử)"
        now = time.time()
        recent = [t for t in self.state.get("reboots", []) if now - t < 86400]
        uptime = sysops.uptime_sec()
        min_up = rec.get("min_uptime_before_reboot_min", 15) * 60

        reason = None
        if not rec.get("reboot_enabled", True):
            reason = "chức năng khởi động lại đang TẮT"
        elif len(recent) >= rec.get("max_reboots_per_day", 3):
            reason = f"đã khởi động lại {len(recent)} lần trong 24h (giới hạn {rec.get('max_reboots_per_day')})"
        elif uptime < min_up:
            reason = f"máy mới bật được {fmt_duration(uptime)}, chờ đủ {fmt_duration(min_up)}"
            cooldown = min(cooldown, max(30, min_up - uptime))
        if reason:
            self.next_recovery_at = now + cooldown
            log.warning("Không khôi phục được, không reboot: %s", reason)
            self.tg.send(f"⚠️ {machine_name(cfg)}: chưa khôi phục được mạng.\nĐã thử:\n{tried}\n"
                         f"Không khởi động lại vì {reason}. Thử lại sau {fmt_duration(cooldown)}.")
            return False

        why = f"mất mạng từ {now_str(self.state.get('offline_since') or now)}, đã thử hết cách"
        recent.append(now)
        self.state.set(reboots=recent, pending_launch=True, reboot_reason=why)
        self.tg.send(f"🔁 {machine_name(cfg)}: KHỞI ĐỘNG LẠI MÁY sau {rec['reboot_delay_sec']} giây.\n"
                     f"Đã thử:\n{tried}")
        ok, out = sysops.reboot(rec["reboot_delay_sec"], "NetWatchdog: khởi động lại do mất mạng")
        log.warning("Lệnh khởi động lại: %s %s", ok, out)
        if not ok:
            self.state.set(pending_launch=False)
            self.tg.send(f"❌ Lệnh khởi động lại thất bại: {out[:300]}\n(Cần chạy NetWatchdog với quyền Administrator)")
        # Chờ máy tắt; nếu ai đó hủy (shutdown /a) thì tiếp tục theo dõi
        self.next_recovery_at = time.time() + max(cooldown, rec["reboot_delay_sec"] + 120)
        return False

    # ------------------------------------------------ Lệnh Telegram
    HELP = ("Lệnh NetWatchdog:\n"
            "/status — tình trạng máy\n"
            "/apps — mở các ứng dụng đã chọn\n"
            "/fix — chạy quy trình khôi phục mạng ngay\n"
            "/reboot yes — khởi động lại máy\n"
            "/cancel — hủy lệnh khởi động lại đang chờ\n"
            "/help — trợ giúp")

    def command_loop(self):
        while not self.stop_event.is_set():
            if not self.tg.enabled or not self.online:
                self.sleep(10)
                continue
            try:
                updates = self.tg.call("getUpdates", {"offset": self.state.get("tg_offset", 0), "timeout": 25,
                                                      "allowed_updates": '["message"]'}, timeout=35)
            except Exception as e:  # noqa: BLE001
                log.debug("getUpdates lỗi: %s", e)
                self.sleep(10)
                continue
            for u in updates or []:
                self.state.set(tg_offset=u["update_id"] + 1)
                msg = u.get("message") or {}
                if str((msg.get("chat") or {}).get("id")) != self.tg.chat_id:
                    continue  # chỉ nhận lệnh từ chat đã cấu hình
                if time.time() - msg.get("date", 0) > 600:
                    continue  # bỏ lệnh cũ tồn đọng (vd. /reboot gửi từ lâu)
                try:
                    reply = self.handle_command((msg.get("text") or "").strip())
                except Exception as e:  # noqa: BLE001
                    log.exception("Lỗi xử lý lệnh")
                    reply = f"❌ Lỗi: {e}"
                if reply:
                    self.tg.send(reply)

    def handle_command(self, text):
        if not text.startswith("/"):
            return ""
        parts = text.split()
        cmd = parts[0].split("@")[0].lower()
        arg = parts[1].lower() if len(parts) > 1 else ""
        if cmd in ("/status", "/start"):
            return status_report(self.cfg, self.state)
        if cmd == "/apps":
            return self.launch_apps() or "Chưa chọn ứng dụng nào."
        if cmd == "/fix":
            if self.recovering.locked():
                return "Đang khôi phục rồi, chờ chút."
            threading.Thread(target=self.recover, daemon=True).start()
            return "🛠 Đang chạy quy trình khôi phục mạng (có thể mất kết nối tạm thời)…"
        if cmd == "/reboot":
            if arg != "yes":
                return "Gửi `/reboot yes` để xác nhận khởi động lại máy."
            self.state.set(pending_launch=True, reboot_reason="lệnh /reboot từ Telegram")
            ok, out = sysops.reboot(30, "NetWatchdog: khởi động lại theo lệnh Telegram")
            if not ok:
                self.state.set(pending_launch=False)
            return "🔁 Máy sẽ khởi động lại sau 30 giây (/cancel để hủy)." if ok else f"❌ Lỗi: {out}"
        if cmd == "/cancel":
            ok, out = sysops.cancel_reboot()
            if ok:
                self.state.set(pending_launch=False)
            return "✅ Đã hủy khởi động lại." if ok else f"Không có lệnh nào để hủy ({out[:200]})"
        return self.HELP


# ---------------------------------------------------------------- main
def setup_logging(verbose=False):
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    fh = RotatingFileHandler(LOG_PATH, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    fh.setFormatter(fmt)
    log.addHandler(fh)
    if sys.stderr:  # pythonw không có console
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        log.addHandler(sh)
    log.setLevel(logging.DEBUG if verbose else logging.INFO)


def single_instance():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", INSTANCE_PORT))
        s.listen(1)
        return s
    except OSError:
        return None


def main(argv=None):
    ap = argparse.ArgumentParser(description="NetWatchdog — theo dõi mạng & báo cáo Telegram")
    ap.add_argument("--config", default=CONFIG_PATH)
    ap.add_argument("--dry-run", action="store_true", help="chỉ ghi log, không đổi mạng/khởi động lại/mở app")
    ap.add_argument("--status", action="store_true", help="in báo cáo tình trạng rồi thoát")
    ap.add_argument("--test-telegram", action="store_true", help="gửi tin thử lên Telegram rồi thoát")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)

    setup_logging(a.verbose)
    sysops.DRY_RUN = a.dry_run
    wd = Watchdog(a.config)
    if a.status:
        print(status_report(wd.cfg, wd.state))
        return 0
    if a.test_telegram:
        wd.tg.send_now("✅ NetWatchdog kết nối Telegram thành công!\n\n" + status_report(wd.cfg, wd.state))
        print("Đã gửi.")
        return 0
    lock = single_instance()
    if not lock:
        log.error("NetWatchdog đã chạy rồi — thoát.")
        return 1
    try:
        wd.run()
    except KeyboardInterrupt:
        wd.stop_event.set()
    return 0


if __name__ == "__main__":
    sys.exit(main())
