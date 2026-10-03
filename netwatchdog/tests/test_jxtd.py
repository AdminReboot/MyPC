"""Test phân tích bảng nhân vật jxtdAuto và logic cảnh báo với reader giả lập (không cần jxtdAuto).

Chạy: python -m unittest discover -s tests   (trong thư mục netwatchdog)
"""
import copy
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import jxtd  # noqa: E402
import netwatchdog as nw  # noqa: E402

HEADERS = ["Tên nhân vật", "Tác vụ", "EXP/giờ", "Thu nhập", "Ngân lượng", "Cấp/EXP", "Phù/chết", "Thẻ tháng"]


def row(name, task="Luyện công", income="13.0 vạn", deaths="3 / 0", card="359h / 359h", checked=True):
    desc = (f"Tác vụ: {task}, EXP/giờ: 1.8m/h, Thu nhập: {income}, Ngân lượng: 225.0 vạn, "
            f"Cấp/EXP: Lv109 (39.6%), Phù/chết: {deaths}, Thẻ tháng: {card}")
    return jxtd.make_row(name, desc, 0x10 if checked else 0, HEADERS)


class FakeReader:
    def __init__(self):
        self.rows, self.found, self.license = [row("[0]A"), row("[1]B")], True, 252

    def __call__(self, J):
        return jxtd.Snapshot(ts=0, found=self.found, process_running=self.found,
                             license_days=self.license if self.found else None,
                             rows=copy.deepcopy(self.rows) if self.found else [])


class ParseTest(unittest.TestCase):
    def test_parse_full(self):
        r = row("[1]VụtĐêEm", income="-4214 lượng", deaths="34 / 2")
        self.assertEqual((r.task, r.exp, r.income, r.money, r.level, r.deaths, r.card),
                         ("Luyện công", "1.8m/h", "-4214 lượng", "225.0 vạn", "Lv109 (39.6%)", "34 / 2",
                          "359h / 359h"))
        self.assertTrue(r.checked and r.income_negative)
        self.assertEqual(r.death_count, 2)
        self.assertEqual(r.card_hours, 359)

    def test_card_hours_formats(self):
        self.assertEqual(row("X", card="2d 5h / 30d").card_hours, 53)
        self.assertEqual(row("X", card="0h / 720h").card_hours, 0)
        self.assertIsNone(row("X", card="Không có").card_hours)

    def test_task_with_comma(self):
        r = row("[0]A", task="Đứng im, chờ hồi máu")
        self.assertEqual(r.task, "Đứng im, chờ hồi máu")
        self.assertEqual(r.exp, "1.8m/h")

    def test_no_headers_fallback(self):
        r = jxtd.make_row("X", "Tác vụ: Luyện công, Thu nhập: 1 vạn", 0, [])
        self.assertEqual((r.task, r.income, r.checked), ("Luyện công", "1 vạn", False))

    def test_config_merged_into_netwatchdog(self):
        cfg = nw.deep_merge(nw.DEFAULT_CONFIG, {"jxtd": {"enabled": True}})
        self.assertTrue(cfg["jxtd"]["enabled"])
        self.assertEqual(cfg["jxtd"]["cooldown_min"], 30)
        self.assertFalse(nw.DEFAULT_CONFIG["jxtd"]["enabled"])


class MonitorTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.cfg = nw.deep_merge(nw.DEFAULT_CONFIG, {"jxtd": {"enabled": True, "report_on_start": False,
                                                              "report_min": 0, "startup_grace_min": 0}})
        self.reader, self.sent = FakeReader(), []
        self.state = nw.State(os.path.join(self.tmp, "s.json"))
        self.mon = jxtd.JxMonitor(lambda: self.cfg, self.state, self.sent.append,
                                  os.path.join(self.tmp, "h"), reader=self.reader)
        self.mon.started = 0
        self.t = 1_700_000_000.0

    def tick(self, minutes=1):
        self.t += minutes * 60
        self.mon.tick(now=self.t)
        out, self.sent[:] = list(self.sent), []
        return out

    def test_quiet_when_normal(self):
        self.assertEqual(self.tick(), [])
        self.assertEqual(self.tick(), [])
        self.assertTrue(os.listdir(os.path.join(self.tmp, "h")))
        self.assertIn("2 nhân vật", self.state.get("jxtd")["summary"])

    def test_window_missing_confirm_cooldown_recover(self):
        self.tick()
        self.reader.found = False
        self.assertEqual(self.tick(), [])              # lần 1 chưa báo (chờ xác nhận)
        self.assertIn("Không thấy cửa sổ", self.tick()[0])
        self.assertEqual(self.tick(10), [])            # trong 30 phút không nhắc
        self.assertIn("Vẫn còn", self.tick(25)[0])     # nhắc lại sau 30 phút
        self.reader.found = True
        msgs = self.tick()
        self.assertTrue(any("hoạt động lại" in m for m in msgs))
        self.assertFalse(any("biến khỏi" in m for m in msgs))

    def test_flapping_is_suppressed(self):
        self.tick()
        self.reader.rows[0] = row("[0]A", checked=False)
        self.assertIn("bỏ tick", self.tick()[0])
        self.reader.rows[0] = row("[0]A")
        self.assertIn("tick lại", self.tick()[0])
        self.reader.rows[0] = row("[0]A", checked=False)
        self.assertEqual(self.tick(), [])              # vừa báo < 30 phút: chờ
        self.assertIn("bỏ tick", self.tick(30)[0])     # vẫn lỗi sau thời gian chờ: báo

    def test_char_missing_and_back(self):
        self.tick()
        gone = self.reader.rows.pop(1)
        self.assertIn("[1]B biến khỏi", self.tick()[0])
        self.assertEqual(self.tick(), [])
        self.reader.rows.append(gone)
        self.assertIn("[1]B đã trở lại", self.tick()[0])

    def test_negative_income(self):
        self.tick()
        self.reader.rows[0] = row("[0]A", income="-7678 lượng")
        self.assertIn("thu nhập âm", self.tick()[0])
        self.reader.rows[0] = row("[0]A", income="2.0 vạn")
        self.assertIn("hết âm", self.tick()[0])

    def test_task_change_includes_full_info_and_batches(self):
        self.tick()
        self.reader.rows[0] = row("[0]A", task="Đứng im")
        m = self.tick()[0]
        self.assertIn("Luyện công → Đứng im", m)
        self.assertIn("Phù/chết", m)
        self.assertIn("Thẻ tháng", m)
        self.reader.rows[0] = row("[0]A", task="Về thành")
        self.assertEqual(self.tick(), [])              # trong thời gian chờ
        self.assertIn("Đứng im → Về thành", self.tick(30)[0])

    def test_death_increase(self):
        self.tick()
        self.reader.rows[0] = row("[0]A", deaths="4 / 0")   # chỉ số phù tăng: không báo
        self.assertEqual(self.tick(), [])
        self.reader.rows[0] = row("[0]A", deaths="4 / 2")
        self.assertIn("chết thêm 2 lần (tổng 2)", self.tick()[0])

    def test_month_card_low_then_out_then_renew(self):
        self.tick()
        self.reader.rows[0] = row("[0]A", card="20h / 720h")
        self.assertIn("thẻ tháng sắp hết: còn 20h", self.tick()[0])
        self.reader.rows[0] = row("[0]A", card="5h / 720h")
        self.assertEqual(self.tick(), [])              # đã báo rồi
        self.reader.rows[0] = row("[0]A", card="0h / 720h")
        self.assertIn("HẾT thẻ tháng", self.tick(30)[0])
        self.reader.rows[0] = row("[0]A", card="720h / 720h")
        msgs = self.tick()
        self.assertEqual(len(msgs), 1)
        self.assertIn("đã gia hạn thẻ tháng", msgs[0])

    def test_month_card_already_out_reports_once(self):
        self.reader.rows[0] = row("[0]A", card="0h / 0h")
        msgs = self.tick()
        self.assertEqual(len(msgs), 1)
        self.assertIn("HẾT thẻ tháng", msgs[0])
        self.assertEqual(self.tick(40), [])

    def test_license(self):
        self.reader.license = 5
        self.assertIn("chỉ còn 5 ngày", self.tick()[0])
        self.assertEqual(self.tick(60), [])            # 1 lần / 24 giờ
        self.reader.license = 30
        self.assertIn("gia hạn", self.tick()[0])

    def test_report_on_start_and_periodic(self):
        self.cfg["jxtd"].update(report_on_start=True, report_min=60)
        self.assertIn("🎮 jxtdAuto", self.tick()[0])
        self.assertEqual(self.tick(30), [])
        self.assertIn("🎮 jxtdAuto", self.tick(31)[0])
        self.assertIn("[0]A", self.mon.report_text())


if __name__ == "__main__":
    unittest.main()
