"""
Flash Farm: ouro e last hits do jogador x referencia de rank alto no heroi.
==========================================================================

Fonte: OpenDota /api/benchmarks?hero_id=ID[&bracket=N][&role=core|support]
  -> {"result": {"gold_per_min": [{"percentile", "value"}], "last_hits_per_min": [...],
                 "xp_per_min": [...]}}

  - bracket=8 (Imortal) costuma vir com tudo null; bracket=7 (Divino) vem cheio.
    Ordem tentada: 8, 7, sem bracket. Com bracket util usa a MEDIANA (0.5);
    sem bracket (todas as partidas) usa o percentil 0.8 (o "rank alto" do geral).
  - role=core|support e respeitado pelo endpoint (Anti-Mage support ~327 gpm,
    core ~673).

Cache em DATA_DIR/metacache/bench_<heroi>_<role>.json, validade 3 dias. Nada aqui
levanta excecao para o chamador: falhou -> None + print("[META] ...").
"""

import json
import time
import urllib.request

from copiloto import config

_API = "https://api.opendota.com/api/benchmarks"
_UA = "CopilotoDota2"
_TIMEOUT = 15
_TTL = 3 * 24 * 3600
_RETRY_S = 300                  # depois de uma falha, espera 5 min antes de tentar de novo
_LABELS = {8: "Imortal", 7: "Divino", None: "rank alto"}
_SUP_ROLES = ("sup4", "sup5", "support", "sup")
_LH_SUP_FACTOR = 0.3

_mem = {}       # chave -> dados ja carregados nesta execucao
_fail = {}      # chave -> instante da ultima falha


# ---------------------------------------------------------------- cache/rede

def _cache_path(key):
    d = config.DATA_DIR / "metacache"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{key}.json"


def _cache_read(key, allow_stale=False):
    """Le o cache em disco. Vencido so volta com allow_stale (rede caiu)."""
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
        print(f"[META] benchmarks: nao gravou cache {key}: {e}")


def _fetch_json(url):
    """GET JSON com User-Agent e timeout. Falha -> None (loga)."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:
        print(f"[META] benchmarks: falha em {url}: {e}")
        return None


# ---------------------------------------------------------------- dados

def _pick(rows, pct):
    """Valor do percentil pedido numa lista [{percentile, value}] (None se null)."""
    best = None
    for r in rows or []:
        try:
            if abs(float(r.get("percentile")) - pct) < 1e-6 and r.get("value") is not None:
                best = float(r["value"])
        except Exception:
            continue
    return best


def _parse(result, bracket):
    pct = 0.5 if bracket else 0.8
    gpm = _pick(result.get("gold_per_min"), pct)
    lhpm = _pick(result.get("last_hits_per_min"), pct)
    xpm = _pick(result.get("xp_per_min"), pct)
    if gpm is None or lhpm is None:
        return None
    return {"gpm": round(gpm, 1), "lhpm": round(lhpm, 2), "xpm": round(xpm or 0.0, 1),
            "label": _LABELS.get(bracket, "rank alto"), "percentile": pct}


def _download(hero_id, role):
    """Tenta bracket 8, 7 e sem bracket; devolve o primeiro com valores."""
    for bracket in (8, 7, None):
        url = f"{_API}?hero_id={int(hero_id)}"
        if bracket:
            url += f"&bracket={bracket}"
        if role:
            url += f"&role={role}"
        data = _fetch_json(url)
        if not isinstance(data, dict) or not isinstance(data.get("result"), dict):
            continue
        out = _parse(data["result"], bracket)
        if out:
            out["role"] = role or "all"
            return out
    return None


def _norm_role(role):
    return "support" if (role or "").lower() in _SUP_ROLES else "core"


def get(hero_id, role="core"):
    """Referencia {gpm, lhpm, xpm, label, percentile, role} do heroi ou None.
    role: core (padrao) | support | hc/mid/off/sup4/sup5."""
    try:
        role = _norm_role(role)
        key = f"bench_{int(hero_id)}_{role}"
        if key in _mem:
            return _mem[key]
        data = _cache_read(key)
        if data is None and time.time() - _fail.get(key, 0) > _RETRY_S:
            data = _download(hero_id, role)
            if data:
                _cache_write(key, data)
            else:
                _fail[key] = time.time()
                data = _cache_read(key, allow_stale=True)
                print(f"[META] benchmarks: sem dados p/ heroi {hero_id} ({role})"
                      + (" - usando cache vencido" if data else ""))
        if data:
            _mem[key] = data
        return data
    except Exception as e:
        print(f"[META] benchmarks.get falhou: {e}")
        return None


# ---------------------------------------------------------------- curva

def _lh_ramp(minutes):
    """Fator dos last hits: 0.55 no minuto 1 -> 1.0 no minuto 10 (linear)."""
    if minutes <= 1:
        return 0.55
    if minutes >= 10:
        return 1.0
    return 0.55 + 0.45 * (minutes - 1) / 9.0


def _gold_ramp(minutes):
    """Fator do ouro: 0.75 no minuto 1 -> 1.0 no minuto 12 (linear)."""
    if minutes <= 1:
        return 0.75
    if minutes >= 12:
        return 1.0
    return 0.75 + 0.25 * (minutes - 1) / 11.0


def expected(hero_id, clock_s, role="hc"):
    """Referencia {lh, nw} no instante clock_s (segundos de jogo) ou None.
    Suporte usa o benchmark role=support; se nao houver, o de core com lh*0.3."""
    try:
        minutes = max(0.0, float(clock_s) / 60.0)
        sup = _norm_role(role) == "support"
        ref = get(hero_id, "support") if sup else None
        lh_factor = 1.0
        if ref is None:
            ref = get(hero_id, "core")
            lh_factor = _LH_SUP_FACTOR if sup else 1.0
        if ref is None:
            return None
        lh = ref["lhpm"] * minutes * _lh_ramp(minutes) * lh_factor
        nw = 600 + ref["gpm"] * minutes * _gold_ramp(minutes)
        return {"lh": int(round(lh)), "nw": int(round(nw)), "label": ref["label"]}
    except Exception as e:
        print(f"[META] benchmarks.expected falhou: {e}")
        return None


def _state(atual, ref):
    if atual >= ref:
        return "ahead"
    if atual >= ref - max(1.0, 0.05 * ref):
        return "warning"
    return "behind"


def _entry(atual, ref):
    atual = int(atual or 0)
    return {"atual": atual, "ref": ref, "delta": atual - ref, "estado": _state(atual, ref)}


def compare(hero_id, clock_s, last_hits, net_worth, role="hc"):
    """Jogador x referencia: {lh: {atual, ref, delta, estado}, nw: {...}, label}.
    estado: ahead | warning (ate 5% abaixo) | behind. Antes de 55 s -> None."""
    try:
        if clock_s is None or float(clock_s) < 55:
            return None
        ref = expected(hero_id, clock_s, role)
        if not ref:
            return None
        return {"lh": _entry(last_hits, ref["lh"]), "nw": _entry(net_worth, ref["nw"]),
                "label": ref["label"]}
    except Exception as e:
        print(f"[META] benchmarks.compare falhou: {e}")
        return None


if __name__ == "__main__":
    for hid, nome, role in ((1, "Anti-Mage", "hc"), (26, "Lion", "sup5")):
        t0 = time.time()
        print(nome, "core:", get(hid, "core"))
        print(nome, "support:", get(hid, "support"))
        print(f"  (busca: {time.time() - t0:.2f}s)")
        for mm in (1, 5, 10, 20):
            print(f"  {mm:>2} min {role}:", expected(hid, mm * 60, role))
        print("  compare 10min lh=55 nw=4200:", compare(hid, 600, 55, 4200, role))
        print("  compare 40s:", compare(hid, 40, 0, 600, role))
