"""
Configuracao central do Copiloto: portas, token, caminhos e versao.
====================================================================

Um lugar so pra tudo que depende de "onde as coisas ficam". Os modulos importam
daqui em vez de calcular caminhos relativos ao proprio arquivo.

Dois modos de execucao:
  - DEV (python main.py no repositorio): tudo relativo a raiz do repo, como sempre.
  - INSTALADO (exe congelado pelo PyInstaller): o codigo/recursos ficam na pasta
    de instalacao (somente leitura) e TUDO que o app escreve vai para
    %LOCALAPPDATA%/CopilotoDota2 (configs, chave, historico, prints, logs).
"""

import os
import sys
from pathlib import Path

# Versao: gerada no build (copiloto/_version.py, fora do git). Em dev = "dev".
try:
    from copiloto._version import __version__ as APP_VERSION
except Exception:
    APP_VERSION = "dev"

# Repositorio oficial (update-check e link de download do instalador)
GITHUB_REPO = "TeteuPower/Dota2Copiloto"

FROZEN = bool(getattr(sys, "frozen", False))

if FROZEN:
    # Pasta da instalacao (onde esta o .exe) e recursos empacotados (_internal)
    BASE_DIR = Path(sys.executable).resolve().parent
    RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", BASE_DIR))
    DATA_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "CopilotoDota2"
else:
    BASE_DIR = Path(__file__).resolve().parents[1]   # raiz do repositorio
    RESOURCE_DIR = BASE_DIR
    DATA_DIR = BASE_DIR

# Recursos (somente leitura no modo instalado)
CACHE_DIR = RESOURCE_DIR / "cache"          # herois, itens, icones (OpenDota)
GSI_CFG_PATH = RESOURCE_DIR / "config" / "gamestate_integration_copiloto.cfg"

# Dados do usuario (sempre gravaveis)
RUNTIME_DIR = DATA_DIR / "runtime"           # prints de captura, configs de execucao
MATCH_HISTORY_DIR = DATA_DIR / "match_history"
LOG_DIR = DATA_DIR / "logs"
SECRET_PATH = DATA_DIR / "openai_secret.json"
OVERLAY_CFG_PATH = RUNTIME_DIR / "overlay_config.json"

# Servidor GSI / painel
HOST = "0.0.0.0"          # 0.0.0.0 = aceita conexoes da rede (celular/2a tela)
PORT = 49317              # porta alta e incomum (evita conflito com apps na 3000)
AUTH_TOKEN = "copiloto-dota-secret"   # precisa bater com o .cfg do GSI

for _d in (DATA_DIR, RUNTIME_DIR):
    _d.mkdir(parents=True, exist_ok=True)
