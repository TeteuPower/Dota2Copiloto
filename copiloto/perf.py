"""
Orcamento de CPU INTELIGENTE para os motores locais (voz e transcricao).
=======================================================================

Objetivo: a voz/transcricao nunca atrapalhar o FPS - em qualquer PC.
A decisao e feita a cada uso, olhando 3 coisas:

  1) O PROCESSADOR (topologia, lida do Windows):
     - Intel hibrido (P-cores + E-cores): prende o motor nos E-CORES, que os
       jogos quase nao usam - o Dota fica com os P-cores so pra ele;
     - processador comum: usa os ULTIMOS nucleos fisicos (1 thread por nucleo,
       nunca o nucleo 0, onde ficam o sistema e a thread principal do jogo).
  2) O MOMENTO (GSI + processo do Dota):
     - em PARTIDA (draft/pre-jogo/jogo): orcamento minimo + teto duro;
     - Dota aberto no menu: orcamento medio;
     - Dota fechado: livre (rapido), so com prioridade baixa.
  3) A FOLGA REAL da CPU agora (amostra de ~0,25 s): PC ja no limite -> aperta
     mais (nunca abaixo de meio nucleo, senao a voz ficaria inutilizavel).

O orcamento e expresso em NUCLEOS e convertido para % do processador inteiro:
assim "1 nucleo" vale o mesmo num PC de 4 ou de 32 threads.
"""

import ctypes
import os
import subprocess
import threading
import time

NCPU = os.cpu_count() or 4

# Contexto do jogo, preenchido pelo servidor (GSI). -> "partida" | "menu" | None
GAME_STATE_FN = None

# Estados do GSI que contam como "partida" (o jogo precisa de todo o FPS)
MATCH_STATES = {
    "DOTA_GAMERULES_STATE_HERO_SELECTION", "DOTA_GAMERULES_STATE_STRATEGY_TIME",
    "DOTA_GAMERULES_STATE_TEAM_SHOWCASE", "DOTA_GAMERULES_STATE_WAIT_FOR_MAP_TO_LOAD",
    "DOTA_GAMERULES_STATE_PRE_GAME", "DOTA_GAMERULES_STATE_GAME_IN_PROGRESS",
}

LAST_PLAN = {}
_topo_cache = None
_dota_cache = {"t": 0.0, "v": False}
_lock = threading.Lock()


# ----------------------------------------------------------------------------
# Topologia do processador
# ----------------------------------------------------------------------------
def topology():
    """-> lista de dicts {lp, core, eff} (grupo 0 do Windows; ate 64 threads).
    eff = classe de eficiencia (0 = E-core num Intel hibrido; tudo 0 se comum)."""
    global _topo_cache
    if _topo_cache is not None:
        return _topo_cache
    out = []
    if os.name == "nt":
        try:
            k32 = ctypes.windll.kernel32
            need = ctypes.c_ulong(0)
            k32.GetSystemCpuSetInformation(None, 0, ctypes.byref(need), None, 0)
            buf = ctypes.create_string_buffer(need.value)
            if k32.GetSystemCpuSetInformation(buf, need, ctypes.byref(need), None, 0):
                raw, off = buf.raw, 0
                while off + 20 <= need.value:
                    size = int.from_bytes(raw[off:off + 4], "little")
                    if size <= 0:
                        break
                    group = int.from_bytes(raw[off + 12:off + 14], "little")
                    if group == 0:
                        out.append({"lp": raw[off + 14], "core": raw[off + 15], "eff": raw[off + 18]})
                    off += size
        except Exception as e:
            print(f"[perf] topologia indisponivel ({e})")
    if not out:   # fallback: supoe SMT 2-way se par, senao 1 thread por nucleo
        smt = 2 if NCPU >= 4 and NCPU % 2 == 0 else 1
        out = [{"lp": i, "core": i // smt, "eff": 0} for i in range(min(NCPU, 64))]
    _topo_cache = out
    return out


def hybrid():
    return len({t["eff"] for t in topology()}) > 1


def pick_cores(n):
    """Escolhe n processadores logicos que menos atrapalham o jogo. -> (mask, [lps])."""
    topo = [t for t in topology() if t["lp"] < 64]
    effs = sorted({t["eff"] for t in topo})
    pool = [t for t in topo if t["eff"] == effs[0]] if len(effs) > 1 else topo   # E-cores
    by_core = {}
    for t in sorted(pool, key=lambda t: t["lp"]):
        by_core.setdefault(t["core"], []).append(t["lp"])
    cores = sorted(by_core, reverse=True)                    # ultimos nucleos primeiro
    if len(effs) == 1 and len(cores) > 1:
        cores = [c for c in cores if c != min(by_core)]      # nunca o nucleo 0
    chosen = []
    for rnd in range(4):                                     # 1 thread por nucleo antes de usar SMT
        for c in cores:
            if len(chosen) >= n:
                break
            if rnd < len(by_core[c]):
                chosen.append(by_core[c][rnd])
    chosen = chosen or [topo[-1]["lp"]]
    mask = 0
    for lp in chosen:
        mask |= 1 << lp
    return mask, chosen


# ----------------------------------------------------------------------------
# Momento do jogo e folga da CPU
# ----------------------------------------------------------------------------
def dota_running():
    now = time.time()
    if now - _dota_cache["t"] < 15:
        return _dota_cache["v"]
    v = False
    if os.name == "nt":
        try:
            r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq dota2.exe", "/NH"], capture_output=True,
                               text=True, timeout=5, creationflags=0x08000000)
            v = "dota2.exe" in (r.stdout or "").lower()
        except Exception:
            pass
    _dota_cache.update(t=now, v=v)
    return v


def moment():
    """-> 'partida' | 'menu' | 'livre'"""
    st = None
    if GAME_STATE_FN:
        try:
            st = GAME_STATE_FN()
        except Exception:
            st = None
    if st == "partida":
        return "partida"
    if st == "menu" or dota_running():
        return "menu"
    return "livre"


class _FT(ctypes.Structure):
    _fields_ = [("lo", ctypes.c_uint32), ("hi", ctypes.c_uint32)]


def _sys_times():
    i, k, u = _FT(), _FT(), _FT()
    ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(i), ctypes.byref(k), ctypes.byref(u))
    f = lambda x: (x.hi << 32) | x.lo
    return f(i), f(k) + f(u)        # kernel ja inclui o idle


def cpu_busy(sample=0.25):
    """Uso do processador inteiro agora (0..1). None se nao der pra medir."""
    if os.name != "nt":
        return None
    try:
        i1, t1 = _sys_times()
        time.sleep(sample)
        i2, t2 = _sys_times()
        dt = t2 - t1
        return max(0.0, min(1.0, 1 - (i2 - i1) / dt)) if dt > 0 else None
    except Exception:
        return None


# ----------------------------------------------------------------------------
# O plano
# ----------------------------------------------------------------------------
def plan(mode="auto"):
    """Decide como rodar o proximo trabalho local. -> dict
    {cores, mask, threads, percent, moment, busy, reason}.
    mode: 'auto' (inteligente) | 'livre' (sem limite) | 'leve' (sempre o minimo)."""
    with _lock:
        m = "livre" if mode == "livre" else ("partida" if mode == "leve" else moment())
        busy = cpu_busy() if m != "livre" else None
        e_cores = hybrid()

        # Medido num i7 hibrido de 28 threads EM PARTIDA: 2 E-cores/1 nucleo de
        # orcamento gerava a voz a 0,4x o tempo real (picotava); os E-cores ficam
        # ociosos no Dota, entao em hibrido da pra usar 4 deles sem tocar nos P-cores.
        if m == "partida":
            want = 4 if e_cores else 2
            budget = 3.0 if e_cores else 1.0           # nucleos de CPU, em media
        elif m == "menu":
            want = 6 if e_cores else 2
            budget = 4.0 if e_cores else 1.5
        else:
            want = max(2, min(8, NCPU // 2))
            budget = None                              # sem teto (so prioridade baixa + nucleos)

        if busy is not None and budget is not None:
            if busy > 0.85:                            # PC no limite: aperta
                budget *= 0.5
            elif busy > 0.65:
                budget *= 0.75
        if budget is not None:
            budget = max(0.5, budget)                  # piso: a voz nao pode travar

        mask, lps = pick_cores(min(want, NCPU))
        percent = None if budget is None else max(1.0, round(100.0 * budget / NCPU, 2))
        p = {
            "moment": m, "busy": None if busy is None else round(busy, 2),
            "cores": lps, "mask": mask,
            "threads": len(lps), "budget_cores": budget, "percent": percent,
            "hybrid": e_cores, "ncpu": NCPU,
        }
        p["reason"] = (f"{m}: {len(lps)} {'E-core' if e_cores else 'nucleo'}(s) "
                       + (f"ate {budget:g} nucleo(s) = {percent}% da CPU" if budget else "sem teto")
                       + (f", CPU em {int(busy * 100)}%" if busy is not None else ""))
        LAST_PLAN.clear()
        LAST_PLAN.update(p)
        return p


def describe():
    topo = topology()
    return {"ncpu": NCPU, "hybrid": hybrid(),
            "e_cores": sum(1 for t in topo if t["eff"] == min(x["eff"] for x in topo)) if hybrid() else 0,
            "last_plan": dict(LAST_PLAN)}
