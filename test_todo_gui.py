import html
import tempfile
import unittest
from pathlib import Path

import shiboken6
from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtGui import QHelpEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QLabel, QPushButton, QTabBar, QToolTip,
)

from db import Database
from qt_gui import TodoEditorWindow


class TodoEditorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp_dir.name) / "test.db")
        self.notifications = 0
        self.window = TodoEditorWindow(self.db, on_changed=self.on_changed)
        self.window.show()
        self.app.processEvents()

    def tearDown(self):
        self.window.close()
        shiboken6.delete(self.window)
        self.app.processEvents()
        self.db.close()
        self.temp_dir.cleanup()

    def on_changed(self):
        self.notifications += 1

    def click(self, widget):
        QTest.mouseClick(widget, Qt.LeftButton)
        self.app.processEvents()

    def row_widget(self, index=0):
        item = self.window.list.item(index)
        self.assertIsNotNone(item)
        widget = self.window.list.itemWidget(item)
        self.assertIsNotNone(widget, "每条待办需要可交互的行控件")
        return widget

    def checkbox(self, index=0):
        checkbox = self.row_widget(index).findChild(QCheckBox)
        self.assertIsNotNone(checkbox, "前面的框必须是真实复选框")
        return checkbox

    def row_button(self, text, index=0):
        buttons = [b for b in self.row_widget(index).findChildren(QPushButton)
                   if b.text() == text and not b.isHidden()]
        self.assertEqual(len(buttons), 1)
        return buttons[0]

    def switch_tab(self, index):
        tabs = self.window.findChild(QTabBar)
        self.assertIsNotNone(tabs, "需要待办和已归档页签")
        QTest.mouseClick(tabs, Qt.LeftButton, pos=tabs.tabRect(index).center())
        self.app.processEvents()

    def add_done(self, text="已完成任务"):
        todo_id = self.db.add_todo(text)
        self.db.toggle_todo(todo_id)
        self.window.refresh_list()
        self.app.processEvents()
        return todo_id

    def created_label(self, index=0):
        labels = [label for label in self.row_widget(index).findChildren(QLabel)
                  if label.text().startswith("创建于 ")]
        self.assertEqual(len(labels), 1, "每条任务应显示一行创建时间")
        return labels[0]

    def test_creation_time_uses_stored_local_time_to_the_minute(self):
        todo_id = self.db.add_todo("较早创建的任务")
        self.db.conn.execute(
            "UPDATE todos SET created_at=? WHERE id=?",
            ("2025-12-31 23:59:47", todo_id),
        )
        self.db.conn.commit()
        self.window.refresh_list()
        self.app.processEvents()
        self.assertEqual(self.created_label().text(), "创建于 2025-12-31 23:59")
        self.assertEqual(self.notifications, 0)

    def test_new_todo_shows_its_own_creation_time(self):
        self.window.entry.setText("新增任务")
        QTest.keyClick(self.window.entry, Qt.Key_Return)
        self.app.processEvents()
        row = self.db.get_todos()[0]
        self.assertEqual(self.created_label().text(), f"创建于 {row['created_at'][:16]}")

    def test_creation_time_unchanged_after_completing_archiving_and_restoring(self):
        todo_id = self.db.add_todo("保留创建时间")
        self.db.conn.execute(
            "UPDATE todos SET created_at=? WHERE id=?",
            ("2024-02-29 08:05:30", todo_id),
        )
        self.db.conn.commit()
        self.window.refresh_list()
        self.app.processEvents()
        expected = "创建于 2024-02-29 08:05"
        self.assertEqual(self.created_label().text(), expected)
        self.click(self.checkbox())
        self.assertEqual(self.created_label().text(), expected)
        self.assertFalse(self.created_label().font().strikeOut())
        self.click(self.row_button("归档"))
        self.switch_tab(1)
        self.assertEqual(self.created_label().text(), expected)
        self.assertFalse(self.created_label().font().strikeOut())
        self.click(self.row_button("恢复"))
        self.switch_tab(0)
        self.assertEqual(self.created_label().text(), expected)
        self.assertEqual(self.db.get_todos()[0]["created_at"], "2024-02-29 08:05:30")

    def test_creation_time_is_secondary_line_and_fits_narrow_window(self):
        self.add_done("需要保留足够空间的长任务标题 " * 12)
        self.window.resize(380, 360)
        self.app.processEvents()
        row = self.row_widget()
        title = row.findChild(QLabel)
        created = self.created_label()
        button = self.row_button("归档")
        self.assertGreater(created.geometry().top(), title.geometry().bottom())
        self.assertEqual(created.geometry().left(), title.geometry().left())
        self.assertLess(created.font().pointSizeF(), title.font().pointSizeF())
        self.assertGreaterEqual(created.width(), created.fontMetrics().horizontalAdvance(created.text()))
        self.assertTrue(row.rect().contains(created.geometry()))
        self.assertTrue(row.rect().contains(button.geometry()))
        self.assertFalse(created.geometry().intersects(button.geometry()))

    def test_creation_time_has_muted_foreground_in_list(self):
        self.add_done("区分正文与创建时间")
        title = self.row_widget().findChild(QLabel)
        created = self.created_label()
        title_color = title.palette().color(title.foregroundRole())
        created_color = created.palette().color(created.foregroundRole())
        self.assertLess(created_color.alpha(), title_color.alpha())
        self.assertGreater(created_color.alpha(), 0)

    def test_clicking_creation_time_selects_without_toggling(self):
        self.db.add_todo("原先选中的任务")
        todo_id = self.db.add_todo("点击时间只选中任务")
        self.window.refresh_list()
        self.app.processEvents()
        self.window.list.setCurrentRow(0)
        self.click(self.created_label(1))
        self.assertEqual(self.window.list.currentItem().data(Qt.UserRole), todo_id)
        self.assertTrue(all(r["done"] == 0 for r in self.db.get_todos()))
        self.assertEqual(self.notifications, 0)

    def test_tabs_default_to_active_and_handle_empty_lists(self):
        tabs = self.window.findChild(QTabBar)
        self.assertIsNotNone(tabs)
        self.assertEqual([tabs.tabText(i) for i in range(tabs.count())],
                         ["待办", "已归档"])
        self.assertEqual(tabs.currentIndex(), 0)
        self.assertEqual(self.window.list.count(), 0)
        self.switch_tab(1)
        self.assertEqual(self.window.list.count(), 0)
        self.assertFalse(self.window.entry.isVisible())
        self.switch_tab(0)
        self.assertTrue(self.window.entry.isVisible())
        self.assertEqual(self.notifications, 0)

    def test_single_checkbox_click_completes_and_reopens_todo(self):
        todo_id = self.db.add_todo("单击完成")
        self.window.refresh_list()
        self.app.processEvents()
        self.assertFalse(self.checkbox().isChecked())
        self.assertFalse(self.row_widget().findChildren(QPushButton))
        self.click(self.checkbox())
        self.assertEqual(self.db.get_todos()[0]["id"], todo_id)
        self.assertEqual(self.db.get_todos()[0]["done"], 1)
        self.assertTrue(self.checkbox().isChecked())
        self.assertTrue(self.row_button("归档").isVisible())
        self.assertEqual(self.notifications, 1)
        self.click(self.checkbox())
        self.assertEqual(self.db.get_todos()[0]["done"], 0)
        self.assertFalse(self.row_widget().findChildren(QPushButton))
        self.assertEqual(self.notifications, 2)

    def test_archive_and_restore_keep_done_state_and_notify_wallpaper(self):
        todo_id = self.add_done()
        self.click(self.row_button("归档"))
        self.assertEqual(self.window.list.count(), 0)
        self.assertEqual(self.db.get_todos(), [])
        self.assertEqual(self.notifications, 1)
        self.switch_tab(1)
        self.assertEqual(self.window.list.count(), 1)
        self.assertTrue(self.checkbox().isChecked())
        self.assertFalse(self.checkbox().isEnabled())
        self.click(self.checkbox())
        self.assertEqual(self.db.get_todos(archived=True)[0]["done"], 1)
        self.assertEqual(self.notifications, 1)
        self.click(self.row_button("恢复"))
        self.assertEqual(self.window.list.count(), 0)
        self.assertEqual(self.notifications, 2)
        self.switch_tab(0)
        self.assertEqual(self.window.list.count(), 1)
        self.assertEqual(self.db.get_todos()[0]["id"], todo_id)
        self.assertTrue(self.checkbox().isChecked())
        self.assertTrue(self.row_button("归档").isVisible())

    def test_each_row_action_targets_its_own_id(self):
        first = self.db.add_todo("第一条")
        second = self.db.add_todo("第二条")
        third = self.add_done("第三条")
        self.click(self.checkbox(1))
        rows = self.db.get_todos()
        self.assertEqual([(r["id"], r["done"]) for r in rows],
                         [(first, 0), (second, 1), (third, 1)])
        self.click(self.row_button("归档", 1))
        self.assertEqual([r["id"] for r in self.db.get_todos()], [first, third])
        self.click(self.row_button("归档", 1))
        self.assertEqual([r["id"] for r in self.db.get_todos()], [first])
        self.assertEqual(self.notifications, 3)

    def test_archive_tab_displays_newest_archive_first(self):
        first = self.add_done("早归档")
        second = self.add_done("晚归档")
        self.db.archive_todo(first)
        self.db.archive_todo(second)
        self.window.refresh_list()
        self.switch_tab(1)
        self.assertEqual(
            [self.window.list.item(i).data(Qt.UserRole)
             for i in range(self.window.list.count())], [second, first]
        )

    def test_clicking_task_text_selects_without_toggling(self):
        todo_id = self.db.add_todo("只选中这条任务")
        self.window.refresh_list()
        self.app.processEvents()
        self.click(self.row_widget().findChild(QLabel))
        self.assertEqual(self.window.list.currentItem().data(Qt.UserRole), todo_id)
        self.assertEqual(self.db.get_todos()[0]["done"], 0)
        self.assertEqual(self.notifications, 0)

    def test_delete_uses_selected_id_instead_of_database_row_position(self):
        first = self.db.add_todo("要删除的任务")
        second = self.add_done("保留的任务")
        self.window.list.setCurrentRow(0)
        self.db.reorder_todos([second, first])
        delete_button = next(b for b in self.window.findChildren(QPushButton)
                             if b.text() == "删除")
        self.click(delete_button)
        self.assertEqual([r["id"] for r in self.db.get_todos()], [second])
        self.assertEqual(self.notifications, 1)

    def test_add_and_blank_input_still_work(self):
        QTest.keyClick(self.window.entry, Qt.Key_Return)
        self.assertEqual(self.db.get_todos(), [])
        self.assertEqual(self.notifications, 0)
        self.window.entry.setText("  新建中文任务  ")
        QTest.keyClick(self.window.entry, Qt.Key_Return)
        self.app.processEvents()
        self.assertEqual(self.db.get_todos()[0]["text"], "新建中文任务")
        self.assertEqual(self.window.entry.text(), "")
        self.assertFalse(self.checkbox().isChecked())
        self.assertEqual(self.notifications, 1)

    def test_long_plain_text_keeps_archive_button_inside_row(self):
        text = "<b>不是 HTML</b> 中文长任务 " * 12
        self.add_done(text)
        self.window.resize(380, 360)
        self.app.processEvents()
        label = self.row_widget().findChild(QLabel)
        self.assertEqual(label.text(), text)
        self.assertEqual(label.textFormat(), Qt.PlainText)
        button = self.row_button("归档")
        self.assertTrue(self.row_widget().rect().contains(button.geometry()))
        self.click(button)
        self.assertEqual(self.db.get_todos(), [])

    def test_hovering_truncated_task_shows_full_plain_text(self):
        text = "<b>详细任务内容</b>" * 20
        self.add_done(text)
        row = self.row_widget()
        label = row.findChild(QLabel)
        pos = label.geometry().center()
        target = row.childAt(pos) or row
        global_pos = row.mapToGlobal(pos)
        event = QHelpEvent(QEvent.ToolTip, target.mapFromGlobal(global_pos), global_pos)
        self.app.sendEvent(target, event)
        self.assertEqual(QToolTip.text(), f"<qt>{html.escape(text)}</qt>")
        QToolTip.hideText()

    def test_toggling_other_row_preserves_explicit_delete_selection(self):
        first = self.db.add_todo("选中的任务")
        second = self.db.add_todo("只勾选完成的任务")
        self.window.refresh_list()
        self.app.processEvents()
        self.window.list.setCurrentRow(0)
        self.click(self.checkbox(1))
        self.assertEqual(self.window.list.currentItem().data(Qt.UserRole), first)
        delete_button = next(b for b in self.window.findChildren(QPushButton)
                             if b.text() == "删除")
        self.click(delete_button)
        self.assertEqual([(r["id"], r["done"]) for r in self.db.get_todos()],
                         [(second, 1)])

    def test_scrolled_list_keeps_position_after_toggle(self):
        ids = [self.db.add_todo(f"任务 {i}") for i in range(200)]
        self.window.refresh_list()
        self.app.processEvents()
        self.window.list.scrollToBottom()
        self.app.processEvents()
        old_position = self.window.list.verticalScrollBar().value()
        self.assertGreater(old_position, 0)
        self.click(self.checkbox(199))
        self.assertEqual(self.window.list.count(), 200)
        self.assertGreater(self.window.list.verticalScrollBar().value(), 0)
        self.assertEqual(self.db.get_todos()[-1]["id"], ids[-1])
        self.assertEqual(self.db.get_todos()[-1]["done"], 1)

    def test_closing_and_reopening_preserves_archived_view(self):
        self.add_done()
        self.click(self.row_button("归档"))
        self.window.close()
        self.window.refresh_list()
        self.window.show()
        self.app.processEvents()
        self.assertTrue(self.window.isVisible())
        self.assertEqual(self.window.list.count(), 0)
        self.switch_tab(1)
        self.assertEqual(self.window.list.count(), 1)
        self.assertTrue(self.row_button("恢复").isVisible())

    def test_window_uses_white_surface_and_dark_primary_action(self):
        self.assertEqual(self.window.palette().color(self.window.backgroundRole()).name(),
                         "#ffffff")
        add_button = self.window.findChild(QPushButton, "addTodo")
        self.assertIsNotNone(add_button)
        self.assertEqual(add_button.palette().color(add_button.backgroundRole()).name(),
                         "#242424")
        self.assertEqual(self.window.windowTitle(), "待办清单")

    def test_integrated_titlebar_keeps_content_and_window_controls_separate(self):
        bar = self.window.findChild(QLabel, "titleBarLabel")
        self.assertIsNotNone(bar)
        self.assertEqual(bar.text(), "待办清单")
        self.assertEqual(bar.parentWidget().height(), 40)
        self.assertEqual(self.window.layout().contentsMargins().top(), 0)
        self.assertGreater(self.window.summary.mapTo(self.window, QPoint()).y(), 40)
        self.assertTrue(self.window.entry.isVisible())

    def test_initial_focus_stays_in_todo_input(self):
        self.assertIs(self.window.focusWidget(), self.window.entry)

    def test_caption_close_preserves_todos_for_reopening(self):
        button = self.window.findChild(QPushButton, "captionClose")
        self.assertIsNotNone(button)
        self.db.add_todo("关闭不删除")
        self.window.refresh_list()
        self.click(button)
        self.assertFalse(self.window.isVisible())
        self.assertTrue(self.window._closed)
        self.window.show()
        self.app.processEvents()
        self.assertEqual(self.window.list.count(), 1)
        self.assertEqual(self.db.get_todos()[0]["text"], "关闭不删除")

    def test_caption_controls_fit_minimum_todo_width(self):
        self.window.resize(self.window.minimumSize())
        self.app.processEvents()
        for name in ("captionMinimize", "captionMaximize", "captionClose"):
            button = self.window.findChild(QPushButton, name)
            self.assertIsNotNone(button)
            self.assertEqual(button.size().width(), 42)
            self.assertTrue(button.parentWidget().rect().contains(button.geometry()))
            self.assertTrue(button.accessibleName())

    def test_empty_state_distinguishes_active_and_archived_lists(self):
        title = self.window.findChild(QLabel, "todoEmptyTitle")
        self.assertIsNotNone(title)
        self.assertTrue(title.isVisible())
        self.assertEqual(title.text(), "清单很干净")
        self.switch_tab(1)
        self.assertEqual(title.text(), "还没有归档事项")
        self.switch_tab(0)
        self.window.entry.setText("开始工作")
        QTest.keyClick(self.window.entry, Qt.Key_Return)
        self.app.processEvents()
        self.assertFalse(title.isVisible())
        self.assertTrue(self.window.list.isVisible())

    def test_summary_tracks_completion_and_archive_state(self):
        summary = self.window.findChild(QLabel, "todoSummary")
        self.assertIsNotNone(summary)
        self.db.add_todo("第一件事")
        self.db.add_todo("第二件事")
        self.window.refresh_list()
        self.app.processEvents()
        self.assertEqual(summary.text(), "2 件待完成 · 0 件已完成")
        self.click(self.checkbox())
        self.assertEqual(summary.text(), "1 件待完成 · 1 件已完成")
        self.click(self.row_button("归档"))
        self.assertEqual(summary.text(), "1 件待完成 · 0 件已完成")
        self.switch_tab(1)
        self.assertEqual(summary.text(), "已归档 1 件 · 可随时恢复")

    def test_delete_is_disabled_until_a_task_is_selected(self):
        button = next(b for b in self.window.findChildren(QPushButton)
                      if b.text() == "删除")
        self.assertFalse(button.isEnabled())
        self.db.add_todo("选择后删除")
        self.window.refresh_list()
        self.app.processEvents()
        self.assertFalse(button.isEnabled())
        self.click(self.row_widget().findChild(QLabel))
        self.assertTrue(button.isEnabled())
        self.click(button)
        self.assertFalse(button.isEnabled())
        self.assertEqual(self.window.list.count(), 0)

    def test_checkbox_supports_keyboard_and_visible_focus(self):
        self.db.add_todo("键盘完成")
        self.window.refresh_list()
        self.app.processEvents()
        self.checkbox().setFocus()
        QTest.keyClick(self.checkbox(), Qt.Key_Space)
        self.app.processEvents()
        self.assertTrue(self.checkbox().isChecked())
        self.assertTrue(self.checkbox().hasFocus())
        self.assertEqual(self.notifications, 1)

    def test_checkbox_edges_toggle_their_task(self):
        self.db.add_todo("边缘点击")
        self.window.refresh_list()
        self.app.processEvents()
        for index, point in enumerate((QPoint(20, 12), QPoint(12, 20),
                                       QPoint(4, 12), QPoint(12, 4))):
            with self.subTest(point=point):
                QTest.mouseClick(self.checkbox(), Qt.LeftButton, pos=point)
                self.app.processEvents()
                self.assertEqual(self.db.get_todos()[0]["done"], (index + 1) % 2)
                self.assertEqual(self.notifications, index + 1)

    def test_ctrl_deselect_disables_delete_with_current_item_remaining(self):
        self.db.add_todo("取消选择")
        self.window.refresh_list()
        self.app.processEvents()
        self.window.list.setCurrentRow(0)
        item = self.window.list.item(0)
        QTest.mouseClick(self.window.list.viewport(), Qt.LeftButton, Qt.ControlModifier,
                         self.window.list.visualItemRect(item).center())
        self.app.processEvents()
        self.assertIs(self.window.list.currentItem(), item)
        self.assertEqual(self.window.list.selectedItems(), [])
        self.assertFalse(self.window.delete_button.isEnabled())
        self.click(self.window.delete_button)
        self.assertEqual(len(self.db.get_todos()), 1)
        self.assertEqual(self.notifications, 0)

    def test_delete_without_selection_does_not_remove_current_item(self):
        self.db.add_todo("未选中时保留")
        self.window.refresh_list()
        self.window.list.setCurrentRow(0)
        self.window.list.clearSelection()
        self.window.delete_selected()
        self.assertEqual(len(self.db.get_todos()), 1)
        self.assertEqual(self.notifications, 0)

    def test_toggling_after_deselect_does_not_restore_delete_selection(self):
        self.db.add_todo("取消选择的任务")
        self.db.add_todo("仅勾选的任务")
        self.window.refresh_list()
        self.app.processEvents()
        self.window.list.setCurrentRow(0)
        self.window.list.clearSelection()
        self.click(self.checkbox(1))
        self.assertEqual(self.window.list.selectedItems(), [])
        self.assertFalse(self.window.delete_button.isEnabled())
        self.assertEqual([r["done"] for r in self.db.get_todos()], [0, 1])


if __name__ == "__main__":
    unittest.main()
