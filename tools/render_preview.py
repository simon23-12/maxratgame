"""Rendert das Link-Vorschaubild (WhatsApp/Open Graph) und das App-Icon aus apartment.blend.

Aufruf:
  /Applications/Blender.app/Contents/MacOS/Blender -b blender/apartment.blend --python tools/render_preview.py
Danach: python3 tools/make_preview.py  (Titel drauf, komprimieren)
"""
import json
import math
import os

import bpy
from bpy_extras.object_utils import world_to_camera_view
from mathutils import Vector

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "renders")
s = bpy.context.scene
ob = bpy.data.objects

# Figuren einblenden, Max wach in Pose bringen
for o in bpy.data.collections["Characters"].objects:
    o.hide_render = False
for name in ("Max", "Max_Koerper"):  # schlafender Max ist aufgestanden
    ob[name].hide_render = True

cam_xy = Vector((6.55, 1.55))
max_xy = Vector((8.55, 2.6))
ob["MaxWach"].location = (max_xy.x, max_xy.y, 0)
ob["MaxWach"].rotation_euler = (0, 0, math.atan2(cam_xy.y - max_xy.y, cam_xy.x - max_xy.x) - 0.15)
for side, sx in (("L", -1), ("R", 1)):          # Arme nach vorn zum Eimer (wie im Spiel)
    ob[f"MaxW_OA_{side}"].rotation_euler = (sx * 0.28, -1.15, 0)
    ob[f"MaxW_UA_{side}"].rotation_euler = (0, -0.15, 0)
ob["MaxW_OS_L"].rotation_euler = (0, -0.35, 0)  # mitten im Schritt
ob["MaxW_OS_R"].rotation_euler = (0, 0.3, 0)
ob["MaxW_US_R"].rotation_euler = (0, 0.45, 0)
ob["MaxW_Torso"].rotation_euler = (0, 0.12, 0)  # leicht vorgebeugt, jagt

rat_xy = Vector((7.2, 1.82))
ob["Ratte"].location = (rat_xy.x, rat_xy.y, 0)
ob["Ratte"].rotation_euler = (0, 0, math.atan2(cam_xy.y - rat_xy.y, cam_xy.x - rat_xy.x) + 0.35)
for i in range(1, 8):  # Schwanz schwingt
    ob[f"Ratte_Schwanz_{i}"].rotation_euler = (0, -0.05, math.sin(i * 0.9) * 0.25)
ob["Ratte_Bein_VL"].rotation_euler = (0, 0.6, 0)
ob["Ratte_Bein_HR"].rotation_euler = (0, 0.6, 0)
ob["Ratte_Bein_VR"].rotation_euler = (0, -0.6, 0)
ob["Ratte_Bein_HL"].rotation_euler = (0, -0.6, 0)

# Kamera: Froschperspektive hinter der Ratte
cd = bpy.data.cameras.new("Cam_Preview")
cd.lens = 15
cd.clip_start = 0.01
cam = bpy.data.objects.new("Cam_Preview", cd)
s.collection.objects.link(cam)
cam.location = (cam_xy.x, cam_xy.y, 0.13)
cam.rotation_euler = (Vector((8.3, 2.45, 0.6)) - cam.location).to_track_quat("-Z", "Y").to_euler()
s.camera = cam

# Licht: Fuehrungslicht von vorn + roter "Alarm"-Rand von hinten
def area(name, loc, target, color, energy, size):
    ld = bpy.data.lights.new(name, "AREA")
    ld.color = color
    ld.energy = energy
    ld.size = size
    lo = bpy.data.objects.new(name, ld)
    lo.location = loc
    lo.rotation_euler = (Vector(target) - Vector(loc)).to_track_quat("-Z", "Y").to_euler()
    s.collection.objects.link(lo)

area("Key", (6.9, 1.4, 1.5), (8.5, 2.6, 1.1), (1.0, 0.88, 0.8), 32, 0.8)
area("Rim", (9.6, 3.6, 2.0), (8.5, 2.6, 1.0), (1.0, 0.12, 0.08), 70, 0.5)
area("RatFill", (6.8, 1.6, 0.3), (7.2, 1.82, 0.04), (0.85, 0.9, 1.0), 5, 0.3)

s.render.engine = "CYCLES"
try:
    prefs = bpy.context.preferences.addons["cycles"].preferences
    prefs.compute_device_type = "METAL"
    prefs.get_devices()
    for d in prefs.devices:
        d.use = d.type != "CPU"
    s.cycles.device = "GPU"
except Exception as e:
    print("GPU:", e)
s.cycles.samples = 192
s.cycles.use_denoising = True
s.view_settings.view_transform = "AgX"
s.view_settings.look = "AgX - Medium High Contrast" if "AgX - Medium High Contrast" in \
    [i.identifier for i in s.view_settings.bl_rna.properties["look"].enum_items] else "None"
s.render.image_settings.file_format = "PNG"

s.render.resolution_x, s.render.resolution_y = 1200, 630
s.render.filepath = os.path.join(OUT, "og_raw.png")
bpy.ops.render.render(write_still=True)

# Bildschirmposition von Max' Kopf fuer das "!" im Overlay
dg = bpy.context.evaluated_depsgraph_get()
head = ob["MaxW_Kopf"].matrix_world.translation + Vector((0, 0, 0.35))
p = world_to_camera_view(s, cam, head)
json.dump({"head": [p.x, 1 - p.y]}, open(os.path.join(OUT, "og_raw.json"), "w"))

# App-Icon: Ratte in Nahaufnahme
fwd = Vector((math.cos(ob["Ratte"].rotation_euler.z), math.sin(ob["Ratte"].rotation_euler.z), 0))
side = Vector((-fwd.y, fwd.x, 0))
look = Vector((rat_xy.x, rat_xy.y, 0.05)) + fwd * 0.05
cam.location = look + fwd * 0.2 + side * 0.08 + Vector((0, 0, 0.03))
cam.rotation_euler = (look - cam.location).to_track_quat("-Z", "Y").to_euler()
cd.lens = 45
s.render.resolution_x = s.render.resolution_y = 512
s.render.filepath = os.path.join(OUT, "icon_raw.png")
bpy.ops.render.render(write_still=True)
print("preview fertig")
