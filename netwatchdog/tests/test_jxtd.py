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

    def test_task_status(self):
        J = self.cfg["jxtd"]
        for task, want in (("Luyện công", "ok"), ("NV Mặc Thạch (55 / 100)", "ok"), ("Chờ vào trận đấu", "ok"),
                           ("4147 / 655", "ok"), ("Phúc lợi", "ok"), ("NV Nghĩa quân", "ok"),
                           ("Lỗi: Kiểm tra đầy hành trang", "danger"),
                           ("Nghỉ <15 phút> do về thành liên tục <5 lần> trong <10 phút>", "danger"),
                           ("<Mất kết nối>", "idle"), ("-", "idle"), ("", "idle"), ("Treo 47(s)", "idle"),
                           ("<Đăng nhập (13)>", "idle"), ("Hoàn thành", "idle")):
            self.assertEqual(jxtd.task_status(task, J), want, task)

    def test_changing_task_is_silent(self):
        self.tick()
        for task in ("NV Mặc Thạch (55 / 100)", "NV Mặc Thạch (56 / 100)", "Phong Lăng Độ", "Luyện công"):
            self.reader.rows[0] = row("[0]A", task=task)
            self.assertEqual(self.tick(40), [])

    def test_danger_task_alerts_after_confirm_then_recovers(self):
        self.tick()
        self.reader.rows[0] = row("[0]A", task="Lỗi: Kiểm tra đầy hành trang")
        self.assertEqual(self.tick(), [])              # lần 1, 2: chờ xác nhận
        self.assertEqual(self.tick(), [])
        m = self.tick()[0]
        self.assertIn("🚨 [0]A đang đứng chơi", m)
        self.assertIn("Lỗi: Kiểm tra đầy hành trang", m)
        self.assertIn("Thẻ tháng", m)                  # kèm đầy đủ thông tin nhân vật
        self.assertEqual(self.tick(10), [])
        self.assertIn("Vẫn còn", self.tick(25)[0])     # nhắc lại sau 30 phút
        self.reader.rows[0] = row("[0]A", task="<Mất kết nối>")
        self.assertEqual(self.tick(), [])              # vẫn kẹt, chưa phải hoạt động lại
        self.reader.rows[0] = row("[0]A", task="Luyện công")
        self.assertIn("🟢 [0]A hoạt động lại: Luyện công", self.tick()[0])

    def test_transient_error_is_ignored(self):
        self.tick()
        self.reader.rows[0] = row("[0]A", task="Lỗi: Hết thời gian đăng nhập")
        self.assertEqual(self.tick(), [])
        self.reader.rows[0] = row("[0]A")
        self.assertEqual(self.tick(), [])

    def test_idle_alerts_only_after_idle_min(self):
        self.tick()
        self.reader.rows[0] = row("[0]A", task="<Mất kết nối>")
        for _ in range(5):                             # 0..4 phút: im lặng
            self.assertEqual(self.tick(), [])
        self.assertIn("⚠️ [0]A đang đứng chơi (5 phút)", self.tick()[0])
        self.reader.rows[0] = row("[0]A", task="-", checked=False)   # bỏ tick: cảnh báo riêng lo
        msgs = self.tick()
        self.assertTrue(any("bỏ tick" in m for m in msgs))

    def test_extra_columns_shown(self):
        headers = HEADERS + ["N. động", "Xu/Kim đỉnh", "Ô trống", "Vé MT", "Hạn phù", "Nhiệm vụ MT", "Cột lạ"]
        desc = ("Tác vụ: Luyện công, EXP/giờ: 1.5m/h, Thu nhập: 13.3 vạn, Ngân lượng: 427.4 vạn, "
                "Cấp/EXP: Lv109 (57.1%), Phù/chết: 14 / 0, Thẻ tháng: 238h / 238h, N. động: 35, "
                "Xu/Kim đỉnh: 58 / 3010, Ô trống: 33 / 27, Vé MT: 17, Hạn phù: - / 8d / -, Nhiệm vụ MT: 4 / 100, Cột lạ: x")
        r = jxtd.make_row("[0]A", desc, 0x10, headers)
        t = jxtd.fmt_row(r)
        self.assertIn("🎒 Ô trống 33 / 27 · 🎟 Vé MT 17", t)
        self.assertIn("📜 NV MT 4 / 100 · ⚡ N.động 35", t)
        self.assertIn("📿 Hạn phù - / 8d / -", t)
        self.assertIn("• Cột lạ: x", t)
        self.assertNotIn("Ô trống", jxtd.fmt_row(row("[0]B")))   # không có cột thì không hiện
        folder = os.path.join(self.tmp, "hx")
        jxtd.write_history(jxtd.Snapshot(ts=self.t, found=True, rows=[r]), folder, 90)
        jxtd.write_history(jxtd.Snapshot(ts=self.t + 60, found=False), folder, 90)
        import csv
        with open(os.path.join(folder, os.listdir(folder)[0]), encoding="utf-8-sig", newline="") as f:
            data = list(csv.DictReader(f))
        self.assertEqual((data[0]["o_trong"], data[0]["han_phu"], data[0]["ghi_chu"]), ("33 / 27", "- / 8d / -", ""))
        self.assertEqual(data[1]["ghi_chu"], "không thấy jxtdAuto")

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


class CommandTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.cfg = nw.deep_merge(nw.DEFAULT_CONFIG, {"jxtd": {"enabled": True, "report_on_start": False,
                                                              "report_min": 0, "startup_grace_min": 0}})
        self.reader = FakeReader()
        self.reader.rows = [row("[0]BuffThoai"), row("[1]VụtĐêEm", task="Đứng im", income="-380 lượng")]
        self.state = nw.State(os.path.join(self.tmp, "s.json"))
        self.hist = os.path.join(self.tmp, "h")
        self.mon = jxtd.JxMonitor(lambda: self.cfg, self.state, lambda m: None, self.hist, reader=self.reader)
        self.mon.started = 0

    def test_filter_without_diacritics(self):
        self.mon.tick()
        self.assertEqual([r.name for r in jxtd.filter_rows(self.reader.rows, "vut dem")], [])
        self.assertEqual([r.name for r in jxtd.filter_rows(self.reader.rows, "vutdê")], ["[1]VụtĐêEm"])
        t = self.mon.report_text("VUT")
        self.assertIn("[1]VụtĐêEm", t)
        self.assertNotIn("BuffThoai", t)
        self.assertIn("Không thấy nhân vật", self.mon.report_text("abc"))
        self.assertIn("BuffThoai", self.mon.report_text(""))

    def test_short(self):
        self.mon.tick()
        lines = self.mon.short_text().splitlines()
        self.assertEqual(len(lines), 3)
        self.assertEqual(lines[2], "✅ [1]VụtĐêEm · Đứng im · 1.8m/h · -380 lượng")

    def test_day_summary(self):
        from datetime import datetime
        day = datetime(2026, 10, 3, 8, 0)
        def r(task, deaths, level):
            x = row("[0]BuffThoai", task=task, deaths=deaths)
            x.level = level
            return [x]
        snaps = [r("Luyện công", "3 / 0", "Lv109 (39.7%)"),
                 None,  # một lần không thấy jxtdAuto
                 r("Về thành", "4 / 1", "Lv109 (45.2%)"),
                 r("Luyện công", "5 / 2", "Lv110 (1.5%)")]
        for i, rows in enumerate(snaps):
            s = jxtd.Snapshot(ts=day.timestamp() + i * 60, found=rows is not None, rows=rows or [])
            jxtd.write_history(s, self.hist, 90)
        t = jxtd.day_summary(self.cfg, self.hist, day)
        self.assertIn("ngày 03/10 (08:00 → 08:03)", t)
        self.assertIn("Không thấy jxtdAuto ≈ 1 phút", t)
        self.assertIn("Lv109 39.7% → Lv110 1.5% (+1 cấp)", t)
        self.assertIn("chết 2 · 🔄 đổi tác vụ 2", t)
        self.assertIn("Chưa có dữ liệu", jxtd.day_summary(self.cfg, self.hist, datetime(2026, 1, 1)))

    def test_encode_png(self):
        png = jxtd.encode_png(2, 1, bytes([0, 0, 255, 0, 255, 0, 0, 0]))  # đỏ, xanh dương (BGRX)
        self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))
        import struct
        import zlib
        self.assertEqual(struct.unpack(">II", png[16:24]), (2, 1))
        idat = png.index(b"IDAT")
        n = struct.unpack(">I", png[idat - 4:idat])[0]
        self.assertEqual(zlib.decompress(png[idat + 4:idat + 4 + n]), b"\x00\xff\x00\x00\x00\x00\xff")

    def test_watchdog_routes_commands(self):
        cfg_path = os.path.join(self.tmp, "cfg.json")
        nw.save_json(cfg_path, self.cfg)
        wd = nw.Watchdog(cfg_path, os.path.join(self.tmp, "s2.json"))
        self.assertIn("Đang khởi động", wd.handle_command("/jx_gon"))
        wd.jx = self.mon
        self.mon.tick()
        self.assertIn("·", wd.handle_command("/jx_gon"))
        self.assertIn("[1]VụtĐêEm", wd.handle_command("/jx vut"))
        self.assertIn("/jx_anh", wd.handle_command("/help"))
        self.cfg["jxtd"]["enabled"] = False
        nw.save_json(cfg_path, self.cfg)
        wd._cfg = None  # buộc nạp lại (mtime có thể trùng trong cùng giây)
        self.assertIn("Chưa bật", wd.handle_command("/jx_homnay"))


class StatsTest(unittest.TestCase):
    def setUp(self):
        from datetime import datetime
        self.tmp = tempfile.mkdtemp()
        self.hist = os.path.join(self.tmp, "h")
        self.cfg = nw.deep_merge(nw.DEFAULT_CONFIG, {"machine_name": "PC1"})
        self.day = datetime(2026, 10, 3, 8, 58)
        # A: 1.8m/h, tiền 100 -> 101 -> 102 ... vạn; B: 0.9m/h, tiền không đổi; có 1 lần không thấy jxtdAuto
        for i in range(5):
            a, b = row("[0]A"), row("[1]B")
            a.money, a.level = f"{100 + i}.0 vạn", f"Lv109 ({39.0 + i:.1f}%)"
            b.exp, b.money = "900k/h", "50.0 vạn"
            s = jxtd.Snapshot(ts=self.day.timestamp() + i * 60, found=i != 2, rows=[a, b] if i != 2 else [])
            jxtd.write_history(s, self.hist, 90)

    def test_parse_units(self):
        self.assertEqual(jxtd.parse_exp_rate("1.8m/h"), 1.8e6)
        self.assertEqual(jxtd.parse_exp_rate("950k/h"), 950e3)
        self.assertIsNone(jxtd.parse_exp_rate("?"))
        self.assertEqual(jxtd.parse_van("225.0 vạn"), 225.0)
        self.assertAlmostEqual(jxtd.parse_van("-4214 lượng"), -0.4214)
        self.assertEqual(jxtd.fmt_exp(1.8e6), "1.8m")
        self.assertEqual(jxtd.fmt_van(-0.04), "0.0")

    def test_compute_by_hour(self):
        recs = jxtd.load_history(self.hist, self.day.date(), self.day.date())
        self.assertEqual(len(recs), 8)
        s = jxtd.compute_stats(recs, by="hour")
        self.assertEqual([k.hour for k, _ in s["buckets"]], [8, 9])
        a = s["chars"]["[0]A"]
        # các khoảng 1 phút (08:58→59, 09:01→02) và 2 phút (08:59→09:01) đều dưới ngưỡng gián đoạn
        self.assertAlmostEqual(a["exp"], 1.8e6 * 4 / 60)
        self.assertAlmostEqual(a["van"], 4.0)
        self.assertAlmostEqual(s["chars"]["[1]B"]["van"], 0.0)
        self.assertAlmostEqual(s["buckets"][0][1]["van"], 1.0)  # 08:58 → 08:59
        self.assertAlmostEqual(s["total_exp"], (1.8e6 + 0.9e6) * 4 / 60)
        self.assertEqual(s["active_hours"], 2)
        only_b = jxtd.compute_stats(recs, names=["[1]B"], by="hour")
        self.assertEqual(list(only_b["chars"]), ["[1]B"])

    def test_compute_by_day_fills_range(self):
        start, end = jxtd.stats_range("day", self.day.date(), 3)
        s = jxtd.compute_stats(jxtd.load_history(self.hist, start, end), by="day", start=start, end=end)
        self.assertEqual([k.day for k, _ in s["buckets"]], [1, 2, 3])
        self.assertEqual(s["buckets"][0][1]["chars"], set())

    def test_match_names_exact_and_multi(self):
        names = ["[1]A", "[1]AB", "[2]VụtĐêm"]
        self.assertEqual(jxtd.match_names(names, "[1]A"), ["[1]A"])
        self.assertEqual(jxtd.match_names(names, "1]a, vut"), ["[1]A", "[1]AB", "[2]VụtĐêm"])
        self.assertEqual(jxtd.match_names(names, ""), names)

    def test_build_stats_text(self):
        t, names = jxtd.build_stats(self.cfg, self.hist, "hour", day=self.day.date())
        self.assertEqual(names, ["[0]A", "[1]B"])
        self.assertIn("theo giờ · 03/10 [PC1]", t)
        self.assertIn("Tất cả (2 nhân vật)", t)
        self.assertIn("<pre>", t)
        self.assertIn("[0]A: 120k EXP · +4.0 vạn · Lv109 +4.0%", t)
        t, _ = jxtd.build_stats(self.cfg, self.hist, "hour", "b", day=self.day.date())
        self.assertIn("👤 [1]B", t)
        self.assertIn("NL hiện tại: 50.0 vạn", t)
        t, _ = jxtd.build_stats(self.cfg, self.hist, "day", "zzz", day=self.day.date())
        self.assertIn("Không thấy nhân vật", t)

    def test_telegram_buttons_and_callbacks(self):
        cfg_path = os.path.join(self.tmp, "cfg.json")
        nw.save_json(cfg_path, self.cfg)
        wd = nw.Watchdog(cfg_path, os.path.join(self.tmp, "s.json"))
        kb = wd.reply_keyboard()["keyboard"]
        self.assertEqual(len(kb), 3)
        for row_ in kb:
            for b in row_:
                self.assertIn(b["text"], wd.BUTTONS)
        help_ = wd.handle_command("❓ Trợ giúp")
        self.assertIn("/jx_gio", help_)
        self.assertIn("keyboard", help_.markup)
        r = wd.handle_command("📊 Theo giờ")  # nút bấm = lệnh /jx_gio
        self.assertTrue(r.html)
        texts = [b["text"] for row_ in r.markup["inline_keyboard"] for b in row_]
        self.assertIn("✅ 👥 Tất cả", texts)
        r = wd.handle_callback("jx:d:[1]B")
        self.assertTrue(r.edit)
        self.assertIn("theo ngày", r)
        self.assertEqual(wd.handle_callback("other"), "")
        self.assertLessEqual(len(nw.cb_data("h", "Đ" * 40).encode()), 64)


if __name__ == "__main__":
    unittest.main()
