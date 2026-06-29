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

Calibrado para 2560x1600. Se sua resolucao/HUD for diferente, ajuste a caixa
pela propria janela grande (botao de engrenagem) ou edite MINIMAP_BOX aqui.
"""

import io
import threading
import time

MON_W, MON_H = 2560, 1600
# (left, top, right, bottom) do quadrado do minimapa, em pixels, na tela cheia.
# Calibrado a partir de um print real 2560x1600 (canto inferior esquerdo).
MINIMAP_BOX = [0, 1260, 342, 1600]

TARGET_FPS = 6          # quadros por segundo da captura
IDLE_STOP = 4.0         # para de capturar apos X s sem ninguem assistindo
JPEG_QUALITY = 80

_lock = threading.Lock()
_frame = None           # ultimo JPEG (bytes)
_frame_at = 0.0         # timestamp do ultimo frame capturado
_last_request = 0.0     # ultima vez que alguem pediu um frame
_running = False        # ha um grabber rodando?


def set_box(left, top, right, bottom):
    """Atualiza a caixa de recorte do minimapa (o grabber pega na hora)."""
    left, top, right, bottom = int(left), int(top), int(right), int(bottom)
    # normaliza para garantir left<right, top<bottom e tamanho minimo
    if right < left:
        left, right = right, left
    if bottom < top:
        top, bottom = bottom, top
    right = max(right, left + 10)
    bottom = max(bottom, top + 10)
    with _lock:
        MINIMAP_BOX[:] = [left, top, right, bottom]


def get_box():
    with _lock:
        return list(MINIMAP_BOX)


def _grab_loop():
    """Loop de captura: roda num thread proprio ate ficar ocioso."""
    global _frame, _frame_at, _running
    try:
        import mss
        from PIL import Image
        period = 1.0 / TARGET_FPS
        with mss.mss() as sct:
            mon = next((m for m in sct.monitors[1:]
                        if m["width"] == MON_W and m["height"] == MON_H),
                       sct.monitors[1])
            while True:
                t0 = time.time()
                if t0 - _last_request > IDLE_STOP:
                    break
                box = get_box()
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
