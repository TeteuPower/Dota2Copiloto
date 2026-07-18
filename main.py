"""
Copiloto Dota 2 - ponto de entrada.
====================================

Dev:       python main.py   (ou 2 cliques no iniciar.bat) - logs no terminal.
Instalado: CopilotoDota2.exe (sem console) - logs em %LOCALAPPDATA%/CopilotoDota2/logs.
Painel:    http://localhost:49317
"""

import sys
import time


def _setup_frozen_logging():
    """No exe sem console nao existe stdout/stderr: manda tudo pro arquivo de log
    (senao prints somem e excecoes morrem em silencio)."""
    if not getattr(sys, "frozen", False):
        return
    from copiloto import config
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    logfile = config.LOG_DIR / "copiloto.log"
    try:  # rotacao simples: acima de ~1 MB vira .old (mantem 1 anterior)
        if logfile.exists() and logfile.stat().st_size > 1_000_000:
            old = config.LOG_DIR / "copiloto.old.log"
            old.unlink(missing_ok=True)
            logfile.rename(old)
    except OSError:
        pass
    f = open(logfile, "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stderr = f
    print(f"\n==== Copiloto iniciado em {time.strftime('%Y-%m-%d %H:%M:%S')} "
          f"| versao {config.APP_VERSION} ====")


def _ensure_single_instance():
    """Instancia UNICA via mutex do Windows.

    Nao da pra confiar no "porta ocupada": o servidor HTTP usa SO_REUSEADDR e,
    no Windows, isso deixa DOIS processos escutarem a mesma porta. O mutex e
    a garantia de verdade. Se ja existe outro Copiloto: abre o painel e sai."""
    import ctypes
    ERROR_ALREADY_EXISTS = 183
    ctypes.windll.kernel32.CreateMutexW(None, False, "Local\\CopilotoDota2_instancia")
    if ctypes.windll.kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
        from copiloto import config
        print("[BOOT] o Copiloto ja esta rodando - abrindo o painel e saindo.")
        if getattr(sys, "frozen", False):
            import webbrowser
            webbrowser.open(f"http://localhost:{config.PORT}")
        sys.exit(0)


def _hide_subprocess_consoles():
    """Exe SEM console: subprocessos de console (ex.: o CLI do claude, chamado
    pelo Agent SDK) ganhariam uma janela preta propria piscando na tela. Forca
    CREATE_NO_WINDOW em todo subprocess criado pelo app."""
    if not getattr(sys, "frozen", False):
        return
    import subprocess
    CREATE_NO_WINDOW = 0x08000000
    orig = subprocess.Popen.__init__

    def patched(self, *args, **kwargs):
        kwargs["creationflags"] = kwargs.get("creationflags", 0) | CREATE_NO_WINDOW
        return orig(self, *args, **kwargs)

    subprocess.Popen.__init__ = patched


def _selftest_scan():
    """Diagnostico no ambiente REAL (congelado ou nao): roda a leitura do placar
    pelo caminho REAL do app - imagem INLINE (base64) junto do prompt, sem a
    ferramenta Read - numa imagem sintetica, e grava o resultado num arquivo.
    Valida que o scan inline funciona no exe sem console (o bug do stderr herdado
    ja fica coberto: _ask usa o callback de stderr). Nao sobe servidor."""
    import time
    from PIL import Image, ImageDraw, ImageFont
    from copiloto import config
    from copiloto.capture import scoreboard

    # Placar sintetico legivel, com nomes de heroi reais -> leitura significativa.
    try:
        F = ImageFont.truetype("arialbd.ttf", 26)
        Fs = ImageFont.truetype("arial.ttf", 20)
    except Exception:
        F = Fs = ImageFont.load_default()
    im = Image.new("RGB", (720, 460), (12, 16, 24))
    d = ImageDraw.Draw(im)

    def row(y, player, hero, k, mo, a):
        d.text((30, y), player, font=Fs, fill=(210, 215, 225))
        d.text((30, y + 22), hero, font=F, fill=(240, 235, 210))
        d.text((460, y + 10), f"{k}   {mo}   {a}", font=F, fill=(235, 235, 235))

    d.text((30, 16), "OS ILUMINADOS", font=F, fill=(120, 200, 120))
    row(56, "Sofia", "LICH", 2, 1, 9)
    row(116, "Bruno", "JUGGERNAUT", 7, 2, 4)
    d.text((30, 240), "OS TEMIDOS", font=F, fill=(200, 120, 120))
    row(280, "Rafa", "PUDGE", 3, 5, 6)
    row(340, "Duda", "SNIPER", 9, 3, 2)
    im.save(scoreboard.CROP_PATH)

    lines = [f"frozen={getattr(sys, 'frozen', False)}  v{config.APP_VERSION}  (leitura INLINE)"]
    t0 = time.time()
    try:
        data, reason = scoreboard._claude_read()   # caminho REAL: imagem inline
        dt = time.time() - t0
        if data:
            def kda(rows):
                return [(p.get("heroi"), f"{p.get('k')}/{p.get('d')}/{p.get('a')}") for p in rows]
            lines.append(f"[INLINE] OK em {dt:.0f}s -> iluminados={kda(data.get('iluminados', []))} "
                         f"temidos={kda(data.get('temidos', []))}")
        else:
            lines.append(f"[INLINE] SEM DADOS em {dt:.0f}s -> {reason}")
    except Exception as e:
        lines.append(f"[INLINE] {type(e).__name__} em {time.time()-t0:.0f}s: {str(e)[:140]}")
    (config.DATA_DIR / "selftest_scan.txt").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    _setup_frozen_logging()
    if "--selftest-scan" in sys.argv:
        _hide_subprocess_consoles()   # replica o comportamento do app instalado
        _selftest_scan()
        sys.exit(0)
    _ensure_single_instance()
    _hide_subprocess_consoles()
    from copiloto.web.server import main
    main()
