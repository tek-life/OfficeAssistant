# -*- mode: python ; coding: utf-8 -*-


# 只打包运行时真正用到的依赖：排除未使用的 Qt 模块，
# 以及 matplotlib/numpy 等重依赖，避免它们带入额外的 VC 运行时 DLL、污染打包环境与体积。
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
