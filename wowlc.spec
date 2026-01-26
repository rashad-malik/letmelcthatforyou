# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec file for WoW Loot Council (wowlc) application.

This creates a single-file executable for Windows and Linux.
The application uses NiceGUI for the web-based UI with native window support.

Build command:
    pyinstaller wowlc.spec --clean

Output:
    dist/letmelcthatforyou.exe (Windows)
    dist/letmelcthatforyou (Linux)
"""

from PyInstaller.utils.hooks import collect_data_files, collect_submodules
import sys
import os

block_cipher = None

# ============================================================================
# Data Files to Bundle
# ============================================================================

# Application data files
datas = [
    ('data', 'data'),            # TBC tokens JSON, etc.
]

# Collect NiceGUI static assets (JavaScript, CSS, etc.)
datas += collect_data_files('nicegui')

# Collect PySide6/Qt data files (for native window and TMB auth)
datas += collect_data_files('PySide6')

# Collect SSL certificates for HTTPS requests (required for LLM API calls)
datas += collect_data_files('certifi')

# Collect LiteLLM data files (tokenizers, model configs, etc.)
# LiteLLM uses importlib.resources to load these at runtime
datas += collect_data_files('litellm')

# Collect tiktoken data files (used by LiteLLM for token counting)
try:
    datas += collect_data_files('tiktoken')
    datas += collect_data_files('tiktoken_ext')
except Exception:
    pass  # tiktoken may not be installed

# ============================================================================
# Hidden Imports
# ============================================================================
# These are modules that PyInstaller can't detect automatically because
# they're imported dynamically at runtime.

hidden_imports = [
    # === NiceGUI and Web Framework ===
    'nicegui',
    'nicegui.elements',
    'nicegui.events',
    'nicegui.ui',
    'uvicorn',
    'uvicorn.logging',
    'uvicorn.loops',
    'uvicorn.loops.auto',
    'uvicorn.protocols',
    'uvicorn.protocols.http',
    'uvicorn.protocols.http.auto',
    'uvicorn.protocols.websockets',
    'uvicorn.protocols.websockets.auto',
    'uvicorn.lifespan',
    'uvicorn.lifespan.on',
    'starlette',
    'starlette.applications',
    'starlette.routing',
    'starlette.middleware',
    'starlette.responses',
    'starlette.websockets',
    'fastapi',
    'httpx',
    'httpx._transports',
    'httpx._transports.default',
    'anyio',
    'anyio._backends',
    'anyio._backends._asyncio',

    # === Socket/Real-time ===
    'engineio',
    'engineio.async_drivers',
    'engineio.async_drivers.asgi',
    'socketio',
    'socketio.async_server',

    # === Data Processing ===
    'pandas',
    'pandas._libs',
    'openpyxl',
    'tabulate',
    'numpy',

    # === LLM Providers (via LiteLLM) ===
    'litellm',
    'litellm.llms',
    'litellm.litellm_core_utils',
    'litellm.litellm_core_utils.tokenizers',
    'litellm.litellm_core_utils.llm_cost_calc',
    'anthropic',
    'openai',
    'tiktoken',
    'tiktoken_ext',
    'tiktoken_ext.openai_public',

    # === Network/API ===
    'gql',
    'gql.transport',
    'gql.transport.aiohttp',
    'gql.transport.requests',
    'aiohttp',
    'requests',
    'bs4',

    # === Templates ===
    'jinja2',
    'jinja2.ext',

    # === PySide6/Qt (native window and TMB auth) ===
    'PySide6',
    'PySide6.QtCore',
    'PySide6.QtGui',
    'PySide6.QtWidgets',
    'PySide6.QtWebEngineWidgets',
    'PySide6.QtWebEngineCore',
    'PySide6.QtNetwork',
    'PySide6.QtPrintSupport',
    'PySide6.QtPositioning',
    'PySide6.QtWebChannel',

    # === GUI Fallback ===
    'tkinter',
    'tkinter.messagebox',

    # === Async Support ===
    'asyncio',
    'concurrent.futures',
    'multiprocessing',
    'multiprocessing.queues',

    # === HTTP Server (for OAuth callback) ===
    'http.server',
    'socketserver',

    # === Standard library modules sometimes missed ===
    'email.mime.text',
    'email.mime.multipart',
    'json',
    'urllib.parse',
    'webbrowser',
]

# Collect all submodules for complex packages
hidden_imports += collect_submodules('nicegui')
hidden_imports += collect_submodules('litellm')
hidden_imports += collect_submodules('gql')
hidden_imports += collect_submodules('PySide6')

# ============================================================================
# Analysis
# ============================================================================

a = Analysis(
    ['src/wowlc/__main__.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=['hooks/hook-ssl.py'],
    excludes=[
        'playwright',           # Not needed - using system browser
        'pytest',               # Testing only
        'jupyter',              # Dev only
        'IPython',              # Dev only
        'matplotlib',           # Not used
        'scipy',                # Not used
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# ============================================================================
# Create PYZ archive
# ============================================================================

pyz = PYZ(
    a.pure,
    a.zipped_data,
    cipher=block_cipher
)

# ============================================================================
# Create Executable
# ============================================================================

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='letmelcthatforyou',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,                    # Compress with UPX if available
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,                # DEBUG: Show console window for logging
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,                   # Add 'assets/icon.ico' if you have one
)
