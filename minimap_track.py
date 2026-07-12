"""
Deteccao + rastreamento dos INIMIGOS no minimapa (base do overlay-fantasma).
=============================================================================

No minimapa do Dota, cada heroi aparece na COR do jogador (10 cores unicas).
Inimigos = seta; aliados = balao; construcoes = quadrado. Como sabemos o NOSSO
time pelo GSI, os inimigos sao as 5 cores do time adversario. Detectamos cada
cor (validado em frames reais) -> posicao + identidade (a propria cor).

O EnemyTracker guarda o estado: quando uma cor inimiga estava visivel e some,
vira "fantasma" na ultima posicao com o horario. Quando reaparece, o fantasma
some. Puro Python + numpy + OpenCV (sem Qt), pra dar pra testar offline.
"""

import numpy as np
import cv2

# Cores de jogador no minimapa (RGB). Validado em frames reais do jogo.
RADIANT = {
    "azul":    (51, 117, 255),
    "teal":    (58, 226, 178),
    "roxo":    (180, 0, 220),
    "amarelo": (243, 240, 11),
    "laranja": (255, 107, 0),
}
DIRE = {
    "rosa":   (254, 134, 194),
    "oliva":  (140, 155, 80),
    "ciano":  (100, 218, 248),
    "verde":  (0, 131, 31),
    "marrom": (165, 106, 45),
}
DRAW_RGB = {**RADIANT, **DIRE}   # cor de desenho do fantasma, por nome

TOL = 62          # distancia RGB maxima pra casar a cor
MIN_AREA = 18     # heroi ~50-90 px; creep/ruido ~10 (filtra falso-positivo)
MAX_AREA = 300


def enemy_palette(my_team):
    """my_team 'radiant'/'dire' -> dict {cor: RGB} do time INIMIGO.
    Default (sem GSI): assume que o inimigo e o Radiant."""
    return DIRE if (my_team or "").lower() == "radiant" else RADIANT


def detect(frame_bgr, palette):
    """Acha os inimigos visiveis. Devolve {cor: (px, py)} (maior blob por cor)."""
    rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB).astype(np.int16)
    out = {}
    for name, target in palette.items():
        d = np.sqrt(((rgb - np.array(target)) ** 2).sum(axis=2))
        mask = (d < TOL).astype(np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
        n, _lbl, stats, cent = cv2.connectedComponentsWithStats(mask, 8)
        best = None
        for i in range(1, n):
            area = int(stats[i, cv2.CC_STAT_AREA])
            if MIN_AREA <= area <= MAX_AREA and (best is None or area > best[0]):
                best = (area, float(cent[i][0]), float(cent[i][1]))
        if best:
            out[name] = (best[1], best[2])
    return out


class EnemyTracker:
    """Estado por cor inimiga -> fantasmas de quem sumiu.

    ghosts: {cor: (x, y, since)}  (since = timestamp de quando sumiu)
    CONFIRM: quantos frames seguidos preciso ver a cor pra considerar inimigo
             de verdade (anti-ruido: 1 pixel solto nao vira fantasma)."""
    CONFIRM = 2

    def __init__(self):
        self.seen = {}     # cor -> streak de frames visto
        self.last = {}     # cor -> (x, y) ultima posicao confirmada
        self.ghosts = {}   # cor -> (x, y, since)

    def update(self, detections, now):
        names = set(self.seen) | set(detections) | set(self.last)
        for name in names:
            if name in detections:
                self.seen[name] = self.seen.get(name, 0) + 1
                if self.seen[name] >= self.CONFIRM:
                    self.last[name] = detections[name]
                    self.ghosts.pop(name, None)          # reapareceu -> some o fantasma
            else:
                streak = self.seen.get(name, 0)
                self.seen[name] = 0
                if streak >= self.CONFIRM and name in self.last and name not in self.ghosts:
                    x, y = self.last[name]
                    self.ghosts[name] = (x, y, now)       # acabou de sumir -> fantasma
        return self.ghosts


if __name__ == "__main__":
    # auto-teste da maquina de estado (deterministico, sem depender de imagem)
    t = EnemyTracker()
    seq = [
        ({"laranja": (10, 10), "amarelo": (90, 90)}, 100.0),  # aparecem
        ({"laranja": (11, 10), "amarelo": (90, 91)}, 100.2),  # confirmam
        ({"laranja": (12, 11)}, 100.4),                        # amarelo SUMIU
        ({"laranja": (13, 12)}, 100.6),                        # fantasma persiste
        ({"laranja": (14, 12), "amarelo": (70, 70)}, 100.8),  # amarelo volta (1 frame)
        ({"laranja": (15, 12), "amarelo": (70, 71)}, 101.0),  # confirma -> limpa fantasma
    ]
    for det, now in seq:
        g = t.update(det, now)
        print(f"t={now:.1f} det={sorted(det)} -> fantasmas={ {k:(round(v[0]),round(v[1])) for k,v in g.items()} }")
