"""Check overture.py against independent code: tiles made with mapbox-vector-tile, archives written with the
pmtiles package (pip install mapbox-vector-tile pmtiles). Covers tile numbering, leaf directories, decoding,
holes in buildings, skipping buildings that came from OpenStreetMap, and the coordinates of every corner.

usage: python -I tests/check_overture.py
"""
import gzip
import json
import math
import random
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import mapbox_vector_tile
from pmtiles.tile import Compression, TileType, zxy_to_tileid
from pmtiles.writer import Writer

import overture

problems = []
rng = random.Random(7)

# 1. tile numbering, against the pmtiles package
for _ in range(3000):
    z = rng.randint(0, 20)
    x, y = rng.randrange(1 << z), rng.randrange(1 << z)
    if overture.zxy_to_tile_id(z, x, y) != zxy_to_tileid(z, x, y):
        problems.append(f"tile id of {z}/{x}/{y}")
print("tile ids: 3000 compared")

# 2. an archive with 20,000 tiles (enough for leaf directories), tiles made by mapbox-vector-tile
Z = 14
TX, TY = 9566, 9440                                   # a tile near Johannesburg
building_tile = mapbox_vector_tile.encode([{
    "name": "building",
    "features": [
        {"geometry": "POLYGON ((100 100, 400 100, 400 300, 100 300, 100 100), (200 150, 300 150, 300 250, 200 250, 200 150))",
         "properties": {"id": "aaa", "class": "school", "height": 9.5, "num_floors": 2, "roof_color": "#3b5b45",
                        "facade_material": "brick", "@geometry_source": "Microsoft ML Buildings",
                        "sources": json.dumps([{"dataset": "Microsoft ML Buildings", "record_id": "x1"}])}},
        {"geometry": "POLYGON ((1000 1000, 1200 1000, 1200 1100, 1000 1100, 1000 1000))",
         "properties": {"id": "bbb", "height": 12.0, "@geometry_source": "OpenStreetMap",
                        "sources": json.dumps([{"dataset": "OpenStreetMap", "record_id": "w563557486@3"}])}},
        {"geometry": "POLYGON ((3000 3000, 3100 3000, 3100 3050, 3000 3050, 3000 3000))",
         "properties": {"id": "ccc", "is_underground": True}},
    ]}], default_options={"y_coord_down": True})
cover_tile = mapbox_vector_tile.encode([{
    "name": "land_cover",
    "features": [{"geometry": "POLYGON ((0 0, 2048 0, 2048 2048, 0 2048, 0 0))", "properties": {"subtype": "forest"}},
                 {"geometry": "POLYGON ((2048 2048, 4096 2048, 4096 4096, 2048 4096, 2048 2048))", "properties": {"subtype": "grass"}}]}],
    default_options={"y_coord_down": True})
folder = Path(tempfile.mkdtemp())
path = folder / "test.pmtiles"
def filler(k):                                        # every tile different, so the archive needs leaf directories
    return gzip.compress(mapbox_vector_tile.encode([{"name": f"x{k}", "features": []}]))
with open(path, "wb") as f:
    w = Writer(f)
    ids = {zxy_to_tileid(Z, TX, TY): gzip.compress(building_tile), zxy_to_tileid(13, TX // 2, TY // 2): gzip.compress(cover_tile)}
    spots = [(rng.randrange(1 << Z), rng.randrange(1 << Z)) for _ in range(20000)]   # spread out: a big directory
    for k, (x, y) in enumerate(spots):
        ids.setdefault(zxy_to_tileid(Z, x, y), filler(k))
    for tile_id in sorted(ids):
        w.write_tile(tile_id, ids[tile_id])
    w.finalize({"tile_type": TileType.MVT, "tile_compression": Compression.GZIP, "min_zoom": 13, "max_zoom": 14,
                "min_lon_e7": 0, "min_lat_e7": 0, "max_lon_e7": 1, "max_lat_e7": 1, "center_zoom": 14,
                "center_lon_e7": 0, "center_lat_e7": 0}, {})
archive = overture.PMTiles(str(path))
print(f"archive: {len(ids)} tiles, root directory {len(archive.root)} entries, "
      f"{'with' if any(e[3] == 0 for e in archive.root) else 'WITHOUT'} leaf directories")
for k in rng.sample(range(20000), 300):
    x, y = spots[k]
    if (x, y) != (TX, TY) and archive.tile(Z, x, y) != gzip.decompress(filler(k)):
        problems.append(f"tile {x},{y} read wrong")
if archive.tile(Z, 5, 5) is not None:
    problems.append("a missing tile was found")

# 3. decoding, against mapbox-vector-tile's own decoder
ours = overture.decode_tile(archive.tile(Z, TX, TY))
theirs = mapbox_vector_tile.decode(building_tile, default_options={"y_coord_down": True})
for (fid, props, polys), ref in zip(ours["building"][1], theirs["building"]["features"]):
    if props != ref["properties"]:
        problems.append(f"properties differ: {props} vs {ref['properties']}")
    ref_rings = ref["geometry"]["coordinates"]
    ref_rings = [ref_rings] if ref["geometry"]["type"] == "Polygon" else [r for p in ref_rings for r in [p]]
    ours_flat = [[list(pt) for pt in ring] for poly in polys for ring in poly]
    ref_flat = [[list(pt) for pt in ring] for poly in ref_rings for ring in poly]
    if ours_flat != ref_flat:
        problems.append(f"rings differ for {props.get('id')}: {ours_flat} vs {ref_flat}")
print(f"decoded {len(ours['building'][1])} buildings; holes: {[len(p[0]) for _, _, p in ours['building'][1]]} rings")

# 4. as map objects
found, heights = overture.buildings_from_tiles([(TX, TY, ours)], Z)
if len(found) != 1 or found[0]["type"] != "relation":
    problems.append(f"expected 1 building with a courtyard, got {[(e['type'], e['tags']) for e in found]}")
else:
    tags = found[0]["tags"]
    want = {"building": "school", "height": "9.5", "building:levels": "2", "roof:colour": "#3b5b45", "building:material": "brick"}
    if any(tags.get(k) != v for k, v in want.items()):
        problems.append(f"tags {tags}")
    corner = found[0]["members"][0]["geometry"][0]
    n = 1 << Z
    lon = (TX + 100 / 4096) / n * 360 - 180
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (TY + 100 / 4096) / n))))
    if abs(corner["lat"] - lat) > 1e-6 or abs(corner["lon"] - lon) > 1e-6:
        problems.append(f"corner at {corner}, expected {lat:.7f}, {lon:.7f}")
if heights != {"way/563557486": {"height": "12.0"}}:
    problems.append(f"heights for OpenStreetMap buildings: {heights}")
cover = overture.land_cover_from_tiles([(TX // 2, TY // 2, overture.decode_tile(archive.tile(13, TX // 2, TY // 2)))], 13)
if [e["tags"] for e in cover] != [{"place2mc:land_cover": "forest"}]:
    problems.append(f"land cover {[e['tags'] for e in cover]}")
print(f"map objects: {len(found)} building (courtyard: {found[0]['type'] == 'relation'}), heights for {list(heights)}, "
      f"land cover {[e['tags']['place2mc:land_cover'] for e in cover]}")
els = [{"type": "way", "id": 563557486, "tags": {"building": "yes"}}, {"type": "way", "id": 5, "tags": {"building": "yes", "height": "3"}}]
overture.add_heights(els, {"way/563557486": {"height": "12.0"}, "way/5": {"height": "99"}})
if els[0]["tags"].get("height") != "12.0" or els[1]["tags"]["height"] != "3":
    problems.append(f"add_heights: {els}")
print("\n".join(problems) if problems else "Overture reader OK")
sys.exit(1 if problems else 0)
