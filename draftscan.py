"""
Leitura da tela de PICKS (draft) do Dota 2 pela visao do Claude.
================================================================

Durante a selecao de herois (All Pick) os dois times aparecem como RETRATOS
no topo da tela. Captura a tela, recorta a faixa de cima e pede pro Claude
(Agent SDK, assinatura) identificar os herois ja escolhidos, ancorando pelo
SEU heroi (vindo do GSI) para saber qual lado e o inimigo.

Best-effort: ler RETRATO e menos preciso que ler o TEXTO do placar (scoreboard.py).
Por isso a entrada principal continua sendo o toque manual no grid; isto aqui
e o botao "copiar a tela de picks" que acelera o preenchimento.

Calibrado para 2560x1600. Ajuste CROP_BOX se mudar a resolucao.
"""

import asyncio
import json
import re

from PIL import Image

MON_W, MON_H = 2560, 1600
# Faixa de cima da tela: as duas fileiras de retratos picados (All Pick).
# Generoso na altura pra tolerar pequenas variacoes de HUD/escala.
CROP_BOX = (0, 0, 2560, 300)
FULL_PATH = r"C:\temp\draft_full.png"
CROP_PATH = r"C:\temp\draft_crop.png"

SYSTEM = ("Voce e um copiloto especialista de Dota 2 com visao computacional. "
          "Responda SOMENTE com um JSON valido, sem nenhum texto fora do JSON.")


def _prompt(my_hero=None):
    anchor = (f"O MEU heroi e '{my_hero}' - ele esta em um dos times. " if my_hero else "")
    return (
        f"A imagem {CROP_PATH} e a faixa de cima da tela de SELECAO DE HEROIS "
        "(draft All Pick) do Dota 2. No topo aparecem dois times, cada um com "
        "ate 5 RETRATOS de herois ja escolhidos (um time a esquerda, outro a direita). "
        + anchor +
        "Identifique pelos retratos os herois JA escolhidos de cada time. "
        "Use os nomes oficiais em ingles (ex: 'Queen of Pain', 'Anti-Mage', 'Sand King'). "
        "Ignore slots vazios (ainda sem heroi). "
        "Retorne SO um JSON valido neste formato exato: "
        '{"meu_time":["..."],"inimigo":["..."]}. '
        "Se nao conseguir saber qual e o meu time, ponha o time da esquerda em "
        "'meu_time' e o da direita em 'inimigo'."
    )


async def _ask(prompt):
    from claude_agent_sdk import query, ClaudeAgentOptions
    opts = ClaudeAgentOptions(
        allowed_tools=["Read"],
        permission_mode="bypassPermissions",
        max_turns=8,
        system_prompt=SYSTEM,
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
    """Captura o monitor 2560x1600 e salva o recorte da faixa de picks (rapido)."""
    import mss
    with mss.MSS() as sct:
        target = next((m for m in sct.monitors[1:]
                       if m["width"] == MON_W and m["height"] == MON_H), sct.monitors[1])
        img = sct.grab(target)
        mss.tools.to_png(img.rgb, img.size, output=FULL_PATH)
    Image.open(FULL_PATH).crop(CROP_BOX).save(CROP_PATH)


def analyze(my_hero=None):
    """Le o recorte ja capturado (Claude vision) -> dict {meu_time, inimigo} ou None."""
    raw = asyncio.run(_ask(_prompt(my_hero)))
    return _extract_json(raw)


def read_picks(my_hero=None):
    """Captura + le os picks de uma vez (uso standalone)."""
    capture()
    return analyze(my_hero)


if __name__ == "__main__":
    import pprint
    print("Capturando e lendo a tela de picks...")
    pprint.pprint(read_picks())
