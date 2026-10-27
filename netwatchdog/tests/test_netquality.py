"""Test logic phát hiện mạng chậm và chuyển card, với card mạng và số đo giả lập (không đụng mạng thật).

Chạy: python -m unittest discover -s tests   (trong thư mục netwatchdog)
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import netquality as nq  # noqa: E402
import netwatchdog as nw  # noqa: E402


def iface(name, index, ip, gw="10.0.0.1", metric=35, kind="wifi", status="Up"):
    return {"index": index, "name": name, "desc": "", "status": status, "kind": kind, "ip": ip, "gateway": gw,
            "if_metric": metric, "auto_metric": True, "route_metric": 0}


class FakeNet:
    """Ba card: Ethernet (đi qua modem, đang được ưu tiên), Wi-Fi 2, Wi-Fi 3. Đặt metric thì đổi card đang dùng."""

    def __init__(self):
        self.ifs = [iface("Ethernet", 17, "192.168.93.251", metric=25, kind="ethernet"),
                    iface("Wi-Fi 2", 19, "10.5.1.1"), iface("Wi-Fi 3", 20, "10.4.1.1")]
        self.rtt = {"192.168.93.251": 40, "10.5.1.1": 45, "10.4.1.1": 50}
        self.kbps = {"192.168.93.251": 9000, "10.5.1.1": 6000, "10.4.1.1": 9000}
        self.ssid = {"Wi-Fi 2": "SVTECH Guest", "Wi-Fi 3": "SVTECH Staff"}
        self.calls = []

    def list_ifs(self):
        return [dict(i) for i in self.ifs]

    def probe(self, ip, *a):
        r = self.rtt.get(ip)
        return (r, 0.0) if r is not None else (None, 100.0)

    def speed(self, ip, *a):
        return self.kbps.get(ip)

    def set_metric(self, index, metric):
        self.calls.append(("metric", index, metric))
        for i in self.ifs:
            if i["index"] == index:
                i["if_metric"] = metric
        return True, "OK"

    def connect(self, profile, interface):
        self.calls.append(("wifi", interface, profile))
        self.ssid[interface] = profile
        for i in self.ifs:
            if i["name"] == interface:
                i.update(status="Up", ip="10.9.9.9", gateway="10.9.0.1")
        return True, "OK"


class QualityTest(unittest.TestCase):
    def setUp(self):
        self.net, self.sent, self.t = FakeNet(), [], 1_700_000_000.0
        self.cfg = nw.deep_merge(nw.DEFAULT_CONFIG, {"recovery": {"wifi_profiles": ["SVTECH Guest", "SVTECH Staff"]}})
        n = self.net
        self.m = nq.QualityMonitor(lambda: self.cfg, self.sent.append, list_ifs=n.list_ifs, ssids=lambda: dict(n.ssid),
                                   rtt=n.probe, speed=n.speed, set_metric=n.set_metric, connect_wifi=n.connect,
                                   clock=lambda: self.t)

    def tick(self, minutes=1):
        self.t += minutes * 60
        out = self.m.tick()
        self.sent.clear()
        return out

    def active(self):
        return nq.active_interface(self.net.list_ifs())["name"]

    def test_good_network_does_nothing(self):
        for _ in range(5):
            self.assertEqual(self.tick(), [])
        self.assertEqual(self.net.calls, [])
        self.assertEqual(self.active(), "Ethernet")

    def test_slow_modem_switches_to_best_wifi(self):
        self.tick()
        self.net.rtt["192.168.93.251"] = 2500                      # đường modem rất chậm nhưng vẫn kết nối được
        self.assertEqual(self.tick(), [])                          # lần 1, 2: chưa đủ số lần chậm liên tiếp
        self.assertEqual(self.tick(), [])
        msg = self.tick()
        self.assertEqual(len(msg), 1)
        self.assertIn("Mạng chậm qua Ethernet: 2500 ms", msg[0])
        self.assertIn("Chuyển sang Wi-Fi 2 (SVTECH Guest): 45 ms", msg[0])   # độ trễ thấp nhất
        self.assertEqual(self.active(), "Wi-Fi 2")
        self.assertIn(("metric", 17, 100), self.net.calls)
        self.assertIn(("metric", 19, 5), self.net.calls)
        self.assertIn(("metric", 20, 100), self.net.calls)

    def test_low_speed_also_counts_as_slow(self):
        self.tick()
        self.net.kbps["192.168.93.251"] = 120                     # độ trễ bình thường nhưng tải rất chậm
        self.cfg["quality"]["speed_every_min"] = 0
        for _ in range(2):
            self.assertEqual(self.tick(), [])
        self.assertIn("120 kbps", self.tick()[0])
        self.assertNotEqual(self.active(), "Ethernet")

    def test_timeouts_count_as_slow(self):
        self.tick()
        self.net.rtt["192.168.93.251"] = None                     # mọi kết nối qua modem đều quá hạn
        for _ in range(2):
            self.tick()
        self.assertIn("không phản hồi", self.tick()[0])

    def test_cooldown_prevents_flapping(self):
        self.tick()
        self.net.rtt["192.168.93.251"] = 2500
        for _ in range(3):
            self.tick()
        self.assertEqual(self.active(), "Wi-Fi 2")
        self.net.rtt["10.5.1.1"] = 3000                           # card mới cũng chậm ngay sau đó
        for _ in range(3):
            self.assertEqual(self.tick(), [])                     # còn trong 10 phút chờ: chưa chuyển tiếp
        msg = self.tick(10)
        self.assertIn("Chuyển sang Wi-Fi 3", msg[0])
        self.assertEqual(self.active(), "Wi-Fi 3")

    def test_all_slow_notifies_once_without_switching(self):
        self.tick()
        for ip in self.net.rtt:
            self.net.rtt[ip] = 2000
        msgs = []
        for _ in range(6):
            msgs += self.tick()
        self.assertEqual(len(msgs), 1)
        self.assertIn("chậm trên mọi card", msgs[0])
        self.assertFalse([c for c in self.net.calls if c[0] == "metric"])

    def test_standby_connects_idle_wifi_to_unused_profile(self):
        self.net.ifs[2].update(status="Disconnected", ip="", gateway="")    # Wi-Fi 3 đang rảnh
        del self.net.ssid["Wi-Fi 3"]
        self.tick()
        self.assertIn(("wifi", "Wi-Fi 3", "SVTECH Staff"), self.net.calls)  # mạng chưa card nào dùng
        n = len(self.net.calls)
        self.tick()                                               # đã kết nối: không thử lại
        self.assertEqual(len(self.net.calls), n)

    def test_force_and_report(self):
        self.tick()
        self.assertIn("⭐ Ethernet", self.m.report())
        self.assertIn("🟢 Wi-Fi 3 · SVTECH Staff · 50 ms", self.m.report())
        self.assertIn("Đã ưu tiên Wi-Fi 3", self.m.force("wi-fi 3"))
        self.assertEqual(self.active(), "Wi-Fi 3")
        self.assertIn("Không xác định được card", self.m.force("wi-fi"))   # khớp nhiều card
        self.assertIn("Không xác định được card", self.m.force("abc"))

    def test_disabled_does_nothing(self):
        self.cfg["quality"]["enabled"] = False
        self.net.rtt["192.168.93.251"] = 5000
        for _ in range(5):
            self.tick()
        self.assertEqual(self.net.calls, [])


class ParseTest(unittest.TestCase):
    def test_parse_state(self):
        out = ('{"ip":[{"ifIndex":19,"IPAddress":"10.5.128.177"},{"ifIndex":21,"IPAddress":"169.254.1.1"}],'
               '"if":[{"ifIndex":19,"InterfaceMetric":35,"AutomaticMetric":1},{"ifIndex":17,"InterfaceMetric":25,"AutomaticMetric":1}],'
               '"a":[{"ifIndex":19,"Name":"Wi-Fi 2","InterfaceDescription":"Realtek","Status":"Up","MediaType":"Native 802.11","NdisPhysicalMedium":9},'
               '{"ifIndex":17,"Name":"Ethernet","InterfaceDescription":"Intel","Status":"Disabled","MediaType":"802.3","NdisPhysicalMedium":14}],'
               '"rt":[{"ifIndex":19,"NextHop":"10.5.0.1","RouteMetric":0}]}')
        ifs = {i["name"]: i for i in nq.parse_state(out)}
        self.assertEqual((ifs["Wi-Fi 2"]["kind"], ifs["Wi-Fi 2"]["ip"], ifs["Wi-Fi 2"]["gateway"], ifs["Wi-Fi 2"]["if_metric"]),
                         ("wifi", "10.5.128.177", "10.5.0.1", 35))
        self.assertTrue(nq.usable(ifs["Wi-Fi 2"]))
        self.assertEqual((ifs["Ethernet"]["kind"], ifs["Ethernet"]["ip"]), ("ethernet", ""))
        self.assertFalse(nq.usable(ifs["Ethernet"]))
        self.assertEqual(nq.parse_state("lỗi"), [])

    def test_parse_wifi_interfaces(self):
        out = ("\n    Name                   : Wi-Fi 3\n    Description            : Realtek #2\n    State                  : connected\n"
               "    SSID                   : SVTECH Staff\n    BSSID                  : 7c:57\n\n"
               "    Name                   : Wi-Fi 2\n    State                  : disconnected\n")
        self.assertEqual(nq.parse_wifi_interfaces(out), {"Wi-Fi 3": "SVTECH Staff", "Wi-Fi 2": ""})


class CommandTest(unittest.TestCase):
    def test_net_command_and_keyboard(self):
        import tempfile
        d = tempfile.mkdtemp()
        nw.save_json(os.path.join(d, "c.json"), nw.deep_merge(nw.DEFAULT_CONFIG, {}))
        wd = nw.Watchdog(os.path.join(d, "c.json"), os.path.join(d, "s.json"))
        self.assertIn("Chưa đo chất lượng", wd.handle_command("/net"))
        self.assertIn("/net", wd.handle_command("/help"))
        self.assertEqual(wd.BUTTONS["📶 Mạng"], "/net")


if __name__ == "__main__":
    unittest.main()
