"""
Notificacoes por Voz: avisos no minuto certo, filtrados pela sua funcao.
=======================================================================

Motor por relogio do jogo (map.clock_time do GSI): cada aviso tem um horario
(segundos de jogo, pode ser negativo no pre-jogo), opcionalmente repete a cada
N segundos por X vezes, e vale para algumas funcoes (vazio = todas). Quando o
horario passa, o aviso e FALADO pela voz local (voice.speak) e aparece no painel.

Regras: so roda em PRE_GAME / GAME_IN_PROGRESS (nao em pausa); partida nova zera
os contadores; se o relogio PULAR mais de 5 s (abriu o app no meio do jogo,
reconectou), o que ja passou fica marcado como dado e nao toca atrasado.

Eventos: Roshan morto (bloco `events` do GSI) agenda "Roshan pode nascer" em
+8 min e "Roshan nasce ate agora" em +11 min; Observer voltando a loja
(map.ward_purchase_cooldown voltou a 0) avisa os suportes.

Configuracao (funcao, liga/desliga geral e de cada aviso, avisos proprios) fica
em DATA_DIR/avisos.json.
"""

import json
import threading
import time
import uuid

from copiloto import config

PATH = config.DATA_DIR / "avisos.json"
ROLES = {"hc": "Carry (pos 1)", "mid": "Meio (pos 2)", "off": "Offlane (pos 3)",
         "sup4": "Suporte (pos 4)", "sup5": "Suporte (pos 5)"}
ROLE_POS = {"hc": 1, "mid": 2, "off": 3, "sup4": 4, "sup5": 5}
SUP = ["sup4", "sup5"]
RUN_STATES = {"DOTA_GAMERULES_STATE_PRE_GAME", "DOTA_GAMERULES_STATE_GAME_IN_PROGRESS"}
JUMP_S = 5

# Avisos padrao (horarios do jogo atual; ajustaveis no painel).
# t = 1o disparo (s de jogo) | every = repete a cada (s) | times = quantas vezes (-1 = sempre)
DEFAULTS = [
    {"id": "agua",      "texto": "Runa d'agua em 20 segundos",        "t": 100,  "every": 120, "times": 2,  "roles": ["mid"]},
    {"id": "poder",     "texto": "Runa de poder em 20 segundos",      "t": 340,  "every": 120, "times": -1, "roles": ["mid"]},
    # Filtros por funcao no estilo da referencia: so as runas do rio ficam so pro mid.
    {"id": "bounty",    "texto": "Runa de ouro em 20 segundos",       "t": 160,  "every": 180, "times": 6,  "roles": SUP + ["off", "hc"]},
    {"id": "lotus",     "texto": "Lotus na lagoa em 20 segundos",     "t": 160,  "every": 180, "times": 4,  "roles": SUP + ["off", "hc"]},
    {"id": "sabedoria", "texto": "Runa de sabedoria em 30 segundos",  "t": 390,  "every": 420, "times": 6,  "roles": SUP + ["off", "hc"]},
    {"id": "ward",      "texto": "Ward contra gank",                  "t": 241,  "every": 0,   "times": 1,  "roles": SUP + ["off", "hc"]},
    {"id": "stack",     "texto": "Hora do stack",                     "t": 170,  "every": 60,  "times": 20, "roles": []},
    {"id": "pull",      "texto": "Puxe a wave agora",                 "t": 75,   "every": 60,  "times": 8,  "roles": SUP + ["off", "hc"], "ativo": False},
    {"id": "tormentor", "texto": "Tormentor em 1 minuto",             "t": 1140, "every": 600, "times": 3,  "roles": []},
    {"id": "dia",       "texto": "Vai virar noite, cuidado com gank", "t": 285,  "every": 600, "times": 6,  "roles": [], "ativo": False},
]

_lock = threading.Lock()
CFG = {"role": "hc", "enabled": True, "items": [], "overrides": {}, "voice": False, "beep": True}
STATE = {"match": None, "last": None, "fired": {}, "events_seen": set(),
         "dynamic": [], "ward_cd": None, "feed": []}
SPEAK = [None]      # callable(texto) - definido pelo servidor (voz local)
BANNER = [None]     # callable(texto) - aviso na tela (overlay), definido pelo servidor
BEEP = [None]       # callable() - bip discreto, definido pelo servidor


# ----------------------------------------------------------------------------
# Configuracao
# ----------------------------------------------------------------------------
def load():
    try:
        d = json.loads(PATH.read_text(encoding="utf-8"))
        if d.get("role") in ROLES:
            CFG["role"] = d["role"]
        CFG["enabled"] = bool(d.get("enabled", True))
        CFG["items"] = [i for i in d.get("items") or [] if isinstance(i, dict) and i.get("texto")]
        CFG["overrides"] = d.get("overrides") or {}
        # chave nova: a voz da Skill Certa passou a ser DESLIGADA por padrao
        # (a antiga 'say_skill' vinha salva como True e e ignorada)
        CFG["skill_voice"] = bool(d.get("skill_voice", False))
        CFG.pop("say_skill", None)
        CFG["voice"] = bool(d.get("voice", False))   # falar os avisos (padrao: nao)
        CFG["beep"] = bool(d.get("beep", True))      # bip discreto a cada aviso
        m = d.get("medal")
        CFG["medal"] = int(m) if isinstance(m, int) and 1 <= m <= 8 else None
    except Exception:
        pass
    return CFG


def save():
    try:
        PATH.write_text(json.dumps(CFG, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception as e:
        print(f"[AVISOS] nao consegui salvar: {e}")


def role():
    return CFG.get("role") or "hc"


def all_items():
    """Padrao (com os ajustes do usuario) + os avisos proprios."""
    out = []
    for d in DEFAULTS:
        it = dict(d, ativo=d.get("ativo", True), proprio=False)
        it.update(CFG["overrides"].get(d["id"]) or {})
        out.append(it)
    for it in CFG["items"]:
        out.append(dict({"every": 0, "times": 1, "roles": [], "ativo": True}, **it, proprio=True))
    return out


def set_item(item_id, **kw):
    with _lock:
        for it in CFG["items"]:
            if it.get("id") == item_id:
                it.update(kw)
                save()
                return True
        if any(d["id"] == item_id for d in DEFAULTS):
            CFG["overrides"].setdefault(item_id, {}).update(kw)
            save()
            return True
    return False


def add_item(texto, t, every=0, times=1, roles=None):
    it = {"id": "u" + uuid.uuid4().hex[:8], "texto": str(texto)[:60], "t": int(t),
          "every": int(every or 0), "times": int(times or 1), "roles": list(roles or []), "ativo": True}
    with _lock:
        CFG["items"].append(it)
        save()
    return it


def remove_item(item_id):
    with _lock:
        CFG["items"] = [i for i in CFG["items"] if i.get("id") != item_id]
        save()


def public():
    return {"role": role(), "roles": ROLES, "enabled": CFG["enabled"], "items": all_items(),
            "voice": CFG.get("voice", False), "beep": CFG.get("beep", True),
            "feed": STATE["feed"][-12:]}


# ----------------------------------------------------------------------------
# Motor
# ----------------------------------------------------------------------------
def _fire(item, clock):
    texto = item["texto"]
    STATE["feed"].append({"texto": texto, "clock": clock, "at": time.time()})
    del STATE["feed"][:-30]
    print(f"[AVISO] {_fmt(clock)} | {texto}")
    if BANNER[0]:
        try:
            BANNER[0](texto)          # aviso na tela, por cima do jogo
        except Exception as e:
            print(f"[AVISO] banner falhou: {e}")
    if CFG.get("voice", False) and SPEAK[0]:
        try:
            SPEAK[0](texto)
        except Exception as e:
            print(f"[AVISO] voz falhou: {e}")
    elif CFG.get("beep", True) and BEEP[0]:
        try:
            BEEP[0]()
        except Exception as e:
            print(f"[AVISO] bip falhou: {e}")


def _fmt(s):
    s = int(s)
    sign = "-" if s < 0 else ""
    s = abs(s)
    return f"{sign}{s // 60}:{s % 60:02d}"


def _due(item, prev, clock):
    """Quantas ocorrencias do item cairam em (prev, clock]. -> (n, ultimo_idx)."""
    t0, every, times = int(item["t"]), int(item.get("every") or 0), int(item.get("times") or 1)
    if every <= 0:
        return (1, 0) if prev < t0 <= clock else (0, None)
    if clock < t0:
        return 0, None
    last_idx = int((clock - t0) // every)
    if times > 0:
        last_idx = min(last_idx, times - 1)
    first_idx = 0 if prev < t0 else int((prev - t0) // every) + 1
    if last_idx < first_idx:
        return 0, None
    return last_idx - first_idx + 1, last_idx


def _applies(item):
    rs = item.get("roles") or []
    return item.get("ativo", True) and (not rs or role() in rs)


def tick(raw):
    """Chamado a cada pacote do GSI."""
    if not CFG.get("enabled", True):
        return
    mapi = (raw or {}).get("map") or {}
    match = mapi.get("matchid")
    with _lock:
        if match != STATE["match"]:
            STATE.update(match=match, last=None, fired={}, events_seen=set(), dynamic=[], ward_cd=None)
        if mapi.get("game_state") not in RUN_STATES or mapi.get("paused"):
            return
        try:
            clock = int(float(mapi.get("clock_time")))
        except (TypeError, ValueError):
            return
        prev = STATE["last"]
        STATE["last"] = clock
        if prev is None or clock < prev or clock - prev > JUMP_S:
            return                       # 1o pacote ou salto: nao toca o que ja passou
        if clock == prev:
            pending = []
        else:
            pending = []
            for it in all_items() + STATE["dynamic"]:
                if not _applies(it):
                    continue
                n, idx = _due(it, prev, clock)
                if n:
                    key = (it["id"], idx)
                    if key not in STATE["fired"]:
                        STATE["fired"][key] = True
                        pending.append(it)
        _events(raw, clock, pending)
        _ward(mapi, clock, pending)
    for i, it in enumerate(pending[:3]):        # no maximo 3 por vez, 1 s entre eles
        threading.Timer(i * 1.2, _fire, args=(it, clock)).start()


def _events(raw, clock, pending):
    for ev in (raw or {}).get("events") or []:
        if not isinstance(ev, dict):
            continue
        kind, gt = ev.get("event_type"), ev.get("game_time")
        key = (kind, gt)
        if key in STATE["events_seen"]:
            continue
        STATE["events_seen"].add(key)
        if kind == "roshan_killed" and isinstance(gt, (int, float)):
            base = int(gt)
            STATE["dynamic"] += [
                {"id": f"rosh8-{base}", "texto": "Roshan pode ter nascido", "t": base + 480, "roles": []},
                {"id": f"rosh11-{base}", "texto": "Roshan ja nasceu, se ninguem pegou", "t": base + 660,
                 "roles": []},
            ]
            print(f"[AVISO] Roshan morreu em {_fmt(base)} - avisos em {_fmt(base + 480)} e {_fmt(base + 660)}")
        elif kind == "aegis_denied" or kind == "aegis_picked_up":
            pass


def _ward(mapi, clock, pending):
    cd = mapi.get("ward_purchase_cooldown")
    if not isinstance(cd, (int, float)):
        return
    prev = STATE["ward_cd"]
    STATE["ward_cd"] = cd
    if prev is not None and prev > 0 and cd == 0 and role() in SUP:
        pending.append({"id": f"obs-{clock}", "texto": "Observer disponivel na loja"})


def test(item_id=None):
    """Fala um aviso de teste (ou o aviso pedido)."""
    it = next((i for i in all_items() if i["id"] == item_id), None) if item_id else None
    _fire(it or {"texto": "Teste de aviso. Runa de poder em 20 segundos"}, 0)


if __name__ == "__main__":
    said = []
    SPEAK[0] = said.append
    CFG.update(role="mid", enabled=True)

    def pk(clock, match="1", **extra):
        return {"map": dict({"matchid": match, "clock_time": clock,
                             "game_state": "DOTA_GAMERULES_STATE_GAME_IN_PROGRESS"}, **extra)}
    for c in range(90, 350):
        tick(pk(c))
    time.sleep(4)
    print("mid de 1:30 a 5:50 ->", said)
    said.clear()
    CFG["role"] = "sup5"
    tick(pk(400, match="2"))
    tick(pk(401, match="2", **{}))
    raw = pk(402, match="2")
    raw["events"] = [{"event_type": "roshan_killed", "game_time": 402}]
    tick(raw)
    for c in range(403, 402 + 482):
        tick(pk(c, match="2"))
    time.sleep(4)
    print("sup5 de 6:40 a 14:44 ->", said)
    said.clear()
    tick(pk(2000, match="2"))
    print("salto de relogio (abriu no meio) ->", said)
