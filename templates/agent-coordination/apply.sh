#!/usr/bin/env bash
# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
#
# Copy the agent-coordination template into another repo and fill in its
# placeholders. See templates/agent-coordination/README.md.
#
#   templates/agent-coordination/apply.sh --env <values-file> <target-repo-dir>
#
# The values file holds KEY=value lines (see template.env.example); each
# {{KEY}} in a file's path or text becomes its value. Existing files in the
# target are never overwritten: they are listed as skipped. Placeholders left
# unfilled are listed at the end, and the script then exits 1.
# A path segment named dot-<x> in files/ is written as .<x> (dot-claude ->
# .claude), so the template's own skills stay inert in this repo.

set -euo pipefail

usage() { echo "usage: $0 --env <values-file> <target-repo-dir>" >&2; exit 2; }
[[ $# -eq 3 && $1 == --env ]] || usage
env_file=$2
target=$3
[[ -f $env_file ]] || { echo "no values file: $env_file" >&2; exit 2; }
[[ -d $target ]] || { echo "no target directory: $target" >&2; exit 2; }

src="$(cd "$(dirname "$0")" && pwd)/files"

python3 -I - "$src" "$target" "$env_file" <<'PY'
import os
import re
import shutil
import sys
from pathlib import Path

src, target, env_file = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])

values = {}
for line in env_file.read_text().splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()

placeholder = re.compile(r"\{\{([A-Z_]+)\}\}")


def fill(text):
    return placeholder.sub(lambda m: values.get(m.group(1), m.group(0)), text)


def dest_path(rel):
    # files/ stores .claude and .github as dot-claude and dot-github, so the
    # harness doesn't load the template's skills as this repo's own.
    parts = rel.split("/")
    parts = ["." + p[4:] if p.startswith("dot-") else p for p in parts]
    return "/".join(parts)


written, skipped, unfilled = [], [], set()
for path in sorted(p for p in src.rglob("*") if p.is_file()):
    rel = dest_path(fill(str(path.relative_to(src))))
    dest = target / rel
    if dest.exists():
        skipped.append(rel)
        continue
    text = fill(path.read_text())
    unfilled.update(f"{rel}: {{{{{m}}}}}" for m in placeholder.findall(text))
    unfilled.update(f"{rel}: {{{{{m}}}}}" for m in placeholder.findall(rel))
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text)
    shutil.copymode(path, dest)
    written.append(rel)

for rel in written:
    print(f"wrote    {rel}")
for rel in skipped:
    print(f"skipped  {rel} (exists)")
if unfilled:
    print("\nunfilled placeholders:")
    for item in sorted(unfilled):
        print(f"  {item}")
    sys.exit(1)
PY
