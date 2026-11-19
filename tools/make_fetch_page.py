"""Put app/lidar.js, the laz-perf decoder (Apache-2.0) and its WebAssembly into one page (app/fetch-template.html)
that gets a place's map and laser scan in any browser and saves place2mc-data.json for --osm-file and --lidar.

    python tools/make_fetch_page.py LAT LON SIZE EPT_URL OUT.html [path/to/laz-perf/lib/web]"""
import base64
import re
import sys
from pathlib import Path

repo = Path(__file__).resolve().parents[1]
lp = Path(sys.argv[6]) if len(sys.argv) > 6 else repo / "node_modules/laz-perf/lib/web"   # npm install laz-perf@0.0.6
lat, lon, size, ept, out = sys.argv[1:6]
page = (repo / "app/fetch-template.html").read_text()
lidar = (repo / "app/lidar.js").read_text()
lidar = re.sub(r"^export ", "", lidar, flags=re.M)              # one module: no exports needed
lazperf = (lp / "laz-perf.js").read_text()
lazperf = lazperf.replace("module.exports = createLazPerf;", "0;")   # a classic script: createLazPerf is a global
wasm = base64.b64encode((lp / "laz-perf.wasm").read_bytes()).decode()
for key, value in (("__LAT__", lat), ("__LON__", lon), ("__SIZE__", size), ("__EPT__", ept)):
    page = page.replace(key, value)
page = page.replace("__LAZPERF__", lazperf).replace("__LIDARJS__", lidar).replace("__WASM__", wasm)
Path(out).write_text(page)
print(f"{out}: {len(page) / 1e3:.0f} kB")
