# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
# SPDX-License-Identifier: MIT
# Compare node names, extras and translations between two GLBs (stdlib only).
import json
import struct
import sys


def nodes(path):
    b = open(path, "rb").read()
    n = struct.unpack("<I", b[12:16])[0]
    j = json.loads(b[20 : 20 + n])
    return {nd["name"]: nd for nd in j.get("nodes", []) if "mesh" in nd}


a, b = nodes(sys.argv[1]), nodes(sys.argv[2])
ok = True
for name, na in a.items():
    nb = b.get(name)
    if nb is None:
        print("MISSING", name)
        ok = False
        continue
    if na.get("extras") != nb.get("extras"):
        print("EXTRAS DIFFER", name, na.get("extras"), nb.get("extras"))
        ok = False
    ta, tb = na.get("translation", [0, 0, 0]), nb.get("translation", [0, 0, 0])
    if max(abs(x - y) for x, y in zip(ta, tb)) > 1e-5:
        print("TRANSLATION DIFFERS", name, ta, tb)
        ok = False
print("MATCH" if ok else "MISMATCH", len(a), "nodes")
sys.exit(0 if ok else 1)
