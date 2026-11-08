"""
Voz LOCAL do copiloto: gerada no proprio PC, sem API e sem custo.
=================================================================

Motor: Piper (github.com/rhasspy/piper) - voz neural que roda na CPU, offline,
rapida (frase curta em ~0,5 s). Vozes pt-BR do catalogo oficial
(huggingface.co/rhasspy/piper-voices).

Como o Codex, o motor NAO vai dentro do instalador (deixaria cada atualizacao
~80 MB maior): o app baixa UMA vez, sozinho, em segundo plano no primeiro boot
(%LOCALAPPDATA%/CopilotoDota2/engines/piper) - sobrevive as atualizacoes.
Depois disso funciona sem internet.

A fala e tocada em STREAMING: o Piper solta o audio frase a frase e a gente ja
toca a primeira enquanto gera as seguintes (relatorio longo comeca a falar na hora).
"""

import json
import os
import subprocess
import threading
import time
import urllib.request
import zipfile

from copiloto import config, limiter, perf

CPU_MODE = "auto"     # 'auto' (inteligente) | 'leve' | 'livre' - definido pelo voice.py

PIPER_URL = ("https://github.com/rhasspy/piper/releases/download/2023.11.14-2/"
             "piper_windows_amd64.zip")
VOICES_BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/main/pt/pt_BR"

# id -> (rotulo, caminho no catalogo)
VOICES = {
    "pt_BR-faber-medium": ("Faber (masculina, clara) \u00b7 padr\u00e3o", "faber/medium"),
    "pt_BR-cadu-medium": ("Cadu (masculina, jovem)", "cadu/medium"),
    "pt_BR-jeff-medium": ("Jeff (masculina, grave)", "jeff/medium"),
    "pt_BR-edresson-low": ("Edresson (masculina, mais leve)", "edresson/low"),
}
DEFAULT_VOICE = "pt_BR-faber-medium"

PIPER_DIR = config.DATA_DIR / "engines" / "piper"
VOICES_DIR = PIPER_DIR / "voices"
PIPER_EXE = PIPER_DIR / "piper" / ("piper.exe" if os.name == "nt" else "piper")

STATE = {"phase": "idle", "progress": 0.0, "what": "", "error": None}   # idle|baixando|pronto|erro
_lock = threading.Lock()


def _voice_files(voice):
    return VOICES_DIR / f"{voice}.onnx", VOICES_DIR / f"{voice}.onnx.json"


def engine_installed():
    return PIPER_EXE.exists()


def voice_installed(voice):
    m, j = _voice_files(voice)
    return m.exists() and j.exists()


def ready(voice=DEFAULT_VOICE):
    return engine_installed() and voice_installed(voice)


def _download(url, dst, what):
    """Baixa com progresso em STATE; grava em .part e troca no fim."""
    STATE.update(what=what, progress=0.0)
    dst.parent.mkdir(parents=True, exist_ok=True)
    part = dst.with_name(dst.name + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": "CopilotoDota2"})
    with urllib.request.urlopen(req, timeout=60) as r, open(part, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        while True:
            chunk = r.read(1024 * 256)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if total:
                STATE["progress"] = round(min(done / total, 1.0), 3)
    os.replace(part, dst)


def install(voice=DEFAULT_VOICE):
    """Garante o motor + a voz pedida (baixa so o que falta). Bloqueante."""
    if voice not in VOICES:
        voice = DEFAULT_VOICE
    with _lock:
        if ready(voice):
            STATE.update(phase="pronto", error=None, what="")
            return True
        if os.name != "nt" and not engine_installed():
            STATE.update(phase="erro", error="voz local disponivel so no Windows")
            return False
        STATE.update(phase="baixando", error=None)
        try:
            if not engine_installed():
                z = PIPER_DIR / "piper_windows.zip"
                _download(PIPER_URL, z, "motor de voz")
                with zipfile.ZipFile(z) as zf:
                    zf.extractall(PIPER_DIR)
                z.unlink(missing_ok=True)
                if not engine_installed():
                    raise RuntimeError("pacote do Piper sem piper.exe")
            if not voice_installed(voice):
                path = VOICES[voice][1]
                m, j = _voice_files(voice)
                _download(f"{VOICES_BASE}/{path}/{voice}.onnx.json", j, "voz (config)")
                _download(f"{VOICES_BASE}/{path}/{voice}.onnx", m, "voz " + voice.split("-")[1])
            STATE.update(phase="pronto", progress=1.0, what="")
            print(f"[VOZ] voz local pronta ({voice})")
            return True
        except Exception as e:
            STATE.update(phase="erro", error=f"falha ao baixar a voz local ({str(e)[:90]})")
            print(f"[VOZ] {STATE['error']}")
            return False


def install_bg(voice=DEFAULT_VOICE):
    if STATE["phase"] == "baixando":
        return
    threading.Thread(target=install, args=(voice,), daemon=True).start()


def sample_rate(voice):
    try:
        return int(json.loads(_voice_files(voice)[1].read_text(encoding="utf-8"))["audio"]["sample_rate"])
    except Exception:
        return 22050


def _cmd(voice, speed):
    try:
        speed = float(speed)
    except (TypeError, ValueError):
        speed = 1.0
    speed = max(0.6, min(1.8, speed))
    return [str(PIPER_EXE), "--model", str(_voice_files(voice)[0]),
            "--output_raw", "--length_scale", f"{1.0 / speed:.3f}", "--sentence_silence", "0.15"]


def _popen(voice, speed):
    """Piper com o orcamento de CPU do momento (perf.plan): nucleos escolhidos,
    teto duro e prioridade baixa - pra nao roubar FPS do jogo."""
    p = perf.plan(CPU_MODE)
    print(f"[VOZ] gerando ({p['reason']})")
    return limiter.popen(_cmd(voice, speed), p["percent"] or 0, affinity=p["mask"],
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.DEVNULL, cwd=str(PIPER_EXE.parent))


def synthesize(text, voice=DEFAULT_VOICE, speed=1.0):
    """Texto -> (pcm int16 mono, sample_rate). Bloqueante (nao toca)."""
    if not ready(voice) and not install(voice):
        raise RuntimeError(STATE.get("error") or "voz local indisponivel")
    p = _popen(voice, speed)
    try:
        out, _ = p.communicate((text.replace("\n", " ") + "\n").encode("utf-8"), timeout=300)
    finally:
        p.close_job()
    return out, sample_rate(voice)


def speak_stream(text, voice=DEFAULT_VOICE, speed=1.0):
    """Gera e TOCA ao mesmo tempo (streaming). Bloqueante ate terminar de falar."""
    if not ready(voice) and not install(voice):
        raise RuntimeError(STATE.get("error") or "voz local indisponivel")
    import sounddevice as sd
    sr = sample_rate(voice)
    p = _popen(voice, speed)

    def _feed():
        try:
            # uma frase por linha: o Piper sintetiza e solta cada uma assim que fica pronta
            p.stdin.write((text.replace("\n", " ") + "\n").encode("utf-8"))
            p.stdin.close()
        except Exception:
            pass
    threading.Thread(target=_feed, daemon=True).start()

    t0 = time.time()
    with sd.RawOutputStream(samplerate=sr, channels=1, dtype="int16") as stream:
        leftover = b""
        while True:
            chunk = p.stdout.read(4096)
            if not chunk:
                break
            chunk = leftover + chunk
            cut = len(chunk) - (len(chunk) % 2)       # amostras int16 inteiras
            leftover = chunk[cut:]
            stream.write(chunk[:cut])
            if time.time() - t0 > 600:
                p.kill()
                break
    try:
        p.wait(timeout=5)
    except subprocess.TimeoutExpired:
        p.kill()
    p.close_job()


def public(voice=DEFAULT_VOICE):
    return {
        "engine_installed": engine_installed(),
        "voice_installed": voice_installed(voice),
        "ready": ready(voice),
        "phase": STATE["phase"] if STATE["phase"] in ("baixando", "erro") else ("pronto" if ready(voice) else "idle"),
        "progress": STATE["progress"],
        "what": STATE["what"],
        "error": STATE["error"],
        "voices": [[k, v[0]] for k, v in VOICES.items()],
    }

