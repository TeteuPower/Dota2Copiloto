"""
Prompts personalizaveis da IA (leitura do placar + relatorio tatico).
=====================================================================

Dois prompts que o usuario pode editar em Configuracoes (persistidos em
prompts_config.json, no DATA_DIR):

  - "vision"  -> INSTRUCOES de como LER o placar da imagem. O contrato de saida
                 (o JSON) e SEMPRE anexado pelo sistema e NAO e editavel - senao
                 a leitura quebraria e o painel/overlay ficariam sem dados.
  - "report"  -> INSTRUCOES do relatorio tatico (o "jeitao" do copiloto: tom,
                 foco, profundidade). A ultima linha de maquina ('ITENS_SUGERIDOS:')
                 tambem e anexada pelo sistema e NAO e editavel (o painel depende
                 dela pra desenhar os icones dos itens).

Campo vazio (ou igual ao padrao) = usar o padrao. Assim "Restaurar padrao" e so
mandar o texto padrao de volta.
"""

import json

from copiloto import config

PROMPTS_PATH = str(config.DATA_DIR / "prompts_config.json")

# ---------------------------------------------------------------------------
# LEITURA DO PLACAR (visao)
# ---------------------------------------------------------------------------
# So as INSTRUCOES de leitura (a imagem vai inline junto com este texto).
VISION_DEFAULT = (
    "A imagem e o placar do Dota 2 (tela do Tab). "
    "Ha dois times: 'OS ILUMINADOS' (cima) e 'OS TEMIDOS' (baixo), 5 jogadores cada. "
    "Em CADA linha ha DUAS linhas de texto: em cima o NOME DO JOGADOR (capitalizacao "
    "normal, ex.: 'Sofia') e LOGO ABAIXO o NOME DO HEROI em LETRAS MAIUSCULAS (ex.: 'LICH'). "
    "LEIA O TEXTO escrito - NAO adivinhe o heroi pela arte/retrato do personagem. "
    "Depois do OURO ha 3 numeros: V=abates(K), M=mortes(D), A=assistencias(A) - "
    "leia cada numero com atencao. Sao 5 jogadores em cada time."
)

# Contrato de saida (NAO editavel): garante que o JSON sempre saia parseavel.
VISION_JSON_CONTRACT = (
    "Responda APENAS com um JSON valido neste formato exato (sem texto antes ou depois): "
    '{"iluminados":[{"heroi":"...","jogador":"...","k":0,"d":0,"a":0}],'
    '"temidos":[{"heroi":"...","jogador":"...","k":0,"d":0,"a":0}]}'
)

# ---------------------------------------------------------------------------
# RELATORIO TATICO
# ---------------------------------------------------------------------------
# Instrucoes editaveis. O marcador {atualizacao} e trocado em tempo de execucao:
# vira o aviso de "nao repita" quando ja existem relatorios anteriores na partida,
# ou some (string vazia) no primeiro relatorio.
REPORT_DEFAULT = (
    "Faca um relatorio tatico OBJETIVO e CURTO em PT-BR, frases diretas, para um jogador INICIANTE. "
    "LINGUAGEM: nunca cite o nome de uma habilidade sozinho - diga em poucas palavras O QUE ELA FAZ "
    "(ex.: em vez de 'Chronosphere', 'o ultimate do Void te prende parado, ate com BKB'); diga quando for o "
    "'ultimate'; traduza giria ('bursta' = 'te mata rapido com muito dano'; 'stun' = 'te atordoa, sem poder agir'). "
    "Pode citar nome de heroi e de item normalmente. "
    "{atualizacao}"
    "RESPONDA NESTES 4 TOPICOS, curtos e diretos: "
    "(1) SITUACAO: 1 frase - quem esta ganhando (pelo KDA); se ja houver relatorios anteriores, cite tambem o que mudou. "
    "(2) AMEACAS: foque nos INIMIGOS EM DESTAQUE (os mais fortes listados acima) - 1 frase cada de como te matam. "
    "(3) CRONOGRAMA DE ITENS: lista numerada (1) 2) 3)...) dos PROXIMOS itens, do que da pra comprar agora ate o "
    "fim de jogo. PRIORIZE itens que NEUTRALIZAM os inimigos em destaque, dizendo em cada um QUAL inimigo ele "
    "neutraliza (ex.: BKB/Pipe vs muito dano magico, MKB vs quem desvia ataque, armadura/Halberd vs fisico forte, "
    "Sentinela/Gem vs invisivel, Linken/Lotus vs habilidade de alvo unico). Marque rapidinho o que eu JA tenho. "
    "(4) AGORA: 1 frase do que fazer (atacar junto, recuar e farmar, pegar Roshan, empurrar...). "
    "Seja direto, sem enrolacao."
)

# Injetado no lugar de {atualizacao} quando ja houve relatorios nesta partida.
REPORT_UPDATE_NOTE = (
    "NAO REPITA o que ja disse nos relatorios acima; foque no que MUDOU (mortes novas, quem cresceu/caiu, "
    "itens novos) e avance o cronograma (nao recomende item que eu ja comprei). "
)

# Linha de maquina (NAO editavel): o painel depende dela pra desenhar os icones.
REPORT_ITEMS_CONTRACT = (
    "IMPORTANTE - ULTIMA LINHA, SOZINHA E SO PRA MAQUINA (o jogador nao le): escreva "
    "'ITENS_SUGERIDOS:' seguido dos NOMES INTERNOS em ingles (minusculo, com _, SEM o prefixo 'item_') "
    "dos itens que voce recomendou no cronograma (item 3), separados por virgula. "
    "Ex.: ITENS_SUGERIDOS: black_king_bar, monkey_king_bar, pipe"
)

DEFAULTS = {"vision": VISION_DEFAULT, "report": REPORT_DEFAULT}

# Overrides do usuario ("" = usar o padrao). Fonte da verdade em memoria.
_CFG = {"vision": "", "report": ""}


def load():
    """Le prompts_config.json (se existir) pros prompts persistirem entre sessoes."""
    try:
        with open(PROMPTS_PATH, encoding="utf-8") as f:
            d = json.load(f)
        for k in _CFG:
            v = d.get(k)
            if isinstance(v, str):
                _CFG[k] = v
    except Exception:
        pass
    return _CFG


def save():
    try:
        with open(PROMPTS_PATH, "w", encoding="utf-8") as f:
            json.dump(_CFG, f, ensure_ascii=False, indent=1)
    except Exception as e:
        print(f"  (nao consegui salvar prompts: {e})")


def get(name):
    """Texto EFETIVO do prompt: o custom do usuario, ou o padrao se vazio."""
    v = (_CFG.get(name) or "").strip()
    return v or DEFAULTS.get(name, "")


def set_prompt(name, text):
    """Define o prompt. Vazio OU igual ao padrao -> volta a usar o padrao ('')."""
    if name not in DEFAULTS:
        return
    t = (text or "").strip()
    _CFG[name] = "" if (not t or t == DEFAULTS[name].strip()) else t


def public():
    """Pro front: texto atual (custom ou padrao), o padrao e se esta customizado."""
    return {
        name: {
            "text": get(name),
            "default": DEFAULTS[name],
            "custom": bool((_CFG.get(name) or "").strip()),
        }
        for name in DEFAULTS
    }


# ---------------------------------------------------------------------------
# Montagem final (instrucoes do usuario + contrato fixo do sistema)
# ---------------------------------------------------------------------------
def vision_prompt():
    """Instrucoes de leitura (editaveis) + contrato JSON fixo."""
    return get("vision").strip() + "\n\n" + VISION_JSON_CONTRACT


def report_prompt(has_previous):
    """Instrucoes do relatorio (editaveis, com {atualizacao} resolvido) + linha de maquina fixa."""
    nota = REPORT_UPDATE_NOTE if has_previous else ""
    base = get("report").replace("{atualizacao}", nota)
    return base.strip() + "\n\n" + REPORT_ITEMS_CONTRACT
