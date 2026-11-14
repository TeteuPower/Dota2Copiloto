"""
Counter Pick: meta da medalha do jogador + sugestao de picks por FUNCAO.
=======================================================================

Na selecao de herois, cruza tres coisas:
  1) a medalha do jogador (OpenDota /players/{account_id} -> rank_tier);
  2) o winrate de cada heroi NAQUELA medalha (OpenDota /heroStats, k_pick/k_win);
  3) os counters que o projeto ja tem (cache/matchups.json, via drafting.py).
e devolve 4 sugestoes para cada posicao (pos 1..5).

API publica (nenhuma funcao levanta excecao para o chamador):
  - player_medal(account_id)  -> {"tier", "nome", "estrelas"} ou None
  - meta(bracket)             -> {hero_id: {"wr": 0-1, "picks": int}}
  - hero_positions()          -> {hero_id: {pos: peso 0-1}}  (pos principal = 1.0)
  - suggest(enemy_ids, ally_ids, bans, bracket, role=None, matchups=None)

Fontes (todas publicas, sem chave; User-Agent "CopilotoDota2", timeout 15 s):
  - https://api.opendota.com/api/heroStats            (cache 1 dia)
  - https://api.opendota.com/api/players/{account_id} (cache 1 dia por conta)
  - https://api.opendota.com/api/explorer?sql=...     (cache 7 dias) -> posicao
    mais jogada de cada heroi em partidas PROFISSIONAIS dos ultimos 120 dias
    (a tabela player_matches do explorer so tem partidas pro/ligas).
Cache em config.DATA_DIR / "metacache". Se a rede falha, usa o cache vencido;
sem cache, cai no fallback (heroes.json do projeto / heuristica de funcao).

Medalhas no heroStats: 1=Arauto ... 7=Divino, 8=Imortal. Hoje a OpenDota manda
8_pick = 0 para todos os herois (Imortal entra junto do Divino), entao
bracket 8 usa os dados do 7 quando a amostra do 8 e pequena.

Winrate na medalha (com encolhimento bayesiano para amostras pequenas):
    wr = (k_win + PRIOR_N * wr_pub) / (k_pick + PRIOR_N)
  wr_pub = pub_win/pub_pick (todas as medalhas). Com milhares de jogos o
  encolhimento e desprezivel; herois raros na medalha ficam perto do geral.

Funcao (posicao) de cada heroi:
  peso[pos] = mistura de (a) dados reais pro do explorer e (b) heuristica pelas
  roles do heroStats, ponderada pela amostra:  (n*real + K*heur) / (n + K),
  K = POS_PRIOR_N. Depois normaliza para a posicao principal = 1.0.
  Heuristica (pontos por role; ver _heuristic_positions):
    Carry                         -> pos1 +3
    Carry + (Durable|Initiator)   -> pos3 +1
    Nuker|Escape (sem Support/Carry) -> pos2 +1 cada; ranged/int sem Support +1 pos2
    Carry + Nuker|Escape          -> pos2 +1
    Initiator (sem Support)       -> pos3 +1.5 ; Durable (sem Support) -> pos3 +1.5
    Initiator + Durable           -> pos3 +1 (extra)
    Support                       -> pos4 +2.5, pos5 +2.5
    Support + Disabler            -> pos5 +1
    Support + Nuker|Escape|Initiator -> pos4 +1 cada
    Pusher (sem Support)          -> pos3 +0.5 ; Jungler -> pos4 +0.5
  Um heroi entra numa funcao se peso >= MIN_ROLE_WEIGHT (0.5).

Score de cada candidato (em pontos percentuais):
  meta_pp     = (wr_na_medalha - 0.5) * 100
  counter_pp  = media de drafting.advantage(heroi, inimigo) * 100 sobre os
                inimigos ja escolhidos (mesma conta do drafting.py, MIN_GAMES
                incluso). Se a linha heroi->inimigo nao tem amostra, usa o
                espelho -advantage(inimigo, heroi). Sem inimigo -> 0.
  synergy_pp  = 0 (nao ha fonte publica confiavel e sem chave de sinergia por
                dupla; o campo existe para quando houver, ex. Stratz 'with').
  score       = 1*meta_pp + 2*counter_pp
"""

import json
import os
import threading
import time
import urllib.parse
import urllib.request

from copiloto import config

try:
    from copiloto.game import drafting
except Exception as _e:  # nunca quebrar o import
    drafting = None
    print(f"[META] drafting indisponivel: {_e}")

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------
UA = "CopilotoDota2"
TIMEOUT = 15
API = "https://api.opendota.com/api"
CDN = "https://cdn.cloudflare.steamstatic.com/apps/dota2/images/dota_react/heroes/"

TTL_HEROSTATS = 24 * 3600
TTL_PLAYER = 24 * 3600
TTL_POSITIONS = 7 * 24 * 3600

PRIOR_N = 200            # encolhimento do winrate por medalha
MIN_BRACKET_TOTAL = 20000  # picks somados abaixo disso = medalha sem dados -> usa vizinha
POS_PRIOR_N = 20         # peso da heuristica frente aos dados pro de posicao
POS_DAYS = 120           # janela de partidas pro para posicao
MIN_ROLE_WEIGHT = 0.5
PICKS_PER_ROLE = 4
W_META = 1.0
W_COUNTER = 2.0

MEDALHAS = {1: "Arauto", 2: "Guardião", 3: "Cruzado", 4: "Arconte",
            5: "Lenda", 6: "Ancestral", 7: "Divino", 8: "Imortal"}
POS_TITULO = {1: "Carry (pos 1)", 2: "Meio (pos 2)", 3: "Offlane (pos 3)",
              4: "Suporte (pos 4)", 5: "Suporte (pos 5)"}
ROLE_POS = {"hc": 1, "carry": 1, "mid": 2, "off": 3, "offlane": 3,
            "sup4": 4, "sup5": 5, "1": 1, "2": 2, "3": 3, "4": 4, "5": 5}
STEAM64_BASE = 76561197960265728

_lock = threading.Lock()
_mem = {}   # nome do cache -> (ts, dados)
_fail = {}  # nome do cache -> ts da ultima falha (evita martelar a rede offline)
RETRY_AFTER = 300


# ---------------------------------------------------------------------------
# Infra: diretorio de cache, HTTP, leitura/escrita
# ---------------------------------------------------------------------------
def _cache_dir():
    d = config.DATA_DIR / "metacache"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        print(f"[META] nao criou {d}: {e}")
    return d


def _http_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8"))


def _read_cache(name):
    """-> (ts, data) ou (0, None)."""
    with _lock:
        if name in _mem:
            return _mem[name]
    try:
        with open(_cache_dir() / name, encoding="utf-8") as f:
            obj = json.load(f)
        val = (float(obj.get("ts", 0)), obj.get("data"))
        with _lock:
            _mem[name] = val
        return val
    except FileNotFoundError:
        return 0, None
    except Exception as e:
        print(f"[META] cache {name} ilegivel: {e}")
        return 0, None


def _write_cache(name, data):
    ts = time.time()
    with _lock:
        _mem[name] = (ts, data)
    try:
        p = _cache_dir() / name
        tmp = p.with_suffix(p.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"ts": ts, "data": data}, f, ensure_ascii=False)
        os.replace(tmp, p)
    except Exception as e:
        print(f"[META] nao gravou cache {name}: {e}")


def _cached_fetch(name, ttl, fetch):
    """Cache fresco -> usa; senao busca; se falhar, usa o vencido (ou None)."""
    ts, data = _read_cache(name)
    if data is not None and time.time() - ts < ttl:
        return data
    if time.time() - _fail.get(name, 0) < RETRY_AFTER:
        return data          # falhou ha pouco: nao tenta de novo ainda
    try:
        t0 = time.time()
        new = fetch()
        print(f"[META] {name} baixado em {time.time() - t0:.1f}s")
        _write_cache(name, new)
        return new
    except Exception as e:
        _fail[name] = time.time()
        if data is not None:
            print(f"[META] falha ao atualizar {name} ({e}); usando cache antigo")
            return data
        print(f"[META] falha ao buscar {name}: {e}")
        return None


# ---------------------------------------------------------------------------
# Medalha do jogador
# ---------------------------------------------------------------------------
def _to_account_id(account_id):
    try:
        a = int(str(account_id).strip())
    except Exception:
        return None
    if a >= STEAM64_BASE:          # veio steamid64 -> converte
        a -= STEAM64_BASE
    return a if a > 0 else None


def player_medal(account_id):
    """{"tier": 1-8, "nome": "...", "estrelas": n} ou None (perfil privado/sem rank/erro)."""
    try:
        aid = _to_account_id(account_id)
        if not aid:
            return None

        def fetch():
            d = _http_json(f"{API}/players/{aid}")
            return {"rank_tier": d.get("rank_tier"),
                    "leaderboard_rank": d.get("leaderboard_rank")}

        d = _cached_fetch(f"player_{aid}.json", TTL_PLAYER, fetch)
        rt = (d or {}).get("rank_tier")
        if not rt:
            return None
        rt = int(rt)
        tier, stars = rt // 10, rt % 10
        if tier not in MEDALHAS:
            return None
        out = {"tier": tier, "nome": MEDALHAS[tier], "estrelas": stars}
        if d.get("leaderboard_rank"):
            out["leaderboard"] = d["leaderboard_rank"]
        return out
    except Exception as e:
        print(f"[META] player_medal falhou: {e}")
        return None


# ---------------------------------------------------------------------------
# heroStats / meta por medalha
# ---------------------------------------------------------------------------
_HS_KEYS = ("id", "name", "localized_name", "roles", "primary_attr",
            "attack_type", "pub_pick", "pub_win") + tuple(
    f"{k}_{s}" for k in range(1, 9) for s in ("pick", "win"))


def hero_stats():
    """Lista enxuta do heroStats (cache 1 dia). Fallback: heroes.json do projeto."""
    def fetch():
        d = _http_json(f"{API}/heroStats")
        if not isinstance(d, list) or not d:
            raise ValueError("heroStats vazio")
        return [{k: h.get(k) for k in _HS_KEYS} for h in d]

    d = _cached_fetch("herostats.json", TTL_HEROSTATS, fetch)
    if d:
        return d
    # Fallback offline: heroes.json do projeto (so winrate global, sem medalha)
    out = []
    for h in (getattr(drafting, "HEROES", None) or []):
        out.append({"id": h.get("id"), "name": h.get("name"),
                    "localized_name": h.get("localized_name"),
                    "roles": h.get("roles") or [],
                    "primary_attr": h.get("primary_attr"),
                    "attack_type": h.get("attack_type"),
                    "_wr_global": h.get("winrate_global", 0.5)})
    if out:
        print("[META] usando heroes.json local (sem winrate por medalha)")
    return out


def _effective_bracket(stats, bracket):
    """Medalha com dados: se a pedida tem pouca amostra, desce/sobe para a vizinha."""
    try:
        b = int(bracket)
    except Exception:
        return None
    if b not in MEDALHAS:
        return None
    order = [b] + [x for d in range(1, 8) for x in (b - d, b + d) if 1 <= x <= 8]
    for k in order:
        tot = sum(int(h.get(f"{k}_pick") or 0) for h in stats)
        if tot >= MIN_BRACKET_TOTAL:
            return k
    return None


def meta(bracket):
    """{hero_id: {"wr": float 0-1, "picks": int}} para a medalha (1-8).

    bracket invalido/None -> todas as medalhas (pub). Nunca levanta excecao.
    """
    try:
        return _meta(bracket)[0]
    except Exception as e:
        print(f"[META] meta falhou: {e}")
        return {}


def _meta(bracket):
    stats = hero_stats()
    k = _effective_bracket(stats, bracket)
    out = {}
    for h in stats:
        hid = h.get("id")
        if hid is None:
            continue
        if "_wr_global" in h:          # fallback local
            out[hid] = {"wr": float(h["_wr_global"] or 0.5), "picks": 0}
            continue
        pp, pw = int(h.get("pub_pick") or 0), int(h.get("pub_win") or 0)
        wr_pub = pw / pp if pp else 0.5
        if k is None:
            out[hid] = {"wr": round(wr_pub, 4), "picks": pp}
            continue
        kp, kw = int(h.get(f"{k}_pick") or 0), int(h.get(f"{k}_win") or 0)
        wr = (kw + PRIOR_N * wr_pub) / (kp + PRIOR_N)
        out[hid] = {"wr": round(wr, 4), "picks": kp}
    return out, k


# ---------------------------------------------------------------------------
# Funcao (posicao) de cada heroi
# ---------------------------------------------------------------------------
def _heuristic_positions(h):
    """Pontos por posicao a partir das roles do heroStats (regras no docstring)."""
    r = set(h.get("roles") or [])
    sup = "Support" in r
    ranged = h.get("attack_type") == "Ranged"
    p = {1: 0.0, 2: 0.0, 3: 0.0, 4: 0.0, 5: 0.0}
    if "Carry" in r:
        p[1] += 3
        if "Durable" in r or "Initiator" in r:
            p[3] += 1
        if "Nuker" in r or "Escape" in r:
            p[2] += 1
    if not sup and "Carry" not in r:   # carry ja ganhou o +1 de pos2 acima
        if "Nuker" in r:
            p[2] += 1
        if "Escape" in r:
            p[2] += 1
    if not sup:
        if ranged or h.get("primary_attr") == "int":
            p[2] += 1
        if "Initiator" in r:
            p[3] += 1.5
        if "Durable" in r:
            p[3] += 1.5
        if "Pusher" in r:
            p[3] += 0.5
    if "Initiator" in r and "Durable" in r:
        p[3] += 1
    if sup:
        p[4] += 2.5
        p[5] += 2.5
        if "Disabler" in r:
            p[5] += 1
        for role in ("Nuker", "Escape", "Initiator"):
            if role in r:
                p[4] += 1
    if "Jungler" in r:
        p[4] += 0.5
    if sum(p.values()) <= 0:
        p[3] = 1.0
    return p


_POS_SQL = """WITH p AS (
 SELECT hero_id, lane_role,
  rank() OVER (PARTITION BY match_id, (player_slot < 128)
               ORDER BY gold_per_min DESC, player_slot) r
 FROM player_matches
 WHERE match_id > (SELECT min(match_id) FROM matches
                   WHERE start_time > extract(epoch from now() - interval '%d days'))
   AND gold_per_min IS NOT NULL),
q AS (SELECT hero_id, CASE
  WHEN r <= 3 AND lane_role = 1 THEN 1 WHEN r <= 3 AND lane_role = 2 THEN 2
  WHEN r <= 3 AND lane_role = 3 THEN 3 WHEN r <= 3 THEN r
  WHEN lane_role = 1 THEN 5 WHEN lane_role = 3 THEN 4 ELSE r END pos FROM p)
SELECT hero_id, pos, count(*) c FROM q GROUP BY hero_id, pos""" % POS_DAYS


def _real_positions():
    """{hero_id: {pos: jogos}} a partir de partidas pro (explorer). {} se falhar.

    Posicao inferida por partida: os 3 de maior GPM do time sao cores
    (lane 1 -> pos1, lane 2 -> pos2, lane 3 -> pos3); os 2 menores sao suportes
    (lane 1 = safe -> pos5, lane 3 = off -> pos4; senao pela ordem de GPM).
    """
    def fetch():
        d = _http_json(f"{API}/explorer?sql=" + urllib.parse.quote(_POS_SQL))
        rows = d.get("rows") if isinstance(d, dict) else None
        if not rows:
            raise ValueError(f"explorer sem linhas: {d.get('err') if isinstance(d, dict) else d}")
        out = {}
        for row in rows:
            out.setdefault(str(row["hero_id"]), {})[str(row["pos"])] = int(row["c"])
        return out

    return _cached_fetch("positions.json", TTL_POSITIONS, fetch) or {}


def hero_positions():
    """{hero_id: {pos: peso}} com a posicao principal = 1.0."""
    try:
        real = _real_positions()
        out = {}
        for h in hero_stats():
            hid = h.get("id")
            if hid is None:
                continue
            heur = _heuristic_positions(h)
            hs = sum(heur.values()) or 1.0
            cnt = {int(k): v for k, v in (real.get(str(hid)) or {}).items()
                   if str(k).isdigit() and 1 <= int(k) <= 5}
            n = sum(cnt.values())
            mix = {}
            for pos in range(1, 6):
                share_real = cnt.get(pos, 0) / n if n else 0.0
                mix[pos] = (n * share_real + POS_PRIOR_N * heur[pos] / hs) / (n + POS_PRIOR_N)
            top = max(mix.values()) or 1.0
            out[hid] = {pos: round(v / top, 3) for pos, v in mix.items()}
        return out
    except Exception as e:
        print(f"[META] hero_positions falhou: {e}")
        return {}


# ---------------------------------------------------------------------------
# Counter (reaproveita drafting.py)
# ---------------------------------------------------------------------------
def _adv_fn(matchups):
    """Funcao advantage(a, b) em [-0.5, 0.5]; usa drafting.advantage por padrao."""
    min_games = getattr(drafting, "MIN_GAMES", 50)

    if matchups:
        def raw(a, b):
            row = matchups.get(str(a), {}).get(str(b))
            if not row or row.get("games", 0) < min_games:
                return None
            return row["wins"] / row["games"] - 0.5
    else:
        mt = getattr(drafting, "MATCHUP", None) or {}

        def raw(a, b):
            row = mt.get(str(a), {}).get(str(b))
            if not row or row.get("games", 0) < min_games:
                return None
            return drafting.advantage(a, b)   # mesma conta do drafting.py

    def adv(a, b):
        v = raw(a, b)
        if v is None:
            m = raw(b, a)            # espelho: se b vence a, a perde de b
            v = -m if m is not None else 0.0
        return v

    return adv


# ---------------------------------------------------------------------------
# Sugestao
# ---------------------------------------------------------------------------
def _img(npc):
    return CDN + (npc or "").replace("npc_dota_hero_", "") + ".png"


def _motivo(meta_pp, wr, vs, bracket_nome, picks):
    partes = []
    bons = [v for v in vs if v["adv"] > 0.5]
    ruins = [v for v in vs if v["adv"] < -1.0]
    if bons:
        partes.append("Forte vs " + ", ".join(f"{v['name']} (+{v['adv']:.1f})" for v in bons[:2]))
    if meta_pp >= 1.0:
        partes.append(f"meta {wr*100:.1f}% no {bracket_nome}")
    elif meta_pp <= -1.5:
        partes.append(f"só {wr*100:.1f}% no {bracket_nome}")
    if ruins:
        partes.append("cuidado com " + ruins[0]["name"])
    if not partes:
        partes.append(f"sólido: {wr*100:.1f}% no {bracket_nome}" if picks else "opção equilibrada")
    s = "; ".join(partes)
    return s[0].upper() + s[1:]


def _ids(xs):
    out = []
    for x in xs or []:
        try:
            out.append(int(x))
        except Exception:
            pass
    return out


def suggest(enemy_ids, ally_ids, bans, bracket, role=None, matchups=None):
    """Sugestoes por funcao (pos 1..5), 4 por funcao. Nunca levanta excecao."""
    try:
        return _suggest(enemy_ids, ally_ids, bans, bracket, role, matchups)
    except Exception as e:
        print(f"[META] suggest falhou: {e}")
        return {"bracket": None, "bracket_nome": None, "por_funcao": [],
                "ordem": [1, 2, 3, 4, 5], "erro": str(e)}


def _suggest(enemy_ids, ally_ids, bans, bracket, role, matchups):
    t0 = time.time()
    stats = hero_stats()
    by_id = {h["id"]: h for h in stats if h.get("id") is not None}
    mt, k = _meta(bracket)
    positions = hero_positions()
    adv = _adv_fn(matchups)

    enemy = [e for e in _ids(enemy_ids) if e in by_id]
    allies = _ids(ally_ids)
    taken = set(enemy) | set(allies) | set(_ids(bans))
    bracket_nome = MEDALHAS.get(k, "Geral") if k else "Geral"

    # Pontua cada heroi livre uma vez
    scored = {}
    for hid, h in by_id.items():
        if hid in taken:
            continue
        m = mt.get(hid) or {"wr": 0.5, "picks": 0}
        meta_pp = (m["wr"] - 0.5) * 100
        vs = [{"name": by_id[e].get("localized_name") or str(e),
               "adv": round(adv(hid, e) * 100, 1)} for e in enemy]
        counter_pp = (sum(adv(hid, e) for e in enemy) / len(enemy) * 100) if enemy else 0.0
        synergy_pp = 0.0
        score = W_META * meta_pp + W_COUNTER * counter_pp
        vs.sort(key=lambda v: v["adv"], reverse=True)
        scored[hid] = {
            "hero_id": hid,
            "name": h.get("localized_name") or h.get("name"),
            "img": _img(h.get("name")),
            "score": round(score, 2),
            "meta_pp": round(meta_pp, 2),
            "counter_pp": round(counter_pp, 2),
            "synergy_pp": synergy_pp,
            "vs": vs,
            "motivo": _motivo(meta_pp, m["wr"], vs, bracket_nome, m["picks"]),
        }

    por_funcao = []
    for pos in range(1, 6):
        cands = [s for hid, s in scored.items()
                 if (positions.get(hid) or {}).get(pos, 0) >= MIN_ROLE_WEIGHT]
        cands.sort(key=lambda s: s["score"], reverse=True)
        por_funcao.append({"pos": pos, "titulo": POS_TITULO[pos],
                           "picks": [dict(c) for c in cands[:PICKS_PER_ROLE]]})

    my_pos = ROLE_POS.get(str(role).lower()) if role is not None else None
    ordem = ([my_pos] if my_pos else []) + [p for p in range(1, 6) if p != my_pos]
    return {"bracket": k, "bracket_nome": bracket_nome, "por_funcao": por_funcao,
            "ordem": ordem, "n_inimigos": len(enemy),
            "ms": int((time.time() - t0) * 1000)}


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    t = time.time()
    res = suggest([14, 8], [], [], 6, role="off")
    print(f"\nsuggest em {time.time() - t:.2f}s | medalha {res['bracket']} "
          f"({res['bracket_nome']}) | ordem {res['ordem']}")
    by_pos = {f["pos"]: f for f in res["por_funcao"]}
    for pos in res["ordem"]:
        f = by_pos[pos]
        print(f"\n== {f['titulo']}")
        for p in f["picks"]:
            vs = ", ".join(f"{v['name']} {v['adv']:+.1f}" for v in p["vs"])
            print(f"  {p['name']:<20} score {p['score']:+6.2f}  meta {p['meta_pp']:+5.2f}"
                  f"  counter {p['counter_pp']:+5.2f}  [{vs}]  - {p['motivo']}")
    print("\nimg exemplo:", res["por_funcao"][0]["picks"][0]["img"] if res["por_funcao"][0]["picks"] else "-")
    for aid in (87278757, "76561198047544485", 0):
        t = time.time()
        print(f"player_medal({aid!r}) -> {player_medal(aid)}  ({time.time() - t:.2f}s)")
