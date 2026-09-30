"""Draws DuneShark's icon (a gold shark fin cutting through the dunes) -> duneshark.ico + duneshark.png."""
import os
from PIL import Image, ImageDraw, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
S = 1024                                   # drawn big, scaled down = smooth edges

GOLD, GOLD_HI, GOLD_DIM = (201, 164, 106), (240, 207, 142), (122, 99, 64)
BG_TOP, BG_BOT = (24, 27, 36), (12, 13, 18)
DUNE_1, DUNE_2 = (58, 46, 30), (92, 72, 44)


def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def draw():
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    # background: rounded square, vertical night gradient
    bg = Image.new("RGBA", (S, S))
    d = ImageDraw.Draw(bg)
    for y in range(S):
        d.line([(0, y), (S, y)], fill=lerp(BG_TOP, BG_BOT, y / S) + (255,))
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, S - 1, S - 1], radius=210, fill=255)
    img.paste(bg, (0, 0), mask)
    d = ImageDraw.Draw(img)

    # moon disc
    d.ellipse([170, 150, 350, 330], fill=GOLD_DIM + (255,))
    d.ellipse([190, 170, 330, 310], fill=(150, 121, 76, 255))

    # back dune
    back = [(0, 700)] + [(x, 700 - 70 * (1 - ((x - 380) / 520) ** 2)) for x in range(0, S + 1, 16)] + [(S, S), (0, S)]
    d.polygon(back, fill=DUNE_1 + (255,))

    # shark fin: convex leading edge sweeping back to a hooked tip, concave trailing edge
    def bez(p0, p1, p2, n=40):
        return [((1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * p1[0] + t * t * p2[0],
                 (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * p1[1] + t * t * p2[1]) for t in (i / n for i in range(n + 1))]
    lead = bez((230, 770), (330, 330), (720, 215))
    trail = bez((720, 215), (560, 420), (660, 770))
    d.polygon(lead + trail, fill=GOLD + (255,))
    shade = bez((720, 215), (590, 430), (640, 770))
    d.polygon(trail + shade[::-1], fill=GOLD_DIM + (255,))          # shaded back edge
    d.line(lead, fill=GOLD_HI + (255,), width=22, joint="curve")

    # front dune the fin cuts through, with a sand wake line
    front = [(0, 760)] + [(x, 760 + 40 * ((x - 512) / 512) ** 2) for x in range(0, S + 1, 16)] + [(S, S), (0, S)]
    d.polygon(front, fill=DUNE_2 + (255,))
    for (x0, y0, x1, y1) in [(110, 790, 250, 760), (60, 830, 230, 800), (680, 760, 880, 800), (700, 800, 940, 850)]:
        d.line([(x0, y0), (x1, y1)], fill=GOLD_HI + (190,), width=14)      # sand wake

    img.putalpha(Image.composite(img.getchannel("A"), Image.new("L", (S, S), 0), mask))
    return img


if __name__ == "__main__":
    big = draw()
    png = big.resize((256, 256), Image.LANCZOS)
    png.save(os.path.join(HERE, "duneshark.png"))
    png.save(os.path.join(HERE, "duneshark.ico"), sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("ok")
