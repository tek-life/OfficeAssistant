import html
import tempfile
import unittest
from pathlib import Path

import shiboken6
from PySide6.QtCore import Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton

from db import Database
from qt_gui import DiaryWindow, md_to_html


class DiaryMarkdownTests(unittest.TestCase):
    def test_strikethrough_renders_each_pair(self):
        rendered = md_to_html("~~取消任务~~ 保留 ~~旧计划~~")
        self.assertIn("<s>取消任务</s> 保留 <s>旧计划</s>", rendered)

    def test_underline_renders_each_pair(self):
        rendered = md_to_html("<u>重点</u> 普通 <u>期限</u>")
        self.assertIn("<u>重点</u> 普通 <u>期限</u>", rendered)

    def test_new_formats_combine_with_existing_formats(self):
        cases = (
            ("~~**取消**~~", "<s><b>取消</b></s>"),
            ("<u>*强调*</u>", "<u><i>强调</i></u>"),
            ("~~<u>过期</u>~~", "<s><u>过期</u></s>"),
            ("<u>~~过期~~</u>", "<u><s>过期</s></u>"),
        )
        for source, expected in cases:
            with self.subTest(source=source):
                self.assertIn(expected, md_to_html(source))

    def test_formats_work_in_headings_and_lists(self):
        for prefix in ("# ", "## ", "### ", "- ", "* ", "+ ", "1. "):
            with self.subTest(prefix=prefix):
                rendered = md_to_html(prefix + "~~取消~~ <u>重点</u>")
                self.assertIn("<s>取消</s> <u>重点</u>", rendered)

    def test_inline_code_keeps_formatting_markers_literal(self):
        for source in ("~~原样~~", "<u>原样</u>", "**原样**", "*原样*", "[原样](url)"):
            with self.subTest(source=source):
                rendered = md_to_html(f"`{source}`")
                self.assertIn(f">{html.escape(source)}</code>", rendered)

    def test_format_can_surround_inline_code(self):
        rendered = md_to_html("~~取消 `old()` 调用~~ <u>保留 `new()` 调用</u>")
        self.assertIn("<s>取消 <code ", rendered)
        self.assertIn("old()</code> 调用</s>", rendered)
        self.assertIn("<u>保留 <code ", rendered)
        self.assertIn("new()</code> 调用</u>", rendered)

    def test_code_blocks_keep_new_markers_literal(self):
        source = "~~原样~~ <u>原样</u>"
        rendered = md_to_html(f"```\n{source}\n```")
        self.assertIn(html.escape(source), rendered)
        self.assertNotIn("<s>", rendered)
        self.assertNotIn("<u>", rendered)

    def test_only_plain_underline_tags_are_allowed(self):
        source = '<u><img src="file:///not-an-image"> & 文本</u>'
        rendered = md_to_html(source)
        self.assertIn('<u>&lt;img src=&quot;file:///not-an-image&quot;&gt; &amp; 文本</u>', rendered)
        for source in ('<script>alert(1)</script>', '<u onclick="alert(1)">文本</u>',
                       '<u style="color:red">文本</u>', '<code-placeholder-0/>'):
            with self.subTest(source=source):
                self.assertIn(html.escape(source), md_to_html(source))

    def test_incomplete_markers_stay_literal(self):
        for source in ("~~未闭合", "单个 ~符号~", "<u>未闭合", "未打开</u>"):
            with self.subTest(source=source):
                self.assertIn(html.escape(source), md_to_html(source))

    def test_existing_formats_are_preserved(self):
        rendered = md_to_html("**粗体** *斜体* `代码` [链接](url)\n---")
        self.assertIn("<b>粗体</b> <i>斜体</i>", rendered)
        self.assertIn(">代码</code> <u>链接</u>", rendered)
        self.assertIn("<hr/>", rendered)


class DiaryWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp_dir.name) / "test.db")
        self.window = DiaryWindow(self.db)
        self.window.load_date("2026-10-09")
        self.window.show()
        self.app.processEvents()

    def tearDown(self):
        self.window.close()
        shiboken6.delete(self.window)
        self.app.processEvents()
        self.db.close()
        self.temp_dir.cleanup()

    def click(self, widget):
        QTest.mouseClick(widget, Qt.LeftButton)
        self.app.processEvents()

    def format_button(self, label):
        buttons = [b for b in self.window.toolbar.findChildren(QPushButton)
                   if b.text() == label]
        self.assertEqual(len(buttons), 1, f"缺少 {label} 格式按钮")
        return buttons[0]

    def test_buttons_wrap_selection_and_preserve_selection(self):
        self.click(self.window.toggle_btn)
        for label, before, after in (("S", "~~", "~~"), ("U", "<u>", "</u>")):
            with self.subTest(label=label):
                self.window.editor.setPlainText("中文内容")
                self.window.editor.selectAll()
                self.click(self.format_button(label))
                self.assertEqual(self.window.editor.toPlainText(), f"{before}中文内容{after}")
                self.assertEqual(self.window.editor.textCursor().selectedText(), "中文内容")
                self.assertEqual(self.window.focusWidget(), self.window.editor)

    def test_empty_selection_places_cursor_between_markers(self):
        self.click(self.window.toggle_btn)
        for label, before, after in (("S", "~~", "~~"), ("U", "<u>", "</u>")):
            with self.subTest(label=label):
                self.window.editor.clear()
                self.click(self.format_button(label))
                self.assertEqual(self.window.editor.textCursor().position(), len(before))
                QTest.keyClicks(self.window.editor, "text")
                self.assertEqual(self.window.editor.toPlainText(), f"{before}text{after}")

    def test_new_buttons_have_visual_format_and_tooltips(self):
        strike = self.format_button("S")
        underline = self.format_button("U")
        self.assertTrue(strike.font().strikeOut())
        self.assertTrue(underline.font().underline())
        self.assertIn("删除线", strike.toolTip())
        self.assertIn("下划线", underline.toolTip())
        self.assertTrue(self.window.toolbar.isHidden())
        self.click(self.window.toggle_btn)
        for button in (strike, underline):
            self.assertTrue(button.isVisible())
            self.assertTrue(self.window.toolbar.rect().contains(button.geometry()))

    def test_toolbar_fits_default_window_without_clipping_labels(self):
        self.click(self.window.toggle_btn)
        for expanded in (True, False):
            self.assertEqual(self.window.calendar_panel.isVisible(), expanded)
            for button in self.window.toolbar.findChildren(QPushButton):
                with self.subTest(expanded=expanded, label=button.text()):
                    self.assertTrue(self.window.toolbar.rect().contains(button.geometry()))
                    self.assertGreater(button.width(), button.fontMetrics().horizontalAdvance(button.text()))
            if expanded:
                self.click(self.window.history_button)

    def test_save_and_reload_preserves_source_and_display_formats(self):
        self.click(self.window.toggle_btn)
        self.window.editor.setPlainText("取消任务")
        self.window.editor.selectAll()
        self.click(self.format_button("S"))
        cursor = self.window.editor.textCursor()
        cursor.movePosition(QTextCursor.End)
        self.window.editor.setTextCursor(cursor)
        self.window.editor.insertPlainText(" ")
        self.click(self.format_button("U"))
        self.window.editor.insertPlainText("重点内容")
        source = "~~取消任务~~ <u>重点内容</u>"
        self.click(self.window.toggle_btn)
        self.assertEqual(self.db.get_daily_note("2026-10-09")["content"], source)
        self.window.editor.clear()
        self.window.load_date("2026-10-09")
        self.assertEqual(self.window.editor.toPlainText(), source)
        self.assertEqual(self.window.preview.toPlainText(), "取消任务 重点内容")
        document = self.window.preview.document()
        self.assertTrue(document.find("取消任务").charFormat().fontStrikeOut())
        self.assertTrue(document.find("重点内容").charFormat().fontUnderline())
        self.assertFalse(document.find("重点内容").charFormat().fontStrikeOut())
        self.assertTrue(self.window.toolbar.isHidden())
        self.window.close()
        self.window.show()
        self.app.processEvents()
        self.assertTrue(self.window.isVisible())
        self.assertEqual(self.window.preview.toPlainText(), "取消任务 重点内容")

    def test_qt_preview_keeps_inline_code_literal_and_unformatted(self):
        self.window.editor.setPlainText("`~~原样~~ <u>原样</u>`\n~~删除~~ <u>强调</u>")
        self.window.enter_read_mode()
        document = self.window.preview.document()
        literal = document.find("~~原样~~ <u>原样</u>")
        self.assertFalse(literal.isNull())
        self.assertFalse(literal.charFormat().fontStrikeOut())
        self.assertFalse(literal.charFormat().fontUnderline())
        self.assertTrue(document.find("删除").charFormat().fontStrikeOut())
        self.assertTrue(document.find("强调").charFormat().fontUnderline())


if __name__ == "__main__":
    unittest.main()
