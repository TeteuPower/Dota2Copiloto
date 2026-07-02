"""
Copiloto Dota 2 - Servidor GSI (prova de conceito)
==================================================

Objetivo desta fase: fazer o programa "ver" a partida.

O Dota 2 (via Game State Integration) envia, sozinho, um JSON com o estado
da partida para o endpoint POST /gsi deste servidor. Guardamos o ultimo
estado em memoria e servimos:

  - GET  /        -> painel web (abra no celular / 2a tela)
  - GET  /state   -> ultimo estado (JSON) + um resumo ja interpretado
  - POST /gsi     -> endpoint que o Dota chama automaticamente

Tudo com biblioteca padrao (sem pip install). Python 3.10+.

Rodar:   python server.py
Depois:  abra http://localhost:49317 no navegador.
"""

import os
import json
import time
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import brain
import drafting
import scoreboard
import draftscan
import minimap
import voice
import history

# ----------------------------------------------------------------------------
# Configuracao
# ----------------------------------------------------------------------------
HOST = "0.0.0.0"          # 0.0.0.0 = aceita conexoes da rede (celular/2a tela)
PORT = 49317          # porta alta e incomum (evita conflito com apps na 3000)
AUTH_TOKEN = "copiloto-dota-secret"   # precisa bater com o .cfg do GSI

# Ultimo estado recebido do Dota (compartilhado entre as threads)
LATEST = {
    "raw": None,        # JSON cru que o Dota mandou
    "received_at": 0.0, # timestamp do ultimo recebimento
}

# Historico do chat com o copiloto (limpo: sem o estado do jogo embutido)
CHAT_HISTORY = []
# Cerebro de IA selecionado (definido no main via brain.get_provider())
PROVIDER = None

# Estado do draft. source: "auto" (vazio), "gsi", "manual", "scoreboard".
DRAFT_STATE = {"enemy": [], "allies": [], "bans": [], "source": "auto"}

# Ultimo placar lido. allies/enemies: [{hero_id, hero, player, img, k, d, a}]
# status: idle | capturando | recebido | analisando | pronto | erro
SCOREBOARD_STATE = {"allies": [], "enemies": [], "report": "", "status": "idle",
                    "scanned_at": 0.0, "scanning": False, "error": None}

# Os relatorios de cada partida ficam no historico persistente (history.py / match_history/).

# Estado da leitura da tela de PICKS (aba Draft). status: idle|capturando|analisando|pronto|erro
DRAFT_SCAN_STATE = {"status": "idle", "scanning": False, "error": None,
                    "scanned_at": 0.0, "enemy": [], "allies": []}
HOTKEY_KEY = "f7"        # segunda tecla do atalho (Tab + HOTKEY_KEY), configuravel
_HOTKEY_HANDLE = None     # handle do keyboard para re-registrar
SCAN_SOUND = True        # alerta sonoro quando o comando de capturar a tela e reconhecido

# ----------------------------------------------------------------------------
# Interpretacao do estado (aqui no futuro mora o "cerebro" do copiloto)
# ----------------------------------------------------------------------------
GAME_STATE_PT = {
    "DOTA_GAMERULES_STATE_INIT": "Inicializando",
    "DOTA_GAMERULES_STATE_WAIT_FOR_PLAYERS_TO_LOAD": "Aguardando jogadores",
    "DOTA_GAMERULES_STATE_HERO_SELECTION": "Selecao de herois (DRAFT)",
    "DOTA_GAMERULES_STATE_STRATEGY_TIME": "Tempo de estrategia",
    "DOTA_GAMERULES_STATE_TEAM_SHOWCASE": "Apresentacao dos times",
    "DOTA_GAMERULES_STATE_PRE_GAME": "Pre-jogo (fonte/compras)",
    "DOTA_GAMERULES_STATE_GAME_IN_PROGRESS": "Partida em andamento",
    "DOTA_GAMERULES_STATE_POST_GAME": "Pos-jogo",
}


def pretty_hero(npc_name):
    """npc_dota_hero_queenofpain -> 'Queenofpain'."""
    if not npc_name:
        return None
    return npc_name.replace("npc_dota_hero_", "").replace("_", " ").title()


def pretty_item(item_name):
    """item_blink -> 'Blink'."""
    if not item_name or item_name == "empty":
        return None
    return item_name.replace("item_", "").replace("_", " ").title()


# CDN oficial dos assets do Dota 2 (mesma origem dos retratos em drafting.py)
_CDN = "https://cdn.cloudflare.steamstatic.com/apps/dota2/images/dota_react"


def item_icon_url(item_name):
    """item_black_king_bar -> URL do icone do item no CDN do Dota."""
    if not item_name or item_name in ("empty", "0"):
        return None
    short = item_name.replace("item_", "")
    return f"{_CDN}/items/{short}.png"


def ability_icon_url(ability_name):
    """nome cru da skill do GSI -> URL do icone no CDN do Dota."""
    if not ability_name:
        return None
    return f"{_CDN}/abilities/{ability_name}.png"


def fmt_clock(seconds):
    """Segundos -> 'mm:ss' (negativo no pre-jogo vira -mm:ss)."""
    if seconds is None:
        return None
    sign = "-" if seconds < 0 else ""
    seconds = abs(int(seconds))
    return f"{sign}{seconds // 60:02d}:{seconds % 60:02d}"


def summarize(raw):
    """Extrai os campos-chave do JSON cru do GSI num formato facil de exibir.

    E aqui que, nas proximas fases, plugamos a logica de sugestao de picks e
    de itens. Por enquanto so traduz/organiza o que o Dota manda.
    """
    if not raw:
        return {"connected": False}

    g = (raw.get("map") or {})
    player = raw.get("player") or {}
    hero = raw.get("hero") or {}
    items = raw.get("items") or {}

    # Itens do inventario (slot0..slot8): nome bonito + URL do icone (pro painel)
    inventory = []        # lista de nomes (compatibilidade: usado pela IA)
    inventory_items = []  # lista de {name, img, charges} (usado pelo painel)
    for i in range(9):
        slot = items.get(f"slot{i}") or {}
        raw_name = slot.get("name")
        name = pretty_item(raw_name)
        if name:
            inventory.append(name)
            inventory_items.append({
                "name": name,
                "img": item_icon_url(raw_name),
                "charges": slot.get("charges") or slot.get("item_charges"),
            })

    # Habilidades (bloco "abilities" do GSI; ausente em alguns estados)
    abilities = []
    for i in range(24):
        ab = (raw.get("abilities") or {}).get(f"ability{i}")
        if not ab:
            continue
        ab_name = ab.get("name")
        if not ab_name or "empty" in ab_name or "generic_hidden" in ab_name:
            continue
        abilities.append({
            "name": pretty_item(ab_name) or ab_name,
            "img": ability_icon_url(ab_name),
            "level": ab.get("level"),
            "ultimate": bool(ab.get("ultimate")),
            "passive": bool(ab.get("passive")),
        })

    # Retrato do heroi: mapeia npc do GSI -> cache de herois -> imagem
    npc_name = hero.get("name")
    hero_meta = drafting.BY_ID.get(drafting.BY_NPC.get(npc_name)) if npc_name else None
    hero_img = (hero_meta or {}).get("img_url")
    hero_localized = (hero_meta or {}).get("localized_name") or pretty_hero(npc_name)

    raw_state = g.get("game_state")
    summary = {
        "connected": True,
        "match_id": g.get("matchid"),
        "game_state_raw": raw_state,
        "game_state": GAME_STATE_PT.get(raw_state, raw_state),
        "clock": fmt_clock(g.get("clock_time")),
        "daytime": "Dia" if g.get("daytime") else "Noite",
        "hero": hero_localized,
        "hero_img": hero_img,
        "team": (player.get("team_name") or "").lower() or None,  # "radiant"/"dire"
        "level": hero.get("level"),
        "health_pct": hero.get("health_percent"),
        "mana_pct": hero.get("mana_percent"),
        "alive": hero.get("alive"),
        "gold": player.get("gold"),
        "net_worth": player.get("net_worth"),
        "gpm": player.get("gpm"),
        "xpm": player.get("xpm"),
        "kda": [player.get("kills"), player.get("deaths"), player.get("assists")],
        "last_hits": player.get("last_hits"),
        "denies": player.get("denies"),
        "hero_damage": player.get("hero_damage"),
        "inventory": inventory,
        "items": inventory_items,
        "abilities": abilities,
        "has_draft": "draft" in raw,
    }
    return summary


def game_context_text():
    """Resumo do estado atual + placar lido + TODOS os relatorios da partida, p/ o chat."""
    s = summarize(LATEST["raw"])
    if s.get("connected"):
        inv = ", ".join(s.get("inventory") or []) or "vazio"
        kda = s.get("kda") or []
        kda_txt = "/".join(str(x) for x in kda) if any(x is not None for x in kda) else "-"
        parts = [
            f"Fase: {s.get('game_state')}",
            f"Relogio: {s.get('clock')} ({s.get('daytime')})",
            f"Seu heroi: {s.get('hero')} (nivel {s.get('level')})",
            f"Gold atual: {s.get('gold')} | GPM {s.get('gpm')} | XPM {s.get('xpm')}",
            f"KDA: {kda_txt} | Last hits: {s.get('last_hits')}",
            f"Seus itens: {inv}",
        ]
    else:
        parts = ["Nenhuma partida detectada agora (Dota fechado ou fora de uma partida)."]

    # Placar lido (ultima leitura): os 2 times com KDA
    def fmt_team(rows):
        return "; ".join(f"{r.get('hero')} ({r.get('k')}/{r.get('d')}/{r.get('a')})"
                         for r in (rows or []) if r.get("hero"))
    if SCOREBOARD_STATE.get("enemies"):
        parts.append("Seu time (placar): " + (fmt_team(SCOREBOARD_STATE.get("allies")) or "-"))
        parts.append("Time INIMIGO (placar): " + (fmt_team(SCOREBOARD_STATE.get("enemies")) or "-"))

    # TODOS os relatorios JA gerados nesta partida (o chat conhece o historico completo)
    reps = history.load(s.get("match_id")) if s.get("connected") else []
    if reps:
        parts.append("\nRELATORIOS JA GERADOS NESTA PARTIDA (do mais antigo ao mais novo - "
                     "use como base e seja coerente com o que ja foi dito):")
        for r in reps[-8:]:
            parts.append(f"--- [{r.get('clock')}] ---\n{r.get('report')}")
    return "\n".join(parts)


def voice_handle(text):
    """Recebe a fala JA transcrita (atalho de voz), grava no historico do chat,
    consulta o cerebro com o contexto do jogo e devolve a resposta (que o
    voice.py vai falar em voz alta). Mesma logica do endpoint /chat."""
    CHAT_HISTORY.append({"role": "user", "content": text})
    if PROVIDER is None:
        reply = "Cerebro de IA indisponivel."
    else:
        reply = PROVIDER.reply(CHAT_HISTORY, game_context_text())
    CHAT_HISTORY.append({"role": "assistant", "content": reply})
    return reply


def generate_report(allies, enemies, my_hero, items, clock, gold, level, previous=None):
    """Relatorio tatico via Claude (texto) a partir do placar + meus itens.
    `previous`: relatorios JA dados nesta partida, p/ o copiloto NAO repetir."""
    if PROVIDER is None:
        return ""

    def fmt_team(rows):
        return "; ".join(f"{r['hero']} ({r['k']}/{r['d']}/{r['a']})" for r in rows if r.get("hero"))

    # Inimigos EM DESTAQUE (mais fortes agora) pelo KDA -> foco dos itens de counter.
    def _fed(e):
        return (e.get("k") or 0) * 2 - (e.get("d") or 0) + (e.get("a") or 0) * 0.5
    standouts = [e for e in sorted(enemies, key=_fed, reverse=True) if e.get("hero")][:2]
    standout_txt = "; ".join(f"{e['hero']} ({e['k']}/{e['d']}/{e['a']})" for e in standouts) or "nenhum claro ainda"

    blocks = [
        f"Meu heroi: {my_hero} (nivel {level})" if my_hero else "",
        f"Tempo: {clock} | Meu gold: {gold}",
        f"Meus itens atuais: {items}",
        f"Meu time: {fmt_team(allies)}",
        f"Time INIMIGO: {fmt_team(enemies)}",
        f"INIMIGOS EM DESTAQUE (os mais fortes agora, pelo KDA): {standout_txt}",
    ]
    if previous:
        blocks.append("RELATORIOS QUE VOCE JA DEU NESTA PARTIDA (do mais antigo ao mais novo):\n"
                      + "\n--- (anterior) ---\n".join(previous[-3:]))
    ctx = "\n".join(filter(None, blocks))

    nao_repita = (
        "NAO REPITA o que ja disse nos relatorios acima; foque no que MUDOU (mortes novas, quem cresceu/caiu, "
        "itens novos) e avance o cronograma (nao recomende item que eu ja comprei). "
        if previous else "")

    pedido = (
        "Faca um relatorio tatico OBJETIVO e CURTO em PT-BR, frases diretas, para um jogador INICIANTE. "
        "LINGUAGEM: nunca cite o nome de uma habilidade sozinho - diga em poucas palavras O QUE ELA FAZ "
        "(ex.: em vez de 'Chronosphere', 'o ultimate do Void te prende parado, ate com BKB'); diga quando for o "
        "'ultimate'; traduza giria ('bursta' = 'te mata rapido com muito dano'; 'stun' = 'te atordoa, sem poder agir'). "
        "Pode citar nome de heroi e de item normalmente. "
        + nao_repita +
        "RESPONDA NESTES 4 TOPICOS, curtos e diretos: "
        "(1) SITUACAO: 1 frase - quem esta ganhando (pelo KDA)" + (" e o que mudou. " if previous else ". ") +
        "(2) AMEACAS: foque nos INIMIGOS EM DESTAQUE (os mais fortes listados acima) - 1 frase cada de como te matam. "
        "(3) CRONOGRAMA DE ITENS: lista numerada (1) 2) 3)...) dos PROXIMOS itens, do que da pra comprar agora ate o "
        "fim de jogo. PRIORIZE itens que NEUTRALIZAM os inimigos em destaque, dizendo em cada um QUAL inimigo ele "
        "neutraliza (ex.: BKB/Pipe vs muito dano magico, MKB vs quem desvia ataque, armadura/Halberd vs fisico forte, "
        "Sentinela/Gem vs invisivel, Linken/Lotus vs habilidade de alvo unico). Marque rapidinho o que eu JA tenho. "
        "(4) AGORA: 1 frase do que fazer (atacar junto, recuar e farmar, pegar Roshan, empurrar...). "
        "Seja direto, sem enrolacao."
    )
    try:
        # OpenAI (rapido ~5s) se escolhido em Settings e com chave; senao Claude (assinatura)
        if voice.report_engine() == "openai" and voice.get_key():
            return voice.openai_chat(ctx, pedido)
        return PROVIDER.reply([{"role": "user", "content": pedido}], ctx)
    except Exception as e:
        return f"(nao consegui gerar o relatorio: {e})"


def beep_recognized():
    """Alerta sonoro curto ('ding-ding' agudo) confirmando que o comando de
    capturar a tela foi reconhecido. Toca num thread proprio para nao atrasar a
    captura. Usa winsound (stdlib do Windows); no-op silencioso fora do Windows."""
    if not SCAN_SOUND:
        return

    def _play():
        try:
            import winsound
            winsound.Beep(880, 90)    # A5
            winsound.Beep(1245, 130)  # ~D#6 (sobe -> sensacao de "ok, reconhecido")
        except Exception:
            pass

    import threading
    threading.Thread(target=_play, daemon=True).start()


def do_scoreboard_scan():
    """Captura o placar (Tab), o Claude le, mapeia herois e gera o relatorio.
    Decide quem e inimigo pelo SEU heroi (GSI). Atualiza SCOREBOARD_STATE."""
    if SCOREBOARD_STATE["scanning"]:
        return SCOREBOARD_STATE
    SCOREBOARD_STATE["scanning"] = True
    SCOREBOARD_STATE["error"] = None
    SCOREBOARD_STATE["status"] = "capturando"
    beep_recognized()   # confirma na hora que o comando foi reconhecido (antes de capturar)
    try:
        # Contexto do jogador (GSI) para o relatorio: heroi, tempo, gold, ITENS e nivel
        raw = LATEST["raw"] or {}
        my_npc = (raw.get("hero") or {}).get("name")
        my_id = drafting.BY_NPC.get(my_npc) if my_npc else None
        my_hero = drafting.BY_ID.get(my_id, {}).get("localized_name", "") if my_id else ""
        s = summarize(raw)
        clock = s.get("clock")
        gold = s.get("gold")
        items = ", ".join(s.get("inventory") or []) or "nenhum item relevante ainda"
        level = s.get("level")

        # 1) Captura (rapida) -> print recebido
        scoreboard.capture()
        SCOREBOARD_STATE["status"] = "recebido"

        # 2) Claude (visao) le os herois + KDA do placar
        SCOREBOARD_STATE["status"] = "analisando"
        parsed = scoreboard.analyze()
        if not parsed:
            SCOREBOARD_STATE["error"] = "nao consegui ler o placar (abra o Tab e tente de novo)"
            SCOREBOARD_STATE["status"] = "erro"
            return SCOREBOARD_STATE

        def rows(team):
            out = []
            for p in (parsed.get(team) or []):
                hid = drafting.id_from_name(p.get("heroi"))
                out.append({
                    "hero_id": hid,
                    "hero": p.get("heroi"),
                    "player": p.get("jogador"),
                    "img": drafting.BY_ID.get(hid, {}).get("img_url"),
                    "k": p.get("k"), "d": p.get("d"), "a": p.get("a"),
                })
            return out

        il, te = rows("iluminados"), rows("temidos")
        il_ids = [r["hero_id"] for r in il if r["hero_id"]]
        te_ids = [r["hero_id"] for r in te if r["hero_id"]]

        # Meu time = aquele que contem o MEU heroi (vindo do GSI)
        if my_id and my_id in te_ids:
            allies, enemies, ally_ids, enemy_ids = te, il, te_ids, il_ids
        else:
            allies, enemies, ally_ids, enemy_ids = il, te, il_ids, te_ids

        SCOREBOARD_STATE["allies"] = allies
        SCOREBOARD_STATE["enemies"] = enemies
        # 3) Relatorio tatico. Usa o historico DESTA partida (persistido) como "anteriores".
        mid = s.get("match_id")
        previous = history.reports_text(mid)
        report = generate_report(allies, enemies, my_hero, items, clock, gold, level, previous=previous)
        SCOREBOARD_STATE["report"] = report
        # 4) Salva o relatorio COMPLETO (placar, itens, KDA, texto) no historico da partida.
        if report and not report.startswith("(nao consegui"):
            def slim(rows):
                return [{"hero": r.get("hero"), "player": r.get("player"),
                         "k": r.get("k"), "d": r.get("d"), "a": r.get("a")} for r in rows]
            history.add_report(mid, {
                "at": time.time(), "clock": clock, "gold": gold, "level": level,
                "my_hero": my_hero, "my_items": items,
                "allies": slim(allies), "enemies": slim(enemies), "report": report,
            })
        SCOREBOARD_STATE["scanned_at"] = time.time()
        SCOREBOARD_STATE["status"] = "pronto"
        # Fala a analise tatica em voz alta (OpenAI), se ligado em Settings
        if SCOREBOARD_STATE["report"] and voice.load_config().get("speak_report"):
            voice.speak(SCOREBOARD_STATE["report"])

        DRAFT_STATE.update({"enemy": enemy_ids, "allies": ally_ids, "bans": [], "source": "scoreboard"})
        print(f"[PLACAR] {time.strftime('%H:%M:%S')} | inimigos={[r['hero'] for r in enemies]}")
        return SCOREBOARD_STATE
    except Exception as e:
        SCOREBOARD_STATE["error"] = str(e)
        SCOREBOARD_STATE["status"] = "erro"
        return SCOREBOARD_STATE
    finally:
        SCOREBOARD_STATE["scanning"] = False


def do_draft_scan():
    """Le a tela de PICKS por visao (Claude) e preenche o DRAFT_STATE (inimigos/aliados).
    Ancora pelo SEU heroi (GSI) pra decidir o lado inimigo. Atualiza DRAFT_SCAN_STATE."""
    if DRAFT_SCAN_STATE["scanning"]:
        return DRAFT_SCAN_STATE
    DRAFT_SCAN_STATE["scanning"] = True
    DRAFT_SCAN_STATE["error"] = None
    DRAFT_SCAN_STATE["status"] = "capturando"
    beep_recognized()   # confirma na hora que o comando foi reconhecido
    try:
        raw = LATEST["raw"] or {}
        my_npc = (raw.get("hero") or {}).get("name")
        my_id = drafting.BY_NPC.get(my_npc) if my_npc else None
        my_hero = drafting.BY_ID.get(my_id, {}).get("localized_name", "") if my_id else ""

        # 1) Captura a faixa de cima da tela (picks)
        draftscan.capture()
        # 2) Claude (visao) le os retratos -> {meu_time, inimigo}
        DRAFT_SCAN_STATE["status"] = "analisando"
        parsed = draftscan.analyze(my_hero)
        if not parsed:
            DRAFT_SCAN_STATE["error"] = "nao consegui ler os picks (tente de novo ou marque no grid)"
            DRAFT_SCAN_STATE["status"] = "erro"
            return DRAFT_SCAN_STATE

        def ids(names):
            out = []
            for n in names or []:
                hid = drafting.id_from_name(n)
                if hid and hid not in out:
                    out.append(hid)
            return out

        mine = ids(parsed.get("meu_time"))
        foe = ids(parsed.get("inimigo"))
        # Corrige o lado pelo MEU heroi (GSI), se o Claude tiver invertido
        if my_id and my_id in foe:
            mine, foe = foe, mine
        if my_id and my_id not in mine:
            mine.append(my_id)
        foe = [h for h in foe if h not in mine]   # nunca o mesmo heroi nos dois lados

        # Preenche o draft (source=manual pra o poller do GSI nao sobrescrever)
        DRAFT_STATE.update({"enemy": foe, "allies": mine, "source": "manual"})
        DRAFT_SCAN_STATE.update({"enemy": foe, "allies": mine,
                                 "scanned_at": time.time(), "status": "pronto"})
        print(f"[DRAFT] {time.strftime('%H:%M:%S')} | "
              f"inimigos={[drafting.BY_ID.get(h, {}).get('localized_name') for h in foe]}")
        return DRAFT_SCAN_STATE
    except Exception as e:
        DRAFT_SCAN_STATE["error"] = str(e)
        DRAFT_SCAN_STATE["status"] = "erro"
        return DRAFT_SCAN_STATE
    finally:
        DRAFT_SCAN_STATE["scanning"] = False


def reset_context():
    """Zera TODO o contexto acumulado da partida atual sem reiniciar o servidor:
    chat com o copiloto, draft, placar lido, relatorios e leitura de picks.
    Serve para comecar uma nova partida do zero (o GSI volta a popular sozinho).
    Obs.: NAO apaga o historico em disco (match_history/) - esse fica guardado."""
    CHAT_HISTORY.clear()
    DRAFT_STATE.update({"enemy": [], "allies": [], "bans": [], "source": "auto"})
    SCOREBOARD_STATE.update({"allies": [], "enemies": [], "report": "", "status": "idle",
                             "scanned_at": 0.0, "scanning": False, "error": None})
    DRAFT_SCAN_STATE.update({"status": "idle", "scanning": False, "error": None,
                             "scanned_at": 0.0, "enemy": [], "allies": []})
    print(f"[CONTEXTO] {time.strftime('%H:%M:%S')} | contexto limpo (nova partida).")


def shutdown_process():
    """Encerra o processo do servidor por completo. Usa os._exit porque o listener
    global de teclas (lib keyboard) deixa threads vivas que, de outra forma, manteriam
    o processo 'fantasma' rodando mesmo apos fechar a janela / parar o serve_forever."""
    print(f"\n[DESLIGAR] {time.strftime('%H:%M:%S')} | encerrando a pedido do painel. Tchau!")

    def _kill():
        time.sleep(0.4)   # da tempo da resposta HTTP chegar no navegador
        os._exit(0)

    threading.Thread(target=_kill, daemon=True).start()


# ----------------------------------------------------------------------------
# Servidor HTTP
# ----------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    # Silencia o log padrao barulhento; logamos so o que importa.
    def log_message(self, *args):
        pass

    def _send_json(self, obj, status=200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/" or self.path.startswith("/index"):
            body = DASHBOARD_HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if self.path == "/state":
            age = time.time() - LATEST["received_at"] if LATEST["received_at"] else None
            self._send_json({
                "summary": summarize(LATEST["raw"]),
                "seconds_since_update": round(age, 1) if age is not None else None,
                "raw": LATEST["raw"],
            })
            return

        if self.path == "/chat/history":
            self._send_json({
                "history": CHAT_HISTORY,
                "provider": PROVIDER.name if PROVIDER else "?",
            })
            return

        if self.path == "/heroes":
            self._send_json({"cache_ok": drafting.CACHE_OK, "heroes": drafting.heroes_for_ui()})
            return

        if self.path == "/draft/state":
            self._send_json(DRAFT_STATE)
            return

        if self.path == "/draft/suggestions":
            self._send_json(drafting.score_candidates(DRAFT_STATE))
            return

        if self.path == "/draft/grid":
            # Todos os herois livres, pontuados/ordenados pela vantagem (aba Draft)
            self._send_json(drafting.score_candidates(DRAFT_STATE, full=True))
            return

        if self.path == "/draft/scan/state":
            self._send_json(DRAFT_SCAN_STATE)
            return

        if self.path == "/draft/scan/image":
            try:
                with open(draftscan.CROP_PATH, "rb") as f:
                    body = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except FileNotFoundError:
                self._send_json({"error": "sem imagem"}, status=404)
            return

        if self.path == "/scoreboard/state":
            # Enriquece cada inimigo com a "facilidade de matar AGORA":
            #   adv  = vantagem natural do MEU heroi (counter/matchup, [-0.5,+0.5])
            #   ease = adv + forma atual pelo KDA (quem morre muito/mata pouco e mais facil)
            my_npc = ((LATEST["raw"] or {}).get("hero") or {}).get("name")
            my_id = drafting.BY_NPC.get(my_npc) if my_npc else None
            enemies = []
            for e in SCOREBOARD_STATE.get("enemies") or []:
                aid = e.get("hero_id")
                adv = drafting.advantage(my_id, aid) if (my_id and aid) else 0.0
                try:
                    k, d = int(e.get("k") or 0), int(e.get("d") or 0)
                except (TypeError, ValueError):
                    k = d = 0
                form = max(-0.2, min(0.2, (d - k) * 0.02))
                enemies.append({**e, "adv": round(adv, 3), "ease": round(adv + form, 3)})
            self._send_json({**SCOREBOARD_STATE, "enemies": enemies, "hotkey": HOTKEY_KEY})
            return

        if self.path == "/scoreboard/image":
            try:
                with open(scoreboard.CROP_PATH, "rb") as f:
                    body = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except FileNotFoundError:
                self._send_json({"error": "sem imagem"}, status=404)
            return

        # --- Minimapa ao vivo (espelho da tela -> 2a janela) ---
        if self.path == "/minimap" or self.path.startswith("/minimap?"):
            body = MINIMAP_HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if self.path.startswith("/minimap/stream"):
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
            self.send_header("Pragma", "no-cache")
            self.end_headers()
            last_sent = 0.0
            try:
                while True:
                    frame, fat = minimap.get_frame()
                    if frame is None or fat == last_sent:
                        time.sleep(0.04)
                        continue
                    last_sent = fat
                    self.wfile.write(b"--frame\r\n")
                    self.wfile.write(b"Content-Type: image/jpeg\r\n")
                    self.wfile.write(f"Content-Length: {len(frame)}\r\n\r\n".encode())
                    self.wfile.write(frame)
                    self.wfile.write(b"\r\n")
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass  # janela fechada / cliente desconectou
            return

        if self.path.startswith("/minimap/frame"):
            frame, _ = minimap.get_frame()
            if not frame:
                self._send_json({"error": "sem frame ainda"}, status=503)
                return
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(frame)))
            self.end_headers()
            self.wfile.write(frame)
            return

        if self.path == "/minimap/box":
            b = minimap.get_box()
            self._send_json({"left": b[0], "top": b[1], "right": b[2], "bottom": b[3]})
            return

        if self.path == "/voice/state":
            self._send_json(voice.get_state())
            return

        if self.path == "/voice/config":
            self._send_json(voice.public_config())
            return

        # Medidor de nivel do microfone (pro grafico de teste em Settings).
        # ?device=N testa um mic especifico antes de salvar; sem param usa o da config.
        if self.path.startswith("/voice/miclevel"):
            dev = None
            if "device=" in self.path:
                q = self.path.split("device=", 1)[1].split("&")[0]
                if q.lstrip("-").isdigit():
                    dev = int(q)
            level, rms = voice.mic_level(dev)
            self._send_json({"level": level, "rms": rms})
            return

        self._send_json({"error": "not found"}, status=404)

    def do_POST(self):
        # --- Chat com o copiloto ---
        if self.path == "/chat":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length) if length else b"{}"
            try:
                data = json.loads(body)
            except json.JSONDecodeError:
                self._send_json({"error": "json invalido"}, status=400)
                return
            msg = (data.get("message") or "").strip()
            if not msg:
                self._send_json({"error": "mensagem vazia"}, status=400)
                return
            CHAT_HISTORY.append({"role": "user", "content": msg})
            reply = PROVIDER.reply(CHAT_HISTORY, game_context_text())
            CHAT_HISTORY.append({"role": "assistant", "content": reply})
            self._send_json({"reply": reply, "provider": PROVIDER.name})
            return

        if self.path == "/chat/reset":
            CHAT_HISTORY.clear()
            self._send_json({"ok": True})
            return

        # --- Limpar o contexto entre partidas (chat + draft + placar + relatorios) ---
        if self.path == "/context/clear":
            reset_context()
            self._send_json({"ok": True})
            return

        # --- Desligar a aplicacao pelo proprio navegador ---
        if self.path == "/shutdown":
            self._send_json({"ok": True})   # responde ANTES de matar o processo
            try:
                self.wfile.flush()
            except Exception:
                pass
            shutdown_process()
            return

        # --- Draft: marcacao manual (grid) ---
        if self.path == "/draft/state":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length) if length else b"{}"
            try:
                data = json.loads(body)
            except json.JSONDecodeError:
                self._send_json({"error": "json invalido"}, status=400)
                return

            def ints(key):
                return [int(x) for x in (data.get(key) or []) if str(x).lstrip("-").isdigit()]

            DRAFT_STATE["enemy"] = ints("enemy")
            DRAFT_STATE["allies"] = ints("allies")
            DRAFT_STATE["bans"] = ints("bans")
            DRAFT_STATE["source"] = "manual"
            self._send_json({"ok": True, "state": DRAFT_STATE})
            return

        if self.path == "/draft/clear":
            DRAFT_STATE.update({"enemy": [], "allies": [], "bans": [], "source": "auto"})
            self._send_json({"ok": True})
            return

        if self.path == "/scoreboard/scan":
            self._send_json(do_scoreboard_scan())
            return

        if self.path == "/draft/scan":
            self._send_json(do_draft_scan())
            return

        if self.path == "/scoreboard/hotkey":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length) if length else b"{}"
            try:
                key = (json.loads(body).get("key") or "").strip().lower()
            except json.JSONDecodeError:
                key = ""
            if not key:
                self._send_json({"error": "tecla invalida"}, status=400)
                return
            ok = start_hotkey(key)
            self._send_json({"ok": ok, "hotkey": HOTKEY_KEY})
            return

        if self.path == "/minimap/box":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length) if length else b"{}"
            try:
                d = json.loads(body)
                minimap.set_box(d["left"], d["top"], d["right"], d["bottom"])
            except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                self._send_json({"error": "caixa invalida"}, status=400)
                return
            b = minimap.get_box()
            self._send_json({"ok": True, "left": b[0], "top": b[1], "right": b[2], "bottom": b[3]})
            return

        # --- Voz: atalho "me ouvir" (OpenAI Whisper + gpt-4o-mini-tts) ---
        if self.path == "/voice/listen":
            threading.Thread(target=lambda: voice.run_listen(voice_handle), daemon=True).start()
            self._send_json({"ok": True})
            return

        # --- Voz: testar (FALA uma frase de teste, nao grava o microfone) ---
        if self.path == "/voice/test":
            ok = voice.test_voice()
            self._send_json({"ok": ok})
            return

        if self.path == "/voice/config":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length) if length else b"{}"
            try:
                d = json.loads(body)
            except json.JSONDecodeError:
                self._send_json({"error": "json invalido"}, status=400)
                return
            if d.get("clear"):
                voice.set_key(clear=True)
            elif d.get("key"):
                voice.set_key(key=d.get("key"))
            voice.save_config(d)
            start_voice_hotkey()  # re-registra caso a tecla tenha mudado
            self._send_json({"ok": True, **voice.public_config()})
            return

        # --- Estado do jogo vindo do Dota ---
        if self.path != "/gsi":
            self._send_json({"error": "not found"}, status=404)
            return

        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b"{}"

        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            self._send_json({"error": "json invalido"}, status=400)
            return

        # Valida o token de autenticacao que configuramos no .cfg
        token = (data.get("auth") or {}).get("token")
        if token != AUTH_TOKEN:
            self._send_json({"error": "token invalido"}, status=403)
            return

        LATEST["raw"] = data
        LATEST["received_at"] = time.time()

        s = summarize(data)
        print(
            f"[GSI] {time.strftime('%H:%M:%S')} | "
            f"estado={s.get('game_state')} | clock={s.get('clock')} | "
            f"heroi={s.get('hero')} | gold={s.get('gold')}"
        )

        # Oportunista: se o GSI trouxer o bloco draft populado (espectador/CM),
        # preenche o DRAFT_STATE - desde que o usuario nao tenha editado manualmente.
        if DRAFT_STATE["source"] != "manual":
            parsed = drafting.parse_gsi_draft(data)
            if parsed:
                DRAFT_STATE["enemy"] = parsed["enemy"]
                DRAFT_STATE["allies"] = parsed["allies"]
                DRAFT_STATE["bans"] = parsed["bans"]
                DRAFT_STATE["source"] = "gsi"
            # Mesmo jogando (sem bloco draft), o GSI expoe o SEU heroi: marca-o como aliado.
            my_id = drafting.BY_NPC.get(((data.get("hero") or {}).get("name")) or "")
            if my_id and my_id not in DRAFT_STATE["allies"] \
                    and my_id not in DRAFT_STATE["enemy"] and my_id not in DRAFT_STATE["bans"]:
                DRAFT_STATE["allies"].append(my_id)

        self._send_json({"ok": True})


# ----------------------------------------------------------------------------
# Painel web (HTML+JS inline; faz polling em /state a cada 1s)
# ----------------------------------------------------------------------------
DASHBOARD_HTML = """<!doctype html>
<html lang="pt-br">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Copiloto Dota 2</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Cinzel:wght@500;600;700;900&family=Rajdhani:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
  /* ============ Tema Dota 2 ============ */
  :root{
    --bg:#070910; --panel:#10151f; --panel2:#0b101a; --panel-hi:#161e2b;
    --line:#212a39; --line-soft:#19212e;
    --gold:#c8aa6e; --gold-hi:#f1d191; --gold-dim:#7c6838;
    --red:#c0392b; --red-hi:#e85a45; --red-deep:#7c1f17;
    --rad:#7ec94f; --rad-dim:#3f6f29;
    --dire:#e24a3b; --dire-dim:#7a241c;
    --tx:#e9eef6; --tx2:#93a0b4; --tx3:#586272;
    --ok:#48c569; --warn:#d9a534;
    --r:5px;
    color-scheme:dark;
  }
  *{box-sizing:border-box}
  html,body{height:100%}
  body{
    margin:0; background:var(--bg); color:var(--tx);
    font-family:'Rajdhani',system-ui,'Segoe UI',sans-serif; font-size:15px;
    overflow:hidden; -webkit-font-smoothing:antialiased;
  }
  body::before{
    content:''; position:fixed; inset:0; z-index:0; pointer-events:none;
    background:
      radial-gradient(1100px 480px at 50% -8%, rgba(192,57,43,.12), transparent 62%),
      radial-gradient(900px 700px at 112% 118%, rgba(40,64,100,.14), transparent 60%),
      linear-gradient(180deg,#0a0e15 0%,#06080d 100%);
  }
  ::-webkit-scrollbar{width:10px;height:10px}
  ::-webkit-scrollbar-track{background:#0a0e15}
  ::-webkit-scrollbar-thumb{background:#222c3b;border-radius:6px;border:2px solid #0a0e15}
  ::-webkit-scrollbar-thumb:hover{background:#33415a}

  .display{font-family:'Cinzel','Trajan Pro',serif}

  /* ============ Estrutura ============ */
  .app{position:relative;z-index:1;height:100vh;display:flex;flex-direction:column}
  .layout{flex:1;display:grid;grid-template-columns:214px 1fr;min-height:0}
  .sidebar{background:linear-gradient(180deg,#0c111a,#080b12);border-right:1px solid var(--line);
           display:flex;flex-direction:column;overflow-y:auto;padding:14px 0 12px}
  .content{overflow-y:auto;min-width:0;padding:20px 22px 40px}

  /* ============ Topbar ============ */
  .topbar{flex:none;height:66px;display:flex;align-items:center;gap:18px;padding:0 20px;
          background:linear-gradient(180deg,#11161f,#0b0f17);
          border-bottom:1px solid var(--line);
          box-shadow:0 2px 14px rgba(0,0,0,.45);position:relative}
  .topbar::after{content:'';position:absolute;left:0;right:0;bottom:-1px;height:1px;
                 background:linear-gradient(90deg,transparent,rgba(200,170,110,.45),transparent)}
  .brand{display:flex;align-items:center;gap:12px;min-width:200px}
  .brand .logo{width:40px;height:40px;flex:none;display:grid;place-items:center;
               background:radial-gradient(circle at 50% 35%,#3a0f0a,#170707);
               border:1px solid #5a1b13;border-radius:7px;box-shadow:0 0 14px rgba(192,57,43,.35),inset 0 0 10px rgba(232,90,69,.25)}
  .brand .logo svg{width:24px;height:24px}
  .brand .bt{line-height:1}
  .brand .bt b{font-family:'Cinzel',serif;font-weight:900;font-size:18px;letter-spacing:1.5px;
               background:linear-gradient(180deg,#f3d9a6,#c0392b);-webkit-background-clip:text;background-clip:text;color:transparent}
  .brand .bt b span{color:var(--tx);-webkit-text-fill-color:var(--tx)}
  .brand .bt small{display:block;font-size:9.5px;letter-spacing:3px;color:var(--gold-dim);margin-top:2px}

  .livematch{flex:1;display:flex;align-items:center;justify-content:center;gap:16px}
  .lm-mode{text-align:right;min-width:140px}
  .lm-mode .live{display:inline-flex;align-items:center;gap:6px;font-size:11px;font-weight:700;letter-spacing:1.5px;color:var(--red-hi)}
  .lm-mode .live i{width:8px;height:8px;border-radius:50%;background:#555;display:inline-block}
  .lm-mode.on .live i{background:var(--red-hi);box-shadow:0 0 9px var(--red-hi);animation:pulse 1.6s infinite}
  @keyframes pulse{50%{opacity:.35}}
  .lm-mode .phase{font-size:12px;color:var(--tx2);letter-spacing:.5px;text-transform:uppercase}
  .lm-core{display:flex;align-items:center;gap:12px}
  .ports{display:flex;gap:4px}
  .ports.dire{flex-direction:row-reverse}
  .ports .port{width:42px;height:30px}
  .lm-score{display:flex;align-items:center;gap:14px;padding:0 4px}
  .lm-score .sc{font-family:'Rajdhani';font-weight:700;font-size:30px;line-height:1;min-width:38px;text-align:center}
  .lm-score .sc.rad{color:var(--rad);text-shadow:0 0 16px rgba(126,201,79,.4)}
  .lm-score .sc.dire{color:var(--dire);text-shadow:0 0 16px rgba(226,74,59,.4)}
  .lm-score .sc small{display:block;font-size:9px;letter-spacing:2px;color:var(--tx3);font-weight:600;margin-top:3px}
  .lm-clock{display:grid;place-items:center;width:62px;height:62px;border-radius:50%;
            border:2px solid var(--gold-dim);background:radial-gradient(circle,#11161f,#0a0e15);
            font-weight:700;font-size:17px;color:var(--gold-hi);box-shadow:inset 0 0 12px rgba(0,0,0,.6),0 0 10px rgba(200,170,110,.12)}
  .topstat{min-width:150px;display:flex;justify-content:flex-end;align-items:center;gap:10px}
  .conn{display:flex;align-items:center;gap:8px;font-size:11px;font-weight:700;letter-spacing:1px;color:var(--tx2);
        text-transform:uppercase;padding:6px 11px;border:1px solid var(--line);border-radius:20px;background:#0c1119}
  .dot{width:9px;height:9px;border-radius:50%;background:var(--red);transition:.3s;flex:none}
  .dot.live{background:var(--ok);box-shadow:0 0 9px var(--ok)}
  .voicebtn{padding:7px 13px;border-radius:20px;font-size:12px;letter-spacing:.5px;display:flex;align-items:center;gap:6px}
  .voicebtn.rec{background:linear-gradient(180deg,#c0392b,#8a2018);border-color:#9a2a1f;color:#fff;
                box-shadow:0 0 12px rgba(226,74,59,.5);animation:pulse 1.3s infinite}
  .voicebtn.busy{opacity:.7;cursor:default}
  .topbtn{padding:7px 12px;border-radius:20px;font-size:12px;letter-spacing:.5px;
          display:flex;align-items:center;gap:6px;white-space:nowrap}
  .topbtn.danger{border-color:#7c1f17;color:#e8a99f}
  .topbtn.danger:hover{background:linear-gradient(180deg,#c0392b,#8a2018);border-color:#9a2a1f;color:#fff}
  /* tela cheia mostrada quando a aplicacao e desligada pelo painel */
  .killscreen{position:fixed;inset:0;z-index:99;display:none;flex-direction:column;gap:14px;
              align-items:center;justify-content:center;text-align:center;padding:24px;
              background:rgba(5,7,12,.94);backdrop-filter:blur(4px)}
  .killscreen.on{display:flex}
  .killscreen .kc-ico{font-size:48px;color:var(--red-hi);filter:drop-shadow(0 0 14px rgba(226,74,59,.5))}
  .killscreen h2{font-family:'Cinzel',serif;letter-spacing:2px;margin:0;color:var(--tx)}
  .killscreen p{color:var(--tx2);margin:0;max-width:440px;line-height:1.5}
  .killscreen code{background:#141b27;border:1px solid #2b3647;border-radius:5px;padding:2px 8px;color:var(--gold-hi)}
  .cfg{display:flex;flex-direction:column;gap:15px}
  .cfg-row{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
  .cfg-row > label{font-size:12px;color:var(--tx2);letter-spacing:.5px;min-width:118px}
  .cfg-input{flex:1;min-width:220px;background:#0a0e15;border:1px solid #2b3647;border-radius:var(--r);
             color:var(--tx);padding:9px 12px;font-size:13px;font-family:inherit}
  .cfg-input:focus{outline:none;border-color:var(--gold-dim)}
  .cfg-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:12px}
  .cfg-cell{display:flex;flex-direction:column;gap:6px}
  .cfg-cell label{font-size:10.5px;color:var(--tx3);text-transform:uppercase;letter-spacing:1px}
  .cfg-cell select{width:100%}
  .cfg-group{margin-top:6px;padding-top:12px;border-top:1px solid var(--line-soft);
             font-size:12px;font-weight:700;letter-spacing:.8px;color:var(--gold);
             display:flex;align-items:baseline;gap:8px;flex-wrap:wrap}
  .cfg-group small{font-weight:500;letter-spacing:.2px;color:var(--tx3);font-size:11.5px}
  .cfg-note{font-size:11.5px;color:var(--tx3);line-height:1.5;margin-top:-6px}
  .meter{flex:1;min-width:160px;height:16px;border-radius:8px;background:#0a0e15;border:1px solid var(--line);overflow:hidden;position:relative}
  .meter i{display:block;height:100%;width:0%;border-radius:8px;background:linear-gradient(90deg,var(--rad-dim),var(--rad) 70%,var(--gold));transition:width .07s linear}
  .meter.live{border-color:var(--rad-dim)}
  .voicehint{font-size:12.5px;line-height:1.5;color:var(--tx2);background:linear-gradient(180deg,rgba(192,57,43,.10),rgba(11,16,26,.5));
             border:1px solid #3a2420;border-radius:var(--r);padding:9px 12px;margin-bottom:10px}
  .voicehint b{color:var(--gold-hi)}

  /* ============ Aviso global de captura do placar ============ */
  .scanflash{position:fixed;inset:0;z-index:90;pointer-events:none;opacity:0;
             box-shadow:inset 0 0 0 3px rgba(232,90,69,.7), inset 0 0 120px rgba(226,74,59,.35)}
  .scanflash.go{animation:scanflash .6s ease-out}
  @keyframes scanflash{0%{opacity:1}100%{opacity:0}}
  .scantoast{position:fixed;top:80px;left:50%;transform:translate(-50%,-18px);z-index:95;
             display:none;align-items:center;gap:14px;min-width:330px;max-width:480px;
             padding:14px 18px;border-radius:10px;cursor:pointer;
             background:linear-gradient(180deg,#1b232f,#10151f);border:1px solid var(--gold-dim);
             box-shadow:0 16px 44px rgba(0,0,0,.6),0 0 24px rgba(200,170,110,.18);
             opacity:0;transition:opacity .25s ease, transform .25s ease}
  .scantoast.show{display:flex;opacity:1;transform:translate(-50%,0)}
  .scantoast.ok{border-color:#2f6b3a;box-shadow:0 16px 44px rgba(0,0,0,.6),0 0 24px rgba(72,197,105,.22)}
  .scantoast.err{border-color:#7a2a22;box-shadow:0 16px 44px rgba(0,0,0,.6),0 0 24px rgba(226,74,59,.28)}
  .st-ic{width:42px;height:42px;flex:none;display:grid;place-items:center;border-radius:8px;
         background:#0b101a;border:1px solid var(--line);font-size:21px;position:relative}
  .st-ic .st-spin{position:absolute;width:36px;height:36px;border:3px solid rgba(200,170,110,.22);
                  border-top-color:var(--gold);border-radius:50%;animation:sp .8s linear infinite;display:none}
  .scantoast.busy .st-ic .st-spin{display:block}
  .scantoast.busy .st-emoji{display:none}
  .st-title{font-weight:700;font-size:15.5px;color:var(--tx);letter-spacing:.3px}
  .scantoast.ok .st-title{color:var(--rad)} .scantoast.err .st-title{color:var(--dire)}
  .st-sub{font-size:12px;color:var(--tx2);margin-top:1px}
  .st-thumb{height:44px;border-radius:5px;border:1px solid var(--line);margin-left:auto;display:none}
  .scantoast.has-thumb .st-thumb{display:block}

  /* ============ Sidebar nav ============ */
  .nav{display:flex;flex-direction:column;gap:2px;padding:0 10px}
  .nav-item{display:flex;align-items:center;gap:12px;padding:11px 12px;border-radius:var(--r);
            color:var(--tx2);cursor:pointer;font-weight:600;font-size:13.5px;letter-spacing:.6px;
            text-transform:uppercase;border:1px solid transparent;position:relative;transition:.15s}
  .nav-item svg{width:19px;height:19px;flex:none;opacity:.8}
  .nav-item:hover{background:#121a26;color:var(--tx)}
  .nav-item.active{color:var(--gold-hi);background:linear-gradient(90deg,rgba(192,57,43,.18),rgba(192,57,43,.02));
                   border-color:#3a2420}
  .nav-item.active::before{content:'';position:absolute;left:0;top:6px;bottom:6px;width:3px;border-radius:2px;
                           background:linear-gradient(180deg,var(--red-hi),var(--red));box-shadow:0 0 10px var(--red-hi)}
  .nav-item.active svg{opacity:1;color:var(--gold)}
  .nav-soon{margin-left:auto;font-size:8.5px;letter-spacing:1px;color:var(--tx3);border:1px solid var(--line);
            border-radius:4px;padding:1px 5px}

  .side-foot{margin-top:auto;padding:12px 12px 0;display:flex;flex-direction:column;gap:10px}
  .agentcard{display:flex;align-items:center;gap:10px;padding:10px;border:1px solid #2a1c1a;border-radius:var(--r);
             background:linear-gradient(180deg,rgba(192,57,43,.12),rgba(192,57,43,.03))}
  .agentcard .av{width:34px;height:34px;border-radius:6px;flex:none;display:grid;place-items:center;
                 background:radial-gradient(circle,#3a0f0a,#170707);border:1px solid #5a1b13;
                 box-shadow:0 0 12px rgba(192,57,43,.4)}
  .agentcard .av svg{width:18px;height:18px}
  .agentcard b{font-size:13px;color:var(--tx);display:block;line-height:1.2}
  .agentcard span{font-size:11px;color:var(--ok);display:flex;align-items:center;gap:5px}
  .agentcard span i{width:6px;height:6px;border-radius:50%;background:var(--ok);box-shadow:0 0 6px var(--ok)}
  .sidenote{font-size:11.5px;line-height:1.5;color:var(--tx3);border:1px dashed var(--line);border-radius:var(--r);padding:10px}

  /* ============ Panels ============ */
  .panel{background:linear-gradient(180deg,var(--panel),var(--panel2));border:1px solid var(--line);
         border-radius:var(--r);padding:15px;position:relative;overflow:hidden}
  .panel::before{content:'';position:absolute;top:0;left:0;right:0;height:1px;
                 background:linear-gradient(90deg,transparent,rgba(200,170,110,.3),transparent)}
  .ptitle{font-size:12px;font-weight:700;letter-spacing:1.6px;text-transform:uppercase;color:var(--gold);
          margin:0 0 13px;display:flex;align-items:center;gap:9px}
  .ptitle::before{content:'';width:3px;height:14px;background:linear-gradient(180deg,var(--red-hi),var(--red));border-radius:2px;flex:none}
  .ptitle .grow{flex:1;height:1px;background:linear-gradient(90deg,var(--line),transparent)}
  .ptitle .acc{color:var(--tx3);font-size:10px;letter-spacing:1px}
  .empty{color:var(--tx3);font-style:italic;font-size:13px}

  /* portraits / icons */
  .port{border-radius:4px;overflow:hidden;background:#0a0e15;border:1px solid var(--line);position:relative;flex:none}
  .port img{width:100%;height:100%;object-fit:cover;display:block}
  .port.ally{border-color:var(--rad-dim);box-shadow:inset 0 0 0 1px rgba(126,201,79,.18)}
  .port.enemy{border-color:var(--dire-dim);box-shadow:inset 0 0 0 1px rgba(226,74,59,.2)}

  /* ============ Dashboard grid ============ */
  .dash{display:grid;grid-template-columns:300px minmax(0,1fr) 318px;gap:16px;align-items:start}
  .col{display:flex;flex-direction:column;gap:16px;min-width:0}

  /* hero panel */
  .hero-portrait{height:168px;border-radius:var(--r);overflow:hidden;position:relative;background:#0a0e15;border:1px solid var(--line)}
  .hero-portrait img{width:100%;height:100%;object-fit:cover;object-position:50% 22%}
  .hero-portrait .ov{position:absolute;inset:0;background:linear-gradient(180deg,rgba(8,10,16,.05) 40%,rgba(8,10,16,.92))}
  .hero-portrait .nm{position:absolute;left:14px;right:14px;bottom:10px}
  .hero-portrait .nm b{font-family:'Cinzel',serif;font-weight:700;font-size:21px;color:var(--gold-hi);
                       text-shadow:0 2px 8px #000;letter-spacing:.5px;display:block;line-height:1.05}
  .hero-portrait .nm small{color:var(--tx2);font-size:12px;letter-spacing:1.5px;text-transform:uppercase}
  .hero-portrait .lvl{position:absolute;top:10px;right:10px;width:34px;height:34px;border-radius:50%;
                      display:grid;place-items:center;font-weight:700;font-size:15px;color:#1a1206;
                      background:radial-gradient(circle,var(--gold-hi),var(--gold));border:2px solid #4a3a18;box-shadow:0 0 10px rgba(200,170,110,.5)}
  .bars{display:flex;flex-direction:column;gap:5px;margin:11px 0 4px}
  .bar{height:9px;border-radius:5px;background:#0a0e15;border:1px solid var(--line-soft);overflow:hidden;position:relative}
  .bar i{position:absolute;left:0;top:0;bottom:0;border-radius:5px;transition:width .4s}
  .bar.hp i{background:linear-gradient(90deg,#3f7a2a,#7ec94f)}
  .bar.mp i{background:linear-gradient(90deg,#1f5a8a,#46a6e8)}
  .statgrid{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:12px}
  .stat{background:#0b101a;border:1px solid var(--line-soft);border-radius:var(--r);padding:9px 11px}
  .stat .l{font-size:10px;letter-spacing:1px;color:var(--tx3);text-transform:uppercase}
  .stat .v{font-size:18px;font-weight:700;color:var(--tx);margin-top:1px}
  .stat .v b{color:var(--rad)} .stat .v i{color:var(--dire);font-style:normal} .stat .v u{color:#5aaee8;text-decoration:none}
  .stat .v.gold{color:var(--gold-hi)}
  .subh{font-size:10.5px;letter-spacing:1.4px;color:var(--tx3);text-transform:uppercase;margin:15px 0 8px;
        display:flex;align-items:center;gap:8px}
  .subh::after{content:'';flex:1;height:1px;background:var(--line-soft)}

  .abilities{display:flex;gap:7px}
  .ab{width:42px;height:42px;border-radius:5px;background:#0a0e15;border:1px solid var(--line);position:relative;overflow:hidden}
  .ab img{width:100%;height:100%;object-fit:cover}
  .ab.ult{border-color:var(--gold-dim);box-shadow:0 0 8px rgba(200,170,110,.25)}
  .ab .lvl{position:absolute;bottom:0;right:0;font-size:9px;font-weight:700;padding:0 3px;color:var(--gold-hi);
           background:rgba(0,0,0,.8);border-top-left-radius:4px}
  .items{display:grid;grid-template-columns:repeat(3,1fr);gap:6px}
  .islot{aspect-ratio:1.35;border-radius:4px;background:#0a0e15;border:1px solid var(--line-soft);position:relative;overflow:hidden}
  .islot img{width:100%;height:100%;object-fit:cover}
  .islot.empty{background:repeating-linear-gradient(45deg,#0a0e15,#0a0e15 6px,#0c111a 6px,#0c111a 12px)}
  .islot .chg{position:absolute;bottom:0;right:1px;font-size:9px;font-weight:700;color:var(--gold-hi);text-shadow:0 0 3px #000}

  /* insights */
  .section-h{font-family:'Cinzel',serif;font-weight:700;font-size:15px;letter-spacing:1.5px;color:var(--tx);
             text-transform:uppercase;margin:2px 0 4px;display:flex;align-items:center;gap:10px}
  .section-h .grow{flex:1;height:1px;background:linear-gradient(90deg,rgba(200,170,110,.3),transparent)}
  .priority{border:1px solid #4a2a22;background:linear-gradient(180deg,rgba(192,57,43,.10),rgba(16,21,31,.7))}
  .priority .tag{display:inline-flex;align-items:center;gap:6px;font-size:11px;font-weight:700;letter-spacing:1.4px;
                 color:var(--red-hi);text-transform:uppercase;margin-bottom:10px}
  .priority .rep{font-size:14.5px;line-height:1.6;color:#dde6f1;white-space:pre-wrap}
  .priority .rep strong{color:var(--gold-hi)}
  .threats{display:flex;gap:8px;flex-wrap:wrap}
  .threats .port{width:62px;height:38px}
  .threats .port .tnm{position:absolute;left:0;right:0;bottom:0;font-size:9px;text-align:center;color:var(--tx);
                      background:linear-gradient(transparent,rgba(0,0,0,.85));padding:6px 1px 1px;line-height:1}
  .sit{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}
  .sit .c{background:#0b101a;border:1px solid var(--line-soft);border-radius:var(--r);padding:11px}
  .sit .c .l{font-size:10px;letter-spacing:1px;color:var(--tx3);text-transform:uppercase}
  .sit .c .v{font-size:16px;font-weight:700;margin-top:3px;color:var(--tx)}
  .sit .c .v.good{color:var(--rad)} .sit .c .v.bad{color:var(--dire)} .sit .c .v.g{color:var(--gold-hi)}
  .sit .c .mini{font-size:11px;color:var(--tx2);margin-top:2px}
  .advbar{height:7px;border-radius:4px;background:#3a1714;overflow:hidden;margin-top:7px}
  .advbar i{display:block;height:100%;background:linear-gradient(90deg,var(--rad-dim),var(--rad));border-radius:4px;transition:width .4s}
  .quicktip{border:1px solid var(--line);background:linear-gradient(180deg,rgba(40,30,15,.35),rgba(11,16,26,.6))}
  .quicktip .ptitle{color:var(--gold-hi)}
  .quicktip .body{font-size:13.5px;line-height:1.55;color:var(--tx2)}

  /* right rail enemies */
  .enemy-row{display:flex;align-items:center;gap:11px;padding:9px;border-radius:var(--r);background:#0b101a;
             border:1px solid var(--line-soft);margin-bottom:8px;border-left:3px solid var(--dire-dim)}
  .enemy-row .port{width:54px;height:32px}
  .enemy-row .nm{font-size:13.5px;font-weight:600;color:var(--tx);line-height:1.1}
  .enemy-row .pl{font-size:11px;color:var(--tx3)}
  .enemy-row .einfo{min-width:0;flex:1}
  .enemy-row .eright{margin-left:auto;display:flex;flex-direction:column;align-items:flex-end;gap:3px}
  .enemy-row .kda{text-align:right;font-size:13px;font-weight:600;white-space:nowrap}
  .enemy-row .kda b{color:var(--rad)} .enemy-row .kda i{color:var(--dire);font-style:normal} .enemy-row .kda u{color:#5aaee8;text-decoration:none}
  .erank{flex:none;width:18px;text-align:center;font-weight:700;font-size:13px;color:var(--gold);font-family:'Rajdhani'}
  .advbadge{font-size:11px;font-weight:700;padding:1px 7px;border-radius:10px;white-space:nowrap;letter-spacing:.3px}
  .advbadge.adv-good{color:var(--rad);background:rgba(126,201,79,.13);border:1px solid var(--rad-dim)}
  .advbadge.adv-bad{color:var(--dire);background:rgba(226,74,59,.13);border:1px solid var(--dire-dim)}
  .advbadge.adv-neu{color:var(--tx3);background:#10151f;border:1px solid var(--line)}

  .donut-wrap{display:flex;align-items:center;gap:14px}
  .donut{width:118px;height:118px;flex:none}
  .donut .d-num{font:700 19px 'Rajdhani';fill:var(--tx)}
  .donut .d-lbl{font:600 9px 'Rajdhani';fill:var(--tx3);letter-spacing:2px}
  .legend{display:flex;flex-direction:column;gap:8px;font-size:13px}
  .legend .li{display:flex;align-items:center;gap:8px;color:var(--tx2)}
  .legend .li b{margin-left:auto;color:var(--tx);font-weight:700}
  .legend .sw{width:11px;height:11px;border-radius:3px;flex:none}

  .map-soon{height:150px;border-radius:var(--r);border:1px solid var(--line);display:grid;place-items:center;
            text-align:center;background:
              radial-gradient(circle at 30% 70%,rgba(126,201,79,.06),transparent 40%),
              radial-gradient(circle at 70% 30%,rgba(226,74,59,.06),transparent 40%),
              repeating-linear-gradient(45deg,#0a0e15,#0a0e15 12px,#0b101a 12px,#0b101a 24px)}
  .map-soon span{font-size:12px;color:var(--tx3);letter-spacing:1px}
  .map-soon b{display:block;color:var(--tx2);font-size:13px;margin-bottom:3px;letter-spacing:1.5px}

  /* minimapa ao vivo (thumbnail no dashboard) */
  .mini-map{position:relative;aspect-ratio:1/1;border-radius:var(--r);overflow:hidden;border:1px solid var(--line);
            background:repeating-linear-gradient(45deg,#0a0e15,#0a0e15 12px,#0b101a 12px,#0b101a 24px)}
  .mini-map img{width:100%;height:100%;object-fit:contain;display:none;background:#05070b}
  .mini-map.live img{display:block}
  .mm-hint{position:absolute;inset:0;display:grid;place-items:center;text-align:center;gap:3px;pointer-events:none}
  .mini-map.live .mm-hint{display:none}
  .mm-hint b{display:block;color:var(--tx2);font-size:13px;letter-spacing:1.5px}
  .mm-hint span{font-size:11px;color:var(--tx3);letter-spacing:.5px}
  .btn.mm-open{width:100%;margin-top:10px;text-align:center}

  /* ============ Toolbar / chips / buttons ============ */
  .toolbar{display:flex;flex-wrap:wrap;align-items:center;gap:10px;margin-bottom:14px}
  .toolbar label{font-size:12px;color:var(--tx2);letter-spacing:.5px}
  select,.btn{background:#141b27;border:1px solid #2b3647;color:var(--tx);border-radius:var(--r);
              padding:9px 13px;font-size:13px;font-family:inherit;font-weight:600;cursor:pointer;letter-spacing:.5px}
  select:hover,.btn:hover{border-color:#3f4f68}
  .btn.primary{background:linear-gradient(180deg,#c0392b,#8a2018);border-color:#9a2a1f;color:#fff;
               box-shadow:0 2px 10px rgba(192,57,43,.3)}
  .btn.primary:hover{background:linear-gradient(180deg,#d8463a,#a02b20)}
  .btn:disabled{opacity:.5;cursor:default}
  .kbd{background:#0a0e15;border:1px solid #2b3647;border-radius:4px;padding:3px 9px;font-size:12px;font-weight:700;color:var(--gold-hi)}
  .chip{display:inline-flex;align-items:center;gap:7px;font-size:12.5px;padding:7px 13px;border-radius:20px;
        background:#121a26;border:1px solid var(--line);color:var(--tx2);font-weight:600;cursor:default}
  .chip.click{cursor:pointer}
  .chip.go{color:var(--ok);border-color:#27502f;background:#0f2417}
  .chip.work{color:var(--warn);border-color:#5a4a16;background:#241d0c}
  .chip.err{color:var(--red-hi);border-color:#5a2222;background:#240f0f}
  .spin{width:13px;height:13px;border:2px solid #d2992255;border-top-color:var(--warn);border-radius:50%;animation:sp .7s linear infinite}
  @keyframes sp{to{transform:rotate(360deg)}}
  #thumb{height:54px;border-radius:5px;border:1px solid var(--line);display:none}

  .teams{display:grid;grid-template-columns:1fr 1fr;gap:16px}
  .team h3{font-size:12px;letter-spacing:1.2px;text-transform:uppercase;margin:0 0 9px;display:flex;align-items:center;gap:8px}
  .team.ally h3{color:var(--rad)} .team.enemy h3{color:var(--dire)}
  .team h3 i{width:8px;height:8px;border-radius:2px;display:inline-block}
  .team.ally h3 i{background:var(--rad)} .team.enemy h3 i{background:var(--dire)}
  .hero{display:flex;align-items:center;gap:10px;padding:8px;border-radius:var(--r);background:#0b101a;
        border:1px solid var(--line-soft);margin-bottom:7px}
  .team.enemy .hero{border-left:3px solid var(--dire-dim)} .team.ally .hero{border-left:3px solid var(--rad-dim)}
  .hero .port{width:54px;height:32px}
  .hero .nm{font-size:13.5px;font-weight:600;line-height:1.1}
  .hero .pl{font-size:11px;color:var(--tx3)}
  .hero .kda{margin-left:auto;font-size:13px;font-weight:600;white-space:nowrap}
  .hero .kda b{color:var(--rad)} .hero .kda i{color:var(--dire);font-style:normal} .hero .kda u{color:#5aaee8;text-decoration:none}

  .report{background:#0a0f18;border:1px solid #243049;border-radius:var(--r);padding:15px;font-size:14.5px;
          line-height:1.6;color:#dde6f1;white-space:pre-wrap}
  .report.empty2{color:var(--tx3);font-style:italic;border-style:dashed}
  .report strong{color:var(--gold-hi)}

  /* live fields (game status) */
  .fields{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:11px}
  .field{background:#0b101a;border:1px solid var(--line-soft);border-radius:var(--r);padding:12px 14px}
  .field .l{font-size:10px;letter-spacing:1px;color:var(--tx3);text-transform:uppercase}
  .field .v{font-size:20px;font-weight:700;margin-top:3px}
  pre{background:#06090f;border:1px solid var(--line);border-radius:var(--r);padding:12px;overflow:auto;
      font-size:11.5px;max-height:340px;color:#8fa3bd;font-family:ui-monospace,Consolas,monospace}

  /* chat */
  #chat{display:flex;flex-direction:column;gap:12px;height:100%}
  #log{display:flex;flex-direction:column;gap:9px;flex:1;overflow-y:auto;padding:4px;min-height:300px}
  .msg{padding:10px 13px;border-radius:11px;font-size:14px;line-height:1.5;white-space:pre-wrap;max-width:86%}
  .msg.user{align-self:flex-end;background:linear-gradient(180deg,rgba(192,57,43,.22),rgba(192,57,43,.08));border:1px solid #5a2a22;color:#fbe9e4}
  .msg.bot{align-self:flex-start;background:#0e1521;border:1px solid var(--line)}
  .msg strong{color:var(--gold-hi)}
  #chatform{display:flex;gap:9px}
  #chatinput{flex:1;background:#0a0e15;border:1px solid #2b3647;border-radius:9px;color:var(--tx);
             padding:11px 13px;font-size:14px;font-family:inherit;resize:none}
  #chatinput:focus{outline:none;border-color:var(--gold-dim)}
  #chatform button{border:none;border-radius:9px;padding:0 16px;font-weight:700;cursor:pointer;font-family:inherit;font-size:14px}
  #chatsend{background:linear-gradient(180deg,#c0392b,#8a2018);color:#fff}
  #micbtn{background:#141b27;border:1px solid #2b3647!important;font-size:17px;color:var(--tx)}
  #micbtn.rec{background:var(--red);border-color:var(--red)!important}

  /* views */
  .view{display:none;animation:fade .25s ease}
  .view.active{display:block}
  @keyframes fade{from{opacity:0;transform:translateY(4px)}to{opacity:1;transform:none}}
  .grid2{display:grid;grid-template-columns:1fr 1fr;gap:16px;align-items:start}
  .stack{display:flex;flex-direction:column;gap:16px}
  .soon-big{display:grid;place-items:center;min-height:340px;text-align:center;gap:8px}
  .soon-big .ic{width:60px;height:60px;opacity:.4}
  .soon-big b{font-family:'Cinzel',serif;font-size:18px;letter-spacing:1px;color:var(--tx2)}
  .soon-big p{color:var(--tx3);max-width:380px;line-height:1.6;font-size:13.5px}

  /* responsive */
  @media (max-width:1280px){
    .dash{grid-template-columns:280px minmax(0,1fr)}
    .dash .rail{grid-column:1 / -1}
    .rail-inner{display:grid;grid-template-columns:1fr 1fr 1fr;gap:16px;align-items:start}
  }
  @media (max-width:920px){
    .layout{grid-template-columns:1fr}
    .sidebar{display:none}
    .dash{grid-template-columns:1fr}
    .rail-inner{grid-template-columns:1fr}
    .livematch{display:none}
  }

  /* ============ Aba Draft (grid de picks + counters ao vivo) ============ */
  .nav-live{margin-left:auto;font-size:8.5px;font-weight:700;letter-spacing:1px;color:#fff;
            border-radius:4px;padding:1px 6px;background:linear-gradient(180deg,var(--red-hi),var(--red));
            box-shadow:0 0 9px rgba(226,74,59,.6);animation:pulse 1.5s infinite;display:none}
  .nav-live.on{display:inline-block}
  .draft-wrap{display:grid;grid-template-columns:minmax(0,1fr) 318px;gap:16px;align-items:start}
  @media (max-width:1100px){.draft-wrap{grid-template-columns:1fr}}
  .dmode{display:inline-flex;gap:4px;background:#0a0e15;border:1px solid var(--line);border-radius:7px;padding:3px}
  .dmode button{background:transparent;border:1px solid transparent;color:var(--tx2);border-radius:5px;
                padding:6px 12px;font-size:12.5px;font-weight:700;letter-spacing:.4px;cursor:pointer;font-family:inherit}
  .dmode button.on.enemy{background:linear-gradient(180deg,#c0392b,#8a2018);color:#fff;border-color:#9a2a1f}
  .dmode button.on.ally{background:linear-gradient(180deg,#3f7a2a,#2a5a1c);color:#fff;border-color:#3f6f29}
  .dmode button.on.ban{background:linear-gradient(180deg,#3a4254,#272e3c);color:#fff;border-color:#4a5468}
  .dsearch{background:#0a0e15;border:1px solid #2b3647;border-radius:var(--r);color:var(--tx);
           padding:8px 12px;font-size:13px;font-family:inherit;min-width:170px}
  .dsearch:focus{outline:none;border-color:var(--gold-dim)}
  .dcounts{display:flex;gap:14px;font-size:12px;color:var(--tx2);align-items:center}
  .dcounts b{color:var(--tx);font-weight:700}
  .dcounts .ce{color:var(--dire)} .dcounts .ca{color:var(--rad)}
  .dgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(84px,1fr));gap:7px;margin-top:4px}
  .dh{position:relative;border-radius:5px;overflow:hidden;border:1px solid var(--line);background:#0a0e15;
      cursor:pointer;aspect-ratio:16/10;transition:transform .1s,border-color .1s}
  .dh img{width:100%;height:100%;object-fit:cover;display:block}
  .dh:hover{transform:translateY(-2px)}
  .dh .nm{position:absolute;left:0;right:0;bottom:0;font-size:9px;text-align:center;color:#fff;
          background:linear-gradient(transparent,rgba(0,0,0,.9));padding:9px 2px 2px;line-height:1.05;
          white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .dh .adv{position:absolute;top:3px;left:3px;font-size:10px;font-weight:700;padding:1px 4px;border-radius:3px;line-height:1.25}
  .dh .adv.good{background:rgba(46,160,67,.9);color:#fff} .dh .adv.bad{background:rgba(192,57,43,.9);color:#fff}
  /* gradacao de vantagem (counter) / desvantagem (counterado) */
  .dh.g1{border-color:#3a6f2c} .dh.g2{border-color:#5fae4a;box-shadow:0 0 0 1px rgba(126,201,79,.35)}
  .dh.g3{border-color:#7ec94f;box-shadow:0 0 0 2px rgba(126,201,79,.45)}
  .dh.b1{border-color:#7a3a2f} .dh.b2{border-color:#c0392b;box-shadow:0 0 0 1px rgba(226,74,59,.35)}
  /* marcacao (inimigo/aliado/ban) */
  .dh.mk-enemy{outline:2px solid var(--dire);outline-offset:-2px}
  .dh.mk-ally{outline:2px solid var(--rad);outline-offset:-2px}
  .dh.mk-ban{outline:2px solid #5a647a;outline-offset:-2px;filter:grayscale(1) brightness(.55)}
  .dh .mk{position:absolute;top:3px;right:3px;width:17px;height:17px;border-radius:4px;display:grid;
          place-items:center;font-size:10px;font-weight:800;color:#fff}
  .dh .mk.enemy{background:var(--dire)} .dh .mk.ally{background:var(--rad);color:#0a1606} .dh .mk.ban{background:#5a647a}
  .dsugg{display:flex;flex-direction:column;gap:8px}
  .dsugg .row{display:flex;align-items:center;gap:10px;padding:8px;border-radius:var(--r);background:#0b101a;
              border:1px solid var(--line-soft);border-left:3px solid var(--rad-dim)}
  .dsugg .row .port{width:48px;height:30px}
  .dsugg .row .nm{font-size:13px;font-weight:700;color:var(--tx);line-height:1.15}
  .dsugg .row .rs{font-size:10.5px;color:var(--tx3);line-height:1.25;margin-top:1px}
  .dsugg .row .pc{margin-left:auto;font-size:14px;font-weight:800;color:var(--rad);white-space:nowrap}
  .dhint{font-size:11.5px;color:var(--tx3);line-height:1.5;margin-top:10px}
</style>
</head>
<body>

<!-- Flash + aviso GLOBAL de captura do placar (aparece em qualquer aba) -->
<div id="scanflash" class="scanflash"></div>
<div id="scantoast" class="scantoast" title="Ver no Team Analysis" onclick="showView('teamanalysis')">
  <div class="st-ic"><span class="st-spin"></span><span class="st-emoji" id="st-emoji">📸</span></div>
  <div class="st-body"><div class="st-title" id="st-title">Capturando…</div><div class="st-sub" id="st-sub">lendo o placar</div></div>
  <img class="st-thumb" id="st-thumb" alt="" onerror="this.style.display='none'">
</div>

<div class="app">

  <header class="topbar">
    <div class="brand">
      <div class="logo">
        <svg viewBox="0 0 24 24" fill="none"><path d="M12 2l8 5v10l-8 5-8-5V7l8-5z" stroke="#e85a45" stroke-width="1.6"/><path d="M12 6.5l4.5 2.8v5.4L12 17.5l-4.5-2.8V9.3L12 6.5z" fill="#c0392b"/></svg>
      </div>
      <div class="bt"><b>DOTA 2 <span>COPILOT</span></b><small>POWERED BY TETEUPOWER</small></div>
    </div>

    <div class="livematch">
      <div class="lm-mode" id="lm-mode"><span class="live"><i></i> PARTIDA AO VIVO</span><div class="phase" id="lm-phase">aguardando</div></div>
      <div class="lm-core">
        <div class="ports" id="lm-allies"></div>
        <div class="lm-score">
          <div class="sc rad"><span id="lm-rad">–</span><small>ALIADOS</small></div>
          <div class="lm-clock" id="lm-clock">--:--</div>
          <div class="sc dire"><span id="lm-dire">–</span><small>INIMIGOS</small></div>
        </div>
        <div class="ports dire" id="lm-enemies"></div>
      </div>
    </div>

    <div class="topstat">
      <button class="btn voicebtn" id="voicebtn" title="Falar com o copiloto (atalho global configurável em Settings)">🎤 <span id="voicebtn-lbl">Falar</span></button>
      <button class="btn topbtn" id="ctxbtn" title="Limpar o contexto da partida (chat, draft, placar e relatórios) para começar um jogo novo do zero">🧹 <span>Novo jogo</span></button>
      <button class="btn topbtn danger" id="killbtn" title="Desligar a aplicação — encerra o servidor por completo (sem ficar fantasma no PC)">⏻ <span>Desligar</span></button>
      <div class="conn"><span class="dot" id="conn-dot"></span><span id="conn-text">conectando...</span></div>
    </div>
  </header>

  <!-- mostrado quando o usuário desliga a aplicação pelo botão -->
  <div class="killscreen" id="killscreen">
    <div class="kc-ico">⏻</div>
    <h2>APLICAÇÃO DESLIGADA</h2>
    <p>O servidor do copiloto foi encerrado. Pode fechar esta aba.<br>
       Para usar de novo, abra o <code>iniciar.bat</code>.</p>
  </div>

  <div class="layout">
    <aside class="sidebar">
      <nav class="nav" id="nav">
        <div class="nav-item active" data-view="dashboard"><svg viewBox="0 0 24 24" fill="currentColor"><rect x="3" y="3" width="8" height="8" rx="1.5"/><rect x="13" y="3" width="8" height="8" rx="1.5"/><rect x="3" y="13" width="8" height="8" rx="1.5"/><rect x="13" y="13" width="8" height="8" rx="1.5"/></svg>Dashboard</div>
        <div class="nav-item" data-view="draft"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M14.5 4l5.5 1-1 5.5"/><path d="M20 5L9 16"/><path d="M4 14.5l3.5 3.5"/><path d="M9.5 4L4 5l1 5.5"/><path d="M4 5l7 7"/><path d="M16 14.5L12.5 18"/></svg>Draft<span class="nav-live" id="draft-live">PICK</span></div>
        <div class="nav-item" data-view="gamestatus"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="2.5" fill="currentColor"/><path d="M12 3v3M12 18v3M3 12h3M18 12h3"/></svg>Game Status</div>
        <div class="nav-item" data-view="heroinsights"><svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 2c1.5 3.5 5 4.8 5 8.8a5 5 0 0 1-10 0c0-1.8 .8-2.9 1.8-3.9-.2 2 .9 3.1 2.7 3.3-2-2.8-1.4-5.4 .5-8.2z"/></svg>Hero Insights</div>
        <div class="nav-item" data-view="itemadvisor"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"><path d="M5 8h14l-1 12H6L5 8z"/><path d="M9 8a3 3 0 0 1 6 0"/></svg>Item Advisor</div>
        <div class="nav-item" data-view="teamanalysis"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><circle cx="9" cy="8" r="3"/><circle cx="17" cy="9" r="2.3"/><path d="M3.5 19c.5-3 2.8-4.5 5.5-4.5s5 1.5 5.5 4.5"/><path d="M16 14.6c2 .3 3.6 1.6 4 4.4"/></svg>Team Analysis</div>
        <div class="nav-item" data-view="strategy"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M6 21V4"/><path d="M6 4h11l-2.5 3.5L17 11H6"/></svg>Strategy</div>
        <div class="nav-item" data-view="replay"><svg viewBox="0 0 24 24" fill="currentColor"><path d="M11 6L4 12l7 6V6z"/><path d="M20 6l-7 6 7 6V6z"/></svg>Replay Analysis<span class="nav-soon">EM BREVE</span></div>
        <div class="nav-item" data-view="settings"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><circle cx="12" cy="12" r="3"/><path d="M19 12a7 7 0 0 0-.1-1.3l2-1.5-2-3.4-2.3 1a7 7 0 0 0-2.2-1.3L14 2h-4l-.4 2.2a7 7 0 0 0-2.2 1.3l-2.3-1-2 3.4 2 1.5A7 7 0 0 0 5 12c0 .4 0 .9.1 1.3l-2 1.5 2 3.4 2.3-1a7 7 0 0 0 2.2 1.3L10 22h4l.4-2.2a7 7 0 0 0 2.2-1.3l2.3 1 2-3.4-2-1.5c.1-.4.1-.9.1-1.3z"/></svg>Settings</div>
      </nav>

      <div class="side-foot">
        <div class="agentcard">
          <div class="av"><svg viewBox="0 0 24 24" fill="none"><path d="M12 2l8 5v10l-8 5-8-5V7l8-5z" stroke="#e85a45" stroke-width="1.4"/><circle cx="12" cy="11" r="3" fill="#e85a45"/></svg></div>
          <div><b id="agent-name">Copiloto</b><span><i></i> <span id="agent-prov">conectando</span></span></div>
        </div>
        <div class="sidenote">Analisando a partida em tempo real (GSI) para te dar os melhores insights. Pressione <b>Tab</b> + tecla para ler o placar.</div>
      </div>
    </aside>

    <main class="content">

      <!-- ============ DASHBOARD ============ -->
      <section class="view active" data-view="dashboard">
        <div class="dash">

          <!-- coluna herói -->
          <div class="col">
            <div class="panel">
              <h2 class="ptitle">Seu Herói<span class="grow"></span></h2>
              <div id="hero-card"><span class="empty">aguardando o jogo (GSI)...</span></div>
            </div>
          </div>

          <!-- coluna insights -->
          <div class="col">
            <div class="section-h">Insights do Copilot<span class="grow"></span></div>
            <div class="panel priority" id="insight-card">
              <div class="tag" id="insight-tag">⚠ análise tática</div>
              <div class="rep" id="insight-report"></div>
            </div>
            <div class="panel">
              <h2 class="ptitle">Ameaças Principais<span class="grow"></span></h2>
              <div class="threats" id="threats"><span class="empty">escaneie o placar para detectar os inimigos.</span></div>
            </div>
            <div class="panel">
              <h2 class="ptitle">Situação da Partida<span class="grow"></span></h2>
              <div class="sit" id="situation"></div>
            </div>
            <div class="panel quicktip">
              <h2 class="ptitle">Sugestão Rápida<span class="grow"></span></h2>
              <div class="body" id="quicktip">Conecte o GSI e escaneie o placar (Tab + tecla) — o copiloto monta a leitura tática da partida aqui.</div>
            </div>
          </div>

          <!-- coluna direita -->
          <div class="col rail">
           <div class="rail-inner">
            <div class="panel">
              <h2 class="ptitle">Inimigos<span class="grow"></span><span class="acc">fáceis no topo ↑</span></h2>
              <div id="enemy-list"><span class="empty">sem leitura do placar ainda.</span></div>
            </div>
            <div class="panel">
              <h2 class="ptitle">Placar de Abates<span class="grow"></span></h2>
              <div id="donut-box"><span class="empty">sem dados de abates.</span></div>
            </div>
            <div class="panel">
              <h2 class="ptitle">Minimapa<span class="grow"></span><span class="acc" id="mm-acc">ao vivo</span></h2>
              <div class="mini-map" id="mini-map">
                <img id="mini-thumb" alt="minimapa ao vivo">
                <div class="mm-hint" id="mm-hint"><b>MINIMAPA</b><span>entre numa partida pra ver ao vivo</span></div>
              </div>
              <button class="btn mm-open" onclick="openMinimap()">⛶ Abrir minimapa grande (2ª janela)</button>
            </div>
           </div>
          </div>

        </div>
      </section>

      <!-- ============ DRAFT (grid de picks + counters ao vivo) ============ -->
      <section class="view" data-view="draft">
        <div class="panel">
          <h2 class="ptitle">Assistente de Draft<span class="grow"></span><span class="acc">marque os picks · veja os counters</span></h2>
          <div class="toolbar">
            <label>Um toque marca:</label>
            <div class="dmode" id="dmode">
              <button data-role="enemy" class="on enemy">Inimigo</button>
              <button data-role="ally" class="ally">Aliado</button>
              <button data-role="ban" class="ban">Ban</button>
            </div>
            <input id="dsearch" class="dsearch" placeholder="🔎 buscar herói..." autocomplete="off">
            <span style="flex:1"></span>
            <button class="btn primary" id="dscan">📷 Copiar tela de picks</button>
            <button class="btn" id="dclear">limpar</button>
          </div>
          <div class="toolbar" style="margin-bottom:0">
            <span class="chip" id="dchip">marque os inimigos ou copie a tela</span>
            <div class="dcounts">
              <span class="ce">Inimigos <b id="dc-enemy">0</b></span>
              <span class="ca">Aliados <b id="dc-ally">0</b></span>
              <span>Bans <b id="dc-ban">0</b></span>
            </div>
            <img id="dthumb" alt="" style="height:46px;border-radius:5px;border:1px solid var(--line);display:none" onerror="this.style.display='none'">
          </div>
        </div>
        <div class="draft-wrap">
          <div class="panel">
            <h2 class="ptitle">Heróis<span class="grow"></span><span class="acc" id="dgrid-acc">ordenado por vantagem</span></h2>
            <div class="dgrid" id="dgrid"><span class="empty">carregando heróis...</span></div>
            <div class="dhint">Verde = você tem vantagem natural contra os inimigos marcados · Vermelho = você é counterado. Quanto mais marca inimigos, mais o grid se reordena pelos melhores picks.</div>
          </div>
          <div class="col">
            <div class="panel">
              <h2 class="ptitle">Melhores Picks<span class="grow"></span></h2>
              <div class="dsugg" id="dsugg"><span class="empty">marque ao menos um inimigo para ver os counters.</span></div>
            </div>
          </div>
        </div>
      </section>

      <!-- ============ GAME STATUS ============ -->
      <section class="view" data-view="gamestatus">
        <div class="stack">
          <div class="panel">
            <h2 class="ptitle">Partida ao Vivo (GSI)<span class="grow"></span><span class="acc" id="gs-acc"></span></h2>
            <div class="fields" id="gs-fields"><span class="empty">aguardando dados do Dota...</span></div>
          </div>
          <div class="panel">
            <h2 class="ptitle">JSON cru do GSI<span class="grow"></span></h2>
            <pre id="raw">-</pre>
          </div>
        </div>
      </section>

      <!-- ============ HERO INSIGHTS ============ -->
      <section class="view" data-view="heroinsights">
        <div class="grid2">
          <div class="panel">
            <h2 class="ptitle">Habilidades<span class="grow"></span></h2>
            <div id="hi-abilities"><span class="empty">aguardando o herói (GSI)...</span></div>
          </div>
          <div class="panel">
            <h2 class="ptitle">Itens Atuais<span class="grow"></span></h2>
            <div id="hi-items"><span class="empty">aguardando inventário (GSI)...</span></div>
          </div>
        </div>
      </section>

      <!-- ============ ITEM ADVISOR ============ -->
      <section class="view" data-view="itemadvisor">
        <div class="stack">
          <div class="panel">
            <h2 class="ptitle">Seus Itens Atuais<span class="grow"></span></h2>
            <div id="ia-items"><span class="empty">aguardando inventário (GSI)...</span></div>
          </div>
          <div class="panel">
            <h2 class="ptitle">Recomendação do Copiloto<span class="grow"></span></h2>
            <div class="report empty2" id="ia-report">escaneie o placar (Team Analysis) — o copiloto avalia seus itens e sugere os próximos contra o time inimigo.</div>
          </div>
        </div>
      </section>

      <!-- ============ TEAM ANALYSIS ============ -->
      <section class="view" data-view="teamanalysis">
        <div class="stack">
          <div class="panel">
            <h2 class="ptitle">Leitura do Placar por IA<span class="grow"></span></h2>
            <div class="toolbar">
              <label>Atalho:</label> <span class="kbd">Tab</span> +
              <select id="hksel">
                <option>f5</option><option>f6</option><option>f7</option><option>f8</option>
                <option>f9</option><option>f10</option><option>f11</option><option>f12</option>
              </select>
              <button class="btn primary js-scan">📷 Escanear agora</button>
              <span style="flex:1"></span>
              <span class="chip click js-voice">🔊 voz: off</span>
            </div>
            <div class="toolbar" style="margin-bottom:0">
              <span class="chip" id="sbchip">pronto pra escanear</span>
              <img id="thumb" alt="" onerror="this.style.display='none'">
            </div>
          </div>
          <div class="panel">
            <h2 class="ptitle">Times<span class="grow"></span></h2>
            <div class="teams" id="teams"><span class="empty">escaneie o placar para listar os times.</span></div>
          </div>
          <div class="panel">
            <h2 class="ptitle">Relatório Tático do Agente<span class="grow"></span></h2>
            <div class="report empty2" id="report">escaneie o placar (Tab + tecla) para o agente analisar a partida.</div>
          </div>
        </div>
      </section>

      <!-- ============ STRATEGY (chat) ============ -->
      <section class="view" data-view="strategy">
        <div class="panel" style="display:flex;flex-direction:column;min-height:calc(100vh - 150px)">
          <h2 class="ptitle">Conversar com o Copiloto<span class="grow"></span>
            <span class="chip click" id="clearchat" style="font-size:11px;padding:4px 11px">limpar</span></h2>
          <div class="voicehint" id="voice-hint">🎤 Aperte <b id="voice-hint-key">F6</b> a qualquer momento (inclusive dentro do jogo) para <b>falar por voz</b> — sua fala vira mensagem aqui e a resposta sai falada. Você também pode digitar abaixo.</div>
          <div id="chat">
            <div id="log"></div>
            <form id="chatform">
              <textarea id="chatinput" rows="1" placeholder="ex: o que compro agora contra esse time?"></textarea>
              <button type="button" id="micbtn" title="Falar">🎤</button>
              <button type="submit" id="chatsend">Enviar</button>
            </form>
          </div>
        </div>
      </section>

      <!-- ============ REPLAY (em breve) ============ -->
      <section class="view" data-view="replay">
        <div class="panel">
          <div class="soon-big">
            <svg class="ic" viewBox="0 0 24 24" fill="var(--tx3)"><path d="M11 6L4 12l7 6V6z"/><path d="M20 6l-7 6 7 6V6z"/></svg>
            <b>ANÁLISE DE REPLAY</b>
            <p>Em breve: revisão de partidas gravadas com timings, erros de posicionamento e sugestões do copiloto.</p>
          </div>
        </div>
      </section>

      <!-- ============ SETTINGS ============ -->
      <section class="view" data-view="settings">
        <div class="stack">
          <div class="grid2">
            <div class="panel">
              <h2 class="ptitle">Cérebro de IA<span class="grow"></span></h2>
              <div class="fields">
                <div class="field"><div class="l">Provedor ativo</div><div class="v" id="set-prov" style="font-size:15px">...</div></div>
                <div class="field"><div class="l">Conexão GSI</div><div class="v" id="set-conn" style="font-size:15px">...</div></div>
                <div class="field"><div class="l">Match ID</div><div class="v" id="set-match" style="font-size:15px">–</div></div>
              </div>
            </div>
            <div class="panel">
              <h2 class="ptitle">Voz do navegador<span class="grow"></span></h2>
              <div class="toolbar"><span class="chip click js-voice">🔊 voz: off</span></div>
              <div class="sidenote" style="border-style:solid">
                <b>Leitura do placar:</b> em <b>Team Analysis</b>, escolha a tecla e use <b>Tab + tecla</b> no jogo.<br><br>
                <b>Voz do navegador (grátis):</b> 🔊 lê as respostas · 🎤 (no chat) captura por voz via Web Speech (Chrome/Edge).
              </div>
            </div>
          </div>

          <div class="panel">
            <h2 class="ptitle">Voz do Copiloto — OpenAI (atalho “me ouvir”)<span class="grow"></span>
              <span class="acc" id="voice-status-acc">—</span></h2>
            <div class="cfg">
              <!-- Chave: uma só, vale pra ouvir E pra falar -->
              <div class="cfg-row">
                <label>Chave da OpenAI</label>
                <input type="password" id="vk-key" class="cfg-input" placeholder="sk-... (fica só no servidor, nunca aparece aqui)" autocomplete="off">
                <button class="btn primary" id="vk-save">Salvar chave</button>
                <button class="btn" id="vk-clear">Limpar</button>
                <span class="chip" id="vk-status">verificando...</span>
              </div>
              <div class="cfg-note">Uma única chave, usada tanto pra <b>ouvir</b> quanto pra <b>falar</b>. Pegue em <b>platform.openai.com/api-keys</b>.</div>

              <div class="cfg-group">⚡ MOTOR DO RELATÓRIO <small>— quem lê o placar e escreve a análise tática</small></div>
              <div class="cfg-row">
                <label>Motor</label>
                <select id="vc-report-engine" style="max-width:400px">
                  <option value="claude">Claude — preciso (lê certo), porém lento ~2 min · padrão</option>
                  <option value="openai">OpenAI — rápido ~10s, mas pode errar a leitura</option>
                </select>
                <span class="acc">vale pra ler o placar (Tab) e escrever o relatório</span>
              </div>

              <!-- ───────── GRUPO 1: SAÍDA (o que VOCÊ OUVE) ───────── -->
              <div class="cfg-group">🔊 O QUE VOCÊ OUVE <small>— a voz do copiloto te respondendo (sai no volume cheio do PC)</small></div>
              <div class="cfg-grid">
                <div class="cfg-cell"><label>O copiloto fala comigo?</label>
                  <select id="vc-engine"><option value="openai">Sim — voz da OpenAI</option><option value="off">Não, só texto</option></select></div>
                <div class="cfg-cell"><label>Voz do copiloto</label><select id="vc-voice"></select></div>
                <div class="cfg-cell"><label>Ler a análise tática em voz alta</label>
                  <select id="vc-speakreport"><option value="on">Sim, quando ficar pronta</option><option value="off">Não</option></select></div>
              </div>
              <div class="cfg-row">
                <label>Estilo da fala</label>
                <input type="text" id="vc-inst" class="cfg-input" placeholder="ex.: fale como um treinador empolgado e direto (opcional)">
                <button class="btn" id="vc-test">🔊 Testar voz agora</button>
              </div>

              <!-- ───────── GRUPO 2: ENTRADA (quando VOCÊ FALA) ───────── -->
              <div class="cfg-group">🎤 QUANDO VOCÊ FALA <small>— captar sua voz pelo MICROFONE DO PC (escolha abaixo), com um atalho que funciona a qualquer momento</small></div>
              <div class="cfg-grid">
                <div class="cfg-cell"><label>Microfone</label>
                  <select id="vc-mic"><option value="">Padrão do Windows</option></select></div>
                <div class="cfg-cell"><label>Tecla pra falar (a qualquer hora)</label>
                  <select id="vc-hotkey"><option>f5</option><option>f6</option><option>f7</option><option>f8</option><option>f9</option><option>f10</option><option>f11</option><option>f12</option></select></div>
                <div class="cfg-cell"><label>Bip ao começar a ouvir</label>
                  <select id="vc-beep"><option value="on">Sim</option><option value="off">Não</option></select></div>
                <div class="cfg-cell"><label>Abaixar os OUTROS apps enquanto ouve/fala</label>
                  <select id="vc-duck"><option value="off">Manter</option><option value="0.1">Abaixar p/ 10%</option><option value="0.2">Abaixar p/ 20%</option><option value="0.35">Abaixar p/ 35%</option><option value="0.5">Abaixar p/ 50%</option></select></div>
              </div>
              <div class="cfg-row">
                <label>Testar microfone</label>
                <button class="btn" id="vc-mictest">🎙️ Testar (fale e veja a barra)</button>
                <div class="meter" id="vc-meter"><i id="vc-meter-bar"></i></div>
                <span class="acc" id="vc-meter-txt" style="min-width:120px"></span>
                <span style="flex:1"></span>
                <button class="btn primary" id="vc-save">Salvar configurações</button>
              </div>

              <div class="sidenote" id="voice-help" style="border-style:solid">
                <b>Como funciona:</b> aperte a tecla <b id="voice-help-key">F8</b> <b>a qualquer momento</b> (inclusive dentro do jogo) → o copiloto usa o <b>microfone do PC</b> escolhido acima (por isso o navegador NÃO pede permissão), abaixa só os outros apps, <b>te ouve</b> e <b>responde falando</b>. Use <b>“Testar microfone”</b> e fale: se a barra subir, o mic está certo. (O <b>“Testar voz agora”</b> lá em cima só FALA uma frase.) Evite usar a mesma tecla do placar (Tab+F7).
              </div>
            </div>
          </div>
        </div>
      </section>

    </main>
  </div>
</div>

<script>
const $ = id => document.getElementById(id);
const $$ = sel => Array.from(document.querySelectorAll(sel));
const esc = t => (t||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
function fmt(t){ return esc(t).replace(/\\*\\*(.+?)\\*\\*/g,'<strong>$1</strong>'); }
const sum = (arr,k) => (arr||[]).reduce((n,r)=>n+(Number(r[k])||0),0);
const nv = x => (x===null||x===undefined||x==='') ? '–' : x;
const num = x => (x===null||x===undefined||x==='') ? '–' : (typeof x==='number' ? x.toLocaleString('pt-BR') : x);

// estado global compartilhado pelos dois pollers
let G={connected:false}, GAGE=null, RAW=null, S={};

// ---------- navegação (com rota por hash, deep-link + refresh) ----------
const VIEWS=['dashboard','draft','gamestatus','heroinsights','itemadvisor','teamanalysis','strategy','replay','settings'];
let curView='dashboard';
function showView(name){
  if(!VIEWS.includes(name)) name='dashboard';
  curView=name;
  $$('.view').forEach(v=>v.classList.toggle('active', v.dataset.view===name));
  $$('.nav-item').forEach(n=>n.classList.toggle('active', n.dataset.view===name));
  if(('#'+name)!==location.hash) history.replaceState(null,'','#'+name);
  if(name==='strategy'){ const i=$('chatinput'); if(i) setTimeout(()=>i.focus(),50); $('log')&&($('log').scrollTop=$('log').scrollHeight); }
  if(name==='draft') draftInit();
}
$$('.nav-item').forEach(n=>n.addEventListener('click',()=>showView(n.dataset.view)));
window.addEventListener('hashchange',()=>showView(location.hash.slice(1)));
showView(location.hash.slice(1)||'dashboard');

// ---------- helpers de render ----------
function port(img,name,cls){
  return `<div class="port ${cls||''}" title="${esc(name||'')}">`+
    (img?`<img src="${img}" onerror="this.style.display='none'">`:'')+`</div>`;
}
function abIcon(a){
  return `<div class="ab${a.ultimate?' ult':''}" title="${esc(a.name||'')}">`+
    (a.img?`<img src="${a.img}" onerror="this.style.display='none'">`:'')+
    (a.level?`<span class="lvl">${a.level}</span>`:'')+`</div>`;
}
function itemSlot(it){
  if(!it) return `<div class="islot empty"></div>`;
  return `<div class="islot" title="${esc(it.name||'')}">`+
    (it.img?`<img src="${it.img}" onerror="this.parentNode.classList.add('empty');this.remove()">`:'')+
    (it.charges?`<span class="chg">${it.charges}</span>`:'')+`</div>`;
}
function itemsGrid(items,slots){
  const arr=(items||[]).slice(); slots=slots||9;
  let out=''; for(let i=0;i<slots;i++) out+=itemSlot(arr[i]); return out;
}

// ---------- render principal ----------
function paint(){
  const fresh = GAGE!==null && GAGE<8;

  // topbar conexão
  $('conn-dot').className = 'dot'+(fresh?' live':'');
  $('conn-text').textContent = GAGE===null ? 'OFFLINE' : (fresh?'AO VIVO':'PARADO '+GAGE+'s');
  $('lm-mode').classList.toggle('on', fresh);
  $('lm-phase').textContent = (G.connected && G.game_state) ? G.game_state : (fresh?'em partida':'aguardando');
  $('lm-clock').textContent = G.clock || '--:--';

  // selo "PICK" no menu Draft durante a selecao de herois
  const inDraft = G.game_state_raw==='DOTA_GAMERULES_STATE_HERO_SELECTION'
               || G.game_state_raw==='DOTA_GAMERULES_STATE_STRATEGY_TIME';
  const dl=$('draft-live'); if(dl) dl.classList.toggle('on', !!(inDraft && fresh));

  // placar topo (vem do scoreboard)
  const allies=S.allies||[], enemies=S.enemies||[];
  $('lm-allies').innerHTML = allies.slice(0,5).map(r=>port(r.img,r.hero,'ally')).join('');
  $('lm-enemies').innerHTML = enemies.slice(0,5).map(r=>port(r.img,r.hero,'enemy')).join('');
  const ak=sum(allies,'k'), ek=sum(enemies,'k');
  $('lm-rad').textContent = (allies.length?ak:'–');
  $('lm-dire').textContent = (enemies.length?ek:'–');

  paintHero(); paintInsights(); paintEnemies(); paintGameStatus(); paintMinimap();
}

// ---------- minimapa ao vivo (thumbnail no dashboard) ----------
let mmStreaming=false;
function paintMinimap(){
  const fresh = GAGE!==null && GAGE<8;
  const box=$('mini-map'), img=$('mini-thumb');
  if(!box||!img) return;
  if(fresh && !mmStreaming){
    img.src='/minimap/stream'; mmStreaming=true; box.classList.add('live');
    img.onerror=()=>{ box.classList.remove('live'); mmStreaming=false; };
  } else if(!fresh && mmStreaming){
    img.src=''; mmStreaming=false; box.classList.remove('live');
  }
  $('mm-acc').textContent = fresh ? 'ao vivo' : 'aguardando';
}
function openMinimap(){
  window.open('/minimap','copiloto_minimapa',
    'width=820,height=900,menubar=no,toolbar=no,location=no,status=no');
}

function paintHero(){
  const box=$('hero-card');
  if(!G.connected){ box.innerHTML='<span class="empty">aguardando o jogo (GSI)...</span>'; }
  else{
    const kda=G.kda||[];
    box.innerHTML = `
      <div class="hero-portrait">
        ${G.hero_img?`<img src="${G.hero_img}" onerror="this.style.display='none'">`:''}
        <div class="ov"></div>
        <div class="lvl">${nv(G.level)}</div>
        <div class="nm"><b>${esc((G.hero||'Herói').toUpperCase())}</b><small>${G.alive===false?'morto':'em jogo'}</small></div>
      </div>
      <div class="bars">
        <div class="bar hp"><i style="width:${G.health_pct||0}%"></i></div>
        <div class="bar mp"><i style="width:${G.mana_pct||0}%"></i></div>
      </div>
      <div class="statgrid">
        <div class="stat"><div class="l">K / D / A</div><div class="v"><b>${nv(kda[0])}</b> / <i>${nv(kda[1])}</i> / <u>${nv(kda[2])}</u></div></div>
        <div class="stat"><div class="l">LH / DN</div><div class="v">${nv(G.last_hits)} / ${nv(G.denies)}</div></div>
        <div class="stat"><div class="l">Ouro</div><div class="v gold">${num(G.gold)}</div></div>
        <div class="stat"><div class="l">Net Worth</div><div class="v gold">${num(G.net_worth!=null?G.net_worth:G.gold)}</div></div>
      </div>
      <div class="subh">Habilidades</div>
      <div class="abilities">${(G.abilities&&G.abilities.length)?G.abilities.map(abIcon).join(''):'<span class="empty">—</span>'}</div>
      <div class="subh">Itens Atuais</div>
      <div class="items">${itemsGrid(G.items,9)}</div>`;
  }
  // hero insights view (espelho expandido)
  if(G.abilities) $('hi-abilities').innerHTML = G.abilities.length
    ? `<div class="abilities" style="flex-wrap:wrap;gap:9px">${G.abilities.map(abIcon).join('')}</div>` : '<span class="empty">sem habilidades no estado atual.</span>';
  $('hi-items').innerHTML = `<div class="items" style="grid-template-columns:repeat(3,52px);gap:8px">${itemsGrid(G.items,9)}</div>`;
  $('ia-items').innerHTML = (G.items&&G.items.length)
    ? `<div class="items" style="grid-template-columns:repeat(6,46px);gap:8px">${itemsGrid(G.items,Math.max(6,G.items.length))}</div>`
    : '<span class="empty">aguardando inventário (GSI)...</span>';
}

function paintInsights(){
  // relatório / prioridade
  const rep = S.report;
  if(rep){
    $('insight-tag').innerHTML = '⚠ ALTA PRIORIDADE';
    $('insight-report').innerHTML = fmt(rep);
  } else {
    $('insight-tag').innerHTML = '⚠ análise tática';
    $('insight-report').innerHTML = '<span class="empty">escaneie o placar (Tab + tecla) — a análise tática do copiloto aparece aqui.</span>';
  }
  $('ia-report').className = rep?'report':'report empty2';
  $('ia-report').innerHTML = rep?fmt(rep):'escaneie o placar (Team Analysis) — o copiloto avalia seus itens e sugere os próximos contra o time inimigo.';

  // ameaças = inimigos
  const enemies=S.enemies||[];
  $('threats').innerHTML = enemies.length
    ? enemies.map(r=>`<div class="port enemy" style="width:62px;height:38px" title="${esc(r.hero||'')}">${r.img?`<img src="${r.img}" onerror="this.style.display='none'">`:''}<span class="tnm">${esc(r.hero||'?')}</span></div>`).join('')
    : '<span class="empty">escaneie o placar para detectar os inimigos.</span>';

  // situação da partida
  const allies=S.allies||[];
  const ak=sum(allies,'k'), ek=sum(enemies,'k'), tot=ak+ek;
  const adv = tot? Math.round(ak/tot*100) : 50;
  const advCls = adv>55?'good':(adv<45?'bad':'');
  const nw = G.net_worth!=null?G.net_worth:G.gold;
  $('situation').innerHTML = `
    <div class="c"><div class="l">Fase</div><div class="v" style="font-size:13px">${G.connected?esc(G.game_state||'–'):'–'}</div></div>
    <div class="c"><div class="l">Relógio</div><div class="v g">${G.clock||'--:--'}</div><div class="mini">${G.connected?esc(G.daytime||''):''}</div></div>
    <div class="c"><div class="l">Vantagem em abates</div><div class="v ${advCls}">${tot?adv+'%':'–'}</div><div class="advbar"><i style="width:${tot?adv:50}%"></i></div></div>
    <div class="c"><div class="l">Patrimônio</div><div class="v g">${num(nw)}</div><div class="mini">GPM ${nv(G.gpm)}</div></div>`;

  // sugestão rápida
  if(rep){
    const first = rep.split(/(?<=[.!?])\\s+/).slice(0,2).join(' ');
    $('quicktip').innerHTML = fmt(first);
  }
}

function paintEnemies(){
  // ordena por FACILIDADE de matar agora (vantagem natural + forma atual): mais facil no topo
  const enemies=(S.enemies||[]).slice().sort((a,b)=>((b.ease??-9)-(a.ease??-9)));
  $('enemy-list').innerHTML = enemies.length ? enemies.map((r,i)=>{
    const pct=Math.round((r.adv||0)*100);
    const cls = pct>3?'adv-good':(pct<-3?'adv-bad':'adv-neu');
    const arrow = pct>3?'▲':(pct<-3?'▼':'▬');
    const badge=`<span class="advbadge ${cls}" title="vantagem natural do seu herói contra ele">${arrow} ${pct>0?'+':''}${pct}%</span>`;
    return `
    <div class="enemy-row">
      <span class="erank">${i+1}</span>
      ${port(r.img,r.hero,'enemy')}
      <div class="einfo"><div class="nm">${esc(r.hero||'?')}</div><div class="pl">${esc(r.player||'')}</div></div>
      <div class="eright">${badge}<div class="kda"><b>${nv(r.k)}</b>/<i>${nv(r.d)}</i>/<u>${nv(r.a)}</u></div></div>
    </div>`;}).join('') : '<span class="empty">sem leitura do placar ainda.</span>';

  // donut placar de abates
  const allies=S.allies||[];
  const ak=sum(allies,'k'), ek=sum(enemies,'k'), tot=ak+ek;
  const box=$('donut-box');
  if(!tot){ box.innerHTML='<span class="empty">sem dados de abates.</span>'; return; }
  const C=2*Math.PI*42, radLen=C*(ak/tot);
  box.innerHTML = `<div class="donut-wrap">
    <svg viewBox="0 0 110 110" class="donut">
      <circle cx="55" cy="55" r="42" fill="none" stroke="#1a2230" stroke-width="14"/>
      <circle cx="55" cy="55" r="42" fill="none" stroke="var(--dire)" stroke-width="14"/>
      <circle cx="55" cy="55" r="42" fill="none" stroke="var(--rad)" stroke-width="14" stroke-dasharray="${radLen} ${C}" transform="rotate(-90 55 55)"/>
      <text x="55" y="52" text-anchor="middle" class="d-num">${ak}–${ek}</text>
      <text x="55" y="67" text-anchor="middle" class="d-lbl">ABATES</text>
    </svg>
    <div class="legend">
      <div class="li"><span class="sw" style="background:var(--rad)"></span> Aliados <b>${ak}</b></div>
      <div class="li"><span class="sw" style="background:var(--dire)"></span> Inimigos <b>${ek}</b></div>
    </div></div>`;
}

function paintGameStatus(){
  $('gs-acc').textContent = GAGE===null?'sem dados':(GAGE<8?'ao vivo':'parado '+GAGE+'s');
  if(!G.connected){ $('gs-fields').innerHTML='<span class="empty">aguardando dados do Dota...</span>'; }
  else{
    const kda=G.kda||[];
    const f=(l,v)=>`<div class="field"><div class="l">${l}</div><div class="v">${nv(v)}</div></div>`;
    $('gs-fields').innerHTML =
      f('Fase',G.game_state)+f('Relógio',G.clock)+f('Período',G.daytime)+f('Herói',G.hero)+
      f('Nível',G.level)+f('Ouro',num(G.gold))+f('Net Worth',num(G.net_worth))+f('GPM',G.gpm)+f('XPM',G.xpm)+
      f('K/D/A',kda.some(x=>x!=null)?kda.map(x=>nv(x)).join(' / '):'–')+f('Last Hits',G.last_hits)+f('Denies',G.denies)+
      f('Dano herói',num(G.hero_damage))+f('Vida %',G.health_pct)+f('Mana %',G.mana_pct);
  }
  $('raw').textContent = RAW ? JSON.stringify(RAW,null,2) : '(nada ainda)';
  $('set-prov').textContent = PROVIDER_NAME || '...';
  $('set-conn').textContent = GAGE===null?'offline':(GAGE<8?'ao vivo':'parado '+GAGE+'s');
  $('set-match').textContent = (G.connected&&G.match_id)?G.match_id:'–';
}

// ---------- poll GSI ----------
async function tick(){
  try{
    const d = await (await fetch('/state')).json();
    G = d.summary || {connected:false};
    GAGE = d.seconds_since_update;
    RAW = d.raw;
  }catch(e){ G={connected:false}; GAGE=null; }
  paint();
}
setInterval(tick,1000); tick();

// ---------- poll Scoreboard ----------
let sbLastScan=0;
const CHIP={ idle:['','pronto pra escanear'], capturando:['work','capturando a tela...'],
  recebido:['go','📸 print recebido'], analisando:['work','🧠 Claude analisando o placar...'],
  pronto:['go','✅ leitura concluída'], erro:['err','erro ao ler'] };
function teamHtml(title,cls,rows){
  const body=(rows||[]).map(r=>`
    <div class="hero">${port(r.img,r.hero,cls)}
      <div><div class="nm">${esc(r.hero||'?')}</div><div class="pl">${esc(r.player||'')}</div></div>
      <div class="kda"><b>${nv(r.k)}</b>/<i>${nv(r.d)}</i>/<u>${nv(r.a)}</u></div>
    </div>`).join('');
  return `<div class="team ${cls}"><h3><i></i>${title}</h3>${body||'<span class="empty">—</span>'}</div>`;
}

// ---------- aviso GLOBAL de captura do placar (toast + flash, em qualquer aba) ----------
let prevScanStatus='idle', firstSB=true, toastHideTimer=null;
const ST_VIEW={
  capturando:{cls:'busy', emoji:'📸', t:'Capturando a tela…', s:'lendo o seu placar'},
  recebido:  {cls:'busy', emoji:'📸', t:'Print capturado', s:'enviando pra IA…'},
  analisando:{cls:'busy', emoji:'🧠', t:'Analisando o placar', s:'Claude lendo heróis e KDA…'},
  pronto:    {cls:'ok',   emoji:'✅', t:'Leitura concluída', s:'toque para ver o relatório'},
  erro:      {cls:'err',  emoji:'⚠️', t:'Erro ao ler o placar', s:'tente de novo (abra o Tab)'},
};
function flashScan(){ const f=$('scanflash'); if(!f) return; f.classList.remove('go'); void f.offsetWidth; f.classList.add('go'); }
function updateScanToast(d){
  const toast=$('scantoast'); if(!toast) return;
  const st=d.status||'idle';
  if(firstSB){ firstSB=false; prevScanStatus=st; return; } // nao mostra um toast "velho" no carregamento
  const active=['capturando','recebido','analisando'].includes(st);
  const wasActive=['capturando','recebido','analisando'].includes(prevScanStatus);
  if(active && !wasActive) flashScan(); // novo scan detectado: pisca a tela
  if(active || st==='pronto' || st==='erro'){
    const v=ST_VIEW[st]||ST_VIEW.capturando;
    toast.className='scantoast show '+v.cls;
    $('st-emoji').textContent=v.emoji;
    $('st-title').textContent=v.t;
    $('st-sub').textContent=(st==='erro'&&d.error)?('erro: '+d.error):v.s;
    const th=$('st-thumb');
    if(['recebido','analisando','pronto'].includes(st) && d.scanned_at){
      th.src='/scoreboard/image?t='+d.scanned_at; th.style.display='block'; toast.classList.add('has-thumb');
    } else toast.classList.remove('has-thumb');
    clearTimeout(toastHideTimer);
    if(st==='pronto'||st==='erro') toastHideTimer=setTimeout(()=>toast.classList.remove('show'), st==='erro'?5000:3800);
  }
  prevScanStatus=st;
}

async function pollSB(){
  try{
    const d = await (await fetch('/scoreboard/state')).json();
    if(!hkReady && d.hotkey){ $('hksel').value=d.hotkey; hkReady=true; }
    S = d;
    updateScanToast(d);
    const [c,txt]=CHIP[d.status]||CHIP.idle;
    $('sbchip').className='chip '+c;
    $('sbchip').innerHTML=(d.status==='capturando'||d.status==='analisando'?'<span class="spin"></span>':'')+(d.error?('erro: '+d.error):txt);
    const th=$('thumb');
    if(['recebido','analisando','pronto'].includes(d.status)){ th.src='/scoreboard/image?t='+d.scanned_at; th.style.display='block'; }
    if((d.allies&&d.allies.length)||(d.enemies&&d.enemies.length))
      $('teams').innerHTML=teamHtml('Seu time','ally',d.allies)+teamHtml('Inimigos','enemy',d.enemies);
    if(d.report){ $('report').className='report'; $('report').innerHTML=fmt(d.report); }
    if(d.scanned_at && d.scanned_at!==sbLastScan){
      sbLastScan=d.scanned_at;
      // o servidor ja fala a analise com a voz da OpenAI? entao o navegador NAO repete.
      const serverSpeaks = voiceCfg && voiceCfg.configured && voiceCfg.engine==='openai' && voiceCfg.speak_report;
      if(d.report && voiceOn && !serverSpeaks) speak(d.report);
    }
    paint();
  }catch(e){}
}
let hkReady=false;
setInterval(pollSB,800); pollSB();

// ---------- scan / hotkey (delegados por classe) ----------
async function doScan(btn){
  firstSB=false; updateScanToast({status:'capturando'}); // feedback visual imediato no clique
  const btns=$$('.js-scan'); btns.forEach(b=>b.disabled=true);
  try{ await fetch('/scoreboard/scan',{method:'POST'}); }catch(e){}
  btns.forEach(b=>b.disabled=false); pollSB();
}
$$('.js-scan').forEach(b=>b.addEventListener('click',()=>doScan(b)));
$('hksel').addEventListener('change', async ()=>{
  await fetch('/scoreboard/hotkey',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({key:$('hksel').value})});
});

// ---------- Aba Draft (grid de picks + counters ao vivo) ----------
let DHEROES=null;                          // lista estatica de /heroes
let DSTATE={enemy:[],allies:[],bans:[]};   // marcacoes (servidor = fonte da verdade)
let DGRID={};                              // hero_id -> {counter_score, reasons, ...}
let DSUGG=[];                              // top counters (com motivos)
let dMode='enemy', dSearch='', dEditing=false, dPollTimer=null, dInited=false;

async function draftInit(){
  if(!dInited){
    dInited=true;
    try{ const d=await (await fetch('/heroes')).json(); DHEROES=d.heroes||[]; }
    catch(e){ DHEROES=[]; }
  }
  draftRefresh();
  if(!dPollTimer) dPollTimer=setInterval(()=>{ if(curView==='draft') draftRefresh(); }, 1400);
}

function applyGrid(gr){
  DGRID={}; (gr.suggestions||[]).forEach(s=>{ DGRID[s.hero_id]=s; });
  DSUGG = DSTATE.enemy.length ? (gr.suggestions||[]).filter(s=>s.counter_score>0).slice(0,8) : [];
}

async function draftRefresh(){
  try{
    const [st,gr,sc]=await Promise.all([
      fetch('/draft/state').then(r=>r.json()),
      fetch('/draft/grid').then(r=>r.json()),
      fetch('/draft/scan/state').then(r=>r.json()),
    ]);
    if(!dEditing){ DSTATE={enemy:st.enemy||[],allies:st.allies||[],bans:st.bans||[]}; }
    applyGrid(gr);
    updateDScan(sc);
    paintDraft();
  }catch(e){}
}

function updateDScan(sc){
  const chip=$('dchip'); if(!chip) return;
  const M={ idle:['','marque os inimigos ou copie a tela'],
            capturando:['work','capturando a tela...'],
            analisando:['work','Claude lendo os picks...'],
            pronto:['go','✅ picks preenchidos'],
            erro:['err','⚠️ '+(sc.error||'erro ao ler')] };
  const [c,txt]=M[sc.status]||M.idle;
  chip.className='chip '+c;
  chip.innerHTML=(sc.status==='capturando'||sc.status==='analisando'?'<span class="spin"></span> ':'')+esc(txt);
  const th=$('dthumb');
  if(th && ['analisando','pronto'].includes(sc.status) && sc.scanned_at){
    th.src='/draft/scan/image?t='+sc.scanned_at; th.style.display='block';
  }
}

function advClass(v){
  if(v>=4) return 'g3'; if(v>=2) return 'g2'; if(v>=0.6) return 'g1';
  if(v<=-4) return 'b2'; if(v<=-1) return 'b1'; return '';
}
function roleOf(id){
  if(DSTATE.enemy.includes(id)) return 'enemy';
  if(DSTATE.allies.includes(id)) return 'ally';
  if(DSTATE.bans.includes(id)) return 'ban';
  return null;
}

function paintDraft(){
  if(!$('dgrid')) return;
  $('dc-enemy').textContent=DSTATE.enemy.length;
  $('dc-ally').textContent=DSTATE.allies.length;
  $('dc-ban').textContent=DSTATE.bans.length;
  const hasEnemy=DSTATE.enemy.length>0;
  $('dgrid-acc').textContent=hasEnemy?'ordenado por vantagem':'ordenado por atributo';

  const box=$('dgrid');
  if(!DHEROES||!DHEROES.length){
    box.innerHTML='<span class="empty">cache de heróis indisponível (rode build_cache.py).</span>';
  } else {
    const q=dSearch.trim().toLowerCase();
    const rank={enemy:0,ally:1,ban:2};
    const list=DHEROES.filter(h=>!q || (h.name||'').toLowerCase().includes(q)).slice();
    list.sort((a,b)=>{
      const ra=roleOf(a.id), rb=roleOf(b.id);
      if(ra&&rb) return rank[ra]-rank[rb];
      if(ra&&!rb) return -1;
      if(rb&&!ra) return 1;
      if(hasEnemy){
        const va=DGRID[a.id]?DGRID[a.id].counter_score:0, vb=DGRID[b.id]?DGRID[b.id].counter_score:0;
        return vb-va;
      }
      return (a.name||'').localeCompare(b.name||'');
    });
    box.innerHTML=list.map(h=>{
      const role=roleOf(h.id), sc=DGRID[h.id];
      const v=(sc&&hasEnemy)?sc.counter_score:null;
      const cls=role?('mk-'+role):(v!=null?advClass(v):'');
      const badge=(v!=null&&Math.abs(v)>=0.6)?`<span class="adv ${v>0?'good':'bad'}">${v>0?'+':''}${Math.round(v)}</span>`:'';
      const mk=role?`<span class="mk ${role}">${role==='enemy'?'I':role==='ally'?'A':'B'}</span>`:'';
      return `<div class="dh ${cls}" data-id="${h.id}" title="${esc(h.name||'')}">`+
        (h.img?`<img src="${h.img}" onerror="this.style.display='none'">`:'')+
        badge+mk+`<span class="nm">${esc(h.name||'')}</span></div>`;
    }).join('');
  }

  const sg=$('dsugg');
  if(!hasEnemy){ sg.innerHTML='<span class="empty">marque ao menos um inimigo para ver os counters.</span>'; }
  else if(!DSUGG.length){ sg.innerHTML='<span class="empty">sem counter claro contra esse time ainda.</span>'; }
  else{
    sg.innerHTML=DSUGG.map(s=>`
      <div class="row">
        ${port(s.img,s.name,'ally')}
        <div><div class="nm">${esc(s.name||'')}</div><div class="rs">${esc((s.reasons||[]).slice(0,2).join(' · ')||'bom contra o time inimigo')}</div></div>
        <div class="pc">+${Math.round(s.counter_score)}</div>
      </div>`).join('');
  }
}

async function draftTap(id){
  const key=dMode==='enemy'?'enemy':(dMode==='ally'?'allies':'bans');
  const had=DSTATE[key].includes(id);
  ['enemy','allies','bans'].forEach(k=>{ DSTATE[k]=DSTATE[k].filter(x=>x!==id); });
  if(!had) DSTATE[key].push(id);   // re-tocar no mesmo papel desmarca
  dEditing=true; paintDraft();
  try{
    await fetch('/draft/state',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({enemy:DSTATE.enemy,allies:DSTATE.allies,bans:DSTATE.bans})});
    const gr=await (await fetch('/draft/grid')).json();
    applyGrid(gr); paintDraft();
  }catch(e){}
  dEditing=false;
}

$('dgrid').addEventListener('click',e=>{ const c=e.target.closest('.dh'); if(c) draftTap(+c.dataset.id); });
$('dmode').addEventListener('click',e=>{ const b=e.target.closest('button'); if(!b) return;
  dMode=b.dataset.role;
  $$('#dmode button').forEach(x=>x.classList.toggle('on', x.dataset.role===dMode));
});
$('dsearch').addEventListener('input',e=>{ dSearch=e.target.value; paintDraft(); });
$('dscan').addEventListener('click',async()=>{
  const b=$('dscan'); b.disabled=true; updateDScan({status:'capturando'});
  try{ await fetch('/draft/scan',{method:'POST'}); }catch(e){}
  b.disabled=false; draftRefresh();
});
$('dclear').addEventListener('click',async()=>{
  DSTATE={enemy:[],allies:[],bans:[]}; DSUGG=[]; paintDraft();
  try{ await fetch('/draft/clear',{method:'POST'}); }catch(e){}
  draftRefresh();
});

// ---------- chat ----------
let PROVIDER_NAME='';
const log=$('log'), input=$('chatinput'), form=$('chatform'), sendBtn=$('chatsend');
function addMsg(role,text){
  const div=document.createElement('div');
  div.className='msg '+(role==='user'?'user':'bot');
  div.innerHTML=fmt(text); log.appendChild(div); log.scrollTop=log.scrollHeight; return div;
}
async function loadHistory(){
  try{
    const d=await (await fetch('/chat/history')).json();
    PROVIDER_NAME=d.provider||'?';
    $('agent-prov').textContent=PROVIDER_NAME; $('set-prov').textContent=PROVIDER_NAME;
    log.innerHTML='';
    (d.history||[]).forEach(m=>addMsg(m.role,m.content));
    if(!(d.history||[]).length) addMsg('bot','Escaneie o placar e me pergunte o que fazer. Posso falar sobre itens, ameaças e jogadas.');
  }catch(e){}
}
form.addEventListener('submit', async (e)=>{
  e.preventDefault();
  const msg=input.value.trim(); if(!msg) return;
  addMsg('user',msg); input.value=''; input.style.height='auto'; sendBtn.disabled=true;
  const thinking=addMsg('bot','...');
  try{
    const d=await (await fetch('/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:msg})})).json();
    thinking.innerHTML=fmt(d.reply||d.error||'(sem resposta)'); speak(d.reply||'');
  }catch(e){ thinking.textContent='erro de conexão.'; }
  log.scrollTop=log.scrollHeight; sendBtn.disabled=false; input.focus();
});
input.addEventListener('input',()=>{ input.style.height='auto'; input.style.height=Math.min(input.scrollHeight,120)+'px'; });
input.addEventListener('keydown',(e)=>{ if(e.key==='Enter'&&!e.shiftKey){ e.preventDefault(); form.requestSubmit(); } });
$('clearchat').addEventListener('click', async ()=>{ await fetch('/chat/reset',{method:'POST'}); loadHistory(); });
loadHistory();

// ---------- voz ----------
const ttsOk='speechSynthesis' in window;
const RecCtor=window.SpeechRecognition||window.webkitSpeechRecognition;
let voiceOn=false;
function ptVoice(){ const vs=speechSynthesis.getVoices(); return vs.find(v=>v.lang&&v.lang.toLowerCase().startsWith('pt'))||vs.find(v=>v.default)||vs[0]; }
function speak(text){
  if(!voiceOn||!ttsOk||!text) return;
  speechSynthesis.cancel();
  const clean=text.replace(/\\*\\*/g,'').replace(/[#>*_`]/g,'').replace(/\\s+/g,' ').trim();
  (clean.match(/[^.!?\\n]+[.!?]?/g)||[clean]).forEach(p=>{
    const u=new SpeechSynthesisUtterance(p.trim()); const v=ptVoice(); if(v) u.voice=v;
    u.lang='pt-BR'; u.rate=1.05; speechSynthesis.speak(u);
  });
}
function setVoiceLabels(){ $$('.js-voice').forEach(el=>el.textContent=voiceOn?'🔊 voz: on':'🔊 voz: off'); }
$$('.js-voice').forEach(el=>el.addEventListener('click',()=>{
  if(!ttsOk){ el.textContent='voz indisponível'; return; }
  voiceOn=!voiceOn; setVoiceLabels();
  if(voiceOn){ speechSynthesis.getVoices(); speak('Voz ligada.'); } else speechSynthesis.cancel();
}));
const micbtn=$('micbtn'); let recording=false, rec=null;
if(RecCtor && micbtn){
  rec=new RecCtor(); rec.lang='pt-BR'; rec.interimResults=false; rec.maxAlternatives=1;
  rec.onresult=(e)=>{ input.value=e.results[0][0].transcript; form.requestSubmit(); };
  rec.onend=()=>{ recording=false; micbtn.classList.remove('rec'); };
  rec.onerror=()=>{ recording=false; micbtn.classList.remove('rec'); };
  micbtn.addEventListener('click',()=>{ if(recording){ try{rec.stop();}catch(e){} return; } try{rec.start(); recording=true; micbtn.classList.add('rec'); }catch(e){} });
} else if(micbtn) micbtn.style.display='none';

// ---------- voz OpenAI (atalho "me ouvir": ouve -> transcreve -> responde -> fala) ----------
const VKEY=$('vk-key'), VSTATUS=$('vk-status'), VACC=$('voice-status-acc'), VBTN=$('voicebtn');
let voiceCfg=null;
async function loadVoiceConfig(){
  try{
    const c=await (await fetch('/voice/config')).json(); voiceCfg=c;
    if(VSTATUS){ VSTATUS.className='chip '+(c.configured?'go':'err'); VSTATUS.textContent=c.configured?'✓ chave configurada':'⚠ sem chave'; }
    const vs=$('vc-voice'); if(vs){ if(!vs.options.length) vs.innerHTML=(c.voices||[]).map(v=>`<option value="${v}">${v}</option>`).join(''); vs.value=c.voice||'coral'; }
    if($('vc-engine')) $('vc-engine').value=c.engine||'openai';
    if($('vc-hotkey')) $('vc-hotkey').value=c.hotkey||'f8';
    if($('vc-inst')) $('vc-inst').value=c.instructions||'';
    if($('vc-duck')) $('vc-duck').value=c.duck?String(c.duck_level):'off';
    if($('vc-beep')) $('vc-beep').value=c.beep?'on':'off';
    if($('vc-speakreport')) $('vc-speakreport').value=c.speak_report?'on':'off';
    if($('vc-report-engine')) $('vc-report-engine').value=c.report_engine||'claude';
    const md=$('vc-mic');
    if(md){
      md.innerHTML='<option value="">Padrão do Windows</option>'+(c.devices||[]).map(d=>`<option value="${d.index}">${esc(d.name)}</option>`).join('');
      md.value=(c.mic_index===null||c.mic_index===undefined)?'':String(c.mic_index);
    }
    const hk=(c.hotkey||'f8').toUpperCase();
    if($('voice-help-key')) $('voice-help-key').textContent=hk;
    if($('voice-hint-key')) $('voice-hint-key').textContent=hk;   // aviso no Strategy
    if(VBTN) VBTN.title='Falar com o copiloto — atalho global '+hk;
    let warn='';
    if(!c.audio_ok) warn+=' Microfone/gravação indisponível (rode: pip install sounddevice).';
    if(!c.volume_ok) warn+=' Controle de volume indisponível (rode: pip install pycaw comtypes).';
    const h=$('voice-help'); if(h && warn && !h.dataset.warned){ h.dataset.warned='1'; h.innerHTML+='<br><b style="color:var(--warn)">Atenção:</b>'+warn; }
  }catch(e){}
}
async function saveVoiceCfg(extra){
  const duck=$('vc-duck')?$('vc-duck').value:'0.2';
  const body={engine:$('vc-engine')?$('vc-engine').value:'openai', voice:$('vc-voice')?$('vc-voice').value:'coral',
    hotkey:$('vc-hotkey')?$('vc-hotkey').value:'f8', instructions:$('vc-inst')?$('vc-inst').value:'',
    duck: duck!=='off', duck_level: duck==='off'?0.2:parseFloat(duck),
    beep: $('vc-beep') ? $('vc-beep').value==='on' : true,
    speak_report: $('vc-speakreport') ? $('vc-speakreport').value==='on' : true,
    report_engine: $('vc-report-engine') ? $('vc-report-engine').value : 'claude'};
  const micEl=$('vc-mic');
  if(micEl){
    const idx = micEl.value!=='' ? parseInt(micEl.value) : null;
    body.mic_index = idx;
    body.mic_name = (idx===null || micEl.selectedIndex<0) ? '' : micEl.options[micEl.selectedIndex].text;
  }
  if(extra) Object.assign(body, extra);
  try{ await fetch('/voice/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}); }catch(e){}
  loadVoiceConfig();
}
if($('vk-save')) $('vk-save').addEventListener('click', async ()=>{ const k=VKEY.value.trim(); if(!k) return; VSTATUS.textContent='salvando...'; await saveVoiceCfg({key:k}); VKEY.value=''; });
if($('vk-clear')) $('vk-clear').addEventListener('click', async ()=>{ await saveVoiceCfg({clear:true}); if(VKEY) VKEY.value=''; });
if($('vc-save')) $('vc-save').addEventListener('click', ()=>saveVoiceCfg());
if($('vc-test')) $('vc-test').addEventListener('click', testVoice);
async function testVoice(){
  if(voiceCfg && !voiceCfg.configured){ showView('settings'); if(VSTATUS) VSTATUS.textContent='configure a chave primeiro'; return; }
  try{ await fetch('/voice/test',{method:'POST'}); }catch(e){}
}
async function triggerListen(){
  if(voiceCfg && !voiceCfg.configured){ showView('settings'); if(VSTATUS) VSTATUS.textContent='configure a chave primeiro'; return; }
  try{ await fetch('/voice/listen',{method:'POST'}); }catch(e){}
}
if(VBTN) VBTN.addEventListener('click', triggerListen);

// ---------- medidor do microfone (Settings): fale e veja a barra subir ----------
let micTestTimer=null, micPolling=false, micTestStart=0;
function stopMicTest(){
  if(micTestTimer){ clearInterval(micTestTimer); micTestTimer=null; }
  const m=$('vc-meter'), b=$('vc-meter-bar'), t=$('vc-mictest');
  if(m) m.classList.remove('live'); if(b) b.style.width='0%';
  if(t) t.textContent='🎙️ Testar (fale e veja a barra)';
}
if($('vc-mictest')) $('vc-mictest').addEventListener('click', ()=>{
  if(micTestTimer){ stopMicTest(); if($('vc-meter-txt')) $('vc-meter-txt').textContent=''; return; }
  const dev = $('vc-mic') ? $('vc-mic').value : '';
  $('vc-mictest').textContent='⏹️ Parar';
  $('vc-meter').classList.add('live');
  micTestStart=tnow();
  micTestTimer=setInterval(async ()=>{
    if(tnow()-micTestStart>15000){ stopMicTest(); if($('vc-meter-txt')) $('vc-meter-txt').textContent='(teste encerrou)'; return; }
    if(micPolling) return; micPolling=true;
    try{
      const q = dev!=='' ? ('?device='+encodeURIComponent(dev)) : '';
      const r = await (await fetch('/voice/miclevel'+q)).json();
      const lvl = r.level;
      if(lvl<0){ $('vc-meter-bar').style.width='0%'; $('vc-meter-txt').textContent='✕ esse mic não funciona — escolha outro'; }
      else { $('vc-meter-bar').style.width=Math.min(100,lvl)+'%'; $('vc-meter-txt').textContent = lvl>8 ? ('✓ captando ('+lvl+')') : 'fale algo... ('+lvl+')'; }
    }catch(e){}
    micPolling=false;
  }, 180);
});
function tnow(){ return (window.performance && performance.now) ? performance.now() : (+new Date()); }
// sair do Settings para o teste do mic
$$('.nav-item').forEach(n=>n.addEventListener('click', ()=>{ if(n.dataset.view!=='settings') stopMicTest(); }));

const VOICE_LBL={ouvindo:'Ouvindo…', transcrevendo:'Transcrevendo…', pensando:'Pensando…', falando:'Falando…', erro:'Erro', idle:'Falar'};
let voiceLastAt=0, prevVoiceStatus='idle';
async function pollVoice(){
  try{
    const s=await (await fetch('/voice/state')).json();
    const st=s.status||'idle';
    // ao COMECAR a falar (atalho/botao), abre o Strategy pra ver o texto + resposta lá
    if(st==='ouvindo' && prevVoiceStatus!=='ouvindo') showView('strategy');
    prevVoiceStatus=st;
    if(VBTN){
      $('voicebtn-lbl').textContent=VOICE_LBL[st]||'Falar';
      VBTN.classList.toggle('rec', st==='ouvindo');
      VBTN.classList.toggle('busy', ['transcrevendo','pensando','falando'].includes(st));
    }
    if(VACC) VACC.textContent = s.error ? ('erro: '+s.error) : (st==='idle' ? 'pronto' : st);
    // a fala transcrita e a resposta aparecem no chat do Strategy (mesma conversa do teclado)
    if(s.at && s.at!==voiceLastAt){ voiceLastAt=s.at; if(s.reply || s.transcript) loadHistory(); }
  }catch(e){}
}
setInterval(pollVoice, 1000); pollVoice();
loadVoiceConfig();

// ---------- limpar contexto (novo jogo) / desligar aplicação ----------
$('ctxbtn').addEventListener('click', async ()=>{
  if(!confirm('Limpar o contexto da partida?\\n\\nIsso apaga a conversa, o draft, o placar lido e os relatórios para começar um jogo novo do zero. O servidor continua ligado.')) return;
  const btn=$('ctxbtn'); btn.disabled=true;
  try{ await fetch('/context/clear',{method:'POST'}); }catch(e){}
  // limpa o que está na tela agora (os pollers re-populam sozinhos na próxima partida)
  DSTATE={enemy:[],allies:[],bans:[]}; DSUGG=[];
  if(typeof paintDraft==='function') paintDraft();
  S={};
  const tm=$('teams'); if(tm) tm.innerHTML='<span class="empty">escaneie o placar para listar os times.</span>';
  const rp=$('report'); if(rp){ rp.className='report empty2'; rp.textContent='escaneie o placar (Tab + tecla) para o agente analisar a partida.'; }
  if(typeof loadHistory==='function') loadHistory();
  if(typeof draftRefresh==='function') draftRefresh();
  btn.disabled=false;
});

$('killbtn').addEventListener('click', async ()=>{
  if(!confirm('Desligar a aplicação?\\n\\nO servidor do copiloto será encerrado por completo. Para usar de novo, abra o iniciar.bat.')) return;
  try{ await fetch('/shutdown',{method:'POST'}); }catch(e){}
  $('killscreen').classList.add('on');   // o servidor cai logo após responder
});
</script>
</body>
</html>
"""


MINIMAP_HTML = """<!doctype html>
<html lang="pt-br">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Minimapa ao vivo - Copiloto Dota 2</title>
<style>
  :root{--bg:#05070b;--line:#212a39;--gold:#c8aa6e;--gold-hi:#f1d191;--tx:#e9eef6;
        --tx2:#93a0b4;--tx3:#586272;--ok:#48c569;--red:#e85a45;--r:6px;color-scheme:dark}
  *{box-sizing:border-box}
  html,body{height:100%;margin:0}
  body{background:var(--bg);color:var(--tx);font-family:Rajdhani,system-ui,'Segoe UI',sans-serif;
       overflow:hidden;display:flex;flex-direction:column}
  .bar{flex:none;display:flex;align-items:center;gap:12px;padding:8px 12px;
       background:linear-gradient(180deg,#11161f,#0b0f17);border-bottom:1px solid var(--line)}
  .bar .t{font-weight:700;letter-spacing:1.5px;text-transform:uppercase;font-size:12px;color:var(--gold)}
  .bar .live{display:inline-flex;align-items:center;gap:6px;font-size:11px;font-weight:700;letter-spacing:1px;color:var(--tx2)}
  .bar .live i{width:8px;height:8px;border-radius:50%;background:#555;display:inline-block}
  .bar .live.on i{background:var(--ok);box-shadow:0 0 8px var(--ok);animation:pulse 1.6s infinite}
  @keyframes pulse{50%{opacity:.4}}
  .bar .clock{font-weight:700;font-size:15px;color:var(--gold-hi);min-width:54px;text-align:center}
  .bar .sp{flex:1}
  .gbtn{background:#141b27;border:1px solid #2b3647;color:var(--tx);border-radius:var(--r);
        padding:6px 11px;font-size:14px;cursor:pointer;font-family:inherit}
  .gbtn:hover{border-color:#3f4f68}
  .stage{flex:1;position:relative;display:grid;place-items:center;min-height:0;padding:10px}
  .wrap{position:relative;width:min(96vmin,96vh);max-width:100%;max-height:100%;aspect-ratio:1/1}
  #map{width:100%;height:100%;object-fit:contain;display:block;border-radius:var(--r);
       border:1px solid var(--line);background:#05070b;box-shadow:0 0 40px rgba(0,0,0,.6)}
  .hint{position:absolute;inset:0;display:grid;place-items:center;text-align:center;gap:6px;pointer-events:none}
  .hint b{font-size:16px;letter-spacing:2px;color:var(--tx2)}
  .hint span{font-size:12px;color:var(--tx3)}
  body.live .hint{display:none}
  /* painel de calibracao */
  .cal{position:absolute;top:10px;right:10px;width:230px;background:rgba(11,16,26,.96);
       border:1px solid var(--line);border-radius:var(--r);padding:12px;display:none;font-size:12px}
  .cal.open{display:block}
  .cal h4{margin:0 0 8px;font-size:11px;letter-spacing:1px;text-transform:uppercase;color:var(--gold)}
  .cal .row{display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-bottom:6px}
  .cal label{display:flex;flex-direction:column;gap:2px;color:var(--tx3);font-size:10px;text-transform:uppercase}
  .cal input{background:#0a0e15;border:1px solid #2b3647;color:var(--tx);border-radius:4px;padding:5px;font-size:12px;font-family:inherit}
  .pad{display:grid;grid-template-columns:repeat(3,1fr);gap:5px;margin:8px 0}
  .pad button,.zoom button{background:#141b27;border:1px solid #2b3647;color:var(--tx);border-radius:4px;
                           padding:7px;cursor:pointer;font-size:13px;font-family:inherit}
  .pad button:hover,.zoom button:hover{border-color:#3f4f68}
  .pad .e{visibility:hidden}
  .zoom{display:grid;grid-template-columns:1fr 1fr;gap:5px}
  .cal .note{color:var(--tx3);font-size:10.5px;line-height:1.4;margin-top:8px}
</style>
</head>
<body>
  <div class="bar">
    <span class="t">Minimapa</span>
    <span class="live" id="live"><i></i> <span id="livetxt">aguardando</span></span>
    <span class="sp"></span>
    <span class="clock" id="clock">--:--</span>
    <button class="gbtn" id="reload" title="Recarregar">↻</button>
    <button class="gbtn" id="gear" title="Ajustar recorte">⚙</button>
  </div>
  <div class="stage">
    <div class="wrap">
      <img id="map" alt="minimapa ao vivo">
      <div class="hint"><b>MINIMAPA</b><span>abra o Dota e entre numa partida</span></div>
      <div class="cal" id="cal">
        <h4>Ajustar recorte (px)</h4>
        <div class="row">
          <label>Esquerda<input type="number" id="i-left"></label>
          <label>Topo<input type="number" id="i-top"></label>
          <label>Direita<input type="number" id="i-right"></label>
          <label>Baixo<input type="number" id="i-bottom"></label>
        </div>
        <div class="pad">
          <span class="e"></span><button data-mv="0,-4">▲</button><span class="e"></span>
          <button data-mv="-4,0">◀</button><button data-mv="0,4">▼</button><button data-mv="4,0">▶</button>
        </div>
        <div class="zoom">
          <button data-zoom="-4">− menor</button><button data-zoom="4">+ maior</button>
        </div>
        <div class="note">Mova/ajuste até o quadro mostrar só o minimapa. Salva automático. Setas do teclado também movem.</div>
      </div>
    </div>
  </div>
<script>
const $=id=>document.getElementById(id);
const map=$('map');

// ---- stream com auto-reconexao ----
function startStream(){ map.src='/minimap/stream?t='+Date.now(); }
map.onerror=()=>{ document.body.classList.remove('live'); setTimeout(startStream,1200); };
$('reload').onclick=startStream;
startStream();

// ---- status (clock + ao vivo) ----
async function tick(){
  try{
    const d=await (await fetch('/state')).json();
    const age=d.seconds_since_update, fresh=age!==null&&age<8;
    const s=d.summary||{};
    $('clock').textContent=s.clock||'--:--';
    $('live').classList.toggle('on',fresh);
    $('livetxt').textContent=fresh?'ao vivo':(age===null?'offline':'parado');
    document.body.classList.toggle('live',fresh);
  }catch(e){ $('live').classList.remove('on'); $('livetxt').textContent='offline'; }
}
setInterval(tick,1000); tick();

// ---- calibracao do recorte ----
let box={left:0,top:0,right:0,bottom:0};
const ins={left:$('i-left'),top:$('i-top'),right:$('i-right'),bottom:$('i-bottom')};
function fill(){ for(const k in ins) ins[k].value=box[k]; }
async function loadBox(){ try{ box=await (await fetch('/minimap/box')).json(); fill(); }catch(e){} }
async function saveBox(){
  try{
    const r=await (await fetch('/minimap/box',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify(box)})).json();
    if(r&&r.left!==undefined){ box={left:r.left,top:r.top,right:r.right,bottom:r.bottom}; fill(); }
  }catch(e){}
}
function move(dx,dy){ box.left+=dx;box.right+=dx;box.top+=dy;box.bottom+=dy; fill(); saveBox(); }
function zoom(d){ box.left-=d;box.top-=d;box.right+=d;box.bottom+=d; fill(); saveBox(); }
for(const k in ins) ins[k].addEventListener('change',()=>{ box[k]=parseInt(ins[k].value)||0; saveBox(); });
document.querySelectorAll('.pad button[data-mv]').forEach(b=>b.onclick=()=>{
  const [dx,dy]=b.dataset.mv.split(',').map(Number); move(dx,dy); });
document.querySelectorAll('.zoom button[data-zoom]').forEach(b=>b.onclick=()=>zoom(Number(b.dataset.zoom)));
$('gear').onclick=()=>{ $('cal').classList.toggle('open'); };
window.addEventListener('keydown',e=>{
  if(!$('cal').classList.contains('open')) return;
  if(e.target.tagName==='INPUT') return;
  const m={ArrowUp:[0,-4],ArrowDown:[0,4],ArrowLeft:[-4,0],ArrowRight:[4,0]};
  if(m[e.key]){ e.preventDefault(); move(...m[e.key]); }
  else if(e.key==='+'||e.key==='='){ zoom(4); } else if(e.key==='-'){ zoom(-4); }
});
loadBox();
</script>
</body>
</html>
"""


def local_ip():
    """Descobre o IP da maquina na rede local (pra acessar do celular)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def start_hotkey(key=None):
    """Registra/re-registra o atalho Tab+<tecla> para escanear o placar."""
    global _HOTKEY_HANDLE, HOTKEY_KEY
    if key:
        HOTKEY_KEY = key.lower()
    try:
        import keyboard
        import threading

        if _HOTKEY_HANDLE is not None:
            try:
                keyboard.remove_hotkey(_HOTKEY_HANDLE)
            except (KeyError, ValueError):
                pass

        def trigger():
            threading.Thread(target=do_scoreboard_scan, daemon=True).start()

        _HOTKEY_HANDLE = keyboard.add_hotkey("tab+" + HOTKEY_KEY, trigger)
        return True
    except Exception as e:
        print(f"  (atalho indisponivel: {e})")
        return False


_VOICE_HOTKEY_HANDLE = None
_VOICE_HOTKEY_KEY = None


def start_voice_hotkey():
    """Registra/re-registra o atalho global 'me ouvir' (tecla vinda da config da voz)."""
    global _VOICE_HOTKEY_HANDLE, _VOICE_HOTKEY_KEY
    key = voice.load_config().get("hotkey", "f8")
    if key == _VOICE_HOTKEY_KEY and _VOICE_HOTKEY_HANDLE is not None:
        return True  # ja registrado nessa tecla
    try:
        import keyboard

        if _VOICE_HOTKEY_HANDLE is not None:
            try:
                keyboard.remove_hotkey(_VOICE_HOTKEY_HANDLE)
            except (KeyError, ValueError):
                pass

        def trigger():
            threading.Thread(target=lambda: voice.run_listen(voice_handle), daemon=True).start()

        _VOICE_HOTKEY_HANDLE = keyboard.add_hotkey(key, trigger)
        _VOICE_HOTKEY_KEY = key
        return True
    except Exception as e:
        print(f"  (atalho de voz indisponivel: {e})")
        return False


def main():
    global PROVIDER
    PROVIDER = brain.get_provider()
    hotkey_ok = start_hotkey()
    voice_ok = start_voice_hotkey()

    server = ThreadingHTTPServer((HOST, PORT), Handler)
    ip = local_ip()
    print("=" * 60)
    print("  Copiloto Dota 2 - Servidor GSI no ar")
    print("=" * 60)
    print(f"  Painel (neste PC):     http://localhost:{PORT}")
    print(f"  Painel (celular/2a tela): http://{ip}:{PORT}")
    print(f"  Endpoint do Dota:      http://127.0.0.1:{PORT}/gsi")
    print(f"  Cerebro de IA:         {PROVIDER.name}")
    if PROVIDER.name.startswith("Modo basico"):
        print("    -> Para ligar o Claude: defina ANTHROPIC_API_KEY e reinicie.")
    print(f"  Placar (Tab+F7):       {'ativo' if hotkey_ok else 'INDISPONIVEL'}")
    vk = voice.load_config().get("hotkey", "f8").upper()
    print(f"  Voz / me ouvir ({vk}):  {'ativo' if voice_ok else 'INDISPONIVEL'}"
          f"{'' if voice.is_configured() else '  (configure a chave OpenAI em Settings)'}")
    print(f"  Minimapa (2a janela):  http://localhost:{PORT}/minimap")
    print("=" * 60)
    print("  Aguardando o Dota enviar dados... (abra/entre numa partida)")
    print("  Ctrl+C para parar.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nEncerrando.")
        server.shutdown()


if __name__ == "__main__":
    main()
