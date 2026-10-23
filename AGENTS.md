# AGENTS.md

This file provides guidance to Qoder (qoder.com) when working with code in this repository.

## Build & Run Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run the application directly
python src/sticky_wallpaper.py

# Run the tests (adds src/ to sys.path, then runs everything in test/)
py -3 test/_run_tests.py

# Build standalone exe (requires PyInstaller) — uses the committed spec
pyinstaller OfficeAssistant.spec --noconfirm
# Output: dist\OfficeAssistant.exe

# Manual PyInstaller build (equivalent one-liner)
pyinstaller --onefile --noconsole --name OfficeAssistant src/sticky_wallpaper.py
```

## Architecture

办公助手 (OfficeAssistant) is a Windows desktop application that renders TODO items as sticky notes onto the desktop wallpaper, and additionally provides a work diary (工作日志) and a Pomodoro timer with break reminders. Data is stored in SQLite (`db/`) and settings live in its `app_settings` table.

### Repository Layout

```
src/      # all source modules (flat, imported by bare name)
test/     # unit tests + _run_tests.py runner
db/       # runtime SQLite database (sticky_wallpaper.db)
config/   # legacy config.json import source (settings now live in the db)
dist/     # PyInstaller output (OfficeAssistant.exe)
```

`src/` must be on `sys.path` for anything that imports these modules: a direct run (`python src/sticky_wallpaper.py`), the tests (`py -3 test/_run_tests.py`), and PyInstaller (`pathex=["src"]`).

### Core Components (`src/sticky_wallpaper.py`)

| Component | Lines | Responsibility |
|-----------|-------|---------------|
| DPI & Console Setup | Top | `SetProcessDPIAware()`, hide console window |
| Font Finder | ~90-103 | Searches Windows Fonts dir for CJK fonts (msyh > simhei > simsun) |
| `TodoItem` | ~107-170 | Dataclass + parser for `todos.txt` (Markdown checkbox format) |
| `StickyNoteRenderer` | ~177-346 | Pillow-based renderer: rounded rect, shadow, fold corner, text wrapping, 6-color palette |
| `WallpaperManager` | ~353-391 | Gets/sets wallpaper via `SystemParametersInfoW`, queries screen resolution |
| `OfficeAssistant` | ~398-1310 | Main app: singleton lock, config, tray icon, Pomodoro loop, hotkey loop, capture bubble, periodic refresh |

### Threading Model

- **Main thread**: Periodic wallpaper refresh loop (configurable interval)
- **Tray thread**: `pystray.Icon.run_detached()` for system tray menu
- **Pomodoro thread**: Work/break timer with per-second countdown
- **Activation listener thread**: Monitors named event for second-instance signals
- **Hotkey thread**: `RegisterHotKey` + `GetMessageW` loop that serves the global capture hotkeys
- **Qt thread**: QApplication event loop; owns all widgets (`DiaryWindow`, `TodoEditorWindow`, `CaptureBubble`)

### 内容投递 (Content Delivery)

一键把当前选中内容或剪贴板追加到当天工作日志。捕获逻辑在 `src/capture.py`（只用 `ctypes` 调 Win32 API，不引入新依赖），热键与托盘入口在 `src/sticky_wallpaper.py`，窗口同步在 `src/qt_gui.py`。

- **Hotkey thread**: `RegisterHotKey(None, ...)` 把 `WM_HOTKEY` 投递到调用线程自己的消息队列，所以注册和 `GetMessageW` 循环必须在同一个专用线程里（`_hotkey_loop`）。退出时由 `_stop_hotkeys()` 用 `PostThreadMessageW(WM_QUIT)` 结束该线程，不要在别的线程里调 `UnregisterHotKey`。
- **Hotkeys**: `Ctrl+Alt+N` 投递选中内容，`Ctrl+Alt+Shift+N` 投递剪贴板；托盘菜单有两项等价入口。
- **Selection capture** (`capture.grab_selection`): 先等修饰键松开（否则注入的 `C` 会拼成 `Ctrl+Alt+C`），记录 `GetClipboardSequenceNumber()`，用 `SendInput` 注入 `Ctrl+C`，确认序列号变化后再读剪贴板。序列号没变说明没有选中内容：返回空文本，且不动用户原有剪贴板。
- **Entry format** (`capture.format_entry`): 首行 `- HH:MM [来源] 内容`，其余行变成两空格缩进的子项；来源取前台窗口标题，压空白后截断到 `MAX_SOURCE_CHARS`。
- **Append-only semantics** (`Database.append_daily_note`): 读出当天内容，空行分隔后追加，再整段写回，从不覆盖，因此已有日志正文与天气字段都不会丢。
- **Qt thread constraint**: `DiaryWindow.append_external` 必须在 Qt 线程里执行（经 `QtGuiManager.post` 投递）。它刷新日历标记，并在窗口正显示当天时刷新预览（阅读模式）或把条目并入编辑器（编辑模式，保留未保存的用户输入）。只写数据库是不够的：已经打开的编辑器下次保存会用编辑器内容整体覆盖。
- **Delivery bubble** (`CaptureBubble`, in `qt_gui.py`): 桌面常驻的可见投递入口。左键单击记录选中内容，右键弹菜单（记录选中内容 / 记录剪贴板 / 打开工作日志 / 隐藏气泡），可拖动且位置持久化。用 `WS_EX_NOACTIVATE` + `Qt.WindowDoesNotAcceptFocus` 保证点击气泡不抢前台焦点（否则注入的 Ctrl+C 会打偏），结果直接回显在气泡本体。抓取在后台线程执行，结果经 `QtGuiManager.post` 投回 Qt 线程更新控件。开关为 `capture_bubble_enabled`，位置为 `capture_bubble_x/y`。

### 阅读疑难 (Reading Doubts)

阅读网页时把「读不懂、待回头细究」的句子汇总到一处，每条都能一键跳回原文并高亮。采集端是 Chrome MV3 扩展（`chrome_extension/`），接收端是应用内的本地 HTTP 服务（`question_server.py`），清单界面在 `qt_gui.py`（`QuestionWindow`），桌面入口是与投递气泡相互独立的 `QuestionBubble`。

- **Extension → app**: 扩展右键菜单「标记疑难」把 `{url,title,quote,anchor,question,category,priority,note}` POST 到 `http://127.0.0.1:<port>/questions`（默认 8765，见 `question_server_port`）。服务只绑定回环地址、不对外暴露；扩展用 `host_permissions` 直连，故无需 CORS 预检（服务端仍带 CORS 头，便于手工调试）。
- **Anchor strategy**: `anchor` 用 W3C-annotation 风格的 `{"q":quote,"p":prefix,"s":suffix,"o":offset}`（不是 XPath），服务端序列化成 JSON 存进 `questions.anchor`。回跳链接形如 `#oaq=<base64url(JSON)>`（`question_server.build_jump_url`，保留原页面已有片段），内容脚本按 quote 命中位置再用前后文评分挑选、滚到可视区并用 CSS Custom Highlight 高亮。因此回跳不依赖任何服务器往返，链接复制到别处也能用。
- **Threading**: HTTP 服务跑在自己的线程里。写入新疑难后回调 `_notify_question_added`（服务器线程）→ 托盘通知 + 经 `QtGuiManager.post` 在 Qt 线程刷新清单窗口与气泡。服务器线程绝不直接碰 Qt 控件。
- **Questions bubble** (`QuestionBubble`, in `qt_gui.py`): 只作为清单入口与「待解决」计数显示；左键打开清单窗口，右键弹菜单（打开疑难清单 / 隐藏气泡），可拖动且位置持久化。开关为 `question_bubble_enabled`，位置为 `question_bubble_x/y`。
- **Question window** (`QuestionWindow`): 「待解决 / 已解决」两个 tab；行内与底部都有「打开原文」，经 `QuestionWindow._jump_url_for` 拼出带锚点的 URL 交给 `QDesktopServices.openUrl`。标记已解决 / 恢复 / 删除都会回调 `on_changed` 同步气泡计数。
- **CRUD** (`questions` table): `add_question` / `get_questions(resolved=)` / `get_question` / `update_question` / `set_question_resolved` / `toggle_question_resolved` / `delete_question` / `count_questions(resolved=)`。排序为 `resolved, priority DESC, created_at DESC, id DESC`。

### Singleton Enforcement

Uses Windows Named Mutex (`Global\OfficeAssistant_SingleInstance`) + Named Event (`Global\OfficeAssistant_Activate`). Second instance signals the first to refresh, then exits.

### Rendering Pipeline

1. Read TODO items from SQLite (`db/sticky_wallpaper.db`); legacy `todos.txt` is imported only on first run
2. Get screen resolution via `ctypes`
3. Load base image (backup of original wallpaper, or generate gradient)
4. Draw sticky note with items onto base image
5. Save as `rendered_wallpaper.bmp`
6. Set wallpaper via Windows Registry + `SystemParametersInfoW`

### Wallpaper Backup Strategy

- Original wallpaper saved to `.original_wallpaper.jpg` on first run
- If user changes wallpaper externally, backup auto-updates on next render
- "Restore wallpaper" sets the backup back as system wallpaper

## Configuration

Settings live in the SQLite `app_settings` table; `config/config.json` is only a legacy import source. Fields:

| Field | Default | Description |
|-------|---------|-------------|
| `refresh_minutes` | 5 | Wallpaper refresh interval |
| `note_position` | `"top_right"` | Sticky note placement (top_right/top_left/bottom_right/bottom_left/center) |
| `margin` | 60 | Edge margin in pixels |
| `pomodoro_enabled` | false | Enable Pomodoro timer |
| `pomodoro_interval_minutes` | 60 | Work duration |
| `pomodoro_break_minutes` | 5 | Break duration |
| `capture_bubble_enabled` | true | 是否显示桌面投递气泡 |
| `capture_bubble_x` | -1 | 气泡横坐标（-1 表示使用默认位置） |
| `capture_bubble_y` | -1 | 气泡纵坐标（-1 表示使用默认位置） |
| `question_bubble_enabled` | true | 是否显示阅读疑难气泡 |
| `question_bubble_x` | -1 | 疑难气泡横坐标（-1 表示使用默认位置） |
| `question_bubble_y` | -1 | 疑难气泡纵坐标（-1 表示使用默认位置） |
| `question_server_port` | 8765 | 疑难接收服务监听端口（仅绑定回环地址） |

## Data Format

Legacy `todos.txt` (read only during the one-time import) uses Markdown-style checkboxes. Only items under the `# TODO` heading are imported:

```
# 个人笔记
(ignored content)

# TODO
- [ ] Uncompleted task
- [x] Completed task
- Plain text (treated as uncompleted)
```

Parsing stops at the next `#` heading after `# TODO`.

## Pomodoro Timer Architecture

### Key Constraints

- **pystray daemon thread**: `run_detached()` runs the tray icon in a separate thread. Menu callbacks must NOT call CPU-intensive operations (like `render()`) directly in the icon thread — spawn a daemon thread instead.
- **Config persistence**: All state changes (e.g. toggling `pomodoro_enabled`) must call `_save_config()` immediately. Persist them to the SQLite `app_settings` table — never rely on in-memory defaults alone.
- **Build prerequisite**: Must `Stop-Process -Name OfficeAssistant` before rebuilding exe, otherwise PyInstaller fails with `PermissionError`.
- **PowerShell stderr false-positive**: PyInstaller stderr output is misreported as exit code 1 in PowerShell — safe to ignore.

### Render Scheduling (Critical)

`_pomodoro_loop` **exclusively** owns render calls during work phases to avoid race conditions:

```
_refresh_loop:  sleeps config["refresh_minutes"] * 60, calls render()
_pomodoro_loop: sleeps 1s per tick, calls render() every 60s during work phase
```

**Rule**: When Pomodoro is active, `_refresh_loop` does NOT adjust its interval — it continues at the configured rate. `_pomodoro_loop` handles the per-minute countdown refresh independently. This avoids duplicate renders and race conditions between the two loops.

### Work/Break Flow

```
_pomodoro_loop:
  while running:
    work phase (while elapsed < _work_duration):
      sleep 1s, render() every 60s with countdown text
    → _show_break_choice() → returns "rest" / "snooze" / "skip"
      rest   → _show_break_overlay() fullscreen countdown → reset _work_duration to config interval
      snooze → _work_duration = 300 (5 min), _pomodoro_last_break = now → re-enter work phase
      skip   → reset _work_duration to config interval → re-enter work phase
```

### Key Instance Variables

| Variable | Purpose |
|----------|--------|
| `_work_duration` | Current work phase length in seconds. Default = `interval * 60`, set to `300` on snooze |
| `_pomodoro_last_break` | Timestamp of last break/snooze start, used for countdown calculation |
| `_pomodoro_enabled` | Read from config on startup, saved on toggle |

### Fullscreen Break Overlay (`_show_break_overlay`)

- **Implementation**: tkinter window with `-fullscreen`, `-topmost`, `overrideredirect(True)`, dark background (`#0d0d1a`)
- **Exit methods**: "跳过休息" button or Esc key
- **Timer update**: `root.after(1000, update_countdown)` for per-second refresh
- **Thread safety**: Runs `mainloop()` in a daemon thread — works on Windows despite tkinter docs recommending main thread

### Break Choice Dialog (`_show_break_choice`)

- tkinter popup with 3 buttons: 休息一下 / 再工作5分钟 / 跳过
- Returns string: `"rest"` / `"snooze"` / `"skip"`
- Blocks the Pomodoro thread until user responds

### Wallpaper Countdown Display

Rendered inside the sticky note between date and divider line:
- Work mode: `⏱ MM:SS 距下次休息`
- Break mode: `⏸ 休息中 MM:SS`

Uses `accent_rgb + (220,)` color. Header height dynamically increases +24px when countdown text is present.

### Rejected Approaches (Do NOT Re-implement)

| Approach | Why Rejected |
|----------|-------------|
| PowerShell Toast notifications | HTML entity encoding for Chinese is fragile; users may disable notifications |
| `win10toast` / `winotify` libraries | Adds external dependency, violates minimal-deps constraint |
| Monitor power-off (`SendMessageW SC_MONITORPOWER`) | Too aggressive; user prefers visual overlay they can dismiss |
| `MessageBoxW` with auto-close (EnumWindows + timer) | Complex ctypes callback logic, tuple unpacking bugs, unreliable |
| `for _ in range(interval)` loop for timing | Cannot handle snooze correctly — loop counts from start regardless of `_pomodoro_last_break`. Must use `while + time.time()` |
| Both `_refresh_loop` and `_pomodoro_loop` calling render() | Race condition — duplicate renders. Only `_pomodoro_loop` should render during work phase |
| Hardcoded 12-item TODO limit | Users need unlimited items; note height is already dynamic |

## Key Technical Decisions

- **Windows-only**: Heavy use of `ctypes.windll`, `winreg`, Windows font paths. No cross-platform support.
- **Flat modules in `src/`**: No package structure; modules import each other by bare name, so `src/` must be on `sys.path` (direct run, tests, and PyInstaller `pathex`).
- **Pillow for rendering**: All drawing done via PIL/Pillow (no Cairo, no HTML rendering).
- **tkinter for dialogs**: Pomodoro break/choice dialogs use tkinter (bundled with Python, no extra dependency).
- **PyInstaller packaging**: `src/sticky_wallpaper.py` derives `BASE_DIR` from `sys.frozen` (exe directory when frozen, project root when run from source); data is written to `db/`, settings to the SQLite `app_settings` table.
- **Frozen DLL hygiene**: `OfficeAssistant.spec` keeps only binaries whose source lives under `sys.prefix` or `System32`. If a foreign toolchain on `PATH` ships a DLL name Qt resolves (for example poppler's `icuuc.dll`), PyInstaller's dependency walker bundles that copy, it shadows the system ICU, and the frozen app dies with `ImportError: DLL load failed while importing QtCore`. Do not remove the filter.
- **BMP output**: Wallpaper saved as BMP for maximum Windows compatibility.

## Code Conventions

- **Language**: Code comments and UI strings are in Chinese
- **Naming**: PascalCase for classes, snake_case for functions/methods, `_` prefix for private methods, UPPER_SNAKE_CASE for constants
- **Section separators**: `# ═══...` comments divide major modules
- **Error handling**: `try-except` with print logging; file operations use `missing_ok=True`

## Dependencies

External packages (keep minimal):
- **Pillow** (>=10.0.0): Image manipulation and text rendering
- **pystray** (>=0.19.0): System tray icon and menu
- **PySide6** (>=6.6.0): Qt GUI (diary, todo editor, bubbles). Pulls in shiboken6.

## Platform Requirements

- Windows OS (uses Win32 API extensively)
- Python 3.12+ (verified on 3.14)
- Chinese fonts installed (微软雅黑 preferred)
