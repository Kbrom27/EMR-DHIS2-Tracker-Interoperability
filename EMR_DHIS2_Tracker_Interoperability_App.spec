# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_submodules


hidden_imports = (
    collect_submodules('certifi')
    + collect_submodules('charset_normalizer')
    + collect_submodules('idna')
    + collect_submodules('requests')
    + collect_submodules('urllib3')
    + collect_submodules('clients')
    + collect_submodules('export')
    + collect_submodules('transform')
    + collect_submodules('import_')
    + collect_submodules('ui')
    + collect_submodules('rules')
)

a = Analysis(
    ['emr_dhis2_tracker_sync.py'],
    pathex=[],
    binaries=[],
    datas=[('Resources', 'Resources')],
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
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
    name='EMR_DHIS2_Tracker_Interoperability_App',
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
)
