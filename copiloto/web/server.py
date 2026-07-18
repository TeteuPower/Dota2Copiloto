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

Rodar:   python main.py  (na raiz do repositorio)
Depois:  abra http://localhost:49317 no navegador.
"""

import os
import re
import sys
import json
import time
import socket
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from copiloto import config, voice
from copiloto.ai import brain
from copiloto.capture import draftscan, minimap, scoreboard
from copiloto.game import drafting, history, items
from copiloto.web.pages import DASHBOARD_HTML, MINIMAP_HTML

# ----------------------------------------------------------------------------
# Configuracao (fonte da verdade: copiloto/config.py)
# ----------------------------------------------------------------------------
HOST = config.HOST
PORT = config.PORT
AUTH_TOKEN = config.AUTH_TOKEN

# Ultimo estado recebido do Dota (compartilhado entre as threads)
LATEST = {
    "raw": None,        # JSON cru que o Dota mandou
    "received_at": 0.0, # timestamp do ultimo recebimento
}

# Historico do chat com o copiloto (limpo: sem o estado do jogo embutido)
CHAT_HISTORY = []
# Cerebro de IA selecionado (definido no main via brain.get_provider())
PROVIDER = None

# Saude da conexao com a IA (health-check REAL, atualizado em background).
#   level: "checking" | "ok" | "bad" | "warn"(modo basico)
AI_HEALTH = {"level": "checking", "detail": "verificando conexao...",
             "checked_at": 0.0, "checking": False}


def run_ai_probe():
    """Testa DE VERDADE se o cerebro de IA responde (chamada minima) e atualiza
    AI_HEALTH. Roda em background para nao travar as requisicoes."""
    if AI_HEALTH["checking"]:
        return
    AI_HEALTH["checking"] = True
    try:
        if PROVIDER is None:
            AI_HEALTH.update(level="bad", detail="IA nao inicializada")
        elif isinstance(PROVIDER, brain.FallbackProvider):
            AI_HEALTH.update(level="warn", detail="modo basico (sem IA conectada)")
        else:
            try:
                ok = bool(PROVIDER.probe())
            except Exception as e:
                AI_HEALTH.update(level="bad", detail=f"sem conexao ({str(e)[:90]})")
            else:
                AI_HEALTH.update(level="ok", detail="conectado e respondendo") if ok \
                    else AI_HEALTH.update(level="bad", detail="a IA nao respondeu")
    finally:
        AI_HEALTH["checked_at"] = time.time()
        AI_HEALTH["checking"] = False


# Aviso de atualizacao (consulta o ultimo release do GitHub; so no modo instalado)
UPDATE_INFO = {"latest": None, "update_url": None, "update_available": False}


def _version_tuple(v):
    try:
        return tuple(int(p) for p in str(v).strip().lstrip("v").split("."))
    except ValueError:
        return None


def check_updates_loop():
    """Thread: consulta o ultimo release ~a cada 6h e preenche UPDATE_INFO.
    Em dev (versao 'dev') nao roda. Silencioso em qualquer falha (sem internet etc)."""
    import urllib.request
    cur = _version_tuple(config.APP_VERSION)
    if cur is None:
        return
    url = f"https://api.github.com/repos/{config.GITHUB_REPO}/releases/latest"
    while True:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "CopilotoDota2",
                                                       "Accept": "application/vnd.github+json"})
            with urllib.request.urlopen(req, timeout=15) as r:
                data = json.loads(r.read().decode("utf-8"))
            latest = (data.get("tag_name") or "").lstrip("v")
            lt = _version_tuple(latest)
            if lt:
                UPDATE_INFO.update(
                    latest=latest,
                    update_url=data.get("html_url"),
                    update_available=lt > cur,
                )
                if lt > cur:
                    print(f"[UPDATE] nova versao disponivel: {latest} (instalada: {config.APP_VERSION})")
        except Exception:
            pass
        time.sleep(6 * 3600)


# Estado do draft. source: "auto" (vazio), "gsi", "manual", "scoreboard".
DRAFT_STATE = {"enemy": [], "allies": [], "bans": [], "source": "auto"}

# Ultimo placar lido. allies/enemies: [{hero_id, hero, player, img, k, d, a}]
# status: idle | capturando | recebido | analisando | pronto | erro
SCOREBOARD_STATE = {"allies": [], "enemies": [], "report": "", "suggested_items": [],
                    "status": "idle", "scanned_at": 0.0, "scanning": False, "error": None}

# Os relatorios de cada partida ficam no historico persistente (history.py / match_history/).

# Estado da leitura da tela de PICKS (aba Draft). status: idle|capturando|analisando|pronto|erro
DRAFT_SCAN_STATE = {"status": "idle", "scanning": False, "error": None,
                    "scanned_at": 0.0, "enemy": [], "allies": []}
HOTKEY_KEY = "f7"        # segunda tecla do atalho (Tab + HOTKEY_KEY), configuravel
_HOTKEY_HANDLE = None     # handle do keyboard para re-registrar
SCAN_SOUND = True        # alerta sonoro quando o comando de capturar a tela e reconhecido

# Relatorio RAPIDO so de itens (Tab + ITEMS_HOTKEY_KEY). Reusa os inimigos do
# ultimo placar lido -> chamada de TEXTO (sem visao), ~5-15s. status: idle|gerando|pronto|erro
ITEMS_HOTKEY_KEY = "f5"
_ITEMS_HOTKEY_HANDLE = None
ITEMS_STATE = {"report": "", "suggested_items": [], "status": "idle",
               "at": 0.0, "error": None, "running": False}

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
        "Seja direto, sem enrolacao. "
        "IMPORTANTE - ULTIMA LINHA, SOZINHA E SO PRA MAQUINA (o jogador nao le): escreva "
        "'ITENS_SUGERIDOS:' seguido dos NOMES INTERNOS em ingles (minusculo, com _, SEM o prefixo 'item_') "
        "dos itens que voce recomendou no cronograma (item 3), separados por virgula. "
        "Ex.: ITENS_SUGERIDOS: black_king_bar, monkey_king_bar, pipe"
    )
    try:
        # OpenAI (rapido ~5s) se escolhido em Settings e com chave; senao Claude (assinatura)
        if voice.report_engine() == "openai" and voice.get_key():
            raw = voice.openai_chat(ctx, pedido)
        else:
            raw = PROVIDER.reply([{"role": "user", "content": pedido}], ctx)
    except Exception as e:
        return f"(nao consegui gerar o relatorio: {e})", []
    return split_report_items(raw)


def split_report_items(text):
    """Separa o relatorio (texto p/ ler e falar) da linha 'ITENS_SUGERIDOS:'.
    Devolve (texto_limpo, lista_de_itens_com_icone)."""
    if not text:
        return text, []
    # aceita o marcador isolado numa linha OU no meio do texto (models variam);
    # (?is): ignorecase + DOTALL p/ capturar os itens ate o fim.
    m = re.search(r"(?is)\bITENS?_SUGERIDOS\b\s*[:\-]?\s*(.+)$", text)
    if not m:
        return text, []
    tokens = re.split(r"[,;/\n]+", m.group(1))
    clean = text[:m.start()].rstrip()
    return clean, items.enrich(tokens)


def _enemy_names_for_items():
    """Nomes dos herois inimigos pro relatorio rapido de itens: usa o ultimo
    placar lido (Tab+F7); senao, o draft marcado na aba Draft."""
    en = [e.get("hero") for e in (SCOREBOARD_STATE.get("enemies") or []) if e.get("hero")]
    if en:
        return en
    return [drafting.BY_ID[i]["localized_name"] for i in (DRAFT_STATE.get("enemy") or [])
            if i in drafting.BY_ID]


def generate_items_report(enemy_names, my_hero, items_txt, gold, level, clock):
    """Relatorio CURTO so de itens (texto, sem visao). Reaproveita o mesmo
    formato de ITENS_SUGERIDOS pra desenhar os icones no painel."""
    if PROVIDER is None:
        return "", []
    ctx = "\n".join(filter(None, [
        f"Meu heroi: {my_hero} (nivel {level})" if my_hero else "",
        f"Tempo: {clock} | Meu gold: {gold}",
        f"Meus itens atuais: {items_txt}",
        f"Time INIMIGO: {', '.join(enemy_names) or 'desconhecido'}",
    ]))
    pedido = (
        "Voce e um copiloto de Dota 2 pra um jogador INICIANTE. Foque SO EM ITENS (nada de "
        "situacao, ameacas ou o que fazer). Em PT-BR, bem curto e direto: uma LISTA NUMERADA "
        "(1) 2) 3)...) dos MEUS PROXIMOS itens, do que da pra comprar agora ate o fim de jogo, "
        "PRIORIZANDO itens que NEUTRALIZAM o time inimigo acima. Em cada item, no maximo 1 frase "
        "curta dizendo CONTRA QUEM/O QUE ele serve (ex.: BKB vs muito dano magico, MKB vs quem "
        "desvia ataque, armadura/Halberd vs fisico forte, Sentinela/Gem vs invisivel). Marque "
        "rapidinho o que eu JA tenho. No maximo 6 itens. "
        "IMPORTANTE - ULTIMA LINHA, SOZINHA E SO PRA MAQUINA (o jogador nao le): escreva "
        "'ITENS_SUGERIDOS:' seguido dos NOMES INTERNOS em ingles (minusculo, com _, SEM 'item_') "
        "dos itens recomendados, separados por virgula. Ex.: ITENS_SUGERIDOS: black_king_bar, pipe"
    )
    try:
        if voice.report_engine() == "openai" and voice.get_key():
            raw = voice.openai_chat(ctx, pedido)
        else:
            raw = PROVIDER.reply([{"role": "user", "content": pedido}], ctx)
    except Exception as e:
        return f"(nao consegui gerar o relatorio de itens: {e})", []
    return split_report_items(raw)


def do_items_report():
    """Gera o relatorio rapido de itens a partir do estado atual (GSI) + inimigos
    do ultimo placar. Atualiza ITEMS_STATE."""
    if ITEMS_STATE["running"]:
        return ITEMS_STATE
    ITEMS_STATE["running"] = True
    ITEMS_STATE["error"] = None
    ITEMS_STATE["status"] = "gerando"
    beep_recognized()
    try:
        raw = LATEST["raw"] or {}
        my_npc = (raw.get("hero") or {}).get("name")
        my_id = drafting.BY_NPC.get(my_npc) if my_npc else None
        my_hero = drafting.BY_ID.get(my_id, {}).get("localized_name", "") if my_id else ""
        s = summarize(raw)
        items_txt = ", ".join(s.get("inventory") or []) or "nenhum item relevante ainda"
        enemies = _enemy_names_for_items()
        if not enemies:
            ITEMS_STATE.update(status="erro",
                               error="escaneie o placar (Tab+F7) ou marque os inimigos na aba Draft primeiro")
            return ITEMS_STATE
        report, suggested = generate_items_report(
            enemies, my_hero, items_txt, s.get("gold"), s.get("level"), s.get("clock"))
        ITEMS_STATE.update(report=report, suggested_items=suggested,
                           status="pronto", at=time.time())
        if report and not report.startswith("(nao consegui") and voice.load_config().get("speak_report"):
            voice.speak(report)
        print(f"[ITENS] {time.strftime('%H:%M:%S')} | relatorio rapido (inimigos={enemies})")
    except Exception as e:
        ITEMS_STATE.update(status="erro", error=str(e))
    finally:
        ITEMS_STATE["running"] = False
    return ITEMS_STATE


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
        report, suggested = generate_report(allies, enemies, my_hero, items, clock, gold, level, previous=previous)
        SCOREBOARD_STATE["report"] = report
        SCOREBOARD_STATE["suggested_items"] = suggested
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
    SCOREBOARD_STATE.update({"allies": [], "enemies": [], "report": "", "suggested_items": [],
                             "status": "idle", "scanned_at": 0.0, "scanning": False, "error": None})
    DRAFT_SCAN_STATE.update({"status": "idle", "scanning": False, "error": None,
                             "scanned_at": 0.0, "enemy": [], "allies": []})
    ITEMS_STATE.update({"report": "", "suggested_items": [], "status": "idle",
                        "at": 0.0, "error": None, "running": False})
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

        # Versao instalada + aviso de atualizacao (GitHub Releases)
        if self.path == "/version":
            self._send_json({"version": config.APP_VERSION, **UPDATE_INFO})
            return

        # Saude da conexao com a IA (teste real). ?force=1 re-testa na hora.
        if self.path in ("/ai/health", "/ai/health?force=1"):
            force = self.path.endswith("force=1")
            # re-testa sozinho a cada 5 min (o probe do Claude e uma chamada real);
            # o clique no indicador forca na hora.
            stale = time.time() - AI_HEALTH["checked_at"] > 300
            if not AI_HEALTH["checking"] and (force or stale):
                threading.Thread(target=run_ai_probe, daemon=True).start()
            self._send_json({**AI_HEALTH, "provider": PROVIDER.name if PROVIDER else "?"})
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

        if self.path == "/items/state":
            self._send_json({**ITEMS_STATE, "hotkey": ITEMS_HOTKEY_KEY})
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

        if self.path == "/overlay/config":
            self._send_json(dict(OVERLAY_CFG))
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

        # Relatorio rapido so de itens (reusa os inimigos do ultimo placar)
        if self.path == "/items/report":
            self._send_json(do_items_report())
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

        if self.path == "/items/hotkey":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length) if length else b"{}"
            try:
                key = (json.loads(body).get("key") or "").strip().lower()
            except json.JSONDecodeError:
                key = ""
            if not key:
                self._send_json({"error": "tecla invalida"}, status=400)
                return
            ok = start_items_hotkey(key)
            self._send_json({"ok": ok, "hotkey": ITEMS_HOTKEY_KEY})
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

        # --- Config do overlay do minimapa (TTL do fantasma) ---
        if self.path == "/overlay/config":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length) if length else b"{}"
            try:
                d = json.loads(body)
            except json.JSONDecodeError:
                self._send_json({"error": "json invalido"}, status=400)
                return
            if "ghost_ttl" in d:
                try:
                    v = float(d["ghost_ttl"])
                    OVERLAY_CFG["ghost_ttl"] = 0 if v <= 0 else max(5.0, min(600.0, v))
                except (TypeError, ValueError):
                    pass
            if "portrait" in d:
                OVERLAY_CFG["portrait"] = bool(d["portrait"])
            save_overlay_cfg()
            self._send_json({"ok": True, **OVERLAY_CFG})
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


def start_items_hotkey(key=None):
    """Registra/re-registra o atalho Tab+<tecla> do relatorio rapido de itens."""
    global _ITEMS_HOTKEY_HANDLE, ITEMS_HOTKEY_KEY
    if key:
        ITEMS_HOTKEY_KEY = key.lower()
    try:
        import keyboard
        import threading

        if _ITEMS_HOTKEY_HANDLE is not None:
            try:
                keyboard.remove_hotkey(_ITEMS_HOTKEY_HANDLE)
            except (KeyError, ValueError):
                pass

        def trigger():
            threading.Thread(target=do_items_report, daemon=True).start()

        _ITEMS_HOTKEY_HANDLE = keyboard.add_hotkey("tab+" + ITEMS_HOTKEY_KEY, trigger)
        return True
    except Exception as e:
        print(f"  (atalho de itens indisponivel: {e})")
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


def current_my_team():
    """Meu time atual (radiant/dire) pelo GSI, ou None. O overlay do minimapa usa
    isso pra saber quais 5 cores sao INIMIGAS."""
    raw = LATEST["raw"] or {}
    return ((raw.get("player") or {}).get("team_name") or "").lower() or None


# --- Config do overlay (editavel pelo painel em /#settings) ---
OVERLAY_CFG_PATH = str(config.OVERLAY_CFG_PATH)
# legado: antes da reorganizacao o arquivo ficava na raiz do repo
_OVERLAY_CFG_LEGACY = str(config.BASE_DIR / "overlay_config.json")
OVERLAY_CFG = {
    "ghost_ttl": 120,   # segundos ate o fantasma expirar (0 = nunca)
    "portrait": False,  # False = bolinha (padrao) | True = retrato do heroi
}


def load_overlay_cfg():
    """Le overlay_config.json (runtime/, com fallback pro local antigo na raiz);
    na 1a vez semeia pelo COPILOT_GHOST_TTL (ou 120)."""
    global OVERLAY_CFG
    for path in (OVERLAY_CFG_PATH, _OVERLAY_CFG_LEGACY):
        try:
            with open(path, encoding="utf-8") as f:
                OVERLAY_CFG.update(json.load(f))
            return OVERLAY_CFG
        except Exception:
            continue
    try:
        OVERLAY_CFG["ghost_ttl"] = float(os.environ.get("COPILOT_GHOST_TTL", "120"))
    except ValueError:
        pass
    return OVERLAY_CFG


def save_overlay_cfg():
    try:
        with open(OVERLAY_CFG_PATH, "w", encoding="utf-8") as f:
            json.dump(OVERLAY_CFG, f, ensure_ascii=False, indent=1)
    except Exception as e:
        print(f"  (nao consegui salvar overlay_config: {e})")


def overlay_ghost_ttl():
    """TTL atual do fantasma em segundos; <=0 -> None (nunca expira)."""
    v = OVERLAY_CFG.get("ghost_ttl")
    try:
        v = float(v)
    except (TypeError, ValueError):
        return 120.0
    return v if v > 0 else None


def overlay_show_portrait():
    """True -> desenha o retrato do heroi no fantasma; False -> so a bolinha."""
    return bool(OVERLAY_CFG.get("portrait"))


def current_color_heroes():
    """Mapa {cor_inimiga: hero_id} pro overlay desenhar o RETRATO no fantasma.

    O placar (Tab+F7) lista os jogadores na ordem das cores (Radiant: azul, teal,
    roxo, amarelo, laranja; Dire: rosa, oliva, ciano, verde, marrom). Cruzamos essa
    ordem com os inimigos lidos. Vazio ate escanear o placar."""
    from copiloto.overlay import tracker as minimap_track
    team = current_my_team()
    order = minimap_track.DIRE if team == "radiant" else minimap_track.RADIANT
    colors = list(order.keys())
    out = {}
    for i, e in enumerate((SCOREBOARD_STATE.get("enemies") or [])[:5]):
        hid = e.get("hero_id")
        if hid and i < len(colors):
            out[colors[i]] = hid
    return out


def main():
    global PROVIDER

    # Rede de seguranca da instancia unica (a garantia REAL e o mutex no main.py;
    # no Windows o SO_REUSEADDR deixaria 2 processos na mesma porta sem erro).
    try:
        server = ThreadingHTTPServer((HOST, PORT), Handler)
    except OSError:
        print(f"[BOOT] porta {PORT} ocupada: o Copiloto ja esta rodando. Abrindo o painel.")
        webbrowser.open(f"http://localhost:{PORT}")
        return

    PROVIDER = brain.get_provider()
    # Testa a conexao com a IA em background (nao trava o boot do servidor).
    threading.Thread(target=run_ai_probe, daemon=True).start()
    # Aviso de nova versao (so faz algo no modo instalado; em dev sai na hora).
    threading.Thread(target=check_updates_loop, daemon=True).start()
    hotkey_ok = start_hotkey()
    items_ok = start_items_hotkey()
    voice_ok = start_voice_hotkey()

    ip = local_ip()
    print("=" * 60)
    print(f"  Copiloto Dota 2 - Servidor GSI no ar  (versao {config.APP_VERSION})")
    print("=" * 60)
    print(f"  Painel (neste PC):     http://localhost:{PORT}")
    print(f"  Painel (celular/2a tela): http://{ip}:{PORT}")
    print(f"  Endpoint do Dota:      http://127.0.0.1:{PORT}/gsi")
    print(f"  Cerebro de IA:         {PROVIDER.name}")
    if PROVIDER.name.startswith("Modo basico"):
        print("    -> Para ligar o Claude: defina ANTHROPIC_API_KEY e reinicie.")
    print(f"  Placar (Tab+{HOTKEY_KEY.upper()}):       {'ativo' if hotkey_ok else 'INDISPONIVEL'}")
    print(f"  Itens rapido (Tab+{ITEMS_HOTKEY_KEY.upper()}): {'ativo' if items_ok else 'INDISPONIVEL'}")
    vk = voice.load_config().get("hotkey", "f8").upper()
    print(f"  Voz / me ouvir ({vk}):  {'ativo' if voice_ok else 'INDISPONIVEL'}"
          f"{'' if voice.is_configured() else '  (configure a chave OpenAI em Settings)'}")
    print(f"  Minimapa (2a janela):  http://localhost:{PORT}/minimap")

    # ---- Overlay integrado (mesmo processo). O Qt PRECISA da thread principal,
    #      entao o servidor HTTP roda numa thread e o overlay na main. ----
    overlay_mod = None
    if os.environ.get("COPILOT_OVERLAY", "1") != "0":
        try:
            from copiloto.overlay import window as overlay_mod
            from PySide6 import QtWidgets
        except Exception as e:
            overlay_mod = None
            print(f"  Overlay:               indisponivel ({e})")

    if overlay_mod is not None:
        load_overlay_cfg()   # carrega o TTL do fantasma (editavel em /#settings)
        _ttl = overlay_ghost_ttl()
        print("  Overlay (Tab+F6):      ativo  (rode o Dota em 'Tela cheia em janela')")
        print(f"  Overlay do minimapa:   fantasma dos inimigos "
              f"(expira em {int(_ttl)}s)" if _ttl else "  Overlay do minimapa:   fantasma (nao expira)")
        print("=" * 60)
        print("  Aguardando o Dota enviar dados... (abra/entre numa partida)")
        print("  Feche pelo 'Desligar' no painel (ou pela bandeja, ou Ctrl+C aqui).")
        threading.Thread(target=server.serve_forever, daemon=True).start()
        import signal
        signal.signal(signal.SIGINT, signal.SIG_DFL)  # Ctrl+C encerra mesmo com o Qt no ar
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        app.setQuitOnLastWindowClosed(False)
        mini = overlay_mod.create_minimap_overlay(
            current_my_team, get_ttl=overlay_ghost_ttl,
            get_color_heroes=current_color_heroes, get_portrait=overlay_show_portrait)
        bridge = overlay_mod.wire_group(app, [mini])                 # Tab+F6 liga/desliga

        # Icone na BANDEJA: mostra que o app esta vivo, abre o painel (2 cliques),
        # liga/desliga o overlay e encerra - o app instalado nao tem console nenhum.
        try:
            from PySide6 import QtGui
            _panel = f"http://localhost:{PORT}"
            tray = QtWidgets.QSystemTrayIcon(
                QtGui.QIcon(str(config.RESOURCE_DIR / "assets" / "icon.ico")))
            menu = QtWidgets.QMenu()
            menu.addAction("Abrir painel", lambda: webbrowser.open(_panel))
            menu.addAction("Mostrar/esconder overlay  (Tab+F6)", lambda: bridge.toggle.emit())
            menu.addSeparator()
            menu.addAction("Desligar o Copiloto", shutdown_process)
            tray.setContextMenu(menu)
            tray._menu = menu   # impede o GC do menu
            tray.setToolTip(f"Copiloto Dota 2 v{config.APP_VERSION} — {_panel}")
            tray.activated.connect(
                lambda reason: webbrowser.open(_panel)
                if reason == QtWidgets.QSystemTrayIcon.ActivationReason.Trigger else None)
            tray.show()
            app._tray = tray    # impede o GC do icone
        except Exception as e:
            print(f"  (bandeja indisponivel: {e})")

        # Instalado: abre o painel sozinho pra dar feedback visual (nao ha console).
        # Excecao: --startup (iniciou junto com o Windows) -> fica so na bandeja.
        if config.FROZEN and "--startup" not in sys.argv:
            threading.Timer(1.0, lambda: webbrowser.open(f"http://localhost:{PORT}")).start()

        try:
            app.exec()
        finally:
            server.shutdown()
    else:
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
