# AGENTS.md

This file provides guidance to Qoder (qoder.com) when working with code in this repository.

## Build & Run Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run the application directly
python sticky_wallpaper.py

# Build standalone exe (requires PyInstaller)
build.bat
# Output: dist\StickyWallpaper.exe

# Manual PyInstaller build
pyinstaller --onefile --noconsole --name StickyWallpaper sticky_wallpaper.py
```

## Architecture

StickyWallpaper is a single-file Windows desktop application (~984 lines) that renders TODO items as sticky notes onto the desktop wallpaper and provides a Pomodoro timer with break reminders.

### Core Components (all in `sticky_wallpaper.py`)

| Component | Lines | Responsibility |
|-----------|-------|---------------|
| DPI & Console Setup | Top | `SetProcessDPIAware()`, hide console window |
| Font Finder | ~90-103 | Searches Windows Fonts dir for CJK fonts (msyh > simhei > simsun) |
| `TodoItem` | ~107-170 | Dataclass + parser for `todos.txt` (Markdown checkbox format) |
| `StickyNoteRenderer` | ~177-346 | Pillow-based renderer: rounded rect, shadow, fold corner, text wrapping, 6-color palette |
| `WallpaperManager` | ~353-391 | Gets/sets wallpaper via `SystemParametersInfoW`, queries screen resolution |
| `StickyWallpaper` | ~398-971 | Main app: singleton lock, config, tray icon, Pomodoro loop, periodic refresh |

### Threading Model

- **Main thread**: Periodic wallpaper refresh loop (configurable interval)
- **Tray thread**: `pystray.Icon.run_detached()` for system tray menu
- **Pomodoro thread**: Work/break timer with per-second countdown
- **Activation listener thread**: Monitors named event for second-instance signals

### Singleton Enforcement

Uses Windows Named Mutex (`Global\StickyWallpaper_SingleInstance`) + Named Event (`Global\StickyWallpaper_Activate`). Second instance signals the first to refresh, then exits.

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
