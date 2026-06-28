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
Depois:  abra http://localhost:3000 no navegador.
"""

import json
import time
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import brain
import drafting
import scoreboard

# ----------------------------------------------------------------------------
# Configuracao
# ----------------------------------------------------------------------------
HOST = "0.0.0.0"          # 0.0.0.0 = aceita conexoes da rede (celular/2a tela)
PORT = 3000
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
HOTKEY_KEY = "f7"        # segunda tecla do atalho (Tab + HOTKEY_KEY), configuravel
_HOTKEY_HANDLE = None     # handle do keyboard para re-registrar

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

    # Itens do inventario (slot0..slot8)
    inventory = []
    for i in range(9):
        slot = items.get(f"slot{i}") or {}
        name = pretty_item(slot.get("name"))
        if name:
            inventory.append(name)

    raw_state = g.get("game_state")
    summary = {
        "connected": True,
        "match_id": g.get("matchid"),
        "game_state_raw": raw_state,
        "game_state": GAME_STATE_PT.get(raw_state, raw_state),
        "clock": fmt_clock(g.get("clock_time")),
        "daytime": "Dia" if g.get("daytime") else "Noite",
        "hero": pretty_hero(hero.get("name")),
        "level": hero.get("level"),
        "health_pct": hero.get("health_percent"),
        "mana_pct": hero.get("mana_percent"),
        "alive": hero.get("alive"),
        "gold": player.get("gold"),
        "gpm": player.get("gpm"),
        "xpm": player.get("xpm"),
        "kda": [player.get("kills"), player.get("deaths"), player.get("assists")],
        "last_hits": player.get("last_hits"),
        "denies": player.get("denies"),
        "inventory": inventory,
        "has_draft": "draft" in raw,
    }
    return summary


def game_context_text():
    """Monta um resumo textual compacto do estado atual, para alimentar a IA."""
    s = summarize(LATEST["raw"])
    if not s.get("connected"):
        return "Nenhuma partida detectada ainda (Dota fechado ou fora de uma partida)."

    inv = ", ".join(s.get("inventory") or []) or "vazio"
    kda = s.get("kda") or []
    kda_txt = "/".join(str(x) for x in kda) if any(x is not None for x in kda) else "-"
    return "\n".join([
        f"Fase: {s.get('game_state')}",
        f"Relogio: {s.get('clock')} ({s.get('daytime')})",
        f"Seu heroi: {s.get('hero')} (nivel {s.get('level')})",
        f"Gold atual: {s.get('gold')} | GPM {s.get('gpm')} | XPM {s.get('xpm')}",
        f"KDA: {kda_txt} | Last hits: {s.get('last_hits')}",
        f"Seus itens: {inv}",
        "Herois inimigos: ainda nao detectados automaticamente (recurso em desenvolvimento).",
    ])


def generate_report(allies, enemies, my_hero, items, clock, gold, level):
    """Relatorio tatico via Claude (texto, sem imagem) a partir do placar + meus itens."""
    if PROVIDER is None:
        return ""

    def fmt_team(rows):
        return "; ".join(f"{r['hero']} ({r['k']}/{r['d']}/{r['a']})" for r in rows if r.get("hero"))

    ctx = "\n".join(filter(None, [
        f"Meu heroi: {my_hero} (nivel {level})" if my_hero else "",
        f"Tempo: {clock} | Meu gold: {gold}",
        f"Meus itens atuais: {items}",
        f"Meu time: {fmt_team(allies)}",
        f"Time INIMIGO: {fmt_team(enemies)}",
    ]))
    pedido = (
        "Com base no placar e nos MEUS itens acima, faca um relatorio tatico CURTO (4-6 frases), em PT-BR, cobrindo: "
        "(1) SITUACAO: quem esta na frente (pelo KDA); "
        "(2) AMEACAS: os herois inimigos mais perigosos e como me matam; "
        "(3) ITENS: avalie meus itens atuais e diga os PROXIMOS a comprar ESPECIFICAMENTE contra esse time "
        "(BKB/Pipe vs magico, MKB vs evasao, sentinela/Gem vs invisivel, armadura vs fisico); "
        "(4) ESTRATEGIA: o que fazer agora (push/defender/pickoff/farmar/agrupar). Direto e pratico."
    )
    try:
        return PROVIDER.reply([{"role": "user", "content": pedido}], ctx)
    except Exception as e:
        return f"(nao consegui gerar o relatorio: {e})"


def do_scoreboard_scan():
    """Captura o placar (Tab), o Claude le, mapeia herois e gera o relatorio.
    Decide quem e inimigo pelo SEU heroi (GSI). Atualiza SCOREBOARD_STATE."""
    if SCOREBOARD_STATE["scanning"]:
        return SCOREBOARD_STATE
    SCOREBOARD_STATE["scanning"] = True
    SCOREBOARD_STATE["error"] = None
    SCOREBOARD_STATE["status"] = "capturando"
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
        # 3) Relatorio tatico (texto) com base no placar + meus itens
        SCOREBOARD_STATE["report"] = generate_report(allies, enemies, my_hero, items, clock, gold, level)
        SCOREBOARD_STATE["scanned_at"] = time.time()
        SCOREBOARD_STATE["status"] = "pronto"

        DRAFT_STATE.update({"enemy": enemy_ids, "allies": ally_ids, "bans": [], "source": "scoreboard"})
        print(f"[PLACAR] {time.strftime('%H:%M:%S')} | inimigos={[r['hero'] for r in enemies]}")
        return SCOREBOARD_STATE
    except Exception as e:
        SCOREBOARD_STATE["error"] = str(e)
        SCOREBOARD_STATE["status"] = "erro"
        return SCOREBOARD_STATE
    finally:
        SCOREBOARD_STATE["scanning"] = False


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

        if self.path == "/scoreboard/state":
            self._send_json({**SCOREBOARD_STATE, "hotkey": HOTKEY_KEY})
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
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body { margin:0; font-family: system-ui, -apple-system, sans-serif; background:#0b0e14; color:#e6edf3; }
  header { padding:14px 20px; background:#11161f; border-bottom:1px solid #232a36;
           display:flex; align-items:center; gap:12px; position:sticky; top:0; z-index:10; }
  header h1 { font-size:17px; margin:0; font-weight:700; letter-spacing:.2px; }
  #dot { width:11px; height:11px; border-radius:50%; background:#f85149; transition:.3s; flex:none; }
  #dot.live { background:#3fb950; box-shadow:0 0 10px #3fb950; }
  #status { font-size:12px; color:#7d8899; }
  main { padding:18px; max-width:880px; margin:0 auto; display:flex; flex-direction:column; gap:18px; }
  .card { background:#141922; border:1px solid #232a36; border-radius:14px; padding:16px; }
  .card > h2 { font-size:13px; margin:0 0 12px; color:#9aa6b8; text-transform:uppercase; letter-spacing:.6px;
               display:flex; align-items:center; gap:8px; }
  .live { display:flex; flex-wrap:wrap; gap:18px; }
  .live .it { display:flex; flex-direction:column; }
  .live .lbl { font-size:11px; color:#7d8899; text-transform:uppercase; letter-spacing:.4px; }
  .live .val { font-size:19px; font-weight:700; }
  .empty { color:#5b6472; font-style:italic; font-size:13px; }

  .toolbar { display:flex; flex-wrap:wrap; align-items:center; gap:10px; margin-bottom:12px; }
  .toolbar label { font-size:13px; color:#9aa6b8; }
  select, .btn { background:#1c2230; border:1px solid #2c3444; color:#e6edf3; border-radius:9px;
                 padding:8px 12px; font-size:13px; font-family:inherit; cursor:pointer; }
  .btn.primary { background:#1f6feb; border-color:#1f6feb; color:#fff; font-weight:600; }
  .btn:disabled { opacity:.5; cursor:default; }
  .kbd { background:#0b0e14; border:1px solid #2c3444; border-radius:6px; padding:2px 8px; font-size:12px; font-weight:600; }

  .scanrow { display:flex; align-items:center; gap:14px; margin-bottom:14px; min-height:54px; }
  .chip { display:inline-flex; align-items:center; gap:7px; font-size:13px; padding:7px 13px; border-radius:20px;
          background:#1c2230; border:1px solid #2c3444; color:#9aa6b8; }
  .chip.go { color:#3fb950; border-color:#27502f; background:#0f2417; }
  .chip.work { color:#d29922; border-color:#5a4a16; background:#241d0c; }
  .chip.err { color:#f85149; border-color:#5a2222; background:#240f0f; }
  .spin { width:13px; height:13px; border:2px solid #d2992255; border-top-color:#d29922; border-radius:50%;
          animation:sp .7s linear infinite; }
  @keyframes sp { to { transform:rotate(360deg); } }
  #thumb { height:50px; border-radius:7px; border:1px solid #2c3444; display:none; }

  .teams { display:grid; grid-template-columns:1fr 1fr; gap:16px; }
  .team h3 { font-size:13px; margin:0 0 8px; display:flex; align-items:center; gap:7px; }
  .team.ally h3 { color:#3fb950; } .team.enemy h3 { color:#f85149; }
  .hero { display:flex; align-items:center; gap:10px; padding:7px; border-radius:10px; background:#1a2030;
          border:1px solid #232a36; margin-bottom:7px; }
  .team.enemy .hero { border-color:#3a2226; }
  .hero img { width:52px; height:30px; object-fit:cover; border-radius:5px; flex:none; background:#0b0e14; }
  .hero .nm { font-size:13.5px; font-weight:600; line-height:1.1; }
  .hero .pl { font-size:11px; color:#7d8899; }
  .hero .kda { margin-left:auto; font-size:13px; font-variant-numeric:tabular-nums; color:#c9d1d9; white-space:nowrap; }
  .hero .kda b { color:#3fb950; } .hero .kda i { color:#f85149; font-style:normal; } .hero .kda u { color:#58a6ff; text-decoration:none; }

  #report { background:#0f1420; border:1px solid #243049; border-radius:11px; padding:14px; font-size:14.5px;
            line-height:1.55; color:#dbe4f0; white-space:pre-wrap; }
  #report.empty2 { color:#5b6472; font-style:italic; border-style:dashed; }

  #chat { display:flex; flex-direction:column; gap:10px; }
  #log { display:flex; flex-direction:column; gap:8px; max-height:300px; overflow-y:auto; padding:2px; }
  .msg { padding:9px 12px; border-radius:11px; font-size:14px; line-height:1.45; white-space:pre-wrap; max-width:92%; }
  .msg.user { align-self:flex-end; background:#1f6feb22; border:1px solid #1f6feb55; }
  .msg.bot { align-self:flex-start; background:#1a2030; border:1px solid #232a36; }
  .msg strong { color:#58a6ff; }
  #chatform { display:flex; gap:8px; }
  #chatinput { flex:1; background:#0b0e14; border:1px solid #2c3444; border-radius:10px; color:#e6edf3;
               padding:10px 12px; font-size:14px; font-family:inherit; resize:none; }
  #chatform button { border:none; border-radius:10px; padding:0 15px; font-weight:600; cursor:pointer; }
  #chatsend { background:#238636; color:#fff; }
  #micbtn { background:#1c2230; border:1px solid #2c3444; font-size:16px; }
  #micbtn.rec { background:#f85149; border-color:#f85149; }
  details { color:#7d8899; } pre { background:#06090f; border:1px solid #232a36; border-radius:8px;
           padding:10px; overflow:auto; font-size:11px; max-height:300px; }
</style>
</head>
<body>
<header>
  <div id="dot"></div>
  <h1>Copiloto Dota 2</h1>
  <span id="status">conectando...</span>
</header>
<main>

  <div class="card">
    <h2>Partida ao vivo</h2>
    <div class="live" id="live"><span class="empty">aguardando o jogo (GSI)...</span></div>
  </div>

  <div class="card">
    <h2>📋 Placar — leitura por IA</h2>
    <div class="toolbar">
      <label>Atalho:</label> <span class="kbd">Tab</span> +
      <select id="hksel">
        <option>f5</option><option>f6</option><option>f7</option><option>f8</option>
        <option>f9</option><option>f10</option><option>f11</option><option>f12</option>
      </select>
      <button class="btn primary" id="sbscan">📷 Escanear agora</button>
      <span style="flex:1"></span>
      <span class="chip" id="voicetoggle" style="cursor:pointer;">🔊 voz: off</span>
    </div>
    <div class="scanrow">
      <span class="chip" id="sbchip">pronto pra escanear</span>
      <img id="thumb" alt="print">
    </div>
    <div class="teams" id="teams"></div>
    <div style="margin-top:14px;">
      <h2 style="margin-bottom:8px;">🧠 Relatório do agente</h2>
      <div id="report" class="empty2">escaneie o placar (Tab + tecla) para o agente analisar a partida.</div>
    </div>
  </div>

  <div class="card">
    <h2>💬 Conversar com o copiloto <span id="provider" style="color:#7d8899;text-transform:none;letter-spacing:0;">...</span>
      <span style="flex:1"></span>
      <span class="chip" id="clearchat" style="cursor:pointer;font-size:11px;padding:4px 10px;">limpar</span></h2>
    <div id="chat">
      <div id="log"></div>
      <form id="chatform">
        <textarea id="chatinput" rows="1" placeholder="ex: o que compro agora contra esse time?"></textarea>
        <button type="button" id="micbtn" title="Falar">🎤</button>
        <button type="submit" id="chatsend">Enviar</button>
      </form>
    </div>
  </div>

  <details><summary>JSON cru do GSI</summary><pre id="raw">-</pre></details>
</main>

<script>
const $ = id => document.getElementById(id);
function card(lbl, val) { return `<div class="it"><span class="lbl">${lbl}</span><span class="val">${val ?? '-'}</span></div>`; }

// ---------- GSI ao vivo ----------
async function tick() {
  try {
    const d = await (await fetch('/state')).json();
    const s = d.summary || {};
    const fresh = d.seconds_since_update !== null && d.seconds_since_update < 8;
    $('dot').className = fresh ? 'live' : '';
    $('status').textContent = d.seconds_since_update === null ? 'sem dados (Dota fechado?)'
      : (fresh ? 'ao vivo' : 'parado ha ' + d.seconds_since_update + 's');
    if (s.connected) {
      const kda = (s.kda || []).filter(x => x != null).length ? (s.kda).join(' / ') : null;
      $('live').innerHTML = card('Fase', s.game_state) + card('Relogio', s.clock) + card('Heroi', s.hero)
        + card('Nivel', s.level) + card('Gold', s.gold) + card('GPM', s.gpm) + card('K/D/A', kda);
    }
    $('raw').textContent = d.raw ? JSON.stringify(d.raw, null, 2) : '(nada ainda)';
  } catch (e) { $('status').textContent = 'servidor offline'; $('dot').className = ''; }
}
setInterval(tick, 1000); tick();

// ---------- Placar (IA) ----------
let sbLastScan = 0, hkReady = false;
const CHIP = {
  idle: ['', 'pronto pra escanear'], capturando: ['work', 'capturando a tela...'],
  recebido: ['go', '📸 print recebido'], analisando: ['work', '🧠 Claude analisando o placar...'],
  pronto: ['go', '✅ leitura concluida'], erro: ['err', 'erro ao ler'],
};
function teamHtml(title, cls, rows) {
  const body = (rows || []).map(r => `
    <div class="hero">
      <img src="${r.img || ''}" alt="" onerror="this.style.visibility='hidden'">
      <div><div class="nm">${r.hero || '?'}</div><div class="pl">${r.player || ''}</div></div>
      <div class="kda"><b>${r.k ?? '-'}</b>/<i>${r.d ?? '-'}</i>/<u>${r.a ?? '-'}</u></div>
    </div>`).join('');
  return `<div class="team ${cls}"><h3>${title}</h3>${body || '<span class="empty">-</span>'}</div>`;
}
function fmt(t) {
  const e = (t || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  return e.replace(/\\*\\*(.+?)\\*\\*/g, '<strong>$1</strong>');
}
async function pollSB() {
  try {
    const d = await (await fetch('/scoreboard/state')).json();
    if (!hkReady && d.hotkey) { $('hksel').value = d.hotkey; hkReady = true; }
    const [c, txt] = CHIP[d.status] || CHIP.idle;
    $('sbchip').className = 'chip ' + c;
    $('sbchip').innerHTML = (d.status === 'capturando' || d.status === 'analisando' ? '<span class="spin"></span>' : '')
      + (d.error ? ('erro: ' + d.error) : txt);
    const th = $('thumb');
    if (['recebido','analisando','pronto'].includes(d.status)) { th.src = '/scoreboard/image?t=' + d.scanned_at; th.style.display = 'block'; }
    if ((d.allies && d.allies.length) || (d.enemies && d.enemies.length))
      $('teams').innerHTML = teamHtml('Seu time', 'ally', d.allies) + teamHtml('Inimigos', 'enemy', d.enemies);
    if (d.report) { $('report').className = ''; $('report').innerHTML = fmt(d.report); }
    if (d.scanned_at && d.scanned_at !== sbLastScan) {
      sbLastScan = d.scanned_at;
      if (d.report && voiceOn) speak(d.report);
    }
  } catch (e) {}
}
setInterval(pollSB, 1000); pollSB();

$('sbscan').addEventListener('click', async () => {
  $('sbscan').disabled = true;
  try { await fetch('/scoreboard/scan', {method:'POST'}); } catch (e) {}
  $('sbscan').disabled = false; pollSB();
});
$('hksel').addEventListener('change', async () => {
  await fetch('/scoreboard/hotkey', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({key: $('hksel').value})});
});

// ---------- Chat ----------
const log = $('log'), input = $('chatinput'), form = $('chatform'), sendBtn = $('chatsend');
function addMsg(role, text) {
  const div = document.createElement('div');
  div.className = 'msg ' + (role === 'user' ? 'user' : 'bot');
  div.innerHTML = fmt(text); log.appendChild(div); log.scrollTop = log.scrollHeight; return div;
}
async function loadHistory() {
  try {
    const d = await (await fetch('/chat/history')).json();
    $('provider').textContent = '· ' + (d.provider || '?');
    log.innerHTML = '';
    (d.history || []).forEach(m => addMsg(m.role, m.content));
    if (!(d.history || []).length) addMsg('bot', 'Escaneie o placar e me pergunte o que fazer. Posso falar sobre itens, ameacas e jogadas.');
  } catch (e) {}
}
form.addEventListener('submit', async (e) => {
  e.preventDefault();
  const msg = input.value.trim(); if (!msg) return;
  addMsg('user', msg); input.value = ''; input.style.height = 'auto'; sendBtn.disabled = true;
  const thinking = addMsg('bot', '...');
  try {
    const d = await (await fetch('/chat', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({message: msg})})).json();
    thinking.innerHTML = fmt(d.reply || d.error || '(sem resposta)'); speak(d.reply || '');
  } catch (e) { thinking.textContent = 'erro de conexao.'; }
  log.scrollTop = log.scrollHeight; sendBtn.disabled = false; input.focus();
});
input.addEventListener('input', () => { input.style.height = 'auto'; input.style.height = Math.min(input.scrollHeight, 120) + 'px'; });
input.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); form.requestSubmit(); } });
$('clearchat').addEventListener('click', async () => { await fetch('/chat/reset', {method:'POST'}); loadHistory(); });
loadHistory();

// ---------- Voz ----------
const ttsOk = 'speechSynthesis' in window;
const RecCtor = window.SpeechRecognition || window.webkitSpeechRecognition;
let voiceOn = false;
function ptVoice() { const vs = speechSynthesis.getVoices(); return vs.find(v => v.lang && v.lang.toLowerCase().startsWith('pt')) || vs.find(v => v.default) || vs[0]; }
function speak(text) {
  if (!voiceOn || !ttsOk || !text) return;
  speechSynthesis.cancel();
  const clean = text.replace(/\\*\\*/g,'').replace(/[#>*_`]/g,'').replace(/\\s+/g,' ').trim();
  (clean.match(/[^.!?\\n]+[.!?]?/g) || [clean]).forEach(p => {
    const u = new SpeechSynthesisUtterance(p.trim()); const v = ptVoice(); if (v) u.voice = v;
    u.lang = 'pt-BR'; u.rate = 1.05; speechSynthesis.speak(u);
  });
}
$('voicetoggle').addEventListener('click', () => {
  if (!ttsOk) { $('voicetoggle').textContent = 'voz indisponivel'; return; }
  voiceOn = !voiceOn; $('voicetoggle').textContent = voiceOn ? '🔊 voz: on' : '🔊 voz: off';
  if (voiceOn) { speechSynthesis.getVoices(); speak('Voz ligada.'); } else speechSynthesis.cancel();
});
const micbtn = $('micbtn'); let recording = false, rec = null;
if (RecCtor && micbtn) {
  rec = new RecCtor(); rec.lang = 'pt-BR'; rec.interimResults = false; rec.maxAlternatives = 1;
  rec.onresult = (e) => { input.value = e.results[0][0].transcript; form.requestSubmit(); };
  rec.onend = () => { recording = false; micbtn.classList.remove('rec'); };
  rec.onerror = () => { recording = false; micbtn.classList.remove('rec'); };
  micbtn.addEventListener('click', () => { if (recording) { try { rec.stop(); } catch (e) {} return; } try { rec.start(); recording = true; micbtn.classList.add('rec'); } catch (e) {} });
} else if (micbtn) micbtn.style.display = 'none';
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


def main():
    global PROVIDER
    PROVIDER = brain.get_provider()
    hotkey_ok = start_hotkey()

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
