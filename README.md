# 办公助手 (OfficeAssistant)

一款常驻系统托盘的 Windows 桌面小工具，把**今日待办**以便签风格渲染到桌面壁纸上，并集成了**工作日志**与**番茄钟**休息提醒。所有数据保存在本地 SQLite 数据库中。

## 功能特性

- **待办便签壁纸**：把当天的 TODO 列表以便签风格绘制到桌面壁纸的角落，条目数量不限，随内容自动调整高度。
- **今日待办管理**：托盘菜单打开待办编辑窗口，支持添加、勾选完成、删除。
- **工作日志**：每天一篇 Markdown 日志。默认阅读模式（渲染后的 Markdown），点击「编辑」按钮或双击正文即可进入编辑模式，带格式工具栏，可浏览历史日志。
- **番茄钟**：默认工作 60 分钟后提醒休息 5 分钟。休息前弹出选择框（休息 / 再工作 5 分钟 / 跳过），休息时显示全屏倒计时，可随时跳过开始下一轮。便签上会实时显示工作/休息倒计时。
- **单实例运行**：重复启动会通知已运行的实例刷新壁纸并自动退出。
- **壁纸备份与还原**：首次运行备份原始壁纸，随时可从托盘菜单一键还原。

## 环境要求

- Windows 10 / 11
- Python 3.12+（开发验证于 3.14）

## 安装与运行

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 直接运行
python sticky_wallpaper.py
```

程序启动后不会显示主窗口，而是常驻系统托盘。右键托盘图标即可使用各项功能。

## 打包为独立 exe

推荐使用已提交的 PyInstaller 配置文件打包（已排除无用的 Qt 模块以减小体积）：

```bash
pyinstaller OfficeAssistant.spec --noconfirm
# 产物：dist\OfficeAssistant.exe
```

也可以直接双击运行 `build.bat`，它会自动检查环境、停止旧进程并调用上述 spec 完成打包。

> 重新打包前需先关闭正在运行的 `OfficeAssistant.exe`，否则 PyInstaller 会因文件占用报 `PermissionError`。

## 使用说明

右键系统托盘图标，可进行以下操作：

- **刷新壁纸**：立即重新渲染便签壁纸（默认每 5 分钟自动刷新一次）。
- **编辑待办**：打开待办管理窗口。
- **工作日志**：打开当天日志，默认阅读模式，双击正文或点「编辑」进入编辑。
- **番茄钟休息提醒**：勾选开启/关闭番茄钟。
- **还原壁纸**：把壁纸还原为程序运行前的原始壁纸。
- **退出**：还原壁纸并退出程序。

## 项目结构

| 文件 | 说明 |
|------|------|
| `sticky_wallpaper.py` | 主程序：便签渲染、壁纸管理、系统托盘、番茄钟、单实例控制 |
| `db.py` | SQLite 存储层：待办、工作日志、应用设置三张表，含从旧版 `todos.txt`/`config.json` 的自动迁移 |
| `qt_gui.py` | 基于 PySide6 (Qt) 的 GUI 模块：工作日志窗口、待办编辑窗口 |
| `OfficeAssistant.spec` | PyInstaller 打包配置 |
| `build.bat` | 一键打包脚本 |
| `requirements.txt` | Python 依赖清单 |

## 数据存储

所有数据保存在程序目录下的 `data/sticky_wallpaper.db`（SQLite）中，包含三张表：

- `todos`：待办事项
- `daily_notes`：工作日志（每天一条，Markdown）
- `app_settings`：应用设置（key-value）

首次运行时，如检测到旧版的 `todos.txt` / `config.json`，会自动导入数据库并把原文件重命名为 `.bak` 备份。

## 技术说明

GUI 采用 PySide6 (Qt) 而非 tkinter。原因是 Windows 上 tkinter 的 TSF 输入法支持不完整，导致中文拼音组词串字号不跟随正文字体（偏小）；Qt 的 `QTextEdit` / `QLineEdit` 有完整 TSF 支持，拼音串会自动与正文等大。Qt 事件循环运行在独立线程，所有窗口操作通过请求队列投递到该线程执行，以满足 Qt「控件只能在其创建线程操作」的约束。
