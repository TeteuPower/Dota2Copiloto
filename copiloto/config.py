"""
Configuracao central do Copiloto: portas, token e caminhos.
============================================================

Um lugar so pra tudo que depende de "onde as coisas ficam". Os modulos importam
daqui em vez de calcular caminhos relativos ao proprio arquivo - assim mover
codigo de pasta nao quebra nada.
"""

from pathlib import Path

# Raiz do repositorio (pai da pasta copiloto/)
BASE_DIR = Path(__file__).resolve().parents[1]

CACHE_DIR = BASE_DIR / "cache"                 # dados baixados (herois, itens, icones)
RUNTIME_DIR = BASE_DIR / "runtime"             # gerados em execucao (prints, configs)
MATCH_HISTORY_DIR = BASE_DIR / "match_history"  # relatorios por partida
SECRET_PATH = BASE_DIR / "openai_secret.json"   # chave OpenAI (gitignored, fica na raiz)
OVERLAY_CFG_PATH = RUNTIME_DIR / "overlay_config.json"

# Servidor GSI / painel
HOST = "0.0.0.0"          # 0.0.0.0 = aceita conexoes da rede (celular/2a tela)
PORT = 49317              # porta alta e incomum (evita conflito com apps na 3000)
AUTH_TOKEN = "copiloto-dota-secret"   # precisa bater com o .cfg do GSI

RUNTIME_DIR.mkdir(exist_ok=True)
