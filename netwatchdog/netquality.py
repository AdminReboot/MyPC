"""Chất lượng mạng theo từng card và tự chuyển card khi mạng CHẬM (không chỉ khi mất hẳn).

Vấn đề cần giải quyết: Windows luôn đưa lưu lượng qua card có "metric" thấp nhất (thường là card có dây).
Khi đường đó vẫn kết nối được nhưng rất chậm, kiểm tra "có mạng hay không" vẫn qua, nên không có gì xảy ra,
dù các card Wi-Fi khác đang tốt.

Cách làm:
- Mỗi chu kỳ, đo riêng từng card đang có cổng mặc định: gắn kết nối vào IP của card (Windows gửi gói qua
  đúng card đó), đo thời gian bắt tay TCP tới vài đích và tỉ lệ lỗi; thỉnh thoảng tải thử một tệp nhỏ để
  đo tốc độ (speed.cloudflare.com, miễn phí, không cần khóa).
- Card đang gánh mạng mà chậm liên tục `bad_checks` lần, trong khi có card khác tốt: đổi interface metric
  để card tốt nhất được ưu tiên. Không tắt card chậm, vẫn đo tiếp để làm dự phòng.
- Giữ các card Wi-Fi rảnh luôn kết nối sẵn với các mạng đã chọn để chuyển được ngay.
"""
import http.client
import json
import logging
import re
import socket
import statistics
import time

import sysops

log = logging.getLogger("netwatchdog")

DEFAULT_CONFIG = {
    "enabled": True,
    "interval_sec": 60,          # chu kỳ đo
    "targets": ["1.1.1.1:443", "8.8.8.8:443", "208.67.222.222:443"],
    "probes_per_target": 2,
    "timeout_sec": 3,
    "rtt_bad_ms": 800,           # trung vị thời gian bắt tay TCP vượt mức này = chậm
    "loss_bad_pct": 50,          # tỉ lệ kết nối lỗi từ mức này = chậm
    "speed_test": True,
    "speed_bytes": 300000,       # tải thử ~300 KB
    "speed_every_min": 5,
    "speed_bad_kbps": 500,       # dưới mức này = chậm
    "bad_checks": 3,             # chậm liên tục bấy nhiêu lần đo mới chuyển card
    "switch_cooldown_min": 10,   # sau mỗi lần chuyển, chờ bấy lâu mới xét chuyển tiếp (tránh nhảy qua lại)
    "standby_wifi": True,        # giữ card Wi-Fi rảnh luôn kết nối sẵn
    "standby_retry_min": 5,
    "metric_best": 5,            # interface metric đặt cho card được chọn
    "metric_other": 100,         # và cho các card còn lại
}


# ---------------------------------------------------------------- Đọc trạng thái card (Windows)
PS_STATE = (
    "$a=Get-NetAdapter -Physical | Select-Object ifIndex,Name,InterfaceDescription,Status,MediaType,NdisPhysicalMedium; "
    "$ip=Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | Select-Object ifIndex,IPAddress; "
    "$if=Get-NetIPInterface -AddressFamily IPv4 | Select-Object ifIndex,InterfaceMetric,AutomaticMetric; "
    "$rt=Get-NetRoute -DestinationPrefix 0.0.0.0/0 -ErrorAction SilentlyContinue | Select-Object ifIndex,NextHop,RouteMetric; "
    "ConvertTo-Json @{a=@($a);ip=@($ip);if=@($if);rt=@($rt)} -Depth 3 -Compress"
)


def parse_state(out):
    """JSON của PS_STATE -> [{index, name, desc, status, kind, ip, gateway, if_metric, route_metric, auto_metric}]."""
    out = (out or "").strip()
    start = out.find("{")
    if start < 0:
        return []
    try:
        d = json.loads(out[start:])
    except ValueError:
        return []

    def lst(k):
        v = d.get(k) or []
        return [x for x in (v if isinstance(v, list) else [v]) if isinstance(x, dict)]
    ips = {}
    for x in lst("ip"):
        ip = str(x.get("IPAddress") or "")
        if ip and not ip.startswith(("169.254.", "127.")):
            ips.setdefault(x.get("ifIndex"), ip)
    ifm = {x.get("ifIndex"): x for x in lst("if")}
    rts = {}
    for x in lst("rt"):
        cur = rts.get(x.get("ifIndex"))
        if cur is None or (x.get("RouteMetric") or 0) < (cur.get("RouteMetric") or 0):
            rts[x.get("ifIndex")] = x
    out = []
    for a in lst("a"):
        idx = a.get("ifIndex")
        wifi = a.get("NdisPhysicalMedium") == 9 or "802.11" in str(a.get("MediaType") or "")
        rt, im = rts.get(idx) or {}, ifm.get(idx) or {}
        out.append({"index": idx, "name": a.get("Name") or "", "desc": a.get("InterfaceDescription") or "",
                    "status": str(a.get("Status") or ""), "kind": "wifi" if wifi else "ethernet",
                    "ip": ips.get(idx, ""), "gateway": rt.get("NextHop", ""),
                    "if_metric": im.get("InterfaceMetric"), "auto_metric": bool(im.get("AutomaticMetric")),
                    "route_metric": rt.get("RouteMetric")})
    return out


def list_interfaces():
    code, out = sysops.powershell(PS_STATE, timeout=60)
    if code != 0:
        log.error("Không đọc được trạng thái card mạng: %s", out[:300])
        return []
    return parse_state(out)


def parse_wifi_interfaces(out):
    """`netsh wlan show interfaces` -> {tên card: SSID đang kết nối (hoặc "")}."""
    res, name = {}, None
    for line in (out or "").splitlines():
        m = re.match(r"^\s*([^:]+?)\s*:\s*(.*)$", line)
        if not m:
            continue
        key, val = m.group(1).strip(), m.group(2).strip()
        if key in ("Name", "Tên"):
            name = val
            res[name] = ""
        elif key == "SSID" and name is not None:
            res[name] = val
    return res


def wifi_ssids():
    code, out = sysops.run(["netsh", "wlan", "show", "interfaces"])
    return parse_wifi_interfaces(out)


def usable(it):
    """Card đang lên, có IP và có cổng mặc định: có thể gánh mạng."""
    return it["status"] == "Up" and bool(it["ip"]) and bool(it["gateway"])


def effective_metric(it):
    return (it.get("route_metric") or 0) + (it.get("if_metric") or 0)


def active_interface(ifs):
    """Card Windows đang dùng cho Internet: cổng mặc định có tổng metric thấp nhất."""
    cands = [it for it in ifs if usable(it)]
    return min(cands, key=effective_metric) if cands else None


def set_metric(index, metric):
    if sysops.DRY_RUN:
        return sysops._dry(f"đặt interface metric {metric} cho card #{index}")
    code, out = sysops.powershell(f"Set-NetIPInterface -InterfaceIndex {int(index)} -AddressFamily IPv4 "
                                  f"-InterfaceMetric {int(metric)} -ErrorAction Stop")
    return code == 0, out or "OK"


# ---------------------------------------------------------------- Đo chất lượng qua một card
def _target(t):
    host, _, port = str(t).rpartition(":")
    return (host, int(port)) if host else (port, 443)


def probe_rtt(src_ip, targets, per_target=2, timeout=3):
    """Bắt tay TCP tới các đích, gắn vào IP nguồn của card -> (trung vị ms hoặc None, % lỗi)."""
    ok, n = [], 0
    for t in targets:
        host, port = _target(t)
        for _ in range(per_target):
            n += 1
            t0 = time.perf_counter()
            try:
                with socket.create_connection((host, port), timeout=timeout, source_address=(src_ip, 0)):
                    ok.append((time.perf_counter() - t0) * 1000)
            except OSError:
                pass
    if not n:
        return None, 100.0
    return (statistics.median(ok) if ok else None), 100.0 * (n - len(ok)) / n


def probe_speed(src_ip, nbytes=300000, timeout=15):
    """Tải thử `nbytes` từ speed.cloudflare.com qua card -> kbps (None nếu lỗi)."""
    t0 = time.perf_counter()
    conn = http.client.HTTPSConnection("speed.cloudflare.com", timeout=timeout, source_address=(src_ip, 0))
    try:
        conn.request("GET", f"/__down?bytes={int(nbytes)}", headers={"User-Agent": "NetWatchdog"})
        got = len(conn.getresponse().read())
        dt = time.perf_counter() - t0
        return got * 8 / dt / 1000 if got and dt > 0 else None
    except (OSError, http.client.HTTPException):
        return None
    finally:
        conn.close()


def fmt_speed(kbps):
    if kbps is None:
        return "?"
    return f"{kbps / 1000:.1f} Mbps" if kbps >= 1000 else f"{kbps:.0f} kbps"


# ---------------------------------------------------------------- Bộ theo dõi
class QualityMonitor:
    """Đo từng card, phát hiện card đang dùng bị chậm, chuyển sang card tốt hơn, giữ Wi-Fi dự phòng.

    Các hàm hệ thống được truyền vào (để test được): list_ifs, ssids, rtt, speed, set_metric, connect_wifi.
    `notify(text)` gửi Telegram (qua hàng đợi của NetWatchdog)."""

    def __init__(self, get_cfg, notify, list_ifs=list_interfaces, ssids=wifi_ssids, rtt=probe_rtt,
                 speed=probe_speed, set_metric=set_metric, connect_wifi=None, clock=time.time):
        self.get_cfg, self.notify, self.clock = get_cfg, notify, clock
        self.list_ifs, self.ssids, self.rtt, self.speed, self.set_metric = list_ifs, ssids, rtt, speed, set_metric
        self.connect_wifi = connect_wifi or sysops.connect_wifi
        self.stats = {}            # tên card -> {rtt, loss, kbps, speed_ts, streak, bad, ssid, ...}
        self.cooldown_until = 0.0
        self.standby_tried = {}    # tên card -> lần thử kết nối Wi-Fi gần nhất
        self.last = {"ts": 0, "active": None, "ifs": []}
        self.all_bad_notified = False

    def Q(self):
        return self.get_cfg()["quality"]

    def is_bad(self, s):
        Q = self.Q()
        if s.get("rtt") is None or s.get("loss", 0) >= Q["loss_bad_pct"]:
            return True
        if s["rtt"] > Q["rtt_bad_ms"]:
            return True
        return bool(Q["speed_test"] and s.get("kbps") is not None and s["kbps"] < Q["speed_bad_kbps"])

    def score(self, name):
        """Càng nhỏ càng tốt: ưu tiên độ trễ thấp, rồi tốc độ cao."""
        s = self.stats.get(name, {})
        return (s.get("rtt") or 1e9) - (min(s.get("kbps") or 0, 50000) / 1000)

    def measure(self, ifs, now):
        Q = self.Q()
        ssid = self.ssids() if any(it["kind"] == "wifi" for it in ifs) else {}
        for it in ifs:
            s = self.stats.setdefault(it["name"], {"streak": 0, "speed_ts": 0, "kbps": None})
            s["ssid"] = ssid.get(it["name"], "")
            if not usable(it):
                s.update(rtt=None, loss=100.0, bad=True, usable=False)
                continue
            s["usable"] = True
            s["rtt"], s["loss"] = self.rtt(it["ip"], Q["targets"], Q["probes_per_target"], Q["timeout_sec"])
            if Q["speed_test"] and s["rtt"] is not None and now - s["speed_ts"] >= Q["speed_every_min"] * 60:
                s["kbps"], s["speed_ts"] = self.speed(it["ip"], Q["speed_bytes"]), now
            s["bad"] = self.is_bad(s)
            s["streak"] = s["streak"] + 1 if s["bad"] else 0

    def standby(self, ifs, now):
        """Card Wi-Fi đang lên nhưng chưa vào mạng nào: thử kết nối một mạng đã chọn mà card khác chưa dùng."""
        cfg, Q = self.get_cfg(), self.Q()
        profiles = cfg["recovery"].get("wifi_profiles") or []
        if not (Q["standby_wifi"] and profiles):
            return []
        used = {s.get("ssid") for s in self.stats.values() if s.get("ssid")}
        done = []
        for it in ifs:
            if it["kind"] != "wifi" or it["status"] not in ("Up", "Disconnected") or self.stats.get(it["name"], {}).get("ssid"):
                continue
            if now - self.standby_tried.get(it["name"], 0) < Q["standby_retry_min"] * 60:
                continue
            self.standby_tried[it["name"]] = now
            for prof in sorted(profiles, key=lambda p: p in used):        # ưu tiên mạng chưa card nào dùng
                ok, out = self.connect_wifi(prof, it["name"])
                log.info("Wi-Fi dự phòng: kết nối %s vào '%s': %s %s", it["name"], prof, ok, str(out)[:120])
                if ok:
                    used.add(prof)
                    done.append(f"{it['name']} → {prof}")
                    break
        return done

    def switch_to(self, ifs, best, reason):
        """Đặt metric: card `best` thấp nhất, các card khác cao hơn. Trả về (ok, thông báo)."""
        Q = self.Q()
        errs = []
        for it in ifs:
            if not usable(it):
                continue
            want = Q["metric_best"] if it["name"] == best["name"] else Q["metric_other"]
            if it.get("if_metric") != want:
                ok, out = self.set_metric(it["index"], want)
                log.info("Đặt metric %s cho %s: %s %s", want, it["name"], ok, str(out)[:200])
                if not ok:
                    errs.append(f"{it['name']}: {str(out)[:120]}")
        self.cooldown_until = self.clock() + Q["switch_cooldown_min"] * 60
        return not errs, reason + ("" if not errs else "\n❌ Lỗi đổi metric: " + "; ".join(errs))

    def describe(self, name, active=None):
        s = self.stats.get(name, {})
        where = f"{name}" + (f" · {s['ssid']}" if s.get("ssid") else "")
        if not s.get("usable"):
            return f"⚫ {where} · không dùng được"
        mark = "⭐" if name == active else ("🐢" if s.get("bad") else "🟢")
        return f"{mark} {where} · {self.short(name)}" + (" · chậm" if name == active and s.get("bad") else "")

    def tick(self):
        """Một chu kỳ đo + xử lý. Trả về danh sách tin đã gửi (để test)."""
        now = self.clock()
        Q, sent = self.Q(), []
        if not Q["enabled"]:
            return sent
        ifs = self.list_ifs()
        if not ifs:
            return sent
        self.measure(ifs, now)
        for name in list(self.stats):                     # quên card đã bị rút ra
            if name not in {it["name"] for it in ifs}:
                del self.stats[name]
        active = active_interface(ifs)
        self.last = {"ts": now, "active": active["name"] if active else None, "ifs": ifs}
        joined = self.standby(ifs, now)
        if joined:
            log.info("Wi-Fi dự phòng đã kết nối: %s", ", ".join(joined))
        if not active:
            return sent
        a = self.stats[active["name"]]
        if a["streak"] < Q["bad_checks"]:
            self.all_bad_notified = False
            return sent
        good = [it for it in ifs if usable(it) and it["name"] != active["name"] and not self.stats[it["name"]]["bad"]]
        if not good:
            if not self.all_bad_notified:
                self.all_bad_notified = True
                msg = ("🐢 Mạng chậm trên mọi card, chưa có card nào tốt hơn để chuyển.\n"
                       + "\n".join(self.describe(it["name"], active["name"]) for it in ifs))
                self.notify(msg)
                sent.append(msg)
            return sent
        if now < self.cooldown_until:
            return sent
        best = min(good, key=lambda it: self.score(it["name"]))
        why = (f"🔀 Mạng chậm qua {active['name']}" + (f" ({a['ssid']})" if a.get("ssid") else "")
               + f": {self.short(active['name'])} trong {a['streak']} lần đo liên tiếp.\n"
               f"Chuyển sang {best['name']}" + (f" ({self.stats[best['name']]['ssid']})" if self.stats[best['name']].get("ssid") else "")
               + f": {self.short(best['name'])}.")
        ok, msg = self.switch_to(ifs, best, why)
        self.notify(msg)
        sent.append(msg)
        for s in self.stats.values():
            s["streak"] = 0
        return sent

    def short(self, name):
        s = self.stats.get(name, {})
        parts = ["không phản hồi" if s.get("rtt") is None else f"{s['rtt']:.0f} ms"]
        if s.get("kbps") is not None:
            parts.append(fmt_speed(s["kbps"]))
        if s.get("loss"):
            parts.append(f"lỗi {s['loss']:.0f}%")
        return ", ".join(parts)

    def report(self):
        """Bảng chất lượng từng card cho /net và /status."""
        if not self.last["ts"]:
            return "📶 Chưa đo chất lượng mạng lần nào, chờ chút."
        active = self.last["active"]
        lines = [f"📶 Chất lượng từng card (⭐ = đang dùng cho Internet), đo lúc {time.strftime('%H:%M:%S', time.localtime(self.last['ts']))}:"]
        for it in sorted(self.last["ifs"], key=lambda it: (not usable(it), effective_metric(it))):
            lines.append(self.describe(it["name"], active))
        if self.clock() < self.cooldown_until:
            lines.append(f"⏳ Vừa chuyển card, chờ {int((self.cooldown_until - self.clock()) // 60) + 1} phút mới xét chuyển tiếp.")
        return "\n".join(lines)

    def force(self, query):
        """Lệnh /net <tên card>: ưu tiên ngay card có tên khớp (không phân biệt hoa thường)."""
        ifs = self.list_ifs()
        q = (query or "").strip().lower()
        hits = [it for it in ifs if usable(it) and (it["name"].lower() == q or q in it["name"].lower())]
        if len(hits) != 1:
            names = ", ".join(it["name"] for it in ifs if usable(it)) or "(không có)"
            return f"Không xác định được card “{query}”. Card dùng được: {names}"
        ok, msg = self.switch_to(ifs, hits[0], f"🔀 Đã ưu tiên {hits[0]['name']} theo lệnh /net.")
        return msg
