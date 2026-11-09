"""
history.py - Diario da partida (persistente em disco): CADA PARTIDA = UM CONTEXTO
================================================================================

Tudo o que acontece numa partida fica num arquivo so (match_history/match_<id>.json):

  - "reports": cada scan do placar (placar dos 2 times com KDA, meus itens, ouro,
    nivel, relogio e o texto do relatorio) - formato antigo, mantido;
  - "events": o resto do diario - perguntas e respostas do chat/voz, relatorios
    rapidos de itens, picks lidos no draft.

Todo pedido a IA (chat, voz, relatorio do placar, relatorio de itens) recebe o
diario INTEIRO da partida (journal_text), entao o copiloto sabe tudo o que ja
foi visto, dito e recomendado - e, por estar em disco, nada se perde se o app
reiniciar (atualizacao, queda) no meio do jogo: a conversa e restaurada.
"""

import json
import os
import threading
import time

from copiloto import config

DIR = str(config.MATCH_HISTORY_DIR)
_lock = threading.Lock()

# Rotulos do diario (o que a IA le)
KIND_LABEL = {
    "pergunta": "JOGADOR PERGUNTOU",
    "resposta": "VOCE (COPILOTO) RESPONDEU",
    "relatorio": "RELATORIO TATICO (placar lido)",
    "itens": "RELATORIO RAPIDO DE ITENS",
    "draft": "DRAFT",
    "placar": "PLACAR",
}


def _path(match_id):
    mid = str(match_id) if match_id not in (None, "") else "sem_partida"
    safe = "".join(c for c in mid if c.isalnum() or c in "-_") or "sem_partida"
    return os.path.join(DIR, f"match_{safe}.json")


def _load_doc(match_id):
    try:
        with open(_path(match_id), "r", encoding="utf-8") as f:
            d = json.load(f) or {}
        return d if isinstance(d, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _save_doc(match_id, doc):
    os.makedirs(DIR, exist_ok=True)
    doc["match_id"] = match_id
    tmp = _path(match_id) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
    os.replace(tmp, _path(match_id))


def load(match_id):
    """Lista de relatorios (dicts) ja salvos desta partida (mais antigo -> mais novo)."""
    return _load_doc(match_id).get("reports", [])


def add_report(match_id, entry):
    """Acrescenta um relatorio (com placar/itens/etc) ao historico da partida."""
    try:
        with _lock:
            doc = _load_doc(match_id)
            doc.setdefault("reports", []).append(entry)
            _save_doc(match_id, doc)
    except Exception as e:
        print("[historico] falha ao salvar:", e)


def reports_text(match_id, limit=12):
    """Os textos dos relatorios da partida (p/ alimentar o chat / o proximo relatorio)."""
    return [r.get("report", "") for r in load(match_id)[-limit:] if r.get("report")]


def add_event(match_id, kind, text, clock=None):
    """Registra um acontecimento no diario da partida."""
    text = (text or "").strip()
    if not text:
        return
    try:
        with _lock:
            doc = _load_doc(match_id)
            doc.setdefault("events", []).append(
                {"at": time.time(), "clock": clock, "kind": kind, "text": text[:4000]})
            _save_doc(match_id, doc)
    except Exception as e:
        print("[historico] falha ao salvar evento:", e)


def _all_entries(match_id, include_chat=True):
    doc = _load_doc(match_id)
    out = []
    for r in doc.get("reports", []):
        if r.get("report"):
            out.append({"at": r.get("at") or 0, "clock": r.get("clock"),
                        "kind": "relatorio", "text": r["report"]})
    for e in doc.get("events", []):
        if not include_chat and e.get("kind") in ("pergunta", "resposta"):
            continue
        out.append(e)
    out.sort(key=lambda e: e.get("at") or 0)
    return out


def journal_lines(match_id, include_chat=False):
    """O diario linha a linha (pra mandar so as NOVIDADES a uma sessao aberta)."""
    out = []
    for e in _all_entries(match_id, include_chat):
        label = KIND_LABEL.get(e.get("kind"), (e.get("kind") or "").upper())
        when = f"[{e.get('clock')}] " if e.get("clock") else ""
        out.append(f"{when}{label}: {e.get('text')}")
    return out


def journal_text(match_id, include_chat=True, max_chars=16000):
    """O diario da partida em texto, do mais antigo ao mais novo. Se passar do
    limite, corta os MAIS ANTIGOS (os recentes importam mais) e avisa."""
    entries = _all_entries(match_id, include_chat)
    if not entries:
        return ""
    lines = []
    for e in entries:
        label = KIND_LABEL.get(e.get("kind"), (e.get("kind") or "").upper())
        when = f"[{e.get('clock')}] " if e.get("clock") else ""
        lines.append(f"{when}{label}: {e.get('text')}")
    kept, size = [], 0
    for line in reversed(lines):
        if size + len(line) > max_chars and kept:
            break
        kept.append(line)
        size += len(line) + 1
    kept.reverse()
    dropped = len(lines) - len(kept)
    head = f"({dropped} registro(s) mais antigo(s) omitido(s) por tamanho)\n" if dropped else ""
    return head + "\n".join(kept)


def chat_messages(match_id, limit=60):
    """A conversa (chat + voz) desta partida no formato do cerebro, pra restaurar
    o historico depois de reiniciar o app."""
    msgs = []
    for e in _all_entries(match_id, include_chat=True):
        if e.get("kind") == "pergunta":
            msgs.append({"role": "user", "content": e["text"]})
        elif e.get("kind") == "resposta":
            msgs.append({"role": "assistant", "content": e["text"]})
    return msgs[-limit:]
