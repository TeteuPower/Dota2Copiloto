"""
Atualizacao automatica (como o Theus): por dentro do proprio app.
=================================================================

  1. Consulta o ultimo Release do GitHub (TeteuPower/Dota2Copiloto) no boot e a
     cada 3h.
  2. Se ha versao nova, BAIXA o instalador em segundo plano
     (%LOCALAPPDATA%/CopilotoDota2/updates).
  3. Instala:
       - na hora, pelo botao "Atualizar agora" do painel; ou
       - sozinho (padrao), quando o app esta OCIOSO (sem partida rolando).
     O instalador roda SILENCIOSO e por usuario (sem pedir administrador),
     fecha este app, atualiza na mesma pasta e o reabre na bandeja.

Em dev (versao 'dev') nada disso roda.
"""

import json
import os
import re
import subprocess
import threading
import time
import urllib.request
from pathlib import Path

from copiloto import config

CHECK_EVERY = 3 * 3600
ASSET_RE = re.compile(r"^CopilotoDota2-Setup-([\d.]+)\.exe$", re.I)
UPDATES_DIR = config.DATA_DIR / "updates"
CFG_PATH = config.DATA_DIR / "update_config.json"

STATE = {
    "version": config.APP_VERSION,
    "latest": None,
    "update_available": False,
    "update_url": None,          # pagina do release (notas)
    "phase": "idle",             # idle | verificando | baixando | pronto | instalando | erro
    "progress": 0.0,
    "error": None,
    "auto": True,
    "checked_at": 0.0,
    "enabled": False,            # so no app instalado
}
_asset = {"url": None, "name": None, "size": 0}
_lock = threading.Lock()


def version_tuple(v):
    try:
        return tuple(int(p) for p in str(v).strip().lstrip("v").split("."))
    except ValueError:
        return None


def _load_cfg():
    try:
        d = json.loads(CFG_PATH.read_text(encoding="utf-8"))
        STATE["auto"] = bool(d.get("auto", True))
    except Exception:
        pass


def set_auto(on):
    STATE["auto"] = bool(on)
    try:
        CFG_PATH.write_text(json.dumps({"auto": STATE["auto"]}), encoding="utf-8")
    except Exception:
        pass


def public():
    return {k: STATE[k] for k in STATE}


def _local_file():
    return UPDATES_DIR / _asset["name"] if _asset["name"] else None


def check():
    """Consulta o ultimo release. -> True se ha versao nova."""
    cur = version_tuple(config.APP_VERSION)
    if cur is None:
        return False
    STATE.update(phase="verificando", error=None)
    try:
        req = urllib.request.Request(
            f"https://api.github.com/repos/{config.GITHUB_REPO}/releases/latest",
            headers={"User-Agent": "CopilotoDota2", "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read().decode("utf-8"))
        latest = (data.get("tag_name") or "").lstrip("v")
        lt = version_tuple(latest)
        asset = next((a for a in data.get("assets") or [] if ASSET_RE.match(a.get("name") or "")), None)
        newer = bool(lt and lt > cur and asset)
        STATE.update(latest=latest or None, update_url=data.get("html_url"),
                     update_available=newer, checked_at=time.time())
        if newer:
            _asset.update(url=asset["browser_download_url"], name=asset["name"],
                          size=int(asset.get("size") or 0))
            f = _local_file()
            ready = f and f.exists() and (not _asset["size"] or f.stat().st_size == _asset["size"])
            STATE["phase"] = "pronto" if ready else "idle"
        else:
            STATE["phase"] = "idle"
        return newer
    except Exception as e:
        STATE.update(phase="erro", error=f"nao consegui verificar ({str(e)[:80]})", checked_at=time.time())
        return False


def download():
    """Baixa o instalador da versao nova (retoma do zero se falhar)."""
    if not _asset["url"]:
        return False
    UPDATES_DIR.mkdir(parents=True, exist_ok=True)
    dst = _local_file()
    if dst.exists() and (not _asset["size"] or dst.stat().st_size == _asset["size"]):
        STATE.update(phase="pronto", progress=1.0)
        return True
    part = dst.with_suffix(".part")
    STATE.update(phase="baixando", progress=0.0, error=None)
    try:
        req = urllib.request.Request(_asset["url"], headers={"User-Agent": "CopilotoDota2"})
        with urllib.request.urlopen(req, timeout=60) as r, open(part, "wb") as f:
            total = int(r.headers.get("Content-Length") or _asset["size"] or 0)
            done = 0
            while True:
                chunk = r.read(1024 * 512)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if total:
                    STATE["progress"] = round(min(done / total, 1.0), 3)
        if _asset["size"] and part.stat().st_size != _asset["size"]:
            raise RuntimeError("download incompleto")
        os.replace(part, dst)
        STATE.update(phase="pronto", progress=1.0)
        print(f"[UPDATE] baixado {dst.name}")
        return True
    except Exception as e:
        try:
            part.unlink(missing_ok=True)
        except OSError:
            pass
        STATE.update(phase="erro", error=f"falha no download ({str(e)[:80]})")
        return False


def apply(on_exit):
    """Roda o instalador SILENCIOSO e encerra o app (o instalador o reabre)."""
    with _lock:
        if STATE["phase"] == "instalando":
            return False
        if STATE["phase"] != "pronto" and not download():
            return False
        f = _local_file()
        log = config.LOG_DIR / "update-install.log"
        STATE.update(phase="instalando")
        print(f"[UPDATE] instalando {f.name} (o app fecha e volta sozinho)")
        DETACHED = 0x00000008 | 0x00000200      # DETACHED_PROCESS | NEW_PROCESS_GROUP
        try:
            subprocess.Popen([str(f), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-",
                              f"/LOG={log}"], close_fds=True, creationflags=DETACHED)
        except Exception as e:
            STATE.update(phase="erro", error=f"nao consegui abrir o instalador ({e})")
            return False
    # o instalador manda POST /shutdown e taskkill; saimos antes, por garantia
    threading.Timer(2.0, on_exit).start()
    return True


def _cleanup_old():
    cur = version_tuple(config.APP_VERSION)
    if not UPDATES_DIR.exists() or cur is None:
        return
    for p in UPDATES_DIR.iterdir():
        m = ASSET_RE.match(p.name)
        if (m and (version_tuple(m.group(1)) or ()) <= cur) or p.suffix == ".part":
            try:
                p.unlink()
            except OSError:
                pass


def loop(is_idle, on_exit):
    """Thread: verifica, baixa e (no automatico) instala quando ocioso."""
    if version_tuple(config.APP_VERSION) is None:
        return
    STATE["enabled"] = True
    _load_cfg()
    _cleanup_old()
    time.sleep(20)
    next_check = 0.0
    while True:
        try:
            if time.time() >= next_check:
                next_check = time.time() + CHECK_EVERY
                if check() and STATE["phase"] != "pronto":
                    download()
            if STATE["auto"] and STATE["phase"] == "pronto" and is_idle():
                apply(on_exit)
                return
        except Exception as e:
            print(f"[UPDATE] erro: {e}")
        time.sleep(60)
