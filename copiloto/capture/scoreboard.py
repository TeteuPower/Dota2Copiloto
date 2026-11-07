"""
Leitura do placar (Tab) do Dota 2 pela visao do Claude + relatorio da partida.
==============================================================================

Captura o monitor onde o Dota 2 esta (detectado automaticamente) e manda a imagem
INLINE (base64) junto do prompt pro Claude (Agent SDK, assinatura) extrair os 10
herois + KDA numa unica leitura direta - sem a ferramenta Read.
Nomes vem em TEXTO no placar -> leitura precisa, sem matching.
As instrucoes de leitura sao editaveis em Configuracoes (copiloto/prompts.py).

Funciona em qualquer resolucao: manda o frame inteiro e a visao acha o placar.
"""

import json
import re

from PIL import Image

from copiloto import config, prompts
from copiloto.ai import engines

FULL_PATH = str(config.RUNTIME_DIR / "sb_full.png")
CROP_PATH = str(config.RUNTIME_DIR / "sb_crop.png")

# Tempo maximo pra uma leitura do placar. Com a imagem inline costuma levar ~5-20s;
# mantemos um teto folgado so como rede de seguranca (estourou -> devolve None e a UI
# mostra a mensagem amigavel, em vez de travar pra sempre).
ANALYZE_TIMEOUT = 120


# Com a imagem inline a leitura e direta (1 turno): o Claude olha a imagem e ja
# devolve o JSON. O system reforca PRECISAO (ler o TEXTO, nao chutar pela arte) e
# saida so-JSON. O retry no analyze() cobre uma eventual leitura ruim.
SYSTEM = ("Voce le placares de Dota 2 com MUITA atencao ao TEXTO escrito e responde "
          "SOMENTE com um JSON valido, sem nenhum texto fora do JSON.")


def _prompt():
    # Instrucoes de leitura (editaveis em Configuracoes) + contrato JSON fixo.
    # A imagem vai INLINE junto deste texto (nao mais via caminho de arquivo).
    return prompts.vision_prompt()


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


_ENGINE_LABEL = {"claude": "Claude", "codex": "Codex"}


def _engine_read(engine):
    """1 leitura por um motor de assinatura (Claude/Codex), imagem INLINE e com
    timeout. -> (dict|None, motivo)."""
    label = _ENGINE_LABEL.get(engine, engine)
    try:
        data = _extract_json(engines.ask(engine, SYSTEM, _prompt(), image=CROP_PATH,
                                         timeout=ANALYZE_TIMEOUT))
        if _ok(data):
            return data, ""
        return None, f"{label}: não retornou um placar válido (JSON)"
    except Exception as e:
        m = str(e) or type(e).__name__
        if type(e).__name__ in ("TimeoutError", "TimeoutExpired"):
            return None, f"{label}: tempo esgotado ({ANALYZE_TIMEOUT}s)"
        if "maximum number of turns" in m.lower():
            return None, f"{label}: estourou o limite de leituras (placar muito carregado) — tente de novo"
        return None, f"{label}: {m[:100]}"


def _claude_read():
    """Compat (selftest): 1 leitura pelo Claude."""
    return _engine_read("claude")


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
    subs = [lambda e=e: _engine_read(e) for e in engines.available_engines()]
    if voice.report_engine() == "openai" and have_openai:
        order = [_openai_read] + subs
    elif have_openai:
        order = subs + [_openai_read]            # assinatura primaria + fallback OpenAI
    elif len(subs) == 1:
        order = subs * 2                         # so um motor: 2 tentativas
    else:
        order = subs
    if not order:
        return None, ("nenhuma IA conectada — conecte o Claude ou o Codex em "
                      "Configurações")

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
