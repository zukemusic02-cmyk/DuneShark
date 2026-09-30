"""Measure each exported icon's average brightness (opaque pixels) -> icon_lum.json {iconPath: 0..1}.
The page multiplies icon x card colour, then brightens by ~0.95/lum, so grey and white icons end up equally bright."""
import json, os
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..", "icons_export", "DuneSandbox", "Content")
out = {}
for i in json.load(open(os.path.join(HERE, "items.json"), encoding="utf-8")):
    ic = i.get("icon", "")
    if not ic.startswith("/Game/") or ic in out:
        continue
    f = os.path.join(ROOT, *ic[6:].split("/")) + ".png"
    if not os.path.isfile(f):
        continue
    im = Image.open(f).convert("RGBA").resize((64, 64))
    tot = n = 0
    for r, g, b, a in im.getdata():
        if a > 40:
            tot += (0.299 * r + 0.587 * g + 0.114 * b) * a / 255
            n += a / 255
    out[ic] = round(tot / n / 255, 3) if n else 1.0
json.dump(out, open(os.path.join(HERE, "icon_lum.json"), "w"), indent=0)
print(len(out), "icons measured")
