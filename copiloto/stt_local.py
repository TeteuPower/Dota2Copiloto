"""
Transcricao LOCAL (ouvir o que voce fala): gerada no proprio PC, sem API.
========================================================================

Motor: whisper.cpp (github.com/ggml-org/whisper.cpp), build SO-CPU para Windows
(whisper-bin-x64.zip: sem CUDA - nao disputa a placa de video com o jogo).
Modelos oficiais (huggingface.co/ggerganov/whisper.cpp): 'base' (148 MB, rapido,
padrao) ou 'small' (488 MB, entende melhor, mais lento).

Igual a voz: baixa UMA vez, sozinho, em %LOCALAPPDATA%/CopilotoDota2/engines/whisper
e roda com o orcamento de CPU do momento (copiloto/perf.py + limiter.py).
Um prompt com o vocabulario do Dota ajuda a acertar nomes (Roshan, BKB, Lion...).
"""

import io
import os
import tempfile
import threading
import urllib.request
import wave
import zipfile

from copiloto import config, limiter, perf

WHISPER_URL = ("https://github.com/ggml-org/whisper.cpp/releases/download/b5454/"
               "whisper-bin-x64.zip")
MODELS_BASE = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main"
MODELS = {
    "base": ("Base \u2014 r\u00e1pido (148 MB) \u00b7 padr\u00e3o", "ggml-base.bin"),
    "small": ("Small \u2014 entende melhor, mais lento (488 MB)", "ggml-small.bin"),
}
DEFAULT_MODEL = "base"
CPU_MODE = "auto"

WHISPER_DIR = config.DATA_DIR / "engines" / "whisper"
CLI_EXE = WHISPER_DIR / ("whisper-cli.exe" if os.name == "nt" else "whisper-cli")

# Vocabulario do jogo: o Whisper tende a escrever "Leao"/"Ruchana" sem isso.
DOTA_PROMPT = ("Conversa sobre Dota 2 em portugues. Roshan, Aegis, BKB, Blink, Lion, Pudge, Invoker, "
               "Anti-Mage, Phantom Assassin, Juggernaut, Sniper, Axe, Lina, Crystal Maiden, ward, "
               "sentry, smoke, gank, farm, push, tormentor, tower, rune, glimmer, linken, mid, safe, off.")

STATE = {"phase": "idle", "progress": 0.0, "what": "", "error": None}
_lock = threading.Lock()


def model_path(model):
    return WHISPER_DIR / MODELS.get(model, MODELS[DEFAULT_MODEL])[1]


def ready(model=DEFAULT_MODEL):
    return CLI_EXE.exists() and model_path(model).exists()


def _download(url, dst, what):
    STATE.update(what=what, progress=0.0)
    dst.parent.mkdir(parents=True, exist_ok=True)
    part = dst.with_name(dst.name + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": "CopilotoDota2"})
    with urllib.request.urlopen(req, timeout=60) as r, open(part, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        while True:
            chunk = r.read(1024 * 512)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if total:
                STATE["progress"] = round(min(done / total, 1.0), 3)
    os.replace(part, dst)


def install(model=DEFAULT_MODEL):
    if model not in MODELS:
        model = DEFAULT_MODEL
    with _lock:
        if ready(model):
            STATE.update(phase="pronto", error=None, what="")
            return True
        if os.name != "nt" and not CLI_EXE.exists():
            STATE.update(phase="erro", error="transcricao local disponivel so no Windows")
            return False
        STATE.update(phase="baixando", error=None)
        try:
            if not CLI_EXE.exists():
                z = WHISPER_DIR / "whisper.zip"
                _download(WHISPER_URL, z, "motor de transcri\u00e7\u00e3o")
                with zipfile.ZipFile(z) as zf:     # so o necessario: cli + dlls (achatado)
                    for n in zf.namelist():
                        base = n.rsplit("/", 1)[-1].lower()
                        if base == "whisper-cli.exe" or (base.endswith(".dll") and not base.startswith("sdl")
                                                         and "parakeet" not in base and "llama" not in base):
                            (WHISPER_DIR / n.rsplit("/", 1)[-1]).write_bytes(zf.read(n))
                z.unlink(missing_ok=True)
                if not CLI_EXE.exists():
                    raise RuntimeError("pacote sem whisper-cli.exe")
            if not model_path(model).exists():
                _download(f"{MODELS_BASE}/{MODELS[model][1]}", model_path(model), "modelo " + model)
            STATE.update(phase="pronto", progress=1.0, what="")
            print(f"[OUVIR] transcricao local pronta ({model})")
            return True
        except Exception as e:
            STATE.update(phase="erro", error=f"falha ao baixar a transcri\u00e7\u00e3o local ({str(e)[:90]})")
            print(f"[OUVIR] {STATE['error']}")
            return False


def install_bg(model=DEFAULT_MODEL):
    if STATE["phase"] != "baixando":
        threading.Thread(target=install, args=(model,), daemon=True).start()


def _to_16k_wav(wav_bytes):
    """Qualquer WAV 16-bit -> WAV mono 16 kHz (o whisper.cpp so aceita esse)."""
    import numpy as np
    with wave.open(io.BytesIO(wav_bytes)) as w:
        sr, ch = w.getframerate(), w.getnchannels()
        a = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    if ch > 1:
        a = a.reshape(-1, ch).mean(axis=1).astype(np.int16)
    if sr != 16000 and a.size:
        n = int(round(a.size * 16000 / sr))
        a = np.interp(np.linspace(0, a.size - 1, n), np.arange(a.size), a.astype(np.float32)).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as o:
        o.setnchannels(1)
        o.setsampwidth(2)
        o.setframerate(16000)
        o.writeframes(a.tobytes())
    return buf.getvalue()


def transcribe(wav_bytes, model=DEFAULT_MODEL):
    """WAV (bytes) -> texto em portugues, no proprio PC."""
    if not ready(model) and not install(model):
        raise RuntimeError(STATE.get("error") or "transcricao local indisponivel")
    p = perf.plan(CPU_MODE)
    fd, path = tempfile.mkstemp(prefix="ouvir_", suffix=".wav", dir=str(config.RUNTIME_DIR))
    os.close(fd)
    try:
        with open(path, "wb") as f:
            f.write(_to_16k_wav(wav_bytes))
        cmd = [str(CLI_EXE), "-m", str(model_path(model)), "-f", path, "-l", "pt",
               "-t", str(max(1, p["threads"])), "-nt", "-np", "--prompt", DOTA_PROMPT]
        if p["moment"] == "partida":
            cmd += ["-bs", "1", "-bo", "1"]          # busca gulosa: bem mais leve em partida
        print(f"[OUVIR] transcrevendo ({p['reason']})")
        r = limiter.run(cmd, p["percent"] or 0, affinity=p["mask"], timeout=180, cwd=str(WHISPER_DIR))
        if r.returncode != 0:
            raise RuntimeError((r.stderr or b"").decode("utf-8", "replace").strip()[-160:] or "falhou")
        text = (r.stdout or b"").decode("utf-8", "replace")
        return " ".join(l.strip() for l in text.splitlines() if l.strip() and not l.strip().startswith("[")).strip()
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def public(model=DEFAULT_MODEL):
    return {
        "ready": ready(model),
        "phase": STATE["phase"] if STATE["phase"] in ("baixando", "erro") else ("pronto" if ready(model) else "idle"),
        "progress": STATE["progress"], "what": STATE["what"], "error": STATE["error"],
        "models": [[k, v[0]] for k, v in MODELS.items()],
    }
