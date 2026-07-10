"""
Catalogo de itens do Dota 2 (nome interno <-> nome bonito + icone).
===================================================================

Serve para transformar os itens que o copiloto SUGERE no relatorio em ICONES no
painel. Cacheia cache/items.json = {slug: nome_bonito} da OpenDota (roda 1x / por
patch). Em partida NAO faz rede: so le o cache.

  slug   = nome interno sem 'item_' (ex.: 'black_king_bar')
  dname  = nome bonito (ex.: 'Black King Bar')
  icone  = {CDN}/items/{slug}.png  (mesmo padrao de item_icon_url no server.py)
"""

import json
import os
import re
import urllib.request

CDN = "https://cdn.cloudflare.steamstatic.com/apps/dota2/images/dota_react"
CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache", "items.json")
_API = "https://api.opendota.com/api/constants/items"
_UA = "CopilotoDota2/0.1 (local tool)"   # Cloudflare bloqueia sem User-Agent


def _norm(s):
    """Normaliza p/ casar nomes: minusculo, so letras/numeros."""
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


# Apelidos comuns (abreviacoes e PT-BR) -> slug interno. Cobrem o jeito que o
# relatorio costuma citar itens (BKB, MKB, Pipe, Sentinela...). Chaves sao
# normalizadas por _norm() no lookup, entao pode escrever natural aqui.
_ALIASES = {
    "bkb": "black_king_bar",
    "mkb": "monkey_king_bar",
    "halberd": "heavens_halberd",
    "linken": "linkens_sphere", "linkens": "linkens_sphere",
    "lotus": "lotus_orb",
    "sentinela": "ward_sentry", "sentry": "ward_sentry", "sentinelas": "ward_sentry",
    "gema": "gem",
    "euls": "cyclone", "eul": "cyclone", "cajado de eul": "cyclone",
    "cota de laminas": "blade_mail",
    "forca": "force_staff", "force staff": "force_staff",
    "guarda carmesim": "crimson_guard",
    "manto negro": "black_king_bar",
    "botas de viagem": "travel_boots", "bot": "boots",
    "cajado atordoante": "cyclone",
}

_catalog = None    # {slug: dname}
_by_dname = None   # {normalized dname: slug}


def load():
    """Carrega o cache em memoria (idempotente). Sem cache -> catalogo vazio
    (os icones ainda funcionam pelo slug, so nao valida nomes)."""
    global _catalog, _by_dname
    if _catalog is not None:
        return _catalog
    try:
        with open(CACHE, encoding="utf-8") as f:
            _catalog = json.load(f)
    except Exception:
        _catalog = {}
    _by_dname = {}
    for slug, dname in _catalog.items():
        _by_dname[_norm(dname)] = slug
    return _catalog


def icon_url(slug):
    return f"{CDN}/items/{slug}.png"


def resolve(token):
    """Recebe um token (slug interno, nome oficial ou apelido) e devolve o slug
    valido correspondente, ou None se nao reconhecer."""
    load()
    t = (token or "").strip().lower()
    if not t:
        return None
    # 1) ja e um slug (com ou sem 'item_')
    slug = re.sub(r"[^a-z0-9]+", "_", t.replace("item_", "")).strip("_")
    if slug in _catalog:
        return slug
    n = _norm(t)
    # 2) apelido conhecido
    if n in _ALIAS_NORM:
        cand = _ALIAS_NORM[n]
        if not _catalog or cand in _catalog:
            return cand
    # 3) nome bonito (dname)
    if n in _by_dname:
        return _by_dname[n]
    # 4) sem catalogo carregado: confia no slug derivado (melhor que nada)
    if not _catalog and slug:
        return slug
    return None


def enrich(tokens):
    """Lista de tokens -> lista de {slug, name, img}, sem repetir e so validos."""
    load()
    out, seen = [], set()
    for tk in tokens or []:
        slug = resolve(tk)
        if slug and slug not in seen:
            seen.add(slug)
            out.append({"slug": slug, "name": _catalog.get(slug) or slug.replace("_", " ").title(),
                        "img": icon_url(slug)})
    return out


def refresh():
    """Baixa o catalogo da OpenDota e salva no cache. Rodar 1x (ou por patch)."""
    req = urllib.request.Request(_API, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.loads(r.read().decode("utf-8"))
    cat = {k: v.get("dname") for k, v in data.items()
           if isinstance(v, dict) and v.get("dname")}
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    with open(CACHE, "w", encoding="utf-8") as f:
        json.dump(cat, f, ensure_ascii=False)
    global _catalog
    _catalog = None   # forca recarregar no proximo load()
    print(f"items.json salvo ({len(cat)} itens)")
    return cat


# apelidos com chave normalizada (pra casar 'BKB', 'b k b', etc.)
_ALIAS_NORM = {_norm(k): v for k, v in _ALIASES.items()}


if __name__ == "__main__":
    refresh()
    load()
    import pprint
    pprint.pprint(enrich(["black_king_bar", "BKB", "Manta Style", "pipe", "xyz123"]))
