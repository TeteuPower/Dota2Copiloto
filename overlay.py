"""
Overlay do Copiloto (prova de conceito) - PySide6.
==================================================

Janela TRANSPARENTE, sempre no TOPO e CLICK-THROUGH (o clique passa direto pro
jogo), posicionada no monitor onde o Dota esta. Objetivo desta fase: PROVAR que
da pra desenhar por cima do jogo sem atrapalhar. Depois isso vira o HUD de verdade
(itens, ameacas, "agora") reaproveitando esta mesma base.

IMPORTANTE: rode o Dota em "Tela cheia em janela" (borderless). Em tela cheia
EXCLUSIVA nenhum overlay simples aparece por cima.

Obs.: ferramentas de captura de tela (mss/print) podem NAO enxergar este overlay
por cima do jogo ("independent flip" do Windows), mas o olho humano ve normal.
Rode o script pela SUA sessao (terminal/atalho) para a janela aparecer na sua tela.

Atalhos globais (funcionam ate com o jogo em foco):
  Tab+F6    -> mostra / esconde o overlay (nao atrapalha o Tab do placar)
  F10       -> alterna click-through <-> interativo (pra arrastar e reposicionar)
  Ctrl+F9   -> fecha o overlay

Rodar:  python overlay.py
"""

import os
import sys
import ctypes
from ctypes import wintypes

# Qt em pixels FISICOS (casa com as coordenadas de tela do win32/mss).
os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "0")
os.environ.setdefault("QT_SCALE_FACTOR", "1")

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402

import screens  # reusa a deteccao do monitor do Dota (win32)

try:
    import mss
except Exception:
    mss = None
try:
    import keyboard
except Exception:
    keyboard = None

# ---- win32: click-through / nao-rouba-foco / topmost ----
GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOOLWINDOW = 0x00000080
HWND_TOPMOST = -1
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010

_u = ctypes.windll.user32
_u.GetWindowLongPtrW.restype = ctypes.c_ssize_t
_u.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
_u.SetWindowLongPtrW.restype = ctypes.c_ssize_t
_u.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
_u.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int,
                            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint]


def _set_click_through(hwnd, on):
    """click-through/no-activate/tool no nivel do Windows (helper compartilhado)."""
    ex = _u.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
    ex |= WS_EX_LAYERED | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW
    if on:
        ex |= WS_EX_TRANSPARENT
    else:
        ex &= ~WS_EX_TRANSPARENT
    _u.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, ex)


def _set_topmost(hwnd):
    _u.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                    SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)


def _minimap_region():
    """(left, top, w, h) FISICO do minimapa do Dota na tela, ou None.
    Reusa screens.dota_monitor + minimap.get_box (a mesma caixa da 2a janela)."""
    if mss is None:
        return None
    try:
        import minimap
        with mss.MSS() as sct:
            mon = screens.dota_monitor(sct)
        box = minimap.get_box()
        if not box:
            return None
        return (mon["left"] + box[0], mon["top"] + box[1],
                box[2] - box[0], box[3] - box[1])
    except Exception:
        return None


def _dota_rect():
    """(left, top, width, height) do monitor onde o Dota esta (pixels fisicos).
    Sem Dota/mss -> um retangulo padrao pra dar pra testar mesmo assim."""
    if mss is None:
        return (200, 200, 1280, 720)
    try:
        with mss.MSS() as sct:
            m = screens.dota_monitor(sct)
            return (m["left"], m["top"], m["width"], m["height"])
    except Exception:
        return (200, 200, 1280, 720)


STYLE = """
#card {
  background: rgba(9, 14, 22, 0.82);
  border: 1px solid rgba(200,170,110,0.45);
  border-radius: 12px;
}
#card[interactive="true"] { border: 1px solid rgba(232,90,69,0.9); }
#title { color: #e6b866; font-size: 14px; font-weight: 700; letter-spacing: 1px; }
#clock { color: #eef3f8; font-size: 26px; font-weight: 700; }
#mode  { color: #7ec94f; font-size: 11px; font-weight: 600; }
#mode[interactive="true"] { color: #ff8a70; }
#hint  { color: #8595a8; font-size: 10px; }
"""


class Overlay(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(
            QtCore.Qt.FramelessWindowHint
            | QtCore.Qt.WindowStaysOnTopHint
            | QtCore.Qt.Tool
        )
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
        self.setAttribute(QtCore.Qt.WA_ShowWithoutActivating, True)
        self._click_through = True
        self._drag_off = None
        self._build_ui()
        self._place()

        self._clk = QtCore.QTimer(self)
        self._clk.timeout.connect(self._tick)
        self._clk.start(1000)
        self._tick()
        self._top = QtCore.QTimer(self)
        self._top.timeout.connect(self._reassert_top)
        self._top.start(2000)

    # ---------- UI ----------
    def _build_ui(self):
        self.card = QtWidgets.QFrame(self)
        self.card.setObjectName("card")
        inner = QtWidgets.QVBoxLayout(self.card)
        inner.setContentsMargins(16, 12, 16, 14)
        inner.setSpacing(3)
        self.title = QtWidgets.QLabel("● COPILOTO — OVERLAY")
        self.title.setObjectName("title")
        self.clock = QtWidgets.QLabel("--:--:--")
        self.clock.setObjectName("clock")
        self.mode = QtWidgets.QLabel("modo: click-through (o clique vai pro jogo)")
        self.mode.setObjectName("mode")
        self.hint = QtWidgets.QLabel("Tab+F6 mostra/esconde · F10 interagir · Ctrl+F9 fechar")
        self.hint.setObjectName("hint")
        for w in (self.title, self.clock, self.mode, self.hint):
            inner.addWidget(w)
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self.card)
        self.setStyleSheet(STYLE)

    def _place(self):
        left, top, w, h = _dota_rect()
        W, H = 360, 150
        self.setGeometry(left + (w - W) // 2, top + 26, W, H)

    def _tick(self):
        self.clock.setText(QtCore.QDateTime.currentDateTime().toString("HH:mm:ss"))

    # ---------- win32 ----------
    def _hwnd(self):
        return int(self.winId())

    def apply_exstyle(self):
        """Aplica click-through/no-activate/tool no nivel do Windows."""
        ex = _u.GetWindowLongPtrW(self._hwnd(), GWL_EXSTYLE)
        ex |= WS_EX_LAYERED | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW
        if self._click_through:
            ex |= WS_EX_TRANSPARENT
        else:
            ex &= ~WS_EX_TRANSPARENT
        _u.SetWindowLongPtrW(self._hwnd(), GWL_EXSTYLE, ex)

    def _reassert_top(self):
        _u.SetWindowPos(self._hwnd(), HWND_TOPMOST, 0, 0, 0, 0,
                        SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)

    # ---------- acoes ----------
    def init_win(self):
        self.apply_exstyle()
        self._reassert_top()

    def toggle_visible(self):
        if self.isVisible():
            self.hide()
        else:
            self.show()
            self.init_win()

    def toggle_click_through(self):
        self._click_through = not self._click_through
        self.apply_exstyle()
        interactive = "true" if not self._click_through else "false"
        self.mode.setText("modo: click-through (o clique vai pro jogo)"
                          if self._click_through else "modo: INTERATIVO (arraste pra mover)")
        for w in (self.card, self.mode):
            w.setProperty("interactive", interactive)
            w.style().unpolish(w)
            w.style().polish(w)

    # arrastar quando interativo
    def mousePressEvent(self, e):
        if e.button() == QtCore.Qt.LeftButton and not self._click_through:
            self._drag_off = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self._drag_off is not None and not self._click_through:
            self.move(e.globalPosition().toPoint() - self._drag_off)

    def mouseReleaseEvent(self, e):
        self._drag_off = None


class Bridge(QtCore.QObject):
    """Ponte thread-safe: a lib 'keyboard' dispara em outra thread; convertemos
    em signals do Qt pra mexer na UI com seguranca."""
    toggle = QtCore.Signal()
    click = QtCore.Signal()
    quit = QtCore.Signal()


# Mantem a Bridge viva (evita GC dos signals) quando usado como biblioteca.
_BRIDGE = None


class MinimapOverlay(QtWidgets.QWidget):
    """Overlay TRANSPARENTE alinhado ao minimapa do Dota. 100% invisivel, exceto
    pelos FANTASMAS: inimigos que estavam visiveis e sumiram na fog, desenhados
    na ultima posicao com o tempo desde que sumiram. Nao altera o minimapa real.

    A cada ~6fps: captura a regiao do minimapa -> detecta inimigos por cor ->
    atualiza o rastreador -> repinta so os fantasmas."""

    FPS = 6

    def __init__(self, get_team=None, ghost_ttl=None):
        super().__init__()
        # get_team: callable -> 'radiant'/'dire'/None (o MEU time, via GSI)
        # ghost_ttl: segundos ate o fantasma expirar (None = usa o default do tracker)
        self._get_team = get_team
        self._ghost_ttl = ghost_ttl
        self.setWindowFlags(
            QtCore.Qt.FramelessWindowHint
            | QtCore.Qt.WindowStaysOnTopHint
            | QtCore.Qt.Tool
        )
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
        self.setAttribute(QtCore.Qt.WA_ShowWithoutActivating, True)

        import minimap_track
        self._mt = minimap_track
        self._tracker = (minimap_track.EnemyTracker(max_ghost_age=ghost_ttl)
                         if ghost_ttl is not None else minimap_track.EnemyTracker())
        self._ghosts = {}          # cor -> (x, y, since)
        self._region = None        # (left, top, w, h)
        self._scan_i = 0

        self._place()
        self._loop = QtCore.QTimer(self)
        self._loop.timeout.connect(self._tick)
        self._loop.start(int(1000 / self.FPS))
        self._top = QtCore.QTimer(self)
        self._top.timeout.connect(lambda: _set_topmost(int(self.winId())))
        self._top.start(2000)

    def _place(self):
        reg = _minimap_region()
        if reg:
            self._region = reg
            self.setGeometry(*reg)

    def init_win(self):
        _set_click_through(int(self.winId()), True)   # sempre click-through
        _set_topmost(int(self.winId()))

    def _team(self):
        try:
            return self._get_team() if self._get_team else None
        except Exception:
            return None

    def _tick(self):
        if mss is None:
            return
        # Re-checa a regiao de vez em quando (o Dota pode mudar de monitor/resolucao)
        self._scan_i += 1
        if self._region is None or self._scan_i % 30 == 0:
            reg = _minimap_region()
            if reg and reg != self._region:
                self._region = reg
                self.setGeometry(*reg)
                self.init_win()
        if not self._region:
            return
        left, top, w, h = self._region
        try:
            import numpy as np
            with mss.MSS() as sct:
                shot = sct.grab({"left": left, "top": top, "width": w, "height": h})
            bgr = np.ascontiguousarray(
                np.frombuffer(shot.raw, np.uint8).reshape(shot.height, shot.width, 4)[:, :, :3])
            palette = self._mt.enemy_palette(self._team())
            det = self._mt.detect(bgr, palette)
            self._ghosts = dict(self._tracker.update(det, _now()))
        except Exception as e:
            print(f"  (minimap-overlay: {e})")
            return
        self.update()

    def paintEvent(self, _e):
        # 100% transparente: so desenha se houver fantasma
        if not self._ghosts:
            return
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        now = _now()
        f = p.font()
        f.setPointSize(7)
        f.setBold(True)
        p.setFont(f)
        for name, (x, y, since) in self._ghosts.items():
            r, g, b = self._mt.DRAW_RGB.get(name, (255, 255, 255))
            col = QtGui.QColor(r, g, b)
            # anel na cor do heroi + miolo translucido (parece "eco"/fantasma)
            p.setPen(QtGui.QPen(col, 2))
            p.setBrush(QtGui.QColor(r, g, b, 70))
            p.drawEllipse(QtCore.QPointF(x, y), 6.5, 6.5)
            # tempo desde que sumiu (com sombra pra ler sobre o minimapa)
            secs = int(now - since)
            label = f"{secs}s"
            tx, ty = int(x) - 12, int(y) + 8
            p.setPen(QtGui.QColor(0, 0, 0, 200))
            p.drawText(QtCore.QRectF(tx + 1, ty + 1, 26, 12), QtCore.Qt.AlignCenter, label)
            p.setPen(QtGui.QColor(255, 255, 255))
            p.drawText(QtCore.QRectF(tx, ty, 26, 12), QtCore.Qt.AlignCenter, label)


def _now():
    import time
    return time.time()


def create_minimap_overlay(get_team=None, ghost_ttl=None):
    """Cria/mostra o overlay-fantasma do minimapa.
      get_team:  callable -> o MEU time (radiant/dire) via GSI.
      ghost_ttl: segundos ate o fantasma expirar (None = default 2 min)."""
    mo = MinimapOverlay(get_team=get_team, ghost_ttl=ghost_ttl)
    mo.show()
    mo.init_win()
    return mo


def wire_group(app, overlays, card=None):
    """Tab+F6 mostra/esconde TODOS os overlays de uma vez; F10 alterna o modo
    interativo do 'card' (se houver). Nao registra Ctrl+F9 (o app fecha pelo painel)."""
    global _BRIDGE
    bridge = Bridge()

    def toggle_all():
        vis = any(o.isVisible() for o in overlays)
        for o in overlays:
            if vis:
                o.hide()
            else:
                o.show()
                o.init_win()

    bridge.toggle.connect(toggle_all)
    if card is not None:
        bridge.click.connect(card.toggle_click_through)
    if keyboard:
        try:
            keyboard.add_hotkey("tab+f6", lambda: bridge.toggle.emit(), suppress=False)
            if card is not None:
                keyboard.add_hotkey("f10", lambda: bridge.click.emit())
        except Exception as e:
            print(f"  (atalhos do overlay indisponiveis: {e})")
    _BRIDGE = bridge
    return bridge


def create_overlay():
    """Cria, mostra e prepara o overlay. Requer uma QApplication ja existente
    na thread principal. Devolve o widget Overlay."""
    ov = Overlay()
    ov.show()
    ov.init_win()
    return ov


def wire_hotkeys(app, ov, include_quit=True):
    """Liga os atalhos globais ao overlay:
      Tab+F6 -> mostra/esconde ; F10 -> interagir ; Ctrl+F9 -> fecha (se include_quit).
    Guarda a Bridge em _BRIDGE p/ nao ser coletada. Devolve a Bridge."""
    global _BRIDGE
    bridge = Bridge()
    bridge.toggle.connect(ov.toggle_visible)
    bridge.click.connect(ov.toggle_click_through)
    if include_quit:
        bridge.quit.connect(app.quit)
    if keyboard:
        try:
            keyboard.add_hotkey("tab+f6", lambda: bridge.toggle.emit(), suppress=False)
            keyboard.add_hotkey("f10", lambda: bridge.click.emit())
            if include_quit:
                keyboard.add_hotkey("ctrl+f9", lambda: bridge.quit.emit())
        except Exception as e:
            print(f"  (atalhos do overlay indisponiveis: {e})")
    _BRIDGE = bridge
    return bridge


def main():
    """Modo standalone (python overlay.py) - roda o overlay sozinho."""
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    ov = create_overlay()
    wire_hotkeys(app, ov, include_quit=True)
    print("Overlay no ar. Tab+F6 mostra/esconde | F10 interagir | Ctrl+F9 fechar")
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
