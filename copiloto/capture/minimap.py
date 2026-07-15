"""
Minimapa ao vivo (espelho da tela) para a segunda janela.
==========================================================

Captura continuamente SO o cantinho do minimapa do Dota (na tela principal) e
mantem o ultimo frame em memoria como JPEG, servido pelo server.py. Assim a
2a/3a tela mostra um minimapa GRANDE e ao vivo, com as posicoes reais de tudo
que o jogo JA te mostra (aliados sempre; inimigos quando visiveis).

Importante: isto NAO revela nada que o jogo esconde (respeita a fog of war) - e
so um "espelho aumentado" do que ja esta na sua tela. Por isso funciona
ENQUANTO voce joga, diferente do bloco `minimap` do GSI (que so e enviado
quando voce ESPECTА uma partida/replay).

TOTALMENTE ADAPTATIVO: a cada frame o sistema redescobre em qual monitor o Dota
esta E qual a resolucao atual (cria um mss novo por frame). Se voce troca de
monitor ou muda a resolucao no meio do jogo, o minimapa se ajusta sozinho - sem
reiniciar nada. A caixa padrao e proporcional a tela; da pra calibrar fino pela
janela grande (engrenagem), e essa calibracao vale enquanto a resolucao continuar
a mesma (mudou a resolucao -> volta pro padrao proporcional da nova tela).
"""

import io
import threading
import time

# (left, top, right, bottom) do quadrado do minimapa, em pixels, dentro do
# monitor. Comeca vazio: e resolvido sob demanda para a tela atual do Dota.
MINIMAP_BOX = None
# Para qual (largura, altura) de tela a MINIMAP_BOX atual vale. Se a tela do Dota
# passar a ter outra resolucao, recalculamos o padrao proporcional.
_box_basis = None
# Fracao da altura da tela = lado do quadrado do minimapa (canto inferior esq).
# Calibrado a partir de um print real 2560x1600 (340/1600 ~= 0.2125).
MINIMAP_SIDE_FRAC = 0.2125

TARGET_FPS = 6          # quadros por segundo da captura
IDLE_STOP = 4.0         # para de capturar apos X s sem ninguem assistindo
JPEG_QUALITY = 80

_lock = threading.Lock()
_frame = None           # ultimo JPEG (bytes)
_frame_at = 0.0         # timestamp do ultimo frame capturado
_last_request = 0.0     # ultima vez que alguem pediu um frame
_running = False        # ha um grabber rodando?


def _default_box(mon):
    """Caixa padrao do minimapa (quadrado no canto inferior esquerdo) para o
    monitor dado - proporcional a altura, entao serve em qualquer resolucao."""
    side = int(mon["height"] * MINIMAP_SIDE_FRAC)
    return [0, mon["height"] - side, side, mon["height"]]


def _box_for(mon):
    """Caixa a usar para o monitor atual do Dota. Se a resolucao mudou desde a
    ultima vez (ou nunca foi definida), recalcula o padrao proporcional. Uma
    calibracao manual (set_box) so continua valendo com a MESMA resolucao."""
    global MINIMAP_BOX, _box_basis
    basis = (mon["width"], mon["height"])
    with _lock:
        if MINIMAP_BOX is None or _box_basis != basis:
            MINIMAP_BOX = _default_box(mon)
            _box_basis = basis
        return list(MINIMAP_BOX)


def _current_monitor():
    """Monitor onde o Dota esta AGORA (mss novo -> reflete troca/resolucao)."""
    import mss
    from copiloto.capture import screens
    with mss.mss() as sct:
        return screens.dota_monitor(sct)


def set_box(left, top, right, bottom):
    """Calibracao manual da caixa (o grabber pega no proximo frame). Fica
    amarrada a resolucao atual da tela do Dota."""
    global MINIMAP_BOX, _box_basis
    left, top, right, bottom = int(left), int(top), int(right), int(bottom)
    # normaliza para garantir left<right, top<bottom e tamanho minimo
    if right < left:
        left, right = right, left
    if bottom < top:
        top, bottom = bottom, top
    right = max(right, left + 10)
    bottom = max(bottom, top + 10)
    try:
        mon = _current_monitor()
        basis = (mon["width"], mon["height"])
    except Exception:
        basis = None
    with _lock:
        MINIMAP_BOX = [left, top, right, bottom]
        _box_basis = basis


def get_box():
    """Caixa atual, ja adaptada a resolucao/monitor do Dota nesse instante."""
    try:
        return _box_for(_current_monitor())
    except Exception:
        with _lock:
            return list(MINIMAP_BOX) if MINIMAP_BOX else [0, 0, 342, 342]


def _grab_loop():
    """Loop de captura: roda num thread proprio ate ficar ocioso. A cada frame
    redescobre monitor + resolucao (mss novo), entao segue o jogo pra onde for."""
    global _frame, _frame_at, _running
    try:
        import mss
        from copiloto.capture import screens
        from PIL import Image
        period = 1.0 / TARGET_FPS
        while True:
            t0 = time.time()
            if t0 - _last_request > IDLE_STOP:
                break
            # mss novo a cada frame -> pega troca de monitor / mudanca de resolucao
            with mss.mss() as sct:
                mon = screens.dota_monitor(sct)
                box = _box_for(mon)
                region = {
                    "left": mon["left"] + box[0],
                    "top": mon["top"] + box[1],
                    "width": max(1, box[2] - box[0]),
                    "height": max(1, box[3] - box[1]),
                }
                shot = sct.grab(region)
                img = Image.frombytes("RGB", shot.size, shot.rgb)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=JPEG_QUALITY)
            with _lock:
                _frame = buf.getvalue()
                _frame_at = time.time()
            dt = time.time() - t0
            if dt < period:
                time.sleep(period - dt)
    except Exception as e:
        print(f"  (minimapa indisponivel: {e})")
    finally:
        with _lock:
            _running = False


def _ensure_running():
    global _running
    with _lock:
        if _running:
            return
        _running = True
        threading.Thread(target=_grab_loop, daemon=True).start()


def get_frame():
    """Retorna (jpeg_bytes, frame_at). Liga o grabber sob demanda.

    Antes do primeiro frame retorna (None, 0.0). O timestamp serve para o
    stream MJPEG nao reenviar o mesmo quadro duas vezes."""
    global _last_request
    _last_request = time.time()
    _ensure_running()
    with _lock:
        return _frame, _frame_at
