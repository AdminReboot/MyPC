"""Test logic khôi phục với sysops giả lập (chạy được trên mọi hệ điều hành).

Chạy: python -m unittest discover -s tests   (trong thư mục netwatchdog)
"""
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import netwatchdog as nw  # noqa: E402
import sysops  # noqa: E402


class FakeNet:
    """Mạng giả: online khi Wi-Fi/card nằm trong danh sách 'cứu được'."""

    def __init__(self, good_wifi=(), good_adapter=(), reboot_ok=True):
        self.online = False
        self.good_wifi, self.good_adapter, self.reboot_ok = set(good_wifi), set(good_adapter), reboot_ok
        self.calls = []

    def install(self, tc):
        patches = {
            (nw, "check_online"): lambda cfg: (self.online, "fake"),
            (sysops, "connect_wifi"): self.connect_wifi,
            (sysops, "restart_adapter"): self.restart_adapter,
            (sysops, "reboot"): self.reboot,
            (sysops, "uptime_sec"): lambda: 3600.0,
            (sysops, "launch_app"): lambda app: (self.calls.append(("launch", app["path"])) or (True, "OK")),
            (sysops, "is_running"): lambda path: False,
            (sysops, "session_locked"): lambda: False,
            (sysops, "current_ssid"): lambda: "",
        }
        for (mod, name), fn in patches.items():
            old = getattr(mod, name)
            setattr(mod, name, fn)
            tc.addCleanup(setattr, mod, name, old)

    def connect_wifi(self, prof, iface=""):
        self.calls.append(("wifi", prof))
        if prof in self.good_wifi:
            self.online = True
        return True, "ok"

    def restart_adapter(self, name):
        self.calls.append(("adapter", name))
        if name in self.good_adapter:
            self.online = True
        return True, "ok"

    def reboot(self, delay, msg=""):
        self.calls.append(("reboot", delay))
        return self.reboot_ok, "ok"


class Base(unittest.TestCase):
    def make(self, net, **recovery):
        d = tempfile.mkdtemp()
        cfg = nw.deep_merge(nw.DEFAULT_CONFIG, {
            "telegram": {"bot_token": "", "chat_id": ""},
            "recovery": dict({"wifi_profiles": ["A", "B"], "adapters": ["Wi-Fi", "Ethernet"],
                              "wait_after_action_sec": 0, "rounds": 2}, **recovery),
            "apps": [{"path": r"C:\Apps\pos.exe"}],
        })
        nw.save_json(os.path.join(d, "config.json"), cfg)
        net.install(self)
        wd = nw.Watchdog(os.path.join(d, "config.json"), os.path.join(d, "state.json"))
        wd.sleep = lambda sec: False
        wd.state.set(offline_since=time.time() - 120)
        return wd


class RecoveryTest(Base):
    def test_switch_wifi(self):
        net = FakeNet(good_wifi={"B"})
        wd = self.make(net)
        self.assertTrue(wd.recover())
        self.assertEqual(net.calls, [("wifi", "A"), ("wifi", "B")])
        self.assertIn("Wi-Fi 'B'", wd.last_fix)

    def test_restart_adapter_after_wifi(self):
        net = FakeNet(good_adapter={"Ethernet"})
        wd = self.make(net)
        self.assertTrue(wd.recover())
        self.assertEqual(net.calls, [("wifi", "A"), ("wifi", "B"), ("adapter", "Wi-Fi"), ("adapter", "Ethernet")])

    def test_reboot_when_all_fail(self):
        net = FakeNet()
        wd = self.make(net, rounds=2)
        self.assertFalse(wd.recover())
        self.assertEqual(len([c for c in net.calls if c[0] == "wifi"]), 4)
        self.assertEqual(len([c for c in net.calls if c[0] == "adapter"]), 4)
        self.assertEqual(net.calls[-1][0], "reboot")
        self.assertTrue(wd.state.get("pending_launch"))
        self.assertEqual(len(wd.state.get("reboots")), 1)
        texts = [m["text"] for m in wd.state.get("outbox")]
        self.assertTrue(any("KHỞI ĐỘNG LẠI MÁY" in t for t in texts))

    def test_reboot_limit_per_day(self):
        net = FakeNet()
        wd = self.make(net, max_reboots_per_day=2)
        wd.state.set(reboots=[time.time() - 100, time.time() - 200])
        self.assertFalse(wd.recover())
        self.assertNotIn("reboot", [c[0] for c in net.calls])
        self.assertGreater(wd.next_recovery_at, time.time())

    def test_reboot_disabled(self):
        net = FakeNet()
        wd = self.make(net, reboot_enabled=False)
        wd.recover()
        self.assertNotIn("reboot", [c[0] for c in net.calls])

    def test_no_reboot_right_after_boot(self):
        net = FakeNet()
        wd = self.make(net, min_uptime_before_reboot_min=120)  # uptime giả = 60 phút
        wd.recover()
        self.assertNotIn("reboot", [c[0] for c in net.calls])

    def test_fix_while_online_never_reboots(self):
        net = FakeNet()
        wd = self.make(net, wifi_profiles=[], adapters=[])
        net.online = True
        self.assertTrue(wd.recover())
        self.assertNotIn("reboot", [c[0] for c in net.calls])

    def test_failed_reboot_clears_pending(self):
        net = FakeNet(reboot_ok=False)
        wd = self.make(net, wifi_profiles=[], adapters=[])
        wd.recover()
        self.assertFalse(wd.state.get("pending_launch"))


class StartupTest(Base):
    def test_launch_apps_after_watchdog_reboot(self):
        net = FakeNet()
        wd = self.make(net)
        wd.state.set(pending_launch=True, reboot_reason="test")
        wd.on_start(background=False)
        self.assertIn(("launch", r"C:\Apps\pos.exe"), net.calls)
        self.assertFalse(wd.state.get("pending_launch"))
        self.assertEqual(wd.last_fix, "khởi động lại máy")

    def test_no_launch_on_normal_start(self):
        net = FakeNet()
        wd = self.make(net)
        wd.on_start(background=False)
        self.assertFalse([c for c in net.calls if c[0] == "launch"])

    def _launch_setup(self, locked_reads, uptime=3600.0, **launch):
        """Watchdog có ứng dụng cần mở; session_locked() trả lần lượt `locked_reads` rồi False."""
        net = FakeNet()
        wd = self.make(net)
        cfg = nw.load_config(wd.config_path)
        cfg["launch"].update(launch)
        nw.save_json(wd.config_path, cfg)
        wd._cfg = None
        reads = list(locked_reads)
        sysops.session_locked = lambda: reads.pop(0) if reads else False
        sysops.uptime_sec = lambda: uptime
        sleeps, sent = [], []
        wd.sleep = lambda sec: sleeps.append(sec) or False
        wd.tg.send = sent.append
        wd.state.set(pending_launch=True, reboot_reason="test")
        launched = lambda: [c for c in net.calls if c[0] == "launch"]  # noqa: E731
        return wd, sleeps, sent, launched

    def test_launch_waits_for_unlock_then_settles(self):
        # on_start đọc 1 lần (đang khóa), launch_when_ready đọc 1 lần, vòng chờ đọc thêm 3 lần khóa rồi mở
        wd, sleeps, sent, launched = self._launch_setup([True, True, True, True, True], delay_sec=45)
        wd.on_start(background=False)
        self.assertIn("🔒 Máy đang khóa. Sẽ mở 1 ứng dụng sau khi mở khóa và chờ thêm 45 giây.", sent[0])
        self.assertEqual(sleeps, [2, 2, 2, 45])            # chờ mở khóa (mỗi 2 giây), rồi chờ ổn định
        self.assertEqual(len(launched()), 1)
        self.assertIn("🚀 Mở ứng dụng (sau khi mở khóa", sent[1])

    def test_launch_not_before_unlock(self):
        wd, sleeps, sent, launched = self._launch_setup([True] * 50, delay_sec=10)
        calls = []
        wd.sleep = lambda sec: calls.append(len(launched())) or False
        wd.on_start(background=False)
        self.assertEqual(set(calls), {0})                  # suốt lúc chờ chưa mở ứng dụng nào
        self.assertEqual(len(launched()), 1)

    def test_launch_delay_after_boot_even_if_unlocked(self):
        wd, sleeps, sent, launched = self._launch_setup([], uptime=40.0, delay_sec=60)
        wd.on_start(background=False)
        self.assertIn("⏳ Sẽ mở 1 ứng dụng sau 60 giây", sent[0])
        self.assertEqual(sleeps, [60])
        self.assertEqual(len(launched()), 1)

    def test_launch_immediately_when_unlocked_and_long_uptime(self):
        wd, sleeps, sent, launched = self._launch_setup([], uptime=7200.0, delay_sec=60)
        wd.on_start(background=False)
        self.assertEqual(sleeps, [])
        self.assertEqual(len(launched()), 1)

    def test_launch_ignores_lock_when_disabled_and_unlock_timeout(self):
        wd, sleeps, sent, launched = self._launch_setup([True] * 9, wait_unlock=False, delay_sec=0)
        wd.on_start(background=False)
        self.assertEqual((sleeps, len(launched())), ([], 1))
        nw.time.time, real = (lambda t=iter(range(0, 10**6, 40)): next(t)), nw.time.time
        self.addCleanup(setattr, nw.time, "time", real)
        wd, sleeps, sent, launched = self._launch_setup([True] * 999, unlock_timeout_min=1, delay_sec=0)
        wd.on_start(background=False)
        self.assertEqual(len(launched()), 1)
        self.assertIn("máy vẫn khóa sau", sent[-1])

    def test_stop_while_waiting_does_not_launch(self):
        wd, sleeps, sent, launched = self._launch_setup([True] * 9, delay_sec=5)
        wd.sleep = lambda sec: True                        # dịch vụ được yêu cầu dừng
        wd.on_start(background=False)
        self.assertEqual(launched(), [])

    def test_online_report_after_outage(self):
        net = FakeNet()
        wd = self.make(net)
        nw.status_report = lambda *a, **k: "STATUS"
        self.addCleanup(setattr, nw, "status_report", nw.status_report)
        wd.on_online()
        self.assertIsNone(wd.state.get("offline_since"))
        self.assertTrue(any("CÓ MẠNG TRỞ LẠI" in m["text"] for m in wd.state.get("outbox")))


class TelegramQueueTest(Base):
    def test_outbox_kept_until_sent_in_order(self):
        wd = self.make(FakeNet())
        cfg = wd.cfg
        cfg["telegram"].update(bot_token="t", chat_id="1")
        wd._cfg = cfg
        sent, fail = [], [True]

        def send_now(text, chat_id=None, **kw):
            if fail[0]:
                raise OSError("offline")
            sent.append(text)
        wd.tg.send_now = send_now
        wd.tg.send("one")
        wd.tg.send("two")
        self.assertEqual([m["text"] for m in wd.state.get("outbox")], ["one", "two"])
        fail[0] = False
        wd.tg.flush()
        self.assertEqual(sent, ["one", "two"])
        self.assertEqual(wd.state.get("outbox"), [])

    def test_commands_only_from_configured_chat_and_reboot_needs_yes(self):
        net = FakeNet()
        wd = self.make(net)
        self.assertIn("/reboot yes", wd.handle_command("/reboot"))
        self.assertNotIn("reboot", [c[0] for c in net.calls])
        wd.handle_command("/reboot yes")
        self.assertIn(("reboot", 30), net.calls)
        self.assertEqual(wd.handle_command("hello"), "")


class ParserTest(unittest.TestCase):
    def test_wifi_profiles(self):
        out = ("Profiles on interface Wi-Fi:\n\nGroup policy profiles (read only)\n"
               "---------------------------------\n    <None>\n\nUser profiles\n-------------\n"
               "    All User Profile     : Nha_5G\n    All User Profile     : Cafe Tầng 2\n")
        self.assertEqual(sysops.parse_wifi_profiles(out), ["Nha_5G", "Cafe Tầng 2"])

    def test_current_ssid_ignores_bssid(self):
        out = ("    Name                   : Wi-Fi\n    State                  : connected\n"
               "    SSID                   : Nha_5G\n    BSSID                  : aa:bb:cc:dd:ee:ff\n")
        self.assertEqual(sysops.parse_current_ssid(out), "Nha_5G")

    def test_visible_ssids(self):
        out = "Interface name : Wi-Fi\n\nSSID 1 : Nha_5G\n    Network type : Infrastructure\nSSID 2 : \nSSID 3 : Cafe\n"
        self.assertEqual(sysops.parse_visible_ssids(out), ["Nha_5G", "Cafe"])

    def test_adapters_json(self):
        one = '{"Name":"Wi-Fi","Status":"Up","InterfaceDescription":"Intel AX201"}'
        self.assertEqual(sysops.parse_adapters_json(one)[0]["name"], "Wi-Fi")
        many = '[{"Name":"Wi-Fi","Status":"Up"},{"Name":"Ethernet","Status":"Disconnected"}]'
        self.assertEqual([a["name"] for a in sysops.parse_adapters_json(many)], ["Wi-Fi", "Ethernet"])
        self.assertEqual(sysops.parse_adapters_json(""), [])

    def test_config_merge_keeps_defaults(self):
        cfg = nw.deep_merge(nw.DEFAULT_CONFIG, {"check": {"interval_sec": 10}})
        self.assertEqual(cfg["check"]["interval_sec"], 10)
        self.assertEqual(cfg["check"]["fail_threshold"], 3)


if __name__ == "__main__":
    unittest.main()
