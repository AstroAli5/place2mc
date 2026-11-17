"""Rough 3D picture of part of the generated volume, to eyeball walls, windows, roofs and trees.
usage: python -I tests/render_oblique.py OSM_FILE LAT,LON RADIUS OUT.png [x0 z0 w d]"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import numpy as np
from PIL import Image
import place2mc as p

osm_file, center, radius, out = sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4]
lat, lon = (float(v) for v in center.split(","))
size = -(-2 * radius // 32) * 32
elements, _ = p.load_osm_file(osm_file)
ground, bmap, specs, trees, _ = p.build_map(elements, lat, lon, size)
vol, _ = p.build_volume(ground, bmap, specs, trees, details=p.build_map.details)
x0, z0, w, d = (int(v) for v in sys.argv[5:9]) if len(sys.argv) > 8 else (0, 0, size, size)
vol = vol[p.GROUND:, z0:z0 + d, x0:x0 + w]
rgb = np.zeros((len(p.BLOCKS), 3), dtype=np.int32)
extra = {"glass": (150, 205, 235), "oak_log": (92, 70, 40), "birch_log": (210, 208, 196), "spruce_log": (60, 42, 24),
         "oak_leaves": (50, 120, 44), "birch_leaves": (96, 150, 70), "spruce_leaves": (36, 86, 52)}
for i, (name, _) in enumerate(p.BLOCKS):
    short = name.split(":")[1]
    rgb[i] = p.BLOCK_RGB.get(short, extra.get(short, (200, 0, 200)))
L, D, W = vol.shape
S = 4                                    # pixels per block
img = np.full((D * S // 2 + (L + 2) * S, W * S, 3), 235, dtype=np.uint8)
for z in range(D):                       # far to near
    for y in range(L):                   # low to high
        row = vol[y, z]
        m = row != p.AIR
        if not m.any():
            continue
        xs = np.nonzero(m)[0]
        top = (z + L - y) * S // 2 * 1     # squashed depth
        sy = (z * S) // 2 + (L - y) * S
        col = rgb[row[xs]]
        for dx in range(S):
            img[sy:sy + S // 2, xs * S + dx] = np.clip(col * 1.08, 0, 255)      # top face
            img[sy + S // 2:sy + S + S // 2, xs * S + dx] = col * 0.72            # front face
Image.fromarray(img).save(out)
print("saved", out, img.shape)
