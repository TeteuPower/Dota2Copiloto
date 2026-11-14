"""
Itens do Meta: build de itens do heroi por fase do jogo.
========================================================

Fontes (OpenDota):
  /api/heroes/{id}/itemPopularity -> {"start_game_items": {"<item_id>": n},
        "early_game_items": .., "mid_game_items": .., "late_game_items": ..}
        (contagem de compras em partidas PROFISSIONAIS recentes; amostra pequena,
        ~100 partidas por heroi)
  /api/constants/item_ids -> {"<id>": "slug"}
  /api/constants/items    -> {slug: {dname, cost, created, components, ...}}

Fases: inicio (itens iniciais), early (ate ~12 min), core (12-27 min), luxo (27+).
Botas saem das fases e vao numa lista propria. Componentes e consumiveis baratos
saem das fases early/core/luxo. pct = % das compras daquela fase (sobre o total
bruto de compras da fase, inclusive o que foi filtrado).

Cache em DATA_DIR/metacache (validade 3 dias). Nada levanta excecao: falhou ->
None + print("[META] ...").
"""

import json
import time
import urllib.request

from copiloto import config
from copiloto.game import items as _items

_BASE = "https://api.opendota.com/api"
_UA = "CopilotoDota2"
_TIMEOUT = 15
_TTL = 3 * 24 * 3600
_RETRY_S = 300
_MAX_PER_PHASE = 6
_MIN_COST = 500
FONTE = "OpenDota (partidas profissionais)"

_PHASES = (
    ("inicio", "Início", "start_game_items"),
    ("early", "Começo (até ~12 min)", "early_game_items"),
    ("core", "Core (12-27 min)", "mid_game_items"),
    ("luxo", "Luxo (27+ min)", "late_game_items"),
)

BOOTS = {"boots", "power_treads", "phase_boots", "arcane_boots", "tranquil_boots",
         "travel_boots", "travel_boots_2", "guardian_greaves", "boots_of_bearing",
         "force_boots", "pirate_hat"}
_JUNK = {"tpscroll", "ward_observer", "ward_sentry", "ward_dispenser", "tango", "tango_single",
         "clarity", "flask", "faerie_fire", "smoke_of_deceit", "dust", "enchanted_mango",
         "blood_grenade", "famango", "great_famango", "greater_famango", "cheese",
         "aegis", "refresher_shard", "courier", "flying_courier"}
# Itens basicos (nao montados) que valem como item "de verdade" mesmo sendo componente.
_KEEP_BASIC = {"blink", "aghanims_shard", "gem"}

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
        print(f"[META] metabuild: nao gravou cache {key}: {e}")


def _fetch_json(url):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:
        print(f"[META] metabuild: falha em {url}: {e}")
        return None


def _cached(key, url, transform=None):
    """Cache em disco -> rede -> cache vencido. Devolve None se nada servir."""
    if key in _mem:
        return _mem[key]
    data = _cache_read(key)
    if data is None and time.time() - _fail.get(key, 0) > _RETRY_S:
        raw = _fetch_json(url)
        if isinstance(raw, dict) and raw:
            data = transform(raw) if transform else raw
            _cache_write(key, data)
        else:
            _fail[key] = time.time()
            data = _cache_read(key, allow_stale=True)
    if data is not None:
        _mem[key] = data
    return data


# ---------------------------------------------------------------- catalogo

def _slim_items(raw):
    """Do constants/items so o que interessa: {slug: {dname, cost, created, comp}}."""
    out = {}
    for slug, v in raw.items():
        if isinstance(v, dict):
            out[slug] = {"dname": v.get("dname") or "", "cost": v.get("cost") or 0,
                         "created": bool(v.get("created")),
                         "components": v.get("components") or []}
    return out


def _catalog():
    """(ids {id: slug}, itens {slug: info}, componentes set). Vazio se falhar."""
    ids = _cached("mb_item_ids", f"{_BASE}/constants/item_ids") or {}
    info = _cached("mb_items", f"{_BASE}/constants/items", _slim_items) or {}
    comps = set()
    for v in info.values():
        comps.update(v.get("components") or [])
    return ids, info, comps


def _skip(slug, phase, info, comps):
    """True se o item nao deve aparecer na fase."""
    if not slug or slug.startswith("recipe_"):
        return True
    if phase == "inicio":
        return False
    if slug in _JUNK or slug.startswith("ward_"):
        return True
    it = info.get(slug)
    if it is None:
        return False
    if (it.get("cost") or 0) < _MIN_COST:
        return True
    # componente puro (Broadsword, Platemail...): fica de fora fora da whitelist
    return (not it.get("created")) and slug in comps and slug not in _KEEP_BASIC


def _entry(slug, count, total, info):
    name = (info.get(slug) or {}).get("dname") or slug.replace("_", " ").title()
    return {"slug": slug, "name": name, "img": _items.icon_url(slug),
            "pct": round(100.0 * count / total, 1) if total else 0.0}


def build(hero_id):
    """Build do meta {fases: [...], botas: [...], fonte} ou None."""
    try:
        pop = _cached(f"mb_itempop_{int(hero_id)}", f"{_BASE}/heroes/{int(hero_id)}/itemPopularity")
        if not pop:
            return None
        ids, info, comps = _catalog()
        if not ids:
            print("[META] metabuild: sem tabela de ids de itens")
            return None
        fases, boots = [], {}
        for fase, titulo, field in _PHASES:
            counts = {}
            for iid, n in (pop.get(field) or {}).items():
                slug = ids.get(str(iid))
                if slug:
                    counts[slug] = counts.get(slug, 0) + int(n or 0)
            total = sum(counts.values())
            chosen = []
            for slug, n in sorted(counts.items(), key=lambda kv: -kv[1]):
                if slug in BOOTS:
                    if fase != "inicio":
                        boots[slug] = boots.get(slug, 0) + n
                    continue
                if _skip(slug, fase, info, comps):
                    continue
                chosen.append(_entry(slug, n, total, info))
                if len(chosen) >= _MAX_PER_PHASE:
                    break
            fases.append({"fase": fase, "titulo": titulo, "itens": chosen})
        btotal = sum(boots.values())
        botas = [_entry(s, n, btotal, info)
                 for s, n in sorted(boots.items(), key=lambda kv: -kv[1])][:_MAX_PER_PHASE]
        return {"fases": fases, "botas": botas, "fonte": FONTE}
    except Exception as e:
        print(f"[META] metabuild.build falhou: {e}")
        return None


def current_phase(clock_s):
    """Fase do jogo pelo relogio: early (<12 min), core (<27 min), luxo."""
    try:
        m = float(clock_s or 0) / 60.0
    except Exception:
        return "early"
    if m < 12:
        return "early"
    return "core" if m < 27 else "luxo"


if __name__ == "__main__":
    for hid, nome in ((1, "Anti-Mage"), (26, "Lion")):
        t0 = time.time()
        b = build(hid)
        print(f"== {nome} ({time.time() - t0:.2f}s)")
        if not b:
            print("  None")
            continue
        for f in b["fases"]:
            print(f"  {f['titulo']}:", ", ".join(f"{i['name']} {i['pct']}%" for i in f["itens"]))
        print("  Botas:", ", ".join(f"{i['name']} {i['pct']}%" for i in b["botas"]))
        print("  exemplo item:", b["fases"][2]["itens"][:1])
    print("fases:", current_phase(300), current_phase(720), current_phase(1700))
