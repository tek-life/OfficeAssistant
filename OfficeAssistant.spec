# -*- mode: python ; coding: utf-8 -*-
import os
import sys
from pathlib import Path


# 打包时只保留 Python 环境与系统目录中的二进制（见下方过滤），
# 未使用的 Qt 模块与 numpy/matplotlib 等无关大件已在 excludes 中排除。

a = Analysis(
    ['src/sticky_wallpaper.py'],
    pathex=['src'],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['PySide6.QtQml', 'PySide6.QtQuick', 'PySide6.QtQuickWidgets', 'PySide6.QtTest', 'PySide6.QtUiTools', 'PySide6.QtOpenGL', 'PySide6.QtOpenGLWidgets', 'PySide6.QtSvg', 'PySide6.QtSvgWidgets', 'PySide6.QtPrintSupport', 'PySide6.QtSql', 'PySide6.QtXml', 'PySide6.QtDBus', 'PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets', 'PySide6.QtWebChannel',
              'numpy', 'matplotlib', 'numpy_financial', 'scipy', 'pandas'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

# 只保留来自 Python 环境或系统目录的二进制，丢弃 PATH 上其它软件（如 PDF 工具链）
# 顺带捎来的同名 DLL；例如 poppler 的 icuuc.dll 会遮蔽系统 ICU，导致 Qt 加载失败。
_PY_PREFIX = Path(sys.prefix).resolve()
_SYS32 = Path(os.environ.get('SystemRoot', 'C:/Windows')).resolve() / 'System32'


def _binary_is_local(entry):
    try:
        src = Path(entry[1]).resolve()
    except Exception:
        return True
    for root in (_PY_PREFIX, _SYS32):
        try:
            src.relative_to(root)
            return True
        except ValueError:
            continue
    return False


for _entry in [e for e in a.binaries if not _binary_is_local(e)]:
    print('excluded foreign binary:', _entry[0], '<-', _entry[1])
a.binaries = [e for e in a.binaries if _binary_is_local(e)]

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='OfficeAssistant',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
