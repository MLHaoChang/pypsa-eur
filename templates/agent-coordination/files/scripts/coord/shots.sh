#!/usr/bin/env bash
# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
#
# Downscale screenshots before a session reads them: a full-size screenshot
# costs several thousand tokens of context each time it is loaded.
#
#   scripts/coord/shots.sh <image>...
#
# For each PNG/JPG it writes <name>.small.<ext> next to the image, longest side
# at most SHOTS_MAX px (default 800; a smaller image is copied at its own size),
# and prints that path. Read the small file, never the original. Uses Pillow
# from the first python that has it (SHOTS_PYTHON, python3), else ImageMagick.
# Exit 1 when an image fails, 2 on bad usage or when no tool is found.
set -uo pipefail

max=${SHOTS_MAX:-800}
[[ $max =~ ^[1-9][0-9]*$ ]] || { echo "SHOTS_MAX must be a positive whole number, got '$max'" >&2; exit 2; }
[ $# -gt 0 ] || { echo "usage: shots.sh <image>..." >&2; exit 2; }

py="" im=""
for c in ${SHOTS_PYTHON:+"$SHOTS_PYTHON"} python3; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import PIL' 2>/dev/null; then py=$c; break; fi
done
if [ -z "$py" ]; then
  im=$(command -v magick || command -v convert) || {
    echo "shots.sh: no image tool found: need a python with Pillow (set SHOTS_PYTHON) or ImageMagick (magick/convert)" >&2
    exit 2
  }
fi

rc=0
for src in "$@"; do
  ext=${src##*.}
  case ${ext,,} in
    png | jpg | jpeg) ;;
    *) echo "shots.sh: $src: not a PNG or JPG" >&2; rc=1; continue ;;
  esac
  [ -f "$src" ] || { echo "shots.sh: $src: no such file" >&2; rc=1; continue; }
  out="${src%.*}.small.$ext"
  if [ -n "$py" ]; then
    "$py" - "$src" "$out" "$max" <<'PY' || { echo "shots.sh: $src: could not be resized" >&2; rc=1; continue; }
import sys

from PIL import Image

src, out, longest = sys.argv[1], sys.argv[2], int(sys.argv[3])
with Image.open(src) as image:
    image.thumbnail((longest, longest))  # keeps the aspect ratio, never enlarges
    image.save(out)
PY
  else
    "$im" "$src" -resize "${max}x${max}>" "$out" || { echo "shots.sh: $src: could not be resized" >&2; rc=1; continue; }
  fi
  echo "$out"
done
exit $rc
