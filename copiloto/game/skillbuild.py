"""
Skill Certa: ordem de habilidades e talentos do heroi + proxima skill a subir.
=============================================================================

Fontes:
  - dotaconstants (raw.githubusercontent.com/odota/dotaconstants/master/build/):
      hero_abilities.json  {"npc_dota_hero_x": {"abilities": [...], "talents": [{name, level 1..4}]}}
      ability_ids.json     {"<id>": "nome_interno"}
      abilities.json       {nome: {dname, is_innate, ...}}  (2 MB -> guardamos so dname/inata)
  - OpenDota Explorer (SQL no banco publico):
      GET https://api.opendota.com/api/explorer?sql=<SQL url-encoded>
      SQL usado (rapido, 0.3-1.5 s; o join com matches = partidas pro/ligas):
        select pm.ability_upgrades_arr from player_matches pm join matches m using(match_id)
        where pm.hero_id=ID and m.start_time > extract(epoch from now() - interval '60 days')
          and array_length(pm.ability_upgrades_arr, 1) >= 10
        order by m.match_id desc limit 300
      Se vierem menos de 40 partidas, cai para as 300 mais recentes sem filtro de data
      (mesmo SQL sem o join/janela; cobre ~4-5 meses para herois pouco jogados).

Agregacao (ordem): para cada posicao k (0..23) escolhe a habilidade mais frequente
entre as partidas cujo PREFIXO (k primeiras escolhas, como multiconjunto) bate com a
ordem ja montada; se forem poucas (<8), usa todas. So entram escolhas LEGAIS no
nivel do k-esimo ponto (niveis 1-16, 18, 20, 25-30; 17/19/21-24 nao dao ponto): basica max 4 (nivel >= 2*n+1), ultimate max 3 (niveis 6/12/18), talento
1 por tier a partir de 10/15/20/25. Limites maiores (Invoker) vem dos dados.

Lado dos talentos: na arvore do jogo o PRIMEIRO talento de cada par (ordem do
hero_abilities.json = Ability10, 12, ... do npc_heroes) fica a DIREITA. Conferido
na Liquipedia (Lion, nivel 10: t1r = "+10% Mana Drain Slow" =
special_bonus_unique_lion_6, que e o 1o do par; t1l = movimento, o 2o).

Cache em DATA_DIR/metacache (validade 3 dias). Nada levanta excecao: falhou ->
None + print("[META] ...").
"""

import json
import time
import urllib.parse
import urllib.request

from copiloto import config

_CONST = "https://raw.githubusercontent.com/odota/dotaconstants/master/build"
_EXPLORER = "https://api.opendota.com/api/explorer?sql="
_UA = "CopilotoDota2"
_TIMEOUT = 15
_TTL = 3 * 24 * 3600
_RETRY_S = 300
# Niveis que dao ponto de habilidade (17, 19 e 21-24 NAO dao). Conferido nos dados:
# a 3a Mana Void (nivel 18) cai na posicao 16 do array, o talento do nivel 20 na 17
# e o do nivel 25 na 18. Logo posicao k do array -> nivel _POINT_LEVELS[k].
_POINT_LEVELS = list(range(1, 17)) + [18, 20] + list(range(25, 31))
_POSITIONS = len(_POINT_LEVELS)
_MIN_ROWS = 40
_MIN_COHERENT = 8
ATTR = "special_bonus_attributes"
_TIER_LEVEL = {1: 10, 2: 15, 3: 20, 4: 25}
_KEYS = ["Q", "W", "E", "D", "F"]

_SQL_RECENT = (
    "select pm.ability_upgrades_arr from player_matches pm join matches m using(match_id) "
    "where pm.hero_id={hid} and m.start_time > extract(epoch from now() - interval '60 days') "
    "and array_length(pm.ability_upgrades_arr, 1) >= 10 "
    "order by m.match_id desc limit 300")
_SQL_ANY = (
    "select pm.ability_upgrades_arr from player_matches pm "
    "where pm.hero_id={hid} and array_length(pm.ability_upgrades_arr, 1) >= 10 "
    "order by pm.match_id desc limit 300")

_mem = {}
_fail = {}


# ---------------------------------------------------------------- cache/rede

def _cache_path(key):
    d = config.DATA_DIR / "metacache"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{key}.json"


def _cache_read(key, allow_stale=False):
    try:
        with open(_cache_path(key), encoding="utf-8") as f:
            blob = json.load(f)
        if allow_stale or time.time() - blob.get("ts", 0) < _TTL:
            return blob.get("data")
    except Exception:
        pass
    return None


def _cache_write(key, data):
    try:
        with open(_cache_path(key), "w", encoding="utf-8") as f:
            json.dump({"ts": time.time(), "data": data}, f)
    except Exception as e:
        print(f"[META] skillbuild: nao gravou cache {key}: {e}")


def _fetch_json(url):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:
        print(f"[META] skillbuild: falha em {url[:120]}: {e}")
        return None


def _cached(key, producer):
    """Cache em disco -> producer() (rede) -> cache vencido. None se nada servir."""
    if key in _mem:
        return _mem[key]
    data = _cache_read(key)
    if data is None and time.time() - _fail.get(key, 0) > _RETRY_S:
        try:
            data = producer()
        except Exception as e:
            print(f"[META] skillbuild: erro montando {key}: {e}")
            data = None
        if data:
            _cache_write(key, data)
        else:
            _fail[key] = time.time()
            data = _cache_read(key, allow_stale=True)
    if data is not None:
        _mem[key] = data
    return data


# ---------------------------------------------------------------- constantes

def _slim_abilities():
    raw = _fetch_json(f"{_CONST}/abilities.json")
    if not isinstance(raw, dict):
        return None
    return {k: {"dname": v.get("dname") or "", "innate": str(v.get("is_innate")) == "True"}
            for k, v in raw.items() if isinstance(v, dict) and v.get("dname")}


def _consts():
    """(hero_abilities, ability_ids, abilities). Dicts vazios se falhar."""
    ha = _cached("sb_hero_abilities", lambda: _fetch_json(f"{_CONST}/hero_abilities.json"))
    ids = _cached("sb_ability_ids", lambda: _fetch_json(f"{_CONST}/ability_ids.json"))
    ab = _cached("sb_abilities", _slim_abilities)
    return ha or {}, ids or {}, ab or {}


def _dname(name, ab):
    if name == ATTR:
        return "Atributos (+2)"
    d = (ab.get(name) or {}).get("dname")
    return d or name.replace("_", " ").title()


def _is_hidden(name, ab):
    n = name or ""
    return (not n or n == "generic_hidden" or "_empty" in n or n.startswith("plus_")
            or (ab.get(n) or {}).get("innate", False))


# ---------------------------------------------------------------- explorer

def _explorer_rows(hero_id):
    """Lista de arrays de ids (ability_upgrades_arr). [] se falhar."""
    out = []
    for sql in (_SQL_RECENT, _SQL_ANY):
        data = _fetch_json(_EXPLORER + urllib.parse.quote(sql.format(hid=int(hero_id))))
        rows = (data or {}).get("rows") if isinstance(data, dict) else None
        if data and data.get("err"):
            print(f"[META] skillbuild: explorer err: {str(data.get('err'))[:200]}")
        out = [r.get("ability_upgrades_arr") for r in rows or [] if r.get("ability_upgrades_arr")]
        if len(out) >= _MIN_ROWS:
            return out, sql is _SQL_RECENT
    return out, False


# ---------------------------------------------------------------- agregacao

def _caps(seqs, basics, ult):
    """Max de niveis por skill: 4/3 padrao; maior so se os dados insistirem."""
    caps = {a: 4 for a in basics}
    if ult:
        caps[ult] = 3
    for a in caps:
        per = sorted(s.count(a) for s in seqs)
        if per:
            p80 = per[int(len(per) * 0.8)]
            if p80 > caps[a]:
                caps[a] = p80
    return caps


def _legal(name, lvl, state, caps, ult, tier_of, tiers_done):
    if name == ATTR:
        return True
    if name in tier_of:
        t = tier_of[name]
        return t not in tiers_done and lvl >= _TIER_LEVEL[t]
    n = state.get(name, 0)
    if n >= caps.get(name, 0):
        return False
    if name == ult:
        return lvl >= 6 + 6 * n
    if caps.get(name, 4) <= 4:
        return lvl >= 2 * n + 1
    return True


def _aggregate(seqs, basics, ult, talents):
    """Ordem de skills (lista de nomes) pela moda por posicao com prefixo coerente."""
    tier_of = {t["name"]: t["level"] for t in talents}
    caps = _caps(seqs, basics, ult)
    order, state, tiers_done = [], {}, set()
    for k in range(_POSITIONS):
        alive = [s for s in seqs if len(s) > k]
        if len(alive) < 3:
            break
        coherent = [s for s in alive if _same_prefix(s, k, state, tiers_done, tier_of)]
        pool = coherent if len(coherent) >= _MIN_COHERENT else alive
        counts = {}
        for s in pool:
            counts[s[k]] = counts.get(s[k], 0) + 1
        pick = None
        for name, _n in sorted(counts.items(), key=lambda kv: -kv[1]):
            if name and _legal(name, _POINT_LEVELS[k], state, caps, ult, tier_of, tiers_done):
                pick = name
                break
        if not pick:
            break
        order.append(pick)
        state[pick] = state.get(pick, 0) + 1
        if pick in tier_of:
            tiers_done.add(tier_of[pick])
    return order


def _same_prefix(seq, k, state, tiers_done, tier_of):
    """O prefixo seq[:k] tem as mesmas contagens (talento = mesmo tier) que a ordem."""
    cnt, tiers = {}, set()
    for name in seq[:k]:
        if name in tier_of:
            tiers.add(tier_of[name])
        else:
            cnt[name] = cnt.get(name, 0) + 1
    base = {a: n for a, n in state.items() if a not in tier_of}
    return cnt == base and tiers == tiers_done


def _talent_stats(seqs, talents, ab):
    """Por tier, o talento mais escolhido (+ % de escolha entre os dois)."""
    out = []
    for tier in (1, 2, 3, 4):
        pair = [t["name"] for t in talents if t.get("level") == tier]
        if len(pair) < 2:
            continue
        cnt = {p: 0 for p in pair}
        for s in seqs:
            first = next((x for x in s if x in cnt), None)
            if first:
                cnt[first] += 1
        tot = sum(cnt.values())
        if not tot:
            continue
        best = max(pair, key=lambda p: cnt[p])
        out.append({"tier": tier, "nivel": _TIER_LEVEL[tier], "ability": best,
                    "nome": _dname(best, ab),
                    "lado": "direita" if pair.index(best) == 0 else "esquerda",
                    "pct": round(100.0 * cnt[best] / tot)})
    return out


def _make_build(hero_npc, hero_id):
    ha, ids, ab = _consts()
    hero = ha.get(hero_npc)
    if not hero or not ids:
        print(f"[META] skillbuild: sem constantes p/ {hero_npc}")
        return None
    talents = [t for t in hero.get("talents") or [] if t.get("name")]
    basics = [a for a in hero.get("abilities") or [] if not _is_hidden(a, ab)]
    ult = basics.pop() if basics else None
    known = set(basics) | {t["name"] for t in talents} | {ATTR} | ({ult} if ult else set())
    rows, recent = _explorer_rows(hero_id)
    seqs = []
    for arr in rows:
        names = [ids.get(str(i)) for i in arr]
        seqs.append([n if n in known else None for n in names])
    if not seqs:
        print(f"[META] skillbuild: explorer sem partidas p/ heroi {hero_id}")
        return None
    order = _aggregate(seqs, basics, ult, talents)
    if not order:
        return None
    janela = "ultimos 60 dias" if recent else "partidas mais recentes"
    return {
        "ordem": [{"nivel": _POINT_LEVELS[i], "ability": a, "nome": _dname(a, ab)}
                  for i, a in enumerate(order)],
        "talentos": _talent_stats(seqs, talents, ab),
        "ultimate": ult,
        "amostra": len(seqs),
        "fonte": f"OpenDota Explorer ({len(seqs)} partidas pro/ligas, {janela})",
    }


def build(hero_name_npc, hero_id):
    """Build {ordem, talentos, amostra, fonte} do heroi ou None."""
    try:
        return _cached(f"sb_build_{int(hero_id)}", lambda: _make_build(hero_name_npc, hero_id))
    except Exception as e:
        print(f"[META] skillbuild.build falhou: {e}")
        return None


# ---------------------------------------------------------------- em partida

def _bar(gsi_abilities, ult, ab):
    """Barra visivel: {nome: (slot, tecla, nivel)} na ordem ability0..N."""
    def idx(k):
        try:
            return int(k.replace("ability", ""))
        except Exception:
            return 99
    out, i = {}, 0
    for slot in sorted((gsi_abilities or {}).keys(), key=idx):
        a = gsi_abilities.get(slot) or {}
        name = a.get("name") or ""
        if _is_hidden(name, ab):
            continue
        is_ult = bool(a.get("ultimate")) or name == ult
        if is_ult:
            key = "R"
        else:
            key = _KEYS[i] if i < len(_KEYS) else None
            i += 1
        out[name] = (slot, key, int(a.get("level") or 0))
    return out


def _talents_taken(hero, talents):
    """Tiers ja escolhidos pelo GSI (talent_1..8: 2 por tier, na ordem do par)."""
    done = set()
    for tier in (1, 2, 3, 4):
        if hero.get(f"talent_{2 * tier - 1}") or hero.get(f"talent_{2 * tier}"):
            done.add(tier)
    return done


def points_at(level):
    """Pontos de habilidade ganhos ate o nivel (17, 19, 21-24 nao dao ponto)."""
    return sum(1 for lv in _POINT_LEVELS if lv <= int(level or 0))


def next_skill(build_, gsi_hero, gsi_abilities):
    """Proxima skill a subir {ability, nome, slot, tecla, talento} ou None
    (None tambem quando nao ha ponto pendente: pontos do nivel <= pontos gastos)."""
    try:
        if not build_ or not build_.get("ordem"):
            return None
        hero = gsi_hero or {}
        level = int(hero.get("level") or 0)
        _ha, _ids, ab = _consts()
        ult = build_.get("ultimate")
        bar = _bar(gsi_abilities, ult, ab)
        tiers = _talents_taken(hero, None)
        attrs = int(hero.get("attributes_level") or 0)
        spent = sum(v[2] for v in bar.values()) + len(tiers) + attrs
        if points_at(level) <= spent:
            return None
        tal_by_tier = {t["tier"]: t for t in build_.get("talentos") or []}
        cur = {n: v[2] for n, v in bar.items()}
        cur[ATTR] = attrs
        need = {}
        for step in build_["ordem"]:
            a = step["ability"]
            if a.startswith("special_bonus_") and a != ATTR:
                tier = next((t for t, v in tal_by_tier.items() if v["ability"] == a), None)
                if tier is None or tier in tiers or level < _TIER_LEVEL[tier]:
                    continue
                return {"ability": a, "nome": step["nome"], "slot": None, "tecla": None,
                        "talento": tal_by_tier[tier]}
            need[a] = need.get(a, 0) + 1
            if need[a] <= cur.get(a, 0):
                continue
            if a != ATTR and a not in bar:
                continue
            n = cur.get(a, 0)
            if a == ult and level < 6 + 6 * n:
                continue
            if a != ult and a != ATTR and level < 2 * n + 1:
                continue
            slot, key = (bar[a][0], bar[a][1]) if a in bar else (None, None)
            return {"ability": a, "nome": step["nome"], "slot": slot, "tecla": key,
                    "talento": None}
        return None
    except Exception as e:
        print(f"[META] skillbuild.next_skill falhou: {e}")
        return None


if __name__ == "__main__":
    for npc, hid in (("npc_dota_hero_antimage", 1), ("npc_dota_hero_lion", 26)):
        t0 = time.time()
        b = build(npc, hid)
        print(f"== {npc} ({time.time() - t0:.2f}s)")
        if not b:
            print("  None")
            continue
        print("  ", b["fonte"], "| amostra", b["amostra"])
        print("   ordem:", " > ".join(f"{s['nivel']}:{s['nome']}" for s in b["ordem"]))
        for t in b["talentos"]:
            print("   talento:", t)
    # simula GSI do Anti-Mage nivel 3 com Mana Break 1 e Blink 1 -> espera 1 ponto pendente
    b = build("npc_dota_hero_antimage", 1)
    gsi_ab = {
        "ability0": {"name": "antimage_mana_break", "level": 1, "ultimate": False},
        "ability1": {"name": "antimage_blink", "level": 1, "ultimate": False},
        "ability2": {"name": "antimage_counterspell", "level": 0, "ultimate": False},
        "ability3": {"name": "antimage_persectur", "level": 1, "ultimate": False},
        "ability4": {"name": "antimage_mana_void", "level": 0, "ultimate": True},
    }
    print("next lvl3:", next_skill(b, {"level": 3}, gsi_ab))
    print("next lvl2 (sem ponto):", next_skill(b, {"level": 2}, gsi_ab))
    gsi_ab["ability0"]["level"] = 3
    gsi_ab["ability1"]["level"] = 2
    print("next lvl6:", next_skill(b, {"level": 6}, gsi_ab))
    gsi_ab.update({"ability0": {"name": "antimage_mana_break", "level": 4},
                   "ability1": {"name": "antimage_blink", "level": 4},
                   "ability2": {"name": "antimage_counterspell", "level": 1},
                   "ability4": {"name": "antimage_mana_void", "level": 1, "ultimate": True}})
    print("next lvl11:", next_skill(b, {"level": 11}, gsi_ab))
    # nivel 17 com os 16 pontos gastos -> nada (17 nao da ponto); nivel 18 -> 3a ultimate
    gsi_ab.update({"ability2": {"name": "antimage_counterspell", "level": 4},
                   "ability4": {"name": "antimage_mana_void", "level": 2, "ultimate": True}})
    hero = {"level": 17, "talent_2": True, "talent_4": True}
    print("next lvl17 (16 gastos):", next_skill(b, hero, gsi_ab))
    hero["level"] = 18
    print("next lvl18:", next_skill(b, hero, gsi_ab))
