#!/bin/zsh
# Komplette Pipeline: Texturen -> Blender (Bau + Licht-Bake + GLB) -> Lightmap als WebP
# Optionen werden an Blender durchgereicht, z.B.:  ./build.sh --bake-samples 512 --render
set -e
cd "$(dirname "$0")"
BLENDER=${BLENDER:-/Applications/Blender.app/Contents/MacOS/Blender}
python3 tools/make_textures.py
"$BLENDER" -b --factory-startup --python blender/build_apartment.py -- "$@" 2>&1 | grep -E "^\[|Error|Traceback"
python3 tools/encode_lightmap.py
