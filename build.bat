@echo off
chcp 65001 >nul
echo ========================================
echo   StickyWallpaper 打包工具
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

echo [1/3] 开始打包...
py -m PyInstaller ^
    --onefile ^
    --noconsole ^
    --name StickyWallpaper ^
    --add-data "sticky_wallpaper.py;." ^
    --clean ^
    sticky_wallpaper.py

if errorlevel 1 (
    echo [错误] 打包失败，请检查上方错误信息。
    pause
    exit /b 1
)

echo.
echo [2/3] 复制配置文件...
if not exist "dist\todos.txt" (
    copy /y todos.txt dist\todos.txt >nul 2>&1
)

echo [3/3] 完成！
echo.
echo  可执行文件位于: dist\StickyWallpaper.exe
echo  配置文件位于:   dist\todos.txt
echo.
echo  你可以把 dist 文件夹里的内容复制到任意位置使用。
echo ========================================
pause
