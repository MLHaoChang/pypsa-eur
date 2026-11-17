# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
# SPDX-License-Identifier: MIT
# Runs inside Blender: import our GLB, export GLB with extras, save .blend.
# Usage: blender --background --factory-startup --offline-mode --python-exit-code 3 \
#          --python roundtrip.py -- IN.glb OUT.glb OUT.blend
import sys

import bpy

args = sys.argv[sys.argv.index("--") + 1 :]
src, out_glb, out_blend = args

bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=src, import_scene_extras=True)
objs = [o for o in bpy.data.objects]
for o in objs:
    print("OBJ", o.name, "feature_id=", o.get("feature_id"), "loc=", tuple(round(v, 6) for v in o.location))
bpy.ops.export_scene.gltf(
    filepath=out_glb, export_format="GLB", export_extras=True, export_yup=True
)
bpy.ops.wm.save_as_mainfile(filepath=out_blend, compress=False)
print("DONE", len(objs), "objects")
