# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules
from PyInstaller.utils.hooks import collect_all

datas = [('E:/work/geminihub-local/frontend/out', 'static'), ('E:/work/geminihub-local/build/_clean_backend', 'backend'), ('E:/work/geminihub-local/version.json', '.'), ('E:/work/geminihub-local/build/_clean_skills', 'skills'), ('E:/work/geminihub-local/mcp_servers.json', '.')]
binaries = []
hiddenimports = ['openpyxl', 'backend.main', 'backend.app.api.router', 'backend.app.api.v1.artifacts', 'backend.app.api.v1.experiences', 'backend.app.api.v1.memories', 'backend.app.api.v1.skills', 'backend.app.api.v1.loop', 'backend.app.agent.runtime', 'backend.app.agent.context_builder', 'backend.app.agent.policy_interceptor', 'backend.app.agent.tool_protocol', 'backend.app.agent.shell_executor', 'backend.app.agent.ui_operator', 'backend.app.core.runtime_paths', 'backend.app.repository.db', 'backend.app.repository.artifact_repository', 'backend.app.repository.conversation_repository', 'backend.app.repository.message_repository', 'backend.app.repository.workspace_repository', 'backend.app.repository.loop_run_repository', 'backend.app.repository.scheduled_repository', 'backend.app.repository.search_repository', 'backend.app.service.engine_pool', 'backend.app.service.audit_service', 'backend.app.service.attachment_service', 'backend.app.memory', 'backend.app.memory.long_term', 'backend.app.memory.policy_guard', 'backend.app.memory.ledger', 'backend.app.memory.fact_manager', 'backend.app.memory.session_summary', 'backend.app.memory.workspace_fts', 'backend.app.skills', 'backend.app.skills.skill_service', 'backend.app.skills.experience_distiller', 'backend.app.extensions.skill_loader', 'backend.app.extensions.mcp_manager', 'backend.app.schema.chat_schema', 'backend.app.schema.loop_schema']
hiddenimports += collect_submodules('openpyxl')
hiddenimports += collect_submodules('et_xmlfile')
tmp_ret = collect_all('webview')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('uvicorn')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('fastapi')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('starlette')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('pydantic')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('pydantic_settings')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('psutil')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('aiosqlite')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('httpx')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('requests')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('clr_loader')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('pythonnet')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['E:/work/geminihub-local/desktop_app.py'],
    pathex=['E:/work/geminihub-local/backend'],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['pandas', 'numpy', 'scipy', 'matplotlib', 'pytest', '_pytest', 'tkinter', 'IPython', 'jupyter', 'notebook', 'setuptools.tests'],
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
    name='AgentHub',
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
