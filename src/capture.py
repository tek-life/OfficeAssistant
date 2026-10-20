"""
办公助手 内容投递模块 — 把当前选中内容或剪贴板一键追加到当天工作日志。

对外接口:
  grab_selection()   抓取前台窗口里选中的文本（注入 Ctrl+C）
  grab_clipboard()   读取剪贴板文本
  clean_source()     把窗口标题压缩成简短的来源标签
  format_entry()     按 "时间 + 来源" 生成 Markdown 条目
  append_to_diary()  追加写入 daily_notes 表

设计要点:
  - 只用 ctypes 调 Win32 API，不引入新依赖。
  - 抓取选中内容前先等修饰键松开，避免拼成 Ctrl+Alt+C。
  - 用剪贴板序列号判断是否真的复制到了新内容；没选中时不动原有剪贴板。
"""

import ctypes
import re
import time
from ctypes import wintypes
from datetime import datetime

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

CF_TEXT = 1
CF_UNICODETEXT = 13

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002

VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12          # Alt
VK_LWIN = 0x5B
VK_RWIN = 0x5C
VK_C = 0x43

# 来源标签最多保留的字符数（超出部分截断）
MAX_SOURCE_CHARS = 24

_ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong


# ═══════════════════════════════════════════════════════════
# Win32 结构体（尺寸必须与系统一致，否则 SendInput 会静默失败）
# ═══════════════════════════════════════════════════════════

class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", _ULONG_PTR),
    ]


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", _ULONG_PTR),
    ]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", _KEYBDINPUT), ("mi", _MOUSEINPUT), ("hi", _HARDWAREINPUT)]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowTextLengthW.argtypes = (wintypes.HWND,)
user32.GetWindowTextLengthW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
user32.GetWindowTextW.restype = ctypes.c_int
user32.GetAsyncKeyState.argtypes = (ctypes.c_int,)
user32.GetAsyncKeyState.restype = wintypes.SHORT
user32.GetClipboardSequenceNumber.restype = wintypes.DWORD
user32.IsClipboardFormatAvailable.argtypes = (wintypes.UINT,)
user32.IsClipboardFormatAvailable.restype = wintypes.BOOL
user32.OpenClipboard.argtypes = (wintypes.HWND,)
user32.OpenClipboard.restype = wintypes.BOOL
user32.CloseClipboard.restype = wintypes.BOOL
user32.GetClipboardData.argtypes = (wintypes.UINT,)
user32.GetClipboardData.restype = wintypes.HANDLE
user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(_INPUT), ctypes.c_int)
user32.SendInput.restype = wintypes.UINT

kernel32.GlobalLock.argtypes = (wintypes.HANDLE,)
kernel32.GlobalLock.restype = wintypes.LPVOID
kernel32.GlobalUnlock.argtypes = (wintypes.HANDLE,)
kernel32.GlobalUnlock.restype = wintypes.BOOL


# ═══════════════════════════════════════════════════════════
# 剪贴板 / 前台窗口
# ═══════════════════════════════════════════════════════════

def _open_clipboard(retries: int = 4) -> bool:
    """剪贴板可能正被其他程序占用，稍等重试。"""
    for _ in range(retries):
        if user32.OpenClipboard(None):
            return True
        time.sleep(0.03)
    return False


def read_clipboard_text() -> str:
    """读取剪贴板文本，没有文本时返回空字符串。"""
    if not _open_clipboard():
        return ""
    try:
        unicode_available = bool(user32.IsClipboardFormatAvailable(CF_UNICODETEXT))
        if unicode_available:
            handle = user32.GetClipboardData(CF_UNICODETEXT)
        elif user32.IsClipboardFormatAvailable(CF_TEXT):
            handle = user32.GetClipboardData(CF_TEXT)
        else:
            return ""
        if not handle:
            return ""
        ptr = kernel32.GlobalLock(handle)
        if not ptr:
            return ""
        try:
            if unicode_available:
                return ctypes.c_wchar_p(ptr).value or ""
            raw = ctypes.c_char_p(ptr).value or b""
            return raw.decode("mbcs", errors="replace")
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


def foreground_title() -> str:
    """当前前台窗口的标题，取不到时返回空字符串。"""
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return ""
    length = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(max(length + 1, 2))
    user32.GetWindowTextW(hwnd, buf, len(buf))
    return buf.value or ""


def clean_source(title: str, fallback: str = "剪贴板") -> str:
    """把窗口标题压缩成简短的来源标签。"""
    text = re.sub(r"\s+", " ", (title or "").strip())
    if not text:
        return fallback
    if len(text) > MAX_SOURCE_CHARS:
        text = text[:MAX_SOURCE_CHARS].rstrip() + "…"
    return text


# ═══════════════════════════════════════════════════════════
# 抓取选中内容
# ═══════════════════════════════════════════════════════════

def modifiers_pressed() -> bool:
    """Ctrl / Alt / Shift / Win 是否仍被按住。"""
    for vk in (VK_CONTROL, VK_MENU, VK_SHIFT, VK_LWIN, VK_RWIN):
        if user32.GetAsyncKeyState(vk) & 0x8000:
            return True
    return False


def wait_modifiers_released(timeout: float = 1.2) -> bool:
    """等待热键修饰键松开，否则注入的 C 会拼成 Ctrl+Alt+C。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not modifiers_pressed():
            return True
        time.sleep(0.01)
    return False


def _key_event(vk: int, key_up: bool = False) -> _INPUT:
    event = _INPUT(type=INPUT_KEYBOARD)
    event.u.ki.wVk = vk
    event.u.ki.dwFlags = KEYEVENTF_KEYUP if key_up else 0
    return event


def send_ctrl_c() -> int:
    """注入一次 Ctrl+C，返回实际发送的事件数。"""
    events = (_INPUT * 4)(
        _key_event(VK_CONTROL),
        _key_event(VK_C),
        _key_event(VK_C, key_up=True),
        _key_event(VK_CONTROL, key_up=True),
    )
    return user32.SendInput(4, events, ctypes.sizeof(_INPUT))


def grab_selection(timeout: float = 0.9) -> tuple:
    """抓取前台窗口的选中文本，返回 (文本, 来源标签)。

    没有选中内容时返回空文本，且不破坏剪贴板原有内容。
    """
    source = clean_source(foreground_title())
    wait_modifiers_released()
    before = user32.GetClipboardSequenceNumber()
    send_ctrl_c()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if user32.GetClipboardSequenceNumber() != before:
            time.sleep(0.05)     # 等目标程序把内容写入完成
            return read_clipboard_text(), source
        time.sleep(0.02)
    return "", source


def grab_clipboard() -> tuple:
    """直接读取剪贴板文本，返回 (文本, 来源标签)。"""
    return read_clipboard_text(), clean_source(foreground_title())


# ═══════════════════════════════════════════════════════════
# 生成日志条目 & 写入
# ═══════════════════════════════════════════════════════════

def _content_lines(text: str) -> list:
    normalized = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    return [line.strip() for line in normalized.split("\n") if line.strip()]


def format_entry(text: str, source: str, now: datetime = None) -> str:
    """生成条目：首行 "HH:MM [来源] 内容"，其余行作为子项。"""
    lines = _content_lines(text)
    if not lines:
        return ""
    stamp = (now or datetime.now()).strftime("%H:%M")
    head = f"- {stamp} [{source}] {lines[0]}"
    if len(lines) == 1:
        return head
    body = "\n".join(f"  - {line}" for line in lines[1:])
    return f"{head}\n{body}"


def append_to_diary(db, text: str, source: str, now: datetime = None):
    """把内容追加到当天日志，返回 (日期, 条目)；内容为空时返回 None。"""
    moment = now or datetime.now()
    entry = format_entry(text, source, moment)
    if not entry:
        return None
    date_str = moment.strftime("%Y-%m-%d")
    db.append_daily_note(date_str, entry)
    return date_str, entry
