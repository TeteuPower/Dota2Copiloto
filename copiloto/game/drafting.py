"""
Motor do assistente de draft.
=============================

Carrega o cache da OpenDota (heroes.json + matchups.json) e expoe:
  - heroes_for_ui()            -> lista de herois para o grid do painel
  - score_candidates(state)    -> top-8 picks ranqueados (counter + funcao + meta)
  - parse_gsi_draft(raw)        -> tenta extrair o draft do bloco GSI (oportunista)
  - CACHE_OK                    -> False se o cache ainda nao foi baixado

O ranking e 100% deterministico (Python); a IA so verbaliza depois, se quiser.
state = {"enemy": [ids], "allies": [ids], "bans": [ids]}
"""

import json
import os
import re

from copiloto import config

CACHE_DIR = str(config.CACHE_DIR)
MIN_GAMES = 50          # matchups com menos jogos viram 0.5 (sem sinal confiavel)
TOP_N = 8

# Pesos do score (counter e o principal)
W_COUNTER = 1.0
W_ROLE = 0.5
W_META = 0.2
W_SYNERGY = 0.4         # reservado para quando Stratz ('with') estiver ligado

HEROES = []             # lista de dicts do heroes.json
MATCHUP = {}            # {str(id): {str(enemy_id): {"games","wins"}}}
BY_ID = {}              # int id -> hero dict
BY_NPC = {}             # "npc_dota_hero_x" -> int id
BY_CLASS = {}           # "x" -> int id
BY_LOCALIZED = {}       # nome localizado normalizado ("witchdoctor") -> int id
CACHE_OK = False


def _norm_name(s):
    """Normaliza um nome de heroi para casar (minusculo, so letras/numeros)."""
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def id_from_name(name):
    """Mapeia um nome localizado (ex: 'WITCH DOCTOR', 'Sand King') para o hero_id."""
    return BY_LOCALIZED.get(_norm_name(name))


def load():
    """Carrega os caches e monta os indices. Seguro chamar mais de uma vez."""
    global HEROES, MATCHUP, BY_ID, BY_NPC, BY_CLASS, BY_LOCALIZED, CACHE_OK
    try:
        with open(os.path.join(CACHE_DIR, "heroes.json"), encoding="utf-8") as f:
            HEROES = json.load(f)
        with open(os.path.join(CACHE_DIR, "matchups.json"), encoding="utf-8") as f:
            MATCHUP = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        HEROES, MATCHUP, CACHE_OK = [], {}, False
        return False

    BY_ID = {h["id"]: h for h in HEROES}
    BY_NPC = {h["name"]: h["id"] for h in HEROES if h.get("name")}
    BY_CLASS = {h["class"]: h["id"] for h in HEROES if h.get("class")}
    BY_LOCALIZED = {_norm_name(h["localized_name"]): h["id"]
                    for h in HEROES if h.get("localized_name")}
    CACHE_OK = len(HEROES) > 0 and len(MATCHUP) > 0
    return CACHE_OK


# ---------------------------------------------------------------------------
# Counter / advantage
# ---------------------------------------------------------------------------
def matchup_wr(a_id, b_id):
    """Winrate do heroi a quando enfrentou b. 0.5 se amostra pequena/ausente."""
    row = MATCHUP.get(str(a_id), {}).get(str(b_id))
    if not row or row["games"] < MIN_GAMES:
        return 0.5
    return row["wins"] / row["games"]


def advantage(a_id, b_id):
    """Vantagem de a contra b, em [-0.5, +0.5]. >0 = a countera b."""
    return matchup_wr(a_id, b_id) - 0.5


# ---------------------------------------------------------------------------
# Cobertura de funcao do time
# ---------------------------------------------------------------------------
def _team_needs(ally_ids):
    """Quais papeis o time (aliados ja pickados) ainda precisa."""
    roles = []
    has_int = False
    for aid in ally_ids:
        h = BY_ID.get(aid)
        if not h:
            continue
        roles += h.get("roles") or []
        if h.get("primary_attr") == "int":
            has_int = True
    rc = {r: roles.count(r) for r in set(roles)}
    return {
        "carry": rc.get("Carry", 0) == 0,
        "support": rc.get("Support", 0) < 1,
        "disabler": rc.get("Disabler", 0) == 0,
        "initiator": rc.get("Initiator", 0) == 0,
        "magico": rc.get("Nuker", 0) == 0 and not has_int,
    }


def _coverage(hero, needs):
    """Quanto o heroi atende das necessidades do time."""
    hroles = set(hero.get("roles") or [])
    score = 0.0
    filled = []
    if needs["carry"] and "Carry" in hroles:
        score += 3; filled.append("Carry")
    if needs["support"] and "Support" in hroles:
        score += 3; filled.append("Suporte")
    if needs["disabler"] and "Disabler" in hroles:
        score += 1.5; filled.append("Controle")
    if needs["initiator"] and "Initiator" in hroles:
        score += 1.5; filled.append("Iniciacao")
    if needs["magico"] and ("Nuker" in hroles or hero.get("primary_attr") == "int"):
        score += 1.0; filled.append("Dano magico")
    return score, filled


# ---------------------------------------------------------------------------
# Scoring principal
# ---------------------------------------------------------------------------
def score_candidates(state, full=False):
    """Ranqueia os herois disponiveis contra o estado atual do draft.

    full=False -> devolve so o top-8 (painel/dashboard).
    full=True  -> devolve TODOS os herois livres pontuados (grid da aba Draft,
                  que colore/ordena cada heroi pela vantagem contra os inimigos).
    """
    if not CACHE_OK:
        return {"ok": False, "error": "cache_indisponivel", "suggestions": []}

    enemy = [e for e in state.get("enemy", []) if e in BY_ID]
    allies = [a for a in state.get("allies", []) if a in BY_ID]
    bans = set(state.get("bans", []))
    taken = set(enemy) | set(allies) | bans

    needs = _team_needs(allies)
    results = []

    for h in HEROES:
        cid = h["id"]
        if cid in taken:
            continue

        # 1) Counter: vantagem media contra inimigos, menos o quanto sou counterado
        if enemy:
            atk = sum(advantage(cid, e) for e in enemy) / len(enemy)
            deff = sum(advantage(e, cid) for e in enemy) / len(enemy)
            counter_final = (atk * 100) - 0.5 * (deff * 100)
        else:
            counter_final = 0.0

        # 3) Cobertura de funcao
        cov, filled = _coverage(h, needs)

        # 4) Prior de meta (forca no patch)
        meta = (h.get("winrate_global", 0.5) - 0.5) * 100

        score = (W_COUNTER * counter_final
                 + W_ROLE * cov
                 + W_META * meta)

        # Motivos (melhores matchups contra os inimigos)
        best_vs = sorted(
            ({"enemy_id": e, "name": BY_ID[e]["localized_name"],
              "adv_pct": round(advantage(cid, e) * 100, 1)} for e in enemy),
            key=lambda x: x["adv_pct"], reverse=True,
        )[:2]

        reasons = []
        for bv in best_vs:
            if bv["adv_pct"] > 0:
                reasons.append(f"Forte vs {bv['name']} (+{bv['adv_pct']}%)")
        if filled:
            reasons.append("fecha " + "/".join(filled) + " do time")
        if h.get("winrate_global", 0) >= 0.52:
            reasons.append(f"meta {round(h['winrate_global']*100,1)}%")

        results.append({
            "hero_id": cid,
            "name": h["localized_name"],
            "img": h.get("img_url"),
            "primary_attr": h.get("primary_attr"),
            "score": round(score, 2),
            "counter_score": round(counter_final, 2),
            "adv_pct": round(counter_final, 1),   # vantagem p/ colorir o grid
            "role_score": round(cov, 1),
            "reasons": reasons,
            "best_vs": best_vs,
        })

    # Com inimigos marcados, ordena por vantagem (counter); senao, pelo score geral.
    key = "counter_score" if enemy else "score"
    results.sort(key=lambda r: r[key], reverse=True)
    n = len(results) if full else TOP_N
    return {"ok": True, "suggestions": results[:n],
            "n_enemy": len(enemy), "n_allies": len(allies)}


# ---------------------------------------------------------------------------
# Parser oportunista do bloco draft do GSI (so popula espectando/CM)
# ---------------------------------------------------------------------------
def _ids_from_team(team, prefix, count):
    ids = []
    for i in range(count):
        hid = team.get(f"{prefix}{i}_id")
        cls = team.get(f"{prefix}{i}_class")
        if hid and hid > 0:
            ids.append(hid)
        elif cls and cls in BY_CLASS:
            ids.append(BY_CLASS[cls])
    return ids


def parse_gsi_draft(raw):
    """Tenta extrair {enemy, allies, bans} do bloco draft do GSI.

    So funciona quando o bloco vem populado (espectador / Captains Mode).
    Numa partida All Pick jogada normalmente retorna None. Nunca lanca excecao.
    """
    try:
        draft = (raw or {}).get("draft")
        if not draft:
            return None
        t2, t3 = draft.get("team2") or {}, draft.get("team3") or {}
        if not t2 and not t3:
            return None

        picks2 = _ids_from_team(t2, "pick", 5)
        picks3 = _ids_from_team(t3, "pick", 5)
        bans = _ids_from_team(t2, "ban", 6) + _ids_from_team(t3, "ban", 6)

        # Descobre qual time e o nosso pelo heroi do jogador local
        my_npc = ((raw or {}).get("hero") or {}).get("name")
        my_id = BY_NPC.get(my_npc) if my_npc else None

        if my_id and my_id in picks2:
            allies, enemy = picks2, picks3
        elif my_id and my_id in picks3:
            allies, enemy = picks3, picks2
        else:
            return None  # nao da pra saber qual lado e o inimigo -> deixa manual

        return {"enemy": enemy, "allies": allies, "bans": bans}
    except Exception:
        return None


def heroes_for_ui():
    """Lista enxuta de herois para montar o grid do painel."""
    return [{
        "id": h["id"],
        "name": h["localized_name"],
        "class": h["class"],
        "primary_attr": h.get("primary_attr"),
        "img": h.get("img_url"),
    } for h in HEROES]


load()
