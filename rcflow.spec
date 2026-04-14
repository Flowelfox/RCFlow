# -*- mode: python ; coding: utf-8 -*-
import sys
from PyInstaller.utils.hooks import collect_submodules

hiddenimports = ['uvicorn.logging', 'uvicorn.loops', 'uvicorn.loops.auto', 'uvicorn.protocols', 'uvicorn.protocols.http', 'uvicorn.protocols.http.auto', 'uvicorn.protocols.websockets', 'uvicorn.protocols.websockets.auto', 'uvicorn.lifespan', 'uvicorn.lifespan.on', 'uvicorn.lifespan.off', 'aiosqlite', 'sqlalchemy.dialects.sqlite', 'sqlalchemy.dialects.sqlite.aiosqlite', 'alembic', 'alembic.command', 'alembic.config', 'src', 'src.main', 'src.config', 'src.paths', 'src.__main__', 'src.api', 'src.api.http', 'src.api.ws', 'src.api.ws.input_text', 'src.api.ws.input_audio', 'src.api.ws.output_text', 'src.api.ws.output_audio', 'src.core', 'src.core.buffer', 'src.core.llm', 'src.core.permissions', 'src.core.prompt_router', 'src.core.session', 'src.database', 'src.database.engine', 'src.executors', 'src.executors.claude_code', 'src.executors.codex', 'src.logs', 'src.models', 'src.database.models', 'src.prompts', 'src.prompts.builder', 'src.services', 'src.services.tool_manager', 'src.services.tool_settings', 'src.speech', 'src.speech.stt', 'src.speech.tts', 'src.tools', 'src.tools.loader', 'src.tools.registry', 'jinja2', 'pydantic', 'pydantic_settings', 'httpx', 'anthropic', 'aiohttp']
hiddenimports += collect_submodules('src')
hiddenimports += collect_submodules('uvicorn')

# Platform-specific hidden imports
if sys.platform == 'win32':
    hiddenimports += ['src.gui', 'src.gui.windows', 'src.gui.core', 'src.gui.theme', 'pystray', 'pystray._win32', 'PIL', 'PIL.Image', 'PIL.ImageDraw', 'winpty', 'customtkinter']
elif sys.platform == 'darwin':
    hiddenimports += ['src.gui', 'src.gui.macos', 'src.gui.core', 'src.gui.theme', 'AppKit', 'Foundation', 'objc', 'PIL', 'PIL.Image', 'PIL.ImageDraw', 'customtkinter']

_is_macos = sys.platform == 'darwin'
_is_windows = sys.platform == 'win32'

a = Analysis(
    ['/home/flowelfox/Projects/RCFlow/src/__main__.py'],
    pathex=[],
    binaries=[],
    datas=[('/home/flowelfox/Projects/RCFlow/src/prompts/templates', 'templates')],
    hiddenimports=hiddenimports,
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
    [],
    exclude_binaries=True,
    name='rcflow',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    # windowed=True suppresses the terminal console; required for macOS .app
    # bundles and for the Windows tray build so no console flashes on launch.
    console=not (_is_macos or _is_windows),
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file='scripts/rcflow_macos.entitlements' if _is_macos else None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='rcflow',
)

# macOS: wrap the COLLECT output in a .app bundle so the process can own an
# NSStatusBar item and run as an LSUIElement (no Dock icon).
if _is_macos:
    app = BUNDLE(
        coll,
        name='RCFlow Worker.app',
        icon='src/gui/assets/tray_icon.icns',
        bundle_identifier='com.rcflow.worker',
        info_plist={
            # Hide from Dock and app switcher — lives entirely in the menu bar
            'LSUIElement': True,
            'NSHighResolutionCapable': True,
            # Honour both Light and Dark Mode
            'NSRequiresAquaSystemAppearance': False,
            'CFBundleName': 'RCFlow Worker',
            'CFBundleDisplayName': 'RCFlow Worker',
            'CFBundleExecutable': 'rcflow',
            'CFBundleIconFile': 'tray_icon',
        },
    )
