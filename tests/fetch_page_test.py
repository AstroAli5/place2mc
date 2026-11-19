"""End-to-end test of the downloader page in headless Chrome: a made-up laser scan (a real .laz file in an Entwine
Point Tiles layout, Web Mercator) and a made-up OpenStreetMap answer are served in place of the real servers; the page
runs, saves its file, and place2mc reads it back.

    python test_fetch_page.py PAGE.html OUT.json"""
import json
import math
import sys
import tempfile
from pathlib import Path

from pathlib import Path
import laspy
import numpy as np
from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import place2mc as p  # noqa: E402

page_file, out_file = sys.argv[1:3]
LAT, LON, SIZE = 40.0465, -76.32, 160
m_lat, m_lon = p.metres_per_degree(LAT)
R = 6378137.0


def to_ll(x, z):
    return LAT - (z - SIZE / 2) / m_lat, LON + (x - SIZE / 2) / m_lon


def merc(lat, lon):
    return R * math.radians(lon), R * math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))


def ground_at(x, z):
    return 100 + 0.03 * x - 0.01 * z


rng = np.random.default_rng(3)
pts = []
for _ in range(SIZE * SIZE * 4):
    x, z = rng.random() * SIZE, rng.random() * SIZE
    gz = ground_at(x, z)
    if 40 <= x < 60 and 40 <= z < 52:
        pts.append((x, z, gz + 6 + 4 * (1 - abs(z - 46) / 6), 1, 1, 1))
    elif 90 <= x < 115 and 30 <= z < 55:
        pts.append((x, z, gz + (21 if 100 <= x < 104 and 38 <= z < 42 else 12), 1, 1, 1))
    elif 130 <= x < 140 and 70 <= z < 78:
        pts.append((x, z, gz + 7, 1, 1, 1))                      # a shed the map lacks
    elif math.hypot(x - 40, z - 110) < 5:
        d = math.hypot(x - 40, z - 110)
        pts.append((x, z, gz + 15 * (1 - 0.5 * (d / 5) ** 2), 1, 2, 1))
        pts.append((x, z, gz, 2, 2, 2))
    else:
        pts.append((x, z, gz, 1, 1, 2))
pts.append((10, 10, 400, 1, 1, 7))                                 # a bird
arr = np.array(pts)
lat, lon = to_ll(arr[:, 0], arr[:, 1])
mx = R * np.radians(lon)
my = R * np.log(np.tan(np.pi / 4 + np.radians(lat) / 2))

root = Path(tempfile.mkdtemp())
ept_dir = root / "FAKE"
(ept_dir / "ept-data").mkdir(parents=True)
(ept_dir / "ept-hierarchy").mkdir()
header = laspy.LasHeader(point_format=6, version="1.4")
header.scales = [0.01, 0.01, 0.01]
header.offsets = [float(mx.min()), float(my.min()), 0.0]
las = laspy.LasData(header)
las.x, las.y, las.z = mx, my, arr[:, 2]
las.return_number = arr[:, 3].astype(np.uint8)
las.number_of_returns = arr[:, 4].astype(np.uint8)
las.classification = arr[:, 5].astype(np.uint8)
las.write(ept_dir / "ept-data" / "0-0-0-0.laz", do_compress=True)
c = [(mx.min() + mx.max()) / 2, (my.min() + my.max()) / 2, 100]
half = max(mx.max() - mx.min(), my.max() - my.min()) / 2 + 10
(ept_dir / "ept.json").write_text(json.dumps({
    "bounds": [c[0] - half, c[1] - half, c[2] - half, c[0] + half, c[1] + half, c[2] + half],
    "dataType": "laszip", "hierarchyType": "json", "points": len(arr), "span": 128,
    "srs": {"authority": "EPSG", "horizontal": "3857"}, "version": "1.0.0"}))
(ept_dir / "ept-hierarchy" / "0-0-0-0.json").write_text(json.dumps({"0-0-0-0": len(arr)}))

# OpenStreetMap's answer: the house and the block (not the shed), and a road
nodes, ways, nid = [], [], 1
for wid, (tags, ring) in enumerate([({"building": "house"}, [(40, 40), (60, 40), (60, 52), (40, 52)]),
                                    ({"building": "university", "name": "Old Main"}, [(90, 30), (115, 30), (115, 55), (90, 55)]),
                                    ({"highway": "residential", "name": "College Avenue"}, [(0, 70), (160, 70)])], start=1):
    refs = []
    for x, z in ring:
        la, lo = to_ll(x, z)
        nodes.append(f'<node id="{nid}" lat="{la:.7f}" lon="{lo:.7f}"/>')
        refs.append(nid)
        nid += 1
    if "building" in tags:
        refs.append(refs[0])
    t = "".join(f'<tag k="{k}" v="{v}"/>' for k, v in tags.items())
    ways.append(f'<way id="{wid}">' + "".join(f'<nd ref="{r}"/>' for r in refs) + t + "</way>")
osm_xml = '<?xml version="1.0"?><osm version="0.6">' + "".join(nodes) + "".join(ways) + "</osm>"
calls = {"osm": 0, "ept": 0}


def serve_ept(route):
    calls["ept"] += 1
    rel = route.request.url.split("/FAKE/", 1)[1]
    f = ept_dir / rel
    body = f.read_bytes() if f.exists() else b"not found"
    route.fulfill(status=200 if f.exists() else 404, body=body, headers={"Access-Control-Allow-Origin": "*"})


def serve_osm(route):
    calls["osm"] += 1
    route.fulfill(status=200, body=osm_xml, headers={"Access-Control-Allow-Origin": "*", "Content-Type": "text/xml"})


with sync_playwright() as pw:
    b = pw.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-sandbox"])
    ctx = b.new_context(accept_downloads=True)
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: m.type == "error" and errors.append(m.text))
    ctx.route("https://usgs-lidar-public.s3.amazonaws.com/FAKE/**", serve_ept)
    ctx.route("https://api.openstreetmap.org/**", serve_osm)
    page.goto("file://" + str(Path(page_file).resolve()))
    page.fill("#lat", str(LAT))
    page.fill("#lon", str(LON))
    page.fill("#size", str(SIZE))
    page.fill("#ept", "https://usgs-lidar-public.s3.amazonaws.com/FAKE/")
    with page.expect_download(timeout=120000) as dl:
        page.click("#go")
    dl.value.save_as(out_file)
    print("page log:\n  " + page.inner_text("#log").strip().replace("\n", "\n  "))
    print("requests:", calls, "errors:", errors or "none")
    b.close()

elements, _ = p.load_osm_file(out_file)
print("map back:", sorted((e["type"], e.get("tags", {}).get("name", e.get("tags", {}).get("building"))) for e in elements))
L = p.load_lidar(out_file, LAT, LON, SIZE)
r = L["roof"]
print(f"ridge {r[46, 50]:.1f} m (10), tower {np.nanmedian(r[38:42, 100:104]):.1f} m (21), block {np.nanmedian(r[31:37, 91:99]):.1f} m (12)")
print(f"trees {np.round(L['trees'], 1).tolist()} (40.5, 110.5, 15)")
print(f"unmapped shed cells {int(np.isfinite(L['unmapped']).sum())} (80), its height {np.nanmedian(L['unmapped']):.1f} m (7)")
zz, xx = np.indices((SIZE, SIZE)) + 0.5
print(f"ground error max {np.abs(L['dtm'] - ground_at(xx, zz)).max():.3f} m")
