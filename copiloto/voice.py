"""
voice.py - Voz do copiloto (mesmo esquema OpenAI do projeto jarvis)
===================================================================

Atalho global "me ouvir": ao apertar a tecla configurada (funciona DENTRO do
jogo), o copiloto:
  1) abaixa o volume do PC (pra captar voz limpa, sem o som do jogo),
  2) toca um BIP avisando que comecou a captacao,
  3) grava o microfone ate voce parar de falar (deteccao de silencio),
  4) transcreve com a OpenAI (Whisper),
  5) manda pro cerebro do copiloto e
  6) FALA a resposta com a voz neural da OpenAI (gpt-4o-mini-tts).

Espelha o esquema do jarvis: STT = whisper-1 (/v1/audio/transcriptions),
TTS = gpt-4o-mini-tts (/v1/audio/speech). A chave da OpenAI fica SERVER-ONLY:
env OPENAI_API_KEY tem precedencia, senao o arquivo `openai_secret.json` ao
lado deste modulo (gitignored). O status NUNCA expoe a chave.

Dependencias (pip): sounddevice, numpy, pycaw, comtypes. winsound/wave/urllib
sao stdlib. Tudo e importado de forma tolerante: faltando uma peca, o recurso
se desliga com uma mensagem clara em vez de derrubar o servidor.
"""

import os
import io
import json
import time
import wave
import threading
import urllib.request

from copiloto import config

# Chave OpenAI: fica na RAIZ do repo (gitignored), fora do pacote de codigo
SECRET_PATH = str(config.SECRET_PATH)

# --- Espelha o esquema do jarvis (mesmos modelos/vozes/instrucao padrao) ---
STT_MODEL = "whisper-1"
TTS_MODEL = "gpt-4o-mini-tts"
TTS_VOICES = ["alloy", "ash", "ballad", "coral", "echo", "fable", "nova",
              "onyx", "sage", "shimmer", "verse", "marin", "cedar"]
TTS_DEFAULT_INSTRUCTIONS = ("Fale em portugues do Brasil num ritmo de conversa "
                            "natural e agil, sem arrastar as palavras.")

DEFAULT_CONFIG = {
    "engine": "openai",   # "openai" = fala a resposta com a OpenAI; "off" = nao fala
    "voice": "coral",
    "instructions": "",
    "hotkey": "f8",        # atalho global "me ouvir"
    "duck": True,          # abaixar o volume do PC ao captar
    "duck_level": 0.2,     # volume durante a captacao (0..1)
    "beep": True,          # bip ao iniciar a captacao
    "speak_report": True,  # falar a analise tatica em voz alta quando o scan terminar
    "mic_index": None,     # indice do microfone escolhido (None = padrao do Windows)
    "mic_name": "",        # nome do mic escolhido (p/ re-achar se o indice mudar)
    "report_engine": "claude",  # "claude" (preciso, lento) PADRAO | "openai" (rapido, pode errar) p/ ler placar + relatorio
}

# Parametros da gravacao (espelham o jarvis: fim por silencio / sem-fala / teto)
SAMPLERATE = 16000
SILENCE_MS = 1400
NOSPEECH_MS = 6000
MAX_MS = 20000
RMS_THRESHOLD = 250     # piso de energia (int16) p/ voz; tambem calibramos o ruido na hora

# Estado ao vivo lido pelo painel (/voice/state). status:
# idle | ouvindo | transcrevendo | pensando | falando | erro
STATE = {"status": "idle", "listening": False, "transcript": "",
         "reply": "", "error": None, "at": 0.0}
_lock = threading.Lock()
_busy = False


def _set(**kw):
    with _lock:
        STATE.update(kw)
        STATE["at"] = time.time()


# ----------------------------------------------------------------------------
# Chave + configuracao (server-only)
# ----------------------------------------------------------------------------
def _read_file():
    try:
        with open(SECRET_PATH, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _write_file(d):
    with open(SECRET_PATH, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)


def get_key():
    """Chave da OpenAI: env OPENAI_API_KEY tem precedencia; senao o arquivo."""
    return (os.environ.get("OPENAI_API_KEY") or "").strip() or (_read_file().get("key") or "").strip()


def is_configured():
    return bool(get_key())


def set_key(key=None, clear=False):
    d = _read_file()
    if clear:
        d.pop("key", None)
    elif key and key.strip():
        d["key"] = key.strip()
    _write_file(d)
    return is_configured()


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    cfg.update((_read_file().get("config") or {}))
    return cfg


def save_config(patch):
    cfg = load_config()
    if isinstance(patch, dict):
        if patch.get("engine") in ("openai", "off"):
            cfg["engine"] = patch["engine"]
        if isinstance(patch.get("voice"), str) and patch["voice"].strip() in TTS_VOICES:
            cfg["voice"] = patch["voice"].strip()
        if isinstance(patch.get("instructions"), str):
            cfg["instructions"] = patch["instructions"].strip()[:4096]
        if isinstance(patch.get("hotkey"), str) and patch["hotkey"].strip():
            cfg["hotkey"] = patch["hotkey"].strip().lower()
        if "duck" in patch:
            cfg["duck"] = bool(patch["duck"])
        if "beep" in patch:
            cfg["beep"] = bool(patch["beep"])
        if "speak_report" in patch:
            cfg["speak_report"] = bool(patch["speak_report"])
        if "mic_index" in patch:
            mi = patch.get("mic_index")
            cfg["mic_index"] = int(mi) if (mi is not None and str(mi).lstrip("-").isdigit()) else None
        if isinstance(patch.get("mic_name"), str):
            cfg["mic_name"] = patch["mic_name"][:120]
        if patch.get("report_engine") in ("openai", "claude"):
            cfg["report_engine"] = patch["report_engine"]
        try:
            if patch.get("duck_level") is not None:
                cfg["duck_level"] = max(0.0, min(1.0, float(patch["duck_level"])))
        except (TypeError, ValueError):
            pass
    d = _read_file()
    d["config"] = cfg
    _write_file(d)
    return cfg


# ----------------------------------------------------------------------------
# OpenAI: STT (Whisper) e TTS (gpt-4o-mini-tts) via HTTP puro (stdlib)
# ----------------------------------------------------------------------------
def _multipart(fields, file_field, filename, file_bytes, file_type):
    boundary = "----CopilotoDota2" + str(int(time.time() * 1000))
    nl = "\r\n"
    out = []
    for k, v in fields.items():
        out.append(("--" + boundary + nl +
                    'Content-Disposition: form-data; name="' + k + '"' + nl + nl +
                    str(v) + nl).encode("utf-8"))
    out.append(("--" + boundary + nl +
                'Content-Disposition: form-data; name="' + file_field +
                '"; filename="' + filename + '"' + nl +
                "Content-Type: " + file_type + nl + nl).encode("utf-8"))
    out.append(file_bytes)
    out.append((nl + "--" + boundary + "--" + nl).encode("utf-8"))
    return boundary, b"".join(out)


def transcribe(wav_bytes):
    """Transcreve um WAV via OpenAI Whisper. Retorna o texto (pt-BR)."""
    key = get_key()
    if not key:
        raise RuntimeError("OpenAI sem chave")
    boundary, body = _multipart(
        {"model": STT_MODEL, "language": "pt", "response_format": "json"},
        "file", "audio.wav", wav_bytes, "audio/wav")
    req = urllib.request.Request(
        "https://api.openai.com/v1/audio/transcriptions", data=body, method="POST",
        headers={"Authorization": "Bearer " + key,
                 "Content-Type": "multipart/form-data; boundary=" + boundary})
    with urllib.request.urlopen(req, timeout=30) as r:
        d = json.loads(r.read().decode("utf-8"))
    return (d.get("text") or "").strip()


def report_engine():
    """Motor pra ler o placar + escrever o relatorio: 'claude' (padrao, preciso) ou 'openai' (rapido)."""
    return load_config().get("report_engine", "claude")


VISION_MODEL = "gpt-4o-mini"   # le imagem (placar) e escreve texto; rapido e barato


def openai_vision(image_path, system, prompt):
    """Le uma imagem com a OpenAI (gpt-4o-mini, JSON mode). Retorna o texto (JSON)."""
    import base64
    key = get_key()
    if not key:
        raise RuntimeError("OpenAI sem chave")
    with open(image_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")
    body = json.dumps({
        "model": VISION_MODEL, "temperature": 0, "max_tokens": 1200,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {
                    "url": "data:image/png;base64," + b64, "detail": "high"}},
            ]},
        ],
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions", data=body, method="POST",
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=45) as r:
        d = json.loads(r.read().decode("utf-8"))
    return d["choices"][0]["message"]["content"]


def openai_chat(system, prompt):
    """Gera texto com a OpenAI (gpt-4o-mini). Usado pro relatorio tatico rapido."""
    key = get_key()
    if not key:
        raise RuntimeError("OpenAI sem chave")
    body = json.dumps({
        "model": VISION_MODEL, "temperature": 0.4, "max_tokens": 900,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions", data=body, method="POST",
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=45) as r:
        d = json.loads(r.read().decode("utf-8"))
    return (d["choices"][0]["message"]["content"] or "").strip()


def synthesize(text, voice=None, instructions=None):
    """Gera a fala via OpenAI gpt-4o-mini-tts em PCM CRU (24kHz, 16-bit, mono).
    Pedimos 'pcm' (nao 'wav') porque o WAV da OpenAI vem com header de streaming
    que o winsound nao toca; o PCM cru a gente toca direto pelo sounddevice."""
    key = get_key()
    if not key:
        raise RuntimeError("OpenAI sem chave")
    v = voice if voice in TTS_VOICES else "coral"
    inst = (instructions or "").strip() or TTS_DEFAULT_INSTRUCTIONS
    body = json.dumps({"model": TTS_MODEL, "input": str(text)[:4096], "voice": v,
                       "response_format": "pcm", "instructions": inst}).encode("utf-8")
    req = urllib.request.Request(
        "https://api.openai.com/v1/audio/speech", data=body, method="POST",
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


# ----------------------------------------------------------------------------
# Microfone, volume do PC e bip (Windows)
# ----------------------------------------------------------------------------
def audio_available():
    try:
        import sounddevice  # noqa: F401
        return True
    except Exception:
        return False


def list_input_devices():
    """Lista os microfones (entradas) sem duplicar o mesmo aparelho em varias APIs.
    Usa MME (resample, mais compativel) > WASAPI > DirectSound; pula WDM-KS (cheio de
    duplicatas e loopback de alto-falante). Retorna [{index, name}]."""
    try:
        import sounddevice as sd
        pref = {"MME": 0, "Windows WASAPI": 1, "Windows DirectSound": 2}
        best = {}
        for i, d in enumerate(sd.query_devices()):
            if (d.get("max_input_channels") or 0) < 1:
                continue
            try:
                host = sd.query_hostapis(d["hostapi"])["name"]
            except Exception:
                host = ""
            if host not in pref:
                continue  # ignora WDM-KS (duplicatas/loopback/lixo)
            low = (d["name"] or "").lower()
            if any(x in low for x in ("alto-falante", "speaker", " output", "loopback")):
                continue  # nao e microfone (e saida/loopback)
            key = low[:20]  # junta o mesmo mic que aparece em APIs diferentes
            rank = pref[host]
            if key not in best or rank < best[key][0]:
                best[key] = (rank, i, d["name"])
        return [{"index": v[1], "name": v[2]} for v in sorted(best.values(), key=lambda x: (x[2] or "").lower())]
    except Exception:
        return []


def _device_samplerate(device):
    """Taxa de amostragem nativa do dispositivo (muitos mics SO aceitam a nativa)."""
    try:
        import sounddevice as sd
        info = sd.query_devices(device) if device is not None else sd.query_devices(kind="input")
        sr = int(info.get("default_samplerate") or 0)
        return sr if sr >= 8000 else 44100
    except Exception:
        return 44100


def _resolve_input_device():
    """Indice do mic a usar: o escolhido na config (re-achado por nome se renumerou),
    ou None = padrao do Windows."""
    cfg = load_config()
    idx = cfg.get("mic_index")
    name = (cfg.get("mic_name") or "")
    if idx is None:
        return None
    try:
        import sounddevice as sd
        d = sd.query_devices(idx)
        if (d.get("max_input_channels") or 0) >= 1 and (not name or name[:18].lower() in (d["name"] or "").lower()):
            return idx
        if name:  # indice mudou: procura pelo nome
            for i, dd in enumerate(sd.query_devices()):
                if (dd.get("max_input_channels") or 0) >= 1 and name[:18].lower() in (dd["name"] or "").lower():
                    return i
    except Exception:
        pass
    return idx


def mic_level(device=None):
    """Grava ~120ms do mic e devolve (nivel 0-100, rms). Para o medidor de teste.
    device=None usa o escolhido na config. Nivel -1 = erro (mic indisponivel)."""
    if _busy:
        return -1, "ocupado"
    try:
        import sounddevice as sd
        import numpy as np
        if device is None:
            device = _resolve_input_device()
        sr = _device_samplerate(device)
        rec = sd.rec(int(sr * 0.12), samplerate=sr, channels=1, dtype="int16", device=device)
        sd.wait()
        rms = float(np.sqrt(np.mean(rec.astype(np.float64) ** 2))) if rec.size else 0.0
        return min(100, int(rms / 25)), round(rms, 1)
    except Exception as e:
        return -1, str(e)


def _get_volume_iface():
    """Interface de volume master do Windows (pycaw). None se indisponivel.
    Usado so como teste de disponibilidade (o ducking real e POR APLICATIVO)."""
    try:
        import comtypes
        try:
            comtypes.CoInitialize()
        except Exception:
            pass
        from pycaw.pycaw import AudioUtilities
        return AudioUtilities.GetSpeakers().EndpointVolume
    except Exception:
        return None


def volume_available():
    return _get_volume_iface() is not None


# --- Ducking POR APLICATIVO -------------------------------------------------
# Abaixa o volume dos OUTROS apps (jogo, navegador...) e NAO mexe no master:
# assim a voz do copiloto (tocada pelo NOSSO processo) sai no volume cheio do PC,
# e os outros voltam ao normal depois. Guard contra duck aninhado/duplo.
_duck_lock = threading.Lock()
_duck_saved = None  # lista [(SimpleAudioVolume, volume_anterior)] enquanto duckado


def _duck_others(level):
    """Abaixa os outros apps p/ `level` (0..1). Retorna True se ESTE chamado fez o
    duck (responsavel por restaurar); False se ja estava duckado por outro fluxo."""
    global _duck_saved
    with _duck_lock:
        if _duck_saved is not None:
            return False
        saved = []
        try:
            import os
            import comtypes
            try:
                comtypes.CoInitialize()
            except Exception:
                pass
            from pycaw.pycaw import AudioUtilities
            ourpid = os.getpid()
            lvl = max(0.0, min(1.0, float(level)))
            for s in AudioUtilities.GetAllSessions():
                pid = getattr(s, "ProcessId", None)
                if not pid or pid == ourpid:
                    continue  # pula o sistema e a NOSSA propria voz (fica no volume cheio)
                sav = s.SimpleAudioVolume
                saved.append((sav, sav.GetMasterVolume()))
                sav.SetMasterVolume(lvl, None)
        except Exception as e:
            print("[voz] ducking por-app falhou:", e)
        _duck_saved = saved
        return True


def _restore_others(did):
    """Restaura o volume dos outros apps (so se ESTE fluxo fez o duck)."""
    global _duck_saved
    if not did:
        return
    with _duck_lock:
        for sav, prev in (_duck_saved or []):
            try:
                sav.SetMasterVolume(prev, None)
            except Exception:
                pass
        _duck_saved = None


def beep():
    """Bip ascendente (880->1320 Hz) avisando que a captacao comecou."""
    try:
        import winsound
        winsound.Beep(880, 90)
        winsound.Beep(1320, 140)
    except Exception:
        pass


_speak_lock = threading.Lock()  # serializa a reproducao (relatorio nao atropela o "me ouvir")


TTS_SAMPLERATE = 24000  # gpt-4o-mini-tts em PCM: 24kHz, 16-bit, mono


def _play_audio(pcm_bytes):
    """Toca PCM cru (24kHz/16-bit/mono da OpenAI) via sounddevice. Bloqueante.
    (winsound nao toca o WAV 'streaming' da OpenAI; o PCM cru toca direto.)"""
    try:
        import numpy as np
        import sounddevice as sd
        audio = np.frombuffer(pcm_bytes, dtype=np.int16)
        if audio.size == 0:
            return
        sd.play(audio, TTS_SAMPLERATE)
        sd.wait()
    except Exception as e:
        print("[voz] nao consegui tocar o audio:", e)


def _clean_for_speech(text):
    """Tira markdown/bullets (**, #, •, etc.) pra a voz nao ler simbolos soltos."""
    t = str(text or "")
    for ch in ("**", "*", "`", "#", ">", "_", "•", "→", "·"):
        t = t.replace(ch, " ")
    return " ".join(t.split()).strip()


def _tts_and_play(text):
    """Sintetiza (OpenAI) e toca, com lock pra nao sobrepor outra fala. Bloqueante."""
    cfg = load_config()
    pcm = synthesize(text, cfg.get("voice"), cfg.get("instructions"))  # rede (fora do lock)
    with _speak_lock:
        _play_audio(pcm)


def _speak_blocking(text):
    """Abaixa os OUTROS apps, fala no volume cheio do PC e restaura os outros."""
    cfg = load_config()
    ducked = False
    try:
        if cfg.get("duck"):
            ducked = _duck_others(cfg.get("duck_level", 0.2))
        _tts_and_play(text)
    finally:
        _restore_others(ducked)


def speak(text):
    """Fala um texto em voz alta com a OpenAI (gpt-4o-mini-tts), numa thread.
    So fala se engine='openai' e a chave estiver configurada. A voz sai no volume
    cheio (so os outros apps abaixam). Usado p/ a analise tatica quando o scan termina."""
    clean = _clean_for_speech(text)
    if not clean or load_config().get("engine") != "openai" or not is_configured():
        return False

    def _go():
        try:
            _speak_blocking(clean)
        except Exception as e:
            print("[voz] falar falhou:", e)

    threading.Thread(target=_go, daemon=True).start()
    return True


def test_voice():
    """Botao 'Testar agora': FALA uma frase de teste com a voz configurada
    (nao grava o microfone). Mostra o resultado no painel via STATE."""
    if not is_configured():
        _set(status="erro", error="Configure a chave da OpenAI primeiro.")
        return False
    if load_config().get("engine") != "openai":
        _set(status="erro", error="Ative 'Falar a resposta = OpenAI' para testar a voz.")
        return False
    phrase = ("Teste de voz do copiloto. Se voce esta ouvindo isso no volume normal, "
              "esta tudo certo. Boa sorte na partida!")

    def _go():
        _set(status="falando", reply=phrase, transcript="", error=None)
        try:
            _speak_blocking(phrase)
            _set(status="idle")
        except Exception as e:
            _set(status="erro", error=str(e))

    threading.Thread(target=_go, daemon=True).start()
    return True


def _record_until_silence():
    """Grava o mic escolhido ate o silencio (ou teto). Retorna (wav_bytes|None, teve_voz).
    Limiar de voz ADAPTATIVO (calibra o ruido nos primeiros 300ms). Loga o que captou."""
    import sounddevice as sd
    import numpy as np

    device = _resolve_input_device()
    sr = _device_samplerate(device)   # taxa nativa do mic (Whisper aceita qualquer uma)
    block = int(sr * 0.05)  # blocos de 50ms
    frames = []
    started = time.time()
    last_voice = None
    had_speech = False
    noise = 0.0
    thresh = float(RMS_THRESHOLD)
    maxr = 0.0
    cal = []
    with sd.InputStream(samplerate=sr, channels=1, dtype="int16",
                        blocksize=block, device=device) as stream:
        while True:
            data, _ = stream.read(block)
            frames.append(data.copy())
            rms = float(np.sqrt(np.mean(data.astype(np.float64) ** 2))) if data.size else 0.0
            maxr = max(maxr, rms)
            now = time.time()
            elapsed_ms = (now - started) * 1000
            n = len(frames)
            if n <= 6:                       # ~300ms iniciais = ruido de fundo
                cal.append(rms)
                if n == 6:
                    noise = sum(cal) / len(cal)
                    thresh = max(float(RMS_THRESHOLD), noise * 3.0)
            elif rms > thresh:
                had_speech = True
                last_voice = now
            if elapsed_ms > MAX_MS:
                break
            if not had_speech and elapsed_ms > NOSPEECH_MS:
                break
            if had_speech and last_voice and (now - last_voice) * 1000 > SILENCE_MS:
                break

    secs = len(frames) * 0.05
    print(f"[voz] mic#{device} @ {sr}Hz: {secs:.1f}s captados | ruido~{noise:.0f} limiar~{thresh:.0f} "
          f"pico~{maxr:.0f} fala_detectada={had_speech}")
    if not frames:
        return None, had_speech
    audio = np.concatenate(frames)
    if audio.shape[0] < int(sr * 0.3):  # < 0.3s = ruido, ignora
        return None, had_speech
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(audio.tobytes())
    # devolve o audio MESMO sem deteccao de voz: o Whisper ainda consegue transcrever
    # (fallback p/ quando o limiar erra). had_speech vai junto so pra log/decisao.
    return buf.getvalue(), had_speech


# ----------------------------------------------------------------------------
# Loop principal do atalho "me ouvir"
# ----------------------------------------------------------------------------
def run_listen(handle):
    """Executa um ciclo de voz. `handle(texto) -> resposta` e fornecido pelo
    server (grava no historico do chat e chama o cerebro). Roda numa thread."""
    global _busy
    if _busy:
        return
    if not is_configured():
        _set(status="erro", error="Configure a chave da OpenAI em Settings.")
        return
    if not audio_available():
        _set(status="erro", error="Microfone indisponivel (instale: pip install sounddevice).")
        return

    _busy = True
    cfg = load_config()
    ducked = False
    try:
        # 1) abaixa SO os outros apps (master intacto: bip e voz saem no volume cheio)
        if cfg.get("duck"):
            ducked = _duck_others(cfg.get("duck_level", 0.2))
        # 2) bip avisando que comecou
        if cfg.get("beep"):
            beep()
        # 3) grava ate o silencio (outros apps abaixados = mic mais limpo)
        _set(status="ouvindo", listening=True, transcript="", reply="", error=None)
        wav_bytes, had = _record_until_silence()
        if not wav_bytes:
            _set(status="erro", listening=False,
                 error="nao captei audio do microfone - confira o mic escolhido em Settings")
            return
        # 4) transcreve (Whisper) - tenta MESMO sem deteccao de voz (o Whisper pega audio baixo)
        _set(status="transcrevendo", listening=False)
        text = transcribe(wav_bytes)
        print(f"[voz] transcricao: {text!r}")
        if not text:
            _set(status="erro",
                 error="nao entendi sua voz - fale logo apos o bip, mais perto do mic (ou troque o mic em Settings)")
            return
        # 5) cerebro do copiloto
        _set(status="pensando", transcript=text)
        reply = handle(text) or ""
        # 6) fala a resposta no volume cheio (outros ainda abaixados = voz clara)
        _set(status="falando", reply=reply)
        if cfg.get("engine") == "openai" and reply:
            try:
                _tts_and_play(_clean_for_speech(reply))
            except Exception as e:
                print("[voz] TTS falhou:", e)
        _set(status="idle")
    except Exception as e:
        _set(status="erro", listening=False, error=str(e))
        print("[voz] erro no ciclo:", e)
    finally:
        _restore_others(ducked)  # restaura os outros apps no fim do ciclo
        _busy = False


def get_state():
    with _lock:
        s = dict(STATE)
    s["configured"] = is_configured()
    return s


def public_config():
    cfg = load_config()
    return {
        "configured": is_configured(),
        "engine": cfg["engine"], "voice": cfg["voice"], "instructions": cfg["instructions"],
        "hotkey": cfg["hotkey"], "duck": cfg["duck"], "duck_level": cfg["duck_level"],
        "beep": cfg["beep"], "speak_report": cfg["speak_report"], "voices": TTS_VOICES,
        "mic_index": cfg["mic_index"], "mic_name": cfg["mic_name"],
        "report_engine": cfg["report_engine"],
        "devices": list_input_devices(),
        "audio_ok": audio_available(), "volume_ok": volume_available(),
    }
