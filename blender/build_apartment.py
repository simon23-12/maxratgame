"""Baut Max' Wohnung in Blender, backt das Licht in eine Lightmap, exportiert GLB + Layout
und rendert Cycles-Standbilder.

Aufruf (headless):
  /Applications/Blender.app/Contents/MacOS/Blender -b --factory-startup \
      --python blender/build_apartment.py -- [--bake-samples 256] [--lm-size 2048]
      [--no-bake] [--render] [--render-samples 128]

Koordinaten (Blender, Z oben, Meter): Ursprung = Suedwest-Ecke (Innenkante).
  x: West -> Ost (0 .. 11.48), y: Sued -> Nord (0 .. 8.05)
Grundriss nach Skizze: links Kueche (oben) + Wohnzimmer (unten), rechts Bad, Abstellkammer,
Toilette, Flur, Schlafzimmer.
"""
import bpy
import bmesh
import json
import math
import os
import random
import sys
import time
from contextlib import contextmanager

import numpy as np
from mathutils import Euler, Matrix, Vector

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TEX = os.path.join(HERE, "textures")
WEB_ASSETS = os.path.join(ROOT, "web", "public", "assets")
RENDERS = os.path.join(ROOT, "renders")
os.makedirs(WEB_ASSETS, exist_ok=True)
os.makedirs(RENDERS, exist_ok=True)

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []


def arg(name, default, cast=str):
    if name in argv:
        i = argv.index(name)
        if cast is bool:
            return True
        return cast(argv[i + 1])
    return default


BAKE = not arg("--no-bake", False, bool)
BAKE_SAMPLES = arg("--bake-samples", 256, int)
LM_SIZE = arg("--lm-size", 2048, int)
RENDER = arg("--render", False, bool)
RENDER_SAMPLES = arg("--render-samples", 128, int)
LM_MAX = 4.0  # Lightmap speichert Irradianz / LM_MAX (HDR in 8 bit)

random.seed(7)
H = 3.0           # Raumhoehe (Altbau)
DOOR_H = 2.2
T_EXT = 0.30      # Aussenwand
T_INT = 0.12      # Innenwand

X0 = 1.12          # Westwand: Wohnzimmer + Kueche gestaucht (vorher 0.0)
LX = (X0 + 5.72) / 2  # Mitte der linken Spalte
ROOMS = {
    "Kueche": (X0, 4.45, 5.72, 8.05),
    "Wohnzimmer": (X0, 0.0, 5.72, 4.45),
    "Schlafzimmer": (5.72, 0.0, 11.48, 4.45),
    "Flur": (5.72, 4.45, 11.48, 5.45),
    "Bad": (5.72, 5.45, 8.40, 8.05),
    "Toilette": (8.40, 5.45, 11.48, 6.70),
    "Abstellkammer": (8.40, 6.70, 11.48, 8.05),
}
W, D = 11.48, 8.05

LAYOUT = {"rooms": ROOMS, "walls": [], "solids": [], "doors": [], "items": [], "lights": []}

# --------------------------------------------------------------------------- Szene
bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
scene.unit_settings.system = "METRIC"


def coll(name):
    c = bpy.data.collections.new(name)
    scene.collection.children.link(c)
    return c


C_STATIC = coll("Static")
C_DYN = coll("Dynamic")
C_LIGHT = coll("Lights")
C_CAM = coll("Cameras")
C_CHAR = coll("Characters")  # Ratte + wacher Max: animiert, Echtzeit-Licht im Spiel

# --------------------------------------------------------------------------- Materialien
_img_cache = {}


def image(name):
    if name not in _img_cache:
        img = bpy.data.images.load(os.path.join(TEX, name))
        _img_cache[name] = img
    return _img_cache[name]


MATS = {}


def mat(name, color=(0.8, 0.8, 0.8), rough=0.6, metal=0.0, tex=None, emit=None, emit_strength=0.0,
        emit_tex=False, spec=0.5):
    if name in MATS:
        return MATS[name]
    m = bpy.data.materials.new(name)
    try:
        m.use_nodes = True
    except Exception:
        pass
    nt = m.node_tree
    bsdf = next(n for n in nt.nodes if n.type == "BSDF_PRINCIPLED")
    bsdf.inputs["Base Color"].default_value = (*color, 1)
    bsdf.inputs["Roughness"].default_value = rough
    bsdf.inputs["Metallic"].default_value = metal
    if "Specular IOR Level" in bsdf.inputs:
        bsdf.inputs["Specular IOR Level"].default_value = spec
    if tex:
        tn = nt.nodes.new("ShaderNodeTexImage")
        tn.image = image(tex)
        tn.location = (-400, 200)
        uvn = nt.nodes.new("ShaderNodeUVMap")
        uvn.uv_map = "UVMap"
        uvn.location = (-650, 200)
        nt.links.new(uvn.outputs["UV"], tn.inputs["Vector"])
        if color != (0.8, 0.8, 0.8) and color != (1, 1, 1):
            mix = nt.nodes.new("ShaderNodeMix")
            mix.data_type = "RGBA"
            mix.blend_type = "MULTIPLY"
            mix.inputs["Factor"].default_value = 1.0
            a_in = next(x for x in mix.inputs if x.name == "A" and x.type == "RGBA")
            b_in = next(x for x in mix.inputs if x.name == "B" and x.type == "RGBA")
            res = next(x for x in mix.outputs if x.name == "Result" and x.type == "RGBA")
            nt.links.new(tn.outputs["Color"], a_in)
            b_in.default_value = (*color, 1)
            nt.links.new(res, bsdf.inputs["Base Color"])
        else:
            nt.links.new(tn.outputs["Color"], bsdf.inputs["Base Color"])
        if emit_tex:
            nt.links.new(tn.outputs["Color"], bsdf.inputs["Emission Color"])
    if emit is not None:
        if not emit_tex:
            bsdf.inputs["Emission Color"].default_value = (*emit, 1)
        bsdf.inputs["Emission Strength"].default_value = emit_strength
    MATS[name] = m
    return m


def build_materials():
    M = {}
    M["floor"] = mat("Dielen", tex="wood_planks.jpg", rough=0.55)
    M["tiles"] = mat("Fliesen", tex="tiles.jpg", rough=0.25)
    M["wall"] = mat("Wand", tex="plaster.jpg", rough=0.9, spec=0.2)
    M["ceiling"] = mat("Decke", color=(0.9, 0.89, 0.86), rough=0.95, spec=0.1)
    M["trim"] = mat("Lack_Weiss", color=(0.88, 0.87, 0.84), rough=0.35)
    M["couch"] = mat("Couch", tex="fabric_white.jpg", rough=0.95, spec=0.2)
    M["blanket"] = mat("Decke_Grau", tex="fabric_grey.jpg", rough=1.0, spec=0.1)
    M["duvet"] = mat("Bettdecke", tex="fabric_blue.jpg", rough=0.95, spec=0.1)
    M["iron"] = mat("Gusseisen", color=(0.035, 0.032, 0.03), rough=0.65, metal=0.7)
    M["glow_fire"] = mat("Glow_Feuer", color=(1, 0.5, 0.15), emit=(1.0, 0.42, 0.08), emit_strength=25)
    M["glow_iron"] = mat("Glow_Eisen", color=(0.04, 0.02, 0.012), rough=0.6, metal=0.5, emit=(0.85, 0.09, 0.015), emit_strength=1.6)
    M["glow_pipe"] = mat("Glow_Rohr", color=(0.04, 0.02, 0.012), rough=0.6, metal=0.5, emit=(0.7, 0.06, 0.01), emit_strength=0.55)
    M["glow_pipe2"] = mat("Glow_Rohr2", color=(0.035, 0.025, 0.02), rough=0.6, metal=0.6, emit=(0.5, 0.035, 0.005), emit_strength=0.18)
    M["steel_plate"] = mat("Ofenblech", color=(0.35, 0.34, 0.32), rough=0.4, metal=1.0)
    M["black"] = mat("Schwarz_Glanz", color=(0.015, 0.015, 0.017), rough=0.2)
    M["screen"] = mat("TV_Screen", color=(0.01, 0.012, 0.016), rough=0.08, emit=(0.05, 0.08, 0.16), emit_strength=0.4)
    M["wood_dark"] = mat("Holz_Dunkel", color=(0.12, 0.065, 0.035), rough=0.45)
    M["wood_light"] = mat("Holz_Hell", color=(0.55, 0.38, 0.22), rough=0.5)
    M["wood_mid"] = mat("Holz_Mittel", color=(0.32, 0.19, 0.1), rough=0.5)
    M["cabinet"] = mat("Kueche_Weiss", color=(0.86, 0.86, 0.83), rough=0.3)
    M["counter"] = mat("Arbeitsplatte", tex="wood_planks.jpg", color=(0.95, 0.8, 0.65), rough=0.4)
    M["steel"] = mat("Edelstahl", color=(0.7, 0.7, 0.7), rough=0.25, metal=1.0)
    M["chrome"] = mat("Chrom", color=(0.85, 0.85, 0.85), rough=0.08, metal=1.0)
    M["fridge"] = mat("Kuehlschrank", color=(0.86, 0.82, 0.7), rough=0.25)
    M["ceramic"] = mat("Keramik", color=(0.92, 0.92, 0.9), rough=0.1)
    M["sheets"] = mat("Laken", tex="fabric_white.jpg", rough=1.0, spec=0.1)
    M["skin"] = mat("Haut", color=(0.78, 0.55, 0.42), rough=0.55)
    M["hair"] = mat("Haare", color=(0.09, 0.06, 0.04), rough=0.7)
    M["stubble"] = mat("Bart", color=(0.35, 0.24, 0.17), rough=0.8)
    M["shirt"] = mat("TShirt", color=(0.9, 0.9, 0.9), rough=0.9)
    M["cardboard"] = mat("Karton", tex="cardboard.jpg", rough=0.9, spec=0.2)
    M["paper"] = mat("Zeitung", color=(0.78, 0.76, 0.7), rough=0.95)
    M["plastic_black"] = mat("Muellsack", color=(0.02, 0.02, 0.025), rough=0.25)
    M["plastic_blue"] = mat("Tuete_Blau", color=(0.1, 0.25, 0.6), rough=0.35)
    M["red"] = mat("Rot", color=(0.45, 0.06, 0.05), rough=0.5)
    M["mate_glass"] = mat("Mate_Glas", color=(0.16, 0.07, 0.02), rough=0.05, spec=1.0)
    M["mate_label"] = mat("Mate_Etikett", tex="mate_label.png", rough=0.6)
    M["cap"] = mat("Kronkorken", color=(0.75, 0.6, 0.2), rough=0.3, metal=1.0)
    M["can"] = mat("Dose_Etikett", tex="energy_can.png", rough=0.25, metal=0.6)
    M["alu"] = mat("Alu", color=(0.75, 0.75, 0.77), rough=0.25, metal=1.0)
    M["quark_side"] = mat("Quark_Seite", tex="quark_side.png", rough=0.4)
    M["quark_lid"] = mat("Quark_Deckel", tex="quark_lid.png", rough=0.3, metal=0.4)
    M["quark_in"] = mat("Quark", color=(0.95, 0.95, 0.92), rough=0.6)
    M["pizza"] = mat("Pizza", tex="pizza.png", rough=0.7)
    M["crust"] = mat("Pizzarand", color=(0.72, 0.47, 0.22), rough=0.8)
    M["pizzabox_lid"] = mat("Pizzakarton_Deckel", tex="pizza_box.jpg", rough=0.9)
    M["pizzabox"] = mat("Pizzakarton", color=(0.62, 0.5, 0.34), rough=0.9)
    M["leather"] = mat("Leder_Schwarz", color=(0.03, 0.02, 0.018), rough=0.4)
    M["print_facade"] = mat("Bild_Fassade", tex="print_facade.jpg", rough=0.3)
    M["print_figure"] = mat("Bild_Figur", tex="print_figure.jpg", rough=0.3)
    M["frame_black"] = mat("Rahmen_Schwarz", color=(0.03, 0.03, 0.03), rough=0.4)
    M["mirror"] = mat("Spiegel", color=(0.9, 0.9, 0.92), rough=0.02, metal=1.0)
    M["night"] = mat("Nachtfenster", tex="night_window.png", rough=0.1, emit=(1, 1, 1), emit_strength=1.3, emit_tex=True)
    M["bulb"] = mat("Gluehbirne", color=(1, 0.9, 0.7), emit=(1.0, 0.75, 0.45), emit_strength=40)
    M["lampshade"] = mat("Lampenschirm", color=(0.9, 0.75, 0.55), rough=0.9, emit=(1.0, 0.55, 0.25), emit_strength=1.5)
    M["cord"] = mat("Kabel", color=(0.02, 0.02, 0.02), rough=0.5)
    M["nest"] = mat("Nest_Papier", color=(0.82, 0.76, 0.62), rough=1.0)
    M["nest2"] = mat("Nest_Stoff", color=(0.55, 0.35, 0.3), rough=1.0)
    M["crate"] = mat("Kasten_Gelb", color=(0.85, 0.6, 0.05), rough=0.5)
    M["glass_dark"] = mat("Glas_Dunkel", color=(0.02, 0.025, 0.03), rough=0.05, spec=1.0)
    M["rubber"] = mat("Gummi", color=(0.03, 0.03, 0.03), rough=0.8)
    M["jeans"] = mat("Jeans", color=(0.12, 0.18, 0.32), rough=0.95)
    M["hoodie"] = mat("Hoodie", color=(0.1, 0.16, 0.5), rough=0.95)
    M["cloth_green"] = mat("Stoff_Gruen", color=(0.12, 0.25, 0.12), rough=0.95)
    M["fairy"] = mat("Lichterkette", color=(1, 0.8, 0.4), emit=(1.0, 0.7, 0.3), emit_strength=12)
    M["doormat"] = mat("Fussmatte", tex="fabric_grey.jpg", color=(0.6, 0.45, 0.3), rough=1.0)
    M["door_entry"] = mat("Wohnungstuer", color=(0.3, 0.17, 0.09), rough=0.45)
    M["suitcase"] = mat("Koffer", color=(0.35, 0.08, 0.06), rough=0.5)
    M["kitchen_black"] = mat("Kueche_Schwarz", color=(0.025, 0.025, 0.028), rough=0.35)
    M["pallet"] = mat("Palette", tex="wood_planks.jpg", color=(1.0, 0.95, 0.8), rough=0.85, spec=0.2)
    M["battlemat"] = mat("Spielplatte", color=(0.2, 0.26, 0.13), rough=0.95)
    M["mini_blue"] = mat("Mini_Blau", color=(0.06, 0.13, 0.5), rough=0.4)
    M["mini_gold"] = mat("Mini_Gold", color=(0.75, 0.55, 0.15), rough=0.3, metal=0.8)
    M["mini_ork"] = mat("Mini_Orkhaut", color=(0.17, 0.36, 0.08), rough=0.6)
    M["mini_rust"] = mat("Mini_Rost", color=(0.32, 0.17, 0.08), rough=0.7)
    M["mini_grey"] = mat("Mini_Grundiert", color=(0.45, 0.45, 0.47), rough=0.7)
    M["mini_skin"] = mat("Mini_Haut", color=(0.75, 0.55, 0.42), rough=0.6)
    M["ruin"] = mat("Ruine", color=(0.42, 0.41, 0.39), rough=0.9)
    M["print_dean1"] = mat("Bild_JamesDean1", tex="print_dean1.jpg", rough=0.3)
    M["print_dean2"] = mat("Bild_JamesDean2", tex="print_dean2.jpg", rough=0.3)
    M["klo_top"] = mat("Klo_Innen_Dreckig", tex="toilet_top.png", rough=0.15)
    M["klo_side"] = mat("Klo_Aussen_Dreckig", tex="toilet_side.png", rough=0.15)
    M["klobrille"] = mat("Klobrille_Vergilbt", color=(0.78, 0.7, 0.5), rough=0.3)
    M["urin"] = mat("Fleck_Gelb", color=(0.62, 0.5, 0.16), rough=0.1)
    M["socke"] = mat("Socke", color=(0.62, 0.59, 0.52), rough=1.0)
    M["socke_dreck"] = mat("Socke_Dreckig", color=(0.3, 0.25, 0.18), rough=1.0)
    M["tights"] = mat("Strumpfhose_Pink", color=(0.95, 0.3, 0.6), rough=0.45, spec=0.7)
    M["bucket"] = mat("Eimer", color=(0.12, 0.38, 0.75), rough=0.35)
    M["bucket_in"] = mat("Eimer_Innen", color=(0.03, 0.05, 0.08), rough=0.6)
    M["eye_white"] = mat("Augapfel", color=(0.95, 0.95, 0.92), rough=0.2)
    M["rat_fur"] = mat("Ratte_Fell", color=(0.28, 0.24, 0.21), rough=0.9)
    M["rat_belly"] = mat("Ratte_Bauch", color=(0.55, 0.5, 0.45), rough=0.9)
    M["rat_pink"] = mat("Ratte_Rosa", color=(0.85, 0.52, 0.52), rough=0.5)
    M["rat_tail"] = mat("Ratte_Schwanz", color=(0.7, 0.54, 0.52), rough=0.5)
    M["whisker"] = mat("Schnurrhaare", color=(0.85, 0.85, 0.8), rough=0.5)
    M["hole"] = mat("Mauseloch", color=(0.004, 0.003, 0.003), rough=1.0, spec=0.0)
    M["water"] = mat("Wasser", color=(0.62, 0.74, 0.8), rough=0.03, spec=1.0)
    wb = next(n for n in M["water"].node_tree.nodes if n.type == "BSDF_PRINCIPLED")
    wb.inputs["Transmission Weight"].default_value = 0.85
    wb.inputs["IOR"].default_value = 1.33
    return M


M = build_materials()


# --------------------------------------------------------------------------- Geometrie-Baukasten
def _sign(v):
    return 1.0 if v >= 0 else -1.0


def uv_box(bm, uvl, tile):
    bm.normal_update()
    for f in bm.faces:
        n = f.normal
        ax = max(range(3), key=lambda i: abs(n[i]))
        for lp in f.loops:
            co = lp.vert.co
            if ax == 2:
                u, v = co.x * _sign(n.z), co.y
            elif ax == 0:
                u, v = co.y * _sign(n.x), co.z
            else:
                u, v = -co.x * _sign(n.y), co.z
            lp[uvl].uv = (u / tile, v / tile)


def uv_fit(bm, uvl):
    """Planar auf Bounding-Box (Bilder, Pizza): grosse Flaechen bekommen 0..1."""
    bm.normal_update()
    xs = [v.co for v in bm.verts]
    mn = Vector((min(c.x for c in xs), min(c.y for c in xs), min(c.z for c in xs)))
    mx = Vector((max(c.x for c in xs), max(c.y for c in xs), max(c.z for c in xs)))
    sz = mx - mn
    sz = Vector((max(sz.x, 1e-6), max(sz.y, 1e-6), max(sz.z, 1e-6)))
    for f in bm.faces:
        n = f.normal
        ax = max(range(3), key=lambda i: abs(n[i]))
        for lp in f.loops:
            c = lp.vert.co - mn
            if ax == 2:
                u, v = c.x / sz.x, c.y / sz.y
            elif ax == 0:
                u, v = c.y / sz.y, c.z / sz.z
                if n.x < 0:
                    u = 1 - u
            else:
                u, v = c.x / sz.x, c.z / sz.z
                if n.y > 0:
                    u = 1 - u
            lp[uvl].uv = (u, v)


def uv_cyl(bm, uvl, r, h):
    bm.normal_update()
    for f in bm.faces:
        if abs(f.normal.z) > 0.6:  # Deckel
            for lp in f.loops:
                c = lp.vert.co
                lp[uvl].uv = (c.x / (2 * r) + .5, c.y / (2 * r) + .5)
            continue
        us = []
        for lp in f.loops:
            c = lp.vert.co
            us.append(math.atan2(c.y, c.x) / (2 * math.pi) + .5)
        if max(us) - min(us) > .5:
            us = [u + 1 if u < .5 else u for u in us]
        for lp, u in zip(f.loops, us):
            lp[uvl].uv = (u, (lp.vert.co.z + h / 2) / h)


def orient(n):
    """Euler, die lokale +Z auf Normale n dreht (lokal +Y bleibt oben)."""
    n = Vector(n).normalized()
    up = Vector((0, 0, 1))
    right = (-n).cross(up).normalized()
    up = n.cross(right).normalized()
    return Matrix((right, up, n)).transposed().to_euler()


class Part:
    """Sammelt Primitive (mit Materialindex + UVs) in einem bmesh."""

    def __init__(self, name):
        self.name = name
        self.bm = bmesh.new()
        self.bm.loops.layers.uv.new("UVMap")
        self.mats = []
        self.stack = [Matrix.Identity(4)]

    @contextmanager
    def frame(self, loc=(0, 0, 0), rz=0.0, rx=0.0, ry=0.0, scale=(1, 1, 1)):
        m = Matrix.Translation(Vector(loc)) @ Euler((rx, ry, rz), "XYZ").to_matrix().to_4x4() \
            @ Matrix.Diagonal((*scale, 1))
        self.stack.append(self.stack[-1] @ m)
        try:
            yield
        finally:
            self.stack.pop()

    def _mi(self, m):
        if m not in self.mats:
            self.mats.append(m)
        return self.mats.index(m)

    def _merge(self, bm2, local, mat, uv="box", tile=1.0, smooth=False, uvfn=None, mat_fn=None):
        uvl = bm2.loops.layers.uv.get("UVMap") or bm2.loops.layers.uv.new("UVMap")
        if uvfn:
            uvfn(bm2, uvl)
        elif uv == "fit":
            uv_fit(bm2, uvl)
        bmesh.ops.transform(bm2, matrix=self.stack[-1] @ local, verts=bm2.verts)
        if uv == "box" and not uvfn:
            uv_box(bm2, uvl, tile)
        for f in bm2.faces:
            f.material_index = self._mi(mat_fn(f) if mat_fn else mat)
            f.smooth = smooth
        me = bpy.data.meshes.new("_tmp")
        bm2.to_mesh(me)
        bm2.free()
        # Materialindizes muessen auf diesen Part zeigen: Slots im tmp-Mesh nicht noetig,
        # from_mesh uebernimmt material_index 1:1.
        self.bm.from_mesh(me)
        bpy.data.meshes.remove(me)

    # -- Primitive ------------------------------------------------------------
    def box(self, size, center, mat, bevel=0.0, tile=1.0, rot=(0, 0, 0), uv="box", segs=2, smooth=False):
        bm2 = bmesh.new()
        bmesh.ops.create_cube(bm2, size=1.0)
        bmesh.ops.scale(bm2, vec=Vector(size), verts=bm2.verts)
        if bevel > 0:
            bmesh.ops.bevel(bm2, geom=list(bm2.edges), offset=min(bevel, min(size) * .49), offset_type="OFFSET",
                            segments=segs, profile=0.5, affect="EDGES", clamp_overlap=True)
        local = Matrix.Translation(Vector(center)) @ Euler(rot, "XYZ").to_matrix().to_4x4()
        self._merge(bm2, local, mat, uv=uv, tile=tile, smooth=smooth)

    def cyl(self, r, h, center, mat, cap=None, segs=16, rot=(0, 0, 0), r2=None, uv="cyl", tile=1.0,
            smooth=True):
        bm2 = bmesh.new()
        bmesh.ops.create_cone(bm2, cap_ends=True, cap_tris=False, segments=segs, radius1=r,
                              radius2=r if r2 is None else r2, depth=h)
        local = Matrix.Translation(Vector(center)) @ Euler(rot, "XYZ").to_matrix().to_4x4()
        bm2.normal_update()
        capm = cap or mat
        fn = (lambda f: capm if abs(f.normal.z) > .6 else mat)
        if uv == "cyl":
            self._merge(bm2, local, mat, uvfn=lambda b, l: uv_cyl(b, l, max(r, r2 or r), h), smooth=False,
                        mat_fn=fn)
        else:
            self._merge(bm2, local, mat, uv=uv, tile=tile, mat_fn=fn)
        if smooth:
            pass  # Seiten weich: wird ueber Auto-Smooth (Winkel) beim Finish geregelt

    def sphere(self, r, center, mat, scale=(1, 1, 1), segs=(16, 10), rot=(0, 0, 0), tile=1.0):
        bm2 = bmesh.new()
        bmesh.ops.create_uvsphere(bm2, u_segments=segs[0], v_segments=segs[1], radius=r)
        bmesh.ops.scale(bm2, vec=Vector(scale), verts=bm2.verts)
        local = Matrix.Translation(Vector(center)) @ Euler(rot, "XYZ").to_matrix().to_4x4()
        self._merge(bm2, local, mat, uv="box", tile=tile, smooth=True)

    def plane(self, sx, sy, center, mat, rot=(0, 0, 0), tile=None):
        """Flaeche in lokaler XY-Ebene, Normale +Z, UV 0..1 (oder Welt-Kachelung mit tile)."""
        bm2 = bmesh.new()
        uvl = bm2.loops.layers.uv.new("UVMap")
        vs = [bm2.verts.new((x * sx / 2, y * sy / 2, 0)) for x, y in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
        f = bm2.faces.new(vs)
        for lp, uvv in zip(f.loops, ((0, 0), (1, 0), (1, 1), (0, 1))):
            lp[uvl].uv = uvv
        local = Matrix.Translation(Vector(center)) @ Euler(rot, "XYZ").to_matrix().to_4x4()
        if tile:
            self._merge(bm2, local, mat, uv="box", tile=tile)
        else:
            self._merge(bm2, local, mat, uvfn=lambda b, l: None)

    def tube(self, pts, radii, mat, segs=8, closed=False, caps=True, tile=1.0):
        """Rohr entlang Punkten (Peitsche, Ofenrohr, Stuhllehne)."""
        pts = [Vector(p) for p in pts]
        if isinstance(radii, (int, float)):
            radii = [radii] * len(pts)
        bm2 = bmesh.new()
        rings = []
        n = len(pts)
        prev_side = None
        for i, p in enumerate(pts):
            if closed:
                t = (pts[(i + 1) % n] - pts[i - 1])
            else:
                t = (pts[min(i + 1, n - 1)] - pts[max(i - 1, 0)])
            t.normalize()
            ref = Vector((0, 0, 1)) if abs(t.z) < .9 else Vector((1, 0, 0))
            side = t.cross(ref).normalized() if prev_side is None else (prev_side - t * prev_side.dot(t)).normalized()
            prev_side = side
            up = side.cross(t).normalized()
            ring = []
            for k in range(segs):
                a = 2 * math.pi * k / segs
                ring.append(bm2.verts.new(p + (side * math.cos(a) + up * math.sin(a)) * radii[i]))
            rings.append(ring)
        rng_ = range(n if closed else n - 1)
        for i in rng_:
            r0, r1 = rings[i], rings[(i + 1) % n]
            for k in range(segs):
                bm2.faces.new((r0[k], r0[(k + 1) % segs], r1[(k + 1) % segs], r1[k]))
        if caps and not closed:
            bm2.faces.new(list(reversed(rings[0])))
            bm2.faces.new(rings[-1])
        bmesh.ops.recalc_face_normals(bm2, faces=bm2.faces)
        self._merge(bm2, Matrix.Identity(4), mat, uv="box", tile=tile, smooth=True)

    def poly_prism(self, outline, z0, z1, mat, side_mat=None, uv_top="fit"):
        """Extrudiertes Polygon (Pizzastuecke)."""
        bm2 = bmesh.new()
        bot = [bm2.verts.new((x, y, z0)) for x, y in outline]
        top = [bm2.verts.new((x, y, z1)) for x, y in outline]
        bm2.faces.new(list(reversed(bot)))
        bm2.faces.new(top)
        n = len(outline)
        for i in range(n):
            bm2.faces.new((bot[i], bot[(i + 1) % n], top[(i + 1) % n], top[i]))
        bm2.normal_update()
        sm = side_mat or mat
        self._merge(bm2, Matrix.Identity(4), mat, uvfn=None, uv="box", tile=1.0,
                    mat_fn=lambda f: mat if abs(f.normal.z) > .6 else sm)

    # -- Abschluss ------------------------------------------------------------
    def finish(self, collection, loc=(0, 0, 0), rz=0.0, props=None):
        me = bpy.data.meshes.new(self.name)
        self.bm.to_mesh(me)
        self.bm.free()
        for m in self.mats:
            me.materials.append(m)
        ob = bpy.data.objects.new(self.name, me)
        collection.objects.link(ob)
        ob.location = loc
        ob.rotation_euler = (0, 0, rz)
        for k, v in (props or {}).items():
            ob[k] = v
        return ob


def solid(x0, y0, x1, y1, kind="solid"):
    LAYOUT["solids"].append({"r": [round(min(x0, x1), 3), round(min(y0, y1), 3),
                                   round(max(x0, x1), 3), round(max(y0, y1), 3)], "kind": kind})


# --------------------------------------------------------------------------- Rohbau
S = Part("Apartment")
CEIL = Part("Ceiling")


def wall(axis, fixed, a, b, thick, openings=(), sides=(1, -1), casing_sides=None):
    """Achsparallele Wand mit Oeffnungen [(start, ende, oberkante)]."""
    casing_sides = sides if casing_sides is None else casing_sides
    segs = []
    pos = a
    for s, e, top in sorted(openings):
        if s > pos:
            segs.append((pos, s, 0.0, H))
        segs.append((s, e, top, H))
        pos = e
    if pos < b:
        segs.append((pos, b, 0.0, H))

    def put_box(s, e, z0, z1, off, th, m, bevel=0.0):
        L = e - s
        c_along = (s + e) / 2
        if axis == "x":
            S.box((L, th, z1 - z0), (c_along, fixed + off, (z0 + z1) / 2), m, tile=2.0, bevel=bevel)
        else:
            S.box((th, L, z1 - z0), (fixed + off, c_along, (z0 + z1) / 2), m, tile=2.0, bevel=bevel)

    for s, e, z0, z1 in segs:
        put_box(s, e, z0, z1, 0, thick, M["wall"])
        if z0 == 0:
            if axis == "x":
                LAYOUT["walls"].append([s, fixed - thick / 2, e, fixed + thick / 2])
            else:
                LAYOUT["walls"].append([fixed - thick / 2, s, fixed + thick / 2, e])
            for sd in sides:  # Fussleiste
                put_box(s, e, 0, 0.11, sd * (thick / 2 + 0.01), 0.022, M["trim"], bevel=0.004)
    for s, e, top in openings:
        LAYOUT["doors"].append({"axis": axis, "at": fixed, "from": s, "to": e})
        for sd in casing_sides:  # Tuerzarge / Bekleidung
            o = sd * (thick / 2 + 0.012)
            put_box(s - 0.07, s, 0, top + 0.07, o, 0.025, M["trim"], bevel=0.005)
            put_box(e, e + 0.07, 0, top + 0.07, o, 0.025, M["trim"], bevel=0.005)
            put_box(s - 0.07, e + 0.07, top, top + 0.07, o, 0.025, M["trim"], bevel=0.005)


def build_shell():
    ex = T_EXT / 2
    # Aussenwaende (Innenseite zeigt in die Wohnung)
    wall("y", X0 - ex, -T_EXT, D + T_EXT, T_EXT, sides=(1,))                       # West
    wall("x", -ex, X0 - T_EXT, W + T_EXT, T_EXT, sides=(1,))                       # Sued
    wall("x", D + ex, X0 - T_EXT, W + T_EXT, T_EXT, sides=(-1,))                   # Nord
    wall("y", W + ex, -T_EXT, D + T_EXT, T_EXT, openings=[(4.55, 5.35, DOOR_H)], sides=(-1,))  # Ost + Wohnungstuer
    # Innenwaende
    wall("y", 5.72, 0, D, T_INT, openings=[(4.55, 5.35, DOOR_H), (7.10, 7.85, DOOR_H)])
    wall("x", 4.45, X0, W, T_INT, openings=[(LX - 0.65, LX + 0.65, 2.45), (9.45, 10.25, DOOR_H)])
    wall("x", 5.45, 5.72, W, T_INT, openings=[(9.75, 10.50, DOOR_H)])
    wall("y", 8.40, 5.45, D, T_INT, openings=[(7.10, 7.80, DOOR_H)])
    wall("x", 6.70, 8.40, W, T_INT)

    # Boden: alte Dielen ueberall, Fliesen in Bad + Toilette
    S.plane(W - X0 + 0.1, D + 0.1, ((X0 + W) / 2, D / 2, 0), M["floor"], tile=1.6)
    for rn in ("Bad", "Toilette"):
        x0, y0, x1, y1 = ROOMS[rn]
        S.plane(x1 - x0 - T_INT, y1 - y0 - T_INT, ((x0 + x1) / 2, (y0 + y1) / 2, 0.004), M["tiles"], tile=0.6)
    # Schwellen
    for (cx, cy, sx, sy) in ((5.72, 4.95, 0.12, 0.8), (8.40, 7.45, 0.12, 0.7), (10.125, 5.45, 0.75, 0.12),
                             (5.72, 7.475, 0.12, 0.75)):
        S.box((sx, sy, 0.012), (cx, cy, 0.006), M["wood_mid"], bevel=0.004)
    # Decke (eigenes Objekt, damit die Draufsicht sie ausblenden kann)
    CEIL.plane(W - X0 + 0.1, D + 0.1, ((X0 + W) / 2, D / 2, H), M["ceiling"], rot=(math.pi, 0, 0), tile=2.0)
    # Stuckleiste im Wohnzimmer
    for (axis, fixed, a, b, sd) in (("y", X0, 0, 4.45, 1), ("x", 0, X0, 5.72, 1), ("x", 4.45, X0, 5.72, -1),
                                    ("y", 5.72, 0, 4.45, -1)):
        L = b - a
        if axis == "x":
            S.box((L, 0.06, 0.08), ((a + b) / 2, fixed + sd * 0.09, H - 0.04), M["trim"], bevel=0.015)
        else:
            S.box((0.06, L, 0.08), (fixed + sd * 0.09, (a + b) / 2, H - 0.04), M["trim"], bevel=0.015)


def window(axis, fixed, a, b, sill, top, inward):
    """Fenster als Wand-Aufsatz: Rahmen + nachtblaue Scheiben + Fensterbank (keine echte Oeffnung)."""
    L = b - a
    hgt = top - sill
    c = (a + b) / 2
    zc = (sill + top) / 2
    o = inward * 0.02

    def at(along, off, z):
        return (along, fixed + off, z) if axis == "x" else (fixed + off, along, z)

    def sz(l, th, h):
        return (l, th, h) if axis == "x" else (th, l, h)

    rot = orient((0, inward, 0) if axis == "x" else (inward, 0, 0))
    S.plane(L - 0.04, hgt - 0.04, at(c, o, zc), M["night"], rot=rot)
    fw = 0.06
    for along, ll in ((a + fw / 2, fw), (b - fw / 2, fw), (c, fw)):
        S.box(sz(ll, 0.06, hgt), at(along, o + inward * 0.02, zc), M["trim"], bevel=0.006)
    for z in (sill + fw / 2, top - fw / 2, sill + hgt * 0.68):
        S.box(sz(L, 0.06, fw), at(c, o + inward * 0.02, z), M["trim"], bevel=0.006)
    S.box(sz(L + 0.12, 0.22, 0.04), at(c, inward * 0.11, sill - 0.02), M["trim"], bevel=0.008)  # Fensterbank
    # Mondlicht
    p = Vector(at(c, inward * 0.45, zc))
    light("Mond", "AREA", p, (0.55, 0.65, 1.0), 14, size=(L, hgt),
          direction=Vector((0, inward, 0)) if axis == "x" else Vector((inward, 0, 0)))


def light(name, kind, loc, color, energy, size=0.05, direction=None):
    ld = bpy.data.lights.new(name, kind)
    ld.color = color
    ld.energy = energy
    if kind == "AREA":
        ld.shape = "RECTANGLE"
        ld.size, ld.size_y = size
    else:
        ld.shadow_soft_size = size
    ob = bpy.data.objects.new(name, ld)
    ob.location = loc
    if direction is not None:
        ob.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
    C_LIGHT.objects.link(ob)
    if kind in ("POINT", "AREA"):
        LAYOUT["lights"].append({"name": name, "pos": [round(v, 3) for v in loc], "color": list(color),
                                 "energy": energy})
    return ob


def door_leaf(hinge, angle_deg, width, mat=None, thick=0.04, ajar_name=None):
    a = math.radians(angle_deg)
    m = mat or M["trim"]
    with S.frame((hinge[0], hinge[1], 0), rz=a):
        S.box((width - 0.01, thick, DOOR_H - 0.01), (width / 2, 0, DOOR_H / 2), m, bevel=0.006)
        for z in (0.55, 1.45):  # Kassetten
            S.box((width - 0.24, thick + 0.012, 0.62), (width / 2, 0, z + 0.18), m, bevel=0.01)
        for sd in (1, -1):
            S.sphere(0.028, (width - 0.08, sd * 0.045, 1.05), M["chrome"], segs=(10, 6))


# --------------------------------------------------------------------------- Moebel
def ofen():
    """Alter Kanonenofen in der NW-Ecke des Wohnzimmers, hinter der Couch, gluehend."""
    cx, cy = X0 + 0.55, 3.85
    LAYOUT["oven"] = [round(cx + 0.27, 3), cy, 0.42]
    S.box((0.85, 0.85, 0.008), (cx, cy, 0.004), M["steel_plate"], bevel=0.003)
    # Hitzeschutz-Fliesen an der Wand
    S.box((0.02, 1.2, 1.5), (X0 + 0.01, cy - 0.05, 0.75), M["tiles"], tile=0.6)
    S.box((1.1, 0.02, 1.5), (X0 + 0.5, 4.38, 0.75), M["tiles"], tile=0.6)
    with S.frame((cx, cy, 0)):
        for k in range(4):
            a = math.pi / 4 + k * math.pi / 2
            S.cyl(0.022, 0.16, (0.17 * math.cos(a), 0.17 * math.sin(a), 0.08), M["iron"], segs=8, r2=0.015)
        S.cyl(0.26, 0.08, (0, 0, 0.2), M["iron"], segs=24)
        S.cyl(0.22, 0.34, (0, 0, 0.41), M["glow_iron"], segs=24)        # unterer Bauch gluehend
        S.cyl(0.235, 0.04, (0, 0, 0.6), M["iron"], segs=24)
        S.cyl(0.2, 0.32, (0, 0, 0.78), M["iron"], segs=24, r2=0.17)
        S.cyl(0.24, 0.04, (0, 0, 0.96), M["iron"], segs=24)
        S.sphere(0.17, (0, 0, 0.98), M["iron"], scale=(1, 1, 0.45), segs=(24, 8))
        # Feuertuer (zeigt nach Osten = ins Zimmer)
        S.box((0.04, 0.2, 0.17), (0.215, 0, 0.42), M["iron"], bevel=0.01)
        S.box((0.012, 0.15, 0.12), (0.238, 0, 0.42), M["glow_fire"])
        for z in (0.39, 0.42, 0.45):
            S.box((0.016, 0.16, 0.008), (0.241, 0, z), M["iron"])
        S.sphere(0.018, (0.245, -0.085, 0.44), M["chrome"], segs=(8, 6))
        # Aschekasten mit Glut-Schlitz
        S.box((0.03, 0.16, 0.05), (0.24, 0, 0.215), M["iron"])
        S.box((0.01, 0.12, 0.012), (0.257, 0, 0.215), M["glow_fire"])
    # Ofenrohr: hoch, Bogen, in die Nordwand (Kamin)
    z_bend = 2.35
    S.tube([(cx, cy, 1.0), (cx, cy, 1.4)], 0.068, M["glow_pipe"], segs=16)
    S.tube([(cx, cy, 1.4), (cx, cy, 1.85)], 0.068, M["glow_pipe2"], segs=16)
    pts = [(cx, cy, 1.85), (cx, cy, z_bend - 0.12)]
    for k in range(1, 7):
        a = k / 6 * math.pi / 2
        pts.append((cx, cy + 0.12 - 0.12 * math.cos(a), z_bend - 0.12 + 0.12 * math.sin(a)))
    pts.append((cx, 4.44, z_bend))
    S.tube(pts, 0.068, M["iron"], segs=16)
    for z in (1.4, 1.85):
        S.cyl(0.075, 0.025, (cx, cy, z), M["iron"], segs=16)
    S.cyl(0.11, 0.02, (cx, 4.38, z_bend), M["iron"], segs=16, rot=(math.pi / 2, 0, 0))  # Wandrosette
    solid(cx - 0.3, cy - 0.3, cx + 0.3, cy + 0.3, "hot")
    light("Ofen_Glut", "POINT", (cx + 0.32, cy, 0.42), (1.0, 0.42, 0.12), 60, size=0.08)
    light("Ofen_Innen", "POINT", (cx, cy, 0.7), (1.0, 0.3, 0.08), 15, size=0.15)


def couch():
    with S.frame((X0, 0, 0)):
        x0, y0, y1 = 0.0, 1.35, 3.5
        L = y1 - y0
        cy = (y0 + y1) / 2
        for yy in (y0 + 0.08, y1 - 0.08):
            for xx in (0.1, 0.85):
                S.cyl(0.025, 0.1, (xx, yy, 0.05), M["wood_dark"], segs=8)
        S.box((0.95, L, 0.24), (0.475, cy, 0.22), M["couch"], bevel=0.03, tile=0.5)
        S.box((0.78, L - 0.3, 0.16), (0.53, cy, 0.42), M["couch"], bevel=0.06, tile=0.5)          # Sitzpolster
        S.box((0.22, L - 0.1, 0.5), (0.13, cy, 0.58), M["couch"], bevel=0.08, tile=0.5)           # Rueckenlehne
        for yy in (y0 + 0.08, y1 - 0.08):
            S.box((0.95, 0.16, 0.36), (0.475, yy, 0.5), M["couch"], bevel=0.07, tile=0.5)           # Armlehnen
        # Kissen
        S.box((0.16, 0.55, 0.45), (0.33, y1 - 0.45, 0.68), M["couch"], bevel=0.07, rot=(0, -0.25, 0.1), tile=0.5)
        S.box((0.16, 0.5, 0.42), (0.33, y1 - 0.95, 0.66), M["couch"], bevel=0.07, rot=(0, -0.3, -0.05), tile=0.5)
        S.box((0.15, 0.45, 0.4), (0.32, y0 + 0.5, 0.64), M["couch"], bevel=0.07, rot=(0, -0.28, 0.15), tile=0.5)
        # graue Decke, ueber den Sitz gelegt und vorne runterhaengend
        S.box((0.62, 1.2, 0.05), (0.56, cy + 0.25, 0.525), M["blanket"], bevel=0.02, rot=(0.02, 0, 0.05), tile=0.6)
        S.box((0.05, 1.1, 0.34), (0.92, cy + 0.3, 0.36), M["blanket"], bevel=0.02, rot=(0, 0.08, 0.04), tile=0.6)
        solid(0.0 + X0, y0, 0.95 + X0, y1, "under")  # Ratte passt drunter (Kriechraum)


def bild(center, w, h, m, frame_mat, axis_normal, lean=0.0):
    """Bilderrahmen. axis_normal: Richtung, in die das Bild schaut ('+x','-y',...)."""
    rz = {"+x": -math.pi / 2, "-x": math.pi / 2, "+y": 0, "-y": math.pi}[axis_normal]
    with S.frame(center, rz=rz):
        with S.frame((0, 0, 0), rx=lean):
            fw = 0.04
            S.box((w, 0.03, fw), (0, 0, h / 2 - fw / 2), frame_mat, bevel=0.004)
            S.box((w, 0.03, fw), (0, 0, -h / 2 + fw / 2), frame_mat, bevel=0.004)
            S.box((fw, 0.03, h), (w / 2 - fw / 2, 0, 0), frame_mat, bevel=0.004)
            S.box((fw, 0.03, h), (-w / 2 + fw / 2, 0, 0), frame_mat, bevel=0.004)
            S.box((w - 0.02, 0.01, h - 0.02), (0, 0.008, 0), M["trim"])  # Passepartout
            S.plane(w - 0.2, h - 0.2, (0, 0.017, 0), m, rot=orient((0, 1, 0)))


def wohnzimmer():
    ofen()
    couch()
    # Grosses S/W-Bild ueber der Couch (wie auf dem Foto)
    bild((X0 + 0.03, 2.35, 1.7), 1.3, 1.0, M["print_facade"], M["wood_light"], "+x")
    # Hoher Spiegel, zwischen Ofen und Durchgang an die Nordwand gelehnt
    with S.frame((2.36, 4.26, 0)):
        with S.frame((0, 0, 0), rx=-0.09):
            S.box((0.5, 0.04, 1.8), (0, 0, 0.9), M["trim"], bevel=0.01)
            S.box((0.42, 0.01, 1.72), (0, -0.022, 0.9), M["mirror"])
    # Couchtisch
    with S.frame((2.65, 2.45, 0)):
        S.box((0.6, 1.0, 0.035), (0, 0, 0.4), M["wood_light"], bevel=0.008)
        for sx in (-0.26, 0.26):
            for sy in (-0.45, 0.45):
                S.box((0.035, 0.035, 0.385), (sx, sy, 0.19), M["wood_light"])
        S.box((0.52, 0.92, 0.02), (0, 0, 0.12), M["wood_light"])
    # Sideboard + TV an der Ostwand
    with S.frame((5.46, 2.3, 0)):
        S.box((0.4, 1.7, 0.42), (0, 0, 0.26), M["wood_dark"], bevel=0.01)
        for sy in (-0.75, 0.75):
            for sx in (-0.15, 0.15):
                S.cyl(0.015, 0.05, (sx, sy, 0.025), M["chrome"], segs=8)
        S.box((0.012, 0.8, 0.3), (-0.2, -0.42, 0.26), M["wood_mid"])
        S.box((0.012, 0.8, 0.3), (-0.2, 0.42, 0.26), M["wood_mid"])
        S.box((0.25, 0.3, 0.015), (0, 0, 0.478), M["black"])
        S.box((0.04, 0.06, 0.08), (0.02, 0, 0.52), M["black"])
        S.box((0.05, 1.23, 0.72), (0.0, 0, 0.92), M["black"], bevel=0.008)
        S.box((0.005, 1.19, 0.68), (-0.026, 0, 0.92), M["screen"])
        S.box((0.3, 0.2, 0.06), (0.02, -0.6, 0.5), M["black"], bevel=0.01)  # Konsole
    solid(5.26, 1.45, 5.66, 3.15)
    # Haengelampe
    S.cyl(0.004, 0.45, (LX, 2.2, H - 0.225), M["cord"], segs=6)
    S.cyl(0.02, 0.05, (LX, 2.2, H - 0.47), M["black"], segs=10)
    S.sphere(0.055, (LX, 2.2, H - 0.54), M["bulb"], scale=(1, 1, 1.2), segs=(14, 10))
    light("Lampe_Wohnzimmer", "POINT", (LX, 2.2, H - 0.56), (1.0, 0.76, 0.5), 90, size=0.06)
    # nur noch ein Fenster nach Sueden
    window("x", 0.0, LX - 0.65, LX + 0.65, 0.85, 2.5, 1)


def stuhl(x, y, rz):
    with S.frame((x, y, 0), rz=rz):
        S.cyl(0.2, 0.035, (0, 0, 0.45), M["wood_dark"], segs=20)
        for k in range(4):
            a = math.pi / 4 + k * math.pi / 2
            S.cyl(0.014, 0.44, (0.15 * math.cos(a), 0.15 * math.sin(a), 0.22), M["wood_dark"], segs=6)
        S.tube([(0.16 * math.cos(a), 0.16 * math.sin(a), 0.42) for a in np.linspace(0, 2 * math.pi, 17)[:-1]],
               0.01, M["wood_dark"], segs=6, closed=True)
        pts = []
        for t in np.linspace(0, 1, 13):
            a = math.pi * 0.25 + t * math.pi * 1.5
            # U-foermige Bugholz-Lehne hinten (-x)
            pts.append((-0.17 + 0.0 * t, 0.17 * math.cos(a), 0.47 + 0.42 * math.sin(a * 0.5 + 0.2)))
        S.tube([(-0.17, -0.14, 0.46), (-0.2, -0.15, 0.7), (-0.22, -0.1, 0.88), (-0.23, 0, 0.92),
                (-0.22, 0.1, 0.88), (-0.2, 0.15, 0.7), (-0.17, 0.14, 0.46)], 0.013, M["wood_dark"], segs=6)
        S.tube([(-0.19, -0.13, 0.62), (-0.21, 0, 0.66), (-0.19, 0.13, 0.62)], 0.009, M["wood_dark"], segs=6)


def kueche():
    # Retro-Kuehlschrank NW-Ecke, Front zeigt nach Osten (zur Kuechenzeile)
    with S.frame((X0 + 0.36, 7.66, 0), rz=math.pi / 2):
        S.box((0.65, 0.66, 1.6), (0, 0, 0.82), M["fridge"], bevel=0.06)
        S.box((0.02, 0.6, 0.004), (0, -0.335, 1.15), M["black"])
        S.box((0.04, 0.05, 0.3), (0.25, -0.35, 1.25), M["chrome"], bevel=0.01)
        S.box((0.6, 0.6, 0.02), (0, 0, 0.01), M["black"])
    solid(X0 + 0.03, 7.33, X0 + 0.69, 8.0)
    # Kuechentisch + Stuehle
    with S.frame((2.3, 6.15, 0)):
        S.box((1.4, 0.85, 0.04), (0, 0, 0.74), M["wood_light"], bevel=0.008)
        for sx in (-0.62, 0.62):
            for sy in (-0.36, 0.36):
                S.box((0.05, 0.05, 0.72), (sx, sy, 0.36), M["wood_light"])
        S.box((1.24, 0.03, 0.08), (0, 0.36, 0.67), M["wood_light"])
        S.box((1.24, 0.03, 0.08), (0, -0.36, 0.67), M["wood_light"])
    stuhl(1.95, 5.45, math.pi / 2)
    stuhl(2.7, 6.85, -math.pi / 2)
    stuhl(3.3, 6.1, math.pi + 0.3)

    # Schwarze Kuechenzeile an der Ostwand (die am Fenster ist weg)
    def zeile(x0, y0, x1, y1, front):
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        sx, sy = x1 - x0, y1 - y0
        S.box((sx, sy, 0.1), (cx, cy, 0.05), M["black"])
        S.box((sx - 0.02, sy - 0.02, 0.76), (cx, cy, 0.48), M["kitchen_black"])
        S.box((sx + 0.02, sy + 0.02, 0.04), (cx, cy, 0.88), M["counter"], bevel=0.005, tile=1.6)
        L = sy if front[0] else sx
        n = max(1, int(round(L / 0.6)))
        for i in range(n):
            t = (i + 0.5) / n
            px, py = (x0 if front[0] < 0 else x1), y0 + t * sy
            S.box((0.02, L / n - 0.008, 0.72), (px, py, 0.48), M["kitchen_black"], bevel=0.004)
            S.box((0.02, 0.012, 0.2), (px + front[0] * 0.015, py + L / n * 0.35, 0.7), M["steel"])
        solid(x0, y0, x1, y1)

    zeile(5.06, 5.45, 5.66, 6.95, (-1, 0))
    # Herd + Backofen
    S.box((0.5, 0.55, 0.012), (5.36, 6.55, 0.906), M["black"])
    for dx, dy, r in ((-0.12, -0.13, 0.08), (0.12, -0.13, 0.06), (-0.12, 0.13, 0.06), (0.12, 0.13, 0.08)):
        S.cyl(r, 0.004, (5.36 + dx, 6.55 + dy, 0.914), M["iron"], segs=16)
    S.box((0.02, 0.52, 0.42), (5.045, 6.55, 0.52), M["glass_dark"], bevel=0.005)
    S.box((0.03, 0.4, 0.02), (5.03, 6.55, 0.76), M["steel"])
    # Spuele (jetzt in der Ostzeile)
    S.box((0.42, 0.5, 0.02), (5.36, 5.85, 0.905), M["steel"])
    S.box((0.34, 0.42, 0.012), (5.36, 5.85, 0.912), M["iron"])
    S.tube([(5.6, 5.85, 0.9), (5.6, 5.85, 1.15), (5.48, 5.85, 1.2), (5.42, 5.85, 1.13)], 0.012, M["chrome"])
    # Oberschraenke Ostwand
    S.box((0.35, 1.48, 0.7), (5.48, 6.2, 1.85), M["kitchen_black"], bevel=0.008)
    for yy in (5.83, 6.57):
        S.box((0.02, 0.72, 0.68), (5.295, yy, 1.85), M["kitchen_black"], bevel=0.004)
    # Mate-Kasten + Muelleimer an der Westwand
    mate_kasten(X0 + 0.23, 4.8, math.pi / 2)
    S.cyl(0.15, 0.45, (X0 + 0.2, 5.3, 0.225), M["steel"], segs=16)
    S.cyl(0.155, 0.03, (X0 + 0.2, 5.3, 0.46), M["black"], segs=16)
    solid(X0 + 0.05, 5.15, X0 + 0.35, 5.45)
    # James-Dean-Bilder, auf dem Boden an die Nordwand gelehnt
    bild((2.12, 7.93, 0.4), 0.55, 0.75, M["print_dean1"], M["frame_black"], "-y", lean=0.12)
    bild((2.66, 7.95, 0.32), 0.45, 0.6, M["print_dean2"], M["trim"], "-y", lean=0.1)
    # Andere Ecke: Warhammer-Figuren auf einer Europalette
    warhammer_palette(4.55, 7.58)
    # Haengelampe ueber dem Tisch
    S.cyl(0.004, 0.6, (2.3, 6.15, H - 0.3), M["cord"], segs=6)
    S.cyl(0.18, 0.15, (2.3, 6.15, H - 0.65), M["iron"], segs=20, r2=0.03)
    S.sphere(0.045, (2.3, 6.15, H - 0.73), M["bulb"], segs=(12, 8))
    light("Lampe_Kueche", "POINT", (2.3, 6.15, H - 0.78), (1.0, 0.8, 0.55), 70, size=0.05)
    window("x", D, LX - 0.6, LX + 0.6, 0.9, 2.5, -1)


def mini_on(P, x, y, z, rz, body, trim, head, ork=False):
    """Eine 28mm-Tabletop-Figur (stark vereinfacht)."""
    s = 1.3 if ork else 1.0
    with P.frame((x, y, z), rz=rz):
        P.cyl(0.0125 * s, 0.004, (0, 0, 0.002), M["black"], segs=10, uv="box")
        for sy in (-0.0045, 0.0045):
            P.cyl(0.0034 * s, 0.012 * s, (0, sy * s, 0.004 + 0.006 * s), body, segs=6, uv="box")
        P.box((0.011 * s, 0.016 * s, 0.013 * s), (0, 0, 0.004 + 0.018 * s), body, uv="box")
        for sy in (-0.0095, 0.0095):
            P.sphere(0.0058 * s, (0, sy * s, 0.004 + 0.025 * s), trim, segs=(8, 5))
        P.sphere(0.0045 * s, (0.001, 0, 0.004 + 0.031 * s), head, segs=(8, 5))
        P.box((0.02 * s, 0.004, 0.0045), (0.011 * s, -0.009 * s, 0.004 + 0.019 * s), M["iron"], uv="box")


def mini(x, y, z, rz, body, trim, head, ork=False):
    mini_on(S, x, y, z, rz, body, trim, head, ork)


def warhammer_palette(cx, cy):
    rnd = random.Random(40)
    with S.frame((cx, cy, 0)):
        for yy in (-0.35, 0, 0.35):                          # Bodenbretter
            S.box((1.2, 0.1, 0.022), (0, yy, 0.011), M["pallet"], tile=0.8)
        for xx in (-0.55, 0, 0.55):
            for yy in (-0.35, 0, 0.35):                      # Kloetze
                S.box((0.1, 0.145 if yy == 0 else 0.1, 0.078), (xx, yy, 0.061), M["pallet"], tile=0.8)
            S.box((0.1, 0.8, 0.022), (xx, 0, 0.111), M["pallet"], tile=0.8)
        for yy in np.linspace(-0.3375, 0.3375, 5):           # Deckbretter
            S.box((1.2, 0.125, 0.022), (0, yy, 0.133), M["pallet"], bevel=0.003, tile=0.8)
        S.box((1.1, 0.7, 0.012), (0, 0, 0.15), M["battlemat"], bevel=0.003)   # Spielplatte
    top = 0.156
    solid(cx - 0.6, cy - 0.4, cx + 0.6, cy + 0.4)
    # Ruinen in der Mitte
    for (dx, dy, w, h, rz) in ((-0.05, 0.12, 0.18, 0.12, 0.2), (0.08, -0.15, 0.14, 0.09, -0.4),
                               (0.02, 0.25, 0.1, 0.07, 1.4)):
        with S.frame((cx + dx, cy + dy, top), rz=rz):
            S.box((w, 0.018, h), (0, 0, h / 2), M["ruin"])
            S.box((w * 0.4, 0.02, h * 0.35), (w * 0.15, 0, h * 0.62), M["battlemat"])  # Fensterloch-Andeutung
            S.box((w * 0.3, 0.018, h * 0.5), (-w * 0.55, 0, h * 0.25), M["ruin"], rot=(0, 0.3, 0))
        for _ in range(5):
            S.box((0.02, 0.015, 0.01), (cx + dx + rnd.uniform(-.08, .08), cy + dy + rnd.uniform(-.06, .06), top + 0.005),
                  M["ruin"], rot=(rnd.uniform(-.5, .5), rnd.uniform(-.5, .5), rnd.uniform(0, 3)))
    # Space-Marines (blau/gold) links, zwei Trupps
    for row, x in enumerate((-0.42, -0.34)):
        for k in range(10):
            mini(cx + x + rnd.uniform(-.01, .01), cy - 0.27 + k * 0.055, top, rnd.uniform(-.3, .3),
                 M["mini_blue"], M["mini_gold"], M["mini_blue"])
    # Orks rechts
    for k in range(14):
        mini(cx + 0.3 + rnd.uniform(-.06, .08), cy - 0.28 + k * 0.042, top, math.pi + rnd.uniform(-.5, .5),
             M["mini_rust"], M["mini_rust"], M["mini_ork"], ork=True)
    # unbemalte Figuren + Farbtoepfe + Pinsel vorne
    for k in range(6):
        mini(cx - 0.1 + k * 0.035, cy - 0.31, top, rnd.uniform(0, 6), M["mini_grey"], M["mini_grey"], M["mini_grey"])
    for k, m in enumerate(("red", "mini_blue", "mini_gold", "mini_ork", "black", "trim")):
        S.cyl(0.012, 0.026, (cx + 0.38 + (k % 3) * 0.03, cy - 0.33 + (k // 3) * 0.03, top + 0.013), M["ceramic"], segs=10)
        S.cyl(0.0125, 0.008, (cx + 0.38 + (k % 3) * 0.03, cy - 0.33 + (k // 3) * 0.03, top + 0.03), M[m], segs=10)
    S.cyl(0.002, 0.14, (cx + 0.2, cy - 0.32, top + 0.003), M["wood_light"], segs=6, rot=(0, math.pi / 2, 0.3))
    # Panzer bei den Marines
    with S.frame((cx - 0.2, cy + 0.05, top), rz=0.1):
        S.box((0.13, 0.08, 0.035), (0, 0, 0.022), M["mini_blue"], bevel=0.004)
        for sy in (-0.045, 0.045):
            S.box((0.14, 0.022, 0.03), (0, sy, 0.016), M["iron"], bevel=0.005)
        S.box((0.05, 0.045, 0.022), (-0.01, 0, 0.05), M["mini_blue"], bevel=0.003)
        S.cyl(0.005, 0.07, (0.045, 0, 0.052), M["iron"], segs=8, rot=(0, math.pi / 2, 0))


def mate_kasten(x, y, rz):
    with S.frame((x, y, 0), rz=rz):
        S.box((0.4, 0.3, 0.02), (0, 0, 0.01), M["crate"])
        for sx, sy, w, d in ((0, 0.145, 0.4, 0.012), (0, -0.145, 0.4, 0.012), (0.195, 0, 0.012, 0.3),
                             (-0.195, 0, 0.012, 0.3)):
            S.box((w, d, 0.28), (sx, sy, 0.14), M["crate"], bevel=0.003)
        for i in range(5):
            for j in range(4):
                px, py = -0.16 + i * 0.08, -0.105 + j * 0.07
                if (i, j) in ((1, 2), (3, 0), (4, 3)):
                    continue  # leergetrunken
                S.cyl(0.033, 0.2, (px, py, 0.12), M["mate_glass"], segs=10, uv="box")
                S.cyl(0.033, 0.04, (px, py, 0.24), M["mate_glass"], segs=10, r2=0.014, uv="box")
                S.cyl(0.014, 0.03, (px, py, 0.275), M["mate_glass"], segs=8, uv="box")
                S.cyl(0.016, 0.008, (px, py, 0.293), M["cap"], segs=8, uv="box")
    hx, hy = (0.16, 0.21) if abs(math.sin(rz)) > 0.7 else (0.21, 0.16)
    solid(x - hx, y - hy, x + hx, y + hy)


BED_O = (10.2, 0.03)  # Kopfende-Mitte unter dem rechten Suedfenster


def bed_xf(x, y):
    """Alte Bett-Koordinaten (Kopfende Ost bei x=11.45, y=2.2) -> neue (Kopfende Sued unterm Fenster)."""
    rx, ry = x - 11.45, y - 2.2
    return (round(BED_O[0] + ry, 3), round(BED_O[1] - rx, 3))


@contextmanager
def bed_frame(P):
    with P.frame((BED_O[0], BED_O[1], 0), rz=-math.pi / 2):
        with P.frame((-11.45, -2.2, 0)):
            yield


def schlafzimmer():
    bx0, bx1, by0, by1 = 9.35, 11.45, 1.4, 3.0
    cx, cy = (bx0 + bx1) / 2, (by0 + by1) / 2
    with bed_frame(S):
        for xx in (bx0 + 0.06, bx1 - 0.06):
            for yy in (by0 + 0.06, by1 - 0.06):
                S.box((0.07, 0.07, 0.2), (xx, yy, 0.1), M["wood_mid"])
        S.box((bx1 - bx0, by1 - by0, 0.12), (cx, cy, 0.25), M["wood_mid"], bevel=0.01)
        S.box((0.06, by1 - by0 + 0.04, 0.75), (bx1 - 0.0, cy, 0.4), M["wood_mid"], bevel=0.01)   # Kopfteil (unter Fensterbank)
        S.box((2.0, 1.56, 0.2), (cx - 0.02, cy, 0.41), M["sheets"], bevel=0.04, tile=0.5)          # Matratze
        S.box((0.45, 0.68, 0.14), (11.1, 2.55, 0.57), M["sheets"], bevel=0.06, rot=(0, 0.15, 0.04), tile=0.5)
        S.box((0.45, 0.66, 0.13), (11.12, 1.85, 0.57), M["sheets"], bevel=0.06, rot=(0, 0.2, -0.05), tile=0.5)
        S.box((1.5, 1.62, 0.1), (10.08, cy, 0.54), M["duvet"], bevel=0.045, rot=(0.02, 0, 0.02), tile=0.6)
        S.box((0.08, 1.4, 0.36), (9.33, cy, 0.38), M["duvet"], bevel=0.035, rot=(0, -0.12, 0), tile=0.6)
    solid(9.4, 0.03, 11.0, 2.15, "under")
    # Nachttisch + Lampe am Kopfende (glimmt, Max schlaeft nicht gern im Dunkeln)
    with S.frame((11.23, 0.3, 0)):
        S.box((0.45, 0.42, 0.5), (0, 0, 0.27), M["wood_dark"], bevel=0.01)
        S.box((0.012, 0.36, 0.16), (-0.226, 0, 0.38), M["wood_mid"])
        S.cyl(0.06, 0.02, (0, 0.08, 0.53), M["iron"], segs=12)
        S.cyl(0.008, 0.25, (0, 0.08, 0.66), M["chrome"], segs=6)
        S.cyl(0.13, 0.17, (0, 0.08, 0.84), M["lampshade"], segs=16, r2=0.08)
        S.box((0.07, 0.15, 0.008), (-0.05, -0.1, 0.524), M["black"], rot=(0, 0, 0.4))  # Handy
    light("Nachtlicht", "POINT", (11.23, 0.38, 0.82), (1.0, 0.5, 0.2), 5, size=0.05)
    solid(11.0, 0.09, 11.46, 0.51)
    # Kleiderschrank Westwand
    with S.frame((6.08, 1.7, 0)):
        S.box((0.6, 1.8, 2.15), (0, 0, 1.1), M["wood_mid"], bevel=0.01)
        S.box((0.64, 1.86, 0.06), (0, 0, 2.2), M["wood_mid"], bevel=0.01)
        for yy in (-0.45, 0.45):
            S.box((0.015, 0.86, 1.95), (0.305, yy, 1.08), M["wood_dark"], bevel=0.004)
        for yy in (-0.06, 0.06):
            S.cyl(0.012, 0.03, (0.32, yy, 1.1), M["chrome"], segs=8, rot=(0, math.pi / 2, 0))
    solid(5.78, 0.8, 6.38, 2.6)
    # Waeschehaufen
    for i, (px, py, s_, m) in enumerate(((7.25, 3.5, 0.28, "jeans"), (7.45, 3.65, 0.22, "hoodie"),
                                         (7.1, 3.75, 0.2, "shirt"), (7.35, 3.3, 0.18, "cloth_green"))):
        S.sphere(s_, (px, py, 0.04), M[m], scale=(1.2, 1, 0.35), rot=(0, 0, i))
    stuhl(8.4, 0.6, math.pi / 2 + 0.4)
    S.box((0.4, 0.5, 0.05), (8.38, 0.6, 0.5), M["hoodie"], bevel=0.02, rot=(0, 0, 0.4))
    window("x", 0.0, 6.8, 8.0, 0.85, 2.5, 1)
    window("x", 0.0, 9.6, 10.8, 0.85, 2.5, 1)


def flur_bad_wc():
    # Flur: Garderobe, Schuhe, Fussmatte, Lampe
    for i, xx in enumerate((7.0, 7.3, 7.6, 7.9)):
        S.box((0.03, 0.05, 0.03), (xx, 5.37, 1.7), M["chrome"])
        if i < 3:
            S.box((0.35, 0.12, 0.75), (xx, 5.3, 1.33), M[["jeans", "hoodie", "cloth_green"][i]], bevel=0.04,
                  rot=(0.05, 0, 0.1 * i))
    for i in range(4):
        x = 6.3 + i * 0.32
        for k in (0, 1):
            S.box((0.11, 0.27, 0.09), (x + k * 0.12, 5.25 - 0.02 * k, 0.045), M["black" if i % 2 else "hoodie"],
                  bevel=0.03, rot=(0, 0, 0.1 * (k - .5)))
    S.box((0.5, 0.75, 0.012), (11.1, 4.95, 0.006), M["doormat"], tile=0.6)
    with S.frame((W, 4.55, 0), rz=math.pi / 2):  # Wohnungstuer, geschlossen
        S.box((0.8, 0.05, DOOR_H), (0.4, 0.0, DOOR_H / 2), M["door_entry"], bevel=0.008)
        for z in (0.55, 1.45):
            S.box((0.6, 0.065, 0.62), (0.4, 0.0, z + 0.18), M["door_entry"], bevel=0.012)
        S.sphere(0.03, (0.72, 0.05, 1.05), M["chrome"], segs=(10, 6))
        S.cyl(0.008, 0.02, (0.4, 0.04, 1.6), M["chrome"], segs=8, rot=(math.pi / 2, 0, 0))
    S.cyl(0.004, 0.25, (8.6, 4.95, H - 0.125), M["cord"], segs=6)
    S.sphere(0.04, (8.6, 4.95, H - 0.28), M["bulb"], segs=(10, 8))
    light("Lampe_Flur", "POINT", (8.6, 4.95, H - 0.32), (1.0, 0.78, 0.5), 18, size=0.04)
    # Tuerblaetter (offen)
    door_leaf((5.72 + 0.06, 4.55), 4, 0.8)            # Kueche <-> Flur, liegt im Flur an der Wand
    door_leaf((5.72 + 0.06, 7.85), -10, 0.75)         # Bad, oeffnet ins Bad
    door_leaf((9.75, 5.45 + 0.06), 85, 0.75)          # WC (Anschlag links, Blick aufs Klo frei)
    door_leaf((9.45, 4.45 - 0.06), -80, 0.8)          # Schlafzimmer
    door_leaf((8.40 + 0.06, 7.10), 55, 0.7)           # Abstellkammer, nur angelehnt

    # Bad: Badewanne an der linken (West-)Wand, laeuft leicht ueber (Wasser ist dynamisch)
    L, Wd = TUB_L, TUB_W
    with tub_frame(S):
        th = 0.07
        S.box((L, Wd, 0.08), (0, 0, 0.14), M["ceramic"], bevel=0.03)
        for (sx, sy, cx_, cy_) in ((L, th, 0, -Wd / 2 + th / 2), (L, th, 0, Wd / 2 - th / 2),
                                   (th, Wd, -L / 2 + th / 2, 0), (th, Wd, L / 2 - th / 2, 0)):
            S.box((sx, sy, 0.5), (cx_, cy_, 0.35), M["ceramic"], bevel=0.03)
        for xx in (-L / 2 + 0.12, L / 2 - 0.12):
            for yy in (-Wd / 2 + 0.1, Wd / 2 - 0.1):
                S.sphere(0.05, (xx, yy, 0.06), M["chrome"], scale=(1, 1, 1.2), segs=(10, 6))
        S.tube([(-L / 2 + 0.035, 0, 0.58), (-L / 2 + 0.035, 0, 0.76), (-L / 2 + 0.16, 0, 0.76),
                (-L / 2 + 0.17, 0, 0.71)], 0.013, M["chrome"])
        for yy in (-0.09, 0.09):
            S.sphere(0.022, (-L / 2 + 0.035, yy, 0.66), M["chrome"], segs=(10, 6))
    solid(TUB_C[0] - Wd / 2, TUB_C[1] - L / 2, TUB_C[0] + Wd / 2, TUB_C[1] + L / 2)
    # Waschbecken + Spiegel jetzt an der Ostwand (Waschmaschine ist raus)
    S.cyl(0.08, 0.7, (8.14, 6.6, 0.35), M["ceramic"], segs=14, r2=0.06)
    S.box((0.42, 0.55, 0.16), (8.12, 6.6, 0.8), M["ceramic"], bevel=0.05)
    S.box((0.3, 0.42, 0.03), (8.10, 6.6, 0.875), M["steel"])
    S.tube([(8.29, 6.6, 0.88), (8.29, 6.6, 1.0), (8.17, 6.6, 1.0)], 0.01, M["chrome"])
    S.box((0.02, 0.5, 0.7), (8.33, 6.6, 1.5), M["mirror"])
    S.box((0.015, 0.56, 0.76), (8.335, 6.6, 1.5), M["trim"])
    solid(7.9, 6.32, 8.34, 6.88)
    S.cyl(0.004, 0.2, (7.0, 6.8, H - 0.1), M["cord"], segs=6)
    S.sphere(0.04, (7.0, 6.8, H - 0.22), M["bulb"], segs=(10, 8))
    light("Lampe_Bad", "POINT", (7.0, 6.8, H - 0.26), (1.0, 0.85, 0.7), 15, size=0.04)
    window("x", D, 6.9, 7.6, 1.4, 2.3, -1)

    # Toilette (Ostwand): verdreckte Schuessel
    with S.frame((11.2, 6.08, 0)):
        S.cyl(0.13, 0.36, (0, 0, 0.18), M["klo_side"], cap=M["klo_top"], segs=20, r2=0.17)
        S.tube([(0.19 * math.cos(a) - 0.02, 0.17 * math.sin(a), 0.375) for a in np.linspace(0, 2 * math.pi, 21)[:-1]],
               0.022, M["klobrille"], segs=8, closed=True)
        S.box((0.03, 0.36, 0.42), (0.17, 0, 0.6), M["klobrille"], bevel=0.02, rot=(0, -0.15, 0))  # Deckel offen
        S.box((0.18, 0.42, 0.38), (0.18, 0, 0.75), M["ceramic"], bevel=0.03)
        S.cyl(0.05, 0.1, (0.0, 0.42, 0.75), M["paper"], segs=12, rot=(math.pi / 2, 0, 0))
    solid(10.95, 5.85, 11.45, 6.3)
    # gelber Fleck vor dem Klo + Klobuerste
    rnd = random.Random(5)
    blob = [(10.86 + 0.16 * (1 + rnd.uniform(-.25, .25)) * math.cos(a), 6.08 + 0.2 * (1 + rnd.uniform(-.25, .25)) * math.sin(a))
            for a in np.linspace(0, 2 * math.pi, 15)[:-1]]
    S.poly_prism(blob, 0.0041, 0.0046, M["urin"])
    S.cyl(0.05, 0.13, (11.33, 5.68, 0.069), M["klo_side"], segs=12)
    S.cyl(0.009, 0.32, (11.33, 5.68, 0.27), M["plastic_black"], segs=6, rot=(0.12, 0, 0))
    S.box((0.02, 0.06, 0.06), (8.47, 6.1, 0.35), M["fairy"], bevel=0.01)  # Steckdosen-Nachtlicht
    light("WC_Nachtlicht", "POINT", (8.55, 6.1, 0.35), (1.0, 0.75, 0.45), 2, size=0.03)
    # schmutzige Socken auf dem Boden
    socke(8.9, 5.82, 0.4)
    socke(9.25, 6.32, 2.3)
    socke(9.9, 6.05, 4.1)
    socke(10.75, 5.65, 1.2)


TUB_L, TUB_W = 1.49, 0.75
TUB_C = (5.78 + TUB_W / 2, 5.51 + TUB_L / 2)  # an der Westwand, Laengsseite entlang y


@contextmanager
def tub_frame(P):
    """Lokale Wannen-Koordinaten: x = Laengsachse (-x = Sued, Hahn), -y = Raumseite (Ost)."""
    with P.frame((TUB_C[0], TUB_C[1], 0), rz=math.pi / 2):
        yield


def socke(x, y, rz):
    with S.frame((x, y, 0), rz=rz, scale=(1, 1, 0.38)):
        S.tube([(0, 0, 0.036), (0.1, 0.006, 0.036), (0.19, 0.0, 0.036)], [0.036, 0.034, 0.032], M["socke"], segs=8)
        S.tube([(0.19, 0.0, 0.036), (0.23, 0.02, 0.036), (0.25, 0.07, 0.036), (0.26, 0.17, 0.036)],
               [0.032, 0.033, 0.032, 0.025], M["socke_dreck"], segs=8)



def abstellkammer():
    """Zugemuellte Abstellkammer = Zufluchtsort der Ratte (Nest ganz hinten, Ostseite)."""
    rnd = random.Random(3)
    # Kartons entlang Nordwand (y 7.6..8.0) und Suedwand (y 6.76..7.05), Weg in der Mitte frei
    def kiste(x, y, sx, sy, sz, z=0.0, rz=0.0):
        S.box((sx, sy, sz), (x, y, z + sz / 2), M["cardboard"], bevel=0.008, rot=(0, 0, rz), tile=0.5)

    x = 8.65
    while x < 10.6:
        sx = rnd.uniform(0.35, 0.55)
        h1 = rnd.uniform(0.3, 0.5)
        kiste(x + sx / 2, 7.8, sx, 0.4, h1, rz=rnd.uniform(-.08, .08))
        if rnd.random() < .7:
            h2 = rnd.uniform(0.25, 0.4)
            kiste(x + sx / 2 + rnd.uniform(-.04, .04), 7.8, sx * .9, 0.36, h2, z=h1, rz=rnd.uniform(-.15, .15))
            if rnd.random() < .5:
                kiste(x + sx / 2, 7.82, sx * .8, 0.3, rnd.uniform(.2, .3), z=h1 + h2, rz=rnd.uniform(-.2, .2))
        x += sx + 0.03
    solid(8.6, 7.58, 10.6, 8.03)
    x = 8.95
    while x < 10.3:
        sx = rnd.uniform(0.3, 0.5)
        h1 = rnd.uniform(0.25, 0.45)
        kiste(x + sx / 2, 6.95, sx, 0.34, h1, rz=rnd.uniform(-.1, .1))
        if rnd.random() < .5:
            kiste(x + sx / 2, 6.95, sx * .85, 0.3, rnd.uniform(.2, .35), z=h1, rz=rnd.uniform(-.2, .2))
        x += sx + 0.04
    solid(8.95, 6.76, 10.3, 7.13)
    # Alter Koffer, Staubsauger, Besen, Muellsaecke, Zeitungsstapel
    S.box((0.7, 0.22, 0.5), (10.95, 6.9, 0.25), M["suitcase"], bevel=0.04, rot=(0, 0, 0.05))
    S.tube([(10.8, 6.9, 0.5), (10.8, 6.9, 0.56), (11.1, 6.9, 0.56), (11.1, 6.9, 0.5)], 0.012, M["black"])
    solid(10.6, 6.76, 11.3, 7.02)
    S.cyl(0.14, 0.3, (8.7, 6.95, 0.15), M["red"], segs=16)
    S.tube([(8.7, 6.95, 0.3), (8.75, 7.0, 0.5), (8.65, 7.1, 0.8), (8.6, 7.0, 1.1)], 0.025, M["black"], segs=8)
    S.cyl(0.012, 1.3, (8.53, 7.95, 0.62), M["wood_light"], segs=6, rot=(0.15, 0, 0))
    S.box((0.25, 0.05, 0.1), (8.53, 7.86, 0.04), M["red"], bevel=0.01)
    for (px, py, s) in ((10.75, 7.75, 0.28), (10.95, 7.85, 0.24), (11.25, 7.82, 0.26), (11.3, 7.1, 0.22)):
        S.sphere(s, (px, py, s * 0.8), M["plastic_black"], scale=(1, 0.85, 0.8), segs=(12, 8))
    for k in range(9):
        S.box((0.4, 0.3, 0.05), (9.25, 7.35 + 0.02 * (k % 2), 0.025 + k * 0.05), M["paper"],
              rot=(0, 0, rnd.uniform(-0.2, 0.2)))
    # Lichterkette (vergessen, glimmt noch)
    pts = [(8.5 + i * 0.3, 7.98, 2.0 - 0.15 * math.sin(i * 1.3)) for i in range(10)]
    S.tube(pts, 0.003, M["cord"], segs=4)
    for p in pts:
        S.sphere(0.012, p, M["fairy"], segs=(6, 4))
    light("Lichterkette", "POINT", (9.8, 7.8, 1.9), (1.0, 0.7, 0.35), 12, size=0.6)
    # ---- Rattennest ganz hinten (Ost), zwischen Kisten versteckt
    nx, ny = 11.15, 7.42
    for k in range(70):
        a = rnd.uniform(0, 2 * math.pi)
        rr = rnd.uniform(0.09, 0.2)
        S.box((rnd.uniform(.04, .1), rnd.uniform(.01, .025), 0.004),
              (nx + rr * math.cos(a), ny + rr * math.sin(a), 0.01 + rnd.uniform(0, .05)),
              M["nest"] if k % 3 else M["nest2"], rot=(rnd.uniform(-.5, .5), rnd.uniform(-.5, .5), rnd.uniform(0, 6)))
    S.sphere(0.2, (nx, ny, 0.0), M["nest2"], scale=(1, 1, 0.18), segs=(14, 6))
    LAYOUT["hideout"] = {"pos": [nx, ny], "radius": 0.35}
    light("Nest_Glimmen", "POINT", (nx - 0.3, ny, 0.25), (1.0, 0.7, 0.4), 2.5, size=0.1)


# --------------------------------------------------------------------------- Dynamische Objekte
ITEM_COUNTER = {}


def item_name(prefix):
    ITEM_COUNTER[prefix] = ITEM_COUNTER.get(prefix, 0) + 1
    return f"{prefix}_{ITEM_COUNTER[prefix]:02d}"


def register(ob, role, **extra):
    ob["role"] = role
    for k, v in extra.items():
        ob[k] = v
    LAYOUT["items"].append({"name": ob.name, "role": role, "pos": [round(ob.location.x, 3), round(ob.location.y, 3),
                                                                     round(ob.location.z, 3)], **extra})
    ob.visible_shadow = False  # wirft keinen gebackenen Schatten (kann ja weg)


def dose(x, y, z=0.0, lying=False, rz=0.0, crushed=False):
    P = Part(item_name("Dose"))
    h = 0.135 * (0.7 if crushed else 1.0)
    rot = (math.pi / 2, 0, 0) if lying else (0, 0, 0)
    c = (0, 0, 0.0265) if lying else (0, 0, h / 2)
    P.cyl(0.0265, h - 0.012, c, M["can"], cap=M["alu"], segs=16, rot=rot)
    off = Vector((0, -(h / 2 - 0.006), 0)) if lying else Vector((0, 0, h / 2 - 0.006))
    for sgn in (1, -1):
        cc = Vector(c) + off * sgn
        P.cyl(0.023, 0.012, tuple(cc), M["alu"], segs=16, rot=rot, r2=0.0265 if sgn < 0 else 0.022, uv="box")
    ob = P.finish(C_DYN, (x, y, z), rz)
    register(ob, "noise", sound="dose", loudness=0.8, lying=lying, crushed=crushed)


def mate(x, y, z=0.0, lying=False, rz=0.0):
    P = Part(item_name("Mate"))
    with P.frame((0, 0, 0.034) if lying else (0, 0, 0), rx=math.pi / 2 if lying else 0):
        zb = -0.12 if lying else 0
        P.cyl(0.034, 0.17, (0, 0, zb + 0.085), M["mate_glass"], segs=14, uv="box")
        P.cyl(0.0345, 0.09, (0, 0, zb + 0.095), M["mate_label"], segs=14)
        P.cyl(0.034, 0.05, (0, 0, zb + 0.195), M["mate_glass"], segs=14, r2=0.014, uv="box")
        P.cyl(0.014, 0.035, (0, 0, zb + 0.237), M["mate_glass"], segs=10, uv="box")
        P.cyl(0.016, 0.008, (0, 0, zb + 0.257), M["cap"], segs=10, uv="box")
    ob = P.finish(C_DYN, (x, y, z), rz)
    register(ob, "noise", sound="flasche", loudness=1.0, lying=lying)


def quark(x, y, z=0.0, rz=0.0, open_=False):
    P = Part(item_name("Magerquark"))
    P.cyl(0.05, 0.075, (0, 0, 0.0375), M["quark_side"], cap=M["quark_in"] if open_ else M["quark_lid"], segs=18,
          r2=0.058)
    if not open_:
        P.cyl(0.061, 0.006, (0, 0, 0.078), M["quark_lid"], segs=18, uv="cyl")
    else:
        P.box((0.012, 0.14, 0.004), (0.02, 0.03, 0.09), M["steel"], rot=(0.3, 0, 0.4))  # Loeffel
    ob = P.finish(C_DYN, (x, y, z), rz)
    register(ob, "food", food="magerquark", value=2 if not open_ else 1)


def pizza_slice(P, a0, a1, r, z, bite=False):
    seg = 6
    pts = [(0, 0)] + [(r * math.cos(a0 + (a1 - a0) * i / seg), r * math.sin(a0 + (a1 - a0) * i / seg))
                      for i in range(seg + 1)]
    if bite:
        pts = pts[:2] + pts[4:]
    P.poly_prism(pts, z, z + 0.012, M["pizza"], side_mat=M["crust"])
    arc = [(r * math.cos(a0 + (a1 - a0) * i / seg), r * math.sin(a0 + (a1 - a0) * i / seg), z + 0.014)
           for i in range(seg + 1)]
    P.tube(arc, 0.012, M["crust"], segs=6)


def fix_pizza_uv(ob, r):
    me = ob.data
    uvl = me.uv_layers["UVMap"]
    for poly in me.polygons:
        if poly.material_index < len(me.materials) and me.materials[poly.material_index] == M["pizza"]:
            for li in poly.loop_indices:
                co = me.vertices[me.loops[li].vertex_index].co
                uvl.data[li].uv = (co.x / (2 * r) + .5, co.y / (2 * r) + .5)


def pizzakarton(x, y, z=0.0, rz=0.0, slices=2):
    P = Part(item_name("Pizza"))
    s = 0.33
    P.box((s, s, 0.008), (0, 0, 0.004), M["pizzabox"])
    for sx, sy, w, d in ((0, s / 2, s, 0.006), (0, -s / 2, s, 0.006), (s / 2, 0, 0.006, s), (-s / 2, 0, 0.006, s)):
        P.box((w, d, 0.035), (sx, sy, 0.0175), M["pizzabox"])
    with P.frame((0, s / 2, 0.035), rx=-math.radians(105)):
        P.box((s, s, 0.006), (0, -s / 2, 0), M["pizzabox"])
        P.plane(s - 0.01, s - 0.01, (0, -s / 2, -0.0035), M["pizzabox_lid"], rot=(math.pi, 0, 0))
    r = 0.15
    for k in range(slices):
        a0 = math.radians(20 + k * 47)
        pizza_slice(P, a0, a0 + math.radians(45), r, 0.009, bite=(k == slices - 1))
    for k in range(2):  # abgenagte Raender
        a = 3.6 + k * 0.9
        P.tube([(0.1 * math.cos(a + t * 0.6), 0.1 * math.sin(a + t * 0.6), 0.02) for t in np.linspace(0, 1, 5)],
               0.011, M["crust"], segs=6)
    ob = P.finish(C_DYN, (x, y, z), rz)
    fix_pizza_uv(ob, r)
    register(ob, "food", food="vegane_pizza", value=3)


def kruste(x, y, z=0.0, rz=0.0):
    P = Part(item_name("Pizzarand"))
    P.tube([(0.07 * math.cos(t), 0.07 * math.sin(t), 0.011) for t in np.linspace(0, 0.9, 6)], 0.011, M["crust"],
           segs=6)
    ob = P.finish(C_DYN, (x, y, z), rz)
    register(ob, "food", food="pizzarand", value=1)


def hantel(x, y, rz=0.0, kg=10):
    P = Part(item_name("Hantel"))
    P.cyl(0.015, 0.4, (0, 0, 0.1), M["chrome"], segs=10, rot=(0, math.pi / 2, 0))
    P.cyl(0.02, 0.12, (0, 0, 0.1), M["rubber"], segs=10, rot=(0, math.pi / 2, 0))
    r = 0.07 + kg * 0.003
    for sx in (-0.15, -0.11, 0.11, 0.15):
        P.cyl(r, 0.035, (sx, 0, 0.1), M["iron"], segs=20, rot=(0, math.pi / 2, 0), uv="box")
    ob = P.finish(C_DYN, (x, y, 0), rz)
    register(ob, "obstacle", kind="hantel", kg=kg)
    LAYOUT["solids"].append({"r": [x - 0.2, y - 0.2, x + 0.2, y + 0.2], "kind": "hantel"})


def peitsche(x, y, rz=0.0):
    P = Part("Peitsche")
    P.cyl(0.016, 0.28, (0, 0, 0.016), M["leather"], segs=10, rot=(0, math.pi / 2, 0), uv="box")
    P.cyl(0.022, 0.03, (-0.15, 0, 0.016), M["chrome"], segs=10, rot=(0, math.pi / 2, 0), uv="box")
    pts, radii = [], []
    n = 40
    for i in range(n):
        t = i / (n - 1)
        a = t * 5.2
        pts.append((0.14 + t * 0.55 + 0.18 * math.sin(a) * t, 0.25 * math.sin(a * 0.8) + 0.15 * t * math.cos(a * 1.7),
                    0.012 * (1 - t) + 0.003))
        radii.append(0.011 * (1 - t) + 0.002)
    P.tube(pts, radii, M["leather"], segs=6)
    ob = P.finish(C_DYN, (x, y, 0), rz)
    register(ob, "obstacle", kind="peitsche")


def max_im_bett():
    """Max schlaeft. Kopf + Arm, Koerper-Wulst atmet (eigenes Objekt). Alte Koordinaten im Bett-Frame."""
    P = Part("Max")
    hx, hy, hz = 11.02, 2.52, 0.72
    with bed_frame(P):
        P.sphere(0.105, (hx, hy, hz), M["skin"], scale=(1.0, 0.92, 1.12), rot=(0.5, 0, 0))
        P.sphere(0.108, (hx + 0.03, hy + 0.01, hz + 0.035), M["hair"], scale=(0.95, 0.9, 1.0), rot=(0.5, 0, 0))
        P.sphere(0.07, (hx - 0.05, hy - 0.05, hz - 0.06), M["stubble"], scale=(0.9, 0.9, 0.75))
        P.sphere(0.02, (hx - 0.1, hy - 0.04, hz - 0.01), M["skin"], scale=(1.3, 0.8, 1))  # Nase
        P.sphere(0.025, (hx + 0.01, hy - 0.08, hz + 0.0), M["skin"], scale=(0.6, 0.4, 1))  # Ohr
        for dy in (-0.03, 0.035):  # geschlossene Augen
            P.box((0.004, 0.025, 0.004), (hx - 0.098, hy + dy - 0.02, hz + 0.02), M["hair"])
        P.sphere(0.13, (10.78, 2.5, 0.62), M["shirt"], scale=(1.1, 1.3, 0.75))
        P.tube([(10.72, 2.3, 0.64), (10.5, 2.12, 0.64), (10.28, 2.05, 0.635)], [0.042, 0.038, 0.032], M["skin"],
               segs=10)
        P.sphere(0.04, (10.22, 2.04, 0.63), M["skin"], scale=(1.4, 1.0, 0.6))
    ob = P.finish(C_DYN, (0, 0, 0))
    ob["role"] = "max"
    B = Part("Max_Koerper")
    B.sphere(0.5, (0, 0, 0), M["duvet"], scale=(1.5, 0.52, 0.22), segs=(20, 10), tile=0.6)
    B.sphere(0.22, (-0.5, 0.0, -0.01), M["duvet"], scale=(1.3, 0.75, 0.42), segs=(14, 8), tile=0.6)
    bx, by = bed_xf(10.05, 2.52)
    body = B.finish(C_DYN, (bx, by, 0.57), rz=-math.pi / 2)
    body["role"] = "max_body"
    LAYOUT["max"] = {"head": [*bed_xf(hx, hy), hz], "body": [bx, by, 0.57]}


def badewasser():
    """Wanne randvoll, Hahn laeuft, Wasser schwappt ueber den Rand und bildet eine Pfuetze."""
    L, Wd = TUB_L, TUB_W
    P = Part("Badewasser")
    with tub_frame(P):
        P.box((L - 0.13, Wd - 0.13, 0.012), (0, 0, 0.594), M["water"])
        P.box((0.55, 0.075, 0.005), (0.12, -Wd / 2 + 0.036, 0.6035), M["water"])          # ueber den Rand
        for (px, w, h) in ((0.32, 0.05, 0.57), (0.16, 0.09, 0.57), (0.0, 0.035, 0.4), (-0.1, 0.06, 0.57)):
            P.box((w, 0.005, h), (px, -Wd / 2 - 0.004, 0.6 - h / 2), M["water"], bevel=0.002)  # Rinnsale aussen
        P.cyl(0.006, 0.12, (-L / 2 + 0.17, 0, 0.65), M["water"], segs=8, uv="box")        # Strahl aus dem Hahn
    ob = P.finish(C_DYN)
    register(ob, "water", kind="badewanne")
    rnd = random.Random(11)
    cx, cy = TUB_C[0] + Wd / 2 + 0.3, TUB_C[1] + 0.1
    pts = []
    for k in range(32):
        a = 2 * math.pi * k / 32
        r = 1 + 0.16 * math.sin(3 * a + 1) + 0.1 * math.sin(5 * a + 2) + rnd.uniform(-0.05, 0.05)
        pts.append((round(max(cx + 0.5 * r * math.cos(a), TUB_C[0] + Wd / 2 + 0.004), 3),
                    round(cy + 0.72 * r * math.sin(a), 3)))
    Q = Part("Pfuetze")
    Q.poly_prism(pts, 0.0042, 0.0062, M["water"])
    ob = Q.finish(C_DYN)
    register(ob, "water", kind="pfuetze")
    LAYOUT["water"] = [pts]


def streuen():
    """Alles, was so rumliegt."""
    # Wohnzimmer: Couchtisch (x 2.65) + Boden
    pizzakarton(2.65, 2.25, 0.4175, rz=0.3, slices=2)
    mate(2.75, 2.75, 0.4175)
    mate(2.55, 2.8, 0.4175)
    dose(2.5, 2.0, 0.4175)
    mate(3.2, 1.5, lying=True, rz=1.2)
    dose(3.55, 3.0, lying=True, rz=0.4)
    dose(3.4, 1.15)
    dose(2.3, 3.6, lying=True, rz=2.2)
    dose(4.3, 3.7, crushed=True)
    quark(2.25, 1.05, rz=0.5, open_=True)
    quark(3.9, 2.6, rz=0.2, open_=True)
    kruste(3.15, 3.5, rz=1.0)
    kruste(2.2, 1.85, rz=2.5)
    pizzakarton(3.7, 0.8, rz=-0.6, slices=1)
    hantel(4.7, 0.85, rz=0.4, kg=12)
    hantel(4.95, 1.25, rz=0.1, kg=12)
    # Kueche
    pizzakarton(2.2, 6.05, 0.76, rz=-0.2, slices=3)
    quark(2.7, 6.3, 0.76)
    quark(2.55, 5.95, 0.76, open_=True)
    quark(5.3, 6.2, 0.9)
    quark(5.4, 6.87, 0.9)
    dose(5.45, 6.07, 0.9)
    quark(3.45, 7.55, rz=0.8, open_=True)
    mate(3.75, 7.8)
    mate(2.1, 7.05, lying=True, rz=0.3)
    mate(3.3, 5.3, lying=True, rz=2.0)
    dose(3.6, 6.5, lying=True, rz=1.1)
    dose(2.4, 7.45)
    dose(4.4, 5.6, crushed=True)
    kruste(3.2, 5.9, rz=0.4)
    quark(2.0, 7.15, rz=1.2, open_=True)
    # Schlafzimmer
    dose(11.33, 0.2, 0.52)
    dose(10.7, 3.4, lying=True, rz=0.6)
    dose(9.1, 3.2, lying=True, rz=1.9)
    dose(8.0, 2.6)
    dose(8.9, 0.45, crushed=True)
    mate(9.15, 1.2)
    mate(7.6, 1.6, lying=True, rz=0.8)
    quark(10.6, 2.55, rz=0.4, open_=True)
    quark(7.0, 2.9)
    kruste(8.6, 3.5, rz=2.0)
    pizzakarton(7.9, 3.75, rz=0.9, slices=1)
    hantel(6.75, 0.5, rz=1.4, kg=8)
    hantel(7.05, 0.35, rz=1.2, kg=8)
    peitsche(8.45, 1.9, rz=0.5)
    # Flur / Bad / WC
    dose(7.3, 4.85, lying=True, rz=0.2)
    mate(10.9, 5.2)
    dose(6.45, 7.3, lying=True, rz=1.4)
    quark(7.6, 7.65, rz=0.6, open_=True)
    dose(10.6, 6.45, crushed=True)
    badewasser()


def mauseloch():
    """Loch in der Schlafzimmer-Aussenwand (Ost) = Ausgang in Level 3."""
    hy, r = 3.7, 0.075
    with S.frame((W - 0.023, hy, 0), ry=-math.pi / 2):
        pts = [(r * math.sin(a), r * math.cos(a)) for a in np.linspace(math.pi, 0, 15)]
        S.poly_prism(pts, 0.0, 0.004, M["hole"])
        rnd = random.Random(9)
        for a in np.linspace(0.15, math.pi - 0.15, 9):  # abgebroeckelter Rand
            rr = r + rnd.uniform(0.004, 0.014)
            S.box((rnd.uniform(.008, .016), rnd.uniform(.008, .02), 0.006), (rr * math.sin(a), rr * math.cos(a), 0.002),
                  M["trim"], rot=(0, 0, rnd.uniform(0, 3)))
    rnd = random.Random(19)
    for _ in range(14):  # Putzkruemel auf dem Boden
        S.box((rnd.uniform(.006, .016), rnd.uniform(.006, .014), rnd.uniform(.003, .008)),
              (W - rnd.uniform(0.03, 0.16), hy + rnd.uniform(-0.12, 0.12), 0.003), M["trim"],
              rot=(rnd.uniform(-.4, .4), rnd.uniform(-.4, .4), rnd.uniform(0, 3)))
    LAYOUT["exit"] = {"pos": [round(W - 0.05, 3), hy], "r": 0.13}


def lose_figuren():
    """Level 2: sechs Figuren, die irgendwie von der Palette in die Wohnung gewandert sind."""
    rnd = random.Random(23)
    spots = [(1.6, 2.2, 0.0), (10.2, 2.45, 0.0), (7.2, 6.3, 0.004), (2.3, 6.1, 0.0), (6.95, 4.75, 0.0),
             (10.55, 6.25, 0.004)]
    looks = [(M["mini_blue"], M["mini_gold"], M["mini_blue"], False), (M["mini_rust"], M["mini_rust"], M["mini_ork"], True)]
    for k, (x, y, z) in enumerate(spots):
        P = Part(f"Figur_{k + 1:02d}")
        body, trim, head, ork = looks[k % 2]
        mini_on(P, 0, 0, 0, 0, body, trim, head, ork)
        ob = P.finish(C_DYN, (x, y, z), rz=rnd.uniform(0, 6.28))
        register(ob, "mini", ork=ork)


def _empty(name, loc, rz=0.0, parent=None):
    ob = bpy.data.objects.new(name, None)
    C_CHAR.objects.link(ob)
    ob.location = loc
    ob.rotation_euler = (0, 0, rz)
    if parent:
        ob.parent = parent
    return ob


def _limb(name, parent, loc, build):
    """Ein Koerperteil: Ursprung = Gelenk, lokal relativ zum Eltern-Gelenk."""
    P = Part(name)
    build(P)
    ob = P.finish(C_CHAR, loc)
    ob.parent = parent
    return ob


def ratte():
    """Die Heldin. Blickrichtung lokal +x, Ursprung am Boden unter dem Bauch."""
    fur = M["rat_fur"]
    root = _empty("Ratte", (10.95, 7.42, 0), rz=math.pi)

    def body(P):
        P.sphere(1.0, (0, 0, 0.045), fur, scale=(0.078, 0.042, 0.038), segs=(16, 10))
        P.sphere(1.0, (0.01, 0, 0.033), M["rat_belly"], scale=(0.06, 0.034, 0.024), segs=(12, 8))
    _limb("Ratte_Koerper", root, (0, 0, 0), body)

    def head(P):
        P.sphere(1.0, (0.025, 0, 0), fur, scale=(0.04, 0.028, 0.026), segs=(14, 8))
        P.cyl(0.02, 0.05, (0.065, 0, -0.004), fur, segs=10, r2=0.006, rot=(0, math.pi / 2, 0), uv="box")
        P.sphere(0.0065, (0.091, 0, -0.004), M["rat_pink"], segs=(8, 6))
        for sy in (-1, 1):
            P.sphere(0.0055, (0.04, sy * 0.019, 0.011), M["black"], segs=(8, 6))
            P.sphere(1.0, (0.012, sy * 0.022, 0.026), M["rat_pink"], scale=(0.005, 0.014, 0.016), segs=(10, 6))
            for k in range(3):
                P.tube([(0.08, sy * 0.008, -0.002), (0.094, sy * (0.036 + 0.004 * k), 0.003 - 0.004 * k)], 0.0007,
                       M["whisker"], segs=3)
    _limb("Ratte_Kopf", root, (0.07, 0, 0.055), head)

    for nm, lx, ly in (("VL", 0.045, 0.025), ("VR", 0.045, -0.025), ("HL", -0.04, 0.03), ("HR", -0.04, -0.03)):
        def leg(P):
            P.cyl(0.0075, 0.03, (0, 0, -0.015), fur, segs=6, uv="box")
            P.sphere(1.0, (0.008, 0, -0.031), M["rat_pink"], scale=(0.012, 0.007, 0.004), segs=(8, 5))
        _limb(f"Ratte_Bein_{nm}", root, (lx, ly, 0.035), leg)

    parent, loc, seg, n = root, (-0.072, 0, 0.04), 0.03, 7
    for i in range(n):
        r0 = 0.0075 * (1 - i / n) + 0.0015
        r1 = 0.0075 * (1 - (i + 1) / n) + 0.0015

        def tail(P, r0=r0, r1=r1):
            P.cyl(r0, seg, (-seg / 2, 0, 0), M["rat_tail"], segs=6, r2=r1, rot=(0, -math.pi / 2, 0), uv="box")
        parent = _limb(f"Ratte_Schwanz_{i + 1}", parent, loc, tail)
        loc = (-seg, 0, -0.005 if i < 2 else 0.0)
    return root


def max_wach():
    """Level 3: Max ist wach. Nur pinke Strumpfhose, Eimer in den Haenden. Blick lokal +x."""
    pink, skin = M["tights"], M["skin"]
    root = _empty("MaxWach", (8.4, 2.9, 0), rz=math.pi)
    _limb("MaxW_Huefte", root, (0, 0, 0.93), lambda P: P.sphere(1.0, (0, 0, 0), pink, scale=(0.13, 0.19, 0.12), segs=(16, 10)))
    for side, sy in (("L", 1), ("R", -1)):
        def thigh(P):
            P.cyl(0.06, 0.47, (0, 0, -0.235), pink, segs=12, r2=0.085)

        def shin(P):
            P.sphere(0.062, (0, 0, 0), pink, segs=(10, 6))
            P.cyl(0.04, 0.42, (0, 0, -0.21), pink, segs=12, r2=0.058)
            P.box((0.24, 0.09, 0.07), (0.06, 0, -0.415), pink, bevel=0.03)
        th = _limb(f"MaxW_OS_{side}", root, (0, sy * 0.1, 0.92), thigh)
        _limb(f"MaxW_US_{side}", th, (0, 0, -0.47), shin)

    def torso(P):
        with P.frame(scale=(0.62, 1, 1)):
            P.cyl(0.16, 0.5, (0, 0, 0.25), skin, segs=16, r2=0.2)
            P.cyl(0.168, 0.05, (0, 0, 0.0), pink, segs=16)                  # Bund der Strumpfhose
        P.sphere(0.2, (0, 0, 0.5), skin, scale=(0.62, 1, 0.45))
        P.sphere(0.14, (0.05, 0, 0.13), skin, scale=(0.95, 1.05, 0.8))       # Bauch
        P.sphere(0.08, (0.112, 0, 0.36), M["stubble"], scale=(0.1, 0.65, 0.55))  # Brusthaar
        P.cyl(0.055, 0.14, (0, 0, 0.6), skin, segs=10)
    tor = _limb("MaxW_Torso", root, (0, 0, 0.95), torso)

    def head(P):
        P.sphere(0.105, (0.01, 0, 0.11), skin, scale=(1.0, 0.92, 1.12))
        P.sphere(0.108, (-0.025, 0, 0.15), M["hair"], scale=(0.95, 0.96, 0.95))
        P.sphere(0.07, (0.05, 0, 0.04), M["stubble"], scale=(0.85, 1.1, 0.75))
        P.sphere(0.02, (0.115, 0, 0.1), skin, scale=(1.3, 0.8, 1))
        for sy in (-1, 1):
            P.sphere(0.017, (0.093, sy * 0.038, 0.13), M["eye_white"], segs=(8, 6))
            P.sphere(0.008, (0.108, sy * 0.038, 0.13), M["black"], segs=(6, 4))
            P.box((0.01, 0.045, 0.009), (0.103, sy * 0.04, 0.157), M["hair"], rot=(sy * 0.4, 0, 0))  # boese Brauen
            P.sphere(0.022, (0.0, sy * 0.1, 0.11), skin, scale=(0.5, 0.4, 1))
    _limb("MaxW_Kopf", tor, (0, 0, 0.64), head)
    for side, sy in (("L", 1), ("R", -1)):
        def upper(P):
            P.sphere(0.06, (0, 0, 0), skin, segs=(10, 6))
            P.cyl(0.045, 0.3, (0, 0, -0.15), skin, segs=10, r2=0.055)

        def lower(P):
            P.cyl(0.035, 0.27, (0, 0, -0.135), skin, segs=10, r2=0.043)
            P.sphere(0.045, (0, 0, -0.29), skin, scale=(1.1, 0.7, 1.2), segs=(10, 6))
        ua = _limb(f"MaxW_OA_{side}", tor, (0, sy * 0.22, 0.48), upper)
        _limb(f"MaxW_UA_{side}", ua, (0, 0, -0.3), lower)

    def bucket(P):
        # Eimer kopfueber: Oeffnung unten (z=0), Boden oben
        P.cyl(0.13, 0.28, (0, 0, 0.14), M["bucket"], segs=20, r2=0.105)
        P.cyl(0.124, 0.004, (0, 0, 0.004), M["bucket_in"], segs=20)
        P.tube([(0, 0.13 * math.cos(a), 0.03 - 0.13 * math.sin(a)) for a in np.linspace(0, math.pi, 9)], 0.006,
               M["iron"], segs=5)
    _limb("MaxW_Eimer", root, (0.4, 0, 1.0), bucket)
    return root


# --------------------------------------------------------------------------- Bau
t0 = time.time()
build_shell()
wohnzimmer()
kueche()
schlafzimmer()
flur_bad_wc()
abstellkammer()
mauseloch()
apartment = S.finish(C_STATIC)
ceiling = CEIL.finish(C_STATIC)
apartment["role"] = "static"
ceiling["role"] = "ceiling"
streuen()
lose_figuren()
max_im_bett()
ratte()
max_wach()
print(f"[build] Geometrie fertig in {time.time() - t0:.1f}s")

# Weiche Normalen, Kanten > 35 Grad bleiben hart
for ob in [o for o in list(C_STATIC.objects) + list(C_DYN.objects) + list(C_CHAR.objects) if o.type == "MESH"]:
    me = ob.data
    bm = bmesh.new()
    bm.from_mesh(me)
    for f in bm.faces:
        f.smooth = True
    for e in bm.edges:
        e.smooth = len(e.link_faces) == 2 and e.calc_face_angle(0) < math.radians(35)
    bm.to_mesh(me)
    bm.free()

tri = sum(sum(len(p.vertices) - 2 for p in ob.data.polygons) for ob in list(C_STATIC.objects) + list(C_DYN.objects))
print(f"[build] Objekte: static={len(C_STATIC.objects)} dynamic={len(C_DYN.objects)} Dreiecke~{tri}")

# --------------------------------------------------------------------------- Welt / Render-Settings
world = bpy.data.worlds.new("Nacht")
scene.world = world
try:
    world.use_nodes = True
except Exception:
    pass
bg = world.node_tree.nodes.get("Background")
bg.inputs["Color"].default_value = (0.004, 0.006, 0.014, 1)
bg.inputs["Strength"].default_value = 1.0

scene.render.engine = "CYCLES"
try:
    prefs = bpy.context.preferences.addons["cycles"].preferences
    prefs.compute_device_type = "METAL"
    prefs.get_devices()
    for d in prefs.devices:
        d.use = d.type != "CPU"  # nur GPU, Hybrid mit CPU bremst den Bake
    scene.cycles.device = "GPU"
    print("[build] Cycles GPU:", [d.name for d in prefs.devices if d.use])
except Exception as e:
    print("[build] GPU nicht verfuegbar:", e)
scene.view_settings.view_transform = "AgX"
scene.view_settings.look = "None"
scene.view_settings.exposure = 0.0
scene.cycles.max_bounces = 6
scene.cycles.diffuse_bounces = 4
scene.cycles.glossy_bounces = 2
scene.cycles.transmission_bounces = 2
scene.cycles.sample_clamp_indirect = 8.0

# --------------------------------------------------------------------------- Lightmap
bake_objs = list(C_STATIC.objects) + list(C_DYN.objects)
for ob in bake_objs:
    lm = ob.data.uv_layers.new(name="lightmap")
    ob.data.uv_layers.active = lm
    for uv in ob.data.uv_layers:
        uv.active_render = uv.name == "UVMap"

bpy.ops.object.select_all(action="DESELECT")
for ob in bake_objs:
    ob.select_set(True)
bpy.context.view_layer.objects.active = apartment
bpy.ops.object.mode_set(mode="EDIT")
bpy.ops.mesh.select_all(action="SELECT")
bpy.ops.uv.smart_project(angle_limit=math.radians(60), island_margin=0.003, area_weight=0.0,
                         correct_aspect=True, scale_to_bounds=False)
# smart_project packt pro Objekt -> Inseln auf gleiche Texeldichte bringen und gemeinsam packen
scene.tool_settings.use_uv_select_sync = True
bpy.ops.uv.average_islands_scale()
bpy.ops.uv.pack_islands(rotate=True, margin_method="FRACTION", margin=0.0016, shape_method="CONCAVE")
bpy.ops.object.mode_set(mode="OBJECT")
for ob in bake_objs:
    ob.data.uv_layers.active = ob.data.uv_layers["UVMap"]
print(f"[lightmap] UVs fertig ({time.time() - t0:.1f}s)")

lm_img = bpy.data.images.new("Lightmap", LM_SIZE, LM_SIZE, alpha=True, float_buffer=True)
lm_img.generated_color = (0, 0, 0, 0)
bake_nodes = []
for m in MATS.values():
    nt = m.node_tree
    tn = nt.nodes.new("ShaderNodeTexImage")
    tn.image = lm_img
    tn.location = (-400, -400)
    uvn = nt.nodes.new("ShaderNodeUVMap")
    uvn.uv_map = "lightmap"
    uvn.location = (-650, -400)
    nt.links.new(uvn.outputs["UV"], tn.inputs["Vector"])
    nt.nodes.active = tn
    bake_nodes.append((nt, tn, uvn))


def blur_masked(rgb, mask, radius=2, sigma=1.2):
    k = np.exp(-(np.arange(-radius, radius + 1) ** 2) / (2 * sigma ** 2))
    k /= k.sum()

    def conv(a, axis):
        out = np.zeros_like(a)
        for i, w in enumerate(k):
            out += w * np.roll(a, i - radius, axis=axis)
        return out

    num = rgb * mask[..., None]
    num = conv(conv(num, 0), 1)
    den = conv(conv(mask, 0), 1)
    return np.where(den[..., None] > 1e-4, num / np.maximum(den[..., None], 1e-4), rgb)


def srgb_encode(x):
    x = np.clip(x, 0, 1)
    return np.where(x <= 0.0031308, x * 12.92, 1.055 * np.power(x, 1 / 2.4) - 0.055)


if BAKE:
    scene.cycles.samples = BAKE_SAMPLES
    scene.render.bake.margin = 12
    scene.render.bake.margin_type = "EXTEND"
    scene.render.bake.use_clear = True
    t1 = time.time()
    for ob in C_CHAR.objects:
        ob.hide_render = True
    for ob in bake_objs:  # Bake schreibt in die AKTIVE UV-Map
        ob.data.uv_layers.active = ob.data.uv_layers["lightmap"]
    # Metall hat keinen Diffus-Anteil -> wuerde schwarz backen. Fuer den Bake kurz nicht-metallisch.
    metal_restore = []
    for m in MATS.values():
        b = next(n for n in m.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
        for inp in ("Metallic", "Transmission Weight"):
            if b.inputs[inp].default_value > 0:
                metal_restore.append((b.inputs[inp], b.inputs[inp].default_value))
                b.inputs[inp].default_value = 0.0
    # Blender backt jedes Objekt einzeln (mit Szenen-Sync) -> Kleinteile fuer den Bake zu EINER Kopie
    # zusammenfassen. Gleiches Atlas-UV, also landet alles an der richtigen Stelle.
    dyn = [o for o in bake_objs if o.users_collection[0] == C_DYN]
    shadowless = [o for o in dyn if not o.visible_shadow]
    casting = [o for o in dyn if o.visible_shadow]
    temp_objs = []
    for group, cast in ((shadowless, False), (casting, True)):
        if not group:
            continue
        copies = []
        for o in group:
            c = o.copy()
            c.data = o.data.copy()
            C_DYN.objects.link(c)
            copies.append(c)
            o.hide_render = True
        bpy.ops.object.select_all(action="DESELECT")
        for c in copies:
            c.select_set(True)
        bpy.context.view_layer.objects.active = copies[0]
        bpy.ops.object.join()
        j = bpy.context.view_layer.objects.active
        j.name = "_bake_dyn_shadow" if cast else "_bake_dyn"
        j.visible_shadow = cast
        temp_objs.append(j)
    bpy.ops.object.select_all(action="DESELECT")
    for ob in [apartment, ceiling] + temp_objs:
        ob.select_set(True)
    bpy.context.view_layer.objects.active = apartment
    print(f"[lightmap] Backe {LM_SIZE}px mit {BAKE_SAMPLES} Samples ...")
    bpy.ops.object.bake(type="DIFFUSE", pass_filter={"DIRECT", "INDIRECT"}, margin=12, use_clear=True)
    print(f"[lightmap] gebacken in {time.time() - t1:.1f}s")
    for j in temp_objs:
        me = j.data
        bpy.data.objects.remove(j)
        bpy.data.meshes.remove(me)
    for o in dyn:
        o.hide_render = False
    for ob in bake_objs:
        ob.data.uv_layers.active = ob.data.uv_layers["UVMap"]
    for sock, v in metal_restore:
        sock.default_value = v
    for ob in C_CHAR.objects:
        ob.hide_render = False
    px = np.empty(LM_SIZE * LM_SIZE * 4, np.float32)
    lm_img.pixels.foreach_get(px)
    px = px.reshape(LM_SIZE, LM_SIZE, 4)
    rgb, a = px[..., :3].astype(np.float64), (px[..., 3] > 0.5).astype(np.float64)
    rgb = blur_masked(rgb, a, radius=2, sigma=1.1)
    LM_MAX = float(max(0.25, np.percentile(rgb.max(-1), 99.95) * 1.1))
    enc = srgb_encode(rgb / LM_MAX)
    out = bpy.data.images.new("Lightmap_8bit", LM_SIZE, LM_SIZE, alpha=False)
    out.colorspace_settings.name = "Non-Color"
    o = np.ones((LM_SIZE, LM_SIZE, 4), np.float32)
    o[..., :3] = enc
    out.pixels.foreach_set(o.ravel())
    out.filepath_raw = os.path.join(WEB_ASSETS, "lightmap.png")
    out.file_format = "PNG"
    out.save()
    print("[lightmap] gespeichert:", out.filepath_raw, "max irr", float(rgb.max()), "lm_max", LM_MAX)
    np.save(os.path.join(HERE, "_lightmap_linear.npy"), rgb.astype(np.float16))
    # Fuer Cycles-Renders/Blend: Bake-Knoten wieder entfernen
for nt, tn, uvn in bake_nodes:
    nt.nodes.remove(tn)
    nt.nodes.remove(uvn)

# --------------------------------------------------------------------------- Layout + Export
LAYOUT["meta"] = {"x0": X0, "width": W, "depth": D, "height": H, "lm_max": LM_MAX, "lm_size": LM_SIZE,
                  "coords": "blender: x=Ost, y=Nord, z=oben (three: x, z, -y)"}
with open(os.path.join(WEB_ASSETS, "layout.json" if BAKE else "_layout_unbaked.json"), "w") as f:
    json.dump(LAYOUT, f, indent=1)

bpy.ops.object.select_all(action="DESELECT")
for ob in bake_objs + list(C_CHAR.objects):
    ob.select_set(True)
glb = os.path.join(WEB_ASSETS, "apartment.glb" if BAKE else "_apartment_unbaked.glb")  # UVs muessen zur Lightmap passen
bpy.ops.export_scene.gltf(filepath=glb, export_format="GLB", use_selection=True, export_extras=True,
                          export_apply=True, export_texcoords=True, export_normals=True,
                          export_lights=False, export_cameras=False, export_yup=True,
                          export_image_format="AUTO")
print(f"[export] {glb} ({os.path.getsize(glb) / 1e6:.1f} MB)")


# --------------------------------------------------------------------------- Kameras + Renders
def cam(name, loc, target, lens=24, sensor=36):
    cd = bpy.data.cameras.new(name)
    cd.lens = lens
    cd.sensor_width = sensor
    cd.clip_start = 0.01
    ob = bpy.data.objects.new(name, cd)
    ob.location = loc
    ob.rotation_euler = (Vector(target) - Vector(loc)).to_track_quat("-Z", "Y").to_euler()
    C_CAM.objects.link(ob)
    return ob


CX = (X0 + W) / 2
cams = [
    cam("Cam_Foto", (2.7, 0.6, 1.15), (3.45, 6.5, 1.25), lens=20),
    cam("Cam_Ratte", (3.8, 1.5, 0.05), (X0 + 0.6, 3.8, 0.45), lens=16),
    cam("Cam_Max", (8.7, 3.9, 1.75), (10.25, 0.9, 0.45), lens=22),
    cam("Cam_Nest", (9.6, 7.35, 0.12), (11.15, 7.42, 0.05), lens=18),
    cam("Cam_Kueche", (5.2, 4.75, 1.65), (2.4, 7.6, 0.5), lens=18),
    cam("Cam_Warhammer", (3.75, 6.95, 0.24), (4.6, 7.6, 0.14), lens=24),
    cam("Cam_Bad", (8.05, 7.8, 1.7), (6.3, 6.1, 0.35), lens=18),
    cam("Cam_WC", (9.95, 5.56, 1.6), (11.15, 6.1, 0.3), lens=16),
    cam("Cam_Draufsicht", (CX + 2.5, -7.5, 15.0), (CX, D / 2 - 0.3, 0), lens=30),
]
scene.camera = cams[0]
blend_path = os.path.join(HERE, "apartment.blend")
bpy.ops.wm.save_as_mainfile(filepath=blend_path)
print("[build] gespeichert:", blend_path)

for ob in C_CHAR.objects:
    if ob.name.startswith("MaxW"):
        ob.hide_render = True

if RENDER:
    scene.cycles.samples = RENDER_SAMPLES
    scene.cycles.use_denoising = True
    scene.cycles.use_adaptive_sampling = True
    scene.render.resolution_x = 1600
    scene.render.resolution_y = 900
    scene.render.image_settings.file_format = "PNG"
    for c in cams:
        scene.camera = c
        ceiling.hide_render = c.name == "Cam_Draufsicht"
        scene.render.filepath = os.path.join(RENDERS, c.name.replace("Cam_", "").lower() + ".png")
        t1 = time.time()
        bpy.ops.render.render(write_still=True)
        print(f"[render] {scene.render.filepath} ({time.time() - t1:.1f}s)")
    ceiling.hide_render = False
print(f"[build] fertig nach {time.time() - t0:.1f}s")
