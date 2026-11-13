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
  6) FALA a resposta - por padrao com a VOZ LOCAL (Piper, gerada no proprio PC,
     sem API: copiloto/tts_local.py) ou, se escolhido, com a OpenAI (gpt-4o-mini-tts).

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
import re
import time
import wave
import threading
import urllib.request

from copiloto import config, perf, stt_local, tts_local

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
    "engine": "local",    # "local" = voz gerada no PC (Piper) | "openai" = API da OpenAI | "off" = nao fala
    "voice": "coral",       # voz da OpenAI (so quando engine=openai)
    "local_voice": tts_local.DEFAULT_VOICE,
    "local_speed": 1.1,     # velocidade da voz local (1.0 = normal)
    "stt_engine": "local",  # "local" = transcricao no PC (whisper.cpp) | "openai" = Whisper da API
    "stt_model": stt_local.DEFAULT_MODEL,
    "cpu_mode": "auto",     # uso do PC pela voz/transcricao local: auto | leve | livre
    "speak_chat": True,     # narrar em voz alta as respostas do chat ESCRITO (padrao: sempre)
    "continuous": True,     # conversa: depois de responder, volta a ouvir ate voce ficar em silencio
    "ptt": True,            # segure-pra-falar: grava enquanto a tecla estiver apertada (solta = envia)
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
MIC_STALL_S = 3              # mic sem mandar audio por 3 s = travou -> encerra a captura
PTT_MAX_MS = 60000           # segure-pra-falar: teto de 1 min por fala
CONVERSATION_MAX_TURNS = 8   # teto de idas-e-voltas por atalho no modo conversa
RMS_THRESHOLD = 250     # piso de energia (int16) p/ voz; tambem calibramos o ruido na hora

# Estado ao vivo lido pelo painel (/voice/state). status:
# idle | ouvindo | transcrevendo | pensando | falando | erro
STATE = {"status": "idle", "listening": False, "transcript": "",
         "reply": "", "error": None, "at": 0.0}
_lock = threading.Lock()
_busy = False
_busy_since = 0.0
_busy_lock = threading.Lock()
BUSY_STUCK_S = 120   # um ciclo de voz nunca leva isso tudo: passou = travou


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
    d = _read_file()
    saved = d.get("config") or {}
    cfg = dict(DEFAULT_CONFIG)
    cfg.update(saved)
    # Migracao (uma vez): quem estava na voz da OpenAI passa pra voz LOCAL.
    if saved and not saved.get("tts_local_v1"):
        if cfg.get("engine") == "openai":
            cfg["engine"] = "local"
        cfg["tts_local_v1"] = True
        d["config"] = cfg
        try:
            _write_file(d)
        except OSError:
            pass
    _apply_cpu_mode(cfg)
    return cfg


def save_config(patch):
    cfg = load_config()
    if isinstance(patch, dict):
        if patch.get("engine") in ("local", "openai", "off"):
            cfg["engine"] = patch["engine"]
        if patch.get("local_voice") in tts_local.VOICES:
            cfg["local_voice"] = patch["local_voice"]
        if patch.get("stt_engine") in ("local", "openai"):
            cfg["stt_engine"] = patch["stt_engine"]
        if patch.get("stt_model") in stt_local.MODELS:
            cfg["stt_model"] = patch["stt_model"]
        if patch.get("cpu_mode") in ("auto", "leve", "livre"):
            cfg["cpu_mode"] = patch["cpu_mode"]
        if "continuous" in patch:
            cfg["continuous"] = bool(patch["continuous"])
        if "speak_chat" in patch:
            cfg["speak_chat"] = bool(patch["speak_chat"])
        if "ptt" in patch:
            cfg["ptt"] = bool(patch["ptt"])
        try:
            if patch.get("local_speed") is not None:
                cfg["local_speed"] = max(0.6, min(1.8, float(patch["local_speed"])))
        except (TypeError, ValueError):
            pass
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
    cfg["tts_local_v1"] = True
    d = _read_file()
    d["config"] = cfg
    _write_file(d)
    if cfg["engine"] == "local" and not tts_local.ready(cfg["local_voice"]):
        tts_local.install_bg(cfg["local_voice"])   # baixa a voz escolhida em segundo plano
    if cfg["stt_engine"] == "local" and not stt_local.ready(cfg["stt_model"]):
        stt_local.install_bg(cfg["stt_model"])     # e o modelo de transcricao
    _apply_cpu_mode(cfg)
    return cfg


def _apply_cpu_mode(cfg):
    tts_local.CPU_MODE = stt_local.CPU_MODE = cfg.get("cpu_mode") or "auto"


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


def beep_release():
    """Bip curto descendente: soltou a tecla, a captura terminou."""
    try:
        import winsound
        winsound.Beep(990, 60)
        winsound.Beep(660, 90)
    except Exception:
        pass


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


def _play_audio(pcm_bytes, samplerate=TTS_SAMPLERATE):
    """Toca PCM cru (16-bit/mono) via sounddevice. Bloqueante.
    (winsound nao toca o WAV 'streaming' da OpenAI; o PCM cru toca direto.)"""
    try:
        import numpy as np
        import sounddevice as sd
        audio = np.frombuffer(pcm_bytes, dtype=np.int16)
        if audio.size == 0:
            return
        sd.play(audio, samplerate)
        sd.wait()
    except Exception as e:
        print("[voz] nao consegui tocar o audio:", e)


def _clean_for_speech(text):
    """Deixa so o que e pra FALAR: tira markdown/bullets (**, #, •) e TODO emoji ou
    simbolo grafico (a voz local lia o nome deles em voz alta). Mantem letras,
    numeros, pontuacao e os simbolos que se leem ($, %, +, /)."""
    import unicodedata
    t = str(text or "")
    for ch in ("**", "*", "`", "#", ">", "_", "•", "→", "·", "|", "~"):
        t = t.replace(ch, " ")
    keep = set("$%+/-")
    out = []
    for c in t:
        cat = unicodedata.category(c)
        if c in keep:
            out.append(c)
        elif cat[0] == "S" or cat in ("Co", "Cs", "Cf", "Mn") and ord(c) > 0x2000:
            out.append(" ")            # emoji, seta, simbolo, ZWJ, seletor de variacao
        else:
            out.append(c)
    t = "".join(out)
    t = re.sub(r"\s+([,.;:!?])", r"\1", t)            # pontuacao que ficou solta
    t = re.sub(r"([,.;:!?])(\s*[,.;:!?])+", r"\1", t)
    return " ".join(t.split()).strip(" -")


def can_speak(cfg=None):
    """(pode_falar, motivo). Local: so precisa do motor (baixa sozinho);
    OpenAI: precisa da chave."""
    cfg = cfg or load_config()
    eng = cfg.get("engine")
    if eng == "local":
        return True, ""
    if eng == "openai":
        return (True, "") if is_configured() else (False, "Configure a chave da OpenAI (ou use a voz local).")
    return False, "A voz está desligada (O copiloto fala comigo? = Não)."


def _tts_and_play(text):
    """Sintetiza e toca, com lock pra nao sobrepor outra fala. Bloqueante."""
    cfg = load_config()
    if cfg.get("engine") == "local":
        with _speak_lock:   # local: gera e toca em streaming (comeca a falar na 1a frase)
            tts_local.speak_stream(text, cfg.get("local_voice"), cfg.get("local_speed", 1.0))
        return
    pcm = synthesize(text, cfg.get("voice"), cfg.get("instructions"))  # rede (fora do lock)
    with _speak_lock:
        _play_audio(pcm)


_SENT_END = re.compile(r"[.!?\u2026;:](?=\s)|\n")


def _next_sentence(buf, final=False):
    """Tira a 1a frase completa do buffer. -> (frase|None, resto). Junta pedacos
    muito curtos ('Sim.') com a frase seguinte pra voz nao picotar."""
    pos = 0
    while True:
        m = _SENT_END.search(buf, pos)
        if not m:
            break
        end = m.end()
        if len(buf[:end].strip()) >= 18 or final:
            return buf[:end], buf[end:]
        pos = end
    if final and buf.strip():
        return buf, ""
    return None, buf


def _speak_live(chunks, cfg, t0=None):
    """Fala a resposta CONFORME ela chega: cada frase pronta vai pra voz na hora.
    A 1a frase toca enquanto a IA ainda escreve as proximas (voz local); com a voz
    da OpenAI, junta tudo e fala no fim."""
    live = can_speak(cfg)[0] and cfg.get("engine") == "local"
    speaker, buf, full, first = None, "", [], True
    locked = False

    def _open():
        nonlocal speaker, locked
        _speak_lock.acquire()
        locked = True
        speaker = tts_local.Speaker(cfg.get("local_voice"), cfg.get("local_speed", 1.0))

    try:
        for chunk in chunks:
            full.append(chunk)
            if not live:
                continue
            buf += chunk
            while True:
                sent, buf = _next_sentence(buf)
                if sent is None:
                    break
                clean = _clean_for_speech(sent)
                if not clean:
                    continue
                if speaker is None:
                    _open()
                if first and t0:
                    print(f"[voz] 1a frase pronta em {time.time() - t0:.1f}s")
                    first = False
                _set(status="falando", reply="".join(full))
                speaker.say(clean)
        reply = "".join(full).strip()
        _set(status="falando", reply=reply)
        if live:
            clean = _clean_for_speech(buf)        # tudo o que sobrou (ultima frase)
            if clean:
                if speaker is None:
                    _open()
                speaker.say(clean)
        elif reply and can_speak(cfg)[0]:
            _tts_and_play(_clean_for_speech(reply))
    except Exception as e:
        print("[voz] fala ao vivo falhou:", e)
    finally:
        if hasattr(chunks, "close"):
            chunks.close()                 # garante que a resposta vai pro diario
        try:
            if speaker is not None:
                speaker.close()
        finally:
            if locked:
                _speak_lock.release()


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
    """Fala um texto em voz alta (voz local ou OpenAI, conforme a config), numa
    thread. A voz sai no volume cheio (so os outros apps abaixam). Usado p/ a
    analise tatica quando o scan termina."""
    clean = _clean_for_speech(text)
    if not clean or not can_speak()[0]:
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
    ok, why = can_speak()
    if not ok:
        _set(status="erro", error=why)
        return False
    phrase = ("Teste de voz do copiloto. Se voce esta ouvindo isso no volume normal, "
              "esta tudo certo. Boa sorte na partida!")

    def _go():
        cfg = load_config()
        if cfg.get("engine") == "local" and not tts_local.ready(cfg.get("local_voice")):
            _set(status="pensando", reply="Baixando a voz local (só na primeira vez)…", transcript="", error=None)
            tts_local.install(cfg.get("local_voice"))
        _set(status="falando", reply=phrase, transcript="", error=None)
        try:
            _speak_blocking(phrase)
            _set(status="idle")
        except Exception as e:
            _set(status="erro", error=str(e))

    threading.Thread(target=_go, daemon=True).start()
    return True


def _record_until_silence(hold_fn=None):
    """Grava o mic escolhido ate o silencio (ou teto). Retorna (wav_bytes|None, teve_voz).
    Limiar de voz ADAPTATIVO (calibra o ruido nos primeiros 300ms). Loga o que captou.
    hold_fn (segure-pra-falar): grava ENQUANTO hold_fn() for True - soltar a tecla
    encerra na hora; nesse modo o silencio nao corta nada."""
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
    # Modo callback + fila: o laco NUNCA fica preso esperando o microfone (o read()
    # bloqueante travou o F8 com o mic aberto). Sem audio por MIC_STALL_S -> encerra.
    import queue as _q
    fifo = _q.Queue()

    def _cb(indata, _frames, _t, _status):
        fifo.put(indata.copy())

    print(f"[voz] gravando: mic#{device} @ {sr}Hz ({'segurar' if hold_fn else 'silencio'})")
    stream = sd.InputStream(samplerate=sr, channels=1, dtype="int16", blocksize=block,
                            device=device, callback=_cb)
    stream.start()
    last_data = time.time()
    try:
        while True:
            try:
                data = fifo.get(timeout=0.1)
                last_data = time.time()
            except _q.Empty:
                now = time.time()
                if hold_fn is not None and (now - started) > 0.15 and not hold_fn():
                    break                    # soltou a tecla (mesmo sem audio chegando)
                if now - last_data > MIC_STALL_S:
                    print(f"[voz] o microfone parou de mandar audio ha {MIC_STALL_S}s - encerrando a captura")
                    break
                if (now - started) * 1000 > (PTT_MAX_MS if hold_fn else MAX_MS):
                    break
                continue
            frames.append(data)
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
            if hold_fn is not None:
                if elapsed_ms > PTT_MAX_MS:
                    break
                if elapsed_ms > 150 and not hold_fn():
                    break                    # soltou a tecla: fim da captura
                continue
            if elapsed_ms > MAX_MS:
                break
            if not had_speech and elapsed_ms > NOSPEECH_MS:
                break
            if had_speech and last_voice and (now - last_voice) * 1000 > SILENCE_MS:
                break
    finally:
        # fechar o mic tambem ja travou: fecha numa thread e segue em frente
        def _close():
            try:
                stream.abort()
                stream.close()
            except Exception:
                pass
        t = threading.Thread(target=_close, daemon=True)
        t.start()
        t.join(2.0)
        if t.is_alive():
            print("[voz] o driver do microfone demorou pra fechar - seguindo mesmo assim")

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
def run_listen(handle, hold_fn=None):
    """Executa um ciclo de voz. `handle(texto) -> resposta` e fornecido pelo
    server (grava no historico do chat e chama o cerebro). Roda numa thread.
    hold_fn: segure-pra-falar - grava enquanto a tecla estiver apertada."""
    global _busy
    global _busy_since
    with _busy_lock:          # a tecla segurada repete o disparo: so 1 ciclo por vez
        if _busy and time.time() - _busy_since < BUSY_STUCK_S:
            return
        if _busy:             # ciclo anterior preso: libera em vez de exigir reiniciar o app
            print(f"[voz] ciclo anterior preso ha {int(time.time() - _busy_since)}s - liberando o F8")
            _set(status="idle", listening=False)
        _busy = True
        _busy_since = time.time()
    if load_config().get("stt_engine") == "openai" and not is_configured():
        _set(status="erro", error="Transcrição pela OpenAI escolhida, mas sem chave (ou escolha a transcrição local).")
        _busy = False
        return
    if not audio_available():
        _set(status="erro", error="Microfone indisponivel (instale: pip install sounddevice).")
        _busy = False
        return

    cfg = load_config()
    ducked = False
    try:
        # 1) abaixa SO os outros apps (master intacto: bip e voz saem no volume cheio)
        if cfg.get("duck"):
            ducked = _duck_others(cfg.get("duck_level", 0.2))
        # Conversa: 1 turno por padrao; com "conversa continua", depois de responder
        # volta a ouvir sozinho e so para quando voce fica em silencio.
        # Segure-pra-falar: cada vez que segura a tecla = 1 fala (o contexto segue).
        ptt = hold_fn is not None
        turns = 1 if ptt else (CONVERSATION_MAX_TURNS if cfg.get("continuous") else 1)
        for turn in range(turns):
            follow = turn > 0
            # 2) bip avisando que comecou. No segure-pra-falar ele toca JUNTO com a
            #    gravacao (bloqueante comeria o inicio da sua frase).
            if cfg.get("beep"):
                if ptt:
                    threading.Thread(target=beep, daemon=True).start()
                else:
                    beep()
            # 3) grava ate o silencio (ou ate soltar a tecla)
            _set(status="ouvindo", listening=True, transcript="", reply="" if not follow else STATE["reply"],
                 error=None)
            wav_bytes, had = _record_until_silence(hold_fn)
            if ptt and cfg.get("beep"):
                threading.Thread(target=beep_release, daemon=True).start()
            if follow and not had:
                break                              # silencio depois da resposta: fim da conversa
            if not wav_bytes:
                _set(status="erro", listening=False,
                     error=("fala muito curta - SEGURE a tecla enquanto fala e solte no fim" if ptt else
                            "nao captei audio do microfone - confira o mic escolhido em Settings"))
                return
            # 4) transcreve - no 1o turno tenta MESMO sem deteccao de voz (pega audio baixo)
            _set(status="transcrevendo", listening=False)
            if cfg.get("stt_engine") == "local":
                text = stt_local.transcribe(wav_bytes, cfg.get("stt_model"))
            else:
                text = transcribe(wav_bytes)
            print(f"[voz] transcricao: {text!r}")
            if not text:
                if follow:
                    break
                _set(status="erro",
                     error="nao entendi sua voz - fale logo apos o bip, mais perto do mic (ou troque o mic em Settings)")
                return
            # 5) cerebro do copiloto (o que voce disse entra no diario da partida)
            _set(status="pensando", transcript=text)
            t_ask = time.time()
            res = handle(text)
            if isinstance(res, str) or res is None:
                reply = res or ""
                # 6) fala a resposta no volume cheio (outros ainda abaixados = voz clara)
                _set(status="falando", reply=reply)
                if reply and can_speak(cfg)[0]:
                    try:
                        _tts_and_play(_clean_for_speech(reply))
                    except Exception as e:
                        print("[voz] TTS falhou:", e)
            else:
                _speak_live(res, cfg, t_ask)       # resposta em pedacos: fala frase a frase
        _set(status="idle", listening=False)
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
        "local_voice": cfg["local_voice"], "local_speed": cfg["local_speed"],
        "local": tts_local.public(cfg["local_voice"]),
        "stt_engine": cfg["stt_engine"], "stt_model": cfg["stt_model"],
        "stt": stt_local.public(cfg["stt_model"]),
        "cpu_mode": cfg["cpu_mode"], "perf": perf.describe(), "continuous": cfg["continuous"], "ptt": cfg["ptt"],
        "speak_chat": cfg["speak_chat"],
        "mic_index": cfg["mic_index"], "mic_name": cfg["mic_name"],
        "report_engine": cfg["report_engine"],
        "devices": list_input_devices(),
        "audio_ok": audio_available(), "volume_ok": volume_available(),
    }
