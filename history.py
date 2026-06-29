"""
history.py - Historico de relatorios por partida (persistente em disco)
=======================================================================

Cada scan do placar vira um "relatorio" com TUDO que temos no momento (placar dos
2 times com KDA, meus itens, ouro, nivel, relogio e o texto do relatorio). Os
relatorios sao agrupados por partida (match_id) e salvos em match_history/.

Isso permite:
  - um historico revisavel de cada relatorio gerado, por partida;
  - o chat (Strategy) ler TODOS os relatorios da partida atual;
  - cada novo relatorio conhecer os anteriores (sem repetir).
"""

import os
import json

HERE = os.path.dirname(os.path.abspath(__file__))
DIR = os.path.join(HERE, "match_history")


def _path(match_id):
    mid = str(match_id) if match_id not in (None, "") else "sem_partida"
    safe = "".join(c for c in mid if c.isalnum() or c in "-_") or "sem_partida"
    return os.path.join(DIR, f"match_{safe}.json")


def load(match_id):
    """Lista de relatorios (dicts) ja salvos desta partida (mais antigo -> mais novo)."""
    try:
        with open(_path(match_id), "r", encoding="utf-8") as f:
            d = json.load(f) or {}
        return d.get("reports", []) if isinstance(d, dict) else []
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []


def add_report(match_id, entry):
    """Acrescenta um relatorio (com placar/itens/etc) ao historico da partida."""
    try:
        os.makedirs(DIR, exist_ok=True)
        reports = load(match_id)
        reports.append(entry)
        with open(_path(match_id), "w", encoding="utf-8") as f:
            json.dump({"match_id": match_id, "reports": reports}, f,
                      ensure_ascii=False, indent=2)
    except Exception as e:
        print("[historico] falha ao salvar:", e)


def reports_text(match_id, limit=12):
    """Os textos dos relatorios da partida (p/ alimentar o chat / o proximo relatorio)."""
    return [r.get("report", "") for r in load(match_id)[-limit:] if r.get("report")]
