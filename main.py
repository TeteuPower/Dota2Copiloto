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


if __name__ == "__main__":
    _setup_frozen_logging()
    from copiloto.web.server import main
    main()
