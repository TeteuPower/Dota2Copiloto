"""
Motores de IA pela ASSINATURA do usuario: Claude e Codex (conta ChatGPT).
=========================================================================

Um lugar so para "como falar com o Claude/Codex", usado pelo chat (brain.py) e
pelas leituras de tela (placar e draft). O app fica auto-suficiente:

  - CLAUDE: o executavel `claude.exe` ja vem DENTRO do app (empacotado pelo
    claude-agent-sdk em _bundled/). Nao precisa de Node, npm nem PATH.
    Credencial, em ordem: o TOKEN colado em Configuracoes (gerado com
    `claude setup-token`, vai como CLAUDE_CODE_OAUTH_TOKEN so pro processo do
    Claude) -> o login que ja exista no PC (~/.claude/.credentials.json).

  - CODEX: o `codex.exe` oficial (github.com/openai/codex) e baixado UMA vez pelo
    proprio app na primeira conexao (fica em %LOCALAPPDATA%/CopilotoDota2/engines,
    sobrevive as atualizacoes). Login com a conta do ChatGPT pelo navegador
    (`codex login`) ou com chave da OpenAI. Respostas via `codex exec`.

Nada aqui usa variavel de ambiente global: o token/chave so entram no processo
filho de cada motor (ANTHROPIC_API_KEY no ambiente sequestraria o login).
"""

import asyncio
import base64
import io
import json
import os
import platform
import queue
import re
import shutil
import subprocess
import tarfile
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

from copiloto import config

# ----------------------------------------------------------------------------
# Estado configurado pelo servidor (ai_config.json)
# ----------------------------------------------------------------------------
_CLAUDE_TOKEN = ""
PREFERRED = "auto"          # provedor escolhido em Configuracoes
ON_CHANGE = None            # callback: credenciais mudaram -> servidor recria o cerebro

ENGINES_DIR = config.DATA_DIR / "engines"
CODEX_DIR = ENGINES_DIR / "codex"
CODEX_EXE = CODEX_DIR / ("codex.exe" if os.name == "nt" else "codex")

CLAUDE_TIMEOUT = 120
CODEX_TIMEOUT = 180


def set_claude_token(tok):
    global _CLAUDE_TOKEN
    _CLAUDE_TOKEN = (tok or "").strip()


def set_preferred(pref):
    global PREFERRED
    PREFERRED = (pref or "auto").lower()


# Modelo escolhido na UI (vazio = o padrao da conta/CLI)
MODELS = {"claude": "", "codex": ""}
CLAUDE_MODEL_CHOICES = [
    ("", "Padrão da sua conta"),
    ("haiku", "Haiku — o mais rápido"),
    ("sonnet", "Sonnet — equilíbrio (recomendado)"),
    ("opus", "Opus — o mais inteligente, mais lento"),
]
CODEX_MODEL_SUGGESTIONS = ["gpt-5", "gpt-5-codex", "gpt-5-mini"]


def set_models(claude=None, codex=None):
    if claude is not None:
        MODELS["claude"] = (claude or "").strip()
    if codex is not None:
        MODELS["codex"] = (codex or "").strip()


def _changed():
    cb = ON_CHANGE
    if cb:
        try:
            cb()
        except Exception as e:
            print(f"[IA] erro ao recarregar o cerebro: {e}")


def _drain(_line):
    """stderr do 'claude': registrar um callback faz o SDK usar PIPE. Sem isso o
    processo herda o stderr INVALIDO do app sem console e pode travar."""
    pass


# ----------------------------------------------------------------------------
# CLAUDE (claude.exe embutido no SDK)
# ----------------------------------------------------------------------------
def claude_cli_path():
    """O executavel do Claude: o EMBUTIDO no SDK (sempre presente no app
    instalado). So cai num 'claude.exe' do PATH se o embutido faltar."""
    try:
        import claude_agent_sdk
        name = "claude.exe" if os.name == "nt" else "claude"
        p = Path(claude_agent_sdk.__file__).resolve().parent / "_bundled" / name
        if p.exists():
            return str(p)
    except Exception:
        return None
    w = shutil.which("claude.exe") if os.name == "nt" else shutil.which("claude")
    return w


def _claude_config_dir():
    d = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(d) if d else Path.home() / ".claude"


def claude_login_found():
    """Login do Claude Code ja feito neste PC (ex.: quem usa o CLI)."""
    try:
        return (_claude_config_dir() / ".credentials.json").exists()
    except Exception:
        return False


def claude_ready():
    """Da pra usar o Claude? (executavel + alguma credencial)."""
    if not claude_cli_path():
        return False
    return bool(_CLAUDE_TOKEN) or bool(os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")) or claude_login_found()


def claude_env():
    env = {"ANTHROPIC_API_KEY": ""}   # nunca deixa uma chave do ambiente sequestrar a assinatura
    if _CLAUDE_TOKEN:
        env["CLAUDE_CODE_OAUTH_TOKEN"] = _CLAUDE_TOKEN
    return env


def claude_options(**kw):
    """ClaudeAgentOptions ja com: o executavel embutido, o token, stderr em PIPE
    e buffer grande (imagem inline estourava o limite de 1 MB do SDK)."""
    from claude_agent_sdk import ClaudeAgentOptions
    kw.setdefault("stderr", _drain)
    kw.setdefault("max_buffer_size", 64 * 1024 * 1024)
    env = dict(kw.pop("env", {}) or {})
    env.update(claude_env())
    kw["env"] = env
    cli = claude_cli_path()
    if cli:
        kw.setdefault("cli_path", cli)
    if MODELS["claude"]:
        kw.setdefault("model", MODELS["claude"])
    return ClaudeAgentOptions(**kw)


async def _claude_collect(prompt, opts):
    from claude_agent_sdk import query
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
            if getattr(msg, "is_error", False) and not out:
                raise RuntimeError(r or "o Claude devolveu erro")
    return "\n".join(out).strip()


def claude_ask(system, prompt, image=None, timeout=CLAUDE_TIMEOUT, max_turns=1):
    """Pergunta ao Claude (texto, opcionalmente com 1 imagem INLINE). -> texto."""
    if image:
        data, mime = _image_for_upload(image)
        b64 = base64.b64encode(data).decode("ascii")

        async def _gen():
            yield {"type": "user", "message": {"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image", "source": {"type": "base64", "media_type": mime, "data": b64}},
            ]}}
        p = _gen()
        turns = max(max_turns, 2)
    else:
        p = prompt
        turns = max_turns
    opts = claude_options(system_prompt=system, allowed_tools=[], max_turns=turns)

    async def _run():
        return await asyncio.wait_for(_claude_collect(p, opts), timeout=timeout)
    return asyncio.run(_run())


def claude_stream(system, prompt, timeout=CLAUDE_TIMEOUT):
    """Resposta do Claude EM PEDACOS, conforme ele escreve (gerador de texto).
    E o que deixa a voz comecar a falar a 1a frase antes da resposta acabar."""
    q = queue.Queue()
    end = object()

    def _worker():
        async def _go():
            from claude_agent_sdk import query
            opts = claude_options(system_prompt=system, allowed_tools=[], max_turns=1,
                                  include_partial_messages=True)
            got = False
            async for msg in query(prompt=prompt, options=opts):
                cls = type(msg).__name__
                if cls == "StreamEvent":
                    ev = getattr(msg, "event", None) or {}
                    d = ev.get("delta") or {}
                    if ev.get("type") == "content_block_delta" and d.get("type") == "text_delta":
                        if d.get("text"):
                            q.put(d["text"])
                            got = True
                elif cls == "AssistantMessage" and not got:      # sem eventos parciais
                    for b in getattr(msg, "content", None) or []:
                        t = getattr(b, "text", None)
                        if t:
                            q.put(t)
                            got = True
                elif cls == "ResultMessage" and not got:
                    r = getattr(msg, "result", None)
                    if getattr(msg, "is_error", False):
                        raise RuntimeError(r or "o Claude devolveu erro")
                    if isinstance(r, str) and r:
                        q.put(r)
                        got = True
        try:
            asyncio.run(asyncio.wait_for(_go(), timeout=timeout))
        except Exception as e:
            q.put(e)
        finally:
            q.put(end)

    threading.Thread(target=_worker, daemon=True).start()
    while True:
        x = q.get()
        if x is end:
            return
        if isinstance(x, Exception):
            raise x
        yield x


class ClaudeSession:
    """Uma sessao do Claude ABERTA durante a partida inteira (ClaudeSDKClient).

    Abrir o claude.exe custa segundos a cada pergunta; aqui ele abre UMA vez por
    partida e cada pergunta seguinte ja chega num processo quente - e o proprio
    Claude guarda a conversa (memoria da partida). Roda num loop asyncio proprio,
    numa thread; ask_stream() e sincrono e devolve a resposta em pedacos.
    Chave da sessao = (partida, token, modelo): mudou -> fecha e abre outra."""

    def __init__(self):
        self._loop = None
        self._client = None
        self.key = None
        self.turns = 0
        self._lock = threading.Lock()

    def _ensure_loop(self):
        if self._loop is None:
            self._loop = asyncio.new_event_loop()
            threading.Thread(target=self._loop.run_forever, daemon=True, name="claude-sessao").start()

    def _run(self, coro, timeout):
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout)

    async def _open(self, system):
        from claude_agent_sdk import ClaudeSDKClient
        opts = claude_options(system_prompt=system, allowed_tools=[], include_partial_messages=True)
        c = ClaudeSDKClient(options=opts)
        await c.connect()
        return c

    async def _close(self):
        c, self._client = self._client, None
        if c is not None:
            try:
                await c.disconnect()
            except Exception:
                pass

    def close(self):
        with self._lock:
            if self._loop is not None and self._client is not None:
                try:
                    self._run(self._close(), 15)
                except Exception:
                    self._client = None
            self.key, self.turns = None, 0

    def is_open_for(self, key):
        return self._client is not None and self.key == key

    def ask_stream(self, key, system, prompt, timeout=CLAUDE_TIMEOUT):
        """Pergunta na sessao `key` (abre/reabre se precisar). Gerador de texto."""
        with self._lock:
            self._ensure_loop()
            if self.key != key or self._client is None:
                if self._client is not None:
                    try:
                        self._run(self._close(), 15)
                    except Exception:
                        self._client = None
                t0 = time.time()
                self._client = self._run(self._open(system), 60)
                self.key, self.turns = key, 0
                print(f"[IA] sessao do Claude aberta para a partida ({time.time() - t0:.1f}s)")
            q = queue.Queue()
            end = object()
            client = self._client

            async def _go():
                try:
                    await client.query(prompt)
                    got = False
                    async for msg in client.receive_response():
                        cls = type(msg).__name__
                        if cls == "StreamEvent":
                            ev = getattr(msg, "event", None) or {}
                            d = ev.get("delta") or {}
                            if ev.get("type") == "content_block_delta" and d.get("type") == "text_delta" \
                                    and d.get("text"):
                                q.put(d["text"])
                                got = True
                        elif cls == "AssistantMessage" and not got:
                            for b in getattr(msg, "content", None) or []:
                                t = getattr(b, "text", None)
                                if t:
                                    q.put(t)
                                    got = True
                        elif cls == "ResultMessage":
                            if getattr(msg, "is_error", False) and not got:
                                raise RuntimeError(getattr(msg, "result", None) or "o Claude devolveu erro")
                except Exception as e:
                    q.put(e)
                finally:
                    q.put(end)

            fut = asyncio.run_coroutine_threadsafe(asyncio.wait_for(_go(), timeout), self._loop)
            failed = False
            try:
                while True:
                    try:
                        x = q.get(timeout=timeout + 5)
                    except queue.Empty:
                        raise TimeoutError("o Claude nao respondeu a tempo")
                    if x is end:
                        break
                    if isinstance(x, Exception):
                        raise x
                    yield x
                self.turns += 1
            except BaseException:
                failed = True
                raise
            finally:
                if failed or fut.cancelled():
                    # sessao em estado incerto: descarta (a proxima pergunta abre outra)
                    try:
                        self._run(self._close(), 15)
                    except Exception:
                        self._client = None
                    self.key = None


SESSION = ClaudeSession()


def open_claude_setup_token():
    """Abre uma janela de terminal rodando `claude setup-token` (o executavel
    EMBUTIDO): o navegador abre, a pessoa entra na conta do Claude e o token
    aparece na janela pra ela copiar e colar em Configuracoes."""
    cli = claude_cli_path()
    if not cli:
        raise RuntimeError("o Claude embutido nao foi encontrado nesta instalacao")
    if os.name != "nt":
        raise RuntimeError("disponivel so no Windows")
    CREATE_NEW_CONSOLE = 0x00000010
    title = "Copiloto Dota 2 - gerar token do Claude"
    msg = ("echo. & echo  Entre na sua conta do Claude no navegador que vai abrir. & "
           "echo  No final, COPIE o token (comeca com sk-ant-oat) e cole no painel do Copiloto. & echo.")
    env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "CLAUDECODE")}
    subprocess.Popen(
        ["cmd.exe", "/k", f'title {title} & {msg} & "{cli}" setup-token'],
        creationflags=CREATE_NEW_CONSOLE, env=env, close_fds=True)


# ----------------------------------------------------------------------------
# CODEX (conta ChatGPT)
# ----------------------------------------------------------------------------
CODEX_STATE = {
    "phase": "idle",       # idle | baixando | instalado | entrando | ok | erro
    "progress": 0.0,       # 0..1 do download
    "url": None,           # link de login (se o navegador nao abrir sozinho)
    "error": None,
    "logged": None,        # cache de `codex login status`
    "account": "",
    "checked_at": 0.0,
}
_CODEX_LOCK = threading.Lock()
_LOGIN_PROC = None


def codex_path():
    if CODEX_EXE.exists():
        return str(CODEX_EXE)
    bundled = config.RESOURCE_DIR / "bin" / CODEX_EXE.name
    if bundled.exists():
        return str(bundled)
    if os.name == "nt":
        return shutil.which("codex.exe")     # evita o shim .cmd do npm
    return shutil.which("codex")


def _codex_asset_name():
    arch = (platform.machine() or "").lower()
    triple = "aarch64-pc-windows-msvc" if arch in ("arm64", "aarch64") else "x86_64-pc-windows-msvc"
    return f"codex-{triple}.exe.tar.gz"


def _http_json(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": "CopilotoDota2",
                                               "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def install_codex():
    """Baixa o codex.exe oficial do ultimo release do GitHub (openai/codex) e
    extrai em engines/codex. Atualiza CODEX_STATE com o progresso."""
    if os.name != "nt":
        raise RuntimeError("o download automatico do Codex e so pro Windows")
    CODEX_STATE.update(phase="baixando", progress=0.0, error=None)
    rel = _http_json("https://api.github.com/repos/openai/codex/releases/latest")
    want = _codex_asset_name()
    asset = next((a for a in rel.get("assets") or [] if a.get("name") == want), None)
    if not asset:
        raise RuntimeError(f"o release do Codex nao tem {want}")
    total = int(asset.get("size") or 0)
    CODEX_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CODEX_DIR / (want + ".part")
    req = urllib.request.Request(asset["browser_download_url"], headers={"User-Agent": "CopilotoDota2"})
    with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
        done = 0
        while True:
            chunk = r.read(1024 * 256)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if total:
                CODEX_STATE["progress"] = min(done / total, 1.0)
    with tarfile.open(tmp, "r:gz") as tf:
        member = next((m for m in tf.getmembers() if m.isfile() and m.name.lower().endswith(".exe")), None)
        if member is None:
            raise RuntimeError("pacote do Codex sem executavel")
        src = tf.extractfile(member)
        out = CODEX_EXE.with_suffix(".new")
        with open(out, "wb") as f:
            shutil.copyfileobj(src, f)
    os.replace(out, CODEX_EXE)
    tmp.unlink(missing_ok=True)
    (CODEX_DIR / "versao.txt").write_text(str(rel.get("tag_name") or ""), encoding="utf-8")
    CODEX_STATE.update(phase="instalado", progress=1.0)
    print(f"[CODEX] instalado {rel.get('tag_name')} em {CODEX_EXE}")


def _codex_run(args, input_text=None, timeout=30):
    exe = codex_path()
    if not exe:
        raise RuntimeError("Codex nao instalado")
    return subprocess.run([exe] + args, input=input_text, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout,
                          cwd=str(config.RUNTIME_DIR), stdin=None if input_text is not None else subprocess.DEVNULL)


def codex_logged_in(force=False):
    """`codex login status` (com cache de 60s). -> bool."""
    if not codex_path():
        CODEX_STATE.update(logged=False, account="")
        return False
    if not force and CODEX_STATE["logged"] is not None and time.time() - CODEX_STATE["checked_at"] < 60:
        return CODEX_STATE["logged"]
    try:
        r = _codex_run(["login", "status"], timeout=20)
        txt = ((r.stdout or "") + (r.stderr or "")).strip()
        ok = r.returncode == 0 and "not logged" not in txt.lower()
        CODEX_STATE.update(logged=ok, account=txt.splitlines()[-1][:120] if (ok and txt) else "")
    except Exception:
        CODEX_STATE.update(logged=False, account="")
    CODEX_STATE["checked_at"] = time.time()
    return CODEX_STATE["logged"]


def codex_ready():
    return bool(codex_path()) and codex_logged_in()


def _login_worker():
    global _LOGIN_PROC
    try:
        if not codex_path():
            install_codex()
        CODEX_STATE.update(phase="entrando", url=None, error=None)
        exe = codex_path()
        _LOGIN_PROC = subprocess.Popen(
            [exe, "login"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            text=True, encoding="utf-8", errors="replace", cwd=str(config.RUNTIME_DIR))
        deadline = time.time() + 600
        for line in _LOGIN_PROC.stdout:
            m = re.search(r"https://\S+", line)
            if m and not CODEX_STATE["url"] and "localhost" not in m.group(0):
                CODEX_STATE["url"] = m.group(0).rstrip(".")
            if time.time() > deadline:
                break
        try:
            rc = _LOGIN_PROC.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _LOGIN_PROC.kill()
            rc = -1
        if rc == 0 and codex_logged_in(force=True):
            CODEX_STATE.update(phase="ok", url=None)
            print("[CODEX] login concluido")
            _changed()
        else:
            CODEX_STATE.update(phase="erro", error="o login nao foi concluido (tente de novo)")
    except Exception as e:
        CODEX_STATE.update(phase="erro", error=str(e)[:160])
        print(f"[CODEX] falha: {e}")
    finally:
        _LOGIN_PROC = None


def codex_login_start():
    """Inicia (em background) o login do Codex com a conta do ChatGPT. Baixa o
    Codex antes se ainda nao estiver instalado. O navegador abre sozinho."""
    with _CODEX_LOCK:
        if CODEX_STATE["phase"] in ("baixando", "entrando"):
            return
        CODEX_STATE.update(phase="baixando" if not codex_path() else "entrando", error=None, url=None)
        threading.Thread(target=_login_worker, daemon=True).start()


def codex_login_cancel():
    p = _LOGIN_PROC
    if p:
        try:
            p.kill()
        except Exception:
            pass


def codex_login_apikey(key):
    """Login do Codex com chave da OpenAI (cobranca por uso)."""
    if not codex_path():
        install_codex()
    r = _codex_run(["login", "--with-api-key"], input_text=key.strip() + "\n", timeout=30)
    if r.returncode != 0:
        raise RuntimeError(((r.stderr or r.stdout) or "falhou").strip()[:160])
    codex_logged_in(force=True)
    CODEX_STATE.update(phase="ok", error=None)
    _changed()


def codex_logout():
    if codex_path():
        try:
            _codex_run(["logout"], timeout=20)
        except Exception:
            pass
    CODEX_STATE.update(logged=False, account="", phase="instalado" if codex_path() else "idle",
                       checked_at=time.time())
    _changed()


def codex_ask(system, prompt, image=None, timeout=CODEX_TIMEOUT):
    """Uma resposta do Codex (`codex exec`, sem ferramentas, sandbox so-leitura)."""
    exe = codex_path()
    if not exe:
        raise RuntimeError("Codex nao instalado")
    fd, out_path = tempfile.mkstemp(prefix="codex_", suffix=".txt", dir=str(config.RUNTIME_DIR))
    os.close(fd)
    img_path = None
    try:
        cmd = [exe, "exec", "--skip-git-repo-check", "--ephemeral", "--sandbox", "read-only",
               "--color", "never", "-c", 'model_reasoning_effort="low"', "-o", out_path]
        if MODELS["codex"]:
            cmd += ["-m", MODELS["codex"]]
        if image:
            data, mime = _image_for_upload(image)
            img_path = os.path.join(str(config.RUNTIME_DIR), "codex_img" + (".jpg" if "jpeg" in mime else ".png"))
            with open(img_path, "wb") as f:
                f.write(data)
            cmd += ["--image", img_path]          # sem prompt posicional: o prompt vem pelo stdin
        full = (system.strip() + "\n\n" + prompt) if system else prompt
        full += "\n\n(Responda direto, sem rodar comandos nem abrir arquivos.)"
        r = subprocess.run(cmd, input=full, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout, cwd=str(config.RUNTIME_DIR))
        text = ""
        try:
            text = Path(out_path).read_text(encoding="utf-8").strip()
        except Exception:
            pass
        if r.returncode != 0 and not text:
            err = (r.stderr or r.stdout or "").strip().splitlines()
            msg = next((l for l in reversed(err) if l.strip()), "falhou")
            if "login" in msg.lower() or "auth" in msg.lower():
                CODEX_STATE.update(logged=None)
            raise RuntimeError(msg[:200])
        return text or (r.stdout or "").strip()
    finally:
        for p in (out_path,):
            try:
                os.unlink(p)
            except OSError:
                pass


# ----------------------------------------------------------------------------
# Comum
# ----------------------------------------------------------------------------
def _image_for_upload(path, max_w=1920):
    """Reduz a captura (4K PNG = varios MB) para JPEG ate 1920px: bem mais rapido
    de enviar e longe do limite de buffer. -> (bytes, mime)."""
    try:
        from PIL import Image
        im = Image.open(path).convert("RGB")
        if im.width > max_w:
            im = im.resize((max_w, round(im.height * max_w / im.width)), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=88)
        return buf.getvalue(), "image/jpeg"
    except Exception:
        with open(path, "rb") as f:
            return f.read(), "image/png"


def available_engines():
    """Motores de assinatura prontos, na ordem de preferencia."""
    order = ["codex", "claude"] if PREFERRED == "codex" else ["claude", "codex"]
    ok = {"claude": claude_ready, "codex": codex_ready}
    return [e for e in order if ok[e]()]


def ask(engine, system, prompt, image=None, timeout=None):
    if engine == "claude":
        return claude_ask(system, prompt, image=image, timeout=timeout or CLAUDE_TIMEOUT)
    if engine == "codex":
        return codex_ask(system, prompt, image=image, timeout=timeout or CODEX_TIMEOUT)
    raise ValueError(engine)


def status():
    """Resumo pro painel (sem segredos)."""
    cp = codex_path()
    logged = codex_logged_in() if cp else False
    phase = CODEX_STATE["phase"]
    if phase not in ("baixando", "entrando"):
        if logged:
            phase = "ok"
        elif phase != "erro":
            phase = "instalado" if cp else "idle"
    return {
        "claude": {
            "cli": bool(claude_cli_path()),
            "token": bool(_CLAUDE_TOKEN),
            "login_pc": claude_login_found(),
            "ready": claude_ready(),
        },
        "codex": {
            "installed": bool(cp),
            "logged": bool(logged),
            "account": CODEX_STATE.get("account") or "",
            "phase": phase,
            "progress": round(CODEX_STATE["progress"], 3),
            "url": CODEX_STATE["url"],
            "error": CODEX_STATE["error"],
        },
        "preferred": PREFERRED,
    }
