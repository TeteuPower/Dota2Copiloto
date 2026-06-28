"""
Leitura do placar (Tab) do Dota 2 pela visao do Claude + relatorio da partida.
==============================================================================

Captura o monitor, recorta o painel do placar e pede pro Claude (Agent SDK,
assinatura) extrair os 10 herois + KDA E um relatorio curto da situacao, numa
unica chamada. Nomes vem em TEXTO no placar -> leitura precisa, sem matching.

Calibrado para 2560x1600. Ajuste CROP_BOX se mudar a resolucao.
"""

import asyncio
import json
import re

from PIL import Image

MON_W, MON_H = 2560, 1600
CROP_BOX = (0, 70, 1230, 1195)   # painel do placar (esquerda) em 2560x1600
FULL_PATH = r"C:\temp\sb_full.png"
CROP_PATH = r"C:\temp\sb_crop.png"

SYSTEM = ("Voce e um copiloto especialista de Dota 2 com visao computacional. "
          "Responda SOMENTE com um JSON valido, sem nenhum texto fora do JSON.")


def _prompt():
    # Leitura focada: so extrair herois + KDA (o relatorio e gerado depois, em texto).
    return (
        f"Leia o recorte do placar em {CROP_PATH} (Dota 2, tela do Tab). "
        "Ha dois times: 'OS ILUMINADOS' (cima) e 'OS TEMIDOS' (baixo), 5 jogadores cada. "
        "Em cada linha aparece o nome do JOGADOR e, embaixo em MAIUSCULAS, o nome do HEROI. "
        "As 3 colunas numericas apos o OURO sao V=abates(K), M=mortes(D), A=assistencias(A). "
        "Retorne SO um JSON valido neste formato exato: "
        '{"iluminados":[{"heroi":"...","jogador":"...","k":0,"d":0,"a":0}],'
        '"temidos":[{"heroi":"...","jogador":"...","k":0,"d":0,"a":0}]}'
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
    """Captura o monitor 2560x1600 e salva o recorte do placar (rapido)."""
    import mss
    with mss.MSS() as sct:
        target = next((m for m in sct.monitors[1:]
                       if m["width"] == MON_W and m["height"] == MON_H), sct.monitors[1])
        img = sct.grab(target)
        mss.tools.to_png(img.rgb, img.size, output=FULL_PATH)
    Image.open(FULL_PATH).crop(CROP_BOX).save(CROP_PATH)


def analyze():
    """Le o recorte ja capturado (Claude vision) -> dict {iluminados, temidos} ou None."""
    raw = asyncio.run(_ask(_prompt()))
    return _extract_json(raw)


def read_scoreboard():
    """Captura + le o placar de uma vez (uso standalone)."""
    capture()
    return analyze()


if __name__ == "__main__":
    import pprint
    print("Capturando e lendo o placar...")
    pprint.pprint(read_scoreboard())
