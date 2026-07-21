@echo off
chcp 65001 >nul
echo ========================================
echo   办公助手 (OfficeAssistant) 打包工具
echo ========================================
echo.

REM 检查 py 命令
py --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未找到 Python，请先安装 Python。
    pause
    exit /b 1
)

REM 检查 PyInstaller
py -m PyInstaller --version >nul 2>&1
if errorlevel 1 (
    echo [信息] 正在安装 PyInstaller...
    py -m pip install pyinstaller
)

echo [1/2] 停止正在运行的旧程序...
taskkill /f /im OfficeAssistant.exe >nul 2>&1

echo [2/2] 开始打包（使用 OfficeAssistant.spec，已排除无用 Qt 模块）...
py -m PyInstaller OfficeAssistant.spec --noconfirm

if errorlevel 1 (
    echo [错误] 打包失败，请检查上方错误信息。
    pause
    exit /b 1
)

echo.
echo  完成！可执行文件位于: dist\OfficeAssistant.exe
echo  数据（SQLite）会在首次运行时自动生成于 data\ 目录。
echo ========================================
pause
