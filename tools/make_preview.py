"""Setzt Titel + Alarm-"!" auf das gerenderte Vorschaubild und erzeugt Icons.

Aufruf (nach tools/render_preview.py):  python3 tools/make_preview.py
Ausgabe: web/public/og-preview.jpg (1200x630), apple-touch-icon.png, favicon.png
"""
import json
import os

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
R = os.path.join(ROOT, "renders")
PUB = os.path.join(ROOT, "web", "public")
SUPP = "/System/Library/Fonts/Supplemental/"


def font(name, size):
    return ImageFont.truetype(SUPP + name, size)


def outlined(d, xy, text, f, fill, stroke=8, anchor="la"):
    d.text(xy, text, font=f, fill=fill, stroke_width=stroke, stroke_fill=(10, 8, 12), anchor=anchor)


img = Image.open(os.path.join(R, "og_raw.png")).convert("RGB")
W, H = img.size
meta = json.load(open(os.path.join(R, "og_raw.json")))

# dunkler Verlauf oben links fuer den Titel
shade = Image.new("L", (W, H), 0)
sd = ImageDraw.Draw(shade)
for i in range(260):
    sd.line([(0, i), (W, i)], fill=int(170 * (1 - i / 260) ** 1.6))
img = Image.composite(Image.new("RGB", (W, H), (8, 6, 12)), img, shade)

d = ImageDraw.Draw(img)
outlined(d, (44, 30), "MAX'", font("Impact.ttf", 118), (245, 238, 226), stroke=9)
outlined(d, (44, 140), "RATTE", font("Impact.ttf", 118), (255, 138, 61), stroke=9)
outlined(d, (48, 268), "3D-Stealth fürs Handy · 3 Level", font("DIN Condensed Bold.ttf", 40), (235, 228, 215), stroke=5)

# MGS-Ausrufezeichen ueber Max' Kopf, mit Glow
hx, hy = meta["head"][0] * W, meta["head"][1] * H
glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
gd = ImageDraw.Draw(glow)
gd.text((hx + 70, max(70, hy - 10)), "!", font=font("Impact.ttf", 150), fill=(255, 40, 30, 255), anchor="mm")
glow = glow.filter(ImageFilter.GaussianBlur(14))
img.paste(glow, (0, 0), glow)
d = ImageDraw.Draw(img)
outlined(d, (hx + 70, max(70, hy - 10)), "!", font("Impact.ttf", 150), (255, 59, 48), stroke=8, anchor="mm")

out = os.path.join(PUB, "og-preview.jpg")
img.save(out, quality=84, optimize=True, progressive=True)
print("og-preview.jpg", img.size, os.path.getsize(out) // 1024, "KB")

# Icons aus der Ratten-Nahaufnahme
icon = Image.open(os.path.join(R, "icon_raw.png")).convert("RGB")
icon.resize((180, 180), Image.LANCZOS).save(os.path.join(PUB, "apple-touch-icon.png"), optimize=True)
icon.resize((512, 512), Image.LANCZOS).save(os.path.join(PUB, "icon-512.png"), optimize=True)
icon.resize((64, 64), Image.LANCZOS).save(os.path.join(PUB, "favicon.png"), optimize=True)
print("Icons geschrieben")
