"""
Junta Flash Farm, Itens do Meta e Skill Certa com o estado AO VIVO do GSI.
=========================================================================

Os modulos de dados (benchmarks, metabuild, skillbuild) buscam na OpenDota e
guardam em cache; a 1a busca de um heroi leva ~1 s. Aqui a busca roda numa
thread quando o heroi muda, e status() so le o que ja esta pronto - o servidor
nunca espera rede no meio de um pedido do painel.

Skill Certa: quando surge um ponto de skill/talento pendente, avisa UMA vez
por nivel pela voz ("Suba a Q, Mana Break") se a opcao estiver ligada.
"""

import threading
import time

from copiloto.game import benchmarks, metabuild, skillbuild

_lock = threading.Lock()
CACHE = {}           # (kind, hero_id, role) -> resultado | None (falhou) ; ausente = buscando
_busy = set()
SPEAK = [None]       # callable(texto)
BANNER = [None]      # callable(texto) - aviso na tela
SAY_SKILL = [False]  # falar a skill (padrao: nao - so aviso na tela + bip)
BEEP = [None]        # callable() - bip discreto quando nao fala
_last_said = {"match": None, "key": None}


def _hero(raw):
    h = (raw or {}).get("hero") or {}
    return h.get("id"), h.get("name")


def _fetch(key, fn):
    try:
        val = fn()
    except Exception as e:
        print(f"[META] {key[0]} falhou: {e}")
        val = None
    with _lock:
        CACHE[key] = val
        _busy.discard(key)


def _get(key, fn):
    with _lock:
        if key in CACHE:
            return CACHE[key], False
        if key not in _busy:
            _busy.add(key)
            threading.Thread(target=_fetch, args=(key, fn), daemon=True).start()
    return None, True


def prefetch(raw, role):
    hid, npc = _hero(raw)
    if not hid:
        return
    _get(("bench", hid, role), lambda: benchmarks.get(hid, "support" if role in ("sup4", "sup5") else "core"))
    _get(("build", hid, ""), lambda: metabuild.build(hid))
    if npc:
        _get(("skill", hid, ""), lambda: skillbuild.build(npc, hid))


def _net_worth(raw):
    p = (raw or {}).get("player") or {}
    nw = p.get("net_worth")
    if isinstance(nw, (int, float)) and nw > 0:
        return int(nw)
    # Sem net_worth no GSI: ouro + preco dos itens (inventario, mochila, stash)
    gold = p.get("gold") or 0
    total = 0
    try:
        cat = metabuild._catalog()[1]          # cache local (ja baixado pelo Itens do Meta)
    except Exception:
        cat = {}
    for k, it in ((raw or {}).get("items") or {}).items():
        n = (it or {}).get("name") or ""
        if n.startswith("item_") and k.startswith(("slot", "stash")):
            total += int((cat.get(n[5:]) or {}).get("cost") or 0)
    return int(gold) + total


def status(raw, role):
    """Tudo pro painel (nunca bloqueia)."""
    hid, npc = _hero(raw)
    if not hid:
        return {"hero": None}
    prefetch(raw, role)
    mapi = (raw or {}).get("map") or {}
    clock = mapi.get("clock_time") or 0
    p = (raw or {}).get("player") or {}
    out = {"hero": npc, "hero_id": hid, "role": role, "clock": clock}

    bench, wait_b = _get(("bench", hid, role), lambda: None)
    flash = None
    if bench is not None:
        try:
            flash = benchmarks.compare(hid, clock, p.get("last_hits") or 0, _net_worth(raw), role)
        except Exception as e:
            print(f"[META] flash farm: {e}")
    out["flash"] = flash
    out["flash_wait"] = wait_b

    build, wait_i = _get(("build", hid, ""), lambda: None)
    out["build"] = build
    out["build_phase"] = metabuild.current_phase(clock) if build else None
    out["build_wait"] = wait_i

    sk, wait_s = _get(("skill", hid, ""), lambda: None)
    nxt = None
    if sk:
        try:
            nxt = skillbuild.next_skill(sk, (raw or {}).get("hero") or {}, (raw or {}).get("abilities") or {})
        except Exception as e:
            print(f"[META] skill certa: {e}")
    out["skill"] = nxt
    out["skill_build"] = sk
    out["skill_wait"] = wait_s
    _announce(raw, nxt)
    return out


def _announce(raw, nxt):
    """Fala (e mostra na tela) a proxima skill uma vez por nivel."""
    if not nxt or not (SAY_SKILL[0] and SPEAK[0]) and not BANNER[0]:
        return
    h = (raw or {}).get("hero") or {}
    match = ((raw or {}).get("map") or {}).get("matchid")
    if _last_said["match"] != match:
        _last_said.update(match=match, key=None)
    key = (h.get("level"), nxt.get("ability"), bool(nxt.get("talento")))
    if key == _last_said["key"]:
        return
    _last_said["key"] = key
    tal = nxt.get("talento")
    if tal:
        txt = f"Talento do nivel {tal.get('nivel')}: {tal.get('lado')}, {tal.get('nome')}"
    else:
        txt = f"Suba a {nxt.get('tecla') or ''}, {nxt.get('nome')}".replace("  ", " ")
    if BANNER[0]:
        try:
            BANNER[0](f"Talento {tal.get('nivel')} · {tal.get('lado')} · {tal.get('nome')}" if tal
                      else f"Suba {nxt.get('tecla') or ''} · {nxt.get('nome')}")
        except Exception as e:
            print(f"[META] banner da skill falhou: {e}")
    if not (SAY_SKILL[0] and SPEAK[0]):
        if BEEP[0]:
            try:
                BEEP[0]()
            except Exception:
                pass
        return
    try:
        SPEAK[0](txt)
    except Exception as e:
        print(f"[META] voz da skill falhou: {e}")


def tick(raw, role):
    """Chamado a cada pacote do GSI: so dispara as buscas e o aviso da skill."""
    hid, _ = _hero(raw)
    if not hid:
        return
    sk = CACHE.get(("skill", hid, ""))
    if sk is None:
        prefetch(raw, role)
        return
    try:
        _announce(raw, skillbuild.next_skill(sk, (raw or {}).get("hero") or {}, (raw or {}).get("abilities") or {}))
    except Exception:
        pass


__all__ = ["status", "tick", "prefetch", "SPEAK", "SAY_SKILL"]
_t0 = time.time()
