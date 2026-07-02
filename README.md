# Copiloto Dota 2

Copiloto de partida: **assistente de draft** (sugere picks por counters + função do time),
**chat com IA** (Claude) que enxerga o estado do jogo, e um **painel ao vivo** via GSI.
Você acompanha tudo no celular ou numa 2ª/3ª tela, sem tirar o jogo da tela principal.

```
  Dota 2 ──(GSI)──►  server.py ──►  painel web (celular / 2ª-3ª tela)
                        │                ├─ Painel ao vivo (herói, gold, itens)
   OpenDota (cache) ────┤                ├─ Assistente de Draft (grid + sugestões)
                        │                └─ Chat com o copiloto (IA)
   Claude API ──────────┘
```

## Arquivos

| Arquivo | O que é |
|---|---|
| `server.py` | Servidor: recebe GSI, serve o painel, chat e endpoints de draft |
| `minimap.py` | Minimapa ao vivo: captura o cantinho do minimapa da tela e transmite ampliado |
| `drafting.py` | Motor do draft: índices, algoritmo de counter/função, parser GSI |
| `brain.py` | Cérebro de IA plugável (Claude / OpenAI / modo básico) |
| `build_cache.py` | Baixa e cacheia herois + matriz de counters da OpenDota |
| `cache/` | `heroes.json`, `matchups.json`, `meta.json` (atualizar por patch) |
| `gamestate_integration_copiloto.cfg` | Config que faz o Dota enviar os dados |

## Como ligar a aplicação

**Jeito fácil:** dê **dois cliques** no arquivo `iniciar.bat` (abre uma janela preta com o servidor).

**Ou pelo terminal:**
```powershell
python c:\Trabalho\dota2\server.py
```
Depois abra `http://localhost:49317` no navegador (ou o IP mostrado no terminal, pra ver no celular/2ª-3ª tela). Para **desligar**, feche a janela (ou Ctrl+C no terminal).

## Ligar o painel ao vivo (GSI) — setup único

1. A config já está em `...\dota 2 beta\game\dota\cfg\gamestate_integration\` ✅
2. **Steam → Dota 2 → Propriedades → Opções de Inicialização**, adicione:
   ```
   -gamestateintegration
   ```
3. **Feche e reabra o Dota.** A bolinha do painel fica verde quando os dados chegam.

> Sem a opção de inicialização o Dota **não envia** nada — e ela só vale após reiniciar o jogo.

## Assistente de draft (aba **Draft**) — não depende do GSI

O GSI **não expõe os heróis do time inimigo enquanto você joga** (proteção anti-cheat da
Valve — issue [#19408](https://github.com/ValveSoftware/Dota2-Gameplay/issues/19408)). Por isso
a aba **Draft** te dá o **quadro de heróis** e dois jeitos de informar os picks do inimigo:

1. **Toque manual:** escolha o modo (Inimigo / Aliado / Ban) e toque nos heróis conforme aparecem
   na sua tela de pick (re-tocar desmarca). Instantâneo e 100% confiável.
2. **📷 Copiar tela de picks:** captura a faixa de cima da sua tela e o Claude (visão) identifica
   os heróis já escolhidos e preenche sozinho — ancora pelo SEU herói (GSI) pra saber qual lado é
   o inimigo. *Best-effort* (lê retrato, menos preciso que o texto do placar) — ajuste fino no toque.

Conforme você marca inimigos, **o grid se colore e reordena pela vantagem natural**: verde = você
countera, vermelho = você é counterado, com o selo de +X% em cada herói. O painel **Melhores Picks**
lista os counters mais fortes com o motivo ("Forte vs X +Y%"). Quando você espectа (Captains Mode),
o GSI pré-preenche o grid sozinho. O menu mostra um selo **PICK** pulsando durante a seleção.

Atualizar os dados de counter quando sair um patch:
```powershell
python c:\Trabalho\dota2\build_cache.py
```

## Cérebro de IA (chat) — Claude pela assinatura, SEM API key

O cérebro é plugável e escolhido automaticamente nesta ordem:
**chave OpenAI → chave Anthropic → Claude (assinatura, Agent SDK) → Ollama local → modo básico.**

O caminho recomendado é o **Claude Agent SDK**, que usa o login da sua **assinatura do Claude Code**
(igual ao projeto `jarvis`) — **sem chave de API, sem custo por uso**. Pré-requisitos (já instalados):
```powershell
npm install -g @anthropic-ai/claude-code   # CLI 'claude' (reusa seu login ~/.claude)
pip install claude-agent-sdk               # SDK Python
```
O selo do painel mostra **"Claude (assinatura - Agent SDK)"** quando ativo.
Alternativas: `setx ANTHROPIC_API_KEY "sk-ant-..."` (pague-por-uso), ou
`setx COPILOT_PROVIDER local` + Ollama (modelo local grátis).

## Minimapa ao vivo (2ª janela grande)

No dashboard, o painel **Minimapa** mostra o mapa ao vivo; o botão **⛶ Abrir minimapa grande**
abre uma **2ª janela** dedicada (em tela cheia) — ideal pra deixar numa 3ª tela. Ou abra direto
`http://localhost:49317/minimap`.

Como funciona: o servidor captura continuamente **só o cantinho do minimapa da sua tela**
(canto inferior esquerdo) e transmite ampliado (stream MJPEG, ~6 fps). Mostra as posições reais de
tudo que **o jogo já te mostra** (aliados sempre; inimigos quando visíveis) — **respeita a névoa**,
não revela nada escondido. Por isso funciona **enquanto você joga**.

> Por que não vetorial (bolinhas com x/y)? O bloco `minimap` do GSI (com coordenadas de todos) só é
> enviado quando você **espectа** uma partida/replay — não na sua própria ranqueada (mesma proteção
> anti-cheat do draft inimigo). O espelho de tela é o que funciona de verdade durante a sua partida.

**Calibração:** está calibrado pra **2560×1600**. Se o recorte não bater (outra resolução / escala de
HUD), clique na **engrenagem ⚙** da janela grande e ajuste com as setas/zoom até enquadrar só o
minimapa (salva automático; setas do teclado também movem).

## Voz (ouvir e falar)

No painel: o selo **🔊 voz** liga a leitura em voz alta das respostas (TTS), e o botão **🎤**
captura sua pergunta por voz (STT). Usa a **Web Speech API do navegador** — grátis, sem chave
(funciona melhor no Chrome/Edge). Espelha o motor "local" do jarvis.

## Status do projeto

- [x] **Fase 0 – Ver o jogo:** GSI + servidor + painel
- [x] **Fase 1 – Assistente de draft:** aba Draft com grid, marcação rápida (toque) + 📷 copiar tela de picks (visão), counters (OpenDota) coloridos/ordenados ao vivo + função do time
- [x] **Chat com copiloto:** Claude pela assinatura (Agent SDK, sem API key) / OpenAI / Ollama / básico
- [x] **Voz:** ouvir (STT) + falar (TTS) via Web Speech API
- [x] **Minimapa ao vivo:** espelho ampliado do minimapa numa 2ª janela (funciona jogando)
- [ ] **Fase 2 – Itens ao vivo:** recomendação situacional vs. composição inimiga durante a partida
- [ ] **Synergy real (Stratz)** e **explicação por IA** dos picks
- [ ] Alertas de timing (Roshan, BKB inimigo)

## Notas técnicas

- **Token GSI:** `copiloto-dota-secret` (igual no `.cfg` e no `server.py`).
- **Counters:** OpenDota `/heroes/{id}/matchups`, cacheado 1x → sugestões instantâneas, sem rede em partida.
- **Sem dependências externas obrigatórias:** o core usa só a stdlib do Python; `anthropic` só é
  necessário para o cérebro Claude.
