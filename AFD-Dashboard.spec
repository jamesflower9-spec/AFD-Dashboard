# -*- mode: python ; coding: utf-8 -*-
"""
AFD-Dashboard.spec

PyInstaller build for the Airfoil Performance Demonstrator dashboard.

    pyinstaller AFD-Dashboard.spec

Output lands in dist/. Ship the whole dist/AFD-Dashboard folder, not just the
.exe - the one-folder build starts faster than one-file and makes it obvious
to the next person where the data lives.

After building, copy calibration.json into dist/AFD-Dashboard/ so it can be
replaced without a rebuild. A default is bundled inside as a fallback.
"""

block_cipher = None

a = Analysis(
    ['dashboard.py'],
    pathex=[],
    binaries=[],

    # (source, destination inside the bundle)
    datas=[
        ('assets/xfoil', 'assets/xfoil'),   # polar and 41 Cp files
        ('calibration.json', '.'),          # default, overridden by an
                                            # external copy if one exists
    ],

    # PyInstaller finds most imports by scanning, but pyqtgraph loads parts of
    # itself dynamically and numpy's Qt bindings are picked at runtime, so
    # these are named explicitly.
    hiddenimports=[
        'pyqtgraph',
        'pyqtgraph.graphicsItems',
        'pyqtgraph.widgets',
        'serial',
        'serial.tools.list_ports',
        'numpy',
    ],

    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],

    # PySide6 ships a great deal that a plotting dashboard never touches.
    # Excluding it cuts the build from roughly 300 MB to under 100 MB.
    excludes=[
        'PySide6.QtWebEngineCore',
        'PySide6.QtWebEngineWidgets',
        'PySide6.Qt3DCore',
        'PySide6.Qt3DRender',
        'PySide6.QtQuick',
        'PySide6.QtQml',
        'PySide6.QtDesigner',
        'PySide6.QtBluetooth',
        'PySide6.QtNfc',
        'PySide6.QtPositioning',
        'PySide6.QtSql',
        'PySide6.QtTest',
        'matplotlib',
        'tkinter',
        'scipy',
        'PIL',
    ],

    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='AFD-Dashboard',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,

    # Leave this True for the first build. If anything fails on startup - a
    # missing data file, an import that was excluded by mistake - the error
    # appears in the console window instead of the program vanishing without
    # explanation. Set it False once the build is proven.
    console=True,

    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='AFD-Dashboard',
)