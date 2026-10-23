"""投递 / 疑难气泡虚拟框的布局与交互测试。"""

import unittest

import shiboken6
from PySide6.QtCore import QEvent, QPoint, QPointF, QSize, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication

from qt_gui import BubbleGroup, CaptureBubble, QuestionBubble


def _mouse(kind, local, global_, button=Qt.LeftButton, buttons=Qt.LeftButton):
    return QMouseEvent(kind, QPointF(local), QPointF(global_), button,
                       buttons, Qt.NoModifier)


def _point_event(kind, local, global_, button=Qt.LeftButton,
                 buttons=Qt.LeftButton):
    return _mouse(kind, QPoint(local, local), QPoint(global_, global_),
                  button, buttons)


class BubbleGroupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.captured = []
        self.opened_diary = []
        self.hidden_capture = []
        self.opened_question = []
        self.hidden_question = []
        self.moved = []
        self.group = BubbleGroup(
            on_capture=lambda mode: self.captured.append(mode),
            on_open_diary=lambda: self.opened_diary.append(True),
            on_hide_capture=lambda: self.hidden_capture.append(True),
            on_open_question=lambda: self.opened_question.append(True),
            on_hide_question=lambda: self.hidden_question.append(True),
            on_moved=lambda x, y: self.moved.append((x, y)),
        )
        self.group.show_at(60, 60)
        self.group.set_capture_visible(True)
        self.group.set_question_visible(True)
        self.group.sync_visibility()
        self.app.processEvents()
        self.addCleanup(self._destroy)

    def _destroy(self):
        self.group.close()
        shiboken6.delete(self.group)
        self.app.processEvents()

    def test_children_are_vertically_stacked_and_aligned(self):
        self.assertEqual(self.group.width(), 72)
        self.assertEqual(self.group.height(), 72 * 2 + BubbleGroup.SPACING)
        self.assertEqual(self.group.capture_bubble.size(), QSize(72, 72))
        self.assertEqual(self.group.question_bubble.size(), QSize(72, 72))
        self.assertEqual(self.group.capture_bubble.x(),
                         self.group.question_bubble.x())
        self.assertEqual(self.group.question_bubble.y(),
                         self.group.capture_bubble.y() + 72
                         + BubbleGroup.SPACING)

    def test_container_is_invisible_and_does_not_steal_focus(self):
        self.assertEqual(self.group.objectName(), "bubbleGroup")
        self.assertIn("background: transparent", self.group.styleSheet())
        self.assertIn("border: none", self.group.styleSheet())
        self.assertTrue(self.group.testAttribute(Qt.WA_TranslucentBackground))
        self.assertTrue(self.group.testAttribute(Qt.WA_ShowWithoutActivating))
        self.assertEqual(self.group.windowFlags() & Qt.FramelessWindowHint,
                         Qt.FramelessWindowHint)
        self.assertEqual(self.group.windowFlags()
                         & Qt.WindowDoesNotAcceptFocus,
                         Qt.WindowDoesNotAcceptFocus)

    def test_hiding_children_keeps_layout_and_hides_empty_group(self):
        for bubble in (self.group.capture_bubble,
                       self.group.question_bubble):
            policy = bubble.sizePolicy()
            self.assertTrue(policy.retainSizeWhenHidden())

        self.group.set_capture_visible(False)
        self.group.sync_visibility()
        self.assertTrue(self.group.isVisible())
        self.assertFalse(self.group.capture_bubble.isVisibleTo(self.group))
        self.assertTrue(self.group.question_bubble.isVisibleTo(self.group))
        self.assertEqual(self.group.capture_bubble.x(),
                         self.group.question_bubble.x())

        self.group.set_question_visible(False)
        self.group.sync_visibility()
        self.assertFalse(self.group.isVisible())

        self.group.set_capture_visible(True)
        self.group.sync_visibility()
        self.assertTrue(self.group.isVisible())
        self.assertTrue(self.group.capture_bubble.isVisibleTo(self.group))
        self.assertFalse(self.group.question_bubble.isVisibleTo(self.group))

    def test_count_is_forwarded_to_question_badge_only(self):
        self.group.set_count(4)
        self.assertEqual(self.group.question_bubble.count_badge.text(), "4")
        self.assertTrue(self.group.question_bubble.count_badge
                        .isVisibleTo(self.group.question_bubble))
        self.assertFalse(self.group.question_bubble.title_label
                         .isVisibleTo(self.group.question_bubble))
        self.assertFalse(self.group.question_bubble.status_label
                         .isVisibleTo(self.group.question_bubble))

    def test_dragging_either_bubble_moves_the_whole_group(self):
        for bubble in (self.group.capture_bubble,
                       self.group.question_bubble):
            with self.subTest(bubble=bubble.__class__.__name__):
                self.assertIs(bubble._drag_target(), self.group)
                before = self.group.pos()
                local = QPoint(4, 4)
                press_global = self.group.mapToGlobal(
                    QPoint(bubble.x() + local.x(), bubble.y() + local.y()))
                bubble.mousePressEvent(
                    _mouse(QEvent.MouseButtonPress, local, press_global))
                move_global = press_global + QPoint(28, 34)
                bubble.mouseMoveEvent(
                    _mouse(QEvent.MouseMove, local + QPoint(28, 34),
                           move_global, buttons=Qt.LeftButton))
                bubble.mouseReleaseEvent(
                    _mouse(QEvent.MouseButtonRelease,
                           local + QPoint(28, 34), move_global,
                           buttons=Qt.NoButton))
                self.assertEqual(self.group.pos(), before + QPoint(28, 34))
                self.assertEqual(self.moved[-1],
                                 (self.group.x(), self.group.y()))

        self.assertEqual(self.captured, [])
        self.assertEqual(self.opened_question, [])

    def test_child_clicks_dispatch_their_own_actions(self):
        for bubble in (self.group.capture_bubble,
                       self.group.question_bubble):
            bubble._press_offset = QPoint(0, 0)
            bubble._dragged = False
            bubble.mouseReleaseEvent(
                _point_event(QEvent.MouseButtonRelease, 5,
                             bubble.mapToGlobal(QPoint(5, 5)).x(),
                             buttons=Qt.NoButton))
        self.assertEqual(self.captured, ["selection"])
        self.assertEqual(self.opened_question, [True])


class StandaloneBubbleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def test_standalone_bubbles_remain_top_level_and_self_dragging(self):
        bubbles = [CaptureBubble(), QuestionBubble()]
        self.addCleanup(self._destroy, bubbles)
        for bubble in bubbles:
            bubble.show_at(-1, -1)
            self.assertIs(bubble.window(), bubble)
            self.assertIs(bubble._drag_target(), bubble)
            self.assertTrue(bubble.testAttribute(Qt.WA_TranslucentBackground))
            self.assertTrue(bubble.testAttribute(Qt.WA_ShowWithoutActivating))
        self.app.processEvents()

    def _destroy(self, bubbles):
        for bubble in bubbles:
            bubble.close()
        self.app.processEvents()
        for bubble in bubbles:
            shiboken6.delete(bubble)
        self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
