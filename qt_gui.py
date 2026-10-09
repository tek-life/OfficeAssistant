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
import sqlite3
import threading
from datetime import datetime

import shiboken6
from PySide6.QtCore import QDate, QLocale, QPointF, QRectF, QSignalBlocker, QTimer, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPalette, QPen, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QStackedWidget, QTextEdit, QPlainTextEdit, QListWidget,
    QListWidgetItem, QLineEdit, QMessageBox, QCheckBox, QTabBar, QSizePolicy,
    QCalendarWidget, QComboBox, QSpinBox,
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
    code_spans = []

    def protect_code(match):
        code_spans.append(
            f'<code style="background:#eef2f5;padding:1px 4px;">{match.group(1)}</code>')
        return f'<code-placeholder-{len(code_spans) - 1}/>'

    # 先保护代码片段；输入已转义，不会与原始 HTML 占位标签冲突。
    s = re.sub(r'`([^`]+)`', protect_code, s)
    s = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', s)
    s = re.sub(r'(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)', r'<i>\1</i>', s)
    s = re.sub(r'~~(.+?)~~', r'<s>\1</s>', s)
    s = re.sub(r'&lt;u&gt;(.+?)&lt;/u&gt;', r'<u>\1</u>', s)
    s = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'<u>\1</u>', s)
    return re.sub(r'<code-placeholder-(\d+)/>',
                  lambda m: code_spans[int(m.group(1))], s)


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

class DiaryCalendar(QCalendarWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.recorded_dates = set()
        self.setLocale(QLocale(QLocale.Chinese, QLocale.China))
        self.setFirstDayOfWeek(Qt.Monday)
        self.setNavigationBarVisible(False)
        self.setVerticalHeaderFormat(QCalendarWidget.NoVerticalHeader)
        self.setHorizontalHeaderFormat(QCalendarWidget.ShortDayNames)
        self.setDateRange(QDate(1900, 1, 1), QDate(9999, 12, 31))
        self.setFont(QFont(BASE_FONT, 10))
        self.setMinimumWidth(280)
        self.setFixedHeight(294)
        self.setAccessibleName("日志日历")
        self.setStyleSheet(
            "QCalendarWidget QAbstractItemView{background:#fff;color:#758197;"
            "border:0;outline:0;selection-background-color:transparent;}"
        )
        header_format = QTextCharFormat()
        header_format.setForeground(QColor("#758197"))
        header_format.setBackground(QColor("#fff"))
        self.setHeaderTextFormat(header_format)
        self.setWeekdayTextFormat(Qt.Saturday, header_format)
        self.setWeekdayTextFormat(Qt.Sunday, header_format)

    def set_recorded_dates(self, dates):
        for date in self.recorded_dates - dates:
            self.setDateTextFormat(date, QTextCharFormat())
        for date in dates - self.recorded_dates:
            fmt = QTextCharFormat()
            fmt.setToolTip(f"{date.toString('yyyy-MM-dd')} · 已记录日志")
            self.setDateTextFormat(date, fmt)
        self.recorded_dates = set(dates)
        self.updateCells()

    def paintCell(self, painter, rect, date):
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(rect, QColor("#fff"))
        selected = date == self.selectedDate()
        today = date == QDate.currentDate()
        in_month = date.month() == self.monthShown() and date.year() == self.yearShown()
        bounds = QRectF(rect).adjusted(3, 3, -3, -3)
        painter.setBrush(QColor("#eaf1ff") if selected else Qt.NoBrush)
        painter.setPen(QPen(QColor("#527cce"), 1.2) if today else Qt.NoPen)
        if selected or today:
            painter.drawRoundedRect(bounds, 7, 7)
        color = "#285db5" if selected else ("#34415a" if in_month else "#aab3c2")
        painter.setPen(QColor(color))
        font = self.font()
        font.setBold(selected or date in self.recorded_dates)
        painter.setFont(font)
        painter.drawText(rect.adjusted(0, 0, 0, -6), Qt.AlignCenter, str(date.day()))
        if date in self.recorded_dates:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor("#527cce" if in_month else "#aabbdc"))
            painter.drawEllipse(QPointF(rect.center().x(), rect.bottom() - 8), 2.5, 2.5)
        painter.restore()


class DiaryWindow(QWidget):
    """工作日志：默认阅读模式（Markdown 渲染），点"编辑"进入编辑模式。"""

    def __init__(self, db, on_saved=None):
        super().__init__()
        self.db = db
        self.on_saved = on_saved
        self.current_date = datetime.now().strftime("%Y-%m-%d")
        self._saved_content = ""
        self.mode = "read"
        self._closed = False
        self._build_ui()
        self.enter_read_mode()

    def _build_ui(self):
        self.setWindowTitle(f"工作日志 — {self.current_date}")
        self.setObjectName("diaryWindow")
        self.resize(780, 640)
        self.setMinimumSize(740, 560)
        self.setAttribute(Qt.WA_DeleteOnClose, False)
        self.setFont(QFont(BASE_FONT, 10))
        self.setStyleSheet(
            "QWidget#diaryWindow{background:#f4f6fa;}"
            "QWidget#diaryWindow QLabel{color:#34415a;background:transparent;}"
            "QWidget#calendarPanel,QWidget#diaryContent{background:#fff;"
            "border:1px solid #e4e9f1;border-radius:12px;}"
            "QWidget#diaryWindow QPushButton{background:#fff;color:#52617a;"
            "border:1px solid #dfe5ef;border-radius:6px;padding:5px 12px;}"
            "QWidget#diaryWindow QPushButton:hover{background:#f0f4fc;border-color:#bacceb;}"
            "QWidget#diaryWindow QPushButton:pressed{background:#e1eafa;}"
            "QWidget#diaryWindow QPushButton:focus{border-color:#527cce;}"
            "QWidget#diaryWindow QPushButton:checked{background:#eaf1ff;color:#285db5;}"
            "QWidget#diaryWindow QPushButton#primaryButton{background:#3569c8;"
            "color:#fff;border-color:#3569c8;}"
            "QWidget#diaryWindow QPushButton#primaryButton:hover{background:#285bb8;}"
            "QWidget#diaryWindow QSpinBox,QWidget#diaryWindow QComboBox{"
            "background:#f7f9fc;color:#34415a;border:1px solid #e4e9f1;"
            "border-radius:5px;padding:4px;}"
        )
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 18, 20, 20)
        outer.setSpacing(18)
        title_row = QHBoxLayout()
        title = QLabel("工作日志")
        title.setFont(QFont(BASE_FONT, 17, QFont.Bold))
        title_row.addWidget(title)
        title_row.addStretch(1)
        self.history_button = QPushButton("历史日志")
        self.history_button.setCheckable(True)
        self.history_button.setFixedHeight(34)
        self.history_button.clicked.connect(self.open_history)
        title_row.addWidget(self.history_button)
        outer.addLayout(title_row)

        panels = QHBoxLayout()
        panels.setSpacing(18)
        self._build_calendar_panel()
        panels.addWidget(self.calendar_panel)
        self.calendar_panel.hide()
        self.content_panel = QWidget()
        self.content_panel.setObjectName("diaryContent")
        panels.addWidget(self.content_panel, 1)
        outer.addLayout(panels, 1)

        root = QVBoxLayout(self.content_panel)
        root.setContentsMargins(24, 22, 24, 20)
        root.setSpacing(14)
        header = QHBoxLayout()
        self.date_label = QLabel(self.current_date)
        self.date_label.setFont(QFont(BASE_FONT, 18, QFont.Bold))
        header.addWidget(self.date_label)
        header.addStretch(1)
        self.status_label = QLabel("")
        self.status_label.setFont(QFont(BASE_FONT, 9))
        self.status_label.setStyleSheet("color:#7b879a;")
        header.addWidget(self.status_label)
        root.addLayout(header)
        self.date_meta = QLabel("")
        self.date_meta.setStyleSheet("color:#7b879a;")
        root.addWidget(self.date_meta)

        # ── 格式工具栏（仅编辑模式显示）──
        self.toolbar = QWidget()
        self.toolbar.setStyleSheet("QPushButton{padding:0;}")
        tb = QHBoxLayout(self.toolbar)
        tb.setContentsMargins(0, 0, 0, 0)
        tb.setSpacing(4)

        def tbtn(text, fn, bold=False, italic=False, strike=False, underline=False):
            b = QPushButton(text)
            font = QFont(BASE_FONT, 10, QFont.Bold if bold else QFont.Normal, italic)
            font.setStrikeOut(strike)
            font.setUnderline(underline)
            b.setFont(font)
            b.setFixedHeight(26)
            b.setFixedWidth(b.fontMetrics().horizontalAdvance(text) + 24)
            b.clicked.connect(fn)
            tb.addWidget(b)
            return b

        tbtn("B", lambda: self._wrap("**"), bold=True)
        tbtn("I", lambda: self._wrap("*"), italic=True)
        tbtn("S", lambda: self._wrap("~~"), strike=True).setToolTip("删除线（~~文字~~）")
        tbtn("U", lambda: self._wrap("<u>", "</u>"), underline=True).setToolTip(
            "下划线（<u>文字</u>）")
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
        self.preview.setStyleSheet("QTextEdit{background:#fff;border:0;}")
        self.preview.document().setDocumentMargin(2)
        # 双击进入编辑模式：QTextEdit 的鼠标事件走内部 viewport，需装在 viewport 上
        self.preview.viewport().installEventFilter(self)
        self.stack.addWidget(self.preview)   # index 0 = 阅读

        self.editor = QPlainTextEdit()
        self.editor.setFont(QFont(BASE_FONT, BASE_SIZE))
        self.editor.setStyleSheet(
            "QPlainTextEdit{background:#fbfcff;color:#34415a;border:1px solid #dfe5ef;"
            "border-radius:6px;padding:10px;}")
        self.editor.setPlaceholderText("记录这一天的工作，支持 Markdown 格式…")
        self.editor.textChanged.connect(self._update_save_status)
        self.stack.addWidget(self.editor)    # index 1 = 编辑

        self.empty_state = QWidget()
        empty_layout = QVBoxLayout(self.empty_state)
        empty_layout.setSpacing(12)
        empty_layout.addStretch(1)
        empty_title = QLabel("当天暂无日志")
        empty_title.setFont(QFont(BASE_FONT, 16))
        empty_layout.addWidget(empty_title, 0, Qt.AlignCenter)
        empty_hint = QLabel("从一条记录开始，留存这一天的工作。")
        empty_hint.setStyleSheet("color:#8590a2;")
        empty_layout.addWidget(empty_hint, 0, Qt.AlignCenter)
        self.write_button = QPushButton("写日志")
        self.write_button.setObjectName("primaryButton")
        self.write_button.setFixedSize(112, 36)
        self.write_button.clicked.connect(self.enter_edit_mode)
        empty_layout.addWidget(self.write_button, 0, Qt.AlignCenter)
        empty_layout.addStretch(1)
        self.stack.addWidget(self.empty_state)
        root.addWidget(self.stack, 1)

        btns = QHBoxLayout()

        def button(text, fn):
            b = QPushButton(text)
            b.setFont(QFont(BASE_FONT, 10))
            b.setFixedHeight(34)
            b.clicked.connect(fn)
            btns.addWidget(b)
            return b

        button("保存", self.save)
        button("保存并关闭", self.save_and_close)
        btns.addStretch(1)
        self.toggle_btn = button("编辑", self.toggle_mode)
        self.toggle_btn.setObjectName("primaryButton")
        root.addLayout(btns)

    def _build_calendar_panel(self):
        self.calendar_panel = QWidget()
        self.calendar_panel.setObjectName("calendarPanel")
        self.calendar_panel.setFixedWidth(332)
        layout = QVBoxLayout(self.calendar_panel)
        layout.setContentsMargins(18, 20, 18, 20)
        layout.setSpacing(14)
        title_row = QHBoxLayout()
        title = QLabel("历史日志")
        title.setFont(QFont(BASE_FONT, 12, QFont.Bold))
        title_row.addWidget(title)
        title_row.addStretch(1)
        self.today_button = QPushButton("今天")
        self.today_button.setToolTip("返回今天")
        self.today_button.clicked.connect(self._go_today)
        title_row.addWidget(self.today_button)
        layout.addLayout(title_row)

        navigation = QHBoxLayout()
        navigation.setSpacing(6)
        self.year_select = QSpinBox()
        self.year_select.setRange(1900, 9999)
        self.year_select.setSuffix(" 年")
        self.year_select.setKeyboardTracking(False)
        self.year_select.setAccessibleName("年份")
        self.year_select.setFixedWidth(106)
        navigation.addWidget(self.year_select)
        self.month_select = QComboBox()
        self.month_select.addItems([f"{month} 月" for month in range(1, 13)])
        self.month_select.setAccessibleName("月份")
        self.month_select.setFixedWidth(74)
        navigation.addWidget(self.month_select)
        navigation.addStretch(1)
        self.previous_month_button = QPushButton("‹")
        self.next_month_button = QPushButton("›")
        for button, label in ((self.previous_month_button, "上个月"),
                              (self.next_month_button, "下个月")):
            button.setFixedSize(32, 32)
            button.setStyleSheet("padding:0;font-size:20px;")
            button.setToolTip(label)
            button.setAccessibleName(label)
            navigation.addWidget(button)
        layout.addLayout(navigation)

        self.calendar = DiaryCalendar()
        layout.addWidget(self.calendar)
        legend = QLabel('<span style="color:#527cce;">●</span> 已记录日志'
                        '&nbsp;&nbsp;&nbsp; 描边为今天')
        legend.setStyleSheet("color:#7b879a;font-size:12px;")
        layout.addWidget(legend)
        self.month_summary = QLabel("")
        self.month_summary.setStyleSheet(
            "color:#64748b;background:#f5f7fb;border-radius:6px;padding:12px;")
        layout.addWidget(self.month_summary)
        layout.addStretch(1)
        hint = QLabel("单击日期查看或补写日志\n切换日期时自动保存修改")
        hint.setStyleSheet("color:#8a95a6;font-size:12px;")
        layout.addWidget(hint)

        self.previous_month_button.clicked.connect(self.calendar.showPreviousMonth)
        self.next_month_button.clicked.connect(self.calendar.showNextMonth)
        self.calendar.currentPageChanged.connect(self._calendar_page_changed)
        self.calendar.selectionChanged.connect(self._calendar_date_changed)
        self.year_select.valueChanged.connect(self._jump_month)
        self.month_select.currentIndexChanged.connect(self._jump_month)
        self._calendar_page_changed(self.calendar.yearShown(), self.calendar.monthShown())

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
        content = self.editor.toPlainText()
        self.preview.setHtml(md_to_html(content))
        self.stack.setCurrentWidget(self.preview if content.strip() else self.empty_state)
        self.toolbar.hide()
        self.toggle_btn.setText("编辑" if content.strip() else "写日志")
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
        elif self.save():
            self.enter_read_mode()

    def _update_save_status(self):
        content = self.editor.toPlainText()
        if content != self._saved_content:
            self.status_label.setText("未保存")
        else:
            self.status_label.setText("已保存" if content.strip() else "暂无记录")

    def save(self):
        content = self.editor.toPlainText()
        if content == self._saved_content:
            return True
        try:
            self.db.save_daily_note(self.current_date, content)
        except sqlite3.Error as exc:
            QMessageBox.warning(self, "日志未保存", f"保存失败，内容仍保留在编辑器中。\n{exc}")
            return False
        self._saved_content = content
        self._update_save_status()
        dates = set(self.calendar.recorded_dates)
        date = QDate.fromString(self.current_date, "yyyy-MM-dd")
        if content.strip():
            dates.add(date)
        else:
            dates.discard(date)
        self.calendar.set_recorded_dates(dates)
        self._calendar_page_changed(self.calendar.yearShown(), self.calendar.monthShown())
        if self.on_saved:
            self.on_saved()
        return True

    def save_and_close(self):
        if self.save():
            self.close()

    def load_date(self, d):
        if d != self.current_date and not self.save():
            self._sync_calendar_selection()
            return False
        try:
            note = self.db.get_daily_note(d)
            self._refresh_calendar_marks()
        except sqlite3.Error as exc:
            QMessageBox.warning(self, "无法打开日志", f"读取失败，请稍后重试。\n{exc}")
            self._sync_calendar_selection()
            return False
        self.current_date = d
        self._saved_content = note.get("content", "")
        self.editor.setPlainText(self._saved_content)
        date = QDate.fromString(d, "yyyy-MM-dd")
        self.date_label.setText(date.toString("yyyy年M月d日"))
        self.date_meta.setText(QLocale(QLocale.Chinese, QLocale.China).toString(date, "dddd"))
        self.setWindowTitle(f"工作日志 — {d}")
        self._update_save_status()
        self._sync_calendar_selection()
        self.enter_read_mode()
        return True

    def open_history(self):
        show = self.calendar_panel.isHidden()
        self.calendar_panel.setVisible(show)
        self.history_button.setChecked(show)
        self.history_button.setText("收起日历" if show else "历史日志")
        self.setMinimumSize(1090 if show else 740,
                            max(560, self.minimumSizeHint().height()) if show else 560)
        if show:
            self.resize(max(self.width(), 1140), self.height())
            self._sync_calendar_selection()
            try:
                self._refresh_calendar_marks()
            except sqlite3.Error as exc:
                QMessageBox.warning(self, "无法刷新日历", f"记录标记未更新，请稍后重试。\n{exc}")
        else:
            self.resize(max(780, self.width() - 350), self.height())

    def _sync_calendar_selection(self):
        date = QDate.fromString(self.current_date, "yyyy-MM-dd")
        with QSignalBlocker(self.calendar):
            self.calendar.setSelectedDate(date)
            self.calendar.setCurrentPage(date.year(), date.month())
        self._calendar_page_changed(date.year(), date.month())

    def _calendar_date_changed(self):
        date = self.calendar.selectedDate().toString("yyyy-MM-dd")
        if date != self.current_date:
            self.load_date(date)

    def _calendar_page_changed(self, year, month):
        with QSignalBlocker(self.year_select), QSignalBlocker(self.month_select):
            self.year_select.setValue(year)
            self.month_select.setCurrentIndex(month - 1)
        self.previous_month_button.setEnabled((year, month) > (1900, 1))
        self.next_month_button.setEnabled((year, month) < (9999, 12))
        count = sum(date.year() == year and date.month() == month
                    for date in self.calendar.recorded_dates)
        self.month_summary.setText(f"本月已记录 {count} 天")

    def _jump_month(self):
        self.calendar.setCurrentPage(self.year_select.value(),
                                     self.month_select.currentIndex() + 1)

    def _go_today(self):
        today = QDate.currentDate().toString("yyyy-MM-dd")
        if today == self.current_date:
            self._sync_calendar_selection()
        else:
            self.load_date(today)

    def _refresh_calendar_marks(self):
        dates = {QDate.fromString(date, "yyyy-MM-dd") for date in self.db.get_all_note_dates()}
        self.calendar.set_recorded_dates(dates)
        self._calendar_page_changed(self.calendar.yearShown(), self.calendar.monthShown())

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
    """待办事项编辑与归档管理。"""

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

        self.tabs = QTabBar()
        self.tabs.setFont(QFont(BASE_FONT, 11))
        self.tabs.addTab("待办")
        self.tabs.addTab("已归档")
        root.addWidget(self.tabs)

        self.list = QListWidget()
        self.list.setFont(QFont(BASE_FONT, 12))
        root.addWidget(self.list, 1)

        ops = QHBoxLayout()
        delete_button = QPushButton("删除")
        delete_button.setFont(QFont(BASE_FONT, 10))
        delete_button.setFixedHeight(30)
        delete_button.clicked.connect(self.delete_selected)
        ops.addWidget(delete_button)
        ops.addStretch(1)
        root.addLayout(ops)

        self.add_controls = QWidget()
        add_row = QHBoxLayout(self.add_controls)
        add_row.setContentsMargins(0, 0, 0, 0)
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
        root.addWidget(self.add_controls)
        self.tabs.currentChanged.connect(self.refresh_list)

    def _rows(self):
        return self.db.get_todos(archived=self.tabs.currentIndex() == 1)

    def refresh_list(self):
        current = self.list.currentItem()
        selected_id = current.data(Qt.UserRole) if current else None
        scroll_position = self.list.verticalScrollBar().value()
        archived = self.tabs.currentIndex() == 1
        self.add_controls.setVisible(not archived)
        self.list.clear()
        for r in self._rows():
            item = QListWidgetItem()
            item.setData(Qt.UserRole, r["id"])
            item.setData(Qt.AccessibleTextRole, r["text"])
            self.list.addItem(item)

            row = QWidget()
            row.setFont(self.list.font())
            layout = QHBoxLayout(row)
            layout.setContentsMargins(10, 10, 10, 10)
            layout.setSpacing(10)

            checkbox = QCheckBox()
            checkbox.setAccessibleName(r["text"])
            checkbox.setChecked(bool(r["done"]))
            checkbox.setEnabled(not archived)
            checkbox.clicked.connect(
                lambda checked=False, tid=r["id"]: self._toggle_todo(tid)
            )
            layout.addWidget(checkbox, 0, Qt.AlignTop)

            text_layout = QVBoxLayout()
            text_layout.setSpacing(4)
            pri = {1: "  ⚑", 2: "  ⚑⚑"}.get(r["priority"], "")
            label = QLabel(f"{r['text']}{pri}")
            label.setTextFormat(Qt.PlainText)
            row.setToolTip(f"<qt>{_html.escape(r['text'])}</qt>")
            label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            label.setAttribute(Qt.WA_TransparentForMouseEvents)
            font = QFont(self.list.font())
            font.setStrikeOut(bool(r["done"]))
            label.setFont(font)
            text_layout.addWidget(label)

            created_label = QLabel(f"创建于 {r['created_at'][:16]}")
            created_label.setFont(QFont(BASE_FONT, 10))
            created_label.setAttribute(Qt.WA_TransparentForMouseEvents)
            secondary_color = self.list.palette().color(QPalette.Text)
            secondary_color.setAlphaF(0.65)
            palette = created_label.palette()
            palette.setColor(QPalette.Text, secondary_color)
            created_label.setPalette(palette)
            text_layout.addWidget(created_label)
            layout.addLayout(text_layout, 1)

            if archived or r["done"]:
                action = self._restore_todo if archived else self._archive_todo
                button = QPushButton("恢复" if archived else "归档")
                button.setFont(QFont(BASE_FONT, 10))
                button.setFixedHeight(30)
                button.clicked.connect(
                    lambda checked=False, tid=r["id"], fn=action: fn(tid)
                )
                layout.addWidget(button)

            item.setSizeHint(row.sizeHint())
            self.list.setItemWidget(item, row)
            if r["id"] == selected_id:
                self.list.setCurrentItem(item)
        self.list.verticalScrollBar().setValue(scroll_position)

    def _notify(self):
        if self.on_changed:
            self.on_changed()

    def _toggle_todo(self, todo_id):
        self.db.toggle_todo(todo_id)
        self.refresh_list()
        self._notify()

    def _archive_todo(self, todo_id):
        self.db.archive_todo(todo_id)
        self.refresh_list()
        self._notify()

    def _restore_todo(self, todo_id):
        self.db.restore_todo(todo_id)
        self.refresh_list()
        self._notify()

    def delete_selected(self):
        item = self.list.currentItem()
        if item is not None:
            self.db.delete_todo(item.data(Qt.UserRole))
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
