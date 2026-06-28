"""
Cerebro do Copiloto Dota 2 - camada de IA plugavel.
=====================================================

Abstrai o "cerebro" do copiloto atras de uma interface unica (Provider),
para a gente poder trocar de motor sem mexer no resto do app:

  - AnthropicProvider : usa a API do Claude (claude-opus-4-8).  [recomendado]
  - OpenAIProvider    : usa a API da OpenAI (futuro, com sua chave).
  - FallbackProvider  : modo basico por regras, SEM IA (roda sem chave).

Selecao automatica (get_provider):
  1. COPILOT_PROVIDER=openai     + OPENAI_API_KEY    -> OpenAI
  2. ANTHROPIC_API_KEY presente  + pacote 'anthropic'-> Claude
  3. caso contrario                                  -> Fallback (modo basico)

Assim o painel funciona JA (modo basico) e vira IA de verdade assim que
voce definir a variavel de ambiente ANTHROPIC_API_KEY.
"""

import asyncio
import json
import os
import shutil
import urllib.request

MODEL_ANTHROPIC = os.environ.get("COPILOT_MODEL", "claude-opus-4-8")
MODEL_OPENAI = os.environ.get("COPILOT_OPENAI_MODEL", "gpt-4o")
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
    """Cerebro via API do Claude."""

    name = "Claude (Anthropic)"

    def __init__(self):
        import anthropic  # importa so quando for usar
        self.anthropic = anthropic
        self.client = anthropic.Anthropic()  # le ANTHROPIC_API_KEY do ambiente
        self.model = MODEL_ANTHROPIC

    def reply(self, history, game_ctx):
        messages = _inject_state(history, game_ctx)
        try:
            resp = self.client.messages.create(
                model=self.model,
                max_tokens=700,
                system=[{
                    "type": "text",
                    "text": SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},  # cacheia a persona
                }],
                thinking={"type": "adaptive"},
                output_config={"effort": EFFORT},
                messages=messages,
            )
            if resp.stop_reason == "refusal":
                return "(O modelo recusou responder a esta mensagem.)"
            return "".join(b.text for b in resp.content if b.type == "text").strip()
        except self.anthropic.AuthenticationError:
            return "Chave da API do Claude invalida. Confira a variavel ANTHROPIC_API_KEY."
        except self.anthropic.RateLimitError:
            return "Limite de uso da API atingido por agora. Tenta de novo em alguns segundos."
        except Exception as e:  # rede, etc.
            return f"Erro ao falar com o Claude: {e}"


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
        opts = self._Options(system_prompt=SYSTEM_PROMPT, allowed_tools=[], max_turns=1)
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


def _claude_agent_available():
    """True se o Agent SDK do Python e o CLI 'claude' (login da assinatura) existirem."""
    try:
        import claude_agent_sdk  # noqa: F401
    except Exception:
        return False
    return shutil.which("claude") is not None


class OpenAIProvider:
    """Cerebro via API da OpenAI (futuro)."""

    name = "OpenAI"

    def __init__(self):
        from openai import OpenAI
        self.client = OpenAI()  # le OPENAI_API_KEY do ambiente
        self.model = MODEL_OPENAI

    def reply(self, history, game_ctx):
        messages = [{"role": "system", "content": SYSTEM_PROMPT}] + _inject_state(history, game_ctx)
        try:
            resp = self.client.chat.completions.create(
                model=self.model,
                max_tokens=700,
                messages=messages,
            )
            return (resp.choices[0].message.content or "").strip()
        except Exception as e:
            return f"Erro ao falar com a OpenAI: {e}"


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


def get_provider():
    """Decide qual cerebro usar com base no ambiente. Nunca lanca excecao.

    Ordem automatica: OpenAI(chave) -> Claude(chave) -> Local/Ollama -> modo basico.
    Force um especifico com COPILOT_PROVIDER=openai|anthropic|local.
    """
    pref = os.environ.get("COPILOT_PROVIDER", "").lower()

    def try_openai():
        if os.environ.get("OPENAI_API_KEY"):
            try:
                return OpenAIProvider()
            except Exception:
                pass
        return None

    def try_anthropic():
        if os.environ.get("ANTHROPIC_API_KEY"):
            try:
                return AnthropicProvider()
            except Exception:
                pass
        return None

    def try_claude_agent():
        if pref in ("claude", "subscription", "agent") or _claude_agent_available():
            try:
                return ClaudeAgentProvider()
            except Exception:
                pass
        return None

    def try_local():
        if pref == "local" or _ollama_available():
            try:
                return LocalProvider()
            except Exception:
                pass
        return None

    if pref == "openai":
        return try_openai() or FallbackProvider()
    if pref == "anthropic":
        return try_anthropic() or FallbackProvider()
    if pref in ("claude", "subscription", "agent"):
        return try_claude_agent() or FallbackProvider()
    if pref == "local":
        return try_local() or FallbackProvider()

    # auto: chave OpenAI -> chave Claude -> assinatura Claude (Agent SDK) -> local -> basico
    return (try_openai() or try_anthropic() or try_claude_agent()
            or try_local() or FallbackProvider())
