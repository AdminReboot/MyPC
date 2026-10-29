"""Test tự khóa máy và cảnh báo thao tác trực tiếp tại máy, với đồng hồ / bộ đếm giả lập.

Chạy: python -m unittest discover -s tests   (trong thư mục netwatchdog)
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import netwatchdog as nw  # noqa: E402
import presence  # noqa: E402


class FakeWatcher:
    error = ""

    def __init__(self):
        self.c = {"mouse_physical": 0, "key_physical": 0, "mouse_injected": 0, "key_injected": 0}

    def snapshot(self):
        return dict(self.c), 0.0


class PresenceTest(unittest.TestCase):
    def setUp(self):
        self.cfg = nw.deep_merge(nw.DEFAULT_CONFIG, {})
        self.t, self.idle, self.is_locked = 1_700_000_000.0, 0.0, False
        self.w, self.sent, self.photos, self.locks = FakeWatcher(), [], [], []
        self.m = presence.PresenceMonitor(
            lambda: self.cfg, self.sent.append, send_photo=lambda png, cap: self.photos.append(cap), watcher=self.w,
            idle=lambda: self.idle, locked=lambda: self.is_locked, lock=self._lock, screenshot=lambda: b"PNG",
            clock=lambda: self.t, label=lambda: " PC1:")

    def _lock(self):
        self.locks.append(self.t)
        self.is_locked = True
        return True, "OK"

    def tick(self, sec=2, **add):
        self.t += sec
        for k, v in add.items():
            self.w.c[k] += v
        out = self.m.tick()
        return out

    def test_auto_lock_after_idle(self):
        self.idle = 4 * 60
        self.tick()
        self.assertEqual(self.locks, [])
        self.idle = 5 * 60
        self.tick()
        self.assertEqual(len(self.locks), 1)
        self.tick()                                            # đã khóa: không khóa lại
        self.assertEqual(len(self.locks), 1)

    def test_auto_lock_disabled(self):
        self.cfg["presence"]["auto_lock"] = False
        self.idle = 3600
        self.tick()
        self.assertEqual(self.locks, [])

    def test_remote_input_never_alerts(self):
        self.tick()
        for _ in range(20):
            self.assertEqual(self.tick(key_injected=30, mouse_injected=200), [])
        self.assertEqual(self.photos + self.sent, [])

    def test_physical_key_alerts_once_with_screenshot_then_ends(self):
        self.tick()
        out = self.tick(key_physical=3)
        self.assertEqual(len(out), 1)
        self.assertIn("⚠️ PC1: Có người dùng bàn phím TRỰC TIẾP tại máy", self.photos[0])
        for _ in range(10):                                    # vẫn đang dùng: không báo lại
            self.assertEqual(self.tick(30, key_physical=2, mouse_physical=40), [])
        for _ in range(9):                                     # im lặng 9 phút: chưa hết lượt
            self.assertEqual(self.tick(60), [])
        end = self.tick(60)
        self.assertIn("Hết thao tác trực tiếp tại máy", end[0])
        self.assertIn("23 phím", end[0])
        self.assertEqual(len(self.tick(key_physical=1)), 1)   # lượt mới: báo lại

    def test_small_mouse_jitter_is_ignored(self):
        self.tick()
        for _ in range(20):                                    # rung nhẹ: 2 sự kiện mỗi 30 giây
            self.assertEqual(self.tick(30, mouse_physical=2), [])
        self.assertEqual(len(self.tick(2, mouse_physical=20)), 1)   # di chuột thật

    def test_no_screenshot_when_locked_or_disabled(self):
        self.tick()
        self.is_locked = True
        self.cfg["presence"]["auto_lock"] = False
        self.tick(key_physical=1)
        self.assertEqual(self.photos, [])
        self.assertIn("Có người dùng bàn phím", self.sent[0])

    def test_status_line(self):
        self.assertIn("Tự khóa sau 5 phút", self.m.status_line())
        self.tick()
        self.tick(key_physical=1)
        self.assertIn("đang có người thao tác tại máy", self.m.status_line())

    def test_lock_command_and_help(self):
        import tempfile
        d = tempfile.mkdtemp()
        nw.save_json(os.path.join(d, "c.json"), nw.deep_merge(nw.DEFAULT_CONFIG, {}))
        wd = nw.Watchdog(os.path.join(d, "c.json"), os.path.join(d, "s.json"))
        called = []
        orig = presence.lock_workstation
        presence.lock_workstation = lambda: called.append(1) or (True, "OK")
        try:
            self.assertIn("Đã khóa máy", wd.handle_command("/lock"))
        finally:
            presence.lock_workstation = orig
        self.assertEqual(called, [1])
        self.assertIn("/lock", wd.handle_command("/help"))


if __name__ == "__main__":
    unittest.main()
