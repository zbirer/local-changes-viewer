# PyInstaller spec for local-changes-viewer.
# Build: .venv/bin/pyinstaller packaging/local_changes_viewer.spec --noconfirm
# Output: dist/local-changes-viewer.app

from pathlib import Path

block_cipher = None

project_root = Path(SPECPATH).parent
entry_script = project_root / "packaging" / "app_entry.py"

a = Analysis(
    [str(entry_script)],
    pathex=[str(project_root / "src")],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    cipher=block_cipher,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="local-changes-viewer",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
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
    name="local-changes-viewer",
)

app = BUNDLE(
    coll,
    name="local-changes-viewer.app",
    icon=None,
    bundle_identifier="us.canopycare.local-changes-viewer",
    info_plist={
        "CFBundleName": "local-changes-viewer",
        "CFBundleDisplayName": "local-changes-viewer",
        "CFBundleShortVersionString": "0.1.0",
        "NSHighResolutionCapable": True,
    },
)
