"""
Visao: detecta os herois no placar do topo do Dota por leitura de tela.
=====================================================================

O placar do topo (sempre visivel em jogo, e populado no All Pick conforme se
escolhe) mostra 5 herois Radiant (esquerda) e 5 Dire (direita), ao redor do
relogio central. Capturamos a tela, recortamos os 10 slots e identificamos cada
heroi por template matching contra os retratos da OpenDota.

Calibrado para 2560x1600 (ajustavel via GEOM). Usa mss + OpenCV + numpy.
"""

import json
import os
import urllib.request

import numpy as np

# cache/ fica na RAIZ do repo (este script mora em scripts/)
CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cache")
PORTRAIT_DIR = os.path.join(CACHE_DIR, "portraits")
ICON_DIR = os.path.join(CACHE_DIR, "icons")
USE_ICONS = True  # icones (rosto) batem melhor com o placar do que a landscape

# Geometria do placar (coordenadas no frame 2560x1600). Ajustavel na calibracao.
GEOM = {
    "y0": 2, "slot_h": 76, "slot_w": 84,
    "left_x0": 710,    # x do 1o slot do time da esquerda (Radiant)
    "right_x0": 1440,  # x do 1o slot do time da direita (Dire)
    "gap": 0,          # espaco extra entre slots (0 = encostados)
}

_TEMPLATES = {}  # {hero_id: {"fp": vetor de assinatura de cor}}


def _heroes():
    with open(os.path.join(CACHE_DIR, "heroes.json"), encoding="utf-8") as f:
        return json.load(f)


def ensure_portraits():
    """Baixa retratos landscape (portraits/) e icones de rosto (icons/) 1x."""
    os.makedirs(PORTRAIT_DIR, exist_ok=True)
    os.makedirs(ICON_DIR, exist_ok=True)
    n = 0
    for h in _heroes():
        jobs = [(h["img_url"], os.path.join(PORTRAIT_DIR, f"{h['id']}.png")),
                (h["img_url"].replace("/heroes/", "/heroes/icons/"),
                 os.path.join(ICON_DIR, f"{h['id']}.png"))]
        for url, dst in jobs:
            if os.path.exists(dst):
                continue
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "CopilotoDota2/0.1"})
                with urllib.request.urlopen(req, timeout=20) as r:
                    data = r.read()
                with open(dst, "wb") as f:
                    f.write(data)
                n += 1
            except Exception as e:
                print(f"  falhou {dst}: {e}")
    return n


def _fingerprint(bgr, size=20):
    """Assinatura de cor: imagem reduzida a size x size, achatada, normalizada
    (media zero, norma 1). Comparacao por produto escalar = correlacao de cor."""
    import cv2
    s = cv2.resize(bgr, (size, size)).astype(np.float32)
    v = s.flatten()
    v -= v.mean()
    n = np.linalg.norm(v)
    return v / n if n > 0 else v


def _load_templates():
    """Carrega os retratos (quadrado central) como assinaturas de cor."""
    import cv2
    if _TEMPLATES:
        return _TEMPLATES
    for h in _heroes():
        if USE_ICONS:
            img = cv2.imread(os.path.join(ICON_DIR, f"{h['id']}.png"))
            if img is None:
                continue
            sq = img  # icone ja e ~quadrado, focado no rosto
        else:
            img = cv2.imread(os.path.join(PORTRAIT_DIR, f"{h['id']}.png"))
            if img is None:
                continue
            ih, iw = img.shape[:2]
            side = min(iw, ih)
            x0 = (iw - side) // 2
            sq = img[0:side, x0:x0 + side]
        _TEMPLATES[h["id"]] = {"fp": _fingerprint(sq)}
    return _TEMPLATES


def _slot_boxes():
    g = GEOM
    boxes = []
    for side, x0 in (("left", g["left_x0"]), ("right", g["right_x0"])):
        for i in range(5):
            x = x0 + i * (g["slot_w"] + g["gap"])
            boxes.append((side, x, g["y0"], g["slot_w"], g["slot_h"]))
    return boxes


def _match_slot(slot_bgr):
    """Retorna (hero_id, score) do template de melhor correlacao de cor."""
    fp = _fingerprint(slot_bgr)
    best_id, best_score = None, -2.0
    for hid, t in _load_templates().items():
        score = float(np.dot(fp, t["fp"]))
        if score > best_score:
            best_id, best_score = hid, score
    return best_id, best_score


def detect_from_bgr(frame, my_team=None, debug_montage=None):
    """Detecta os 10 herois no frame BGR. my_team: 'left'/'right' (lado do jogador)
    para separar inimigos. Retorna dict com radiant/dire/enemy/all + scores."""
    import cv2
    names = {h["id"]: h["localized_name"] for h in _heroes()}

    crops = []
    results = []
    for side, x, y, w, h in _slot_boxes():
        slot = frame[y:y + h, x:x + w]
        if slot.size == 0:
            continue
        hid, score = _match_slot(slot)
        results.append({"side": side, "x": x, "hero_id": hid,
                        "name": names.get(hid), "score": round(score, 3)})
        if debug_montage is not None:
            crops.append(frame[y:y + h, x:x + w])

    if debug_montage is not None and crops:
        montage = np.hstack([cv2.resize(c, (84, 76)) for c in crops])
        cv2.imwrite(debug_montage, montage)

    left = [r for r in results if r["side"] == "left"]
    right = [r for r in results if r["side"] == "right"]
    enemy = right if my_team == "left" else left if my_team == "right" else []
    return {"left": left, "right": right, "enemy": enemy, "all": results}


def capture_frame(monitor_w=2560, monitor_h=1600):
    """Captura o monitor (primario por padrao) e devolve um frame BGR (numpy)."""
    import mss
    import cv2
    with mss.MSS() as sct:
        target = next((m for m in sct.monitors[1:]
                       if m["width"] == monitor_w and m["height"] == monitor_h),
                      sct.monitors[1])
        shot = sct.grab(target)
    frame = np.array(shot)[:, :, :3]  # BGRA -> BGR
    return frame


if __name__ == "__main__":
    # Calibracao: roda contra uma captura salva e mostra os matches + montagem.
    import sys
    import cv2
    print("Baixando retratos (1x)...", ensure_portraits(), "novos")
    path = sys.argv[1] if len(sys.argv) > 1 else r"C:\temp\dota_cap.png"
    frame = cv2.imread(path)
    out = detect_from_bgr(frame, my_team="left", debug_montage=r"C:\temp\slots_montage.png")
    print("\nSLOTS detectados (montagem em C:\\temp\\slots_montage.png):")
    for r in out["all"]:
        print(f"  {r['side']:5} x={r['x']:4} -> {r['name']:22} (score {r['score']})")
