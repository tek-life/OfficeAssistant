"""Windows 自绘标题栏；颜色由调用方的 palette / QSS 提供。"""

import ctypes
import math

from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import QPainter, QPalette, QPen
from PySide6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QProxyStyle, QPushButton, QSizePolicy,
    QStyle, QStyleOptionButton, QVBoxLayout, QWidget,
)


HTCLIENT, HTCAPTION = 1, 2
HTLEFT, HTRIGHT, HTTOP = 10, 11, 12
HTTOPLEFT, HTTOPRIGHT, HTBOTTOM = 13, 14, 15
HTBOTTOMLEFT, HTBOTTOMRIGHT = 16, 17
WM_GETMINMAXINFO = 0x0024
WM_STYLECHANGING = 0x007C
WM_NCCALCSIZE = 0x0083
WM_NCHITTEST = 0x0084
WM_DWMCOMPOSITIONCHANGED = 0x031E
GWL_STYLE = -16
# WS_CAPTION | WS_THICKFRAME | WS_SYSMENU | WS_MINIMIZEBOX | WS_MAXIMIZEBOX
NATIVE_STYLE = 0x00C00000 | 0x00040000 | 0x00080000 | 0x00020000 | 0x00010000


# 固定宽度字段与指针宽度字段分开，避免 64 位 HWND / LPARAM 被截断。
class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_int32), ("y", ctypes.c_int32)]


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_int32), ("top", ctypes.c_int32),
                ("right", ctypes.c_int32), ("bottom", ctypes.c_int32)]


class MINMAXINFO(ctypes.Structure):
    _fields_ = [("ptReserved", POINT), ("ptMaxSize", POINT),
                ("ptMaxPosition", POINT), ("ptMinTrackSize", POINT),
                ("ptMaxTrackSize", POINT)]


class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint32), ("rcMonitor", RECT),
                ("rcWork", RECT), ("dwFlags", ctypes.c_uint32)]


class _MSG(ctypes.Structure):
    _fields_ = [("hwnd", ctypes.c_void_p), ("message", ctypes.c_uint32),
                ("wParam", ctypes.c_size_t), ("lParam", ctypes.c_ssize_t),
                ("time", ctypes.c_uint32), ("pt", POINT)]


class _STYLESTRUCT(ctypes.Structure):
    _fields_ = [("styleOld", ctypes.c_uint32), ("styleNew", ctypes.c_uint32)]


class _MARGINS(ctypes.Structure):
    _fields_ = [("left", ctypes.c_int32), ("right", ctypes.c_int32),
                ("top", ctypes.c_int32), ("bottom", ctypes.c_int32)]


def _signed_screen_point(lparam):
    """GET_X/Y_LPARAM 的有符号语义（支持左侧、上方显示器）。"""
    return ctypes.c_int16(lparam & 0xffff).value, ctypes.c_int16((lparam >> 16) & 0xffff).value


def _physical_to_logical(x, y, origin_x, origin_y, dpr):
    # Qt 全局坐标在混合 DPI 屏幕上不连续；只转换客户区相对坐标。
    return QPoint(math.floor((x - origin_x) / dpr), math.floor((y - origin_y) / dpr))


class _WindowsFrame:
    """仅在 windows QPA 下实例化；DLL 和函数对象由窗口持有。"""

    def __init__(self):
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        hwnd, uint, integer = ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int32
        pointer_int = ctypes.c_ssize_t

        def bind(name, args, result, actual_name=None):
            function = getattr(self.user32, actual_name or name)
            function.argtypes = args
            function.restype = result
            setattr(self, name, function)

        is_64bit = ctypes.sizeof(ctypes.c_void_p) == 8
        bind("GetWindowLongPtrW", [hwnd, integer], pointer_int,
             "GetWindowLongPtrW" if is_64bit else "GetWindowLongW")
        bind("SetWindowLongPtrW", [hwnd, integer, pointer_int], pointer_int,
             "SetWindowLongPtrW" if is_64bit else "SetWindowLongW")
        bind("SetWindowPos", [hwnd, hwnd, integer, integer, integer, integer, uint], integer)
        bind("ClientToScreen", [hwnd, ctypes.POINTER(POINT)], integer)
        bind("GetClientRect", [hwnd, ctypes.POINTER(RECT)], integer)
        bind("IsZoomed", [hwnd], integer)
        bind("MonitorFromWindow", [hwnd, uint], hwnd)
        bind("GetMonitorInfoW", [hwnd, ctypes.POINTER(_MONITORINFO)], integer)
        bind("DefWindowProcW", [hwnd, uint, ctypes.c_size_t, pointer_int], pointer_int)
        bind("GetSystemMetrics", [integer], integer)
        self.GetDpiForWindow = None
        self.GetSystemMetricsForDpi = None
        if hasattr(self.user32, "GetDpiForWindow"):
            bind("GetDpiForWindow", [hwnd], uint)
        if hasattr(self.user32, "GetSystemMetricsForDpi"):
            bind("GetSystemMetricsForDpi", [integer, uint], integer)
        self.dwm = None
        try:
            self.dwm = ctypes.WinDLL("dwmapi")
            self.dwm.DwmSetWindowAttribute.argtypes = [hwnd, uint, ctypes.c_void_p, uint]
            self.dwm.DwmSetWindowAttribute.restype = ctypes.c_int32
            self.dwm.DwmExtendFrameIntoClientArea.argtypes = [hwnd, ctypes.POINTER(_MARGINS)]
            self.dwm.DwmExtendFrameIntoClientArea.restype = ctypes.c_int32
        except (OSError, AttributeError):
            self.dwm = None

    def configure(self, hwnd):
        style = self.GetWindowLongPtrW(hwnd, GWL_STYLE)
        ctypes.set_last_error(0)
        previous = self.SetWindowLongPtrW(hwnd, GWL_STYLE, style | NATIVE_STYLE)
        if not previous and ctypes.get_last_error():
            raise ctypes.WinError(ctypes.get_last_error())
        # FRAMECHANGED | NOMOVE | NOSIZE | NOZORDER | NOACTIVATE；不调整几何或窗口状态。
        if not self.SetWindowPos(hwnd, None, 0, 0, 0, 0, 0x0020 | 0x0002 | 0x0001 | 0x0004 | 0x0010):
            raise ctypes.WinError(ctypes.get_last_error())
        self.apply_shadow(hwnd)

    def apply_shadow(self, hwnd):
        if self.dwm is not None:
            policy = ctypes.c_int32(2)  # DWMNCRP_ENABLED；不指定任何颜色。
            self.dwm.DwmSetWindowAttribute(hwnd, 2, ctypes.byref(policy), ctypes.sizeof(policy))
            self.dwm.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(_MARGINS(1, 1, 1, 1)))

    def monitor_info(self, hwnd):
        monitor = self.MonitorFromWindow(hwnd, 2)  # MONITOR_DEFAULTTONEAREST
        info = _MONITORINFO()
        info.cbSize = ctypes.sizeof(info)
        if monitor and self.GetMonitorInfoW(monitor, ctypes.byref(info)):
            return info
        return None

    def resize_border(self, hwnd, dpr):
        dpi = self.GetDpiForWindow(hwnd) if self.GetDpiForWindow else round(96 * dpr)
        metric = (lambda index: self.GetSystemMetricsForDpi(index, dpi)) if self.GetSystemMetricsForDpi else self.GetSystemMetrics
        # SM_CXSIZEFRAME / SM_CYSIZEFRAME + SM_CXPADDEDBORDER，单位为物理像素。
        return (metric(32) + metric(92)) / dpr, (metric(33) + metric(92)) / dpr


class _CaptionStyle(QProxyStyle):
    def __init__(self, button):
        # 不接管 QApplication.style() 的所有权；代理样式只属于此按钮。
        super().__init__()
        self.setParent(button)
        self.button = button

    def drawItemText(self, painter, rect, flags, palette, enabled, text,
                     text_role=QPalette.NoRole):
        # 通过 QSS 绘制回调获取伪状态颜色，widget.palette() 不包含 hover 颜色。
        if text == " ":
            self.button._paint_glyph(painter, palette.color(QPalette.ButtonText))
        else:
            super().drawItemText(painter, rect, flags, palette, enabled, text, text_role)


class _CaptionButton(QPushButton):
    def __init__(self, glyph, object_name, accessible_name, parent):
        super().__init__(parent)
        self.glyph = glyph
        self._caption_style = _CaptionStyle(self)
        self.setStyle(self._caption_style)
        self.setObjectName(object_name)
        self.setProperty("caption", True)
        self.setFixedSize(42, 40)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAccessibleName(accessible_name)
        self.setToolTip(accessible_name)

    def paintEvent(self, event):
        option = QStyleOptionButton()
        self.initStyleOption(option)
        # 仅在绘制选项中传入占位空格，不设置按钮文字或影响无障碍名称。
        option.text = " "
        painter = QPainter(self)
        self.style().drawControl(QStyle.CE_PushButton, option, painter, self)

    def _paint_glyph(self, painter, color):
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(color, 1.0))
        painter.setBrush(Qt.NoBrush)
        x, y = (self.width() - 10) / 2, (self.height() - 10) / 2
        if self.glyph == "minimize":
            painter.drawLine(QPointF(x, y + 5), QPointF(x + 10, y + 5))
        elif self.glyph == "maximize":
            painter.drawRect(QRectF(x + 0.5, y + 0.5, 9, 9))
        elif self.glyph == "restore":
            painter.drawRect(QRectF(x + 0.5, y + 3.5, 6, 6))
            painter.drawPolyline([QPointF(x + 3.5, y + 3.5), QPointF(x + 3.5, y + 0.5),
                                  QPointF(x + 9.5, y + 0.5), QPointF(x + 9.5, y + 6.5),
                                  QPointF(x + 6.5, y + 6.5)])
        else:
            painter.drawLine(QPointF(x + 0.5, y + 0.5), QPointF(x + 9.5, y + 9.5))
            painter.drawLine(QPointF(x + 9.5, y + 0.5), QPointF(x + 0.5, y + 9.5))
        painter.restore()


class _TitleBar(QWidget):
    def __init__(self, window):
        super().__init__(window)
        self.setObjectName("titleBar")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedHeight(40)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 0, 0, 0)
        layout.setSpacing(0)
        self.title_label = QLabel(self)
        self.title_label.setObjectName("titleBarLabel")
        self.title_label.setTextFormat(Qt.PlainText)
        self.title_label.setAccessibleName("窗口标题")
        self.title_label.setTextInteractionFlags(Qt.NoTextInteraction)
        self.title_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.title_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        layout.addWidget(self.title_label, 1)
        self.actions_layout = QHBoxLayout()
        self.actions_layout.setContentsMargins(0, 0, 0, 0)
        self.actions_layout.setSpacing(0)
        layout.addLayout(self.actions_layout)
        self.minimize_button = _CaptionButton("minimize", "captionMinimize", "最小化", self)
        self.maximize_button = _CaptionButton("maximize", "captionMaximize", "最大化", self)
        self.close_button = _CaptionButton("close", "captionClose", "关闭", self)
        for button in (self.minimize_button, self.maximize_button, self.close_button):
            layout.addWidget(button)
        self.minimize_button.clicked.connect(window.showMinimized)
        self.maximize_button.clicked.connect(window._toggle_maximized)
        self.close_button.clicked.connect(window.close)

    def is_caption(self, point):
        if not self.rect().contains(point):
            return False
        child = self.childAt(point)
        # 标题和空白可拖动；其它所有子控件（包括嵌套操作控件）均保留客户区行为。
        return child is None or child is self.title_label

    def sync_state(self):
        maximized = self.window().isMaximized()
        button = self.maximize_button
        button.glyph = "restore" if maximized else "maximize"
        text = "还原" if maximized else "最大化"
        button.setAccessibleName(text)
        button.setToolTip(text)
        button.update()

    def mouseDoubleClickEvent(self, event):
        # 原生 HTCAPTION 双击不产生此客户区事件，避免双重切换。
        if event.button() == Qt.LeftButton and self.is_caption(event.position().toPoint()):
            self.window()._toggle_maximized()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class ChromeWindow(QWidget):
    """带集成标题栏的顶层 QWidget；业务窗口继续拥有自己的 closeEvent。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._native = None
        self._configured_hwnd = None
        self._configuring_frame = False
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint | Qt.WindowSystemMenuHint
                            | Qt.WindowMinimizeButtonHint | Qt.WindowMaximizeButtonHint
                            | Qt.WindowCloseButtonHint)
        self.chrome_layout = QVBoxLayout(self)
        self.chrome_layout.setContentsMargins(0, 0, 0, 0)
        self.chrome_layout.setSpacing(0)
        self.title_bar = _TitleBar(self)
        self.chrome_layout.addWidget(self.title_bar)
        if QApplication.platformName() == "windows":
            self._native = _WindowsFrame()
            self.winId()  # 在显示前配置，避免原生标题栏闪烁；WinIdChange 负责应用样式。
            self._ensure_native_frame()

    def _toggle_maximized(self):
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    def _ensure_native_frame(self):
        if self._native is None or self._configuring_frame:
            return
        self._configuring_frame = True
        try:
            hwnd = int(self.winId())
            if hwnd != self._configured_hwnd:
                self._native.configure(hwnd)
                self._configured_hwnd = hwnd
        finally:
            self._configuring_frame = False

    def event(self, event):
        result = super().event(event)
        if event.type() == QEvent.WinIdChange and getattr(self, "_native", None) is not None:
            # 销毁 HWND 时不要通过 winId() 意外重新创建窗口。
            if self.internalWinId():
                self._ensure_native_frame()
            else:
                self._configured_hwnd = None
        if event.type() == QEvent.WindowStateChange and hasattr(self, "title_bar"):
            self.title_bar.sync_state()
        return result

    def _hit_test(self, point, border=(6, 6), maximized=None):
        if not self.rect().contains(point):
            return HTCLIENT
        maximized = self.isMaximized() if maximized is None else maximized
        if not maximized:
            x, y = point.x(), point.y()
            left, right = x < border[0], x >= self.width() - border[0]
            top, bottom = y < border[1], y >= self.height() - border[1]
            if top and left:
                return HTTOPLEFT
            if top and right:
                return HTTOPRIGHT
            if bottom and left:
                return HTBOTTOMLEFT
            if bottom and right:
                return HTBOTTOMRIGHT
            if left:
                return HTLEFT
            if right:
                return HTRIGHT
            if top:
                return HTTOP
            if bottom:
                return HTBOTTOM
        if self.title_bar.is_caption(self.title_bar.mapFrom(self, point)):
            return HTCAPTION
        return HTCLIENT

    def nativeEvent(self, event_type, message):
        native = getattr(self, "_native", None)
        if native is None or event_type != b"windows_generic_MSG":
            return super().nativeEvent(event_type, message)
        msg = _MSG.from_address(int(message))
        hwnd = msg.hwnd
        if msg.message == WM_NCCALCSIZE:
            # wParam=TRUE 的 NCCALCSIZE_PARAMS 首字段同样是 RECT。
            if msg.lParam and native.IsZoomed(hwnd):
                monitor = native.monitor_info(hwnd)
                if monitor is not None:
                    rect = RECT.from_address(msg.lParam)
                    rect.left, rect.top = monitor.rcWork.left, monitor.rcWork.top
                    rect.right, rect.bottom = monitor.rcWork.right, monitor.rcWork.bottom
            return True, 0
        if msg.message == WM_GETMINMAXINFO and msg.lParam:
            # 保留系统默认 tracking 限制，再补充 Qt 最小/最大尺寸及当前显示器工作区。
            native.DefWindowProcW(hwnd, msg.message, msg.wParam, msg.lParam)
            info = MINMAXINFO.from_address(msg.lParam)
            monitor = native.monitor_info(hwnd)
            if monitor is not None:
                work, screen = monitor.rcWork, monitor.rcMonitor
                info.ptMaxPosition = POINT(work.left - screen.left, work.top - screen.top)
                info.ptMaxSize = POINT(work.right - work.left, work.bottom - work.top)
            dpr = self.devicePixelRatioF()
            minimum = self.minimumSize().expandedTo(self.minimumSizeHint())
            info.ptMinTrackSize = POINT(max(info.ptMinTrackSize.x, math.ceil(minimum.width() * dpr)),
                                        max(info.ptMinTrackSize.y, math.ceil(minimum.height() * dpr)))
            if self.maximumWidth() < 16777215:
                info.ptMaxTrackSize.x = math.ceil(self.maximumWidth() * dpr)
            if self.maximumHeight() < 16777215:
                info.ptMaxTrackSize.y = math.ceil(self.maximumHeight() * dpr)
            return True, 0
        if msg.message == WM_NCHITTEST:
            origin = POINT(0, 0)
            if native.ClientToScreen(hwnd, ctypes.byref(origin)):
                x, y = _signed_screen_point(msg.lParam)
                dpr = self.devicePixelRatioF()
                point = _physical_to_logical(x, y, origin.x, origin.y, dpr)
                return True, self._hit_test(point, native.resize_border(hwnd, dpr),
                                            bool(native.IsZoomed(hwnd)))
        if msg.message == WM_STYLECHANGING and ctypes.c_int32(msg.wParam).value == GWL_STYLE and msg.lParam:
            _STYLESTRUCT.from_address(msg.lParam).styleNew |= NATIVE_STYLE
            return True, 0
        if msg.message == WM_DWMCOMPOSITIONCHANGED:
            native.apply_shadow(hwnd)
        # 不截获原生非客户区鼠标/系统命令，让系统处理拖拽还原、双击及 Win+方向键。
        return super().nativeEvent(event_type, message)
