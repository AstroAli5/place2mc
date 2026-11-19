"""Measured roofs and trees (--lidar): build a small made-up place whose laser scan we know exactly, and check that
every roof and tree comes out at its real height, that gables and towers get walls, that sloped roofs get stairs, and
that the trees stand where they were measured.

    python tests/check_lidar.py [out.png] [LIDAR.json ELEMENTS.json]

With a LIDAR.json made by app/lidar.js (tests/lidar_js_test.mjs writes one), it also checks that file's ground, roofs and
trees come back right through load_lidar."""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import place2mc as p  # noqa: E402

LAT, LON, SIZE = 40.0465, -76.32, 160
HOUSE = dict(x0=40, x1=60, z0=40, z1=52, eave=6, ridge=10)          # gable roof, ridge along x at z = 46
BLOCK = dict(x0=90, x1=115, z0=30, z1=55, top=12, tower=(100, 104, 38, 42, 21))
TREES = [(40, 110, 15, 5), (70, 120, 9, 3), (120, 110, 20, 6)]      # x, z, height, crown radius
fails = []


def ground_at(x, z):
    return 100 + 0.03 * x - 0.01 * z


def check(ok, text):
    print(("  ok    " if ok else "  FAIL  ") + text)
    if not ok:
        fails.append(text)


def geo(points):
    m_lat, m_lon = p.metres_per_degree(LAT)
    return [{"lat": LAT - (z - SIZE / 2) / m_lat, "lon": LON + (x - SIZE / 2) / m_lon} for x, z in points]


def made_up_scan():
    zz, xx = np.indices((SIZE, SIZE)) + 0.5
    dtm = ground_at(xx, zz)
    roof = np.full((SIZE, SIZE), np.nan)
    h = HOUSE
    m = (xx >= h["x0"]) & (xx < h["x1"]) & (zz >= h["z0"]) & (zz < h["z1"])
    roof[m] = (h["eave"] + (h["ridge"] - h["eave"]) * (1 - np.abs(zz - 46) / 6))[m]
    b = BLOCK
    m = (xx >= b["x0"]) & (xx < b["x1"]) & (zz >= b["z0"]) & (zz < b["z1"])
    roof[m] = b["top"]
    tx0, tx1, tz0, tz1, th = b["tower"]
    roof[(xx >= tx0) & (xx < tx1) & (zz >= tz0) & (zz < tz1)] = th
    roof[44, 52] = np.nan                                  # a gap in the scan
    roof[40, 41] = 0.3                                     # the outline overhangs the wall here: the scan sees ground
    trees = np.array([(x + 0.5, z + 0.5, hh, r) for x, z, hh, r in TREES], dtype=float)
    # a tower bay sticking 4 m out of the block's east wall, which the map's outline leaves out: the scan reader keeps
    # one ring of cells with the building and passes the rest as a patch the map lacks
    unmapped = np.full((SIZE, SIZE), np.nan)
    roof[40:45, 115] = 26
    unmapped[40:45, 116:119] = 26
    return {"dtm": dtm, "roof": roof, "trees": trees, "points": 0, "source": "made up", "unmapped": unmapped}


def build(lidar):
    elements = [
        {"type": "way", "id": 1, "tags": {"building": "house"},
         "geometry": geo([(40, 40), (60, 40), (60, 52), (40, 52), (40, 40)])},
        {"type": "way", "id": 2, "tags": {"building": "university"},
         "geometry": geo([(90, 30), (115, 30), (115, 55), (90, 55), (90, 30)])},
        {"type": "way", "id": 3, "tags": {"building": "yes"},              # put up after the scan: no roof measured
         "geometry": geo([(20, 80), (32, 80), (32, 92), (20, 92), (20, 80)])},
    ]
    ground, bmap, specs, trees, _ = p.build_map(elements, LAT, LON, SIZE)
    p.add_scanned_buildings(ground, bmap, specs, lidar)
    terrain = p.make_terrain(LAT, LON, SIZE, ground, elev=lidar["dtm"])
    vol, planted = p.build_volume(ground, bmap, specs, trees, terrain=terrain, details=p.build_map.details, lidar=lidar)
    return vol, terrain[0], bmap, planted


def top_solid(vol, z, x):
    col = np.flatnonzero(vol[:, z, x] != p.AIR)
    return int(col[-1])


def main():
    args = sys.argv[1:]
    out_png = args.pop(0) if args and args[0].endswith(".png") else None
    lidar = made_up_scan()
    vol, g, bmap, planted = build(lidar)
    names = {i: p.BLOCKS[i][0].split(":")[1] for i in range(len(p.BLOCKS))}
    print("Roofs (height of the top of the roof above the ground beside the building):")
    for label, z, x, want in (("house eaves", 40, 50, HOUSE["eave"] + 0.5), ("house ridge", 46, 50, HOUSE["ridge"]),
                              ("flat block", 34, 93, BLOCK["top"]), ("tower", 40, 102, BLOCK["tower"][4])):
        # the top surface of the top block, minus the ground surface under it
        got = top_solid(vol, z, x) + 1 - (g[z, x] + 1)
        check(abs(got - want) <= 1, f"{label}: {got} blocks, measured {want} m")
    check(top_solid(vol, 44, 52) - g[44, 52] >= HOUSE["eave"], "a gap in the scan takes the roof around it")
    check(top_solid(vol, 40, 41) - g[40, 41] >= HOUSE["eave"] - 1, "an outline cell where the scan saw ground keeps the wall")
    tower_side = names[int(vol[g[40, 100] + 15, 40, 100])]
    check(tower_side in ("bricks", "white_concrete", "light_gray_concrete", "stone_bricks", "quartz_block",
                         "smooth_sandstone", "polished_andesite", "terracotta", "white_terracotta", "glass")
          or "glass" in tower_side or "concrete" in tower_side, f"the side of the tower is wall ({tower_side}), not roof")
    stairs = sum(1 for z in range(40, 52) for x in range(41, 59) if "stairs" in names[int(vol[top_solid(vol, z, x), z, x])])
    check(stairs >= 60, f"the gable roof slopes with stairs ({stairs} stair blocks on top)")
    west = int(np.flatnonzero(bmap[46] == bmap[46, 50])[0])            # the house's west wall, a gable end
    gable = names[int(vol[g[46, west] + 8, 46, west])]
    check("stairs" not in gable and gable not in ("air",), f"the gable end is closed ({gable} at the ridge end)")
    flat_top = names[int(vol[top_solid(vol, 34, 93), 34, 93])]
    check("stairs" not in flat_top, f"the flat roof is flat ({flat_top})")
    bay = top_solid(vol, 42, 117) + 1 - (g[42, 117] + 1)
    check(bmap[42, 117] == bmap[42, 100] and abs(bay - 26) <= 1,
          f"a tower bay the outline leaves out joins its building at its height ({bay} blocks, measured 26 m)")
    late = top_solid(vol, 86, 26) - g[86, 26]
    check(3 <= late <= 30, f"a building the scan missed still gets a guessed height ({late} blocks)")
    print("Trees:")
    check(planted == len(TREES), f"{planted} trees planted, {len(TREES)} measured, none made up")
    for x, z, h, r in TREES:
        col = vol[:, z - 1:z + 2, x - 1:x + 2]
        leafy = [y for y in range(vol.shape[0]) if any("leaves" in names[int(v)] for v in col[y].ravel())]
        top = max(leafy) + 1 - (g[z, x] + 1) if leafy else 0
        width = max((abs(dx) for dx in range(-9, 10) for y in leafy
                     if 0 <= x + dx < SIZE and "leaves" in names[int(vol[y, z, x + dx])]), default=0)
        check(abs(top - h) <= 1, f"tree at {x},{z}: {top} blocks tall, measured {h} m")
        check(abs(width - r) <= 1.5, f"tree at {x},{z}: crown {width} blocks from the trunk, measured {r} m")
    if len(args) == 2:
        print("app/lidar.js output through load_lidar:")
        got = p.load_lidar(args[0], LAT, LON, SIZE)
        zz, xx = np.indices((SIZE, SIZE)) + 0.5
        err = np.abs(got["dtm"] - ground_at(xx, zz))
        check(err.max() < 0.15, f"ground within {err.max():.2f} m everywhere")
        r = got["roof"]
        check(abs(r[46, 50] - HOUSE["ridge"]) <= 0.5 and abs(np.nanmedian(r[38:42, 100:104]) - 21) <= 0.5
              and abs(np.nanmedian(r[31:37, 91:99]) - 12) <= 0.5, "ridge 10 m, tower 21 m, block 12 m")
        for x, z, h, _ in TREES:
            near = [t for t in got["trees"] if abs(t[0] - x - .5) <= 1.5 and abs(t[1] - z - .5) <= 1.5]
            check(len(near) == 1 and abs(near[0][2] - h) <= 0.6, f"one tree found at {x},{z}, {near[0][2] if near else 0:.1f} m")
        check(len(got["trees"]) == len(TREES), f"{len(got['trees'])} trees found, {len(TREES)} there")
    if out_png:
        import mcrender
        img = mcrender.render(vol, (150.5, int(g[80, 150]) + 16, 140.5), (78, int(g[60, 78]) + 6, 55),
                              width=900, height=500, look=mcrender.Look(lat=LAT, hour=15.5))
        img.save(out_png)
        print(f"Picture: {out_png}")
    print("check_lidar: " + ("all OK" if not fails else f"{len(fails)} FAILED"))
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
