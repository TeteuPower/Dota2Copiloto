# Copiloto Dota 2

Copiloto de partida: **assistente de draft** (sugere picks por counters + função do time),
**chat com IA** (Claude) que enxerga o estado do jogo, e um **painel ao vivo** via GSI.
Você acompanha tudo no celular ou numa 2ª/3ª tela, sem tirar o jogo da tela principal.

```
  Dota 2 ──(GSI)──►  main.py (pacote copiloto/) ──►  painel web + overlay no jogo
                        │                ├─ Painel ao vivo (herói, gold, itens)
   OpenDota (cache) ────┤                ├─ Assistente de Draft (grid + sugestões)
                        │                ├─ Chat com o copiloto (IA) + voz
   Claude (assinatura) ─┘                └─ Overlay do minimapa (fantasma de inimigos)
```

## Estrutura do projeto

```
dota2/
├── main.py                  # ponto de entrada: python main.py
├── iniciar.bat              # atalho do Windows (2 cliques)
├── requirements.txt         # dependências (pip install -r requirements.txt)
├── config/
│   └── gamestate_integration_copiloto.cfg   # copiar pra pasta do Dota (setup)
├── copiloto/                # código-fonte (pacote Python)
│   ├── config.py            # portas, token e caminhos (fonte da verdade)
│   ├── voice.py             # voz: ouvir (Whisper) e falar (TTS OpenAI)
│   ├── ai/brain.py          # cérebros de IA plugáveis (Claude/OpenAI/local/básico)
│   ├── capture/             # captura de tela: monitor do Dota, placar, draft, minimapa
│   ├── game/                # dados do jogo: heróis/counters, itens, histórico
│   ├── overlay/             # overlay sobre o jogo: janela + rastreador do minimapa
│   └── web/                 # servidor GSI + painel (server.py, pages.py)
├── assets/                  # ícone do app/instalador
├── installer/               # script do instalador (Inno Setup)
├── CopilotoDota2.spec       # empacotamento do exe (PyInstaller)
├── scripts/                 # build_installer.ps1, build_cache, utilitários
├── cache/                   # dados baixados da OpenDota (atualizar por patch)
├── runtime/                 # gerados em execução (prints, configs) [não versionado]
└── match_history/           # relatórios por partida [não versionado]
```

## Instalar como programa do Windows

Cada push na `main` gera automaticamente um **Release no GitHub** com o instalador
(`CopilotoDota2-Setup-1.0.N.exe` — a versão sobe sozinha: `N` = número de commits).
Pra gerar localmente: `.\scripts\build_installer.ps1` (requer PyInstaller e Inno Setup).

O instalador (em português) é **auto-suficiente** — num PC zerado nada precisa ser feito à mão:
- instala **por usuário** (`%LOCALAPPDATA%\Programs\CopilotoDota2`, sem pedir administrador) e
  remove a versão antiga de *Arquivos de Programas*, se existir (um UAC, uma vez só);
- o **Claude já vem dentro do app** (`claude.exe` empacotado pelo `claude-agent-sdk`) — nada de
  Node, npm ou PATH;
- **configura o Dota 2** (`CopilotoDota2.exe --provision`): acha o jogo em qualquer biblioteca da
  Steam (`libraryfolders.vdf`), grava o `.cfg` do GSI e põe `-gamestateintegration` nas opções de
  inicialização (no `localconfig.vdf` de cada conta; se a Steam estiver aberta, fecha e reabre);
- opcionalmente **inicia junto com o Windows** (sobe direto pra bandeja, sem abrir o painel).

O app também confere o Dota a cada abertura e mostra o estado em **Configurações → Dota 2**
(com o botão *Configurar agora*).

**Atualização automática** (como o Theus): o app consulta o último Release no boot e a cada 3h,
baixa o Setup novo em segundo plano e instala **silencioso** — sozinho quando está ocioso (sem
partida), ou na hora pelo botão *Atualizar agora* em Configurações. O app fecha e volta na bandeja.

O programa instalado roda **sem console**: ele vive no **ícone da bandeja** (2 cliques abrem o
painel; o menu tem *abrir painel*, *mostrar/esconder overlay* e *desligar*). Logs em
`%LOCALAPPDATA%\CopilotoDota2\logs`. Abrir duas vezes **não** cria uma segunda instância — a
segunda detecta a primeira (mutex do Windows) e só traz o painel.

Os dados do usuário (chave OpenAI, configs do overlay, histórico de partidas) ficam em
`%LOCALAPPDATA%\CopilotoDota2` e **sobrevivem a atualizações e à desinstalação**.
No modo dev (`python main.py`), tudo continua relativo à pasta do repositório — mas **não rode
os dois ao mesmo tempo** (o mutex é global: o segundo a abrir apenas mostra o painel do primeiro).

## Como ligar a aplicação

**Jeito fácil:** dê **dois cliques** no arquivo `iniciar.bat` (abre uma janela preta com o servidor).

**Ou pelo terminal:**
```powershell
python c:\Trabalho\dota2\main.py
```
Depois abra `http://localhost:49317` no navegador (ou o IP mostrado no terminal, pra ver no celular/2ª-3ª tela). Para **desligar**, feche a janela (ou Ctrl+C no terminal).

## Ligar o painel ao vivo (GSI)

Automático (instalador + app, ver acima). No modo dev dá pra rodar o mesmo passo com
`python main.py --provision --fechar-steam`. O que ele faz, caso precise conferir à mão:

1. `.cfg` em `...\dota 2 beta\game\dota\cfg\gamestate_integration\`
2. **Steam → Dota 2 → Propriedades → Opções de Inicialização** com `-gamestateintegration`
3. **Feche e reabra o Dota.** A bolinha do painel fica verde quando os dados chegam.

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
python c:\Trabalho\dota2\scripts\build_cache.py
```

## Cérebro de IA (chat) — Claude ou Codex pela assinatura, SEM API key

Conecte em **Configurações → Conectar a IA** (um dos dois já liga chat, placar e draft; com os
dois, um cobre o outro):

- **Claude** — cole o **token da assinatura** (`sk-ant-oat01-…`). O botão *Gerar token* abre uma
  janela com o `claude setup-token` do Claude **embutido**: você entra na conta no navegador e
  copia o token. O token vai só pro processo do Claude (`CLAUDE_CODE_OAUTH_TOKEN`). Se o PC já tem
  um login do Claude Code (`~/.claude`), ele conecta sozinho.
- **Codex** — *Entrar com ChatGPT*: na primeira vez o app baixa o `codex.exe` oficial
  (github.com/openai/codex, ~120 MB, fica em `%LOCALAPPDATA%\CopilotoDota2\engines`) e abre o login
  da conta do ChatGPT no navegador. Também aceita chave da OpenAI.

No **Automático** a ordem é: **Claude → Codex → chaves de API (Anthropic/OpenAI/Gemini) → Ollama
local → modo básico**. Dá pra forçar um provedor no seletor. Toda a lógica dos motores de
assinatura fica em `copiloto/ai/engines.py`.

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

**Calibração:** o monitor e a resolução do Dota são **detectados automaticamente** (a caixa padrão é
proporcional à tela). Se o seu HUD for diferente, clique na **engrenagem ⚙** da janela grande e ajuste
com as setas/zoom até enquadrar só o minimapa (salva automático; setas do teclado também movem).

## Overlay no jogo (fantasma de inimigos no minimapa)

Com o Dota em **"Tela cheia em janela"** (borderless), o app desenha uma camada transparente por cima
do **minimapa do jogo**: quando um inimigo visível **some na névoa**, fica um **fantasma** na última
posição (bolinha na cor do herói + há quanto tempo sumiu; opcionalmente o retrato). Quando ele
reaparece, o fantasma some. **Tab+F6** liga/desliga; tempo de expiração e estilo em **⚙ Configurações**.

## Voz (ouvir e falar)

**Voz do copiloto gerada no próprio PC** (padrão): motor **Piper** (`copiloto/tts_local.py`), voz
neural pt-BR que roda na CPU, offline e sem custo. Na primeira vez o app baixa o motor (~21 MB) e a
voz escolhida (~60 MB) em segundo plano para `%LOCALAPPDATA%\CopilotoDota2\engines\piper` — fica
fora do instalador e sobrevive às atualizações. A fala toca em streaming (começa na 1ª frase).
Vozes: Faber, Cadu, Jeff e Edresson; velocidade ajustável em Configurações → Voz. A voz da OpenAI
continua como opção. **Ouvir** (transcrever o que você fala, atalho F8) ainda usa a OpenAI (Whisper).

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

- **Token GSI:** `copiloto-dota-secret` (igual no `config/gamestate_integration_copiloto.cfg` e no
  `copiloto/config.py`). Porta e caminhos também ficam em `copiloto/config.py`.
- **Counters:** OpenDota `/heroes/{id}/matchups`, cacheado 1x → sugestões instantâneas, sem rede em partida.
- **Dependências:** `pip install -r requirements.txt` (PySide6, mss, Pillow, numpy, opencv, keyboard,
  pywin32, claude-agent-sdk). O núcleo GSI/painel funciona só com a stdlib.
