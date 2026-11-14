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

from copiloto import config  # noqa: E402
from copiloto.capture import screens  # deteccao do monitor do Dota (win32)  # noqa: E402

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
        from copiloto.capture import minimap
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
    """MiniMapa Tracker: camada TRANSPARENTE e click-through em cima do minimapa
    do Dota. Desenha so os FANTASMAS (inimigo que sumiu = icone do heroi na ultima
    posicao, com os segundos desde que sumiu, desbotando com o tempo).

    As posicoes vem do GSI (bloco `minimap`, coordenadas do mundo) via
    copiloto/game/ghosts.py - nada de ler a tela. Aqui so se converte mundo ->
    pixel dentro da caixa do minimapa (caixa proporcional a tela + ajuste fino
    salvo). No modo ALINHAR mostra tambem os herois visiveis e as torres, pra
    conferir que o desenho bate com o minimapa do jogo."""

    FPS = 10

    def __init__(self, get_snapshot, get_cfg=None, get_ttl=None, hero_id_of=None):
        super().__init__()
        self._get_snapshot = get_snapshot     # -> ghosts.snapshot()
        self._get_cfg = get_cfg               # -> {"mm_dx","mm_dy","mm_scale","mm_align","portrait"}
        self._get_ttl = get_ttl               # -> segundos (None = nunca)
        self._hero_id_of = hero_id_of         # npc name -> hero_id (icone local)
        self._icons = {}
        self._snap = {}
        self._region = None
        self._scan_i = 0
        self.setWindowFlags(QtCore.Qt.FramelessWindowHint | QtCore.Qt.WindowStaysOnTopHint
                            | QtCore.Qt.Tool)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
        self.setAttribute(QtCore.Qt.WA_ShowWithoutActivating, True)
        self._place()
        self._loop = QtCore.QTimer(self)
        self._loop.timeout.connect(self._tick)
        self._loop.start(int(1000 / self.FPS))
        self._top = QtCore.QTimer(self)
        self._top.timeout.connect(lambda: _set_topmost(int(self.winId())))
        self._top.start(2000)

    def _cfg(self):
        try:
            return (self._get_cfg() if self._get_cfg else {}) or {}
        except Exception:
            return {}

    def _target_region(self):
        """Caixa do minimapa (proporcional a tela do Dota) + ajuste fino salvo."""
        reg = _minimap_region()
        if not reg:
            return None
        left, top, w, h = reg
        c = self._cfg()
        try:
            dx, dy = int(c.get("mm_dx") or 0), int(c.get("mm_dy") or 0)
            sc = float(c.get("mm_scale") or 1.0)
        except (TypeError, ValueError):
            dx, dy, sc = 0, 0, 1.0
        side = max(60, int(round(min(w, h) * sc)))
        return (left + dx, top + (h - side) + dy, side, side)   # cresce pra cima/direita

    def _place(self):
        reg = self._target_region()
        if reg:
            self._region = reg
            self.setGeometry(*reg)

    def init_win(self):
        _set_click_through(int(self.winId()), True)   # sempre click-through
        _set_topmost(int(self.winId()))

    def _tick(self):
        self._scan_i += 1
        if self._region is None or self._scan_i % 10 == 0:   # ~1x/s: monitor, resolucao, ajuste
            reg = self._target_region()
            if reg and reg != self._region:
                self._region = reg
                self.setGeometry(*reg)
                self.init_win()
        try:
            self._snap = self._get_snapshot() or {}
        except Exception as e:
            print(f"  (minimap-overlay: {e})")
            self._snap = {}
        self.update()

    def _pixmap(self, hero_npc):
        if hero_npc in self._icons:
            return self._icons[hero_npc]
        pm = None
        try:
            hid = self._hero_id_of(hero_npc) if self._hero_id_of else None
            path = str(config.CACHE_DIR / "icons" / f"{hid}.png") if hid else ""
            if path and os.path.exists(path):
                pm = QtGui.QPixmap(path)
                if pm.isNull():
                    pm = None
        except Exception:
            pm = None
        self._icons[hero_npc] = pm
        return pm

    def _hero_dot(self, p, x, y, npc, R, ring, alpha):
        pm = self._pixmap(npc)
        p.setOpacity(alpha)
        if pm is not None:
            box = QtCore.QRectF(x - R, y - R, 2 * R, 2 * R)
            p.save()
            clip = QtGui.QPainterPath()
            clip.addEllipse(box)
            p.setClipPath(clip)
            p.drawPixmap(box, pm, QtCore.QRectF(pm.rect()))
            p.restore()
            p.setBrush(QtCore.Qt.NoBrush)
        else:
            p.setBrush(QtGui.QColor(ring.red(), ring.green(), ring.blue(), 110))
        p.setPen(QtGui.QPen(QtGui.QColor(0, 0, 0, 170), 3))
        p.drawEllipse(QtCore.QPointF(x, y), R + 1, R + 1)
        p.setPen(QtGui.QPen(ring, 2))
        p.drawEllipse(QtCore.QPointF(x, y), R, R)
        p.setOpacity(1.0)

    def paintEvent(self, _e):
        snap = self._snap or {}
        align = bool(self._cfg().get("mm_align"))
        ghosts = snap.get("ghosts") or []
        if not ghosts and not align:
            return                              # 100% transparente
        W, H = self.width(), self.height()
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        p.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, True)
        R = max(7.0, W * 0.034)                 # icone proporcional ao minimapa

        if align:   # moldura + herois visiveis + torres: pra conferir o alinhamento
            p.setPen(QtGui.QPen(QtGui.QColor(232, 90, 69, 220), 2, QtCore.Qt.DashLine))
            p.setBrush(QtCore.Qt.NoBrush)
            p.drawRect(1, 1, W - 2, H - 2)
            p.setPen(QtCore.Qt.NoPen)
            p.setBrush(QtGui.QColor(255, 255, 255, 220))
            for b in snap.get("buildings") or []:
                s = 4 if b.get("kind") == "tower" else 3
                p.drawRect(QtCore.QRectF(b["u"] * W - s, b["v"] * H - s, 2 * s, 2 * s))
            me = snap.get("my_team") or 0
            for h in snap.get("visible") or []:
                ring = QtGui.QColor(34, 197, 94) if h.get("team") == me else QtGui.QColor(239, 68, 68)
                self._hero_dot(p, h["u"] * W, h["v"] * H, h["unitname"], R * 0.8, ring, 0.9)

        try:
            ttl = self._get_ttl() if self._get_ttl else None
        except Exception:
            ttl = None
        f = p.font()
        f.setPointSize(max(7, int(R * 0.75)))
        f.setBold(True)
        p.setFont(f)
        for g in ghosts:
            x, y, age = g["u"] * W, g["v"] * H, g["age"]
            alpha = 1.0 - 0.5 * min(1.0, age / ttl) if ttl else 0.9
            self._hero_dot(p, x, y, g["unitname"], R, QtGui.QColor(239, 68, 68), alpha)
            label = f"{int(age)}s"
            tr = QtCore.QRectF(x - 2 * R, y + R, 4 * R, R * 1.4)
            p.setPen(QtGui.QColor(0, 0, 0, 210))
            p.drawText(tr.translated(1, 1), QtCore.Qt.AlignCenter, label)
            p.setPen(QtGui.QColor(255, 255, 255))
            p.drawText(tr, QtCore.Qt.AlignCenter, label)


def _now():
    import time
    return time.time()


def create_minimap_overlay(get_snapshot, get_cfg=None, get_ttl=None, hero_id_of=None):
    """Cria/mostra o MiniMapa Tracker (fantasmas dos inimigos vindos do GSI)."""
    mo = MinimapOverlay(get_snapshot, get_cfg=get_cfg, get_ttl=get_ttl, hero_id_of=hero_id_of)
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
