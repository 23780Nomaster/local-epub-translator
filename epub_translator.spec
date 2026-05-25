# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for EPUB Translator Windows exe (tkinter desktop app)."""

import sys
from pathlib import Path

project_dir = Path(__file__).parent.absolute()

a = Analysis(
    [str(project_dir / 'main.py')],
    pathex=[str(project_dir)],
    binaries=[],
    datas=[],
    hiddenimports=[
        'tkinter',
        'tkinter.ttk',
        'tkinter.filedialog',
        'tkinter.messagebox',
        'psutil',
        'psutil._pswindows',
        'ebooklib',
        'ebooklib.epub',
        'bs4',
        'bs4.builder._html5lib',
        'bs4.builder._lxml',
        'PIL',
        'PIL.Image',
        'PIL._imaging',
        'pytesseract',
        'requests',
        'tqdm',
        'tqdm.std',
        'lxml',
        'lxml.etree',
        'json',
        'threading',
        'os',
        'signal',
        'subprocess',
        'time',
        'socket',
        'uuid',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'unittest',
        'email',
        'http',
        'xmlrpc',
        'pydoc',
        'distutils',
        'setuptools',
        'pip',
        'flask',
        'flask.app',
    ],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='EPUB-Translator',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)
