"""Gera assets/icon.ico do Copiloto (hexagono vermelho, miolo dourado).

Rodar 1x (o .ico gerado e commitado):  python scripts/make_icon.py
"""

import math
import os

from PIL import Image, ImageDraw

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "assets", "icon.ico")

BG = (13, 17, 26, 255)        # fundo do painel
RED = (192, 57, 43, 255)      # vermelho da marca
RED_HI = (232, 90, 69, 255)
GOLD = (230, 184, 102, 255)   # dourado


def hexagon(cx, cy, r, rot=math.pi / 2):
    return [(cx + r * math.cos(rot + i * math.pi / 3),
             cy + r * math.sin(rot + i * math.pi / 3)) for i in range(6)]


def draw(size):
    s = 8  # supersampling p/ bordas lisas
    W = size * s
    im = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    # fundo arredondado
    rad = W * 0.22
    d.rounded_rectangle([0, 0, W - 1, W - 1], radius=rad, fill=BG)
    cx = cy = W / 2
    # hexagono externo (marca)
    d.polygon(hexagon(cx, cy, W * 0.36), outline=RED_HI, width=max(1, int(W * 0.045)))
    d.polygon(hexagon(cx, cy, W * 0.26), fill=RED)
    # miolo dourado (o "ponto" do copiloto no minimapa)
    r = W * 0.10
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=GOLD)
    return im.resize((size, size), Image.LANCZOS)


def main():
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    sizes = [16, 24, 32, 48, 64, 128, 256]
    imgs = [draw(z) for z in sizes]
    imgs[-1].save(OUT, format="ICO", sizes=[(z, z) for z in sizes],
                  append_images=imgs[:-1])
    print("icone salvo em", OUT)


if __name__ == "__main__":
    main()
