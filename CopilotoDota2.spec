# -*- mode: python ; coding: utf-8 -*-
"""Spec do PyInstaller: gera dist/CopilotoDota2/ (pasta com o exe SEM console).

Build normal:  scripts/build_installer.ps1  (gera versao + exe + instalador)
So o exe:      python -m PyInstaller --noconfirm CopilotoDota2.spec
"""

import os

from PyInstaller.utils.hooks import collect_all

# Dados empacotados (viram somente-leitura em _internal/ ao lado do exe)
datas = [
    ("cache/heroes.json", "cache"),
    ("cache/matchups.json", "cache"),
    ("cache/items.json", "cache"),
    ("cache/meta.json", "cache"),
    ("cache/icons", "cache/icons"),                      # retratos do overlay
    ("config/gamestate_integration_copiloto.cfg", "config"),
    ("assets/icon.ico", "assets"),
]
binaries = []
hiddenimports = []

# claude-agent-sdk carrega dados/submodulos proprios -> empacota tudo
# sounddevice: a DLL da PortAudio (_sounddevice_data) - sem ela nao toca nem grava
# pycaw/comtypes: volume por aplicativo (importados dentro de funcoes -> o
#   PyInstaller nao os achava sozinho; foi isso que deixou a voz muda no app instalado)
for _pkg in ("claude_agent_sdk", "sounddevice", "_sounddevice_data", "pycaw", "comtypes"):
    try:
        _d, _b, _h = collect_all(_pkg)
    except Exception as _e:
        print(f"[spec] {_pkg}: {_e}")
        continue
    datas += _d
    binaries += _b
    hiddenimports += _h
hiddenimports += ["sounddevice", "pycaw.pycaw", "comtypes.client", "comtypes.stream"]

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter"],
    noarchive=False,
)
pyz = PYZ(a.pure)

# Propriedades de versao do exe (geradas pelo build_installer.ps1; opcional)
_verfile = os.path.join(SPECPATH, "build", "version_info.txt")

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="CopilotoDota2",
    icon="assets/icon.ico",
    version=_verfile if os.path.exists(_verfile) else None,
    console=False,                # SEM janela preta: logs em %LOCALAPPDATA%/CopilotoDota2/logs
    disable_windowed_traceback=False,
    upx=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    upx=False,
    name="CopilotoDota2",
)
