# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for the Akaion runner daemon.
Bundles cli.py + runner/ + ui/dist as a single executable.

Build with (from akaion-app-runner/):
    pyinstaller runner.spec --distpath ui/src-tauri/binaries/_tmp \\
                            --workpath build/work --clean --noconfirm
"""
from PyInstaller.utils.hooks import collect_all
import os
import sys

datas, binaries, hiddenimports = [], [], []

for pkg in (
    "loguru",
    "fastapi",
    "uvicorn",
    "starlette",
    "pydantic",
    "pydantic_settings",
    "httpx",
    "cryptography",
    "typer",
    "rich",
    "click",
    "PyYAML",
    "watchdog",
    "psutil",
    "openai",
    "anthropic",
    "websockets",
):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:
        # Best-effort: a missing optional package shouldn't kill the build.
        pass

# include the pre-built UI dist so the daemon can serve it from StaticFiles
ui_dist = os.path.abspath("ui/dist")
if os.path.isdir(ui_dist):
    datas.append((ui_dist, "ui/dist"))

# the shipped skills: loader.py looks for them next to itself
datas.append((os.path.abspath("runner/skills/shipped"), "runner/skills/shipped"))

# also ship the .env.example so first-launch can scaffold defaults
env_example = os.path.abspath(".env.example")
if os.path.isfile(env_example):
    datas.append((env_example, "."))


a = Analysis(
    ["cli.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports + [
        "uvicorn.loops.auto",
        "uvicorn.loops.asyncio",
        "uvicorn.lifespan.on",
        "uvicorn.lifespan.off",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.http.h11_impl",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.protocols.websockets.websockets_impl",
        "email.mime.multipart",
        "email.mime.text",
    ],
    excludes=["tkinter", "test", "tests"],
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data)

# Pick the icon format PyInstaller can actually consume on this host.
#   • Windows → .ico
#   • macOS   → .icns
#   • Linux   → ignored by PyInstaller (leave it None)
_icon_candidate = None
if sys.platform.startswith("win"):
    _icon_candidate = os.path.abspath("ui/src-tauri/icons/icon.ico")
elif sys.platform == "darwin":
    _icon_candidate = os.path.abspath("ui/src-tauri/icons/icon.icns")
_icon = _icon_candidate if (_icon_candidate and os.path.isfile(_icon_candidate)) else None

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="annona",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    onefile=True,
    icon=_icon,
)
