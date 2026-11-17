"""Render one view of a place. usage: python -I tests/shot.py OSM LAT,LON RADIUS OUT.png ex ey ez tx ty tz [W H SSAA FOV]"""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import place2mc as p, mcrender
a = sys.argv
lat, lon = (float(v) for v in a[2].split(","))
radius = int(a[3]); size = -(-2 * radius // 32) * 32
els, _ = p.load_osm_file(a[1])
ground, bmap, specs, trees, _ = p.build_map(els, lat, lon, size)
vol, _ = p.build_volume(ground, bmap, specs, trees, details=p.build_map.details)
eye, target = [float(v) for v in a[5:8]], [float(v) for v in a[8:11]]
w, h, ss, fov = (int(a[11]), int(a[12]), int(a[13]), float(a[14])) if len(a) > 14 else (640, 360, 1, 65.0)
t0 = time.time()
mcrender.render(vol, eye, target, w, h, fov, ssaa=ss).save(a[4])
print("saved", a[4], f"{time.time() - t0:.1f}s")
