"""
办公助手 Qt GUI 模块。

为什么用 Qt：Windows 上 tkinter 的 TSF 输入法支持不完整，内联拼音组词串
字号不跟随正文字体（偏小）。Qt 的 QTextEdit/QLineEdit 有完整 TSF 支持，
拼音串自动与正文等大。

架构：QApplication 运行在独立线程（QtGuiManager），所有窗口的创建与操作
都通过请求队列投递到该线程执行，满足 Qt "控件只能在创建它的线程操作" 的约束。
"""

import ctypes
import html as _html
import json
import math
import queue
import re
import sqlite3
import threading
from datetime import datetime

import shiboken6
import question_server
from window_chrome import ChromeWindow
from PySide6.QtCore import (
    QDate, QEvent, QLocale, QObject, QPointF, QRectF, QSize, QSignalBlocker, QTimer, Qt,
    QUrl, Signal,
)
from PySide6.QtGui import (QColor, QDesktopServices, QFont, QFontMetrics, QPainter,
                           QPalette, QPen, QTextCharFormat, QTextCursor)
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import (
    QApplication, QAbstractButton, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QStackedWidget, QTextEdit, QPlainTextEdit, QListWidget,
    QListWidgetItem, QLineEdit, QMessageBox, QCheckBox, QTabBar, QSizePolicy,
    QCalendarWidget, QComboBox, QMenu, QSpinBox, QGraphicsDropShadowEffect,
)

BASE_FONT = "Microsoft YaHei UI"
BASE_SIZE = 13  # pt
COLORS = {
    "surface": "#ffffff", "sidebar": "#fafafa", "text": "#242424",
    "secondary": "#707070", "border": "#e5e5e5", "hover": "#f2f2f2",
    "selected": "#ededed", "disabled": "#a0a0a0",
    "danger": "#d62424", "danger_pressed": "#b51f1f",
}
WINDOW_STYLE = """
QWidget#todoWindow, QWidget#diaryWindow, QWidget#questionWindow { background: %(surface)s; }
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
QWidget#titleBar { background: %(surface)s; border-bottom: 1px solid %(border)s; }
QLabel#titleBarLabel { color: %(secondary)s; font-size: 12px; }
QPushButton[caption="true"] { border: 0; border-radius: 0; padding: 0; }
QPushButton[caption="true"]:focus { border: 1px solid %(secondary)s; }
QPushButton#captionClose:hover { background: %(danger)s; color: %(surface)s; }
QPushButton#captionClose:pressed { background: %(danger_pressed)s; color: %(surface)s; }
QWidget#titleBar QPushButton#calendarToggle { border-radius: 0; padding: 0 14px;
    border: 0; border-right: 1px solid %(border)s; font-size: 12px; }
QWidget#titleBar QPushButton#calendarToggle:focus { border: 1px solid %(secondary)s; }
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
QWidget#questionRow { background: transparent; border-bottom: 1px solid %(border)s; }
QLabel#questionMeta { color: %(secondary)s; }
QLabel#questionChip { color: %(secondary)s; background: %(sidebar)s;
    border: 1px solid %(border)s; border-radius: 4px; padding: 1px 6px; font-size: 11px; }
QWidget#todoInput { background: %(sidebar)s; border: 1px solid %(border)s; border-radius: 10px; }
QLineEdit#todoEntry { color: %(text)s; background: transparent; border: 1px solid transparent;
    border-radius: 5px; padding: 5px; selection-background-color: %(selected)s;
    selection-color: %(text)s; }
QLineEdit#todoEntry:focus { border-color: %(secondary)s; }
QLineEdit#todoEdit { color: %(text)s; background: %(sidebar)s; border: 1px solid %(border)s;
    border-radius: 5px; padding: 2px 6px; selection-background-color: %(selected)s;
    selection-color: %(text)s; }
QLineEdit#todoEdit:focus { border-color: %(secondary)s; }
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


class TodoEditLine(QLineEdit):
    """待办行内编辑框：回车或失焦提交，Esc 取消。"""

    canceled = Signal()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.canceled.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class TodoRowWidget(QWidget):
    """待办行容器：双击文本区进入行内编辑。

    文本标签对鼠标透明，双击会落到本容器上；复选框和操作按钮各自处理事件，
    所以双击它们不会误触发编辑。子控件引用由列表刷新时挂载。
    """

    def __init__(self, todo_id, on_double_click=None, parent=None):
        super().__init__(parent)
        self.todo_id = todo_id
        self.on_double_click = on_double_click
        self.item = None
        self.label = None
        self.editor = None
        self.checkbox = None
        self.raw_text = ""
        self.priority = 0

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton and self.on_double_click is not None:
            # 复选框和操作按钮各自处理双击；只有文本区才进入行内编辑
            child = self.childAt(event.position().toPoint())
            if not isinstance(child, QAbstractButton):
                self.on_double_click(self.todo_id)
                event.accept()
                return
        super().mouseDoubleClickEvent(event)


class ElidedLabel(QLabel):
    """单行标签：宽度不足时用省略号截断，完整文本留在悬停提示里。

    故意不做自动换行，行高由字体行高决定，放进 QListWidget 后
    不会被拉成忽大忽小的高度。
    """

    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.setTextFormat(Qt.PlainText)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)

    def sizeHint(self):
        return QSize(0, self.fontMetrics().height())

    def minimumSizeHint(self):
        return self.sizeHint()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setFont(self.font())
        painter.setPen(self.palette().color(self.foregroundRole()))
        text = self.fontMetrics().elidedText(self.text(), Qt.ElideRight, self.width())
        painter.drawText(self.rect(), int(self.alignment()), text)
        painter.end()


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


class DiaryWindow(ChromeWindow):
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
        outer = self.chrome_layout
        self.title_bar.title_label.setText("工作日志")
        self.history_button = QPushButton("打开日历")
        self.history_button.setObjectName("calendarToggle")
        self.history_button.setProperty("variant", "quiet")
        self.history_button.setCheckable(True)
        self.history_button.setFixedHeight(40)
        self.history_button.clicked.connect(self.open_history)
        self.title_bar.actions_layout.addWidget(self.history_button)

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
        self.preview.setFocus()

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
            if not self.isMaximized() and not self.isFullScreen():
                self.resize(max(self.width(), 1140), self.height())
            self._sync_calendar_selection()
            try:
                self._refresh_calendar_marks()
            except sqlite3.Error as exc:
                QMessageBox.warning(self, "无法刷新日历", f"记录标记未更新，请稍后重试。\n{exc}")
        elif not self.isMaximized() and not self.isFullScreen():
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

    def append_external(self, entry, date_str):
        """一键投递写入数据库后，把新条目同步到已打开的日志窗口。

        必须在窗口所属的 Qt 线程中调用。返回是否把条目并入了编辑器。
        """
        self._refresh_calendar_marks()
        if date_str != self.current_date:
            return False
        if self.mode != "edit":
            self.load_date(date_str)
            return True
        if self.editor.toPlainText() == self._saved_content:
            # 编辑器没有未保存的改动：并入条目并保持"已保存"状态
            old = self._saved_content.rstrip()
            merged = f"{old}\n\n{entry}" if old else entry
            position = self.editor.textCursor().position()
            self.editor.setPlainText(merged)
            cursor = self.editor.textCursor()
            cursor.setPosition(min(position, len(merged)))
            self.editor.setTextCursor(cursor)
            self._saved_content = merged
        else:
            # 有未保存的输入：追加到编辑器末尾，保留用户改动（仍标记未保存）
            current = self.editor.toPlainText().rstrip()
            self.editor.setPlainText(f"{current}\n\n{entry}" if current else entry)
            cursor = self.editor.textCursor()
            cursor.movePosition(QTextCursor.End)
            self.editor.setTextCursor(cursor)
        self._update_save_status()
        return True

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

class TodoEditorWindow(ChromeWindow):
    """待办事项编辑与归档管理。"""

    def __init__(self, db, on_changed=None):
        super().__init__()
        self.db = db
        self.on_changed = on_changed
        self._closed = False
        self._editing_id = None
        self._row_widgets = {}
        self._build_ui()
        self.refresh_list()

    def _build_ui(self):
        self.setWindowTitle("待办清单")
        self.setObjectName("todoWindow")
        self.resize(680, 660)
        self.setMinimumSize(440, 460)
        _apply_window_style(self)
        self.title_bar.title_label.setText("待办清单")

        content = QWidget()
        self.chrome_layout.addWidget(content, 1)
        root = QVBoxLayout(content)
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
        self.entry.setFocus()

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
                                "回车添加 · 双击文字修改 · 完成后可归档回看")
        self._row_widgets = {}
        self._editing_id = None
        self.list.clear()
        self.content_stack.setCurrentWidget(self.list if rows else self.empty_state)
        for r in rows:
            item = QListWidgetItem()
            item.setData(Qt.UserRole, r["id"])
            item.setData(Qt.AccessibleTextRole, r["text"])
            self.list.addItem(item)

            row = TodoRowWidget(r["id"], self._begin_edit)
            row.setObjectName("todoRow")
            row.setFont(self.list.font())
            row.item = item
            row.raw_text = r["text"]
            row.priority = r["priority"]
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
            row.checkbox = checkbox

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
            row.label = label

            editor = TodoEditLine()
            editor.setObjectName("todoEdit")
            editor.setFont(QFont(self.list.font()))
            editor.setAccessibleName(f"编辑待办：{r['text']}")
            editor.setPlaceholderText("修改这件事…")
            editor.setFixedHeight(QFontMetrics(editor.font()).height() + 8)
            editor.editingFinished.connect(
                lambda tid=r["id"]: self._commit_edit(tid)
            )
            editor.canceled.connect(
                lambda tid=r["id"]: self._cancel_edit(tid)
            )
            editor.hide()
            text_layout.addWidget(editor)
            row.editor = editor

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

            # 预留编辑框显示时的高度，双击进入编辑时行高不跳动、内容不被裁切
            label.hide()
            editor.show()
            item.setSizeHint(row.sizeHint())
            editor.hide()
            label.show()
            self.list.setItemWidget(item, row)
            self._row_widgets[r["id"]] = row
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

    def _begin_edit(self, todo_id):
        """双击待办行进入行内编辑；若已在编辑其它行，先提交再切换。"""
        if self._editing_id == todo_id:
            return
        self._finish_edit()
        row = self._row_widgets.get(todo_id)
        if row is None or row.editor is None:
            return
        self._editing_id = todo_id
        row.editor.setText(row.raw_text)
        row.label.hide()
        row.editor.show()
        row.editor.setFocus(Qt.MouseFocusReason)
        row.editor.selectAll()
        if row.item is not None:
            self.list.setCurrentItem(row.item)

    def _commit_edit(self, todo_id):
        if self._editing_id == todo_id:
            self._finish_edit()

    def _cancel_edit(self, todo_id):
        if self._editing_id != todo_id:
            return
        self._editing_id = None
        row = self._row_widgets.get(todo_id)
        if row is None or row.editor is None:
            return
        row.editor.hide()
        row.label.show()

    def _finish_edit(self):
        """提交当前行内编辑；文本为空或未改动时只退出编辑。"""
        if self._editing_id is None:
            return
        todo_id = self._editing_id
        self._editing_id = None
        row = self._row_widgets.get(todo_id)
        if row is None or row.editor is None:
            return
        text = row.editor.text().strip()
        row.editor.hide()
        row.label.show()
        if text and text != row.raw_text:
            self.db.update_todo(todo_id, text=text)
            row.raw_text = text
            self._apply_row_text(row, text)
            self._notify()

    def _apply_row_text(self, row, text):
        suffix = {1: "  ⚑", 2: "  ⚑⚑"}.get(row.priority, "")
        row.label.setText(f"{text}{suffix}")
        row.setToolTip(f"<qt>{_html.escape(text)}</qt>")
        if row.item is not None:
            row.item.setData(Qt.AccessibleTextRole, text)
        if row.checkbox is not None:
            row.checkbox.setAccessibleName(text)

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


# ═══════════════════════════════════════════════════════════
# 桌面投递气泡（可见的一键投递入口）
# ═══════════════════════════════════════════════════════════

BUBBLE_IDLE = "idle"
BUBBLE_BUSY = "busy"
BUBBLE_OK = "ok"
BUBBLE_ERROR = "error"

BUBBLE_STYLE = """
QWidget#captureBubble {
    background: rgba(255, 255, 255, 0.50);
    border: 1px solid rgba(217, 217, 227, 0.60);
    border-radius: 26px;
}
QWidget#captureBubble:hover {
    background: rgba(255, 255, 255, 0.62);
    border-color: rgba(200, 202, 208, 0.75);
}
QLabel#bubbleIcon {
    background: transparent;
    color: #10a37f;
    font-size: 21px;
    font-weight: 600;
}
QLabel#bubbleStateDot {
    background: rgba(232, 232, 237, 0.90);
    border: 2px solid rgba(255, 255, 255, 0.90);
    border-radius: 7px;
}
QWidget#captureBubble[state="busy"] QLabel#bubbleStateDot { background: #2f80ed; }
QWidget#captureBubble[state="ok"] QLabel#bubbleStateDot { background: #10a37f; }
QWidget#captureBubble[state="error"] QLabel#bubbleStateDot { background: #e5484d; }
QLabel#bubbleTitle, QLabel#bubbleStatus { background: transparent; }
"""


def _foreground_hwnd():
    """当前前台窗口句柄（点击气泡前用户正在操作的窗口）。"""
    try:
        return int(ctypes.windll.user32.GetForegroundWindow() or 0)
    except Exception:
        return 0


def _set_no_activate(widget):
    """给气泡窗口加上 WS_EX_NOACTIVATE / WS_EX_TOOLWINDOW。

    点击气泡时来源窗口必须保持焦点，注入的 Ctrl+C 才能复制到用户选中的文字。
    """
    try:
        hwnd = int(widget.winId())
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        if ctypes.sizeof(ctypes.c_void_p) == 8:
            get_long, set_long = user32.GetWindowLongPtrW, user32.SetWindowLongPtrW
            long_t = ctypes.c_ssize_t
        else:
            get_long, set_long = user32.GetWindowLongW, user32.SetWindowLongW
            long_t = ctypes.c_int32
        get_long.argtypes = [ctypes.c_void_p, ctypes.c_int]
        get_long.restype = long_t
        set_long.argtypes = [ctypes.c_void_p, ctypes.c_int, long_t]
        set_long.restype = long_t
        GWL_EXSTYLE = -20
        WS_EX_NOACTIVATE = 0x08000000
        WS_EX_TOOLWINDOW = 0x00000080
        style = get_long(hwnd, GWL_EXSTYLE)
        set_long(hwnd, GWL_EXSTYLE,
                 (style or 0) | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW)
    except Exception as e:
        print(f"[投递气泡] 设置窗口样式失败: {e}")


class CaptureBubble(QWidget):
    """桌面常驻的投递气泡：左键记录选中内容，右键打开投递菜单。

    - WS_EX_NOACTIVATE 保证点击气泡时来源窗口仍是前台窗口，
      这样抓取选中内容时注入的 Ctrl+C 才有效。
    - 可拖动，位置由调用方持久化（on_moved 回调）。
    """

    def __init__(self, on_capture=None, on_open_diary=None, on_hide=None,
                 on_moved=None, parent=None):
        super().__init__(parent)
        self.on_capture = on_capture
        self.on_open_diary = on_open_diary
        self.on_hide = on_hide
        self.on_moved = on_moved
        self._state = BUBBLE_IDLE
        self._press_offset = None
        self._dragged = False
        self._no_activate_applied = False
        self._reset_timer = QTimer(self)
        self._reset_timer.setSingleShot(True)
        self._reset_timer.timeout.connect(self._reset)
        if parent is None:
            self.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint
                                | Qt.WindowStaysOnTopHint
                                | Qt.WindowDoesNotAcceptFocus)
            self.setAttribute(Qt.WA_ShowWithoutActivating, True)
            self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setFixedSize(72, 72)
        self.setToolTip("左键：记录选中内容\n右键：更多投递方式\n拖动：移动气泡")
        self._build_ui()
        self._apply_state()

    def _build_ui(self):
        """投递入口只保留一个圆形气泡，状态用边缘圆点表达。"""
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(10, 10, 10, 10)
        root_layout.setSpacing(0)

        self.card = QWidget(self)
        self.card.setObjectName("captureBubble")
        self.card.setFixedSize(52, 52)
        self.card.setAttribute(Qt.WA_StyledBackground, True)
        self.card.setCursor(Qt.PointingHandCursor)
        root_layout.addWidget(self.card, 0, Qt.AlignCenter)

        icon_layout = QVBoxLayout(self.card)
        icon_layout.setContentsMargins(0, 0, 0, 0)
        icon_layout.setSpacing(0)

        self.icon_label = QLabel("↗", self.card)
        self.icon_label.setObjectName("bubbleIcon")
        self.icon_label.setAlignment(Qt.AlignCenter)
        self.icon_label.setFont(QFont(BASE_FONT, 12, QFont.Weight.DemiBold))
        self.icon_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        icon_layout.addWidget(self.icon_label, 0, Qt.AlignCenter)

        self.state_dot = QLabel(self.card)
        self.state_dot.setObjectName("bubbleStateDot")
        self.state_dot.setGeometry(37, 37, 15, 15)
        self.state_dot.setAttribute(Qt.WA_TransparentForMouseEvents, True)

        # 保留状态文本 API / 可访问性信息，但圆形气泡本身不再承载文字。
        self.title_label = QLabel("投递", self.card)
        self.title_label.setObjectName("bubbleTitle")
        self.title_label.setAccessibleName("投递气泡")
        self.title_label.hide()
        self.status_label = QLabel("记录选中内容", self.card)
        self.status_label.setObjectName("bubbleStatus")
        self.status_label.setFont(QFont(BASE_FONT, 8))
        self.status_label.setAccessibleName("投递状态")
        self.status_label.hide()

        shadow = QGraphicsDropShadowEffect(self.card)
        shadow.setBlurRadius(14)
        shadow.setXOffset(0)
        shadow.setYOffset(3)
        shadow.setColor(QColor(0, 0, 0, 46))
        self.card.setGraphicsEffect(shadow)

    def _apply_state(self):
        self.card.setProperty("state", self._state)
        self.setStyleSheet(BUBBLE_STYLE)
        for widget in (self.card, self.icon_label, self.state_dot):
            widget.style().unpolish(widget)
            widget.style().polish(widget)

    # --- 状态 ---
    @staticmethod
    def _shorten(text, limit=10):
        text = (text or "").replace("\n", " ").strip()
        return text if len(text) <= limit else text[:limit - 1] + "…"

    def set_busy(self, text="读取中…"):
        self._state = BUBBLE_BUSY
        self.status_label.setText(self._shorten(text))
        self._apply_state()

    def show_result(self, state, text):
        self._state = state
        self.status_label.setText(self._shorten(text))
        self._apply_state()
        self._reset_timer.start(4000)

    def _reset(self):
        self._state = BUBBLE_IDLE
        self.status_label.setText("记录选中内容")
        self._apply_state()

    # --- 位置 ---
    def show_at(self, x, y):
        if self.parentWidget() is not None:
            self.setVisible(True)
            return
        if x is None or y is None or int(x) < 0 or int(y) < 0:
            self._move_default()
        else:
            self.move(int(x), int(y))
            self._clamp()
        self.show()
        self.raise_()

    def _drag_target(self):
        """分组内拖动整个虚拟框；独立使用时拖动自身。"""
        return self.window() if self.parentWidget() is not None else self

    def _move_default(self):
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        self.move(area.right() - self.width() - 24, area.top() + 96)

    def _clamp(self):
        screen = QApplication.screenAt(self.frameGeometry().center())
        if screen is None:
            screen = QApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        x = min(max(self.x(), area.left()), area.right() - self.width() + 1)
        y = min(max(self.y(), area.top()), area.bottom() - self.height() + 1)
        self.move(x, y)

    # --- 交互 ---
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            target = self._drag_target()
            self._press_offset = (event.globalPosition().toPoint()
                                  - target.frameGeometry().topLeft())
            self._dragged = False
            event.accept()
        elif event.button() == Qt.RightButton:
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._press_offset is not None and (event.buttons() & Qt.LeftButton):
            target = self._drag_target()
            target.move(event.globalPosition().toPoint() - self._press_offset)
            self._dragged = True
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._press_offset is not None:
            dragged = self._dragged
            target = self._drag_target()
            self._press_offset = None
            self._dragged = False
            if dragged:
                target._clamp()
                if self.on_moved:
                    self.on_moved(target.x(), target.y())
            else:
                self._dispatch("selection")
            event.accept()
        elif event.button() == Qt.RightButton:
            self._popup_menu(event.globalPosition().toPoint())
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def _build_menu(self):
        menu = QMenu(self)
        menu.addAction("记录选中内容", lambda: self._dispatch("selection"))
        menu.addAction("记录剪贴板", lambda: self._dispatch("clipboard"))
        menu.addSeparator()
        menu.addAction("打开工作日志", self._open_diary)
        menu.addSeparator()
        menu.addAction("隐藏气泡", self._hide)
        return menu

    def _popup_menu(self, global_pos):
        self._build_menu().exec(global_pos)

    def _dispatch(self, mode):
        if self.on_capture:
            self.on_capture(mode)

    def _open_diary(self):
        if self.on_open_diary:
            self.on_open_diary()

    def _hide(self):
        if self.on_hide:
            self.on_hide()

    def showEvent(self, event):
        super().showEvent(event)
        if self.parentWidget() is None and not self._no_activate_applied:
            _set_no_activate(self)
            self._no_activate_applied = True

# ═══════════════════════════════════════════════════════════
# 阅读疑难（收集气泡 + 疑问清单窗口）
# ═══════════════════════════════════════════════════════════

QUESTION_BUBBLE_STYLE = """
QWidget#questionBubble {
    background: rgba(255, 255, 255, 0.50);
    border: 1px solid rgba(217, 217, 227, 0.60);
    border-radius: 26px;
}
QWidget#questionBubble:hover {
    background: rgba(255, 255, 255, 0.62);
    border-color: rgba(200, 202, 208, 0.75);
}
QLabel#questionBubbleIcon {
    background: transparent;
    color: #e5484d;
    font-size: 23px;
    font-weight: 600;
}
QLabel#questionCountBadge {
    background: rgba(229, 72, 77, 0.95);
    color: #ffffff;
    border: 2px solid rgba(255, 255, 255, 0.90);
    border-radius: 11px;
    font-size: 9px;
    font-weight: 600;
}
QLabel#questionBubbleTitle, QLabel#questionBubbleStatus { background: transparent; }
"""


class QuestionBubble(QWidget):
    """阅读疑难气泡：左键打开疑难清单，右键更多操作。

    与投递气泡（CaptureBubble）互相独立：这里不做内容捕获，只作为清单入口，
    并显示当前待解决的条数。
    """

    def __init__(self, on_open=None, on_hide=None, on_moved=None,
                 parent=None):
        super().__init__(parent)
        self.on_open = on_open
        self.on_hide = on_hide
        self.on_moved = on_moved
        self._press_offset = None
        self._dragged = False
        self._no_activate_applied = False
        self.setObjectName("questionBubbleRoot")
        if parent is None:
            self.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint
                                | Qt.WindowStaysOnTopHint
                                | Qt.WindowDoesNotAcceptFocus)
            self.setAttribute(Qt.WA_ShowWithoutActivating, True)
            self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setFixedSize(72, 72)
        self.setToolTip("左键：打开疑难清单  右键：更多操作  拖动：移动气泡")
        self._build_ui()

    def _build_ui(self):
        """疑难入口是一个圆形气泡；数量只用数字徽标表达。"""
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(10, 10, 10, 10)
        root_layout.setSpacing(0)

        self.card = QWidget(self)
        self.card.setObjectName("questionBubble")
        self.card.setFixedSize(52, 52)
        self.card.setAttribute(Qt.WA_StyledBackground, True)
        self.card.setCursor(Qt.PointingHandCursor)
        root_layout.addWidget(self.card, 0, Qt.AlignCenter)

        icon_layout = QVBoxLayout(self.card)
        icon_layout.setContentsMargins(0, 0, 0, 0)
        icon_layout.setSpacing(0)

        self.icon_label = QLabel("？", self.card)
        self.icon_label.setObjectName("questionBubbleIcon")
        self.icon_label.setAlignment(Qt.AlignCenter)
        self.icon_label.setFont(QFont(BASE_FONT, 13, QFont.Weight.DemiBold))
        self.icon_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        icon_layout.addWidget(self.icon_label, 0, Qt.AlignCenter)

        self.count_badge = QLabel("", self.card)
        self.count_badge.setObjectName("questionCountBadge")
        self.count_badge.setGeometry(34, 1, 22, 22)
        self.count_badge.setAlignment(Qt.AlignCenter)
        self.count_badge.setFont(QFont(BASE_FONT, 7, QFont.Weight.DemiBold))
        self.count_badge.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.count_badge.hide()

        # 文案保留给辅助技术和既有 API；可见面上只显示数字。
        self.title_label = QLabel("疑难", self.card)
        self.title_label.setObjectName("questionBubbleTitle")
        self.title_label.setAccessibleName("疑难气泡")
        self.title_label.hide()
        self.status_label = QLabel("", self.card)
        self.status_label.setObjectName("questionBubbleStatus")
        self.status_label.setFont(QFont(BASE_FONT, 8))
        self.status_label.setAccessibleName("疑难状态")
        self.status_label.hide()

        shadow = QGraphicsDropShadowEffect(self.card)
        shadow.setBlurRadius(14)
        shadow.setXOffset(0)
        shadow.setYOffset(3)
        shadow.setColor(QColor(0, 0, 0, 46))
        self.card.setGraphicsEffect(shadow)
        self.setStyleSheet(QUESTION_BUBBLE_STYLE)

    def set_count(self, count):
        """更新气泡上的待解决条数：可见面只显示数字。"""
        count = int(count or 0)
        self.count_badge.setText(str(count))
        self.count_badge.setVisible(count > 0)
        self.status_label.setText("%d 条待解决" % count if count else "暂无疑问")

    # --- 位置 ---
    def show_at(self, x, y):
        if self.parentWidget() is not None:
            self.setVisible(True)
            return
        if x is None or y is None or int(x) < 0 or int(y) < 0:
            self._move_default()
        else:
            self.move(int(x), int(y))
            self._clamp()
        self.show()
        self.raise_()

    def _drag_target(self):
        """分组内拖动整个虚拟框；独立使用时拖动自身。"""
        return self.window() if self.parentWidget() is not None else self

    def _move_default(self):
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        self.move(area.right() - self.width() - 24, area.top() + 168)

    def _clamp(self):
        screen = QApplication.screenAt(self.frameGeometry().center())
        if screen is None:
            screen = QApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        x = min(max(self.x(), area.left()), area.right() - self.width() + 1)
        y = min(max(self.y(), area.top()), area.bottom() - self.height() + 1)
        self.move(x, y)

    # --- 交互 ---
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            target = self._drag_target()
            self._press_offset = (event.globalPosition().toPoint()
                                  - target.frameGeometry().topLeft())
            self._dragged = False
            event.accept()
        elif event.button() == Qt.RightButton:
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._press_offset is not None and (event.buttons() & Qt.LeftButton):
            target = self._drag_target()
            target.move(event.globalPosition().toPoint() - self._press_offset)
            self._dragged = True
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._press_offset is not None:
            dragged = self._dragged
            target = self._drag_target()
            self._press_offset = None
            self._dragged = False
            if dragged:
                target._clamp()
                if self.on_moved:
                    self.on_moved(target.x(), target.y())
            else:
                self._open()
            event.accept()
        elif event.button() == Qt.RightButton:
            self._popup_menu(event.globalPosition().toPoint())
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def _build_menu(self):
        menu = QMenu(self)
        menu.addAction("打开疑难清单", self._open)
        menu.addSeparator()
        menu.addAction("隐藏气泡", self._hide)
        return menu

    def _popup_menu(self, global_pos):
        self._build_menu().exec(global_pos)

    def _open(self):
        if self.on_open:
            self.on_open()

    def _hide(self):
        if self.on_hide:
            self.on_hide()

    def showEvent(self, event):
        super().showEvent(event)
        if self.parentWidget() is None and not self._no_activate_applied:
            _set_no_activate(self)
            self._no_activate_applied = True


BUBBLE_GROUP_STYLE = """
QWidget#bubbleGroup {
    background: transparent;
    border: none;
}
"""


class BubbleGroup(QWidget):
    """一个不可见的虚拟框：把投递与疑难气泡垂直排列并整体拖动。

    容器本身没有背景和边框；两个子气泡保持同一横坐标。窗口不抢焦点，
    保证投递气泡注入 Ctrl+C 时仍然作用于用户原来的前台窗口。
    """

    SPACING = 16

    def __init__(self, on_capture=None, on_open_diary=None,
                 on_hide_capture=None, on_open_question=None,
                 on_hide_question=None, on_moved=None):
        super().__init__(None)
        self.on_moved = on_moved
        self.setObjectName("bubbleGroup")
        self.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint
                            | Qt.WindowStaysOnTopHint
                            | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)

        self.capture_bubble = CaptureBubble(
            on_capture=on_capture,
            on_open_diary=on_open_diary,
            on_hide=on_hide_capture,
            on_moved=on_moved,
            parent=self,
        )
        self.question_bubble = QuestionBubble(
            on_open=on_open_question,
            on_hide=on_hide_question,
            on_moved=on_moved,
            parent=self,
        )
        # 虚拟框创建时两个入口都处于关闭状态；显示开关由调用方逐个控制。
        self.capture_bubble.hide()
        self.question_bubble.hide()
        for bubble in (self.capture_bubble, self.question_bubble):
            policy = bubble.sizePolicy()
            policy.setRetainSizeWhenHidden(True)
            bubble.setSizePolicy(policy)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(self.SPACING)
        layout.addWidget(self.capture_bubble, 0, Qt.AlignHCenter)
        layout.addWidget(self.question_bubble, 0, Qt.AlignHCenter)
        self.setFixedSize(72, 72 * 2 + self.SPACING)
        self.setToolTip("投递 / 疑难气泡组：拖动任意气泡可整体移动")
        self.setStyleSheet(BUBBLE_GROUP_STYLE)

    def show_at(self, x, y):
        if x is None or y is None or int(x) < 0 or int(y) < 0:
            self._move_default()
        else:
            self.move(int(x), int(y))
            self._clamp()
        self.show()
        self.raise_()

    def set_capture_visible(self, visible):
        self.capture_bubble.setVisible(bool(visible))

    def set_question_visible(self, visible):
        self.question_bubble.setVisible(bool(visible))

    def set_count(self, count):
        self.question_bubble.set_count(count)

    def has_visible_bubble(self):
        return (self.capture_bubble.isVisibleTo(self)
                or self.question_bubble.isVisibleTo(self))

    def sync_visibility(self):
        if self.has_visible_bubble():
            self.show()
        else:
            self.hide()

    def _move_default(self):
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        self.move(area.right() - self.width() - 24, area.top() + 96)

    def _clamp(self):
        screen = QApplication.screenAt(self.frameGeometry().center())
        if screen is None:
            screen = QApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        x = min(max(self.x(), area.left()), area.right() - self.width() + 1)
        y = min(max(self.y(), area.top()), area.bottom() - self.height() + 1)
        self.move(x, y)

    def showEvent(self, event):
        super().showEvent(event)
        _set_no_activate(self)


class QuestionWindow(ChromeWindow):
    """阅读疑难清单：所有疑问汇到这一处，每条都能一键跳回原文高亮。"""

    RESOLVE_TEXT = {True: "恢复未解决", False: "标记已解决"}

    def __init__(self, db, on_changed=None):
        super().__init__()
        self.db = db
        self.on_changed = on_changed
        self._closed = False
        self._build_ui()
        self.refresh_list()

    def _build_ui(self):
        self.setWindowTitle("阅读疑难")
        self.setObjectName("questionWindow")
        self.resize(720, 660)
        self.setMinimumSize(480, 460)
        _apply_window_style(self)
        self.title_bar.title_label.setText("阅读疑难")

        content = QWidget()
        self.chrome_layout.addWidget(content, 1)
        root = QVBoxLayout(content)
        root.setContentsMargins(28, 24, 28, 20)
        root.setSpacing(12)

        title_row = QHBoxLayout()
        title = QLabel("阅读疑难")
        title.setFont(QFont(BASE_FONT, 20, QFont.DemiBold))
        title_row.addWidget(title)
        title_row.addStretch(1)
        self.summary = QLabel("")
        self.summary.setObjectName("questionSummary")
        self.summary.setProperty("tone", "secondary")
        self.summary.setFont(QFont(BASE_FONT, 9))
        title_row.addWidget(self.summary)
        root.addLayout(title_row)
        root.addSpacing(4)

        navigation = QWidget()
        navigation.setObjectName("questionNavigation")
        nav = QHBoxLayout(navigation)
        nav.setContentsMargins(0, 0, 0, 0)
        self.tabs = QTabBar()
        self.tabs.setFont(QFont(BASE_FONT, 10))
        self.tabs.setExpanding(False)
        self.tabs.setDrawBase(False)
        self.tabs.addTab("待解决")
        self.tabs.addTab("已解决")
        nav.addWidget(self.tabs)
        nav.addStretch(1)
        self.open_button = QPushButton("打开原文")
        self.open_button.setProperty("variant", "primary")
        self.open_button.setFixedHeight(32)
        self.open_button.setToolTip("在浏览器中打开，并高亮当时的句子")
        self.open_button.clicked.connect(self.open_selected)
        nav.addWidget(self.open_button)
        self.resolve_button = QPushButton("标记已解决")
        self.resolve_button.setProperty("variant", "quiet")
        self.resolve_button.setFixedHeight(32)
        self.resolve_button.clicked.connect(self.toggle_selected)
        nav.addWidget(self.resolve_button)
        self.delete_button = QPushButton("删除")
        self.delete_button.setProperty("variant", "quiet")
        self.delete_button.setFixedHeight(32)
        self.delete_button.setToolTip("删除选中的疑难")
        self.delete_button.clicked.connect(self.delete_selected)
        nav.addWidget(self.delete_button)
        root.addWidget(navigation)

        self.content_stack = QStackedWidget()
        self.list = QListWidget()
        self.list.setFont(QFont(BASE_FONT, 12))
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        self.list.setWordWrap(True)
        self.list.itemSelectionChanged.connect(self._sync_actions)
        self.list.itemDoubleClicked.connect(lambda _item: self.open_selected())
        self.content_stack.addWidget(self.list)
        self.empty_state = QWidget()
        empty = QVBoxLayout(self.empty_state)
        empty.setSpacing(10)
        empty.addStretch(1)
        self.empty_title = QLabel("")
        self.empty_title.setObjectName("questionEmptyTitle")
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

        self.tabs.currentChanged.connect(self.refresh_list)
        self._sync_actions()

    # --- 列表 ---
    def _rows(self):
        return self.db.get_questions(resolved=self.tabs.currentIndex() == 1)

    # 疑难原文常是整段话，列表里只放首句做标题，完整原文留给悬停提示。
    QUOTE_TITLE_LIMIT = 72

    @staticmethod
    def _quote_title(text, limit=QUOTE_TITLE_LIMIT):
        """把疑难原文压成一行标题：取第一句，过长再截断成省略号。"""
        clean = re.sub(r"\s+", " ", text or "").strip()
        if not clean:
            return ""
        match = re.search(r"[^。！？!?…]*[。！？!?…]", clean)
        title = (match.group(0) if match else clean).strip()
        if len(title) > limit:
            title = title[:limit].rstrip() + "…"
        return title

    def refresh_list(self):
        selected = self.list.selectedItems()
        selected_id = selected[0].data(Qt.UserRole) if selected else None
        scroll_position = self.list.verticalScrollBar().value()
        resolved = self.tabs.currentIndex() == 1
        rows = self._rows()
        pending = self.db.count_questions()
        done = self.db.count_questions(resolved=True)
        self.summary.setText("%d 条待解决 · %d 条已解决" % (pending, done))
        self.empty_title.setText("这些疑问都解决了" if resolved else "还没有记录疑问")
        self.empty_hint.setText(
            "已解决的疑问会留在这里，可随时恢复。" if resolved
            else "在网页上选中不懂的句子，右键「标记疑难」，就会汇总到这里。")
        self.list.clear()
        self.content_stack.setCurrentWidget(self.list if rows else self.empty_state)
        for position, r in enumerate(rows, start=1):
            item = QListWidgetItem()
            item.setData(Qt.UserRole, r["id"])
            item.setData(Qt.AccessibleTextRole,
                         r["quote"] or r["question"] or r["url"])
            self.list.addItem(item)
            row = self._build_row(r, resolved, position)
            item.setSizeHint(row.sizeHint())
            self.list.setItemWidget(item, row)
            if r["id"] == selected_id:
                self.list.setCurrentItem(item)
        self.list.verticalScrollBar().setValue(scroll_position)
        self._sync_actions()

    def _build_row(self, record, resolved, index=None):
        row = QWidget()
        row.setObjectName("questionRow")
        row.setFont(self.list.font())
        layout = QHBoxLayout(row)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(10)

        if index is not None:
            number = QLabel("%d" % index)
            number.setObjectName("questionIndex")
            number.setProperty("tone", "secondary")
            number.setFont(QFont(BASE_FONT, 10))
            number.setFixedWidth(24)
            number.setAlignment(Qt.AlignRight | Qt.AlignTop)
            number.setAttribute(Qt.WA_TransparentForMouseEvents)
            layout.addWidget(number, 0)

        text_layout = QVBoxLayout()
        text_layout.setSpacing(2)
        flags = {1: " ⚑", 2: " ⚑⚑", 3: " ⚑⚑⚑"}.get(record["priority"], "")
        title = self._quote_title(record["quote"]) or "(未记录原文)"
        quote = ElidedLabel("%s%s" % (title, flags))
        quote.setObjectName("questionQuote")
        quote.setAttribute(Qt.WA_TransparentForMouseEvents)
        quote_font = QFont(self.list.font())
        quote_font.setStrikeOut(bool(resolved))
        quote.setFont(quote_font)
        if resolved:
            quote.setProperty("tone", "secondary")
        text_layout.addWidget(quote)

        if record["question"]:
            question = ElidedLabel(record["question"])
            question.setAttribute(Qt.WA_TransparentForMouseEvents)
            text_layout.addWidget(question)

        meta_row = QHBoxLayout()
        meta_row.setSpacing(8)
        if record["category"]:
            chip = QLabel(record["category"])
            chip.setObjectName("questionChip")
            chip.setAttribute(Qt.WA_TransparentForMouseEvents)
            meta_row.addWidget(chip)
        source = record["title"] or record["url"] or "未知来源"
        meta = ElidedLabel("%s · %s" % (source, record["created_at"][:16]))
        meta.setObjectName("questionMeta")
        meta.setFont(QFont(BASE_FONT, 9))
        meta.setAttribute(Qt.WA_TransparentForMouseEvents)
        meta_row.addWidget(meta, 1)
        text_layout.addLayout(meta_row)
        row.setToolTip("<qt>%s</qt>" % _html.escape(record["quote"] or ""))
        layout.addLayout(text_layout, 1)

        open_button = QPushButton("打开原文")
        open_button.setProperty("variant", "quiet")
        open_button.setFont(QFont(BASE_FONT, 10))
        open_button.setFixedHeight(32)
        open_button.clicked.connect(
            lambda checked=False, rid=record["id"]: self.open_by_id(rid)
        )
        layout.addWidget(open_button, 0, Qt.AlignTop)
        return row

    def _sync_actions(self):
        has_selection = bool(self.list.selectedItems())
        resolved = self.tabs.currentIndex() == 1
        self.open_button.setEnabled(has_selection)
        self.resolve_button.setEnabled(has_selection)
        self.resolve_button.setText(self.RESOLVE_TEXT[resolved])
        self.delete_button.setEnabled(has_selection)

    def _selected_id(self):
        selected = self.list.selectedItems()
        return selected[0].data(Qt.UserRole) if selected else None

    # --- 动作 ---
    @staticmethod
    def _jump_url_for(record):
        """由疑难记录生成带回跳锚点的链接；无锚点时退回原链接。"""
        anchor = {}
        raw = (record.get("anchor") or "").strip()
        if raw:
            try:
                parsed = json.loads(raw)
            except (ValueError, TypeError):
                parsed = None
            if isinstance(parsed, dict):
                anchor = parsed
        return question_server.build_jump_url(record.get("url") or "", anchor)

    def open_original(self, record):
        """在默认浏览器中打开原文，并带上高亮锚点。"""
        if not record:
            return False
        url = self._jump_url_for(record)
        if not url:
            return False
        QDesktopServices.openUrl(QUrl(url))
        return True

    def open_by_id(self, question_id):
        self.open_original(self.db.get_question(question_id))

    def open_selected(self):
        self.open_by_id(self._selected_id())

    def toggle_selected(self):
        selected_id = self._selected_id()
        if selected_id is None:
            return
        self.db.toggle_question_resolved(selected_id)
        self.refresh_list()
        self._notify()

    def delete_selected(self):
        selected_id = self._selected_id()
        if selected_id is None:
            return
        self.db.delete_question(selected_id)
        self.refresh_list()
        self._notify()

    def _notify(self):
        if self.on_changed:
            self.on_changed()

    def closeEvent(self, event):
        self._closed = True
        event.accept()
