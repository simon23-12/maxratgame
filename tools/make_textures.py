"""Erzeugt alle prozeduralen Texturen fuer die Wohnung (System-Python + PIL + numpy).

Aufruf:  python3 tools/make_textures.py
Ausgabe: blender/textures/*.jpg|png
"""
import math
import os

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "blender", "textures")
os.makedirs(OUT, exist_ok=True)
rng = np.random.default_rng(42)
SUPP = "/System/Library/Fonts/Supplemental/"


def font(name, size):
    for n in (name, "Arial Black.ttf", "Impact.ttf"):
        try:
            return ImageFont.truetype(SUPP + n, size)
        except OSError:
            continue
    return ImageFont.load_default()


def periodic_noise(h, w, octaves=((4, 1.0), (8, 0.5), (16, 0.25), (32, 0.12))):
    """Kachelbares Rauschen ueber zufaellige Fourier-Komponenten mit ganzzahligen Frequenzen."""
    y, x = np.mgrid[0:h, 0:w]
    y = y / h * 2 * math.pi
    x = x / w * 2 * math.pi
    n = np.zeros((h, w))
    for f, amp in octaves:
        for _ in range(6):
            fx, fy = rng.integers(-f, f + 1, 2)
            ph = rng.uniform(0, 2 * math.pi)
            n += amp * np.sin(fx * x + fy * y + ph)
    n -= n.min()
    return n / max(n.max(), 1e-6)


def save(arr, name, quality=90):
    img = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))
    path = os.path.join(OUT, name)
    if name.endswith(".jpg"):
        img.convert("RGB").save(path, quality=quality)
    else:
        img.save(path)
    print("  ->", name, img.size)
    return img


# ---------------------------------------------------------------- Holzdielen
def wood_planks():
    """1.6 m x 1.6 m Kachel, 8 alte Dielen a 20 cm, Dielen laufen entlang U."""
    S = 2048
    rows = 8
    rh = S // rows
    img = np.zeros((S, S, 3))
    y, x = np.mgrid[0:S, 0:S].astype(float)
    big = periodic_noise(S, S, ((2, 1), (4, .6), (8, .3)))
    palette = np.array([
        [150, 104, 64], [138, 92, 55], [160, 115, 72], [128, 86, 50],
        [145, 100, 58], [120, 80, 48], [155, 110, 66], [135, 95, 60],
    ], float)
    for r in range(rows):
        y0 = r * rh
        sl = slice(y0, y0 + rh)
        yy = y[sl] - y0
        xx = x[sl]
        # Stossfugen: 1-2 pro Reihe, versetzt, kachelbar (wrap)
        n_joints = rng.integers(1, 3)
        joints = np.sort(rng.uniform(0, S, n_joints))
        seg = np.searchsorted(joints, xx) % n_joints
        base = np.zeros((rh, S, 3))
        grain = np.zeros((rh, S))
        for j in range(n_joints):
            col = palette[rng.integers(len(palette))] * rng.uniform(.88, 1.08)
            m = seg == j
            base[m] = col
            # Maserung: langgezogene Linien entlang x, leicht wellig
            f1 = rng.uniform(.35, .7)
            wav = 6 * np.sin(xx / S * 2 * math.pi * rng.integers(1, 4) + rng.uniform(0, 6)) \
                + 3 * np.sin(xx / S * 2 * math.pi * rng.integers(3, 8))
            gn = periodic_noise(rh, S, ((2, 1), (6, .5)))
            g = np.sin((yy + wav + gn * 14) * f1 + rng.uniform(0, 6)) * .5 + .5
            g = g ** 4 * (.4 + .6 * gn)
            # Aeste / Jahresringe
            if rng.random() < .55:
                kx, ky = rng.uniform(0, S), rng.uniform(20, rh - 20)
                d = np.sqrt(((xx - kx + S / 2) % S - S / 2) ** 2 * .25 + (yy - ky) ** 2)
                ring = (np.sin(d * .35) * .5 + .5) * np.exp(-d / 60)
                g = np.maximum(g, ring)
                knot = np.exp(-(d / 9) ** 2)
                g += knot * 1.5
            grain[m] = g[m]
        fine = rng.normal(0, 1, (rh, S))
        fine = np.array(Image.fromarray(((fine * 20) + 128).clip(0, 255).astype(np.uint8))
                        .resize((S // 8, rh)).resize((S, rh), Image.BILINEAR), float) / 255 - .5
        shade = 1 - .22 * grain + .3 * fine
        plank = base * shade[..., None]
        # Abnutzung: Mitte der Diele heller/abgelaufen, Raender dunkler (Dreck)
        edge = np.minimum(yy, rh - 1 - yy)
        dirt = np.clip(1 - edge / 14, 0, 1) ** 2
        plank *= (1 - .45 * dirt)[..., None]
        # Fugen
        plank[edge < 2.2] *= .25
        for jx in joints:
            dj = np.abs((xx - jx + S / 2) % S - S / 2)
            plank[dj < 2] *= .3
            plank *= (1 - .3 * np.clip(1 - dj / 10, 0, 1))[..., None]
            # Naegel
            for ny in (rh * .28, rh * .72):
                dn = np.sqrt(((xx - jx - 18 + S / 2) % S - S / 2) ** 2 + (yy - ny) ** 2)
                plank[dn < 4] *= .35
        img[sl] = plank
    img *= (.85 + .3 * big)[..., None]
    # Kratzer
    pil = Image.fromarray(np.clip(img, 0, 255).astype(np.uint8))
    d = ImageDraw.Draw(pil, "RGBA")
    for _ in range(260):
        x0, y0 = rng.uniform(0, S, 2)
        ln = rng.uniform(20, 160)
        a = rng.normal(0, .25)
        d.line([(x0, y0), (x0 + ln * math.cos(a), y0 + ln * math.sin(a))],
               fill=(220, 180, 130, int(rng.uniform(15, 45))), width=1)
    for _ in range(40):  # Flecken
        x0, y0 = rng.uniform(0, S, 2)
        r = rng.uniform(6, 30)
        d.ellipse([x0 - r, y0 - r * .6, x0 + r, y0 + r * .6], fill=(40, 25, 15, int(rng.uniform(15, 40))))
    pil = pil.resize((1024, 1024), Image.LANCZOS)
    pil.save(os.path.join(OUT, "wood_planks.jpg"), quality=90)
    print("  -> wood_planks.jpg")


# ---------------------------------------------------------------- Fliesen
def tiles():
    S = 512  # 0.6 m, 6x6 Fliesen
    n = 6
    t = S // n
    img = np.zeros((S, S, 3))
    noise = periodic_noise(S, S)
    for i in range(n):
        for j in range(n):
            c = np.array([232, 230, 222]) * rng.uniform(.93, 1.02)
            if (i + j) % 2 == 0 and rng.random() < .0:
                c = np.array([40, 40, 45])
            img[i * t:(i + 1) * t, j * t:(j + 1) * t] = c
    img *= (.92 + .1 * noise)[..., None]
    g = np.zeros((S, S), bool)
    for k in range(n):
        g[k * t:k * t + 3, :] = True
        g[:, k * t:k * t + 3] = True
    img[g] = [120, 118, 110]
    pil = Image.fromarray(np.clip(img, 0, 255).astype(np.uint8))
    d = ImageDraw.Draw(pil)
    for _ in range(4):  # Risse
        x, y = rng.uniform(0, S, 2)
        pts = [(x, y)]
        for _ in range(6):
            x += rng.normal(0, 14)
            y += rng.normal(0, 14)
            pts.append((x, y))
        d.line(pts, fill=(140, 138, 130), width=1)
    pil.save(os.path.join(OUT, "tiles.jpg"), quality=90)
    print("  -> tiles.jpg")


# ---------------------------------------------------------------- Putz
def plaster():
    S = 512
    n = periodic_noise(S, S, ((8, 1), (16, .6), (32, .4), (64, .3)))
    base = np.array([238, 233, 222])
    img = base * (.94 + .07 * n)[..., None]
    save(img, "plaster.jpg")


def fabric(name, col, S=256, strength=.12):
    y, x = np.mgrid[0:S, 0:S]
    weave = (np.sin(x * math.pi / 2) * np.sin(y * math.pi / 2)) * .5 + .5
    n = periodic_noise(S, S, ((8, 1), (16, .5)))
    img = np.array(col) * (1 - strength + strength * weave * .6 + strength * .8 * n)[..., None]
    save(img, name)


def cardboard():
    S = 512
    y, x = np.mgrid[0:S, 0:S]
    n = periodic_noise(S, S, ((4, 1), (16, .4), (64, .3)))
    corr = np.sin(x * 2 * math.pi / 16) * .5 + .5
    img = np.array([176, 136, 90]) * (.82 + .1 * n + .06 * corr)[..., None]
    pil = Image.fromarray(np.clip(img, 0, 255).astype(np.uint8))
    d = ImageDraw.Draw(pil, "RGBA")
    d.rectangle([0, S * .47, S, S * .53], fill=(200, 180, 140, 120))  # Klebeband
    d.text((40, 60), "UMZUG", font=font("DIN Condensed Bold.ttf", 90), fill=(30, 30, 30, 170))
    d.text((40, 340), "KRAM", font=font("DIN Condensed Bold.ttf", 70), fill=(30, 30, 30, 150))
    pil.save(os.path.join(OUT, "cardboard.jpg"), quality=88)
    print("  -> cardboard.jpg")


# ---------------------------------------------------------------- Etiketten
def mate_label():
    """Rundum-Etikett, u = Umfang. Mate-Flasche: Glas braun, Etikett gelb-beige."""
    W, H = 512, 256
    pil = Image.new("RGB", (W, H), (236, 206, 98))
    d = ImageDraw.Draw(pil)
    d.rectangle([0, 0, W, 26], fill=(28, 50, 112))
    d.rectangle([0, H - 26, W, H], fill=(28, 50, 112))
    for cx in (W * .25, W * .75):
        d.ellipse([cx - 62, 40, cx + 62, H - 40], fill=(248, 236, 190), outline=(28, 50, 112), width=5)
        d.text((cx, H / 2), "MATE", font=font("Arial Black.ttf", 40), fill=(28, 50, 112), anchor="mm")
    pil.save(os.path.join(OUT, "mate_label.png"))
    print("  -> mate_label.png")


def energy_can():
    W, H = 512, 512
    pil = Image.new("RGB", (W, H), (192, 198, 210))
    d = ImageDraw.Draw(pil)
    cs = 32
    for i in range(W // cs):  # blau-silber Rauten-Karo
        for j in range(H // cs):
            if (i + j) % 2 == 0:
                d.rectangle([i * cs, j * cs, i * cs + cs, j * cs + cs], fill=(24, 42, 120))
    for cx in (W * .25, W * .75):
        d.rectangle([cx - 90, 140, cx + 90, 372], fill=(210, 214, 224))
        d.ellipse([cx - 52, 165, cx + 52, 269], fill=(240, 190, 30))
        d.polygon([(cx - 85, 250), (cx - 20, 215), (cx - 10, 260)], fill=(200, 30, 50))
        d.polygon([(cx + 85, 250), (cx + 20, 215), (cx + 10, 260)], fill=(200, 30, 50))
        d.text((cx, 330), "ENERGY", font=font("Futura.ttc", 38), fill=(200, 30, 50), anchor="mm")
    d.rectangle([0, 0, W, 18], fill=(170, 175, 185))
    d.rectangle([0, H - 18, W, H], fill=(170, 175, 185))
    pil.save(os.path.join(OUT, "energy_can.png"))
    print("  -> energy_can.png")


def quark():
    W, H = 512, 256
    pil = Image.new("RGB", (W, H), (245, 245, 242))
    d = ImageDraw.Draw(pil)
    d.rectangle([0, 150, W, 210], fill=(30, 90, 190))
    for cx in (W * .25, W * .75):
        d.text((cx, 90), "MAGERQUARK", font=font("DIN Condensed Bold.ttf", 54), fill=(30, 90, 190), anchor="mm")
        d.text((cx, 180), "500 g  0,2 % Fett", font=font("DIN Alternate Bold.ttf", 26), fill=(255, 255, 255), anchor="mm")
    pil.save(os.path.join(OUT, "quark_side.png"))
    lid = Image.new("RGB", (256, 256), (30, 90, 190))
    d = ImageDraw.Draw(lid)
    d.ellipse([20, 20, 236, 236], fill=(220, 225, 235))
    d.text((128, 128), "QUARK", font=font("DIN Condensed Bold.ttf", 56), fill=(30, 90, 190), anchor="mm")
    lid.save(os.path.join(OUT, "quark_lid.png"))
    print("  -> quark_side.png / quark_lid.png")


def pizza():
    S = 512
    pil = Image.new("RGB", (S, S), (0, 0, 0))
    d = ImageDraw.Draw(pil)
    c = S / 2
    d.ellipse([4, 4, S - 4, S - 4], fill=(196, 140, 70))       # Rand
    d.ellipse([34, 34, S - 34, S - 34], fill=(178, 52, 30))    # Tomate
    n = rng.uniform
    for _ in range(26):  # vegane "Kaese"-Kleckse (Cashew)
        x, y = n(80, S - 80, 2)
        r = n(14, 34)
        d.ellipse([x - r, y - r * .8, x + r, y + r * .8], fill=(236, 214, 150))
    for _ in range(22):  # Paprika
        x, y = n(70, S - 70, 2)
        d.arc([x - 16, y - 16, x + 16, y + 16], n(0, 360), n(0, 360) + 140, fill=(40, 150, 50), width=7)
    for _ in range(18):  # Oliven
        x, y = n(70, S - 70, 2)
        d.ellipse([x - 9, y - 9, x + 9, y + 9], fill=(25, 22, 22))
        d.ellipse([x - 3, y - 3, x + 3, y + 3], fill=(178, 52, 30))
    for _ in range(30):  # Rucola
        x, y = n(70, S - 70, 2)
        d.line([(x, y), (x + n(-14, 14), y + n(-14, 14))], fill=(70, 120, 40), width=4)
    arr = np.array(pil, float)
    arr *= (.85 + .3 * periodic_noise(S, S))[..., None]
    save(arr, "pizza.png")
    # Karton-Deckel
    box = Image.new("RGB", (512, 512), (205, 178, 130))
    d = ImageDraw.Draw(box)
    d.ellipse([96, 60, 416, 380], outline=(150, 30, 30), width=10)
    d.text((256, 220), "PIZZA", font=font("Impact.ttf", 92), fill=(150, 30, 30), anchor="mm")
    d.text((256, 430), "100% VEGAN · HEISS & LECKER", font=font("DIN Condensed Bold.ttf", 34), fill=(40, 110, 40), anchor="mm")
    box.save(os.path.join(OUT, "pizza_box.jpg"), quality=88)
    print("  -> pizza_box.jpg")


# ---------------------------------------------------------------- Bilder/Fenster
def facade_print():
    """S/W-Fotodruck: Hochhausfassade (wie das Bild ueber der Couch)."""
    W, H = 600, 800
    pil = Image.new("L", (W, H), 200)
    d = ImageDraw.Draw(pil)
    d.rectangle([60, 0, 540, 800], fill=150)
    for i in range(10):
        for j in range(16):
            x, y = 80 + i * 46, 20 + j * 48
            d.rectangle([x, y, x + 30, y + 30], fill=int(rng.uniform(20, 70)))
    d.rectangle([0, 620, W, H], fill=225)
    d.ellipse([-40, 560, 260, 860], fill=245)
    pil = pil.filter(ImageFilter.GaussianBlur(1.2))
    a = np.array(pil, float)
    a += rng.normal(0, 8, a.shape)
    save(np.stack([a] * 3, -1), "print_facade.jpg")


def figure_print():
    W, H = 600, 800
    pil = Image.new("L", (W, H), 225)
    d = ImageDraw.Draw(pil)
    d.ellipse([230, 120, 370, 280], fill=60)
    d.polygon([(200, 300), (400, 300), (450, 760), (150, 760)], fill=45)
    d.rectangle([0, 760, W, H], fill=90)
    pil = pil.filter(ImageFilter.GaussianBlur(3))
    save(np.stack([np.array(pil)] * 3, -1), "print_figure.jpg")


def night_window():
    W, H = 256, 512
    y = np.linspace(0, 1, H)[:, None, None]
    sky = np.array([10, 16, 40]) * (1 - y) + np.array([28, 34, 60]) * y
    img = np.repeat(sky, W, 1)
    pil = Image.fromarray(np.clip(img, 0, 255).astype(np.uint8))
    d = ImageDraw.Draw(pil)
    # gegenueberliegendes Haus mit ein paar erleuchteten Fenstern
    d.rectangle([0, 230, W, H], fill=(16, 16, 22))
    for i in range(4):
        for j in range(5):
            if rng.random() < .3:
                x, yy = 18 + i * 60, 250 + j * 50
                d.rectangle([x, yy, x + 30, yy + 32], fill=(230, 170, 90))
    d.ellipse([170, 40, 210, 80], fill=(235, 235, 220))  # Mond
    pil.save(os.path.join(OUT, "night_window.png"))
    print("  -> night_window.png")


# ---------------------------------------------------------------- James Dean (stilisierte Poster)
def dean_portrait():
    """S/W-Portrait im 50er-Stil: Tolle, hochgeklappter Kragen, Zigarette, harte Schatten."""
    W, H = 600, 800
    pil = Image.new("L", (W, H), 0)
    a = np.array(pil, float)
    y, x = np.mgrid[0:H, 0:W]
    a[:] = 70 + 60 * (1 - y / H)                      # Hintergrund-Verlauf
    pil = Image.fromarray(a.astype(np.uint8))
    d = ImageDraw.Draw(pil)
    # Jacke + hochgeklappter Kragen
    d.polygon([(40, 800), (90, 560), (210, 500), (300, 560), (390, 500), (510, 560), (560, 800)], fill=28)
    d.polygon([(205, 505), (250, 640), (300, 575)], fill=55)
    d.polygon([(395, 505), (350, 640), (300, 575)], fill=40)
    d.polygon([(255, 560), (300, 640), (345, 560), (300, 600)], fill=215)  # T-Shirt
    # Hals
    d.polygon([(255, 430), (345, 430), (340, 560), (300, 600), (260, 560)], fill=150)
    d.polygon([(300, 430), (345, 430), (340, 560), (300, 600)], fill=95)
    # Gesicht (Licht von links -> rechte Haelfte im Schatten)
    d.ellipse([195, 200, 405, 480], fill=200)
    d.chord([195, 200, 405, 480], -90, 90, fill=120)
    d.polygon([(300, 205), (318, 300), (310, 470), (300, 478)], fill=160)
    # Tolle / Pompadour
    d.polygon([(185, 300), (175, 220), (205, 150), (270, 105), (350, 100), (415, 140), (432, 210),
               (410, 300), (395, 225), (330, 190), (250, 205), (205, 250)], fill=22)
    d.ellipse([230, 95, 370, 175], fill=30)
    d.arc([240, 110, 380, 200], 190, 330, fill=70, width=6)
    # Augenbrauen (zusammengezogen), Augen zusammengekniffen
    d.line([(222, 288), (285, 280)], fill=30, width=9)
    d.line([(318, 280), (378, 290)], fill=25, width=9)
    d.line([(232, 312), (280, 308)], fill=35, width=6)
    d.line([(322, 308), (368, 314)], fill=30, width=6)
    # Nase + Mund
    d.polygon([(300, 310), (312, 380), (292, 392), (284, 386)], fill=135)
    d.line([(262, 425), (300, 430), (336, 420)], fill=60, width=5)
    # Zigarette im Mundwinkel + Rauch
    d.line([(318, 426), (392, 446)], fill=235, width=7)
    d.ellipse([388, 440, 400, 452], fill=250)
    for k in range(5):
        d.arc([380 + k * 8, 300 - k * 40, 440 + k * 10, 440 - k * 40], 200, 330, fill=170 - k * 20, width=3)
    pil = pil.filter(ImageFilter.GaussianBlur(1.6))
    arr = np.array(pil, float) + rng.normal(0, 9, (H, W))
    pil = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8)).convert("RGB")
    d = ImageDraw.Draw(pil)
    d.text((W / 2, 740), "JAMES DEAN", font=font("DIN Condensed Bold.ttf", 76), fill=(235, 232, 225), anchor="mm")
    pil.save(os.path.join(OUT, "print_dean1.jpg"), quality=90)
    print("  -> print_dean1.jpg")


def dean_poster():
    """Farbiges 50er-Poster: rote Jacke, weisses Shirt, Jeans, an die Wand gelehnt."""
    W, H = 600, 800
    pil = Image.new("RGB", (W, H), (232, 188, 70))
    d = ImageDraw.Draw(pil)
    for i in range(14):  # Sonnenstrahlen
        a0 = i * math.pi / 7
        d.polygon([(300, 330), (300 + 900 * math.cos(a0), 330 + 900 * math.sin(a0)),
                   (300 + 900 * math.cos(a0 + .2), 330 + 900 * math.sin(a0 + .2))], fill=(240, 205, 95))
    # Beine (Jeans), Stiefel
    d.polygon([(235, 520), (300, 520), (290, 740), (245, 740)], fill=(40, 60, 110))
    d.polygon([(300, 520), (365, 520), (375, 740), (330, 740)], fill=(35, 52, 98))
    d.rectangle([238, 735, 292, 760], fill=(30, 20, 15))
    d.rectangle([328, 735, 384, 760], fill=(30, 20, 15))
    # rote Jacke + Shirt
    d.polygon([(205, 300), (395, 300), (410, 530), (190, 530)], fill=(190, 28, 25))
    d.polygon([(265, 300), (335, 300), (325, 520), (275, 520)], fill=(245, 242, 235))
    d.polygon([(205, 300), (160, 470), (195, 480), (230, 360)], fill=(170, 22, 20))   # Arm links
    d.polygon([(395, 300), (440, 470), (405, 480), (370, 360)], fill=(170, 22, 20))
    d.polygon([(250, 292), (270, 340), (300, 300)], fill=(150, 18, 15))           # Kragen
    d.polygon([(350, 292), (330, 340), (300, 300)], fill=(150, 18, 15))
    # Kopf + Tolle
    d.rectangle([285, 260, 315, 300], fill=(200, 150, 120))
    d.ellipse([255, 165, 345, 280], fill=(222, 172, 140))
    d.chord([255, 165, 345, 280], -90, 90, fill=(185, 135, 105))
    d.polygon([(250, 215), (252, 160), (285, 128), (335, 125), (358, 160), (348, 210), (330, 175), (275, 180)],
              fill=(60, 40, 25))
    d.line([(275, 215), (292, 213)], fill=(50, 35, 25), width=4)
    d.line([(308, 213), (326, 216)], fill=(50, 35, 25), width=4)
    d.line([(285, 255), (315, 253)], fill=(140, 70, 60), width=3)
    d.line([(312, 254), (350, 262)], fill=(245, 245, 240), width=4)              # Zigarette
    d.text((W / 2, 70), "JAMES DEAN", font=font("Impact.ttf", 84), fill=(25, 25, 30), anchor="mm")
    d.text((W / 2, 785), "1931 - 1955", font=font("DIN Condensed Bold.ttf", 30), fill=(25, 25, 30), anchor="mm")
    arr = np.array(pil.filter(ImageFilter.GaussianBlur(0.8)), float)
    arr *= (.9 + .12 * periodic_noise(H, W, ((4, 1), (16, .5))))[..., None]     # vergilbt
    save(arr, "print_dean2.jpg")


# ---------------------------------------------------------------- verdrecktes Klo
def toilet_dirty():
    S = 512
    c = S / 2
    y, x = np.mgrid[0:S, 0:S]
    r = np.hypot(x - c, y - c) / c                     # 0 Mitte .. 1 Rand (Deckel-UV: Kreis fuellt Textur)
    img = np.zeros((S, S, 3))
    img[:] = [235, 232, 222]
    bowl = r < 0.86
    img[bowl] = (np.array([228, 224, 210]) * (0.75 + 0.25 * np.clip((r[bowl] - .3) / .56, 0, 1))[:, None])
    water = r < 0.42
    img[water] = [118, 96, 42]
    ring = (r > 0.4) & (r < 0.5)
    img[ring] = img[ring] * 0.55 + np.array([110, 80, 35]) * 0.45   # Kalk-/Dreckrand
    pil = Image.fromarray(img.astype(np.uint8))
    d = ImageDraw.Draw(pil, "RGBA")
    for _ in range(26):  # Bremsspuren von oben ins Wasser
        a = rng.uniform(0, 2 * math.pi)
        r0, r1 = rng.uniform(.6, .84), rng.uniform(.38, .5)
        pts = [(c + rr * c * math.cos(a + rng.normal(0, .03)), c + rr * c * math.sin(a + rng.normal(0, .03)))
               for rr in np.linspace(r0, r1, 6)]
        d.line(pts, fill=(90, 58, 25, int(rng.uniform(80, 200))), width=int(rng.uniform(3, 12)))
    for _ in range(40):  # Spritzer auf dem Rand
        a = rng.uniform(0, 2 * math.pi)
        rr = rng.uniform(.86, .99) * c
        px, py = c + rr * math.cos(a), c + rr * math.sin(a)
        s_ = rng.uniform(2, 7)
        d.ellipse([px - s_, py - s_, px + s_, py + s_], fill=(200, 170, 60, 150))
    pil = pil.filter(ImageFilter.GaussianBlur(1.5))
    pil.save(os.path.join(OUT, "toilet_top.png"))
    # Aussenseite: Laufspuren vom Rand runter, Schmutz unten am Fuss
    W_, H_ = 512, 256
    side = np.zeros((H_, W_, 3))
    side[:] = [232, 229, 220]
    yy = np.arange(H_)[:, None]
    side *= (1 - 0.35 * np.clip((yy - H_ * .75) / (H_ * .25), 0, 1))[..., None]
    pil = Image.fromarray(side.astype(np.uint8))
    d = ImageDraw.Draw(pil, "RGBA")
    for _ in range(30):
        x0 = rng.uniform(0, W_)
        ln = rng.uniform(20, 140)
        d.line([(x0, 0), (x0 + rng.normal(0, 4), ln)], fill=(190, 160, 70, int(rng.uniform(60, 150))),
               width=int(rng.uniform(2, 6)))
        d.ellipse([x0 - 4, ln - 4, x0 + 4, ln + 4], fill=(170, 140, 60, 120))
    d.rectangle([0, H_ - 22, W_, H_], fill=(90, 75, 50, 170))
    pil = pil.filter(ImageFilter.GaussianBlur(1.0))
    pil.save(os.path.join(OUT, "toilet_side.png"))
    print("  -> toilet_top.png / toilet_side.png")


if __name__ == "__main__":
    print("Texturen ->", OUT)
    wood_planks()
    tiles()
    plaster()
    fabric("fabric_white.jpg", (232, 228, 220))
    fabric("fabric_grey.jpg", (150, 150, 146), strength=.2)
    fabric("fabric_blue.jpg", (70, 95, 150), strength=.18)
    cardboard()
    mate_label()
    energy_can()
    quark()
    pizza()
    facade_print()
    figure_print()
    night_window()
    dean_portrait()
    dean_poster()
    toilet_dirty()
