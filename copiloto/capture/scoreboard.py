"""
Leitura do placar (Tab) do Dota 2 pela visao do Claude + relatorio da partida.
==============================================================================

Captura o monitor onde o Dota 2 esta (detectado automaticamente) e pede pro
Claude (Agent SDK, assinatura) extrair os 10 herois + KDA numa unica chamada.
Nomes vem em TEXTO no placar -> leitura precisa, sem matching.

Funciona em qualquer resolucao: manda o frame inteiro e a visao acha o placar.
"""

import asyncio
import json
import re

from PIL import Image

from copiloto import config

FULL_PATH = str(config.RUNTIME_DIR / "sb_full.png")
CROP_PATH = str(config.RUNTIME_DIR / "sb_crop.png")

# Tempo maximo pra uma leitura do placar (costuma levar ~50-145s com a tela cheia).
# Estourou -> devolve None e a UI mostra a mensagem amigavel, em vez de travar pra sempre.
ANALYZE_TIMEOUT = 180


def _drain(_line):
    """Consome o stderr do 'claude' (o SDK so usa PIPE se houver callback)."""
    pass

# PRECISAO > velocidade: o Claude precisa LER o texto com atencao (nao chutar pela
# arte do personagem). Por isso o prompt pede leitura cuidadosa. Ele costuma usar
# varios turnos verificando (~40-50s); o max_turns alto + retry abaixo evitam o
# erro "max turns" sem sacrificar a precisao.
SYSTEM = ("Voce le placares de Dota 2 com MUITA atencao ao TEXTO escrito e responde "
          "SOMENTE com um JSON valido, sem nenhum texto fora do JSON.")


def _prompt():
    # Leitura focada: so extrair herois + KDA (o relatorio e gerado depois, em texto).
    return (
        f"Leia a imagem {CROP_PATH} (placar do Dota 2, tela do Tab). "
        "Ha dois times: 'OS ILUMINADOS' (cima) e 'OS TEMIDOS' (baixo), 5 jogadores cada. "
        "Em CADA linha ha DUAS linhas de texto: em cima o NOME DO JOGADOR (capitalizacao "
        "normal, ex.: 'Sofia') e LOGO ABAIXO o NOME DO HEROI em LETRAS MAIUSCULAS (ex.: 'LICH'). "
        "LEIA O TEXTO escrito - NAO adivinhe o heroi pela arte/retrato do personagem. "
        "Depois do OURO ha 3 numeros: V=abates(K), M=mortes(D), A=assistencias(A) - "
        "leia cada numero com atencao. Sao 5 jogadores em cada time. "
        "Responda APENAS com um JSON valido neste formato exato (sem texto antes ou depois): "
        '{"iluminados":[{"heroi":"...","jogador":"...","k":0,"d":0,"a":0}],'
        '"temidos":[{"heroi":"...","jogador":"...","k":0,"d":0,"a":0}]}'
    )


async def _ask(prompt):
    from claude_agent_sdk import query, ClaudeAgentOptions
    opts = ClaudeAgentOptions(
        allowed_tools=["Read"],
        permission_mode="bypassPermissions",
        max_turns=28,   # leitura cuidadosa usa varios turnos; 16 estourava as vezes
        system_prompt=SYSTEM,
        # CRITICO: registrar um callback de stderr faz o SDK usar um PIPE. Sem isso,
        # o processo do 'claude' HERDA o stderr do pai - que e INVALIDO no app sem
        # console (exe windowed) - e trava numa operacao longa como esta. (bug do
        # scan que so acontecia no app instalado).
        stderr=_drain,
    )
    out = []
    async for msg in query(prompt=prompt, options=opts):
        cls = type(msg).__name__
        if cls == "AssistantMessage":
            for b in getattr(msg, "content", None) or []:
                t = getattr(b, "text", None)
                if t:
                    out.append(t)
        elif cls == "ResultMessage":
            r = getattr(msg, "result", None)
            if isinstance(r, str) and not out:
                out.append(r)
    return "\n".join(out)


def _extract_json(text):
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def capture():
    """Captura o monitor onde o Dota 2 esta (tela do Tab). Frame inteiro:
    a posicao do placar varia por resolucao, entao deixamos o Claude achar."""
    import mss
    from copiloto.capture import screens
    with mss.MSS() as sct:
        target = screens.dota_monitor(sct)
        img = sct.grab(target)
        mss.tools.to_png(img.rgb, img.size, output=FULL_PATH)
    # Sem recorte fixo: salva o frame cheio para a visao ler (robusto a resolucao)
    Image.open(FULL_PATH).save(CROP_PATH)


def _ok(data):
    return bool(data and (data.get("iluminados") or data.get("temidos")))


def _claude_read():
    """1 leitura pelo Claude (Agent SDK) com timeout. -> (dict|None, motivo)."""
    async def _ask_timed():
        return await asyncio.wait_for(_ask(_prompt()), timeout=ANALYZE_TIMEOUT)
    try:
        data = _extract_json(asyncio.run(_ask_timed()))
        if _ok(data):
            return data, ""
        return None, "Claude: não retornou um placar válido (JSON)"
    except asyncio.TimeoutError:
        return None, f"Claude: tempo esgotado ({ANALYZE_TIMEOUT}s)"
    except Exception as e:
        m = str(e)
        if "maximum number of turns" in m.lower():
            return None, "Claude: estourou o limite de leituras (placar muito carregado) — tente de novo"
        return None, f"Claude: {m[:100]}"


def _openai_read():
    """1 leitura pela visão da OpenAI (rápida). -> (dict|None, motivo)."""
    from copiloto import voice
    if not voice.get_key():
        return None, ""   # sem chave: nem conta como tentativa
    try:
        data = _extract_json(voice.openai_vision(CROP_PATH, SYSTEM, _prompt()))
        if _ok(data):
            return data, ""
        return None, "OpenAI: não retornou um placar válido"
    except Exception as e:
        return None, f"OpenAI: {str(e)[:100]}"


def analyze():
    """Le o recorte -> (dict {iluminados, temidos} ou None, motivo_da_falha).

    Tenta o motor escolhido em Settings e, se falhar, CAI no outro (fallback):
      - 'claude' (padrao): assinatura, preciso porem lento; se falhar, tenta OpenAI.
      - 'openai': visao da OpenAI, rapida; se falhar, tenta o Claude.
    Sem chave OpenAI, tenta o Claude 2x. NUNCA levanta excecao."""
    from copiloto import voice
    have_openai = bool(voice.get_key())
    if voice.report_engine() == "openai" and have_openai:
        order = [_openai_read, _claude_read]
    elif have_openai:
        order = [_claude_read, _openai_read]     # Claude primario + fallback OpenAI
    else:
        order = [_claude_read, _claude_read]     # so Claude: 2 tentativas

    reasons = []
    for fn in order:
        data, why = fn()
        if data:
            return data, ""
        if why:
            reasons.append(why)
            print(f"[placar] {why}")
    return None, " · ".join(reasons) or "não consegui ler o placar (abra o Tab e tente de novo)"


def read_scoreboard():
    """Captura + le o placar de uma vez (uso standalone). -> (dict|None, motivo)."""
    capture()
    return analyze()


if __name__ == "__main__":
    import pprint
    print("Capturando e lendo o placar...")
    pprint.pprint(read_scoreboard())
