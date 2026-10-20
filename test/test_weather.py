import copy
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import shiboken6
from PySide6.QtCore import QByteArray, QDate, QObject, QTimer, Qt, Signal
from PySide6.QtNetwork import QNetworkReply, QNetworkRequest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

import qt_gui
from db import Database


class FakeReply(QObject):
    finished = Signal()

    def __init__(self, parent, payload, error=QNetworkReply.NoError):
        super().__init__(parent)
        self.payload = payload
        self.network_error = error

    def error(self):
        return self.network_error

    def readAll(self):
        return QByteArray(self.payload)

    def abort(self):
        self.network_error = QNetworkReply.OperationCanceledError
        self.finished.emit()


class ShanghaiWeatherTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.assertTrue(hasattr(qt_gui, "ShanghaiWeather"), "缺少上海天气查询组件")
        self.client = qt_gui.ShanghaiWeather()
        self.addCleanup(lambda: shiboken6.delete(self.client))
        self.payload = {
            "timezone": "Asia/Shanghai",
            "current_units": {"temperature_2m": "°C"},
            "current": {"time": "2026-10-12T10:45", "temperature_2m": 24.4, "weather_code": 3},
        }
        self.results = []
        self.failures = []
        self.client.ready.connect(lambda date, text: self.results.append((date, text)))
        self.client.failed.connect(self.failures.append)

    def reply(self, payload=None, error=QNetworkReply.NoError):
        raw = json.dumps(self.payload if payload is None else payload).encode()
        return FakeReply(self.client, raw, error)

    def test_async_fetch_uses_fixed_shanghai_https_and_deduplicates_pending_request(self):
        reply = self.reply()
        with patch.object(self.client.manager, "get", return_value=reply) as get:
            self.client.fetch("2026-10-12")
            self.client.fetch("2026-10-12")
        self.assertEqual(get.call_count, 1)
        request = get.call_args.args[0]
        self.assertIsInstance(request, QNetworkRequest)
        url = request.url().toString()
        self.assertTrue(url.startswith("https://api.open-meteo.com/"))
        self.assertIn("latitude=31.2304", url)
        self.assertIn("longitude=121.4737", url)
        self.assertIn("weather_code", url)
        self.assertEqual(self.results, [])
        reply.finished.emit()
        self.assertEqual(self.results, [("2026-10-12", "上海 · 阴 · 24.4°C")])
        self.assertEqual(self.failures, [])
        self.assertFalse(reply.findChild(QTimer).isActive())

    def test_wmo_conditions_and_negative_temperature_render_in_chinese(self):
        for code, description in ((0, "晴"), (1, "晴间多云"), (2, "多云"), (3, "阴"),
                                  (45, "雾"), (61, "小雨"), (65, "大雨"),
                                  (73, "中雪"), (95, "雷阵雨"), (99, "雷阵雨伴冰雹")):
            with self.subTest(code=code):
                self.payload["current"].update(weather_code=code, temperature_2m=-2)
                reply = self.reply()
                with patch.object(self.client.manager, "get", return_value=reply):
                    self.client.fetch("2026-10-12")
                reply.finished.emit()
                self.assertEqual(self.results[-1], ("2026-10-12", f"上海 · {description} · -2°C"))

    def test_invalid_or_wrong_date_weather_is_not_accepted(self):
        payloads = [None, [], {}, {"current": None}]
        for field, value in (("time", "2026-10-11T23:45"), ("time", "2026-10-13T00:00"),
                             ("time", "not-a-date"), ("temperature_2m", None),
                             ("temperature_2m", True), ("temperature_2m", float("nan")),
                             ("weather_code", 999), ("weather_code", False)):
            data = copy.deepcopy(self.payload)
            data["current"][field] = value
            payloads.append(data)
        wrong_units = copy.deepcopy(self.payload)
        wrong_units["current_units"]["temperature_2m"] = "°F"
        payloads.append(wrong_units)
        for payload in payloads:
            with self.subTest(payload=payload):
                reply = FakeReply(self.client, json.dumps(payload).encode())
                with patch.object(self.client.manager, "get", return_value=reply):
                    self.client.fetch("2026-10-12")
                reply.finished.emit()
        self.assertEqual(self.results, [])
        self.assertEqual(len(self.failures), len(payloads))

    def test_network_and_json_failures_allow_a_later_retry(self):
        replies = [self.reply(error=QNetworkReply.SslHandshakeFailedError),
                   FakeReply(self.client, b"not-json"), self.reply()]
        for reply in replies:
            with patch.object(self.client.manager, "get", return_value=reply):
                self.client.fetch("2026-10-12")
            reply.finished.emit()
        self.assertEqual(self.failures, ["2026-10-12"] * 2)
        self.assertEqual(self.results, [("2026-10-12", "上海 · 阴 · 24.4°C")])

    def test_timeout_aborts_without_blocking_ui(self):
        reply = self.reply()
        with patch.object(self.client, "TIMEOUT_MS", 1), \
                patch.object(self.client.manager, "get", return_value=reply):
            self.client.fetch("2026-10-12")
        QTest.qWait(30)
        self.assertEqual(self.results, [])
        self.assertEqual(self.failures, ["2026-10-12"])


class DiaryWeatherWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db = Database(Path(self.temp_dir.name) / "diary.db")
        self.addCleanup(self.db.close)
        self.window = qt_gui.DiaryWindow(self.db)
        self.addCleanup(lambda: shiboken6.delete(self.window))
        self.assertTrue(hasattr(self.window, "weather_label"), "缺少星期右侧天气展示")
        self.fetch_patch = patch.object(self.window.weather_client, "fetch")
        self.fetch = self.fetch_patch.start()
        self.addCleanup(self.fetch_patch.stop)
        self.today = QDate.currentDate().toString("yyyy-MM-dd")
        self.yesterday = QDate.currentDate().addDays(-1).toString("yyyy-MM-dd")
        self.weather = "上海 · 多云 · 24.4°C"
        self.window.load_date(self.today)
        self.window.show()
        self.app.processEvents()

    def click(self, widget):
        QTest.mouseClick(widget, Qt.LeftButton)
        self.app.processEvents()

    def receive_weather(self):
        self.window.weather_client.ready.emit(self.today, self.weather)
        self.app.processEvents()

    def test_fetch_starts_on_edit_not_browsing_and_does_not_touch_body(self):
        self.fetch.assert_not_called()
        self.click(self.window.write_button)
        self.fetch.assert_called_once_with(self.today)
        self.assertEqual(self.window.weather_label.text(), "上海 · 获取天气中…")
        self.window.editor.setPlainText("尚未保存的正文")
        self.receive_weather()
        self.assertEqual(self.window.weather_label.text(), self.weather)
        self.assertEqual(self.window.editor.toPlainText(), "尚未保存的正文")
        self.assertEqual(self.db.get_daily_note(self.today)["content"], "")
        self.assertTrue(self.window.save())
        self.assertEqual(self.db.get_daily_note(self.today)["weather"], self.weather)

    def test_weather_is_right_aligned_on_weekday_row_at_minimum_sizes(self):
        self.window.enter_edit_mode()
        self.receive_weather()
        for expanded in (True, False):
            with self.subTest(expanded=expanded):
                if not expanded:
                    self.click(self.window.history_button)
                self.window.resize(self.window.minimumSize())
                self.app.processEvents()
                weekday = self.window.date_meta.geometry()
                weather = self.window.weather_label.geometry()
                self.assertEqual(weekday.center().y(), weather.center().y())
                self.assertLess(weekday.right(), weather.left())
                self.assertTrue(self.window.weather_label.alignment() & Qt.AlignRight)
                self.assertTrue(self.window.content_panel.rect().contains(weather))
                self.assertGreaterEqual(weather.width(), self.window.weather_label.fontMetrics().horizontalAdvance(self.weather))
                self.assertEqual(self.window.date_meta.font(), self.window.weather_label.font())
                self.assertEqual(self.window.date_meta.styleSheet(), self.window.weather_label.styleSheet())
                self.assertIn("Open-Meteo", self.window.weather_label.toolTip())

    def test_saved_snapshot_is_reloaded_without_refetching(self):
        self.db.save_daily_note(self.today, "正文", self.weather)
        self.window.load_date(self.today)
        self.window.enter_edit_mode()
        self.fetch.assert_not_called()
        self.assertEqual(self.window.weather_label.text(), self.weather)
        self.window.editor.setPlainText("修改正文")
        self.window.save()
        self.window.load_date(self.yesterday)
        self.window.load_date(self.today)
        self.assertEqual(self.window.weather_label.text(), self.weather)

    def test_historical_and_future_entries_never_fetch_current_weather(self):
        for date in (self.yesterday, QDate.currentDate().addDays(1).toString("yyyy-MM-dd")):
            self.window.load_date(date)
            self.window.enter_edit_mode()
            self.assertEqual(self.window.weather_label.text(), "")
        self.fetch.assert_not_called()
        self.assertEqual(self.db.get_all_note_dates(), [])

    def test_weather_alone_does_not_create_a_record(self):
        self.window.enter_edit_mode()
        self.receive_weather()
        self.window.save()
        self.window.load_date(self.yesterday)
        self.assertEqual(self.db.conn.execute("SELECT COUNT(*) FROM daily_notes").fetchone()[0], 0)
        self.assertEqual(self.window.weather_label.text(), "")
        self.assertEqual(self.db.get_all_note_dates(), [])

    def test_late_result_attaches_to_original_date_after_switch(self):
        self.window.enter_edit_mode()
        self.window.editor.setPlainText("今天正文")
        self.window.load_date(self.yesterday)
        self.window.enter_edit_mode()
        self.window.editor.setPlainText("历史草稿")
        self.receive_weather()
        self.assertEqual(self.db.get_daily_note(self.today)["weather"], self.weather)
        self.assertEqual(self.db.get_daily_note(self.today)["content"], "今天正文")
        self.assertEqual(self.window.weather_label.text(), "")
        self.assertEqual(self.window.editor.toPlainText(), "历史草稿")
        self.assertEqual(self.db.get_daily_note(self.yesterday)["weather"], "")

    def test_late_result_after_save_preserves_new_unsaved_changes(self):
        self.window.enter_edit_mode()
        self.window.editor.setPlainText("已保存正文")
        self.window.save()
        self.window.editor.setPlainText("接着写的草稿")
        self.receive_weather()
        self.assertEqual(self.db.get_daily_note(self.today)["content"], "已保存正文")
        self.assertEqual(self.db.get_daily_note(self.today)["weather"], self.weather)
        self.assertEqual(self.window.editor.toPlainText(), "接着写的草稿")
        self.assertEqual(self.window.status_label.text(), "未保存")

    def test_failure_keeps_diary_usable_and_retries_next_edit(self):
        self.window.enter_edit_mode()
        self.window.weather_client.failed.emit(self.today)
        self.assertEqual(self.window.weather_label.text(), "上海 · 天气暂不可用")
        self.window.editor.setPlainText("断网时也能保存")
        self.click(self.window.toggle_btn)
        self.assertEqual(self.db.get_daily_note(self.today)["content"], "断网时也能保存")
        self.assertEqual(self.db.get_daily_note(self.today)["weather"], "")
        self.click(self.window.toggle_btn)
        self.assertEqual(self.fetch.call_count, 2)
        self.receive_weather()
        self.assertEqual(self.window.weather_label.text(), self.weather)

    def test_late_failure_does_not_pollute_other_date(self):
        self.window.enter_edit_mode()
        self.window.load_date(self.yesterday)
        self.window.weather_client.failed.emit(self.today)
        self.assertEqual(self.window.weather_label.text(), "")

    def test_weather_storage_failure_can_be_retried_by_save_without_body_edits(self):
        self.db.save_daily_note(self.today, "原来正文")
        self.window.load_date(self.today)
        self.window.enter_edit_mode()
        with patch.object(self.db, "save_daily_note_weather", side_effect=sqlite3.OperationalError("只读")), \
                patch("builtins.print") as log:
            self.receive_weather()
        log.assert_called_once_with("[天气] 天气暂未保存: 只读")
        self.assertEqual(self.window.status_label.text(), "未保存")
        self.assertEqual(self.db.get_daily_note(self.today)["weather"], "")
        self.assertTrue(self.window.save())
        self.assertEqual(self.window.status_label.text(), "已保存")
        self.assertEqual(self.db.get_daily_note(self.today)["weather"], self.weather)

    def test_weather_received_while_closed_can_be_saved_with_draft_on_reopen(self):
        self.window.enter_edit_mode()
        self.window.editor.setPlainText("关闭前的草稿")
        self.window.close()
        self.receive_weather()
        self.window.show()
        self.window.save()
        self.window.load_date(self.today)
        self.assertEqual(self.window.editor.toPlainText(), "关闭前的草稿")
        self.assertEqual(self.window.weather_label.text(), self.weather)


if __name__ == "__main__":
    unittest.main()
