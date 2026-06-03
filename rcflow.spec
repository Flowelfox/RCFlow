# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files
from PyInstaller.utils.hooks import collect_submodules
from PyInstaller.utils.hooks import collect_all

datas = [('/home/flowelfox/Projects/RCFlow/.worktrees/claude-agent-sdk/src/prompts/templates', 'templates')]
binaries = []
hiddenimports = ['uvicorn.logging', 'uvicorn.loops', 'uvicorn.loops.auto', 'uvicorn.protocols', 'uvicorn.protocols.http', 'uvicorn.protocols.http.auto', 'uvicorn.protocols.websockets', 'uvicorn.protocols.websockets.auto', 'uvicorn.lifespan', 'uvicorn.lifespan.on', 'uvicorn.lifespan.off', 'aiosqlite', 'sqlalchemy.dialects.sqlite', 'sqlalchemy.dialects.sqlite.aiosqlite', 'alembic', 'alembic.command', 'alembic.config', 'src', 'src.main', 'src.config', 'src.paths', 'src.__main__', 'src.api', 'src.api.http', 'src.api.ws', 'src.api.ws.input_text', 'src.api.ws.output_text', 'src.core', 'src.core.buffer', 'src.core.llm', 'src.core.permissions', 'src.core.prompt_router', 'src.core.session', 'src.database', 'src.database.engine', 'src.executors', 'src.executors.claude_code', 'src.executors.claude_code_sdk', 'src.executors.codex', 'src.logs', 'src.models', 'src.database.models', 'src.prompts', 'src.prompts.builder', 'src.services', 'src.services.tool_manager', 'src.services.tool_settings', 'src.tools', 'src.tools.loader', 'src.tools.registry', 'jinja2', 'pydantic', 'pydantic_settings', 'httpx', 'anthropic', 'aiohttp', 'src.gui', 'src.gui.windows', 'src.gui.core', 'src.gui.theme', 'src.gui.updater', 'pystray', 'pystray._appindicator', 'pystray._gtk', 'pystray._xorg', 'PIL', 'PIL.Image', 'PIL.ImageDraw', 'customtkinter', 'mcp', 'mcp.client.stdio']
datas += collect_data_files('customtkinter')
hiddenimports += collect_submodules('src')
hiddenimports += collect_submodules('uvicorn')
tmp_ret = collect_all('claude_agent_sdk')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['/home/flowelfox/Projects/RCFlow/.worktrees/claude-agent-sdk/src/__main__.py'],
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
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='rcflow',
)
