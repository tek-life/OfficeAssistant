import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import shiboken6
from PySide6.QtCore import QDate, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QCalendarWidget, QTableView

from db import Database
from qt_gui import DiaryWindow


class DiaryCalendarTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp_dir.name) / "diary.db")
        self.notifications = 0
        self.window = DiaryWindow(self.db, on_saved=self.on_saved)
        self.window.load_date("2026-10-09")
        self.window.show()
        self.app.processEvents()

    def tearDown(self):
        self.window.close()
        shiboken6.delete(self.window)
        self.app.processEvents()
        self.db.close()
        self.temp_dir.cleanup()

    def on_saved(self):
        self.notifications += 1

    def click(self, widget):
        QTest.mouseClick(widget, Qt.LeftButton)
        self.app.processEvents()

    def open_calendar(self):
        calendar = self.window.findChild(QCalendarWidget)
        self.assertIsNotNone(calendar, "日志日历应使用可点击的月历")
        if calendar.isVisible():
            self.click(self.window.history_button)
        self.click(self.window.history_button)
        self.assertTrue(calendar.isVisible())
        return calendar

    def select_date(self, date):
        self.window.calendar.setSelectedDate(QDate.fromString(date, "yyyy-MM-dd"))
        self.app.processEvents()

    def click_day(self, day):
        view = self.window.calendar.findChild(QTableView)
        for row in range(1, view.model().rowCount()):
            for col in range(view.model().columnCount()):
                index = view.model().index(row, col)
                if str(index.data()) == str(day):
                    QTest.mouseClick(view.viewport(), Qt.LeftButton,
                                     pos=view.visualRect(index).center())
                    self.app.processEvents()
                    return
        self.fail(f"月历缺少日期 {day}")

    def test_calendar_is_expanded_by_default(self):
        self.assertTrue(self.window.calendar_panel.isVisible())
        self.assertTrue(self.window.calendar.isVisible())
        self.assertTrue(self.window.history_button.isChecked())
        self.assertEqual(self.window.history_button.text(), "收起日历")
        self.assertGreaterEqual(self.window.width(), 1090)
        self.assertEqual(self.notifications, 0)

    def test_calendar_toggle_label_matches_visibility(self):
        self.open_calendar()
        self.assertEqual(self.window.history_button.text(), "收起日历")
        self.click(self.window.history_button)
        self.assertTrue(self.window.calendar_panel.isHidden())
        self.assertFalse(self.window.history_button.isChecked())
        self.assertEqual(self.window.history_button.text(), "打开日历")
        self.click(self.window.history_button)
        self.assertTrue(self.window.calendar_panel.isVisible())
        self.assertTrue(self.window.history_button.isChecked())
        self.assertEqual(self.window.history_button.text(), "收起日历")

    def test_tray_reopening_expands_previously_collapsed_calendar(self):
        for closed in (False, True):
            with self.subTest(closed=closed):
                self.open_calendar()
                self.click(self.window.history_button)
                self.assertTrue(self.window.calendar_panel.isHidden())
                if closed:
                    self.window.close()
                self.reopen_from_tray()
                self.assertTrue(self.window.calendar_panel.isVisible())
                self.assertEqual(self.window.history_button.text(), "收起日历")
                self.assertEqual(self.window.current_date,
                                 QDate.currentDate().toString("yyyy-MM-dd"))

    def test_history_opens_even_without_notes(self):
        calendar = self.open_calendar()
        self.assertEqual(calendar.selectedDate(), QDate(2026, 10, 9))
        self.assertTrue(self.window.empty_state.isVisible())
        self.assertTrue(self.window.write_button.isVisible())
        self.assertEqual(self.db.get_all_note_dates(), [])
        self.assertEqual(self.notifications, 0)

    def test_single_click_displays_existing_markdown(self):
        self.db.save_daily_note("2026-10-08", "# 当天记录\n\n完成 **测试**")
        self.open_calendar()
        self.click_day(8)
        self.assertEqual(self.window.current_date, "2026-10-08")
        self.assertEqual(self.window.mode, "read")
        self.assertTrue(self.window.preview.isVisible())
        self.assertIn("当天记录", self.window.preview.toPlainText())
        self.assertIn("测试", self.window.preview.toPlainText())
        self.assertFalse(self.window.empty_state.isVisible())
        self.assertEqual(self.notifications, 0)

    def test_only_saved_nonblank_dates_have_markers(self):
        self.db.save_daily_note("2026-10-07", "有日志")
        self.db.save_daily_note("2026-10-08", " \n\t\u3000")
        self.db.save_daily_note("2026-09-30", "上个月")
        calendar = self.open_calendar()
        self.assertEqual(calendar.recorded_dates,
                         {QDate(2026, 10, 7), QDate(2026, 9, 30)})
        self.assertIn("已记录", calendar.dateTextFormat(QDate(2026, 10, 7)).toolTip())
        self.assertEqual(self.window.month_summary.text(), "本月已记录 1 天")

    def test_empty_date_can_be_written_saved_and_cleared(self):
        calendar = self.open_calendar()
        self.click(self.window.write_button)
        self.assertEqual(self.window.mode, "edit")
        self.window.editor.setPlainText("补写当天日志")
        self.assertNotIn(QDate(2026, 10, 9), calendar.recorded_dates)
        self.click(self.window.toggle_btn)
        self.assertEqual(self.db.get_daily_note("2026-10-09")["content"], "补写当天日志")
        self.assertIn(QDate(2026, 10, 9), calendar.recorded_dates)
        self.assertTrue(self.window.preview.isVisible())
        self.window.enter_edit_mode()
        self.window.editor.setPlainText(" \n\t")
        self.click(self.window.toggle_btn)
        self.assertNotIn(QDate(2026, 10, 9), calendar.recorded_dates)
        self.assertFalse(calendar.dateTextFormat(QDate(2026, 10, 9)).toolTip())
        self.assertTrue(self.window.empty_state.isVisible())
        self.assertEqual(self.window.month_summary.text(), "本月已记录 0 天")

    def test_switching_dates_saves_draft_before_loading_next_note(self):
        self.db.save_daily_note("2026-10-08", "此前的记录")
        calendar = self.open_calendar()
        self.window.enter_edit_mode()
        self.window.editor.setPlainText("尚未手动保存的草稿")
        self.click_day(8)
        self.assertEqual(self.db.get_daily_note("2026-10-09")["content"],
                         "尚未手动保存的草稿")
        self.assertEqual(self.window.editor.toPlainText(), "此前的记录")
        self.assertIn(QDate(2026, 10, 9), calendar.recorded_dates)
        self.assertEqual(self.notifications, 1)

    def test_browsing_empty_dates_never_writes_records(self):
        self.open_calendar()
        for date in ("2026-10-08", "2026-09-30", "2024-02-29"):
            self.select_date(date)
            self.assertTrue(self.window.empty_state.isVisible())
        self.assertEqual(self.db.conn.execute(
            "SELECT COUNT(*) FROM daily_notes"
        ).fetchone()[0], 0)
        self.assertEqual(self.notifications, 0)

    def test_month_navigation_does_not_change_or_save_current_draft(self):
        self.db.save_daily_note("2026-09-30", "上个月的日志")
        calendar = self.open_calendar()
        self.window.enter_edit_mode()
        self.window.editor.setPlainText("正在编辑")
        self.click(self.window.previous_month_button)
        self.assertEqual((calendar.yearShown(), calendar.monthShown()), (2026, 9))
        self.assertEqual(self.window.current_date, "2026-10-09")
        self.assertEqual(self.window.editor.toPlainText(), "正在编辑")
        self.assertEqual(self.window.mode, "edit")
        self.assertEqual(self.window.month_summary.text(), "本月已记录 1 天")
        self.assertEqual(self.notifications, 0)
        self.click(self.window.next_month_button)
        self.assertEqual(calendar.monthShown(), 10)

    def test_year_month_jump_handles_leap_day_and_year_boundary(self):
        calendar = self.open_calendar()
        self.window.year_select.setValue(2024)
        self.window.month_select.setCurrentIndex(1)
        self.app.processEvents()
        self.assertEqual((calendar.yearShown(), calendar.monthShown()), (2024, 2))
        self.select_date("2024-02-29")
        self.assertEqual(self.window.current_date, "2024-02-29")
        calendar.setCurrentPage(2025, 12)
        self.click(self.window.next_month_button)
        self.assertEqual((calendar.yearShown(), calendar.monthShown()), (2026, 1))
        self.assertEqual(self.window.year_select.value(), 2026)
        self.assertEqual(self.window.month_select.currentIndex(), 0)

    def test_today_restores_both_selection_and_displayed_month(self):
        calendar = self.open_calendar()
        self.select_date(QDate.currentDate().toString("yyyy-MM-dd"))
        calendar.setCurrentPage(2024, 2)
        self.click(self.window.today_button)
        today = QDate.currentDate()
        self.assertEqual(calendar.selectedDate(), today)
        self.assertEqual((calendar.yearShown(), calendar.monthShown()),
                         (today.year(), today.month()))
        self.assertEqual(self.window.current_date, today.toString("yyyy-MM-dd"))

    def test_today_on_current_date_preserves_unsaved_draft(self):
        self.open_calendar()
        today = QDate.currentDate()
        self.window.load_date(today.toString("yyyy-MM-dd"))
        self.window.enter_edit_mode()
        self.window.editor.setPlainText("今天未保存的内容")
        self.window.calendar.setCurrentPage(2024, 2)
        self.click(self.window.today_button)
        self.assertEqual(self.window.editor.toPlainText(), "今天未保存的内容")
        self.assertEqual(self.window.mode, "edit")
        self.assertEqual(self.window.calendar.monthShown(), today.month())

    def test_calendar_supports_keyboard_date_selection(self):
        self.open_calendar()
        view = self.window.calendar.findChild(QTableView)
        view.setFocus()
        QTest.keyClick(view, Qt.Key_Left)
        self.app.processEvents()
        self.assertEqual(self.window.current_date, "2026-10-08")

    def test_saving_failure_keeps_draft_and_restores_selected_date(self):
        calendar = self.open_calendar()
        self.window.enter_edit_mode()
        self.window.editor.setPlainText("必须保留的草稿")
        with patch.object(self.db, "save_daily_note", side_effect=sqlite3.OperationalError("只读")), \
                patch("qt_gui.QMessageBox.warning") as warning:
            self.click_day(8)
        self.assertTrue(warning.called)
        self.assertEqual(self.window.current_date, "2026-10-09")
        self.assertEqual(calendar.selectedDate(), QDate(2026, 10, 9))
        self.assertEqual(self.window.editor.toPlainText(), "必须保留的草稿")
        self.assertEqual(self.window.mode, "edit")
        self.assertEqual(self.notifications, 0)

    def test_collapsing_and_reopening_calendar_preserves_draft(self):
        calendar = self.open_calendar()
        self.window.enter_edit_mode()
        self.window.editor.setPlainText("编辑中的内容")
        self.window.open_history()
        self.app.processEvents()
        self.assertFalse(calendar.isVisible())
        self.window.open_history()
        self.app.processEvents()
        self.assertTrue(calendar.isVisible())
        self.assertEqual(self.window.editor.toPlainText(), "编辑中的内容")
        self.assertEqual(self.window.mode, "edit")
        self.assertEqual(self.notifications, 0)

    def reopen_from_tray(self):
        with patch("ctypes.windll.kernel32.GetConsoleWindow", return_value=0), \
                patch("ctypes.windll.user32.SetProcessDPIAware"):
            from sticky_wallpaper import OfficeAssistant
        host = OfficeAssistant.__new__(OfficeAssistant)
        host._diary_win = self.window
        host._open_diary()
        self.app.processEvents()

    def test_tray_reopening_today_saves_instead_of_discarding_draft(self):
        today = QDate.currentDate().toString("yyyy-MM-dd")
        self.window.load_date(today)
        self.window.enter_edit_mode()
        self.window.editor.setPlainText("托盘重新打开时保留")
        self.reopen_from_tray()
        self.assertEqual(self.window.editor.toPlainText(), "托盘重新打开时保留")
        self.assertEqual(self.db.get_daily_note(today)["content"], "托盘重新打开时保留")

    def test_tray_reopening_after_save_failure_preserves_draft(self):
        self.window.load_date(QDate.currentDate().toString("yyyy-MM-dd"))
        self.window.enter_edit_mode()
        self.window.editor.setPlainText("保存失败也不能丢失")
        with patch.object(self.db, "save_daily_note", side_effect=sqlite3.OperationalError("只读")), \
                patch("qt_gui.QMessageBox.warning"):
            self.window.save()
            self.reopen_from_tray()
        self.assertEqual(self.window.editor.toPlainText(), "保存失败也不能丢失")
        self.assertEqual(self.window.mode, "edit")

    def test_history_query_failure_does_not_mix_dates_and_content(self):
        self.db.save_daily_note("2026-10-09", "当前正文")
        self.db.save_daily_note("2026-10-08", "另一日正文")
        self.window.load_date("2026-10-09")
        calendar = self.open_calendar()
        with patch.object(self.db, "get_all_note_dates", side_effect=sqlite3.OperationalError("读取失败")), \
                patch("qt_gui.QMessageBox.warning") as warning:
            try:
                loaded = self.window.load_date("2026-10-08")
            except sqlite3.Error:
                self.fail("日历读取失败应保留原日期和正文，而不是中断界面更新")
        self.assertFalse(loaded)
        self.assertTrue(warning.called)
        self.assertEqual(self.window.current_date, "2026-10-09")
        self.assertEqual(calendar.selectedDate(), QDate(2026, 10, 9))
        self.assertEqual(self.window.editor.toPlainText(), "当前正文")
        self.assertEqual(self.window.preview.toPlainText(), "当前正文")

    def test_save_updates_marker_without_reading_all_history(self):
        calendar = self.open_calendar()
        self.window.enter_edit_mode()
        self.window.editor.setPlainText("新保存的内容")
        with patch.object(self.db, "get_all_note_dates", side_effect=sqlite3.OperationalError("读取失败")):
            try:
                saved = self.window.save()
            except sqlite3.Error:
                self.fail("保存完成后不应因历史查询失败而报错")
        self.assertTrue(saved)
        self.assertIn(QDate(2026, 10, 9), calendar.recorded_dates)
        self.assertEqual(self.notifications, 1)

    def test_opening_calendar_handles_history_query_failure(self):
        self.window.enter_edit_mode()
        self.window.editor.setPlainText("保留编辑内容")
        with patch.object(self.db, "get_all_note_dates", side_effect=sqlite3.OperationalError("读取失败")), \
                patch("qt_gui.QMessageBox.warning") as warning:
            try:
                self.open_calendar()
            except sqlite3.Error:
                self.fail("打开历史日历应提示查询失败，而不是抛出异常")
        self.assertTrue(warning.called)
        self.assertEqual(self.window.editor.toPlainText(), "保留编辑内容")
        self.assertEqual(self.window.mode, "edit")

    def test_sidebar_controls_never_overlap_at_minimum_size(self):
        self.open_calendar()
        self.window.resize(self.window.minimumSize())
        self.app.processEvents()
        layout = self.window.calendar_panel.layout()
        legend = layout.itemAt(3).widget()
        self.assertLess(self.window.calendar.geometry().bottom(), legend.geometry().top())
        self.assertLess(legend.geometry().bottom(), self.window.month_summary.geometry().top())
        self.assertTrue(self.window.calendar_panel.rect().contains(
            self.window.month_summary.geometry()))

    def test_calendar_and_body_fit_at_minimum_size(self):
        self.open_calendar()
        self.window.resize(self.window.minimumSize())
        self.window.enter_edit_mode()
        self.app.processEvents()
        self.assertTrue(self.window.rect().contains(self.window.calendar_panel.geometry()))
        self.assertTrue(self.window.rect().contains(self.window.content_panel.geometry()))
        self.assertFalse(self.window.calendar_panel.geometry().intersects(
            self.window.content_panel.geometry()))
        self.assertGreaterEqual(self.window.editor.width(), 360)
        self.assertGreaterEqual(self.window.calendar.width(), 280)


if __name__ == "__main__":
    unittest.main()
