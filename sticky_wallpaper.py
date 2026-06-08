#!/usr/bin/env python3
"""
StickyWallpaper — 把每日 TODO 以便利贴风格渲染到桌面壁纸上。

用法:
  1. 安装依赖:  pip install Pillow pystray
  2. 编辑 todos.txt（程序首次运行会自动生成示例）
  3. 运行:      python sticky_wallpaper.py

todos.txt 格式:
  文件分为两个区域，以 "# TODO" 为分界：

  # 个人笔记（或任意标题）
  这里的内容不会显示在桌面上，当作个人注释/备忘。

  # TODO
  - [ ] 未完成事项
  - [x] 已完成事项
  只有 "# TODO" 下方的条目才会渲染到桌面壁纸上。
  遇到下一个 "#" 标题时停止读取。
"""

import ctypes
import json
import os
import sys
import time
import threading
import subprocess
import winreg
from ctypes import wintypes
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from PIL import Image, ImageDraw, ImageFont, ImageFilter


# ═══════════════════════════════════════════════════════════
# DPI 感知（必须在所有屏幕尺寸查询之前调用，否则缩放会导致尺寸异常）
# ═══════════════════════════════════════════════════════════
ctypes.windll.user32.SetProcessDPIAware()

# ═══════════════════════════════════════════════════════════
# 隐藏控制台窗口（用 py 运行 .py 时会自动弹出命令行，这里自动隐藏）
# 对于 --noconsole 打包的 exe，GetConsoleWindow() 返回 0，ShowWindow 是空操作
# ═══════════════════════════════════════════════════════════
_console_hwnd = ctypes.windll.kernel32.GetConsoleWindow()
if _console_hwnd:
    ctypes.windll.user32.ShowWindow(_console_hwnd, 0)  # SW_HIDE = 0


# ═══════════════════════════════════════════════════════════
# 常量 & 路径
# ═══════════════════════════════════════════════════════════

# 兼容 PyInstaller 打包后的路径
if getattr(sys, 'frozen', False):
    SCRIPT_DIR = Path(sys.executable).parent.resolve()
else:
    SCRIPT_DIR = Path(__file__).parent.resolve()
TODOS_FILE = SCRIPT_DIR / "todos.txt"
CONFIG_FILE = SCRIPT_DIR / "config.json"
BACKUP_WALLPAPER = SCRIPT_DIR / ".original_wallpaper.jpg"
RENDERED_WALLPAPER = SCRIPT_DIR / "rendered_wallpaper.bmp"

# Windows API
SPI_GETDESKWALLPAPER = 0x0073
SPI_SETDESKWALLPAPER = 0x0014
SPIF_UPDATEINIFILE = 0x01
SPIF_SENDWININICHANGE = 0x02
ERROR_ALREADY_EXISTS = 183
WAIT_OBJECT_0 = 0
WAIT_TIMEOUT = 0x00000102

# 单例标识
MUTEX_NAME = "Global\\StickyWallpaper_SingleInstance"
EVENT_NAME = "Global\\StickyWallpaper_Activate"


# ═══════════════════════════════════════════════════════════
# 字体查找
# ═══════════════════════════════════════════════════════════

def _find_font(names: list, size: int) -> ImageFont.FreeTypeFont:
    """在 Windows 字体目录中查找第一个存在的字体并返回。"""
    windir = os.environ.get("WINDIR", r"C:\Windows")
    for name in names:
        path = os.path.join(windir, "Fonts", name)
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def make_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    if bold:
        return _find_font(["msyhbd.ttc", "msyh.ttc", "simhei.ttf", "simsun.ttc"], size)
    return _find_font(["msyh.ttc", "simhei.ttf", "simsun.ttc"], size)


# ═══════════════════════════════════════════════════════════
# 数据模型
# ═══════════════════════════════════════════════════════════

@dataclass
class TodoItem:
    text: str
    done: bool


def parse_todos(path: Path) -> tuple:
    """
    解析 todos.txt，返回 (title, [TodoItem])。

    规则：只读取第一个 "# TODO" 标题下方的条目。
    该标题之前的所有内容视为个人笔记/注释，不会显示在桌面上。
    遇到下一个 "#" 标题时停止读取。
    """
    if not path.exists():
        return "今日待办", []

    title = "今日待办"
    items: List[TodoItem] = []
    in_target_section = False

    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line:
                continue

            # 检测标题行
            if line.startswith("#"):
                heading = line.lstrip("#").strip()
                if heading.upper() == "TODO":
                    # 进入目标分区
                    in_target_section = True
                    # 如果 # TODO 后面跟了额外文字，用它作为标题
                    # 例如 "# TODO 今日待办" → 标题为 "今日待办"
                    continue
                elif in_target_section:
                    # 遇到下一个标题，停止读取
                    break
                continue

            if not in_target_section:
                continue

            done = False
            for prefix in ("- [x] ", "- [X] ", "* [x] ", "* [X] "):
                if line.startswith(prefix):
                    done = True
                    line = line[len(prefix):]
                    break
            else:
                for prefix in ("- [ ] ", "* [ ] "):
                    if line.startswith(prefix):
                        line = line[len(prefix):]
                        break
                else:
                    # 纯文本行，视为未完成
                    pass
            items.append(TodoItem(text=line, done=done))

    return title, items


# ═══════════════════════════════════════════════════════════
# 便利贴渲染
# ═══════════════════════════════════════════════════════════

class StickyNoteRenderer:
    """单张便利贴的渲染器。"""

    PALETTE = [
        {"bg": "#FFF9C4", "accent": "#F9A825"},   # 暖黄
        {"bg": "#F8BBD0", "accent": "#C2185B"},   # 粉色
        {"bg": "#C8E6C9", "accent": "#388E3C"},   # 薄荷绿
        {"bg": "#BBDEFB", "accent": "#1565C0"},   # 天蓝
        {"bg": "#FFE0B2", "accent": "#E65100"},   # 橘色
        {"bg": "#E1BEE7", "accent": "#7B1FA2"},   # 薰衣草
    ]

    def __init__(self, font_title, font_item, font_date, font_small):
        self.ft = font_title
        self.fi = font_item
        self.fd = font_date
        self.fs = font_small

    # --- 圆角矩形 ---
    @staticmethod
    def _rrect(draw: ImageDraw.Draw, xy, radius, fill):
        draw.rounded_rectangle(xy, radius=radius, fill=fill)

    # --- 折角效果 ---
    @staticmethod
    def _draw_fold(draw: ImageDraw.Draw, x2, y2, fold, bg_rgb):
        """在右下角画一个三角形折角。"""
        darker = tuple(max(0, c - 45) for c in bg_rgb)
        lighter = tuple(max(0, c - 20) for c in bg_rgb)
        # 折痕阴影
        draw.polygon([(x2 - fold, y2 - fold), (x2, y2 - fold), (x2, y2)], fill=darker)
        # 翻折面
        inner_fold = int(fold * 0.75)
        draw.polygon(
            [(x2 - inner_fold, y2 - inner_fold), (x2, y2 - inner_fold), (x2, y2)],
            fill=lighter,
        )

    # --- 文字换行辅助 ---
    @staticmethod
    def _wrap_text(text: str, font, max_width: int, draw: ImageDraw.Draw) -> list:
        """将文本按像素宽度拆分成多行。"""
        lines = []
        current = ""
        for ch in text:
            test = current + ch
            bbox = draw.textbbox((0, 0), test, font=font)
            if bbox[2] - bbox[0] > max_width and current:
                lines.append(current)
                current = ch
            else:
                current = test
        if current:
            lines.append(current)
        return lines if lines else [""]

    # --- 绘制单张便利贴 ---
    def draw(self, base: Image.Image, title, items: list, date_str: str,
             position: tuple, color_idx: int = 0, rotation: float = -1.5):
        note_w = 600  # 宽度增加 50%
        pad = 16
        x_margin = 22       # 内容区左内边距
        item_indent = 30    # 复选框后的文字缩进
        line_height = 30    # 单行高度
        wrap_indent = 8     # 换行后的额外缩进

        # ── 先计算实际需要的内容高度 ──
        # 用临时画布测量文字
        tmp = Image.new("RGBA", (1, 1))
        td = ImageDraw.Draw(tmp)
        content_w = note_w - x_margin * 2 - item_indent  # 文字可用宽度

        # 预计算每个 item 的行数
        wrapped_items = []
        for item in items[:12]:  # 最多显示 12 条
            lines = self._wrap_text(item.text, self.fi, content_w, td)
            wrapped_items.append((item, lines))

        # 头部固定区域: 标题 + 日期 + 分割线
        header_h = 42 + 24 + 14  # title + date + line+gap = 80
        # 条目区域
        items_h = 0
        for item, lines in wrapped_items:
            items_h += line_height * len(lines)
        # 底部留白
        footer_h = 30
        note_h = header_h + items_h + footer_h + 28  # 28 = 顶部内边距
        note_h = max(note_h, 280)  # 最小高度

        # ── 创建画布 ──
        cw, ch = note_w + pad * 2, note_h + pad * 2
        canvas = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
        d = ImageDraw.Draw(canvas)

        color = self.PALETTE[color_idx % len(self.PALETTE)]
        bg_rgb = tuple(int(color["bg"][i:i + 2], 16) for i in (1, 3, 5))
        accent_rgb = tuple(int(color["accent"][i:i + 2], 16) for i in (1, 3, 5))

        # 阴影
        self._rrect(d, (pad + 6, pad + 6, pad + note_w + 6, pad + note_h + 6), 10,
                     (0, 0, 0, 35))
        # 主体
        self._rrect(d, (pad, pad, pad + note_w, pad + note_h), 10,
                     bg_rgb + (250,))
        # 顶部色条
        self._rrect(d, (pad, pad, pad + note_w, pad + 8), 4,
                     accent_rgb + (255,))
        # 折角
        self._draw_fold(d, pad + note_w, pad + note_h, 22, bg_rgb)

        # 内容区
        x0 = pad + x_margin
        y_cursor = pad + 28

        # 标题
        d.text((x0, y_cursor), title, fill=(50, 50, 50, 255), font=self.ft)
        y_cursor += 42

        # 日期
        d.text((x0, y_cursor), date_str, fill=(140, 140, 140, 230), font=self.fs)
        y_cursor += 24

        # 分割线
        d.line([(x0, y_cursor), (x0 + note_w - 50, y_cursor)],
               fill=(190, 190, 190, 180), width=1)
        y_cursor += 14

        # 条目（支持换行）
        text_x = x0 + item_indent
        for item, lines in wrapped_items:
            if y_cursor > pad + note_h - footer_h:
                remaining = len(items) - items.index(item)
                d.text((x0, y_cursor), f"... 还有 {remaining} 项",
                       fill=(160, 160, 160, 200), font=self.fs)
                break

            if item.done:
                symbol = "☑"
                tcolor = (170, 170, 170, 180)
            else:
                symbol = "☐"
                tcolor = (60, 60, 60, 255)

            # 复选框
            d.text((x0, y_cursor), symbol, fill=accent_rgb + (255,), font=self.fi)
            # 第一行文字
            d.text((text_x, y_cursor), lines[0], fill=tcolor, font=self.fi)
            y_cursor += line_height
            # 后续换行（缩进对齐第一行文字）
            for extra_line in lines[1:]:
                d.text((text_x + wrap_indent, y_cursor), extra_line,
                       fill=tcolor, font=self.fi)
                y_cursor += line_height

        # 旋转
        if rotation:
            canvas = canvas.rotate(rotation, expand=True,
                                   resample=Image.BICUBIC)

        # 粘贴
        base.paste(canvas, position, canvas)


# ═══════════════════════════════════════════════════════════
# 壁纸管理
# ═══════════════════════════════════════════════════════════

class WallpaperManager:
    """Windows 壁纸的读取、设置、还原。"""

    @staticmethod
    def get_current() -> Optional[str]:
        try:
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Control Panel\Desktop", 0, winreg.KEY_READ,
            )
            path, _ = winreg.QueryValueEx(key, "WallPaper")
            winreg.CloseKey(key)
            return path if path else None
        except Exception:
            return None

    @staticmethod
    def set_image(path: str):
        # 填充模式
        try:
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Control Panel\Desktop", 0, winreg.KEY_WRITE,
            )
            winreg.SetValueEx(key, "WallpaperStyle", 0, winreg.REG_SZ, "10")
            winreg.SetValueEx(key, "TileWallpaper", 0, winreg.REG_SZ, "0")
            winreg.CloseKey(key)
        except Exception:
            pass

        ctypes.windll.user32.SystemParametersInfoW(
            SPI_SETDESKWALLPAPER, 0, str(path),
            SPIF_UPDATEINIFILE | SPIF_SENDWININICHANGE,
        )

    @staticmethod
    def get_screen_size() -> tuple:
        user32 = ctypes.windll.user32
        return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)


# ═══════════════════════════════════════════════════════════
# 主程序
# ═══════════════════════════════════════════════════════════

class StickyWallpaper:
    """应用主类：串联 todo 解析 → 渲染 → 壁纸设置 → 系统托盘。"""

    DEFAULT_CONFIG = {
        "refresh_minutes": 5,
        "note_position": "top_right",
        "margin": 60,
    }

    def __init__(self):
        # _running 必须在 _acquire_singleton 之前，因为监听线程会立即访问它
        self._running = True
        self._render_lock = threading.Lock()
        self._mutex_handle = None
        self._event_handle = None
        self._acquire_singleton()
        self.config = self._load_config()
        self.wm = WallpaperManager()
        self.tray = None

        # 字体
        self.renderer = StickyNoteRenderer(
            font_title=make_font(24, bold=True),
            font_item=make_font(17),
            font_date=make_font(13),
            font_small=make_font(13),
        )

    # --- 单例保护（Named Mutex + Named Event）---
    def _acquire_singleton(self):
        """
        尝试创建命名互斥体。如果已存在，说明已有实例在运行，
        此时向已有实例发送激活事件，弹出提示后退出。
        """
        kernel32 = ctypes.windll.kernel32
        user32 = ctypes.windll.user32

        mutex = kernel32.CreateMutexW(None, True, MUTEX_NAME)
        last_error = kernel32.GetLastError()

        if last_error == ERROR_ALREADY_EXISTS:
            # ── 第二个实例：通知已有实例 ──
            event = kernel32.OpenEventW(0x0002, False, EVENT_NAME)  # EVENT_MODIFY_STATE
            if event:
                kernel32.SetEvent(event)
                kernel32.CloseHandle(event)
            if mutex:
                kernel32.ReleaseMutex(mutex)
                kernel32.CloseHandle(mutex)
            user32.MessageBoxW(
                0,
                "便利贴壁纸已经在运行中，已通知它刷新壁纸。",
                "StickyWallpaper",
                0x40,  # MB_ICONINFORMATION
            )
            sys.exit(0)

        # ── 第一个实例：创建激活事件，启动监听线程 ──
        self._mutex_handle = mutex
        self._event_handle = kernel32.CreateEventW(
            None,   # 默认安全属性
            True,   # 手动重置
            False,  # 初始未触发
            EVENT_NAME,
        )
        threading.Thread(target=self._activation_listener, daemon=True).start()

    def _activation_listener(self):
        """监听激活事件：第二次点击 exe 时，已有实例会收到信号并刷新壁纸。"""
        kernel32 = ctypes.windll.kernel32
        while self._running:
            result = kernel32.WaitForSingleObject(self._event_handle, 1000)
            if result == WAIT_OBJECT_0:
                kernel32.ResetEvent(self._event_handle)
                if self._running:
                    try:
                        self.render()
                        print("[激活] 收到唤醒信号，已刷新壁纸。")
                    except Exception as e:
                        print(f"[激活] 刷新失败: {e}")

    def _release_singleton(self):
        """释放 Mutex 和 Event 句柄。"""
        kernel32 = ctypes.windll.kernel32
        if self._event_handle:
            kernel32.CloseHandle(self._event_handle)
            self._event_handle = None
        if self._mutex_handle:
            kernel32.ReleaseMutex(self._mutex_handle)
            kernel32.CloseHandle(self._mutex_handle)
            self._mutex_handle = None

    # --- 配置 ---
    def _load_config(self) -> dict:
        cfg = dict(self.DEFAULT_CONFIG)
        if CONFIG_FILE.exists():
            try:
                cfg.update(json.loads(CONFIG_FILE.read_text(encoding="utf-8")))
            except Exception:
                pass
        return cfg

    # --- 壁纸备份（复制原始壁纸文件，而非只存路径）---
    def _save_backup(self, source_path: str):
        """将原始壁纸文件复制一份保存，避免 Bing 删除旧图后路径失效。"""
        try:
            img = Image.open(source_path)
            img.save(str(BACKUP_WALLPAPER), "JPEG", quality=95)
            print(f"[备份] 已保存原始壁纸: {source_path}")
        except Exception as e:
            print(f"[备份] 保存失败: {e}")

    # --- 示例 todos ---
    def _ensure_todos_file(self):
        if not TODOS_FILE.exists():
            sample = (
                "# 个人笔记\n"
                "这里写的内容不会显示在桌面上。\n"
                "可以用来记录想法、灵感、备忘等。\n"
                "\n"
                "# TODO\n"
                "- [ ] 完成项目报告\n"
                "- [ ] 回复邮件\n"
                "- [x] 更新工作日志\n"
                "- [ ] 准备明天会议\n"
                "- [ ] 阅读技术文章\n"
            )
            TODOS_FILE.write_text(sample, encoding="utf-8")

    # --- 核心：渲染壁纸 ---
    def render(self):
        with self._render_lock:
            self._do_render()

    def _do_render(self):
        self._ensure_todos_file()
        title, items = parse_todos(TODOS_FILE)

        sw, sh = self.wm.get_screen_size()
        current_wp = self.wm.get_current() or ""
        rendered_path = str(RENDERED_WALLPAPER)

        # 判断当前壁纸是否是我们渲染的
        is_ours = (os.path.normcase(current_wp) == os.path.normcase(rendered_path))

        if not is_ours:
            # 用户换了壁纸（如 Bing 每日更新），把新壁纸复制一份作为备份
            if current_wp and os.path.exists(current_wp):
                self._save_backup(current_wp)

        if not items:
            # TODO 列表为空，还原为干净壁纸
            print("TODO 列表为空，还原干净壁纸。")
            self.restore()
            return

        # 始终用备份文件做底图（备份是干净的原始壁纸副本）
        if BACKUP_WALLPAPER.exists():
            try:
                base = Image.open(str(BACKUP_WALLPAPER)).convert("RGB")
                base = base.resize((sw, sh), Image.LANCZOS)
            except Exception:
                base = self._gradient(sw, sh)
        else:
            base = self._gradient(sw, sh)

        base = base.convert("RGBA")

        # 日期
        now = datetime.now()
        weekdays = ["星期一", "星期二", "星期三",
                    "星期四", "星期五", "星期六",
                    "星期日"]
        date_str = f"{now.year}年{now.month}月{now.day}日  {weekdays[now.weekday()]}"

        # 位置
        note_w, note_h = 640, 500  # 含阴影边距（宽度增加后）
        margin = self.config["margin"]
        pos_map = {
            "bottom_right": (sw - note_w - margin, sh - note_h - margin),
            "bottom_left":  (margin, sh - note_h - margin),
            "top_right":    (sw - note_w - margin, margin),
            "top_left":     (margin, margin),
            "center":       ((sw - note_w) // 2, (sh - note_h) // 2),
        }
        pos = pos_map.get(self.config["note_position"], pos_map["top_right"])

        # 渲染便利贴
        self.renderer.draw(base, title, items, date_str, pos,
                           color_idx=0, rotation=-1.5)

        # 保存 & 设置
        out = base.convert("RGB")
        out.save(str(RENDERED_WALLPAPER), "BMP")
        self.wm.set_image(str(RENDERED_WALLPAPER))
        print(f"[{now:%H:%M:%S}] 壁纸已更新")

    @staticmethod
    def _gradient(w, h) -> Image.Image:
        """生成深蓝渐变作为默认底图。"""
        img = Image.new("RGB", (w, h))
        d = ImageDraw.Draw(img)
        for y in range(h):
            r = int(25 + 15 * y / h)
            g = int(25 + 25 * y / h)
            b = int(55 + 50 * y / h)
            d.line([(0, y), (w, y)], fill=(r, g, b))
        return img

    # --- 还原壁纸 ---
    def restore(self):
        if BACKUP_WALLPAPER.exists():
            self.wm.set_image(str(BACKUP_WALLPAPER))
            print("已还原壁纸。")
        else:
            print("未找到备份壁纸，无法还原。")

    # --- 编辑 todos ---
    def edit_todos(self):
        self._ensure_todos_file()
        for editor in ("notepad.exe", "code"):
            try:
                subprocess.Popen([editor, str(TODOS_FILE)])
                return
            except FileNotFoundError:
                continue
        os.startfile(str(TODOS_FILE))

    # --- 系统托盘 ---
    def _make_icon_image(self):
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle((6, 6, 58, 58), radius=8, fill="#FFF176")
        d.rectangle((6, 6, 58, 14), fill="#F9A825")
        # 小横线
        for y_off in (24, 34, 44):
            d.line([(16, y_off), (48, y_off)], fill="#888888", width=2)
        return img

    def _tray_refresh(self):
        threading.Thread(target=self.render, daemon=True).start()

    def _tray_edit(self):
        threading.Thread(target=self.edit_todos, daemon=True).start()

    def _tray_restore(self):
        threading.Thread(target=self.restore, daemon=True).start()

    def _tray_quit(self, icon=None):
        self._running = False
        if icon:
            icon.stop()
        self.restore()
        self._release_singleton()
        # 清理临时文件
        try:
            RENDERED_WALLPAPER.unlink(missing_ok=True)
        except Exception:
            pass

    def setup_tray(self):
        import pystray

        menu = pystray.Menu(
            pystray.MenuItem("刷新壁纸", self._tray_refresh, default=True),
            pystray.MenuItem("编辑 todos.txt", self._tray_edit),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("还原壁纸", self._tray_restore),
            pystray.MenuItem("退出", self._tray_quit),
        )
        self.tray = pystray.Icon("StickyWallpaper", self._make_icon_image(),
                                 "便利贴壁纸", menu)

    def _refresh_loop(self):
        interval = self.config["refresh_minutes"] * 60
        while self._running:
            try:
                self.render()
            except Exception as e:
                print(f"渲染失败: {e}")
            time.sleep(interval)

    def run(self):
        self.setup_tray()
        # 托盘图标在独立线程运行（避免阻塞主线程导致右键菜单无响应）
        self.tray.run_detached()
        # 主线程做定时刷新
        self._refresh_loop()


# ═══════════════════════════════════════════════════════════
# 入口
# ═══════════════════════════════════════════════════════════

if __name__ == "__main__":
    app = StickyWallpaper()
    try:
        app.run()
    except KeyboardInterrupt:
        app._tray_quit()
