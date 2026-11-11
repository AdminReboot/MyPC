"""Test điều khiển máy qua Telegram với màn hình / chuột / bàn phím giả lập.

Chạy: python -m unittest discover -s tests   (trong thư mục netwatchdog)
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import netwatchdog as nw  # noqa: E402
import remote  # noqa: E402


class GridTest(unittest.TestCase):
    def setUp(self):
        self.g = remote.Grid(0, 0, 1920, 1080, cols=10)

    def test_size(self):
        self.assertEqual((self.g.cols, self.g.rows), (10, 6))
        self.assertEqual(self.g.cell_name(9, 5), "J6")

    def test_cell_and_subcells(self):
        self.assertEqual(self.g.target("a1"), (96, 90, "A1"))
        x, y, w, h = self.g.box("B2 5")
        self.assertEqual((round(x), round(y), round(w), round(h)), (256, 240, 64, 60))
        self.assertEqual(self.g.target("b2.5")[:2], (288, 270))
        self.assertEqual(self.g.target("A1 9 1")[:2], self.g.target("a1 91")[:2])
        self.assertEqual(self.g.target("D4 56")[2], "D4 56")

    def test_pixels_and_errors(self):
        self.assertEqual(self.g.target("640 380"), (640, 380, "640,380"))
        for bad in ("K1", "A7", "A0", "Z", "A1 0", "5000 10"):
            with self.assertRaises(ValueError):
                self.g.target(bad)

    def test_negative_virtual_screen(self):
        g = remote.Grid(-1920, 0, 3840, 1080, cols=20)
        self.assertEqual(g.target("A1")[:2], (-1824, 90))


class KeysTest(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(remote.parse_keys("ctrl+c Enter"), [[0x11, 0x43], [0x0D]])
        self.assertEqual(remote.parse_keys("alt+f4"), [[0x12, 0x73]])
        for bad in ("", "ctrl+xyz", "ctrl+alt+del"):
            with self.assertRaises(ValueError):
                remote.parse_keys(bad)


class RemoteTest(unittest.TestCase):
    def setUp(self):
        self.cfg = nw.deep_merge(nw.DEFAULT_CONFIG, {})
        h, s = remote.hash_pin("2468")
        self.cfg["remote"].update(enabled=True, pin_hash=h, pin_salt=s)
        self.t, self.is_locked = 1_700_000_000.0, False
        self.sent, self.photos, self.deleted, self.actions = [], [], [], []
        sysf = {"screen": lambda: (0, 0, 1920, 1080), "cursor": lambda: (5, 5),
                "render": lambda g, **kw: (b"JPG", "image/jpeg"),
                "click": lambda x, y, b, d: self.actions.append(("click", x, y, b, d)),
                "scroll": lambda n: self.actions.append(("scroll", n)),
                "press": lambda c: self.actions.append(("press", c)),
                "type": lambda s: self.actions.append(("type", s))}
        self.rc = remote.RemoteControl(lambda: self.cfg, self.sent.append,
                                       lambda img, cap, markup=None, mime="": self.photos.append(cap),
                                       delete_message=self.deleted.append, locked=lambda: self.is_locked,
                                       clock=lambda: self.t, system=sysf, sleep=lambda s: None, label=lambda: " PC1:")

    def cmd(self, c, rest="", msg=None):
        return self.rc.handle(c, rest, msg or {"chat": {"type": "private"}, "message_id": 7})

    def test_needs_pin_and_deletes_it(self):
        self.assertIn("/dk", self.cmd("/mh"))
        self.assertEqual(self.actions + self.photos, [])
        self.assertIn("Sai", self.cmd("/dk", "1111"))
        self.assertEqual(self.cmd("/dk", "2468"), "")
        self.assertEqual(self.deleted, [7, 7])
        self.assertIn("Đã bật điều khiển", self.photos[0])
        self.assertTrue(self.rc.active())

    def test_commands(self):
        self.cmd("/dk", "2468")
        self.cmd("/c", "A1")
        self.cmd("/cp", "640 380")
        self.cmd("/c2", "b2 5")
        self.cmd("/go", "Xin chào")
        self.cmd("/phim", "ctrl+c enter")
        self.cmd("/cuon", "xuong 2")
        self.assertEqual(self.actions, [("click", 96, 90, "left", False), ("click", 640, 380, "right", False),
                                        ("click", 288, 270, "left", True), ("type", "Xin chào"),
                                        ("press", [[0x11, 0x43], [0x0D]]), ("scroll", -2)])
        self.assertEqual(len(self.photos), 7)
        self.assertIn("❓", self.cmd("/c", "Z9"))
        self.cmd("/zoom", "d4")
        self.assertIn("/c D4 5", self.photos[-1])

    def test_lockout_after_wrong_pins(self):
        for _ in range(4):
            self.assertIn("Sai mã PIN", self.cmd("/dk", "0000"))
        self.assertIn("tạm khóa", self.cmd("/dk", "0000"))
        self.assertIn("Nhập sai mã PIN", self.sent[0])
        self.assertIn("Tạm khóa", self.cmd("/dk", "2468"))   # đúng PIN cũng không vào được
        self.t += 61 * 60
        self.assertEqual(self.cmd("/dk", "2468"), "")

    def test_session_expires_and_extends(self):
        self.cmd("/dk", "2468")
        self.t += 9 * 60
        self.cmd("/mh")                                         # mỗi lệnh gia hạn phiên
        self.t += 9 * 60
        self.rc.tick()
        self.assertTrue(self.rc.active())
        self.t += 2 * 60
        self.rc.tick()
        self.assertFalse(self.rc.active())
        self.assertIn("Đã tắt điều khiển", self.sent[-1])
        self.assertIn("/dk", self.cmd("/c", "A1"))

    def test_locked_screen_and_group_chat_and_disabled(self):
        self.cmd("/dk", "2468")
        self.is_locked = True
        self.assertIn("Máy đang khóa", self.cmd("/c", "A1"))
        self.assertEqual(self.actions, [])
        self.assertIn("chat riêng", self.cmd("/mh", "", {"chat": {"type": "group"}}))
        self.cfg["remote"]["enabled"] = False
        self.assertIn("Chưa bật", self.cmd("/mh"))

    def test_callbacks(self):
        self.cmd("/dk", "2468")
        self.rc.callback("rc:k:enter")
        self.rc.callback("rc:w:-3")
        self.assertEqual(self.actions, [("press", [[0x0D]]), ("scroll", -3)])
        self.rc.callback("rc:end")
        self.assertFalse(self.rc.active())

    def test_watchdog_routing_and_autolock_hold(self):
        d = tempfile.mkdtemp()
        nw.save_json(os.path.join(d, "c.json"), self.cfg)
        wd = nw.Watchdog(os.path.join(d, "c.json"), os.path.join(d, "s.json"))
        wd.rc = self.rc
        wd.pm.hold = self.rc.active
        wd.cur_msg = {"chat": {"type": "private"}, "message_id": 9}
        self.assertEqual(wd.handle_command("/dk 2468"), "")
        self.assertEqual(self.deleted, [9])
        wd.handle_command("/go Hello World")
        self.assertEqual(self.actions[-1], ("type", "Hello World"))   # giữ nguyên chữ hoa
        self.assertTrue(wd.pm.hold())
        self.assertIn("/zoom", wd.handle_command("/help"))
        self.assertIn("ĐANG MỞ", self.rc.status_line())


if __name__ == "__main__":
    unittest.main()
