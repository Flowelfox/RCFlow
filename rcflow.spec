# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules
from PyInstaller.utils.hooks import collect_all

datas = [('/Users/vpohribnichenko/Projects/RCFlow/src/prompts/templates', 'templates')]
binaries = []
hiddenimports = ['uvicorn.logging', 'uvicorn.loops', 'uvicorn.loops.auto', 'uvicorn.protocols', 'uvicorn.protocols.http', 'uvicorn.protocols.http.auto', 'uvicorn.protocols.websockets', 'uvicorn.protocols.websockets.auto', 'uvicorn.lifespan', 'uvicorn.lifespan.on', 'uvicorn.lifespan.off', 'aiosqlite', 'sqlalchemy.dialects.sqlite', 'sqlalchemy.dialects.sqlite.aiosqlite', 'alembic', 'alembic.command', 'alembic.config', 'src', 'src.main', 'src.config', 'src.paths', 'src.__main__', 'src.api', 'src.api.http', 'src.api.ws', 'src.api.ws.input_text', 'src.api.ws.output_text', 'src.core', 'src.core.buffer', 'src.core.llm', 'src.core.permissions', 'src.core.prompt_router', 'src.core.session', 'src.database', 'src.database.engine', 'src.executors', 'src.executors.claude_code', 'src.executors.codex', 'src.logs', 'src.models', 'src.database.models', 'src.prompts', 'src.prompts.builder', 'src.services', 'src.services.tool_manager', 'src.services.tool_settings', 'src.tools', 'src.tools.loader', 'src.tools.registry', 'jinja2', 'pydantic', 'pydantic_settings', 'httpx', 'anthropic', 'aiohttp', 'src.gui', 'src.gui.macos', 'src.gui.core', 'src.gui.theme', 'src.gui.updater', 'AppKit', 'Foundation', 'objc', 'PIL', 'PIL.Image', 'PIL.ImageDraw', 'customtkinter']
hiddenimports += collect_submodules('src')
hiddenimports += collect_submodules('uvicorn')
tmp_ret = collect_all('objc')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('AppKit')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('Foundation')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['/Users/vpohribnichenko/Projects/RCFlow/src/__main__.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
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
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['/Users/vpohribnichenko/Projects/RCFlow/src/gui/assets/tray_icon.icns'],
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
app = BUNDLE(
    coll,
    name='rcflow.app',
    icon='/Users/vpohribnichenko/Projects/RCFlow/src/gui/assets/tray_icon.icns',
    bundle_identifier='com.rcflow.worker',
)
