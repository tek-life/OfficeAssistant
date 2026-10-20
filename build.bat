@echo off
setlocal
cd /d "%~dp0"

echo ========================================
echo   OfficeAssistant - build script
echo ========================================
echo.

REM Pick a working Python launcher (py launcher preferred).
set "PYEXE=py -3"
%PYEXE% --version >nul 2>&1
if errorlevel 1 set "PYEXE=python"
%PYEXE% --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Install Python 3.7+ first.
    pause
    exit /b 1
)
echo [0/4] Python launcher: %PYEXE%
%PYEXE% --version

echo [1/4] Stopping running OfficeAssistant.exe ...
taskkill /f /im OfficeAssistant.exe >nul 2>&1

echo [2/4] Checking dependencies ...
%PYEXE% -c "import PIL, pystray, PySide6" >nul 2>&1
if errorlevel 1 (
    echo [INFO] Installing dependencies from requirements.txt ...
    %PYEXE% -m pip install -r requirements.txt
)

echo [3/4] Ensuring PyInstaller ...
%PYEXE% -m PyInstaller --version >nul 2>&1
if errorlevel 1 (
    echo [INFO] Installing PyInstaller ...
    %PYEXE% -m pip install pyinstaller
)
%PYEXE% -m PyInstaller --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] PyInstaller is not available.
    pause
    exit /b 1
)

echo [4/4] Building from OfficeAssistant.spec ...
%PYEXE% -m PyInstaller OfficeAssistant.spec --noconfirm
if errorlevel 1 (
    echo [ERROR] Build failed. See messages above.
    pause
    exit /b 1
)

echo.
echo   Done. Output: dist\OfficeAssistant.exe
echo   Data: SQLite database is created automatically under db\ on first run.
echo ========================================
pause
endlocal
