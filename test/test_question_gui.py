"""阅读疑难 Qt 控件测试：气泡交互与清单窗口。"""

import json
import tempfile
import unittest
from pathlib import Path

import shiboken6
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication, QLabel

from db import Database
from qt_gui import QuestionBubble, QuestionWindow

# 与 db/sticky_wallpaper.db 中 id=1 的真实疑难一致：一整段论文正文。
LONG_PARAGRAPH = (
    "其中，Q 为量化算子，r 为实数值输入（激活或权重），S 为实数缩放因子，Z 为整数零点。"
    "此外，Int 函数通过舍入操作（例如四舍五入与截断）将实数值映射为整数值。"
    "本质上，该函数是将实数值 r 映射为某些整数值。"
    "由于所得量化值（又称量化级）均匀间隔分布（图 1 左），这种量化方法也被称为均匀量化"
    "（uniform quantization）。"
    "此外还存在量化值不一定均匀间隔的非均匀量化方法（图 1 右），我们将在第 III-F 节详细讨论。"
    "可以通过一个常被称为反量化（dequantization）的操作，从量化值 Q(r) 恢复出实数值 r："
)


def _mouse(kind, pos, button=Qt.LeftButton, buttons=Qt.LeftButton):
    point = QPointF(pos, pos)
    return QMouseEvent(kind, point, point, button, buttons, Qt.NoModifier)


def _app():
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


class QuestionBubbleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = _app()

    def setUp(self):
        self.opened = []
        self.hidden = []
        self.moved = []
        self.bubble = QuestionBubble(
            on_open=lambda: self.opened.append(True),
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

    def test_count_text_and_numeric_badge(self):
        self.assertEqual(self.bubble.title_label.text(), "疑难")
        self.bubble.set_count(0)
        self.assertEqual(self.bubble.status_label.text(), "暂无疑问")
        self.assertFalse(self.bubble.count_badge.isVisibleTo(self.bubble))
        self.bubble.set_count(3)
        self.assertEqual(self.bubble.status_label.text(), "3 条待解决")
        self.assertTrue(self.bubble.count_badge.isVisibleTo(self.bubble))
        self.assertEqual(self.bubble.count_badge.text(), "3")
        self.bubble.set_count(None)
        self.assertEqual(self.bubble.status_label.text(), "暂无疑问")
        self.assertFalse(self.bubble.count_badge.isVisibleTo(self.bubble))
        self.assertFalse(self.bubble.title_label.isVisibleTo(self.bubble))
        self.assertFalse(self.bubble.status_label.isVisibleTo(self.bubble))

    def test_left_click_opens_list(self):
        self.bubble._press_offset = QPoint(0, 0)
        self.bubble._dragged = False
        self.bubble.mouseReleaseEvent(_mouse(QEvent.MouseButtonRelease, 5))
        self.assertEqual(self.opened, [True])
        self.assertIsNone(self.bubble._press_offset)

    def test_drag_reports_position_without_opening(self):
        self.bubble._press_offset = QPoint(0, 0)
        self.bubble.mouseMoveEvent(
            _mouse(QEvent.MouseMove, 200, buttons=Qt.LeftButton))
        self.assertTrue(self.bubble._dragged)
        self.bubble.mouseReleaseEvent(_mouse(QEvent.MouseButtonRelease, 200))
        self.assertEqual(self.opened, [])
        self.assertEqual(self.moved, [(self.bubble.x(), self.bubble.y())])

    def test_menu_actions_dispatch_callbacks(self):
        menu = self.bubble._build_menu()
        actions = [a for a in menu.actions() if not a.isSeparator()]
        self.assertEqual([a.text() for a in actions], ["打开疑难清单", "隐藏气泡"])
        for action in actions:
            action.trigger()
        self.assertEqual(self.opened, [True])
        self.assertEqual(self.hidden, [True])


class QuestionWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = _app()

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db = Database(Path(self.temp_dir.name) / "gui.db")
        self.addCleanup(self.db.close)
        self.changed = []
        self.window = QuestionWindow(self.db, on_changed=self._on_changed)
        self.addCleanup(self._destroy)

    def _on_changed(self):
        self.changed.append(True)

    def _destroy(self):
        self.window.close()
        shiboken6.delete(self.window)
        self.app.processEvents()

    def _ids(self):
        return [self.window.list.item(i).data(Qt.UserRole)
                for i in range(self.window.list.count())]

    def test_empty_state_when_no_questions(self):
        self.window.refresh_list()
        self.assertIs(self.window.content_stack.currentWidget(),
                      self.window.empty_state)
        self.assertEqual(self.window.summary.text(), "0 条待解决 · 0 条已解决")

    def test_pending_tab_lists_unresolved_only(self):
        open_id = self.db.add_question(quote="未解决")
        done_id = self.db.add_question(quote="已解决")
        self.db.set_question_resolved(done_id, True)
        self.window.refresh_list()
        self.assertEqual(self.window.tabs.currentIndex(), 0)
        self.assertEqual(self._ids(), [open_id])
        self.assertIs(self.window.content_stack.currentWidget(), self.window.list)

    def test_resolved_tab_lists_resolved(self):
        done_id = self.db.add_question(quote="已解决")
        self.db.set_question_resolved(done_id, True)
        self.window.tabs.setCurrentIndex(1)
        self.app.processEvents()
        self.assertEqual(self._ids(), [done_id])

    def test_resolve_selected_moves_row_and_notifies(self):
        self.db.add_question(quote="待解决")
        self.window.refresh_list()
        self.window.list.setCurrentRow(0)
        self.window.toggle_selected()
        self.assertEqual(self.db.count_questions(), 0)
        self.assertEqual(self.db.count_questions(resolved=True), 1)
        self.assertEqual(self._ids(), [])
        self.assertEqual(self.changed, [True])

    def test_delete_selected_removes_and_notifies(self):
        qid = self.db.add_question(quote="删除我")
        self.window.refresh_list()
        self.window.list.setCurrentRow(0)
        self.window.delete_selected()
        self.assertIsNone(self.db.get_question(qid))
        self.assertEqual(self.changed, [True])

    def test_actions_disabled_without_selection(self):
        self.window.list.clearSelection()
        self.window._sync_actions()
        self.assertFalse(self.window.open_button.isEnabled())
        self.assertFalse(self.window.delete_button.isEnabled())

    def test_jump_url_uses_anchor(self):
        record = {"url": "https://e.com/x#sec",
                  "anchor": json.dumps({"q": "hi", "o": 1})}
        url = QuestionWindow._jump_url_for(record)
        self.assertTrue(url.startswith("https://e.com/x#sec&oaq="))

    def test_jump_url_without_anchor_is_plain(self):
        self.assertEqual(
            QuestionWindow._jump_url_for({"url": "https://e.com/x", "anchor": ""}),
            "https://e.com/x")

    def test_jump_url_with_bad_anchor_json_is_plain(self):
        self.assertEqual(
            QuestionWindow._jump_url_for({"url": "https://e.com/x", "anchor": "{oops"}),
            "https://e.com/x")

    def test_jump_url_empty_when_no_url(self):
        self.assertEqual(QuestionWindow._jump_url_for({"url": "", "anchor": ""}), "")

    def test_row_shows_summary_title_and_full_quote_in_tooltip(self):
        self.db.add_question(quote=LONG_PARAGRAPH, title="论文")
        self.window.refresh_list()
        row = self.window.list.itemWidget(self.window.list.item(0))
        label = row.findChild(QLabel, "questionQuote")
        self.assertIsNotNone(label)
        self.assertEqual(label.text(), QuestionWindow._quote_title(LONG_PARAGRAPH))
        self.assertNotIn("Int 函数", label.text())
        self.assertNotIn("dequantization", label.text())
        tooltip = row.toolTip()
        self.assertIn("其中", tooltip)
        self.assertIn("dequantization", tooltip)

    def test_row_appends_priority_flag_after_title(self):
        self.db.add_question(quote="需要引用的主题句。后续还有补充说明。", priority=3)
        self.window.refresh_list()
        row = self.window.list.itemWidget(self.window.list.item(0))
        label = row.findChild(QLabel, "questionQuote")
        self.assertEqual(label.text(), "需要引用的主题句。 " + "⚑" * 3)

    def test_row_shows_serial_number_in_order(self):
        self.db.add_question(quote="第一条。")
        self.db.add_question(quote="第二条。")
        self.window.refresh_list()
        numbers = []
        for i in range(self.window.list.count()):
            row = self.window.list.itemWidget(self.window.list.item(i))
            label = row.findChild(QLabel, "questionIndex")
            self.assertIsNotNone(label)
            numbers.append(label.text())
        self.assertEqual(numbers, ["1", "2"])

    def test_rows_stay_compact_without_word_wrap(self):
        self.db.add_question(quote=LONG_PARAGRAPH, title="论文题目")
        self.window.refresh_list()
        row = self.window.list.itemWidget(self.window.list.item(0))
        label = row.findChild(QLabel, "questionQuote")
        self.assertFalse(label.wordWrap())
        self.assertLessEqual(row.sizeHint().height(), 96)


class QuestionQuoteTitleTests(unittest.TestCase):
    """疑难原文压缩成列表标题的纯函数测试，不需要 Qt 窗口。"""

    def test_keeps_first_sentence_of_long_paragraph(self):
        title = QuestionWindow._quote_title(LONG_PARAGRAPH)
        self.assertEqual(
            title,
            "其中，Q 为量化算子，r 为实数值输入（激活或权重），S 为实数缩放因子，Z 为整数零点。")
        self.assertNotIn("Int 函数", title)
        self.assertNotIn("…", title)

    def test_collapses_newlines_and_runs_of_space(self):
        title = QuestionWindow._quote_title("  第一行\n\n第二行\t第三行。后面")
        self.assertEqual(title, "第一行 第二行 第三行。")
        self.assertNotIn("\n", title)

    def test_truncates_sentence_longer_than_limit(self):
        title = QuestionWindow._quote_title("甲" * 120, limit=72)
        self.assertTrue(title.endswith("…"))
        self.assertEqual(len(title), 73)
        self.assertEqual(title[:-1], "甲" * 72)

    def test_without_sentence_terminator_returns_whole_text(self):
        self.assertEqual(
            QuestionWindow._quote_title("把所有偏导数排成一个向量，就是梯度"),
            "把所有偏导数排成一个向量，就是梯度")

    def test_short_sentence_is_unchanged(self):
        self.assertEqual(QuestionWindow._quote_title("反向传播的核心是高效。"),
                         "反向传播的核心是高效。")

    def test_empty_and_whitespace_return_empty(self):
        self.assertEqual(QuestionWindow._quote_title(""), "")
        self.assertEqual(QuestionWindow._quote_title(None), "")
        self.assertEqual(QuestionWindow._quote_title("   \n\t "), "")

    def test_default_limit_constant(self):
        self.assertEqual(QuestionWindow.QUOTE_TITLE_LIMIT, 72)


if __name__ == "__main__":
    unittest.main()
