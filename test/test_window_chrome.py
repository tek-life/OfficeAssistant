"""仅创建临时 Qt 窗口；不导入应用、不访问用户数据库。"""

import ctypes
import unittest

import shiboken6
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QEnterEvent, QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QWidget

import window_chrome as chrome


class ChromeWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.windows = []
        self.addCleanup(self.destroy_windows)
        self.window = self.make_window()

    def make_window(self, cls=None):
        window = (cls or chrome.ChromeWindow)()
        self.windows.append(window)
        window.title_bar.title_label.setText("办公助手 <标题>")
        content = QWidget(window)
        content.setMinimumSize(180, 80)
        window.chrome_layout.addWidget(content, 1)
        window.resize(560, 320)
        window.show()
        self.app.processEvents()
        return window

    def destroy_windows(self):
        for window in self.windows:
            shiboken6.delete(window)
        self.app.processEvents()

    def click(self, button):
        QTest.mouseClick(button, Qt.LeftButton)
        self.app.processEvents()

    def test_titlebar_contract_and_minimum_layout(self):
        window, bar = self.window, self.window.title_bar
        layout = window.chrome_layout
        self.assertIs(layout, window.layout())
        self.assertIs(layout.itemAt(0).widget(), bar)
        self.assertEqual(layout.spacing(), 0)
        self.assertEqual(layout.contentsMargins().left(), 0)
        self.assertEqual(layout.contentsMargins().top(), 0)
        self.assertEqual(layout.contentsMargins().right(), 0)
        self.assertEqual(layout.contentsMargins().bottom(), 0)
        self.assertEqual(bar.objectName(), "titleBar")
        self.assertIsInstance(bar.title_label, QLabel)
        self.assertEqual(bar.title_label.objectName(), "titleBarLabel")
        self.assertEqual(bar.title_label.textFormat(), Qt.PlainText)
        self.assertTrue(bar.title_label.accessibleName())
        self.assertEqual(bar.height(), 40)
        self.assertEqual(bar.title_label.x(), 16)
        self.assertEqual(bar.width(), window.width())
        for name, button, accessible in (
            ("captionMinimize", bar.minimize_button, "最小化"),
            ("captionMaximize", bar.maximize_button, "最大化"),
            ("captionClose", bar.close_button, "关闭"),
        ):
            self.assertEqual(button.objectName(), name)
            self.assertTrue(button.property("caption"))
            self.assertEqual((button.width(), button.height()), (42, 40))
            self.assertEqual(button.text(), "")
            self.assertEqual(button.accessibleName(), accessible)
            self.assertEqual(button.toolTip(), accessible)
            self.assertNotEqual(button.focusPolicy(), Qt.NoFocus)
        bar.title_label.setText("超长标题" * 100)
        window.resize(230, 180)
        self.app.processEvents()
        self.assertLessEqual(window.width(), 230)
        self.assertEqual(bar.close_button.geometry().right(), bar.width() - 1)
        self.assertGreaterEqual(bar.minimize_button.x(), 16)
        self.assertGreaterEqual(window.minimumSizeHint().height(), 120)

    def test_action_layout_precedes_caption_controls(self):
        bar = self.window.title_bar
        button = QPushButton("打开日历")
        bar.actions_layout.addWidget(button)
        self.app.processEvents()
        self.assertLess(button.mapTo(bar, QPoint()).x() + button.width(),
                        bar.minimize_button.x() + 1)
        point = button.mapTo(self.window, button.rect().center())
        self.assertEqual(self.window._hit_test(point), chrome.HTCLIENT)
        # 嵌套在按钮中的标签也不能误判成可拖拽标题。
        label = QLabel("日历", button)
        label.setGeometry(button.rect())
        label.show()
        self.assertEqual(self.window._hit_test(point), chrome.HTCLIENT)
        self.click(button)
        self.assertFalse(self.window.isMaximized())

    def test_caption_blank_label_and_content_hit_tests(self):
        window, bar = self.window, self.window.title_bar
        self.assertEqual(window._hit_test(QPoint(12, 20)), chrome.HTCAPTION)
        point = bar.title_label.mapTo(window, bar.title_label.rect().center())
        self.assertEqual(window._hit_test(point), chrome.HTCAPTION)
        self.assertEqual(window._hit_test(QPoint(100, 100)), chrome.HTCLIENT)
        for button in (bar.minimize_button, bar.maximize_button, bar.close_button):
            point = button.mapTo(window, button.rect().center())
            self.assertEqual(window._hit_test(point), chrome.HTCLIENT)

    def test_resize_edges_corners_and_maximized_exclusion(self):
        window = self.window
        w, h = window.width(), window.height()
        cases = (
            (QPoint(1, 1), chrome.HTTOPLEFT),
            (QPoint(w - 2, 1), chrome.HTTOPRIGHT),
            (QPoint(1, h - 2), chrome.HTBOTTOMLEFT),
            (QPoint(w - 2, h - 2), chrome.HTBOTTOMRIGHT),
            (QPoint(w // 2, 1), chrome.HTTOP),
            (QPoint(w // 2, h - 2), chrome.HTBOTTOM),
            (QPoint(1, h // 2), chrome.HTLEFT),
            (QPoint(w - 2, h // 2), chrome.HTRIGHT),
        )
        for point, expected in cases:
            with self.subTest(point=point):
                self.assertEqual(window._hit_test(point), expected)
        window.showMaximized()
        self.app.processEvents()
        self.assertEqual(window._hit_test(QPoint(1, 1)), chrome.HTCAPTION)
        self.assertEqual(window._hit_test(QPoint(1, 100)), chrome.HTCLIENT)

    def test_minimize_maximize_restore_and_external_state_changes(self):
        window, bar = self.window, self.window.title_bar
        self.click(bar.minimize_button)
        self.assertTrue(window.isMinimized())
        window.showNormal()
        self.app.processEvents()
        self.click(bar.maximize_button)
        self.assertTrue(window.isMaximized())
        self.assertEqual(bar.maximize_button.toolTip(), "还原")
        self.assertEqual(bar.maximize_button.accessibleName(), "还原")
        self.assertEqual(bar.maximize_button.glyph, "restore")
        self.click(bar.maximize_button)
        self.assertFalse(window.isMaximized())
        self.assertEqual(bar.maximize_button.glyph, "maximize")
        window.showMaximized()
        self.app.processEvents()
        self.assertEqual(bar.maximize_button.accessibleName(), "还原")
        window.showNormal()
        self.app.processEvents()
        self.assertEqual(bar.maximize_button.toolTip(), "最大化")
        self.assertEqual(bar.maximize_button.glyph, "maximize")

    def test_keyboard_activation(self):
        button = self.window.title_bar.maximize_button
        self.window.activateWindow()
        button.setFocus()
        self.app.processEvents()
        self.assertTrue(button.hasFocus())
        QTest.keyClick(button, Qt.Key_Space)
        self.app.processEvents()
        self.assertTrue(self.window.isMaximized())

    def test_qt_double_click_toggles_only_titlebar(self):
        bar = self.window.title_bar
        for expected in (True, False):
            QTest.mouseDClick(bar, Qt.LeftButton, pos=QPoint(20, 20))
            self.app.processEvents()
            self.assertEqual(self.window.isMaximized(), expected)
        QTest.mouseDClick(bar, Qt.RightButton, pos=QPoint(20, 20))
        self.assertFalse(self.window.isMaximized())

    def test_caption_icon_uses_qss_hover_foreground(self):
        button = self.window.title_bar.close_button
        self.window.setStyleSheet(
            "QPushButton[caption=\"true\"] { background: #ffffff; color: #101010; border: 0; }"
            "QPushButton#captionClose:hover { background: #cc0000; color: #ffffff; }"
        )
        self.assertTrue(QTest.qWaitForWindowExposed(self.window))
        self.app.sendEvent(button, QEvent(QEvent.Leave))
        self.assertFalse(button.underMouse())

        def icon_colors(image):
            dpr = image.devicePixelRatio()
            return [image.pixelColor(x, y)
                    for x in range(round(16 * dpr), round(26 * dpr))
                    for y in range(round(15 * dpr), round(25 * dpr))]

        normal = button.grab().toImage()
        self.assertLess(min(color.lightness() for color in icon_colors(normal)), 150)
        # 高 DPI 的 offscreen 屏幕可能小于窗口，直接派发悬停事件以测试绘制。
        local = QPointF(button.rect().center())
        global_pos = QPointF(button.mapToGlobal(local.toPoint()))
        self.app.sendEvent(button, QEnterEvent(local, local, global_pos))
        self.app.sendEvent(button, QMouseEvent(QEvent.MouseMove, local, global_pos,
                           Qt.NoButton, Qt.NoButton, Qt.NoModifier))
        self.assertTrue(button.underMouse())
        hovered = button.grab().toImage()
        # 对角线抗锯齿会混合背景，不要求某一个物理像素恰好纯白。
        self.assertTrue(any(color.red() > 225 and color.green() > 100 and color.blue() > 100
                            for color in icon_colors(hovered)))
        dpr = hovered.devicePixelRatio()
        self.assertEqual(hovered.pixelColor(round(2 * dpr), round(20 * dpr)).name(), "#cc0000")

    def test_close_respects_business_refusal(self):
        class RefusingWindow(chrome.ChromeWindow):
            refused = 0

            def closeEvent(self, event):
                self.refused += 1
                event.ignore()

        window = self.make_window(RefusingWindow)
        self.click(window.title_bar.close_button)
        self.assertEqual(window.refused, 1)
        self.assertTrue(window.isVisible())
        self.click(self.window.title_bar.close_button)
        self.assertFalse(self.window.isVisible())

    def test_signed_coordinates_and_local_dpi_mapping(self):
        encoded = ((-240 & 0xffff) << 16) | (-1920 & 0xffff)
        self.assertEqual(chrome._signed_screen_point(encoded), (-1920, -240))
        self.assertEqual(chrome._signed_screen_point(encoded - (1 << 32)),
                         (-1920, -240))
        # 不能把整个全局坐标除以 DPR：先减去当前窗口的物理客户区原点。
        self.assertEqual(chrome._physical_to_logical(-1770, -165, -1920, -240, 1.5),
                         QPoint(100, 50))
        self.assertEqual(chrome._physical_to_logical(215, 130, 200, 100, 1.25),
                         QPoint(12, 24))

    def test_native_backend_only_for_windows_qpa(self):
        self.assertEqual(self.window._native is not None,
                         self.app.platformName() == "windows")

    def test_native_windows_message_probes(self):
        if self.app.platformName() != "windows":
            self.skipTest("需 QT_QPA_PLATFORM=windows 才能探测 HWND")
        native = self.window._native
        hwnd = int(self.window.winId())
        self.assertEqual(native.GetWindowLongPtrW(hwnd, chrome.GWL_STYLE)
                         & chrome.NATIVE_STYLE, chrome.NATIVE_STYLE)
        send = ctypes.WinDLL("user32", use_last_error=True).SendMessageW
        send.argtypes = [ctypes.c_void_p, ctypes.c_uint32,
                         ctypes.c_size_t, ctypes.c_ssize_t]
        send.restype = ctypes.c_ssize_t
        origin = chrome.POINT(0, 0)
        self.assertTrue(native.ClientToScreen(hwnd, ctypes.byref(origin)))
        dpr = self.window.devicePixelRatioF()
        for logical, expected in ((QPoint(100, 20), chrome.HTCAPTION),
                                  (QPoint(100, 100), chrome.HTCLIENT),
                                  (QPoint(1, 100), chrome.HTLEFT)):
            x, y = origin.x + round(logical.x() * dpr), origin.y + round(logical.y() * dpr)
            result = send(hwnd, chrome.WM_NCHITTEST, 0, ((y & 0xffff) << 16) | (x & 0xffff))
            self.assertEqual(result, expected)
        rect = chrome.RECT(10, 20, 510, 320)
        self.assertEqual(send(hwnd, chrome.WM_NCCALCSIZE, 0, ctypes.addressof(rect)), 0)
        self.assertEqual((rect.left, rect.top, rect.right, rect.bottom), (10, 20, 510, 320))
        info = chrome.MINMAXINFO()
        send(hwnd, chrome.WM_GETMINMAXINFO, 0, ctypes.addressof(info))
        work = native.monitor_info(hwnd).rcWork
        self.assertEqual(info.ptMaxSize.x, work.right - work.left)
        self.assertEqual(info.ptMaxSize.y, work.bottom - work.top)
        self.assertGreaterEqual(info.ptMinTrackSize.x, round(self.window.minimumSizeHint().width() * dpr))
        button = QPushButton("日历")
        self.window.title_bar.actions_layout.addWidget(button)
        self.app.processEvents()
        for widget in (button, self.window.title_bar.close_button):
            logical = widget.mapTo(self.window, widget.rect().center())
            x, y = origin.x + round(logical.x() * dpr), origin.y + round(logical.y() * dpr)
            result = send(hwnd, chrome.WM_NCHITTEST, 0, ((y & 0xffff) << 16) | (x & 0xffff))
            self.assertEqual(result, chrome.HTCLIENT)
        # 系统非客户区双击必须只切换一次，不能与 Qt 双击处理相互抵消。
        x, y = origin.x + round(100 * dpr), origin.y + round(20 * dpr)
        caption_pos = ((y & 0xffff) << 16) | (x & 0xffff)
        send(hwnd, 0x00A3, chrome.HTCAPTION, caption_pos)  # WM_NCLBUTTONDBLCLK
        self.app.processEvents()
        self.assertTrue(self.window.isMaximized())
        self.assertEqual(self.window.title_bar.maximize_button.accessibleName(), "还原")
        client = chrome.RECT()
        self.assertTrue(native.GetClientRect(hwnd, ctypes.byref(client)))
        self.assertEqual(client.right, work.right - work.left)
        self.assertEqual(client.bottom, work.bottom - work.top)
        maximized_origin = chrome.POINT()
        self.assertTrue(native.ClientToScreen(hwnd, ctypes.byref(maximized_origin)))
        self.assertEqual((maximized_origin.x, maximized_origin.y), (work.left, work.top))
        send(hwnd, 0x00A3, chrome.HTCAPTION, caption_pos)
        self.app.processEvents()
        self.assertFalse(self.window.isMaximized())
        # SC_MAXIMIZE / SC_RESTORE 使用与 Win+方向键相同的系统窗口状态路径。
        send(hwnd, 0x0112, 0xF030, 0)
        self.app.processEvents()
        self.assertTrue(self.window.isMaximized())
        send(hwnd, 0x0112, 0xF120, 0)
        self.app.processEvents()
        self.assertFalse(self.window.isMaximized())
        self.assertEqual(native.GetWindowLongPtrW(hwnd, chrome.GWL_STYLE)
                         & chrome.NATIVE_STYLE, chrome.NATIVE_STYLE)


if __name__ == "__main__":
    unittest.main()
