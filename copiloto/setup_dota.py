"""
Configuracao automatica do Dota 2 (o que antes era feito a mao).
================================================================

Para o painel ao vivo funcionar, o Dota precisa de DUAS coisas:

  1) o arquivo do GSI em  <dota>/game/dota/cfg/gamestate_integration/
     (diz pro jogo mandar o estado pra http://127.0.0.1:49317/gsi);
  2) a opcao de inicializacao  -gamestateintegration  no Dota 2 da Steam.

Este modulo acha o Dota em QUALQUER biblioteca da Steam (libraryfolders.vdf),
grava o .cfg e coloca a opcao de inicializacao no localconfig.vdf de cada conta
da Steam do PC. A Steam reescreve esse arquivo ao fechar, entao a opcao so pode
ser gravada com a Steam FECHADA: se ela estiver aberta, o app fecha e reabre a
Steam quando a pessoa pedir (ou o instalador, na instalacao).

Edicao do .vdf e CIRURGICA (so o trecho do Dota muda; o resto do arquivo fica
byte a byte) e sempre com backup ao lado (.copiloto.bak).

Uso: CopilotoDota2.exe --provision [--fechar-steam]   (o instalador chama isto)
"""

import os
import subprocess
import time
from pathlib import Path

from copiloto import config

DOTA_APPID = "570"
LAUNCH_FLAG = "-gamestateintegration"
CFG_NAME = "gamestate_integration_copiloto.cfg"

STATUS = {"checked_at": 0.0}


# ----------------------------------------------------------------------------
# Mini parser de VDF (KeyValues da Valve) que guarda as POSICOES no texto
# ----------------------------------------------------------------------------
class _Node:
    __slots__ = ("key", "value", "vspan", "children", "open_pos", "close_pos")

    def __init__(self, key, value=None, vspan=None, children=None, open_pos=None, close_pos=None):
        self.key, self.value, self.vspan = key, value, vspan
        self.children, self.open_pos, self.close_pos = children, open_pos, close_pos

    def get(self, key):
        for c in self.children or []:
            if c.key.lower() == key.lower():
                return c
        return None


def _tokens(text):
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c in " \t\r\n":
            i += 1
        elif text.startswith("//", i):
            j = text.find("\n", i)
            i = n if j < 0 else j + 1
        elif c in "{}":
            yield (c, i, i + 1, None)
            i += 1
        elif c == '"':
            j, buf = i + 1, []
            while j < n and text[j] != '"':
                if text[j] == "\\" and j + 1 < n:
                    buf.append(text[j:j + 2])
                    j += 2
                    continue
                buf.append(text[j])
                j += 1
            yield ("s", i, j + 1, "".join(buf))
            i = j + 1
        else:
            j = i
            while j < n and text[j] not in ' \t\r\n{}"':
                j += 1
            yield ("s", i, j, text[i:j])
            i = j


def _parse(text):
    toks = list(_tokens(text))
    pos = 0

    def block():
        nonlocal pos
        kids = []
        while pos < len(toks):
            t = toks[pos]
            if t[0] == "}":
                pos += 1
                return kids, t[1]
            if t[0] != "s":
                raise ValueError("vdf: chave esperada")
            pos += 1
            if pos >= len(toks):
                raise ValueError("vdf: valor faltando")
            v = toks[pos]
            if v[0] == "{":
                pos += 1
                ch, close = block()
                kids.append(_Node(t[3], children=ch, open_pos=v[1], close_pos=close))
            elif v[0] == "s":
                pos += 1
                # condicional opcional ([$WIN32] etc.) logo depois do valor
                if pos < len(toks) and toks[pos][0] == "s" and toks[pos][3].startswith("[$"):
                    pos += 1
                kids.append(_Node(t[3], value=v[3], vspan=(v[1], v[2])))
            else:
                raise ValueError("vdf: '}' inesperado")
        return kids, len(text)

    kids, _ = block()
    return _Node("<root>", children=kids, open_pos=-1, close_pos=len(text))


def _unescape(s):
    return s.replace("\\\\", "\\").replace('\\"', '"')


def _line_indent(text, pos):
    start = text.rfind("\n", 0, pos) + 1
    j = start
    while j < len(text) and text[j] in " \t":
        j += 1
    return text[start:j]


def _insert_after_open(text, node, snippet_lines):
    """Insere linhas logo depois da '{' de node, com a indentacao certa."""
    nl = "\r\n" if "\r\n" in text else "\n"
    ind = _line_indent(text, node.open_pos) + "\t"
    ins = "".join(nl + ind + line for line in snippet_lines)
    p = node.open_pos + 1
    return text[:p] + ins + text[p:]


# ----------------------------------------------------------------------------
# Steam / Dota
# ----------------------------------------------------------------------------
def steam_dir():
    if os.name != "nt":
        return None
    import winreg
    for hive, sub, name in ((winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
                            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
                            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath")):
        try:
            with winreg.OpenKey(hive, sub) as k:
                v, _ = winreg.QueryValueEx(k, name)
                p = Path(str(v).replace("/", "\\"))
                if p.exists():
                    return p
        except OSError:
            continue
    for c in (r"C:\Program Files (x86)\Steam", r"C:\Program Files\Steam"):
        if Path(c).exists():
            return Path(c)
    return None


def library_dirs(steam):
    libs = [steam]
    vdf = steam / "steamapps" / "libraryfolders.vdf"
    try:
        root = _parse(vdf.read_text(encoding="utf-8", errors="replace"))
        lf = root.get("libraryfolders") or root.get("LibraryFolders")
        for entry in (lf.children if lf else []):
            if entry.children is not None:
                p = entry.get("path")
                if p and p.value:
                    libs.append(Path(_unescape(p.value)))
            elif entry.key.isdigit() and entry.value:      # formato antigo: "1" "D:\\Lib"
                libs.append(Path(_unescape(entry.value)))
    except Exception:
        pass
    out, seen = [], set()
    for l in libs:
        k = str(l).lower().rstrip("\\/")
        if k not in seen:
            seen.add(k)
            out.append(l)
    return out


def dota_dir(steam=None):
    steam = steam or steam_dir()
    if not steam:
        return None
    for lib in library_dirs(steam):
        apps = lib / "steamapps"
        acf = apps / f"appmanifest_{DOTA_APPID}.acf"
        installdir = "dota 2 beta"
        if acf.exists():
            try:
                n = _parse(acf.read_text(encoding="utf-8", errors="replace")).get("AppState")
                d = n.get("installdir") if n else None
                if d and d.value:
                    installdir = _unescape(d.value)
            except Exception:
                pass
        cand = apps / "common" / installdir
        if (cand / "game" / "dota").exists():
            return cand
    return None


def gsi_cfg_target(dota):
    return dota / "game" / "dota" / "cfg" / "gamestate_integration" / CFG_NAME


def ensure_gsi_cfg(dota):
    """Grava/atualiza o .cfg do GSI. -> 'ok' | 'gravado' | 'erro: ...'"""
    want = Path(config.GSI_CFG_PATH).read_text(encoding="utf-8")
    dst = gsi_cfg_target(dota)
    try:
        if dst.exists() and dst.read_text(encoding="utf-8", errors="replace").strip() == want.strip():
            return "ok"
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(want, encoding="utf-8")
        return "gravado"
    except Exception as e:
        return f"erro: {e}"


def steam_running():
    if os.name != "nt":
        return False
    try:
        r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq steam.exe", "/NH"],
                           capture_output=True, text=True, timeout=10)
        return "steam.exe" in (r.stdout or "").lower()
    except Exception:
        return False


def localconfigs(steam):
    ud = steam / "userdata"
    if not ud.exists():
        return []
    return [p for p in ud.glob("*/config/localconfig.vdf") if p.is_file()]


def _launch_options_of(text):
    """-> (node_apps_570 | None, valor LaunchOptions | None) ou levanta ValueError."""
    root = _parse(text)
    steam = root
    for k in ("UserLocalConfigStore", "Software", "Valve", "Steam"):
        steam = steam.get(k) if steam else None
    if not steam:
        return None, None, root
    apps = steam.get("apps")
    app = apps.get(DOTA_APPID) if apps else None
    lo = app.get("LaunchOptions") if app else None
    return app, (lo.value if lo else None), root


def has_flag(value):
    return LAUNCH_FLAG.lower() in (value or "").lower().split()


def patch_localconfig(text):
    """Texto do localconfig.vdf -> texto com '-gamestateintegration' no Dota.
    Devolve None se ja estava certo. Edicao cirurgica, o resto fica intacto."""
    root = _parse(text)
    node = root
    for k in ("UserLocalConfigStore", "Software", "Valve", "Steam"):
        nxt = node.get(k)
        if nxt is None or nxt.children is None:
            raise ValueError(f"localconfig.vdf sem a secao '{k}'")
        node = nxt
    steam = node
    apps = steam.get("apps")
    if apps is None:
        text = _insert_after_open(text, steam, ['"apps"', "{",
                                                f'\t"{DOTA_APPID}"', "\t{",
                                                f'\t\t"LaunchOptions"\t\t"{LAUNCH_FLAG}"', "\t}", "}"])
        return text
    app = apps.get(DOTA_APPID)
    if app is None or app.children is None:
        return _insert_after_open(text, apps, [f'"{DOTA_APPID}"', "{",
                                               f'\t"LaunchOptions"\t\t"{LAUNCH_FLAG}"', "}"])
    lo = app.get("LaunchOptions")
    if lo is None:
        return _insert_after_open(text, app, [f'"LaunchOptions"\t\t"{LAUNCH_FLAG}"'])
    if has_flag(_unescape(lo.value)):
        return None
    new_raw = (lo.value.strip() + " " + LAUNCH_FLAG).strip()
    a, b = lo.vspan
    return text[:a] + '"' + new_raw + '"' + text[b:]


def launch_option_state(steam):
    """-> 'ok' | 'falta' | 'sem_conta' (todas as contas do PC)."""
    files = localconfigs(steam)
    if not files:
        return "sem_conta"
    for f in files:
        try:
            _, val, _ = _launch_options_of(f.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            return "falta"
        if not has_flag(_unescape(val) if val else ""):
            return "falta"
    return "ok"


def apply_launch_option(steam):
    """Grava a opcao em todas as contas (Steam precisa estar FECHADA)."""
    changed = 0
    for f in localconfigs(steam):
        raw = f.read_bytes()
        text = raw.decode("utf-8", errors="surrogateescape")
        new = patch_localconfig(text)
        if new is None:
            continue
        bak = f.with_name(f.name + ".copiloto.bak")
        bak.write_bytes(raw)
        _parse(new)   # garante que o resultado continua um VDF valido
        tmp = f.with_name(f.name + ".copiloto.tmp")
        tmp.write_bytes(new.encode("utf-8", errors="surrogateescape"))
        os.replace(tmp, f)
        changed += 1
    return changed


def _close_steam(steam, timeout=40):
    exe = steam / "steam.exe"
    try:
        subprocess.Popen([str(exe), "-shutdown"], close_fds=True)
    except Exception:
        pass
    t0 = time.time()
    while time.time() - t0 < timeout:
        time.sleep(1.5)
        if not steam_running():
            time.sleep(2)   # deixa a Steam terminar de gravar os arquivos dela
            return True
    return False


def _start_steam(steam):
    try:
        DETACHED = 0x00000008 | 0x00000200
        subprocess.Popen([str(steam / "steam.exe")], close_fds=True, creationflags=DETACHED)
    except Exception:
        pass


def status(gsi_fresh=None):
    """Estado atual (sem mudar nada) - pro painel."""
    st = steam_dir()
    d = dota_dir(st) if st else None
    out = {"steam": str(st) if st else None, "dota": str(d) if d else None,
           "gsi_cfg": None, "launch_option": None, "steam_running": steam_running(),
           "receiving": gsi_fresh}
    if d:
        tgt = gsi_cfg_target(d)
        try:
            want = Path(config.GSI_CFG_PATH).read_text(encoding="utf-8").strip()
            out["gsi_cfg"] = "ok" if tgt.exists() and tgt.read_text(
                encoding="utf-8", errors="replace").strip() == want else "falta"
        except Exception:
            out["gsi_cfg"] = "falta"
    if st:
        out["launch_option"] = launch_option_state(st)
    out["ready"] = out["gsi_cfg"] == "ok" and out["launch_option"] in ("ok", "sem_conta")
    STATUS.update(out, checked_at=time.time())
    return out


def provision(close_steam=False, log=print):
    """Faz tudo o que da. close_steam=True fecha e reabre a Steam se precisar
    gravar a opcao de inicializacao com ela aberta. -> dict de status."""
    st = steam_dir()
    if not st:
        log("[SETUP] Steam nao encontrada neste PC")
        return status()
    d = dota_dir(st)
    if not d:
        log("[SETUP] Dota 2 nao encontrado nas bibliotecas da Steam")
    else:
        r = ensure_gsi_cfg(d)
        log(f"[SETUP] GSI cfg: {r} ({gsi_cfg_target(d)})")

    if launch_option_state(st) == "falta":
        running = steam_running()
        if running and not close_steam:
            log("[SETUP] opcao -gamestateintegration pendente: a Steam esta aberta")
        else:
            reopened = False
            if running:
                log("[SETUP] fechando a Steam para gravar a opcao de inicializacao...")
                if not _close_steam(st):
                    log("[SETUP] a Steam nao fechou a tempo; tente de novo")
                    return status()
                reopened = True
            try:
                n = apply_launch_option(st)
                log(f"[SETUP] opcao {LAUNCH_FLAG} gravada em {n} conta(s) da Steam")
            except Exception as e:
                log(f"[SETUP] erro ao gravar a opcao de inicializacao: {e}")
            if reopened:
                _start_steam(st)
    return status()
