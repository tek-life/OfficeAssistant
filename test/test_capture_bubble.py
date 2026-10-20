import tempfile
import unittest
from datetime import datetime
from pathlib import Path

import shiboken6
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication

from db import Database
from qt_gui import (BUBBLE_ERROR, BUBBLE_IDLE, BUBBLE_OK, CaptureBubble,
                    _foreground_hwnd, _set_no_activate)
from sticky_wallpaper import OfficeAssistant


def _mouse(kind, pos, button=Qt.LeftButton, buttons=Qt.LeftButton):
    point = QPointF(pos, pos)
    return QMouseEvent(kind, point, point, button, buttons, Qt.NoModifier)


class BubbleHelperTests(unittest.TestCase):
    def test_shorten_keeps_short_text_and_truncates_long(self):
        self.assertEqual(CaptureBubble._shorten("已记入"), "已记入")
        self.assertEqual(CaptureBubble._shorten("  会话 中的 空 格  "), "会话 中的 空 格")
        short = CaptureBubble._shorten("一二三四五六七八九十甲乙丙")
        self.assertEqual(len(short), 10)
        self.assertTrue(short.endswith("…"))

    def test_shorten_handles_none_and_newlines(self):
        self.assertEqual(CaptureBubble._shorten(None), "")
        self.assertEqual(CaptureBubble._shorten("第一行\n第二行"), "第一行 第二行")


class BubbleWidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.captured = []
        self.moved = []
        self.diary = []
        self.hidden = []
        self.bubble = CaptureBubble(
            on_capture=self.captured.append,
            on_open_diary=lambda: self.diary.append(True),
            on_hide=lambda: self.hidden.append(True),
            on_moved=lambda x, y: self.moved.append((x, y)),
        )
        self.bubble.show_at(-1, -1)
        self.app.processEvents()
        self.addCleanup(self._destroy)

    def _destroy(self):
        self.bubble.close()
        shiboken6.delete(self.bubble)
        self.app.processEvents()

    def test_left_click_delivers_selection(self):
        self.bubble._press_offset = QPoint(0, 0)
        self.bubble._dragged = False
        self.bubble.mouseReleaseEvent(_mouse(QEvent.MouseButtonRelease, 5))
        self.assertEqual(self.captured, ["selection"])
        self.assertIsNone(self.bubble._press_offset)

    def test_drag_moves_and_reports_position_without_delivering(self):
        self.bubble._press_offset = QPoint(0, 0)
        self.bubble.mouseMoveEvent(_mouse(QEvent.MouseMove, 240, buttons=Qt.LeftButton))
        self.assertTrue(self.bubble._dragged)
        self.bubble.mouseReleaseEvent(_mouse(QEvent.MouseButtonRelease, 240))
        self.assertEqual(self.captured, [])
        self.assertEqual(self.moved, [(self.bubble.x(), self.bubble.y())])

    def test_menu_actions_dispatch_expected_callbacks(self):
        menu = self.bubble._build_menu()
        actions = [a for a in menu.actions() if not a.isSeparator()]
        self.assertEqual([a.text() for a in actions],
                         ["记录选中内容", "记录剪贴板", "打开工作日志", "隐藏气泡"])
        for action in actions:
            action.trigger()
        self.assertEqual(self.captured, ["selection", "clipboard"])
        self.assertEqual(self.diary, [True])
        self.assertEqual(self.hidden, [True])

    def test_state_transitions_and_status_text(self):
        self.assertEqual(self.bubble._state, BUBBLE_IDLE)
        self.bubble.set_busy()
        self.assertEqual(self.bubble._state, "busy")
        self.assertEqual(self.bubble.status_label.text(), "读取中…")
        self.bubble.show_result(BUBBLE_OK, "已记入 10-19")
        self.assertEqual(self.bubble._state, BUBBLE_OK)
        self.assertEqual(self.bubble.status_label.text(), "已记入 10-19")
        self.bubble.show_result(BUBBLE_ERROR, "没有选中内容")
        self.assertEqual(self.bubble._state, BUBBLE_ERROR)
        self.bubble._reset()
        self.assertEqual(self.bubble._state, BUBBLE_IDLE)
        self.assertEqual(self.bubble.status_label.text(), "")

    def test_clamp_pulls_bubble_back_on_screen(self):
        self.bubble.move(-5000, -5000)
        self.bubble._clamp()
        screen = QApplication.primaryScreen().availableGeometry()
        self.assertGreaterEqual(self.bubble.x(), screen.left())
        self.assertGreaterEqual(self.bubble.y(), screen.top())
        self.assertLessEqual(self.bubble.x() + self.bubble.width(), screen.right() + 1)
        self.assertLessEqual(self.bubble.y() + self.bubble.height(), screen.bottom() + 1)

    def test_no_activate_helpers_do_not_raise(self):
        _set_no_activate(self.bubble)
        self.assertIsInstance(_foreground_hwnd(), int)


class DeliverNotifyTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db = Database(Path(self.temp_dir.name) / "test.db")
        self.addCleanup(self.db.close)
        self.assistant = OfficeAssistant.__new__(OfficeAssistant)
        self.assistant.db = self.db
        self.assistant._diary_win = None
        self.assistant.tray = None

        class _QtStub:
            def post(self, fn):
                fn()

        self.assistant.qt = _QtStub()
        self.notified = []
        self.assistant._notify = lambda title, message: self.notified.append((title, message))
        self.today = datetime.now().strftime("%Y-%m-%d")

    def test_deliver_without_notify_returns_entry(self):
        outcome = self.assistant._deliver("抓来的内容", "Chrome", notify=False)
        self.assertEqual(outcome[0], self.today)
        self.assertIn("抓来的内容", self.db.get_daily_note(self.today)["content"])
        self.assertEqual(self.notified, [])

    def test_deliver_with_notify_reports_to_tray(self):
        self.assistant._deliver("抓来的内容", "Chrome", notify=True)
        self.assertEqual(self.notified[0][0], "已记入工作日志")

    def test_deliver_blank_returns_none(self):
        self.assertIsNone(self.assistant._deliver("   ", "Chrome", notify=False))
        self.assertEqual(self.notified, [])


if __name__ == "__main__":
    unittest.main()
