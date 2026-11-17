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


# ----------------------------------------------------------------------------
# Avisos NA TELA (banners por cima do jogo), no estilo do overlay de referencia
# ----------------------------------------------------------------------------
# tipo -> (regex, icone, cor da barra lateral). A 1a regra que casa com o texto vence.
import re as _re  # noqa: E402

BANNER_TYPES = [
    ("runa-poder", r"runa\s*(de\s*)?poder|power\s*rune", "\u26a1", (169, 112, 255)),
    ("runa-agua", r"runa\s*(d.\s*)?[aá]gua|water\s*rune", "\U0001f4a7", (59, 169, 244)),
    ("runa-ouro", r"runa\s*(de\s*)?(ouro|recompensa)|bounty", "\U0001fa99", (244, 192, 37)),
    ("sabedoria", r"sabedoria|wisdom|runa\s*xp", "\U0001f4d6", (63, 185, 80)),
    ("ward", r"ward|observer|sentry|vis[aã]o", "\U0001f441\ufe0f", (63, 185, 80)),
    ("roshan", r"roshan|\brosh\b|aegis|[eé]gide", "\U0001f432", (255, 93, 93)),
    ("tormentor", r"tormentor", "\U0001f48e", (255, 93, 93)),
    ("stack", r"stack|empilh", "\U0001f4e6", (255, 158, 61)),
    ("lotus", r"lotus|l[oó]tus", "\U0001fab7", (240, 130, 200)),
    ("pull", r"puxe|pull", "\U0001f9f2", (255, 158, 61)),
    ("noite", r"noite|night", "\U0001f319", (130, 140, 220)),
    ("skill", r"^suba |talento", "\u2b06\ufe0f", (214, 170, 64)),
    ("runa", r"runa|rune", "\u26a1", (169, 112, 255)),
]
BANNER_DEFAULT = ("padrao", "\U0001f514", (250, 204, 21))


def classify_banner(text):
    t = str(text or "")
    for tipo, rx, icon, color in BANNER_TYPES:
        if _re.search(rx, t, _re.IGNORECASE):
            return tipo, icon, color
    return BANNER_DEFAULT


class _BannerBridge(QtCore.QObject):
    show = QtCore.Signal(str)


class BannerOverlay(QtWidgets.QWidget):
    """Coluna de avisos no canto superior direito da tela do Dota: fundo escuro,
    barra lateral na cor do tipo, icone + texto, brilho pulsando no inicio,
    entra deslizando e sai esmaecendo. Click-through. No maximo 5 ao mesmo tempo."""

    W, H, GAP, MAX = 360, 54, 10, 5
    TOP, RIGHT = 150, 20

    def __init__(self, get_cfg=None):
        super().__init__()
        self._get_cfg = get_cfg
        self._items = []                 # [{text, icon, color, born}]
        self.bridge = _BannerBridge()
        self.bridge.show.connect(self._add)
        self.setWindowFlags(QtCore.Qt.FramelessWindowHint | QtCore.Qt.WindowStaysOnTopHint
                            | QtCore.Qt.Tool)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
        self.setAttribute(QtCore.Qt.WA_ShowWithoutActivating, True)
        self._anim = QtCore.QTimer(self)
        self._anim.timeout.connect(self._step)
        self._place()

    # thread-safe: pode ser chamado de qualquer thread (avisos, skill)
    def push(self, text):
        self.bridge.show.emit(str(text or "")[:120])

    def _cfg(self):
        try:
            return (self._get_cfg() if self._get_cfg else {}) or {}
        except Exception:
            return {}

    def _dur(self):
        try:
            return max(3.0, min(60.0, float(self._cfg().get("banner_secs") or 8)))
        except (TypeError, ValueError):
            return 8.0

    def _place(self):
        left, top, w, h = _dota_rect()
        hh = self.MAX * (self.H + self.GAP) + 20
        self.setGeometry(left + w - self.W - self.RIGHT - 20, top + self.TOP, self.W + 20, hh)

    def init_win(self):
        _set_click_through(int(self.winId()), True)
        _set_topmost(int(self.winId()))

    def _add(self, text):
        if not text or self._cfg().get("banners") is False:
            return
        tipo, icon, color = classify_banner(text)
        self._items.append({"text": text, "icon": icon, "color": color, "born": _now()})
        self._items = self._items[-self.MAX:]
        if not self.isVisible():
            self.show()
        self._place()
        self.init_win()
        if not self._anim.isActive():
            self._anim.start(33)          # ~30 fps so enquanto ha banner
        self.update()

    def _step(self):
        now, dur = _now(), self._dur()
        self._items = [i for i in self._items if now - i["born"] < dur + 0.35]
        if not self._items:
            self._anim.stop()
        self.update()

    def paintEvent(self, _e):
        if not self._items:
            return
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        p.setRenderHint(QtGui.QPainter.TextAntialiasing, True)
        now, dur = _now(), self._dur()
        f_txt = QtGui.QFont("Segoe UI", 11)
        f_txt.setBold(True)
        f_ico = QtGui.QFont("Segoe UI Emoji", 16)
        import math
        for idx, it in enumerate(self._items):
            age = now - it["born"]
            alpha, dx, dy = 1.0, 0.0, 0.0
            if age < 0.3:                          # entra deslizando de baixo
                k = age / 0.3
                alpha, dy = k, (1 - k) * 20
            elif age > dur:                        # sai esmaecendo pra direita
                k = min(1.0, (age - dur) / 0.35)
                alpha, dx = 1 - k, k * 30
            y = 10 + idx * (self.H + self.GAP) + dy
            rect = QtCore.QRectF(10 + dx, y, self.W - 10, self.H)
            r, g, b = it["color"]
            p.setOpacity(alpha)
            # brilho pulsando nos primeiros 3 s
            if age < 3.0:
                pulse = 0.5 + 0.5 * math.sin(age * 2 * math.pi)
                for k in (8, 5, 2):
                    p.setPen(QtCore.Qt.NoPen)
                    p.setBrush(QtGui.QColor(r, g, b, int(28 * pulse)))
                    p.drawRoundedRect(rect.adjusted(-k, -k, k, k), 8 + k, 8 + k)
            # fundo + borda
            path = QtGui.QPainterPath()
            path.addRoundedRect(rect, 8, 8)
            p.fillPath(path, QtGui.QColor(10, 14, 23, 235))
            p.setPen(QtGui.QPen(QtGui.QColor(250, 204, 21, 100), 1))
            p.setBrush(QtCore.Qt.NoBrush)
            p.drawPath(path)
            # barra lateral colorida (4 px)
            p.save()
            p.setClipPath(path)
            p.fillRect(QtCore.QRectF(rect.left(), rect.top(), 4, rect.height()), QtGui.QColor(r, g, b))
            p.restore()
            # icone + texto
            p.setFont(f_ico)
            p.setPen(QtGui.QColor(255, 255, 255))
            p.drawText(QtCore.QRectF(rect.left() + 14, rect.top(), 34, rect.height()),
                       QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft, it["icon"])
            p.setFont(f_txt)
            p.setPen(QtGui.QColor(238, 243, 248))
            tr = QtCore.QRectF(rect.left() + 52, rect.top(), rect.width() - 62, rect.height())
            txt = QtGui.QFontMetrics(f_txt).elidedText(it["text"], QtCore.Qt.ElideRight, int(tr.width()))
            p.drawText(tr, QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft, txt)
        p.setOpacity(1.0)


class ItemsOverlay(QtWidgets.QWidget):
    """Painel fixo por cima do jogo (logo acima do minimapa): os ITENS PRA COMPRAR
    que a IA recomendou por ultimo e os itens pra VENDER. Atualiza sozinho pelo GSI:
    comprou -> o item sai; tem componentes -> barrinha de progresso; ouro suficiente
    -> borda verde. Click-through; Tab+F6 esconde."""

    W, ICON_W, ICON_H, H = 300, 56, 41, 190

    def __init__(self, get_data, get_cfg=None, save_cfg=None):
        super().__init__()
        self._get_data = get_data
        self._get_cfg = get_cfg
        self._save_cfg = save_cfg   # callable(dict) - grava a posicao depois de arrastar
        self._editing = False
        self._drag_off = None
        self._base = (0, 0)         # posicao padrao (sem o ajuste) -> dx/dy = atual - base
        self._data = {}
        self._img = {}            # url -> bytes (b"" = falhou) | None (baixando)
        self._pm = {}             # url -> QPixmap | False
        self._geo = None
        self.setWindowFlags(QtCore.Qt.FramelessWindowHint | QtCore.Qt.WindowStaysOnTopHint
                            | QtCore.Qt.Tool)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
        self.setAttribute(QtCore.Qt.WA_ShowWithoutActivating, True)
        self._loop = QtCore.QTimer(self)
        self._loop.timeout.connect(self._tick)
        self._loop.start(700)
        self._n = 0
        self._place()

    def _cfg(self):
        try:
            return (self._get_cfg() if self._get_cfg else {}) or {}
        except Exception:
            return {}

    def _place(self):
        reg = _minimap_region()
        c = self._cfg()
        try:
            dx, dy = int(c.get("ip_dx") or 0), int(c.get("ip_dy") or 0)
        except (TypeError, ValueError):
            dx, dy = 0, 0
        if reg:
            left, top, w, h = reg
            self._base = (left + 6, top - self.H - 8)
        else:
            l, t, w, h = _dota_rect()
            self._base = (l + 10, t + h - 440)
        geo = (self._base[0] + dx, self._base[1] + dy, self.W, self.H)
        if self._drag_off is not None:
            return                          # arrastando: quem manda e o mouse
        if geo != self._geo:
            self._geo = geo
            self.setGeometry(*geo)

    def init_win(self):
        _set_click_through(int(self.winId()), not self._editing)
        _set_topmost(int(self.winId()))

    # --- modo de mover (Insert ou o botao do painel): arraste com o mouse ---
    def _set_editing(self, on):
        if on == self._editing:
            return
        self._editing = on
        self.setCursor(QtCore.Qt.SizeAllCursor if on else QtCore.Qt.ArrowCursor)
        if on and not self.isVisible():
            self.show()
        self.init_win()
        self.update()

    def mousePressEvent(self, e):
        if self._editing and e.button() == QtCore.Qt.LeftButton:
            self._drag_off = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self._editing and self._drag_off is not None:
            self.move(e.globalPosition().toPoint() - self._drag_off)

    def mouseReleaseEvent(self, e):
        if self._drag_off is None:
            return
        self._drag_off = None
        g = self.frameGeometry()
        dx, dy = g.left() - self._base[0], g.top() - self._base[1]
        self._geo = (g.left(), g.top(), self.W, self.H)
        if self._save_cfg:
            try:
                self._save_cfg({"ip_dx": int(dx), "ip_dy": int(dy)})
            except Exception as ex:
                print(f"  (painel de itens: nao salvei a posicao: {ex})")

    def _fetch(self, url):
        import threading

        def _go():
            try:
                import urllib.request
                req = urllib.request.Request(url, headers={"User-Agent": "CopilotoDota2"})
                with urllib.request.urlopen(req, timeout=10) as r:
                    self._img[url] = r.read()
            except Exception:
                self._img[url] = b""
        self._img[url] = None
        threading.Thread(target=_go, daemon=True).start()

    def _pix(self, url):
        if not url:
            return None
        if url in self._pm:
            return self._pm[url] or None
        if url not in self._img:
            self._fetch(url)
            return None
        b = self._img[url]
        if b is None:
            return None
        pm = QtGui.QPixmap()
        if b:
            pm.loadFromData(b)
        self._pm[url] = pm if not pm.isNull() else False
        return self._pm[url] or None

    def _tick(self):
        on = self._cfg().get("items_panel", True)
        try:
            self._data = (self._get_data() or {}) if on else {}
        except Exception as e:
            print(f"  (painel de itens: {e})")
            self._data = {}
        self._set_editing(bool(self._cfg().get("ip_edit")))
        self._n += 1
        if self._n % 3 == 0:
            self._place()
        self.update()

    def paintEvent(self, _e):
        d = self._data or {}
        buy, sell = (d.get("buy") or [])[:5], (d.get("sell") or [])[:4]
        if self._editing:   # moldura pra arrastar (mesmo sem itens)
            p = QtGui.QPainter(self)
            p.setRenderHint(QtGui.QPainter.Antialiasing, True)
            r = QtCore.QRectF(1, 1, self.W - 2, self.H - 2)
            p.setBrush(QtGui.QColor(10, 14, 23, 120))
            p.setPen(QtGui.QPen(QtGui.QColor(232, 90, 69), 2, QtCore.Qt.DashLine))
            p.drawRoundedRect(r, 9, 9)
            f = QtGui.QFont("Segoe UI", 9)
            f.setBold(True)
            p.setFont(f)
            p.setPen(QtGui.QColor(255, 220, 200))
            p.drawText(QtCore.QRectF(0, 4, self.W, 18), QtCore.Qt.AlignCenter,
                       "ARRASTE O PAINEL · Insert pra travar")
            p.end()
            if not buy and not sell:
                return
        if not buy and not sell and not d.get("bought_all"):
            return
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        p.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, True)
        rows = (1 if (buy or d.get("bought_all")) else 0) + (1 if sell else 0)
        h = 10 + rows * (self.ICON_H + 30)
        rect = QtCore.QRectF(0.5, self.height() - h - 0.5, self.W - 1, h)
        path = QtGui.QPainterPath()
        path.addRoundedRect(rect, 9, 9)
        p.fillPath(path, QtGui.QColor(10, 14, 23, 215))
        p.setPen(QtGui.QPen(QtGui.QColor(214, 170, 64, 130), 1))
        p.drawPath(path)
        f_h = QtGui.QFont("Segoe UI", 8)
        f_h.setBold(True)
        gold = d.get("gold") or 0
        y = rect.top() + 6

        def header(txt, col, yy):
            p.setFont(f_h)
            p.setPen(col)
            p.drawText(QtCore.QRectF(10, yy, self.W - 20, 16), QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, txt)

        def icon(x, yy, it, dim=False):
            r = QtCore.QRectF(x, yy, self.ICON_W, self.ICON_H)
            pm = self._pix(it.get("img") or "")
            p.setOpacity(0.5 if dim else 1.0)
            if pm:
                p.drawPixmap(r, pm, QtCore.QRectF(pm.rect()))
            else:
                p.fillRect(r, QtGui.QColor(30, 38, 52))
            p.setOpacity(1.0)
            return r

        if buy or d.get("bought_all"):
            header(("COMPRAR · META DO HERÓI (Tab+F5 = da IA)" if d.get("source") == "meta" else "COMPRAR")
                   if buy else "COMPROU TUDO · Tab+F5 pra novos", QtGui.QColor(230, 184, 102), y)
            y += 20
            x = 10
            for i, it in enumerate(buy):
                cost, prog = it.get("cost") or 0, it.get("progress") or 0
                afford = bool(cost) and gold >= cost * (1 - prog)
                r = icon(x, y, it)
                p.setBrush(QtCore.Qt.NoBrush)
                if afford:
                    pen = QtGui.QPen(QtGui.QColor(92, 218, 106), 2)
                elif i == 0:
                    pen = QtGui.QPen(QtGui.QColor(214, 170, 64), 2)
                else:
                    pen = QtGui.QPen(QtGui.QColor(60, 72, 92), 1)
                p.setPen(pen)
                p.drawRoundedRect(r, 4, 4)
                if prog > 0:                                   # componentes ja comprados
                    p.fillRect(QtCore.QRectF(r.left(), r.bottom() - 4, r.width(), 4), QtGui.QColor(0, 0, 0, 190))
                    p.fillRect(QtCore.QRectF(r.left(), r.bottom() - 4, r.width() * min(1.0, prog), 4),
                               QtGui.QColor(92, 218, 106))
                x += self.ICON_W + 2
            y += self.ICON_H + 10
        if sell:
            header("VENDER", QtGui.QColor(255, 120, 120), y)
            y += 20
            x = 10
            for it in sell:
                r = icon(x, y, it, dim=True)
                p.setPen(QtGui.QPen(QtGui.QColor(255, 93, 93), 2))
                p.setBrush(QtCore.Qt.NoBrush)
                p.drawRoundedRect(r, 4, 4)
                p.drawLine(r.topLeft() + QtCore.QPointF(7, 7), r.bottomRight() - QtCore.QPointF(7, 7))
                x += self.ICON_W + 2


def create_items_overlay(get_data, get_cfg=None, save_cfg=None):
    io = ItemsOverlay(get_data, get_cfg=get_cfg, save_cfg=save_cfg)
    io.show()
    io.init_win()
    return io


def create_banner_overlay(get_cfg=None):
    bo = BannerOverlay(get_cfg=get_cfg)
    bo.init_win()
    return bo


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
