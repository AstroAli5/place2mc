"""Build a small made-up map with a zebra crossing, a fenced garden, a garden wall, a hedge, a railway, a tram
line and benches, and check the blocks: fences and walls join exactly the neighbours that are there, walls have a
post except on straight runs, every rail turns or climbs towards the rails beside it, the crossing has white
stripes, the benches are there. Saves a picture of it.

usage: python -I tests/check_details.py [OUT.png]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import numpy as np
import place2mc as p

LAT, LON = 52.0, 5.0
M_LAT, M_LON = p.metres_per_degree(LAT)


def pt(x, z):
    return {"lat": LAT - z / M_LAT, "lon": LON + x / M_LON}


def way(points, **tags):
    return {"type": "way", "id": len(ELEMENTS) + 1, "geometry": [pt(x, z) for x, z in points], "tags": tags}


def node(x, z, **tags):
    return {"type": "node", "id": len(ELEMENTS) + 1, **pt(x, z), "tags": tags}


ELEMENTS = []
for el in [
    way([(-100, 0), (100, 0)], highway="residential"),
    node(25, 0, highway="crossing", crossing="uncontrolled", crossing_ref="zebra"),
    way([(-20, -7), (-20, 7)], highway="footway", footway="crossing", crossing="marked"),
    way([(12, 16), (22, 16), (22, 26), (12, 26), (12, 16)], building="house"),
    way([(6, 12), (34, 12), (34, 36), (6, 36), (6, 12)], barrier="fence"),
    way([(-45, 15), (-10, 15), (-10, 42)], barrier="wall", height="2"),
    way([(-45, -14), (-10, -32)], barrier="hedge"),
    way([(-100, -55), (0, -55), (50, -85), (100, -85)], railway="rail"),
    way([(-100, 2), (100, 2)], railway="tram"),
    node(40, 8, amenity="bench"), node(-35, -7, amenity="bench"), node(16, 28, amenity="bench"),
]:
    ELEMENTS.append(el)

size = 224
ground, bmap, specs, trees, counts = p.build_map(ELEMENTS, LAT, LON, size)
vol, _ = p.build_volume(ground, bmap, specs, trees, details=p.build_map.details)
stats = p.build_volume.stats
print("map:", counts)
print("blocks:", stats)
names = [n.split(":")[1] for n, _ in p.BLOCKS]
props = [pr or {} for _, pr in p.BLOCKS]
problems = []
SIDES = {"north": (0, -1, 0), "south": (0, 1, 0), "west": (0, 0, -1), "east": (0, 0, 1)}
OPPOSITE = {"north": "south", "south": "north", "west": "east", "east": "west"}

# fences and walls: every joined side has a fence/wall/building wall there, and the neighbour joins back
for y, z, x in zip(*np.nonzero(np.isin(vol, p.FENCES + p.GARDEN_WALLS))):
    b = vol[y, z, x]
    for side, (dy, dz, dx) in SIDES.items():
        nb = vol[y, z + dz, x + dx]
        joined = props[b].get(side) in ("true", "low", "tall")
        same_kind = names[nb] == names[b]
        if joined and not (same_kind or bmap[z + dz, x + dx] > 0):
            problems.append(f"{names[b]} at {x},{y},{z} joins {side} but nothing is there ({names[nb]})")
        if same_kind and not joined:
            problems.append(f"{names[b]} at {x},{y},{z} does not join the {names[nb]} to its {side}")
        if same_kind and joined and props[nb].get(OPPOSITE[side]) not in ("true", "low", "tall"):
            problems.append(f"{names[b]} at {x},{y},{z} joins {side} but the neighbour does not join back")
    if names[b] == "stone_brick_wall":
        sides = {s for s in SIDES if props[b].get(s) == "low"}
        straight = sides in ({"north", "south"}, {"west", "east"})
        if (props[b].get("up") == "false") != straight:
            problems.append(f"wall at {x},{y},{z} with sides {sorted(sides)} has up={props[b].get('up')}")

# rails: every side a rail points to holds a rail (on the same level, or one up for a climbing rail),
# and a rail with a rail beside it points to it unless it already joins two others
JOINS = {"north_south": ("north", "south"), "east_west": ("east", "west"), "ascending_north": ("north", "south"),
         "ascending_south": ("north", "south"), "ascending_west": ("west", "east"), "ascending_east": ("west", "east"),
         "south_east": ("south", "east"), "south_west": ("south", "west"), "north_west": ("north", "west"),
         "north_east": ("north", "east")}
rail_ids = list(p.RAILS.values())
rail_cells = list(zip(*np.nonzero(np.isin(vol, rail_ids))))
for y, z, x in rail_cells:
    shape = props[vol[y, z, x]]["shape"]
    beside = [s for s, (dy, dz, dx) in SIDES.items()
              if 0 <= z + dz < size and 0 <= x + dx < size
              and any(vol[y + k, z + dz, x + dx] in rail_ids for k in (-1, 0, 1))]
    for s in beside:
        if s not in JOINS[shape] and len(set(beside)) <= 2:
            problems.append(f"rail at {x},{y},{z} ({shape}) does not turn towards the rail to its {s}")
print(f"checked {int(np.isin(vol, p.FENCES).sum())} fence, {int(np.isin(vol, p.GARDEN_WALLS).sum())} wall, "
      f"{int((vol == p.HEDGE).sum())} hedge and leaf, {len(rail_cells)} rail blocks")

# zebra crossings: white stripes across the road at x=-20 (footway) and x=25 (point)
for cx in (-20, 25):
    col = size // 2 + cx
    band = ground[size // 2 - 3:size // 2 + 4, col - 2:col + 3]
    white = int((band == p.S_LINE).sum())
    print(f"zebra at x={cx}: {white} white cells")
    if white < 6:
        problems.append(f"no zebra stripes at x={cx}")
if stats["benches"] != 3:
    problems.append(f"{stats['benches']} benches placed, expected 3")

if len(sys.argv) > 1:
    import mcrender
    h = p.FLOOR
    mcrender.render(vol, (size / 2 - 60, h + 28, size / 2 + 55), (size / 2 - 5, h, size / 2 - 5), 1280, 720, 70,
                    ssaa=2).save(sys.argv[1])
    print("picture:", sys.argv[1])
print("\n".join(problems[:20]) if problems else "details OK")
sys.exit(1 if problems else 0)
