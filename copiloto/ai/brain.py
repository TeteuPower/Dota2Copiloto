"""
Cerebro do Copiloto Dota 2 - camada de IA plugavel.
=====================================================

Abstrai o "cerebro" do copiloto atras de uma interface unica (Provider),
para a gente poder trocar de motor sem mexer no resto do app:

  - ClaudeAgentProvider : Claude pela ASSINATURA (Agent SDK, sem chave).  [padrao]
  - AnthropicProvider   : Claude pela API (chave).      REST puro (urllib).
  - OpenAIProvider      : OpenAI pela API (chave).       REST puro (urllib).
  - GeminiProvider      : Gemini/Google pela API (chave). REST puro (urllib).
  - LocalProvider       : modelo local (Ollama), gratis.
  - FallbackProvider    : modo basico por regras, SEM IA.

As chaves de API sao passadas EXPLICITAS pra get_provider (nao via ambiente):
setar ANTHROPIC_API_KEY no ambiente sequestraria o login da assinatura do CLI.

Selecao (get_provider(pref, keys)): 'auto' prioriza a assinatura (SDK, gratis) e
cai numa chave de API se ela parar de funcionar. Force um com pref='openai'|
'anthropic'|'gemini'|'claude_sdk'|'local'. Nenhum provider de API exige pacote
extra (tudo urllib) - funciona no exe congelado sem empacotar nada.
"""

import asyncio
import json
import os
import shutil
import urllib.request

MODEL_ANTHROPIC = os.environ.get("COPILOT_MODEL", "claude-opus-4-8")
MODEL_OPENAI = os.environ.get("COPILOT_OPENAI_MODEL", "gpt-4o")
MODEL_GEMINI = os.environ.get("COPILOT_GEMINI_MODEL", "gemini-2.0-flash")
# 'low' deixa as respostas rapidas (bom in-game). Suba para 'medium'/'high' se quiser mais profundidade.
EFFORT = os.environ.get("COPILOT_EFFORT", "low")
# Modelo local (Ollama) - gratis, sem chave. Vazio = escolhe o primeiro disponivel.
LOCAL_URL = os.environ.get("COPILOT_LOCAL_URL", "http://localhost:11434")
LOCAL_MODEL = os.environ.get("COPILOT_LOCAL_MODEL", "")

SYSTEM_PROMPT = """Voce e um copiloto especialista de Dota 2 que acompanha a partida do jogador AO VIVO.
Voce conversa com ele como um coach/duo de confianca: direto, pratico e amigavel.

Suas funcoes:
- Ajudar na escolha de heroi (draft), considerando counters e composicao.
- Recomendar itens situacionais com base na composicao inimiga e no estado do jogo.
- Avisar sobre timings (Roshan, BKB inimigo, power spikes) e dar dicas de fase de jogo.

Regras de estilo:
- Responda em portugues do Brasil, no maximo 4-6 linhas, com bullets curtos quando fizer sentido.
- Seja CONCRETO: cite itens e herois pelo nome e explique o "porque" em poucas palavras.
- Use o bloco <estado_do_jogo> que vem na mensagem como a verdade atual da partida.
- Se faltar informacao (ex: herois inimigos ainda nao detectados), diga o que sabe e pergunte o que precisa.
- Nada de textao. O jogador esta no meio da partida e precisa de respostas rapidas e acionaveis."""


def _inject_state(history, game_ctx):
    """Devolve uma copia de history com o estado do jogo embutido na ultima fala do usuario.

    Mantemos o history "limpo" (sem o estado) no servidor; o estado atual e
    injetado so na hora de chamar o modelo, sempre fresco.
    """
    msgs = [dict(m) for m in history]
    for i in range(len(msgs) - 1, -1, -1):
        if msgs[i]["role"] == "user":
            user_text = msgs[i]["content"]
            msgs[i] = {
                "role": "user",
                "content": f"<estado_do_jogo>\n{game_ctx}\n</estado_do_jogo>\n\n{user_text}",
            }
            break
    return msgs


class AnthropicProvider:
    """Cerebro via API do Claude (chave da Anthropic). REST puro (urllib), sem o
    pacote 'anthropic' - assim funciona no exe congelado sem empacotar nada."""

    name = "Claude (Anthropic)"

    def __init__(self, key=None):
        # chave passada EXPLICITA (nao via env): setar ANTHROPIC_API_KEY no
        # ambiente sequestraria o login da ASSINATURA do Claude CLI (o SDK).
        self.key = key or os.environ.get("ANTHROPIC_API_KEY")
        if not self.key:
            raise RuntimeError("sem chave Anthropic")
        self.model = MODEL_ANTHROPIC

    def _call(self, messages, max_tokens=700, timeout=60):
        payload = {"model": self.model, "max_tokens": max_tokens,
                   "system": SYSTEM_PROMPT, "messages": messages}
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "x-api-key": self.key,
                     "anthropic-version": "2023-06-01"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read().decode("utf-8"))
        return "".join(b.get("text", "") for b in (d.get("content") or [])
                       if b.get("type") == "text").strip()

    def reply(self, history, game_ctx):
        try:
            return self._call(_inject_state(history, game_ctx)) or "(sem resposta)"
        except Exception as e:
            return f"Erro ao falar com o Claude (API): {e}"

    def probe(self):
        """Teste minimo de conexao real (1 token). Levanta excecao se falhar."""
        self._call([{"role": "user", "content": "ping"}], max_tokens=1, timeout=15)
        return True


class ClaudeAgentProvider:
    """Cerebro via Claude Agent SDK - usa a credencial da sua ASSINATURA (login do
    Claude Code), SEM API key. Mesma abordagem do projeto jarvis."""

    name = "Claude (assinatura - Agent SDK)"

    def __init__(self):
        from claude_agent_sdk import query, ClaudeAgentOptions
        self._query = query
        self._Options = ClaudeAgentOptions

    def _build_prompt(self, history, game_ctx):
        lines = [f"<estado_do_jogo>\n{game_ctx}\n</estado_do_jogo>\n", "Conversa ate agora:"]
        for m in history:
            who = "Jogador" if m["role"] == "user" else "Voce (copiloto)"
            lines.append(f"{who}: {m['content']}")
        lines.append("\nResponda APENAS a ultima mensagem do Jogador, no seu papel de copiloto de Dota 2.")
        return "\n".join(lines)

    async def _ask(self, prompt):
        # stderr callback -> o SDK usa PIPE. Sem isso o processo do 'claude' herda
        # o stderr do pai, que e INVALIDO no app sem console (exe windowed) e pode
        # travar em respostas longas. (mesmo motivo do bug do scan do placar.)
        opts = self._Options(system_prompt=SYSTEM_PROMPT, allowed_tools=[],
                             max_turns=1, stderr=lambda _line: None)
        out = []
        async for msg in self._query(prompt=prompt, options=opts):
            cls = type(msg).__name__
            if cls == "AssistantMessage":
                for b in getattr(msg, "content", None) or []:
                    t = getattr(b, "text", None)
                    if t:
                        out.append(t)
            elif cls == "ResultMessage" and not out:
                r = getattr(msg, "result", None)
                if isinstance(r, str):
                    out.append(r)
        return "\n".join(out).strip()

    def reply(self, history, game_ctx):
        try:
            text = asyncio.run(self._ask(self._build_prompt(history, game_ctx)))
            return text or "(sem resposta do Claude)"
        except Exception as e:
            return f"Erro no Claude (Agent SDK): {e}"

    def probe(self):
        """Teste real: faz o Claude (assinatura) responder algo minimo.
        Levanta excecao se a credencial/SDK/CLI nao estiverem funcionando."""
        out = asyncio.run(self._ask("Responda apenas com: ok"))
        return bool(out and out.strip())


def _claude_agent_available():
    """True se o Agent SDK do Python e o CLI 'claude' (login da assinatura) existirem."""
    try:
        import claude_agent_sdk  # noqa: F401
    except Exception:
        return False
    return shutil.which("claude") is not None


class OpenAIProvider:
    """Cerebro via API da OpenAI (chave). REST puro (urllib), sem o pacote
    'openai' - funciona no exe congelado sem empacotar nada."""

    name = "OpenAI"

    def __init__(self, key=None):
        self.key = key or os.environ.get("OPENAI_API_KEY")
        if not self.key:
            raise RuntimeError("sem chave OpenAI")
        self.model = MODEL_OPENAI

    def _call(self, messages, max_tokens=700, timeout=60):
        payload = {"model": self.model, "messages": messages, "max_tokens": max_tokens}
        req = urllib.request.Request(
            "https://api.openai.com/v1/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer " + self.key},
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read().decode("utf-8"))
        return (d["choices"][0]["message"].get("content") or "").strip()

    def reply(self, history, game_ctx):
        messages = [{"role": "system", "content": SYSTEM_PROMPT}] + _inject_state(history, game_ctx)
        try:
            return self._call(messages)
        except Exception as e:
            return f"Erro ao falar com a OpenAI: {e}"

    def probe(self):
        """Teste minimo de conexao real (1 token). Levanta excecao se falhar."""
        self._call([{"role": "user", "content": "ping"}], max_tokens=1, timeout=15)
        return True


class GeminiProvider:
    """Cerebro via API do Google Gemini (REST, sem SDK/dependencia extra).
    Le a chave de GEMINI_API_KEY (ou GOOGLE_API_KEY)."""

    name = "Gemini (Google)"

    def __init__(self, key=None):
        self.key = key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not self.key:
            raise RuntimeError("sem chave Gemini")
        self.model = MODEL_GEMINI
        self.url = ("https://generativelanguage.googleapis.com/v1beta/models/"
                    f"{self.model}:generateContent")

    def _call(self, contents, system=SYSTEM_PROMPT, max_tokens=700, timeout=60):
        payload = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": contents,
            "generationConfig": {"maxOutputTokens": max_tokens},
        }
        req = urllib.request.Request(
            self.url + "?key=" + self.key,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read().decode("utf-8"))
        cands = d.get("candidates") or []
        if not cands:
            return ""
        parts = (cands[0].get("content") or {}).get("parts") or []
        return "".join(p.get("text", "") for p in parts).strip()

    def reply(self, history, game_ctx):
        msgs = _inject_state(history, game_ctx)
        contents = [{"role": "model" if m["role"] == "assistant" else "user",
                     "parts": [{"text": m["content"]}]} for m in msgs]
        try:
            return self._call(contents) or "(sem resposta do Gemini)"
        except Exception as e:
            return f"Erro ao falar com o Gemini: {e}"

    def probe(self):
        """Teste real: 1 chamada minima. Levanta excecao se a chave/rede falharem."""
        self._call([{"role": "user", "parts": [{"text": "ping"}]}],
                   system="responda apenas: ok", max_tokens=5, timeout=15)
        return True


class LocalProvider:
    """Cerebro via modelo LOCAL (Ollama) - gratis, sem chave, roda na sua maquina."""

    name = "Modelo local (Ollama)"

    def __init__(self):
        self.base = LOCAL_URL
        self.model = LOCAL_MODEL or self._first_model()
        if not self.model:
            raise RuntimeError("nenhum modelo Ollama disponivel")
        self.name = f"Modelo local ({self.model})"

    def _get(self, path, timeout=5):
        req = urllib.request.Request(self.base + path)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))

    def _first_model(self):
        models = [m["name"] for m in (self._get("/api/tags").get("models") or [])]
        for pref in ("llama3.1", "llama3", "qwen2.5", "qwen2", "mistral", "phi3", "gemma2"):
            for m in models:
                if m.startswith(pref):
                    return m
        return models[0] if models else None

    def reply(self, history, game_ctx):
        messages = [{"role": "system", "content": SYSTEM_PROMPT}] + _inject_state(history, game_ctx)
        payload = json.dumps({
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": {"num_predict": 700},
        }).encode("utf-8")
        try:
            req = urllib.request.Request(
                self.base + "/api/chat", data=payload,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=120) as r:
                data = json.loads(r.read().decode("utf-8"))
            return (data.get("message", {}).get("content") or "").strip()
        except Exception as e:
            return f"Erro ao falar com o modelo local: {e}"

    def probe(self):
        """Teste real: confirma que o Ollama responde e tem o modelo."""
        self._get("/api/tags", timeout=3)
        return True


def _ollama_available():
    """True se o Ollama estiver rodando e com pelo menos um modelo baixado."""
    try:
        req = urllib.request.Request(LOCAL_URL + "/api/tags")
        with urllib.request.urlopen(req, timeout=2) as r:
            return bool(json.loads(r.read().decode("utf-8")).get("models"))
    except Exception:
        return False


class FallbackProvider:
    """Modo basico por regras - funciona SEM chave de API.

    Nao e inteligente, mas ja conversa usando o estado da partida, pra o painel
    nao ficar morto enquanto voce nao configura a ANTHROPIC_API_KEY.
    """

    name = "Modo basico (sem IA)"

    def probe(self):
        """Nao ha IA conectada - sinaliza 'modo basico' (nem erro, nem conectado)."""
        return False

    def reply(self, history, game_ctx):
        last = ""
        for m in reversed(history):
            if m["role"] == "user":
                last = m["content"].lower()
                break

        dica = (
            "Estou em **modo basico** (sem IA conectada). Pra eu virar o copiloto "
            "Claude de verdade, defina a variavel de ambiente ANTHROPIC_API_KEY e reinicie o servidor.\n\n"
        )

        if any(w in last for w in ("item", "compr", "build", "o que faz")):
            corpo = ("Regra geral de itens situacionais:\n"
                     "- Muito dano magico inimigo -> BKB / Pipe / Glimmer.\n"
                     "- Evasao (PA, Windranger) -> MKB / Bloodthorn.\n"
                     "- Invisivel (Riki, BH, Clinkz) -> Sentry/Dust/Gem.\n"
                     "- Muito dano fisico -> armadura (Crimson, AC, Shiva).")
        elif any(w in last for w in ("pick", "escolh", "heroi", "draft", "counter")):
            corpo = ("Pra draft: escolha por ultimo, veja os picks inimigos e priorize "
                     "counters + cobertura de funcao (lane, controle, dano). "
                     "Com a IA ligada eu ranqueio os melhores picks automaticamente.")
        else:
            corpo = ("Posso ajudar com itens e picks. Pergunte algo como "
                     "'o que compro agora?' ou 'qual heroi pego contra esse time?'.")

        return dica + corpo


# Opcoes de provedor pro seletor do painel (valor -> rotulo)
PROVIDERS = [
    ("auto", "Automatico (recomendado)"),
    ("claude_sdk", "Claude — assinatura (Agent SDK, sem chave)"),
    ("anthropic", "Claude — chave da API (Anthropic)"),
    ("openai", "OpenAI — chave da API"),
    ("gemini", "Gemini (Google) — chave da API"),
    ("local", "Modelo local (Ollama)"),
]


def get_provider(pref=None, keys=None):
    """Decide qual cerebro usar. Nunca lanca excecao.

    pref (ou COPILOT_PROVIDER): 'auto' | 'claude_sdk' | 'anthropic' | 'openai' |
    'gemini' | 'local'. No 'auto', prioriza a ASSINATURA (SDK, gratis) e usa as
    chaves de API como fallback -> ideal pra quando o SDK parar de funcionar.

    keys: {'openai','anthropic','gemini'} passadas EXPLICITAS (nao via ambiente),
    pra nao sequestrar o login da assinatura do Claude CLI (que reage a
    ANTHROPIC_API_KEY no ambiente)."""
    if pref is None:
        pref = os.environ.get("COPILOT_PROVIDER", "")
    pref = (pref or "").lower()
    keys = keys or {}

    def _k(name, *envs):
        v = (keys.get(name) or "").strip()
        if v:
            return v
        for e in envs:
            if os.environ.get(e):
                return os.environ[e]
        return None

    def try_openai():
        k = _k("openai", "OPENAI_API_KEY")
        if k:
            try:
                return OpenAIProvider(k)
            except Exception:
                pass
        return None

    def try_anthropic():
        k = _k("anthropic", "ANTHROPIC_API_KEY")
        if k:
            try:
                return AnthropicProvider(k)
            except Exception:
                pass
        return None

    def try_gemini():
        k = _k("gemini", "GEMINI_API_KEY", "GOOGLE_API_KEY")
        if k:
            try:
                return GeminiProvider(k)
            except Exception:
                pass
        return None

    def try_claude_agent(force=False):
        if force or _claude_agent_available():
            try:
                return ClaudeAgentProvider()
            except Exception:
                pass
        return None

    def try_local(force=False):
        if force or _ollama_available():
            try:
                return LocalProvider()
            except Exception:
                pass
        return None

    if pref == "openai":
        return try_openai() or FallbackProvider()
    if pref in ("anthropic", "claude_api"):
        return try_anthropic() or FallbackProvider()
    if pref in ("gemini", "google"):
        return try_gemini() or FallbackProvider()
    if pref in ("claude_sdk", "claude", "subscription", "agent"):
        return try_claude_agent(force=True) or FallbackProvider()
    if pref == "local":
        return try_local(force=True) or FallbackProvider()

    # auto: ASSINATURA (SDK, gratis) primeiro -> chaves (Anthropic/OpenAI/Gemini)
    #       -> local -> modo basico. Se o SDK cair, cai numa chave sozinho.
    return (try_claude_agent() or try_anthropic() or try_openai()
            or try_gemini() or try_local() or FallbackProvider())
