"""Kodiert die gebackene (lineare) Lightmap als 8-bit sRGB-WebP/PNG fuer das Web.

Blender legt blender/_lightmap_linear.npy ab. Hier wird der HDR-Bereich gewaehlt (Perzentil),
damit typische Raumhelligkeiten viel Praezision bekommen; Lampen-Hotspots duerfen clippen.
Aufruf: python3 tools/encode_lightmap.py [perzentil=99.6]
"""
import json
import os
import sys

import numpy as np
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.path.join(ROOT, "web", "public", "assets")
pct = float(sys.argv[1]) if len(sys.argv) > 1 else 99.6

rgb = np.load(os.path.join(ROOT, "blender", "_lightmap_linear.npy")).astype(np.float32)
lm_max = float(max(0.25, np.percentile(rgb.max(-1), pct)))
x = np.clip(rgb / lm_max, 0, 1)
enc = np.where(x <= 0.0031308, x * 12.92, 1.055 * np.power(x, 1 / 2.4) - 0.055)
img = Image.fromarray((enc[::-1] * 255 + 0.5).astype(np.uint8))  # Blender-Pixel starten unten
img.save(os.path.join(ASSETS, "lightmap.png"))
img.save(os.path.join(ASSETS, "lightmap.webp"), quality=92, method=6)

lp = os.path.join(ASSETS, "layout.json")
layout = json.load(open(lp))
layout["meta"]["lm_max"] = round(lm_max, 4)
json.dump(layout, open(lp, "w"), indent=1)
print(f"[web] lightmap.webp (lm_max={lm_max:.3f}, p{pct})")
