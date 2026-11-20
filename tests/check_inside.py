"""Insides by use, name signs and place labels: a made-up block of buildings, each named for what it is, must get the
inside that matches (bookshelves in the library, water in the pool, pews in the chapel, beds in the dorm, a court in
the gym, seats facing a stage in the theatre, tables in the dining hall, display cases in the museum, benches and fume
cupboards in the lab, a board in the classroom), a sign with its name, and a place label for the walk-through.

    python tests/check_inside.py"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import place2mc as p  # noqa: E402

LAT, LON, SIZE = 40.0, -76.3, 256
fails = []


def check(ok, text):
    print(("  ok    " if ok else "  FAIL  ") + text)
    if not ok:
        fails.append(text)


def geo(points):
    m_lat, m_lon = p.metres_per_degree(LAT)
    return [{"lat": LAT - (z - SIZE / 2) / m_lat, "lon": LON + (x - SIZE / 2) / m_lon} for x, z in points]


BUILDINGS = [  # name, tags, the use it should get, a block that must be inside
    ("Town Library", {"building": "yes"}, "library", "bookshelf"),
    ("Kunkel Aquatic Center", {"building": "yes"}, "pool", "water"),
    ("Santee Chapel", {"building": "yes"}, "chapel", "dark_oak_stairs"),
    ("Ware College House", {"building": "yes"}, "dorm", "red_bed"),
    ("Mayser Physical Education Center", {"building": "yes"}, "gym", "birch_planks"),
    ("Roschel Performing Arts Center", {"building": "yes"}, "theatre", "red_nether_brick_stairs"),
    ("Market Place Dining Hall", {"building": "yes"}, "dining", "polished_andesite"),
    ("North Museum", {"building": "yes"}, "museum", "quartz_block"),
    ("Hackman Physical Sciences Laboratory", {"building": "yes"}, "lab", "iron_block"),
    ("Keiper Hall", {"building": "university"}, "classroom", "black_concrete"),
]


def main():
    elements, spots = [], []
    for k, (name, tags, use, _) in enumerate(BUILDINGS):
        x0, z0 = 20 + (k % 5) * 46, 30 + (k // 5) * 70
        spots.append((x0, z0))
        elements.append({"type": "way", "id": 100 + k, "tags": {**tags, "name": name},
                         "geometry": geo([(x0, z0), (x0 + 34, z0), (x0 + 34, z0 + 26), (x0, z0 + 26), (x0, z0)])})
        check(p.building_use(elements[-1]["tags"]) == use, f"{name}: used as {p.building_use(elements[-1]['tags'])}")
    elements.append({"type": "way", "id": 900, "tags": {"highway": "residential", "name": "College Avenue"},
                     "geometry": geo([(0, 112), (SIZE, 112)])})
    elements.append({"type": "way", "id": 901, "tags": {"highway": "footway"}, "geometry": geo([(0, 64), (SIZE, 64)])})
    ground, bmap, specs, trees, _ = p.build_map(elements, LAT, LON, SIZE)
    vol, _ = p.build_volume(ground, bmap, specs, trees, details=p.build_map.details)
    names = {i: p.BLOCKS[i][0].split(":")[1] for i in range(len(p.BLOCKS))}
    for (name, _, use, needs), (x0, z0) in zip(BUILDINGS, spots):
        inside = vol[:, z0 + 2:z0 + 25, x0 + 2:x0 + 33]
        found = {names[int(v)] for v in np.unique(inside)}
        check(needs in found, f"{name} ({use}) has {needs} inside")
    signs = getattr(p.build_volume, "signs", [])
    texts = [" ".join(s[4]).strip() for s in signs]
    named = [n for n, *_ in BUILDINGS if any(" ".join(n.split()) == " ".join(t.split()) for t in texts)]
    check(len(named) == len(BUILDINGS), f"{len(named)} of {len(BUILDINGS)} buildings have a sign with their name")
    labels = p.place_labels(elements, LAT, LON, SIZE, bmap, p.build_map.details)
    lnames, layers = labels
    for (name, *_), (x0, z0) in zip(BUILDINGS, spots):
        z, x = (z0 + 13) // 2, (x0 + 17) // 2
        check(lnames[layers[0, z, x]] == name, f"the walk-through says Inside {lnames[layers[0, z, x]]!r} at {name}")
    street = lnames[layers[1, 56, 64]]
    check(street == "College Avenue", f"on the street it says On {street!r}")
    if len(sys.argv) > 1:                       # a walk-through of this block, for tests/walk_doors.py
        import walkdata
        spawn = p.choose_spawn(vol, ground, p.build_volume.surface, (64, 112))
        walkdata.export(vol, sys.argv[1], spawn, "Inside test", labels=labels, signs=p.build_volume.signs)
        print("walk-through:", sys.argv[1])
    print("check_inside: " + ("all OK" if not fails else f"{len(fails)} FAILED"))
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
