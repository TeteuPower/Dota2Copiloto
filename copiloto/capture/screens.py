"""
Utilitario de telas: descobre em QUAL monitor o Dota 2 esta rodando.
====================================================================

Antes, cada modulo de captura (placar, minimapa, draft) escolhia fixo o monitor
2560x1600. Num setup com varios monitores / outra resolucao, isso capturava a
tela errada (ex.: o VS Code) e nada era lido. Aqui a gente localiza a janela do
Dota 2 e devolve o monitor certo, funcionando em qualquer resolucao.
"""


def dota_monitor(sct):
    """Recebe um mss.MSS() e devolve o dict do monitor onde o Dota 2 esta.

    Estrategia: acha a janela cujo titulo e 'Dota 2' (win32) e escolhe o monitor
    que contem o centro dela. Se nao achar a janela (jogo fechado, sem win32),
    cai pro monitor primario - que e onde o jogo normalmente roda em tela cheia.
    """
    mons = sct.monitors[1:]
    try:
        import win32gui
        rects = []

        def _cb(hwnd, _):
            if win32gui.IsWindowVisible(hwnd) and \
                    win32gui.GetWindowText(hwnd).strip().lower() == "dota 2":
                rects.append(win32gui.GetWindowRect(hwnd))
            return True

        win32gui.EnumWindows(_cb, None)
        if rects:
            l, t, r, b = rects[0]
            cx, cy = (l + r) // 2, (t + b) // 2
            for m in mons:
                if m["left"] <= cx < m["left"] + m["width"] \
                        and m["top"] <= cy < m["top"] + m["height"]:
                    return m
    except Exception as e:
        print(f"[screens] nao consegui localizar a janela do Dota: {e}")
    return next((m for m in mons if m.get("is_primary")), mons[0])
