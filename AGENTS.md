# AGENTS.md

This file provides guidance to Qoder (qoder.com) when working with code in this repository.

## Build & Run Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run the application directly
python sticky_wallpaper.py

# Build standalone exe (requires PyInstaller) — uses the committed spec
pyinstaller OfficeAssistant.spec --noconfirm
# Output: dist\OfficeAssistant.exe

# Manual PyInstaller build (equivalent one-liner)
pyinstaller --onefile --noconsole --name OfficeAssistant sticky_wallpaper.py
```

## Architecture

办公助手 (OfficeAssistant) is a Windows desktop application that renders TODO items as sticky notes onto the desktop wallpaper, and additionally provides a work diary (工作日志) and a Pomodoro timer with break reminders. Data is stored in SQLite (`data/`).

### Core Components (all in `sticky_wallpaper.py`)

| Component | Lines | Responsibility |
|-----------|-------|---------------|
| DPI & Console Setup | Top | `SetProcessDPIAware()`, hide console window |
| Font Finder | ~90-103 | Searches Windows Fonts dir for CJK fonts (msyh > simhei > simsun) |
| `TodoItem` | ~107-170 | Dataclass + parser for `todos.txt` (Markdown checkbox format) |
| `StickyNoteRenderer` | ~177-346 | Pillow-based renderer: rounded rect, shadow, fold corner, text wrapping, 6-color palette |
| `WallpaperManager` | ~353-391 | Gets/sets wallpaper via `SystemParametersInfoW`, queries screen resolution |
| `OfficeAssistant` | ~398-971 | Main app: singleton lock, config, tray icon, Pomodoro loop, periodic refresh |

### Threading Model

- **Main thread**: Periodic wallpaper refresh loop (configurable interval)
- **Tray thread**: `pystray.Icon.run_detached()` for system tray menu
- **Pomodoro thread**: Work/break timer with per-second countdown
- **Activation listener thread**: Monitors named event for second-instance signals

### Singleton Enforcement

Uses Windows Named Mutex (`Global\OfficeAssistant_SingleInstance`) + Named Event (`Global\OfficeAssistant_Activate`). Second instance signals the first to refresh, then exits.

### Rendering Pipeline

1. Read `todos.txt` → parse items under `# TODO` heading
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

`config.json` fields:

| Field | Default | Description |
|-------|---------|-------------|
| `refresh_minutes` | 5 | Wallpaper refresh interval |
| `note_position` | `"top_right"` | Sticky note placement (top_right/top_left/bottom_right/bottom_left/center) |
| `margin` | 60 | Edge margin in pixels |
| `pomodoro_enabled` | false | Enable Pomodoro timer |
| `pomodoro_interval_minutes` | 60 | Work duration |
| `pomodoro_break_minutes` | 5 | Break duration |

## Data Format

`todos.txt` uses Markdown-style checkboxes. Only items under the `# TODO` heading are rendered:

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
- **Config persistence**: All state changes (e.g. toggling `pomodoro_enabled`) must call `_save_config()` immediately. Users expect config.json to be "visible and editable" — never rely on in-memory defaults alone.
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
- **Single file**: Entire app lives in `sticky_wallpaper.py` — no package structure.
- **Pillow for rendering**: All drawing done via PIL/Pillow (no Cairo, no HTML rendering).
- **tkinter for dialogs**: Pomodoro break/choice dialogs use tkinter (bundled with Python, no extra dependency).
- **PyInstaller packaging**: Paths use `sys._MEIPASS` detection for bundled resource resolution.
- **BMP output**: Wallpaper saved as BMP for maximum Windows compatibility.

## Code Conventions

- **Language**: Code comments and UI strings are in Chinese
- **Naming**: PascalCase for classes, snake_case for functions/methods, `_` prefix for private methods, UPPER_SNAKE_CASE for constants
- **Section separators**: `# ═══...` comments divide major modules
- **Error handling**: `try-except` with print logging; file operations use `missing_ok=True`

## Dependencies

Only two external packages (keep minimal):
- **Pillow** (>=10.0.0): Image manipulation and text rendering
- **pystray** (>=0.19.0): System tray icon and menu

## Platform Requirements

- Windows OS (uses Win32 API extensively)
- Python 3.7+ (dataclasses, type hints, pathlib)
- Chinese fonts installed (微软雅黑 preferred)
