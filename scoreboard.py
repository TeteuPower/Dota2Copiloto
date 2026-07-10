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

FULL_PATH = r"C:\temp\sb_full.png"
CROP_PATH = r"C:\temp\sb_crop.png"

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
        max_turns=16,   # leitura cuidadosa usa varios turnos (~14); retry abaixo cobre estouro
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
    """Captura o monitor onde o Dota 2 esta (tela do Tab). Frame inteiro:
    a posicao do placar varia por resolucao, entao deixamos o Claude achar."""
    import mss
    import screens
    with mss.MSS() as sct:
        target = screens.dota_monitor(sct)
        img = sct.grab(target)
        mss.tools.to_png(img.rgb, img.size, output=FULL_PATH)
    # Sem recorte fixo: salva o frame cheio para a visao ler (robusto a resolucao)
    Image.open(FULL_PATH).save(CROP_PATH)


def _ok(data):
    return bool(data and (data.get("iluminados") or data.get("temidos")))


def analyze():
    """Le o recorte ja capturado -> dict {iluminados, temidos} ou None.

    Motor escolhido em Settings (voice.report_engine):
      - 'openai': visao da OpenAI (gpt-4o-mini), RAPIDO (~5s) - precisa da chave;
      - 'claude': Agent SDK da assinatura (preciso, porem lento ~70s), com retry.
    NUNCA levanta excecao: em falha total devolve None p/ a UI mostrar a mensagem amigavel."""
    # 1) OpenAI vision (rapido), se escolhido e com chave
    try:
        import voice
        if voice.report_engine() == "openai" and voice.get_key():
            data = _extract_json(voice.openai_vision(CROP_PATH, SYSTEM, _prompt()))
            if _ok(data):
                return data
            print("[placar] OpenAI vision veio vazio, caindo p/ Claude")
    except Exception as e:
        print(f"[placar] OpenAI vision falhou, caindo p/ Claude: {e}")

    # 2) Claude (Agent SDK), com retry
    for tentativa in range(2):
        try:
            data = _extract_json(asyncio.run(_ask(_prompt())))
            if _ok(data):
                return data
        except Exception as e:
            print(f"[placar] leitura (Claude) falhou (tentativa {tentativa + 1}/2): {e}")
    return None


def read_scoreboard():
    """Captura + le o placar de uma vez (uso standalone)."""
    capture()
    return analyze()


if __name__ == "__main__":
    import pprint
    print("Capturando e lendo o placar...")
    pprint.pprint(read_scoreboard())
