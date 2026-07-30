"""
PyPSA Studio app icon: a one-line diagram — buses, lines, a hub.

Drawn per SIZE rather than scaled from one master. A 1024 px render downsampled
to 32 turns the outer ring into grey mush; at 32 px the ring is dropped and the
strokes are fattened, which is the whole reason macOS iconsets carry separate
images instead of one. Judge this in the Dock, not at 1024.

Geometry follows Apple's grid: the tile occupies ~82% of the canvas so the icon
sits at the same visual weight as its neighbours instead of looming over them.
"""
import math
import sys
from PIL import Image, ImageDraw, ImageFilter

BLACK = (21, 17, 18)
RED = (255, 82, 82)
RED_SOFT = (255, 138, 138)
RED_DEEP = (224, 49, 49)
RED_DEEPER = (176, 34, 38)
INK = (244, 234, 234)


def _tile(size):
    """Dark rounded tile with a warm bloom off the upper left."""
    img = Image.new("RGB", (size, size), BLACK)
    glow = Image.new("RGB", (size, size), BLACK)
    d = ImageDraw.Draw(glow)
    steps = max(8, size // 12)
    for i in range(steps, 0, -1):
        r = size * 0.95 * i / steps
        k = 1 - i / steps
        col = tuple(int(BLACK[c] + (RED_DEEPER[c] - BLACK[c]) * (k ** 1.7) * 0.95) for c in range(3))
        d.ellipse([size * 0.26 - r, size * 0.06 - r, size * 0.26 + r, size * 0.06 + r], fill=col)
    return Image.blend(img, glow.filter(ImageFilter.GaussianBlur(size * 0.06)), 0.92)


def draw(size, *, ring=True, scale=1.0):
    """
    `ring` off and fatter strokes below 64 px: at that size the hex ring and
    the spokes merge into a blob, and a blob says nothing.
    """
    SS = 4 if size <= 128 else 2          # supersample; small icons need more
    S = size * SS
    img = _tile(S).convert("RGBA")

    cx, cy, R = S * 0.5, S * 0.515, S * 0.245
    spoke_w = int(S * 0.050 * scale)
    ring_w = int(S * 0.034 * scale)
    node_r = S * 0.052 * scale
    hub_r = S * 0.098 * scale

    pts = [(cx + R * math.cos(math.radians(a)), cy + R * math.sin(math.radians(a)))
           for a in range(-90, 270, 60)]

    art = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    a = ImageDraw.Draw(art)
    if ring:
        for i in range(len(pts)):
            a.line([pts[i], pts[(i + 1) % len(pts)]], fill=RED_DEEP + (215,), width=ring_w)
    for p in pts:
        a.line([(cx, cy), p], fill=RED + (255,), width=spoke_w)
    for p in pts:
        a.ellipse([p[0] - node_r, p[1] - node_r, p[0] + node_r, p[1] + node_r],
                  fill=RED_SOFT + (255,))

    # Bloom under the linework, then the linework again on top: the glow reads
    # as emitted light rather than as a blurry copy.
    img.alpha_composite(art.filter(ImageFilter.GaussianBlur(S * 0.020)))
    img.alpha_composite(art)

    d = ImageDraw.Draw(img)
    d.ellipse([cx - hub_r, cy - hub_r, cx + hub_r, cy + hub_r], fill=INK + (255,))

    # Apple's grid: the tile is ~82% of the canvas, centred, rest transparent.
    inset = int(S * 0.09)
    tile_px = S - 2 * inset
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [inset, inset, S - inset - 1, S - inset - 1],
        radius=int(tile_px * 0.225), fill=255,
    )
    img.putalpha(mask)
    return img.resize((size, size), Image.LANCZOS)


def variant(px):
    if px <= 32:
        return draw(px, ring=False, scale=1.45)
    if px <= 64:
        return draw(px, ring=False, scale=1.18)
    if px <= 128:
        return draw(px, ring=True, scale=1.06)
    return draw(px, ring=True, scale=1.0)


if __name__ == "__main__":
    out = sys.argv[1]
    # The ten images `iconutil` expects.
    for px, name in [
        (16, "icon_16x16.png"), (32, "icon_16x16@2x.png"),
        (32, "icon_32x32.png"), (64, "icon_32x32@2x.png"),
        (128, "icon_128x128.png"), (256, "icon_128x128@2x.png"),
        (256, "icon_256x256.png"), (512, "icon_256x256@2x.png"),
        (512, "icon_512x512.png"), (1024, "icon_512x512@2x.png"),
    ]:
        variant(px).save(f"{out}/{name}")
    print("iconset written")
