"""Rooms you can walk to: a made-up block of three-storey buildings (a residence hall, offices, a classroom building,
a lab, a library, a shop), each must get rooms with doors, beds in the residence hall's bedrooms, a lamp in every
room, and a staircase, and walking in from the front door (doors open, a step up of one block at most, like a jump)
must reach every floor and nearly every bit of floor inside.

    python tests/check_rooms.py"""
import sys
from collections import deque
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import place2mc as p  # noqa: E402

LAT, LON, SIZE = 40.0, -76.3, 256
fails = []
NAMES = [n.split(":")[1] for n, _ in p.BLOCKS]


def check(ok, text):
    print(("  ok    " if ok else "  FAIL  ") + text)
    if not ok:
        fails.append(text)


def geo(points):
    m_lat, m_lon = p.metres_per_degree(LAT)
    return [{"lat": LAT - (z - SIZE / 2) / m_lat, "lon": LON + (x - SIZE / 2) / m_lon} for x, z in points]


BUILDINGS = [  # name, tags, size (x, z)
    ("Thomas Residence Hall", {"building": "dormitory"}, (60, 16)),
    ("Ware College House", {"building": "dormitory"}, (34, 30)),
    ("Admin Offices", {"building": "office"}, (40, 14)),
    ("Keiper Liberal Arts", {"building": "university"}, (36, 24)),
    ("Hackman Physical Sciences Laboratory", {"building": "university"}, (30, 22)),
    ("Town Library", {"building": "yes"}, (30, 22)),
]
PASS = {i for i, n in enumerate(NAMES) if n in ("air", "lantern") or n.endswith(("_door", "_carpet", "_sign"))}
PASS_ARR = np.zeros(len(NAMES), bool)
PASS_ARR[list(PASS)] = True
STAIRS = np.array([n.endswith("_stairs") for n in NAMES])


def walk_from(vol, start):
    """Every place your feet can be, walking from `start` (layer, z, x): through doors, up at most one block a step
    (stairs, or a jump), down as far as you like."""
    layers, size_z, size_x = vol.shape
    free = PASS_ARR[vol]
    stand = free.copy()
    stand[1:] &= ~free[:-1]                    # something under your feet
    stand[:-1] &= free[1:]                     # room for your head
    stand[0] = False
    seen = np.zeros(vol.shape, bool)
    q = deque([start])
    seen[start] = True
    while q:
        y, z, x = q.popleft()
        for dz, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nz, nx = z + dz, x + dx
            if not (0 <= nz < size_z and 0 <= nx < size_x):
                continue
            for ny in [y + 1] + list(range(y, max(y - 4, 0), -1)):
                if ny >= layers - 1 or not stand[ny, nz, nx]:
                    continue
                if ny > y and not STAIRS[vol[y, nz, nx]] and not free[y + 2, z, x]:
                    continue                               # a jump needs head room; stairs are walked up
                if ny < y and not free[y:ny:-1, nz, nx].all():
                    continue
                if not seen[ny, nz, nx]:
                    seen[ny, nz, nx] = True
                    q.append((ny, nz, nx))
                break
    return seen, stand


def main():
    elements, spots = [], []
    x0, z0 = 8, 10
    for k, (name, tags, (w, h)) in enumerate(BUILDINGS):
        if x0 + w > SIZE - 8:
            x0, z0 = 8, z0 + 50
        spots.append((x0, z0, w, h))
        elements.append({"type": "way", "id": 100 + k, "tags": {**tags, "name": name, "building:levels": "3"},
                         "geometry": geo([(x0, z0), (x0 + w, z0), (x0 + w, z0 + h), (x0, z0 + h), (x0, z0)])})
        x0 += w + 14
    for z in (6, 50, 96, 142):
        elements.append({"type": "way", "id": 900 + z, "tags": {"highway": "residential", "name": f"Street {z}"},
                         "geometry": geo([(0, z), (SIZE, z)])})
    ground, bmap, specs, trees, _ = p.build_map(elements, LAT, LON, SIZE)
    vol, _ = p.build_volume(ground, bmap, specs, trees, details=p.build_map.details)
    stats = p.build_volume.stats
    print("  staircases:", stats.get("staircases"))
    ids = {bmap[z + h // 2, x + w // 2]: (name, (x, z, w, h)) for (name, _, _), (x, z, w, h) in zip(BUILDINGS, spots)}
    for b, (name, (x, z, w, h)) in ids.items():
        inside = p.interior_of(bmap) & (bmap == b)
        cz, cx = np.nonzero(inside)
        found = {NAMES[int(v)] for v in np.unique(vol[:, cz, cx])}
        doors = int(np.isin(vol[:, cz, cx], [i for i, n in enumerate(NAMES) if n.endswith("_door")]).sum()) // 2
        use = p.building_use({"name": name, "building": next(t for n, t, _ in BUILDINGS if n == name)["building"]})
        print(f"  {name} ({use}): {doors} doors inside; " + ", ".join(sorted(found - {"air"}))[:150])
        check("spruce_stairs" in found or "stone_brick_stairs" in found, f"{name}: a staircase")
        check("lantern" in found, f"{name}: lamps")
        if use in p.ROOMED_USES:
            check(doors >= 4, f"{name}: rooms with doors ({doors})")
        if use == "dorm":
            check("red_bed" in found, f"{name}: beds")
        # walk in from outside, through the front door
        surface = p.build_volume.surface
        start = None
        for zz in range(z - 3, z + h + 4):
            for xx in (x - 3, x + w + 3):
                if 0 <= zz < SIZE and 0 <= xx < SIZE:
                    y = int(surface[zz, xx]) + 1
                    if PASS_ARR[vol[y, zz, xx]] and PASS_ARR[vol[y + 1, zz, xx]]:
                        start = (y, zz, xx)
                        break
            if start:
                break
        seen, stand = walk_from(vol, start)
        floors = sorted({int(y) for y in np.nonzero(stand[:, cz, cx])[0]})
        storey_floors = [y for y in floors if stand[y, cz, cx].sum() > 0.3 * len(cz)][:-1]     # not the roof
        reached = [y for y in storey_floors if seen[y, cz, cx].any()]
        check(len(storey_floors) >= 2 and reached == storey_floors,
              f"{name}: walking in reaches floors {reached} of {storey_floors}")
        part = seen[storey_floors][:, cz, cx].sum() / max(stand[storey_floors][:, cz, cx].sum(), 1)
        check(part > 0.9, f"{name}: you can walk to {part:.0%} of the floor inside")
    beds = np.isin(vol, p.BED_BLOCKS)
    check(beds.sum() % 2 == 0, f"{beds.sum()} bed halves, in whole beds")
    print("check_rooms: " + ("all OK" if not fails else f"{len(fails)} FAILED"))
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
