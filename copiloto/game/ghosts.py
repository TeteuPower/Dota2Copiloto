"""
MiniMapa Tracker: ultima posicao dos herois inimigos, a partir do GSI.
=====================================================================

Com "minimap" "1" no .cfg do GSI, cada pacote traz o bloco `minimap`:
{ "<id>": {"xpos","ypos","unitname","team","image",...}, ... } com as unidades
que o SEU time enxerga (o mesmo que o minimapa do jogo ja mostra). Nada de ler
tela nem memoria: a posicao vem em coordenadas do mundo (-8500..8500).

Regra: um heroi inimigo que estava no pacote anterior e SUMIU vira "fantasma"
na ultima posicao. Reapareceu -> o fantasma some. Fantasma expira depois do
TTL. Partida nova (matchid) -> zera tudo. 2 = Radiant, 3 = Dire.

Substitui o rastreador antigo (copiloto/overlay/tracker.py), que adivinhava os
herois pela COR dos pixels do minimapa capturado da tela.
"""

import threading
import time

WORLD_MIN, WORLD_MAX = -8500.0, 8500.0
WORLD_RANGE = WORLD_MAX - WORLD_MIN
MIN_CLOCK = -60          # so rastreia a partir do -1:00 (antes disso ha herois na base/fonte)
DEFAULT_TTL = 120.0      # segundos (configuravel no painel)

_lock = threading.Lock()
STATE = {
    "match": None,
    "my_team": 0,
    "prev": {},          # unitname -> {x, y, team}  (inimigos vistos no pacote anterior)
    "ghosts": {},        # unitname -> {x, y, team, since}
    "visible": [],       # [{unitname, x, y, team}] herois visiveis (pro modo de alinhamento)
    "buildings": [],     # [{x, y, kind}] construcoes (pro modo de alinhamento)
    "at": 0.0,           # ultimo pacote com minimapa
    "has_minimap": False,
}
TTL = [DEFAULT_TTL]


def world_to_unit(x, y):
    """Mundo -> fracao (0..1) do quadrado do minimapa (y invertido: norte em cima)."""
    return (x - WORLD_MIN) / WORLD_RANGE, 1.0 - (y - WORLD_MIN) / WORLD_RANGE


def _my_team(raw, units):
    for u in units:                                   # o proprio jogador tem image especial
        if u.get("image") == "minimap_herocircle_self":
            try:
                return int(u.get("team") or 0)
            except (TypeError, ValueError):
                pass
    tn = ((raw.get("player") or {}).get("team_name") or "").lower()
    return 3 if tn == "dire" else (2 if tn == "radiant" else 0)


def reset():
    with _lock:
        STATE.update(match=None, my_team=0, prev={}, ghosts={}, visible=[], buildings=[],
                     at=0.0, has_minimap=False)


def update(raw, now=None):
    """Recebe o pacote do GSI e atualiza os fantasmas. Barato: chamar a cada pacote."""
    now = now or time.time()
    mm = (raw or {}).get("minimap")
    if not isinstance(mm, dict) or not mm:
        return
    mapi = raw.get("map") or {}
    match = mapi.get("matchid")
    units = [u for u in mm.values() if isinstance(u, dict) and u.get("unitname")]
    with _lock:
        if match != STATE["match"]:
            STATE.update(match=match, prev={}, ghosts={})
        STATE["has_minimap"] = True
        STATE["at"] = now
        me = _my_team(raw, units)
        STATE["my_team"] = me
        enemy = 3 if me == 2 else (2 if me == 3 else 0)

        heroes, builds = [], []
        for u in units:
            name = u["unitname"]
            try:
                x, y, team = float(u.get("xpos")), float(u.get("ypos")), int(u.get("team") or 0)
            except (TypeError, ValueError):
                continue
            if name.startswith("npc_dota_hero_"):
                heroes.append({"unitname": name, "x": x, "y": y, "team": team})
            elif any(k in name for k in ("tower", "rax", "fort")):
                builds.append({"x": x, "y": y, "kind": "tower" if "tower" in name else "base"})
        STATE["visible"] = heroes
        STATE["buildings"] = builds

        try:
            clock = float(mapi.get("clock_time"))
        except (TypeError, ValueError):
            clock = 0.0
        if not enemy or clock < MIN_CLOCK:
            STATE["prev"] = {}
            return

        cur = {}
        for h in heroes:
            if h["team"] == enemy:
                cur[h["unitname"]] = h            # ilusoes: mesmo nome -> fica a ultima
        for name, h in STATE["prev"].items():
            if name not in cur and name not in STATE["ghosts"]:
                STATE["ghosts"][name] = {"x": h["x"], "y": h["y"], "team": h["team"], "since": now}
        for name in cur:
            STATE["ghosts"].pop(name, None)       # reapareceu
        ttl = TTL[0]
        if ttl and ttl > 0:
            for name in [n for n, g in STATE["ghosts"].items() if now - g["since"] > ttl]:
                STATE["ghosts"].pop(name, None)
        STATE["prev"] = cur


def snapshot(now=None):
    """Copia segura pro overlay/painel. ghosts = [{unitname, hero, u, v, age}]."""
    now = now or time.time()
    with _lock:
        ttl = TTL[0]
        out = []
        for name, g in STATE["ghosts"].items():
            age = now - g["since"]
            if ttl and ttl > 0 and age > ttl:
                continue
            u, v = world_to_unit(g["x"], g["y"])
            out.append({"unitname": name, "hero": name.replace("npc_dota_hero_", ""),
                        "u": u, "v": v, "age": age})
        vis = [dict(h, **dict(zip(("u", "v"), world_to_unit(h["x"], h["y"])))) for h in STATE["visible"]]
        bld = [dict(b, **dict(zip(("u", "v"), world_to_unit(b["x"], b["y"])))) for b in STATE["buildings"]]
        return {"ghosts": out, "visible": vis, "buildings": bld, "my_team": STATE["my_team"],
                "has_minimap": STATE["has_minimap"], "age": (now - STATE["at"]) if STATE["at"] else None}


if __name__ == "__main__":
    def pk(units, t, me="radiant", match="1"):
        return {"map": {"matchid": match, "clock_time": t}, "player": {"team_name": me},
                "minimap": {str(i): u for i, u in enumerate(units)}}
    me = {"unitname": "npc_dota_hero_lion", "xpos": -6000, "ypos": -6000, "team": 2,
          "image": "minimap_herocircle_self"}
    pudge = {"unitname": "npc_dota_hero_pudge", "xpos": 0, "ypos": 0, "team": 3}
    tower = {"unitname": "npc_dota_goodguys_tower1_mid", "xpos": -1500, "ypos": -1200, "team": 2}
    update(pk([me, pudge, tower], 100), 1000)
    print("visivel:", [h["unitname"] for h in snapshot(1000)["visible"]], "fantasmas:", snapshot(1000)["ghosts"])
    update(pk([me, tower], 101), 1001)
    print("sumiu ->", snapshot(1003)["ghosts"])
    update(pk([me, dict(pudge, xpos=800)], 102), 1002)
    print("voltou ->", snapshot(1004)["ghosts"])
    TTL[0] = 5
    update(pk([me], 103), 1003)
    print("ttl 5s, 10s depois ->", snapshot(1013)["ghosts"])
    update(pk([me], 104, match="2"), 1014)
    print("partida nova ->", snapshot(1014)["ghosts"])
