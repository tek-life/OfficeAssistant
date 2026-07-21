"""
办公助手 Qt GUI 模块。

为什么用 Qt：Windows 上 tkinter 的 TSF 输入法支持不完整，内联拼音组词串
字号不跟随正文字体（偏小）。Qt 的 QTextEdit/QLineEdit 有完整 TSF 支持，
拼音串自动与正文等大。

架构：QApplication 运行在独立线程（QtGuiManager），所有窗口的创建与操作
都通过请求队列投递到该线程执行，满足 Qt "控件只能在创建它的线程操作" 的约束。
"""

import html as _html
import queue
import re
import threading
from datetime import datetime

import shiboken6
from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QFont, QTextCursor
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QStackedWidget, QTextEdit, QPlainTextEdit, QDialog, QListWidget,
    QListWidgetItem, QLineEdit, QMessageBox,
)

BASE_FONT = "Microsoft YaHei UI"
BASE_SIZE = 13  # pt


# ═══════════════════════════════════════════════════════════
# Qt 事件循环管理器
# ═══════════════════════════════════════════════════════════

class QtGuiManager:
    """在独立线程中运行 Qt 事件循环，窗口操作统一经请求队列投递。"""

    def __init__(self):
        self._queue = queue.Queue()
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout=15):
            print("[Qt] 事件循环启动超时")

    def _run(self):
        self.app = QApplication([])
        # 关键：关闭"最后一个窗口关闭即退出"的默认行为。否则用户关掉日志/待办窗口
        # （此时是唯一窗口）会导致整个 Qt 事件循环退出，再次打开时无任何反应。
        self.app.setQuitOnLastWindowClosed(False)
        timer = QTimer()
        timer.timeout.connect(self._drain)
        timer.start(50)
        self._ready.set()
        self.app.exec()

    def _drain(self):
        while True:
            try:
                fn = self._queue.get_nowait()
            except queue.Empty:
                return
            try:
                fn()
            except Exception as e:
                print(f"[Qt] 窗口操作失败: {e}")

    def post(self, fn):
        """把任意可调用对象投递到 Qt 线程执行。"""
        self._queue.put(fn)

    def shutdown(self, windows=None):
        """干净退出：关闭并销毁窗口，再退出事件循环（避免进程退出时崩溃）。"""
        def _cleanup():
            for w in (windows or []):
                if w is not None and shiboken6.isValid(w):
                    try:
                        w.close()
                        shiboken6.delete(w)
                    except Exception:
                        pass
            self.app.quit()

        self.post(_cleanup)
        self._thread.join(timeout=3)


# ═══════════════════════════════════════════════════════════
# Markdown → HTML（供阅读模式渲染）
# ═══════════════════════════════════════════════════════════

def _inline(s: str) -> str:
    """处理行内格式（输入已 HTML 转义）。"""
    s = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', s)
    s = re.sub(r'(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)', r'<i>\1</i>', s)
    s = re.sub(r'`([^`]+)`',
               r'<code style="background:#eef2f5;padding:1px 4px;">\1</code>', s)
    s = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'<u>\1</u>', s)
    return s


def md_to_html(md: str) -> str:
    """把 Markdown 子集转成 QTextEdit 可显示的 HTML。"""
    lines = md.split("\n")
    out = []
    in_code = False
    for line in lines:
        if line.strip().startswith("```"):
            if in_code:
                out.append("</pre>")
                in_code = False
            else:
                out.append('<pre style="background:#f4f6f8;padding:8px;'
                           'font-family:Consolas,monospace;">')
                in_code = True
            continue
        if in_code:
            out.append(_html.escape(line) + "<br/>")
            continue

        esc = _html.escape(line)
        if line.startswith("### "):
            out.append(f"<h3>{_inline(esc[4:])}</h3>")
        elif line.startswith("## "):
            out.append(f"<h2>{_inline(esc[3:])}</h2>")
        elif line.startswith("# "):
            out.append(f"<h1>{_inline(esc[2:])}</h1>")
        elif line.strip() in ("---", "***", "___"):
            out.append("<hr/>")
        elif line.lstrip().startswith(("- ", "* ", "+ ")):
            indent = len(line) - len(line.lstrip())
            pad = "&nbsp;" * indent
            out.append(f"<p style='margin:2px 0;'>{pad}•&nbsp;&nbsp;"
                       f"{_inline(esc.lstrip()[2:])}</p>")
        elif re.match(r'^\s*\d+\.\s', line):
            m = re.match(r'^(\s*)(\d+)\.\s(.+)', line)
            if m:
                pad = "&nbsp;" * len(m.group(1))
                out.append(f"<p style='margin:2px 0;'>{pad}{m.group(2)}."
                           f"&nbsp;&nbsp;{_inline(_html.escape(m.group(3)))}</p>")
        elif not line.strip():
            out.append("<p style='margin:4px 0;'>&nbsp;</p>")
        else:
            out.append(f"<p style='margin:2px 0;'>{_inline(esc)}</p>")
    if in_code:
        out.append("</pre>")
    body = "\n".join(out)
    return (f"<html><body style='font-family:{BASE_FONT};font-size:{BASE_SIZE}pt;"
            f"color:#333;'>{body}</body></html>")


# ═══════════════════════════════════════════════════════════
# 工作日志窗口
# ═══════════════════════════════════════════════════════════

class DiaryWindow(QWidget):
    """工作日志：默认阅读模式（Markdown 渲染），点"编辑"进入编辑模式。"""

    def __init__(self, db, on_saved=None):
        super().__init__()
        self.db = db
        self.on_saved = on_saved
        self.current_date = datetime.now().strftime("%Y-%m-%d")
        self.mode = "read"
        self._closed = False
        self._build_ui()

    def _build_ui(self):
        self.setWindowTitle(f"工作日志 — {self.current_date}")
        self.resize(780, 580)
        self.setAttribute(Qt.WA_DeleteOnClose, False)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 14, 18, 14)
        root.setSpacing(8)

        # ── 标题栏 ──
        header = QHBoxLayout()
        self.date_label = QLabel(f"工作日志  {self.current_date}")
        f = QFont(BASE_FONT, 15, QFont.Bold)
        self.date_label.setFont(f)
        header.addWidget(self.date_label)
        header.addStretch(1)
        self.status_label = QLabel("")
        self.status_label.setFont(QFont(BASE_FONT, 9))
        self.status_label.setStyleSheet("color:#999;")
        header.addWidget(self.status_label)
        root.addLayout(header)

        # ── 格式工具栏（仅编辑模式显示）──
        self.toolbar = QWidget()
        tb = QHBoxLayout(self.toolbar)
        tb.setContentsMargins(0, 0, 0, 0)
        tb.setSpacing(4)

        def tbtn(text, fn, bold=False, italic=False):
            b = QPushButton(text)
            b.setFont(QFont(BASE_FONT, 10, QFont.Bold if bold else QFont.Normal,
                            italic))
            b.setFixedHeight(26)
            b.clicked.connect(fn)
            tb.addWidget(b)
            return b

        tbtn("B", lambda: self._wrap("**"), bold=True)
        tbtn("I", lambda: self._wrap("*"), italic=True)
        tbtn("H1", lambda: self._prefix("# "))
        tbtn("H2", lambda: self._prefix("## "))
        tbtn("H3", lambda: self._prefix("### "))
        tbtn("代码", lambda: self._wrap("`"))
        tbtn("• 列表", lambda: self._prefix("- "))
        tbtn("1. 列表", lambda: self._prefix("1. "))
        tbtn("链接", lambda: self._wrap("[", "](url)"))
        tbtn("引用", lambda: self._prefix("> "))
        tb.addStretch(1)
        self.toolbar.hide()
        root.addWidget(self.toolbar)

        # ── 内容区：阅读 / 编辑 双页 ──
        self.stack = QStackedWidget()

        self.preview = QTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setFont(QFont(BASE_FONT, BASE_SIZE))
        self.preview.setStyleSheet(
            "QTextEdit{background:#fff;border:1px solid #e0e0e0;border-radius:6px;}")
        # 双击进入编辑模式：QTextEdit 的鼠标事件走内部 viewport，需装在 viewport 上
        self.preview.viewport().installEventFilter(self)
        self.stack.addWidget(self.preview)   # index 0 = 阅读

        self.editor = QPlainTextEdit()
        self.editor.setFont(QFont(BASE_FONT, BASE_SIZE))
        self.editor.setStyleSheet(
            "QPlainTextEdit{background:#fff;border:1px solid #e0e0e0;"
            "border-radius:6px;}")
        self.stack.addWidget(self.editor)    # index 1 = 编辑

        root.addWidget(self.stack, 1)

        # ── 底部按钮 ──
        btns = QHBoxLayout()

        def button(text, color, fn):
            b = QPushButton(text)
            b.setFont(QFont(BASE_FONT, 10))
            b.setFixedHeight(32)
            b.setStyleSheet(
                f"QPushButton{{background:{color};color:#fff;border:none;"
                f"border-radius:5px;padding:0 18px;}}"
                f"QPushButton:hover{{opacity:0.9;}}")
            b.clicked.connect(fn)
            btns.addWidget(b)
            return b

        button("保存", "#4CAF50", self.save)
        button("保存并关闭", "#1976D2", self.save_and_close)
        button("历史日志", "#757575", self.open_history)
        btns.addStretch(1)
        self.toggle_btn = button("编辑", "#1976D2", self.toggle_mode)
        root.addLayout(btns)

    # --- 工具栏操作 ---
    def _wrap(self, before, after=None):
        if after is None:
            after = before
        cur = self.editor.textCursor()
        sel = cur.selectedText()
        cur.insertText(f"{before}{sel}{after}")
        # 重新选中内部文本
        pos = cur.position()
        cur.setPosition(pos - len(after) - len(sel))
        cur.setPosition(pos - len(after), QTextCursor.KeepAnchor)
        self.editor.setTextCursor(cur)
        self.editor.setFocus()

    def _prefix(self, prefix):
        cur = self.editor.textCursor()
        cur.movePosition(QTextCursor.StartOfLine)
        cur.insertText(prefix)
        self.editor.setFocus()

    # --- 模式切换 ---
    def enter_read_mode(self):
        self.preview.setHtml(md_to_html(self.editor.toPlainText()))
        self.stack.setCurrentIndex(0)
        self.toolbar.hide()
        self.toggle_btn.setText("编辑")
        self.mode = "read"

    def enter_edit_mode(self):
        self.stack.setCurrentIndex(1)
        self.toolbar.show()
        self.toggle_btn.setText("保存并返回")
        self.mode = "edit"
        self.editor.setFocus()

    def toggle_mode(self):
        if self.mode == "read":
            self.enter_edit_mode()
        else:
            self.save()
            self.enter_read_mode()

    # --- 数据 ---
    def save(self):
        content = self.editor.toPlainText()
        self.db.save_daily_note(self.current_date, content)
        self.status_label.setText("已保存 ✓")
        QTimer.singleShot(2000, lambda: self.status_label.setText(""))
        if self.on_saved:
            self.on_saved()

    def save_and_close(self):
        self.save()
        self.close()

    def load_date(self, d):
        self.current_date = d
        note = self.db.get_daily_note(d)
        self.editor.setPlainText(note.get("content", ""))
        self.date_label.setText(f"工作日志  {d}")
        self.setWindowTitle(f"工作日志 — {d}")
        self.enter_read_mode()

    # --- 历史浏览 ---
    def open_history(self):
        dates = self.db.get_all_note_dates()
        if not dates:
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("历史日志")
        dlg.resize(280, 400)
        lay = QVBoxLayout(dlg)

        tip = QLabel("选择日期")
        tip.setFont(QFont(BASE_FONT, 11, QFont.Bold))
        lay.addWidget(tip)

        lw = QListWidget()
        lw.setFont(QFont(BASE_FONT, 11))
        for d in dates:
            lw.addItem(QListWidgetItem(d))
        lay.addWidget(lw, 1)

        def on_open():
            item = lw.currentItem()
            if item:
                self.save()          # 先保存当前
                self.load_date(item.text())
                dlg.accept()

        lw.itemDoubleClicked.connect(lambda *_: on_open())
        ob = QPushButton("打开")
        ob.setFont(QFont(BASE_FONT, 10))
        ob.setFixedHeight(30)
        ob.setStyleSheet("QPushButton{background:#1976D2;color:#fff;"
                         "border:none;border-radius:5px;}")
        ob.clicked.connect(on_open)
        lay.addWidget(ob)
        dlg.exec()

    # --- 关闭 ---
    def closeEvent(self, event):
        self._closed = True
        event.accept()

    def eventFilter(self, obj, event):
        """阅读模式下双击预览区进入编辑模式。"""
        from PySide6.QtCore import QEvent
        if obj is self.preview.viewport() and event.type() == QEvent.MouseButtonDblClick:
            if self.mode == "read":
                self.enter_edit_mode()
                return True
        return super().eventFilter(obj, event)


# ═══════════════════════════════════════════════════════════
# 待办编辑窗口
# ═══════════════════════════════════════════════════════════

class TodoEditorWindow(QWidget):
    """待办事项编辑：列表 + 勾选/删除 + 新增输入框。"""

    def __init__(self, db, on_changed=None):
        super().__init__()
        self.db = db
        self.on_changed = on_changed
        self._closed = False
        self._build_ui()
        self.refresh_list()

    def _build_ui(self):
        self.setWindowTitle("编辑待办")
        self.resize(560, 480)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 14, 18, 14)
        root.setSpacing(8)

        self.list = QListWidget()
        self.list.setFont(QFont(BASE_FONT, 12))
        root.addWidget(self.list, 1)

        ops = QHBoxLayout()
        for text, fn in (("切换完成", self.toggle_selected),
                         ("删除", self.delete_selected)):
            b = QPushButton(text)
            b.setFont(QFont(BASE_FONT, 10))
            b.setFixedHeight(30)
            b.clicked.connect(fn)
            ops.addWidget(b)
        ops.addStretch(1)
        root.addLayout(ops)

        add_row = QHBoxLayout()
        self.entry = QLineEdit()
        self.entry.setFont(QFont(BASE_FONT, 12))
        self.entry.setPlaceholderText("输入新待办，回车添加…")
        self.entry.returnPressed.connect(self.add_todo)
        add_row.addWidget(self.entry, 1)

        ab = QPushButton("添加")
        ab.setFont(QFont(BASE_FONT, 10))
        ab.setFixedHeight(30)
        ab.setStyleSheet("QPushButton{background:#1976D2;color:#fff;"
                         "border:none;border-radius:5px;padding:0 16px;}")
        ab.clicked.connect(self.add_todo)
        add_row.addWidget(ab)
        root.addLayout(add_row)

    def _rows(self):
        return self.db.get_todos()

    def refresh_list(self):
        self.list.clear()
        for r in self._rows():
            mark = "☑" if r["done"] else "☐"
            pri = {1: "  ⚑", 2: "  ⚑⚑"}.get(r["priority"], "")
            self.list.addItem(QListWidgetItem(f"{mark}  {r['text']}{pri}"))

    def _notify(self):
        if self.on_changed:
            self.on_changed()

    def toggle_selected(self):
        row = self.list.currentRow()
        rows = self._rows()
        if 0 <= row < len(rows):
            self.db.toggle_todo(rows[row]["id"])
            self.refresh_list()
            self._notify()

    def delete_selected(self):
        row = self.list.currentRow()
        rows = self._rows()
        if 0 <= row < len(rows):
            self.db.delete_todo(rows[row]["id"])
            self.refresh_list()
            self._notify()

    def add_todo(self):
        text = self.entry.text().strip()
        if text:
            self.db.add_todo(text)
            self.entry.clear()
            self.refresh_list()
            self._notify()

    def closeEvent(self, event):
        self._closed = True
        event.accept()
