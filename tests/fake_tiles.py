"""Write Terrarium elevation tiles for a made-up landscape, for testing hills without internet.

usage: python -I tests/fake_tiles.py OUT_DIR LAT,LON SIZE_BLOCKS
The landscape: ground that rises inland to the north-west, with rolling bumps, in metres."""
import math
import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import place2mc as p


def landscape(lat, lon, lat0, lon0):
    m_lat, m_lon = p.metres_per_degree(lat0)
    north, east = (lat - lat0) * m_lat, (lon - lon0) * m_lon
    return 35 + 0.16 * north - 0.10 * east + 9 * np.sin(east / 85) * np.cos(north / 65)


def encode_terrarium(metres):
    v = np.asarray(metres, dtype=np.float64) + 32768
    return np.stack([np.floor(v / 256), np.floor(v % 256), np.floor((v - np.floor(v)) * 256)], -1).astype(np.uint8)


def write_tiles(out_dir, lat0, lon0, size, zoom=p.TILE_ZOOM):
    m_lat, m_lon = p.metres_per_degree(lat0)
    half = size / 2 + 300
    n = 2 ** zoom
    def tile_xy(lat, lon):
        lat_r = math.radians(lat)
        return int((lon + 180) / 360 * n), int((1 - math.log(math.tan(lat_r) + 1 / math.cos(lat_r)) / math.pi) / 2 * n)
    x0, y0 = tile_xy(lat0 + half / m_lat, lon0 - half / m_lon)
    x1, y1 = tile_xy(lat0 - half / m_lat, lon0 + half / m_lon)
    count = 0
    for tx in range(x0, x1 + 1):
        for ty in range(y0, y1 + 1):
            gx = (tx * 256 + np.arange(256) + 0.5) / (n * 256)            # pixel centres
            gy = (ty * 256 + np.arange(256) + 0.5) / (n * 256)
            lons = gx * 360 - 180
            lats = np.degrees(np.arctan(np.sinh(math.pi * (1 - 2 * gy))))
            tile = encode_terrarium(landscape(lats[:, None], lons[None, :], lat0, lon0))
            path = Path(out_dir) / str(zoom) / str(tx) / f"{ty}.png"
            path.parent.mkdir(parents=True, exist_ok=True)
            Image.fromarray(tile, "RGB").save(path)
            count += 1
    return count


if __name__ == "__main__":
    lat0, lon0 = (float(v) for v in sys.argv[2].split(","))
    print("tiles written:", write_tiles(sys.argv[1], lat0, lon0, int(sys.argv[3])))
