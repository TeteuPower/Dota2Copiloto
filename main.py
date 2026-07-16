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


if __name__ == "__main__":
    _setup_frozen_logging()
    _ensure_single_instance()
    _hide_subprocess_consoles()
    from copiloto.web.server import main
    main()
