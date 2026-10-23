import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import shiboken6
from PySide6.QtCore import QDate
from PySide6.QtWidgets import QApplication

from capture import MAX_SOURCE_CHARS, append_to_diary, clean_source, format_entry
from db import Database
from qt_gui import DiaryWindow

MOMENT = datetime(2026, 10, 19, 9, 12)


class CaptureFormatTests(unittest.TestCase):
    def test_clean_source_collapses_whitespace(self):
        self.assertEqual(clean_source("  工作日志  —  2026-10-19  "),
                         "工作日志 — 2026-10-19")

    def test_clean_source_falls_back_when_title_missing(self):
        for title in ("", "   ", None):
            with self.subTest(title=title):
                self.assertEqual(clean_source(title), "剪贴板")
        self.assertEqual(clean_source("", fallback="选中内容"), "选中内容")

    def test_clean_source_truncates_long_titles(self):
        source = clean_source("很长" * 30)
        self.assertEqual(len(source), MAX_SOURCE_CHARS + 1)
        self.assertTrue(source.endswith("…"))
        self.assertEqual(clean_source("短标题"), "短标题")

    def test_format_entry_uses_time_and_source(self):
        entry = format_entry("第一行内容", "Chrome - 某个页面", MOMENT)
        self.assertEqual(entry, "- 09:12 [Chrome - 某个页面] 第一行内容")

    def test_format_entry_turns_following_lines_into_sub_items(self):
        entry = format_entry("第一行\r\n  第二行  \n\n第三行", "编辑器", MOMENT)
        self.assertEqual(entry, "- 09:12 [编辑器] 第一行\n  - 第二行\n  - 第三行")

    def test_format_entry_ignores_blank_text(self):
        for text in ("", "   ", "\n\n"):
            with self.subTest(text=text):
                self.assertEqual(format_entry(text, "剪贴板", MOMENT), "")


class AppendToDiaryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db = Database(Path(self.temp_dir.name) / "test.db")
        self.addCleanup(lambda: self.db.close())

    def test_append_creates_note_for_empty_day(self):
        result = append_to_diary(self.db, "第一条", "Chrome", MOMENT)
        self.assertEqual(result[0], "2026-10-19")
        self.assertEqual(self.db.get_daily_note("2026-10-19")["content"], result[1])

    def test_append_keeps_existing_content_and_weather(self):
        self.db.save_daily_note("2026-10-19", "手写的内容", "上海 晴 22°C")
        append_to_diary(self.db, "抓来的内容", "Chrome", MOMENT)
        note = self.db.get_daily_note("2026-10-19")
        self.assertEqual(note["content"],
                         "手写的内容\n\n- 09:12 [Chrome] 抓来的内容")
        self.assertEqual(note["weather"], "上海 晴 22°C")

    def test_append_twice_keeps_chronological_order(self):
        later = datetime(2026, 10, 19, 15, 30)
        append_to_diary(self.db, "上午的事情", "Chrome", MOMENT)
        append_to_diary(self.db, "下午的事情", "编辑器", later)
        content = self.db.get_daily_note("2026-10-19")["content"]
        self.assertLess(content.index("09:12"), content.index("15:30"))
        self.assertIn("- 09:12 [Chrome] 上午的事情", content)
        self.assertIn("- 15:30 [编辑器] 下午的事情", content)

    def test_append_ignores_blank_text(self):
        self.assertIsNone(append_to_diary(self.db, "   ", "Chrome", MOMENT))
        self.assertEqual(self.db.get_all_note_dates(), [])


class DiaryWindowAppendExternalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db = Database(Path(self.temp_dir.name) / "test.db")
        self.addCleanup(lambda: self.db.close())
        self.window = DiaryWindow(self.db)
        fetch_patch = patch.object(self.window.weather_client, "fetch")
        fetch_patch.start()
        self.addCleanup(fetch_patch.stop)
        self.today = datetime.now().strftime("%Y-%m-%d")
        self.window.load_date(self.today)
        self.window.show()
        self.app.processEvents()
        self.addCleanup(self.close_window)

    def close_window(self):
        self.window.close()
        shiboken6.delete(self.window)
        self.app.processEvents()

    def deliver(self, text="抓来的内容", source="Chrome - 某个页面"):
        result = append_to_diary(self.db, text, source)
        self.window.append_external(result[1], result[0])
        self.app.processEvents()
        return result

    def test_read_mode_preview_shows_new_entry(self):
        self.deliver()
        self.assertEqual(self.window.mode, "read")
        self.assertIn("抓来的内容", self.window.preview.toPlainText())

    def test_edit_mode_without_pending_changes_stays_saved(self):
        self.window.db.save_daily_note(self.today, "先写好的内容")
        self.window.load_date(self.today)
        self.window.enter_edit_mode()
        result = self.deliver()
        self.assertEqual(self.window.editor.toPlainText(), "先写好的内容\n\n" + result[1])
        self.assertEqual(self.window._saved_content, self.window.editor.toPlainText())
        self.assertEqual(self.window.status_label.text(), "已保存")

    def test_edit_mode_keeps_unsaved_input(self):
        self.window.enter_edit_mode()
        self.window.editor.setPlainText("正在输入的想法")
        self.deliver()
        self.assertIn("正在输入的想法", self.window.editor.toPlainText())
        self.assertIn("抓来的内容", self.window.editor.toPlainText())
        self.assertEqual(self.window.status_label.text(), "未保存")
        self.assertTrue(self.window.save())
        stored = self.db.get_daily_note(self.today)["content"]
        self.assertIn("正在输入的想法", stored)
        self.assertIn("抓来的内容", stored)

    def test_other_date_only_refreshes_calendar_marks(self):
        self.window.enter_edit_mode()
        self.window.editor.setPlainText("今天的正文")
        result = append_to_diary(self.db, "别的事情", "剪贴板", datetime(2026, 10, 9, 9, 12))
        self.window.append_external(result[1], result[0])
        self.app.processEvents()
        self.assertEqual(self.window.current_date, self.today)
        self.assertEqual(self.window.editor.toPlainText(), "今天的正文")
        self.assertIn(QDate(2026, 10, 9), self.window.calendar.recorded_dates)


if __name__ == "__main__":
    unittest.main()
