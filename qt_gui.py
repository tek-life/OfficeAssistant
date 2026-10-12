"""
办公助手 Qt GUI 模块。

为什么用 Qt：Windows 上 tkinter 的 TSF 输入法支持不完整，内联拼音组词串
字号不跟随正文字体（偏小）。Qt 的 QTextEdit/QLineEdit 有完整 TSF 支持，
拼音串自动与正文等大。

架构：QApplication 运行在独立线程（QtGuiManager），所有窗口的创建与操作
都通过请求队列投递到该线程执行，满足 Qt "控件只能在创建它的线程操作" 的约束。
"""

import html as _html
import json
import math
import queue
import re
import sqlite3
import threading
from datetime import datetime

import shiboken6
from PySide6.QtCore import (
    QDate, QLocale, QObject, QPointF, QRectF, QSignalBlocker, QTimer, Qt, QUrl, Signal,
)
from PySide6.QtGui import QColor, QFont, QPainter, QPalette, QPen, QTextCharFormat, QTextCursor
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QStackedWidget, QTextEdit, QPlainTextEdit, QListWidget,
    QListWidgetItem, QLineEdit, QMessageBox, QCheckBox, QTabBar, QSizePolicy,
    QCalendarWidget, QComboBox, QSpinBox,
)

BASE_FONT = "Microsoft YaHei UI"
BASE_SIZE = 13  # pt
COLORS = {
    "surface": "#ffffff", "sidebar": "#fafafa", "text": "#242424",
    "secondary": "#707070", "border": "#e5e5e5", "hover": "#f2f2f2",
    "selected": "#ededed", "disabled": "#a0a0a0",
}
WINDOW_STYLE = """
QWidget#todoWindow, QWidget#diaryWindow { background: %(surface)s; }
QLabel { background: transparent; }
QLabel[tone="secondary"] { color: %(secondary)s; }
QPushButton { background: transparent; color: %(text)s; border: 1px solid transparent;
    border-radius: 7px; padding: 6px 12px; }
QPushButton:hover { background: %(hover)s; }
QPushButton:pressed { background: %(selected)s; }
QPushButton:focus { border-color: %(secondary)s; }
QPushButton:disabled { color: %(disabled)s; }
QPushButton[variant="primary"] { background: %(text)s; color: %(surface)s; }
QPushButton[variant="primary"]:hover { background: #3b3b3b; }
QPushButton[variant="primary"]:focus { border: 2px solid %(secondary)s; }
QPushButton[variant="quiet"] { color: %(secondary)s; }
QWidget#diaryHeader { border-bottom: 1px solid %(border)s; }
QWidget#calendarPanel { background: %(sidebar)s; border-right: 1px solid %(border)s; }
QWidget#diaryContent { background: %(surface)s; }
QWidget#diaryFooter { border-top: 1px solid %(border)s; }
QSpinBox, QComboBox { color: %(text)s; background: %(sidebar)s;
    border: 1px solid transparent; border-radius: 5px; padding: 4px; }
QSpinBox:hover, QComboBox:hover, QSpinBox:focus, QComboBox:focus { border-color: %(border)s; }
QComboBox QAbstractItemView { background: %(surface)s; color: %(text)s;
    selection-background-color: %(selected)s; selection-color: %(text)s; }
QTabBar::tab { color: %(secondary)s; background: transparent;
    padding: 10px 4px; margin-right: 24px; border-bottom: 2px solid transparent; }
QTabBar::tab:selected { color: %(text)s; border-bottom-color: %(text)s; }
QTabBar::tab:hover { color: %(text)s; }
QWidget#todoNavigation { border-bottom: 1px solid %(border)s; }
QListWidget { background: transparent; color: %(text)s; border: 0; outline: 0; }
QListWidget::item { border: 0; border-radius: 6px; }
QListWidget::item:hover { background: %(sidebar)s; }
QListWidget::item:selected { background: %(selected)s; color: %(text)s; }
QListWidget::item:focus { border: 1px solid %(secondary)s; }
QWidget#todoRow { background: transparent; border-bottom: 1px solid %(border)s; }
QWidget#todoInput { background: %(sidebar)s; border: 1px solid %(border)s; border-radius: 10px; }
QLineEdit#todoEntry { color: %(text)s; background: transparent; border: 1px solid transparent;
    border-radius: 5px; padding: 5px; selection-background-color: %(selected)s;
    selection-color: %(text)s; }
QLineEdit#todoEntry:focus { border-color: %(secondary)s; }
QTextEdit, QPlainTextEdit { color: %(text)s; background: %(surface)s; border: 0;
    selection-background-color: %(selected)s; selection-color: %(text)s; }
QPlainTextEdit:focus { border-bottom: 1px solid %(border)s; }
QWidget#formatToolbar { border-bottom: 1px solid %(border)s; }
QWidget#formatToolbar QPushButton { padding: 0; }
QListWidget#recentNotes::item { padding: 8px; }
QScrollBar:vertical { background: transparent; width: 8px; margin: 0; }
QScrollBar::handle:vertical { background: #cecece; min-height: 28px; border-radius: 4px; }
QScrollBar::handle:vertical:hover { background: #b3b3b3; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
""" % COLORS


def _apply_window_style(window):
    palette = window.palette()
    for role in (QPalette.Window, QPalette.Base, QPalette.Button):
        palette.setColor(role, QColor(COLORS["surface"]))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        palette.setColor(role, QColor(COLORS["text"]))
        palette.setColor(QPalette.Disabled, role, QColor(COLORS["disabled"]))
    palette.setColor(QPalette.Highlight, QColor(COLORS["selected"]))
    palette.setColor(QPalette.HighlightedText, QColor(COLORS["text"]))
    palette.setColor(QPalette.PlaceholderText, QColor(COLORS["secondary"]))
    window.setPalette(palette)
    window.setFont(QFont(BASE_FONT, 10))
    window.setStyleSheet(WINDOW_STYLE)


class TodoCheckBox(QCheckBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(24, 24)

    def hitButton(self, pos):
        return self.rect().contains(pos)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        color = COLORS["text"] if self.isEnabled() else COLORS["secondary"]
        painter.setBrush(QColor(color) if self.isChecked() else Qt.NoBrush)
        painter.setPen(QPen(QColor(color if self.isChecked() else "#a0a0a0"), 1))
        painter.drawRoundedRect(QRectF(3, 3, 18, 18), 4, 4)
        if self.isChecked():
            painter.setPen(QPen(QColor(COLORS["surface"]), 1.8, Qt.SolidLine,
                                Qt.RoundCap, Qt.RoundJoin))
            painter.drawLine(QPointF(7, 12), QPointF(10, 15))
            painter.drawLine(QPointF(10, 15), QPointF(17, 8))
        if self.hasFocus():
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(QColor(COLORS["secondary"]), 1, Qt.DotLine))
            painter.drawRoundedRect(QRectF(0.5, 0.5, 23, 23), 5, 5)


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
            f'<code style="background:{COLORS["hover"]};padding:1px 4px;">{match.group(1)}</code>')
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
                out.append(f'<pre style="background:{COLORS["sidebar"]};padding:12px;'
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
        elif line.startswith("> "):
            out.append(f'<blockquote style="color:{COLORS["secondary"]};">'
                       f'{_inline(esc[5:])}</blockquote>')
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
    return ("<html><head><style>"
            "h1 { font-size:22pt; margin-top:12px; margin-bottom:16px; }"
            "h2 { font-size:16pt; margin-top:20px; margin-bottom:12px; }"
            "h3 { font-size:14pt; margin-top:16px; margin-bottom:10px; }"
            "p { line-height:160%; }"
            "blockquote { margin:16px 0 16px 16px; }"
            "</style></head>"
            f"<body style='font-family:{BASE_FONT};font-size:{BASE_SIZE}pt;"
            f"color:{COLORS['text']};'>{body}</body></html>")


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
        self.setFont(QFont(BASE_FONT, 9))
        self.setMinimumWidth(280)
        self.setFixedHeight(238)
        self.setAccessibleName("日志日历")
        self.setStyleSheet(
            f"QCalendarWidget QAbstractItemView{{background:{COLORS['sidebar']};"
            f"color:{COLORS['secondary']};border:0;outline:0;"
            "selection-background-color:transparent;}"
        )
        header_format = QTextCharFormat()
        header_format.setForeground(QColor(COLORS["secondary"]))
        header_format.setBackground(QColor(COLORS["sidebar"]))
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
        painter.fillRect(rect, QColor(COLORS["sidebar"]))
        selected = date == self.selectedDate()
        today = date == QDate.currentDate()
        in_month = date.month() == self.monthShown() and date.year() == self.yearShown()
        bounds = QRectF(rect).adjusted(3, 3, -3, -3)
        painter.setBrush(QColor(COLORS["text"]) if selected else Qt.NoBrush)
        painter.setPen(QPen(QColor(COLORS["secondary"]), 1) if today else Qt.NoPen)
        if selected or today:
            painter.drawRoundedRect(bounds, 6, 6)
        color = COLORS["surface"] if selected else COLORS["text" if in_month else "disabled"]
        painter.setPen(QColor(color))
        font = self.font()
        font.setBold(selected)
        painter.setFont(font)
        painter.drawText(rect.adjusted(0, 0, 0, -3), Qt.AlignCenter, str(date.day()))
        if date in self.recorded_dates:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(color))
            painter.drawEllipse(QPointF(rect.center().x(), rect.bottom() - 6), 1.5, 1.5)
        painter.restore()


class ShanghaiWeather(QObject):
    ready = Signal(str, str)
    failed = Signal(str)
    TIMEOUT_MS = 10000
    CONDITIONS = {
        0: "晴", 1: "晴间多云", 2: "多云", 3: "阴", 45: "雾", 48: "雾凇",
        51: "小毛毛雨", 53: "毛毛雨", 55: "强毛毛雨", 56: "冻毛毛雨", 57: "强冻毛毛雨",
        61: "小雨", 63: "中雨", 65: "大雨", 66: "冻雨", 67: "强冻雨",
        71: "小雪", 73: "中雪", 75: "大雪", 77: "米雪",
        80: "小阵雨", 81: "阵雨", 82: "强阵雨", 85: "小阵雪", 86: "强阵雪",
        95: "雷阵雨", 96: "雷阵雨伴冰雹", 99: "雷阵雨伴冰雹",
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self.manager = QNetworkAccessManager(self)
        self._pending = {}

    def fetch(self, date):
        if date in self._pending:
            return
        request = QNetworkRequest(QUrl(
            "https://api.open-meteo.com/v1/forecast?latitude=31.2304&longitude=121.4737"
            "&current=temperature_2m,weather_code&timezone=Asia%2FShanghai"
        ))
        reply = self.manager.get(request)
        self._pending[date] = reply
        timeout = QTimer(reply)
        timeout.setSingleShot(True)
        timeout.timeout.connect(reply.abort)
        reply.finished.connect(timeout.stop)
        reply.finished.connect(lambda: self._finish(date, reply))
        timeout.start(self.TIMEOUT_MS)

    def _finish(self, date, reply):
        self._pending.pop(date, None)
        try:
            if reply.error() != QNetworkReply.NoError:
                self.failed.emit(date)
                return
            try:
                data = json.loads(bytes(reply.readAll()))
                current = data["current"]
                temperature = current["temperature_2m"]
                code = current["weather_code"]
                if (data["timezone"] != "Asia/Shanghai"
                        or data["current_units"]["temperature_2m"] != "°C"
                        or datetime.fromisoformat(current["time"]).date().isoformat() != date
                        or type(temperature) not in (int, float) or not math.isfinite(temperature)
                        or type(code) is not int):
                    raise ValueError("天气数据不符合请求日期或单位")
                weather = f"上海 · {self.CONDITIONS[code]} · {temperature:g}°C"
            except (KeyError, TypeError, ValueError, OverflowError):
                self.failed.emit(date)
                return
            self.ready.emit(date, weather)
        finally:
            reply.deleteLater()


class DiaryWindow(QWidget):
    """工作日志：默认阅读模式（Markdown 渲染），点"编辑"进入编辑模式。"""

    def __init__(self, db, on_saved=None):
        super().__init__()
        self.db = db
        self.on_saved = on_saved
        self.current_date = datetime.now().strftime("%Y-%m-%d")
        self._saved_content = ""
        self._weather = ""
        self._saved_weather = ""
        self._weather_cache = {}
        self.weather_client = ShanghaiWeather(self)
        self.weather_client.ready.connect(self._weather_ready)
        self.weather_client.failed.connect(self._weather_failed)
        self.mode = "read"
        self._closed = False
        self._build_ui()
        self.enter_read_mode()
        self.open_history()

    def _build_ui(self):
        self.setWindowTitle(f"工作日志 — {self.current_date}")
        self.setObjectName("diaryWindow")
        self.resize(780, 740)
        self.setMinimumSize(740, 560)
        self.setAttribute(Qt.WA_DeleteOnClose, False)
        _apply_window_style(self)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        title_bar = QWidget()
        title_bar.setObjectName("diaryHeader")
        title_row = QHBoxLayout(title_bar)
        title_row.setContentsMargins(28, 16, 24, 16)
        title = QLabel("工作日志")
        title.setFont(QFont(BASE_FONT, 13, QFont.DemiBold))
        title_row.addWidget(title)
        title_row.addStretch(1)
        self.history_button = QPushButton("打开日历")
        self.history_button.setProperty("variant", "quiet")
        self.history_button.setCheckable(True)
        self.history_button.setFixedHeight(34)
        self.history_button.clicked.connect(self.open_history)
        title_row.addWidget(self.history_button)
        outer.addWidget(title_bar)

        panels = QHBoxLayout()
        panels.setSpacing(0)
        self._build_calendar_panel()
        panels.addWidget(self.calendar_panel)
        self.calendar_panel.hide()
        self.content_panel = QWidget()
        self.content_panel.setObjectName("diaryContent")
        panels.addWidget(self.content_panel, 1)
        outer.addLayout(panels, 1)

        root = QVBoxLayout(self.content_panel)
        root.setContentsMargins(32, 28, 32, 24)
        root.setSpacing(12)
        header = QHBoxLayout()
        self.date_label = QLabel("")
        self.date_label.setFont(QFont(BASE_FONT, 20, QFont.DemiBold))
        header.addWidget(self.date_label)
        header.addStretch(1)
        self.status_label = QLabel("")
        self.status_label.setFont(QFont(BASE_FONT, 9))
        self.status_label.setProperty("tone", "secondary")
        header.addWidget(self.status_label)
        root.addLayout(header)
        meta_row = QHBoxLayout()
        self.date_meta = QLabel("")
        self.date_meta.setFont(QFont(BASE_FONT, 9))
        self.date_meta.setProperty("tone", "secondary")
        meta_row.addWidget(self.date_meta)
        meta_row.addStretch(1)
        self.weather_label = QLabel("")
        self.weather_label.setFont(QFont(BASE_FONT, 9))
        self.weather_label.setProperty("tone", "secondary")
        self.weather_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.weather_label.setTextFormat(Qt.PlainText)
        self.weather_label.setAccessibleName("上海天气")
        self.weather_label.setToolTip(
            "数据来源：Open-Meteo（上海市区参考天气）\n"
            "随日志保存，不随回看更新；获取失败时，下次编辑重试。")
        meta_row.addWidget(self.weather_label)
        root.addLayout(meta_row)
        root.addSpacing(12)

        self.toolbar = QWidget()
        self.toolbar.setObjectName("formatToolbar")
        tb = QHBoxLayout(self.toolbar)
        tb.setContentsMargins(0, 0, 0, 8)
        tb.setSpacing(4)

        def tbtn(text, fn, bold=False, italic=False, strike=False, underline=False):
            b = QPushButton(text)
            font = QFont(BASE_FONT, 10, QFont.Bold if bold else QFont.Normal, italic)
            font.setStrikeOut(strike)
            font.setUnderline(underline)
            b.setFont(font)
            b.setFixedHeight(30)
            b.setFixedWidth(b.fontMetrics().horizontalAdvance(text) + 18)
            b.setAccessibleName(text)
            b.clicked.connect(fn)
            tb.addWidget(b)
            return b

        tbtn("B", lambda: self._wrap("**"), bold=True).setToolTip("加粗")
        tbtn("I", lambda: self._wrap("*"), italic=True).setToolTip("斜体")
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
        self.preview.document().setDocumentMargin(2)
        self.preview.setAccessibleName("日志正文")
        # 双击进入编辑模式：QTextEdit 的鼠标事件走内部 viewport，需装在 viewport 上
        self.preview.viewport().installEventFilter(self)
        self.stack.addWidget(self.preview)   # index 0 = 阅读

        self.editor = QPlainTextEdit()
        self.editor.setFont(QFont(BASE_FONT, BASE_SIZE))
        self.editor.setAccessibleName("编辑日志正文")
        self.editor.setPlaceholderText("记录这一天的工作，支持 Markdown 格式…")
        self.editor.textChanged.connect(self._update_save_status)
        self.stack.addWidget(self.editor)    # index 1 = 编辑

        self.empty_state = QWidget()
        empty_layout = QVBoxLayout(self.empty_state)
        empty_layout.setSpacing(12)
        empty_layout.addStretch(1)
        empty_title = QLabel("这一天，还没有记录")
        empty_title.setFont(QFont(BASE_FONT, 16))
        empty_layout.addWidget(empty_title, 0, Qt.AlignCenter)
        empty_hint = QLabel("从一件小事开始，留住今天的进展。")
        empty_hint.setProperty("tone", "secondary")
        empty_layout.addWidget(empty_hint, 0, Qt.AlignCenter)
        self.write_button = QPushButton("写日志")
        self.write_button.setProperty("variant", "primary")
        self.write_button.setFixedSize(112, 38)
        self.write_button.clicked.connect(self.enter_edit_mode)
        empty_layout.addWidget(self.write_button, 0, Qt.AlignCenter)
        empty_layout.addStretch(1)
        self.stack.addWidget(self.empty_state)
        root.addWidget(self.stack, 1)

        footer = QWidget()
        footer.setObjectName("diaryFooter")
        btns = QHBoxLayout(footer)
        btns.setContentsMargins(0, 16, 0, 0)
        btns.setSpacing(8)
        self.document_hint = QLabel("")
        self.document_hint.setObjectName("documentHint")
        self.document_hint.setProperty("tone", "secondary")
        self.document_hint.setFont(QFont(BASE_FONT, 9))
        btns.addWidget(self.document_hint)
        btns.addStretch(1)

        def button(text, fn):
            b = QPushButton(text)
            b.setFont(QFont(BASE_FONT, 10))
            b.setFixedHeight(38)
            b.clicked.connect(fn)
            btns.addWidget(b)
            return b

        self.save_button = button("保存", self.save)
        self.save_button.setProperty("variant", "quiet")
        self.save_close_button = button("保存并关闭", self.save_and_close)
        self.save_close_button.setProperty("variant", "quiet")
        self.toggle_btn = button("编辑日志", self.toggle_mode)
        self.toggle_btn.setProperty("variant", "primary")
        root.addWidget(footer)

    def _build_calendar_panel(self):
        self.calendar_panel = QWidget()
        self.calendar_panel.setObjectName("calendarPanel")
        self.calendar_panel.setFixedWidth(316)
        layout = QVBoxLayout(self.calendar_panel)
        layout.setContentsMargins(18, 20, 18, 20)
        layout.setSpacing(10)
        title_row = QHBoxLayout()
        title = QLabel("日志日历")
        title.setFont(QFont(BASE_FONT, 10, QFont.DemiBold))
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
        self.year_select.setFixedWidth(100)
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
        legend = QLabel("· 有日志    描边为今天")
        legend.setProperty("tone", "secondary")
        legend.setFont(QFont(BASE_FONT, 9))
        layout.addWidget(legend)
        self.month_summary = QLabel("")
        self.month_summary.setProperty("tone", "secondary")
        self.month_summary.setFont(QFont(BASE_FONT, 9))
        self.month_summary.setContentsMargins(0, 12, 0, 0)
        layout.addWidget(self.month_summary)
        self.recent_notes = QListWidget()
        self.recent_notes.setObjectName("recentNotes")
        self.recent_notes.setAccessibleName("本月日志记录")
        self.recent_notes.setFont(QFont(BASE_FONT, 10))
        self.recent_notes.setFixedHeight(112)
        self.recent_notes.itemClicked.connect(self._open_recent_note)
        self.recent_notes.itemActivated.connect(self._open_recent_note)
        layout.addWidget(self.recent_notes)
        layout.addStretch(1)
        hint = QLabel("点选日期，回看或补写日志。\n切换日期时，自动保存修改。")
        hint.setProperty("tone", "secondary")
        hint.setFont(QFont(BASE_FONT, 9))
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
        self.save_button.hide()
        self.save_close_button.hide()
        self.toggle_btn.setText("编辑日志" if content.strip() else "写日志")
        self.toggle_btn.setProperty("variant", "primary" if content.strip() else "quiet")
        self.toggle_btn.style().unpolish(self.toggle_btn)
        self.toggle_btn.style().polish(self.toggle_btn)
        count = len("".join(content.split()))
        self.document_hint.setText(f"{count} 字 · 双击正文编辑" if content.strip()
                                   else "每一天，都值得被记住。")
        self.mode = "read"

    def enter_edit_mode(self):
        self.stack.setCurrentIndex(1)
        self.toolbar.show()
        self.save_button.show()
        self.save_close_button.show()
        self.toggle_btn.setText("保存并返回")
        self.toggle_btn.setProperty("variant", "primary")
        self.toggle_btn.style().unpolish(self.toggle_btn)
        self.toggle_btn.style().polish(self.toggle_btn)
        self.document_hint.setText("支持 Markdown")
        self.mode = "edit"
        self.editor.setFocus()
        if not self._weather and self.current_date == QDate.currentDate().toString("yyyy-MM-dd"):
            self.weather_label.setText("上海 · 获取天气中…")
            self.weather_client.fetch(self.current_date)

    def _weather_ready(self, date, weather):
        saved_weather = ""
        try:
            # 请求返回时正文可能已保存或已切换日期，只补充天气，不能覆盖正文。
            saved_weather = self.db.save_daily_note_weather(date, weather)
        except sqlite3.Error as exc:
            print(f"[天气] 天气暂未保存: {exc}")
        weather = saved_weather or weather
        self._weather_cache[date] = weather
        if date == self.current_date:
            self._weather = weather
            if saved_weather:
                self._saved_weather = saved_weather
            self.weather_label.setText(weather)
            self._update_save_status()

    def _weather_failed(self, date):
        if date == self.current_date and not self._weather:
            self.weather_label.setText("上海 · 天气暂不可用")

    def toggle_mode(self):
        if self.mode == "read":
            self.enter_edit_mode()
        elif self.save():
            self.enter_read_mode()

    def _update_save_status(self):
        content = self.editor.toPlainText()
        if (content != self._saved_content
                or (content.strip() and self._weather != self._saved_weather)):
            self.status_label.setText("未保存")
        else:
            self.status_label.setText("已保存" if content.strip() else "暂无记录")

    def save(self):
        content = self.editor.toPlainText()
        if (content == self._saved_content
                and (not content.strip() or self._weather == self._saved_weather)):
            return True
        try:
            self.db.save_daily_note(self.current_date, content, self._weather)
        except sqlite3.Error as exc:
            QMessageBox.warning(self, "日志未保存", f"保存失败，内容仍保留在编辑器中。\n{exc}")
            return False
        self._saved_content = content
        if content.strip():
            self._saved_weather = self._weather
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
        self._saved_weather = note.get("weather", "")
        self._weather = self._saved_weather or self._weather_cache.get(d, "")
        self.weather_label.setText(self._weather)
        self.editor.setPlainText(self._saved_content)
        date = QDate.fromString(d, "yyyy-MM-dd")
        self.date_label.setText(date.toString("M 月 d 日"))
        weekday = QLocale(QLocale.Chinese, QLocale.China).toString(date, "dddd")
        self.date_meta.setText(f"{weekday} · {date.year()} 年")
        self.setWindowTitle(f"工作日志 — {d}")
        self._update_save_status()
        self._sync_calendar_selection()
        self.enter_read_mode()
        return True

    def open_history(self):
        show = self.calendar_panel.isHidden()
        self.calendar_panel.setVisible(show)
        self.history_button.setChecked(show)
        self.history_button.setText("收起日历" if show else "打开日历")
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
        dates = sorted((date for date in self.calendar.recorded_dates
                        if date.year() == year and date.month() == month), reverse=True)
        self.month_summary.setText(f"本月已记录 {len(dates)} 天")
        scroll_position = self.recent_notes.verticalScrollBar().value()
        with QSignalBlocker(self.recent_notes):
            self.recent_notes.clear()
            for date in dates:
                weekday = QLocale(QLocale.Chinese, QLocale.China).toString(date, "ddd")
                item = QListWidgetItem(f"{date.toString('M 月 d 日')}  ·  {weekday}")
                item.setData(Qt.UserRole, date.toString("yyyy-MM-dd"))
                self.recent_notes.addItem(item)
                if item.data(Qt.UserRole) == self.current_date:
                    self.recent_notes.setCurrentItem(item)
        self.recent_notes.verticalScrollBar().setValue(scroll_position)

    def _open_recent_note(self, item):
        date = item.data(Qt.UserRole)
        if date != self.current_date:
            self.load_date(date)

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
        self.setWindowTitle("待办清单")
        self.setObjectName("todoWindow")
        self.resize(680, 620)
        self.setMinimumSize(440, 420)
        _apply_window_style(self)

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 20)
        root.setSpacing(12)
        title_row = QHBoxLayout()
        title = QLabel("待办清单")
        title.setFont(QFont(BASE_FONT, 20, QFont.DemiBold))
        title_row.addWidget(title)
        title_row.addStretch(1)
        self.summary = QLabel("")
        self.summary.setObjectName("todoSummary")
        self.summary.setProperty("tone", "secondary")
        self.summary.setFont(QFont(BASE_FONT, 9))
        root.addLayout(title_row)
        root.addWidget(self.summary)
        root.addSpacing(4)

        navigation = QWidget()
        navigation.setObjectName("todoNavigation")
        nav = QHBoxLayout(navigation)
        nav.setContentsMargins(0, 0, 0, 0)
        self.tabs = QTabBar()
        self.tabs.setFont(QFont(BASE_FONT, 10))
        self.tabs.setExpanding(False)
        self.tabs.setDrawBase(False)
        self.tabs.addTab("待办")
        self.tabs.addTab("已归档")
        nav.addWidget(self.tabs)
        nav.addStretch(1)
        self.delete_button = QPushButton("删除")
        self.delete_button.setProperty("variant", "quiet")
        self.delete_button.setFixedHeight(32)
        self.delete_button.setToolTip("删除选中的事项")
        self.delete_button.setEnabled(False)
        self.delete_button.clicked.connect(self.delete_selected)
        nav.addWidget(self.delete_button)
        root.addWidget(navigation)

        self.content_stack = QStackedWidget()
        self.list = QListWidget()
        self.list.setFont(QFont(BASE_FONT, 12))
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        self.list.itemSelectionChanged.connect(
            lambda: self.delete_button.setEnabled(bool(self.list.selectedItems())))
        self.content_stack.addWidget(self.list)
        self.empty_state = QWidget()
        empty = QVBoxLayout(self.empty_state)
        empty.setSpacing(10)
        empty.addStretch(1)
        self.empty_title = QLabel("")
        self.empty_title.setObjectName("todoEmptyTitle")
        self.empty_title.setFont(QFont(BASE_FONT, 15))
        empty.addWidget(self.empty_title, 0, Qt.AlignCenter)
        self.empty_hint = QLabel("")
        self.empty_hint.setProperty("tone", "secondary")
        self.empty_hint.setWordWrap(True)
        self.empty_hint.setAlignment(Qt.AlignCenter)
        empty.addWidget(self.empty_hint)
        empty.addStretch(1)
        self.content_stack.addWidget(self.empty_state)
        root.addWidget(self.content_stack, 1)

        self.add_controls = QWidget()
        self.add_controls.setObjectName("todoInput")
        add_row = QHBoxLayout(self.add_controls)
        add_row.setContentsMargins(10, 8, 8, 8)
        add_row.setSpacing(8)
        self.entry = QLineEdit()
        self.entry.setObjectName("todoEntry")
        self.entry.setFont(QFont(BASE_FONT, 11))
        self.entry.setAccessibleName("新待办内容")
        self.entry.setPlaceholderText("添加一件要做的事…")
        self.entry.returnPressed.connect(self.add_todo)
        add_row.addWidget(self.entry, 1)

        ab = QPushButton("添加")
        ab.setObjectName("addTodo")
        ab.setProperty("variant", "primary")
        ab.setFixedHeight(36)
        ab.clicked.connect(self.add_todo)
        add_row.addWidget(ab)
        root.addWidget(self.add_controls)
        self.input_hint = QLabel("")
        self.input_hint.setProperty("tone", "secondary")
        self.input_hint.setFont(QFont(BASE_FONT, 9))
        self.input_hint.setWordWrap(True)
        root.addWidget(self.input_hint)
        self.tabs.currentChanged.connect(self.refresh_list)

    def _rows(self):
        return self.db.get_todos(archived=self.tabs.currentIndex() == 1)

    def refresh_list(self):
        selected = self.list.selectedItems()
        selected_id = selected[0].data(Qt.UserRole) if selected else None
        focused = self.focusWidget()
        focused_id = focused.property("todoId") if isinstance(focused, QCheckBox) else None
        scroll_position = self.list.verticalScrollBar().value()
        archived = self.tabs.currentIndex() == 1
        self.add_controls.setVisible(not archived)
        rows = self._rows()
        completed = sum(bool(row["done"]) for row in rows)
        self.summary.setText(f"已归档 {len(rows)} 件 · 可随时恢复" if archived else
                             f"{len(rows) - completed} 件待完成 · {completed} 件已完成")
        self.empty_title.setText("还没有归档事项" if archived else "清单很干净")
        self.empty_hint.setText("已完成的事项归档后，会留在这里。" if archived else
                                "在下方记下下一件要做的事。")
        self.input_hint.setText("已归档事项会保留，可随时恢复到待办列表。" if archived else
                                "回车添加 · 完成的事项可以归档，随时回看")
        self.list.clear()
        self.content_stack.setCurrentWidget(self.list if rows else self.empty_state)
        for r in rows:
            item = QListWidgetItem()
            item.setData(Qt.UserRole, r["id"])
            item.setData(Qt.AccessibleTextRole, r["text"])
            self.list.addItem(item)

            row = QWidget()
            row.setObjectName("todoRow")
            row.setFont(self.list.font())
            layout = QHBoxLayout(row)
            layout.setContentsMargins(6, 14, 6, 14)
            layout.setSpacing(10)

            checkbox = TodoCheckBox()
            checkbox.setProperty("todoId", r["id"])
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
            if r["done"]:
                label.setProperty("tone", "secondary")
            text_layout.addWidget(label)

            created_label = QLabel(f"创建于 {r['created_at'][:16]}")
            created_label.setFont(QFont(BASE_FONT, 9))
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
                button.setProperty("variant", "quiet")
                button.setFont(QFont(BASE_FONT, 10))
                button.setFixedHeight(32)
                button.clicked.connect(
                    lambda checked=False, tid=r["id"], fn=action: fn(tid)
                )
                layout.addWidget(button)

            item.setSizeHint(row.sizeHint())
            self.list.setItemWidget(item, row)
            if r["id"] == selected_id:
                self.list.setCurrentItem(item)
            if r["id"] == focused_id:
                checkbox.setFocus(Qt.OtherFocusReason)
        self.list.verticalScrollBar().setValue(scroll_position)
        self.delete_button.setEnabled(bool(self.list.selectedItems()))

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
        selected = self.list.selectedItems()
        if selected:
            self.db.delete_todo(selected[0].data(Qt.UserRole))
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
