"""
Overture Maps for place2mc: what OpenStreetMap does not have yet.

  * buildings the map is missing (Overture adds footprints found in aerial imagery by Microsoft and Google,
    and Esri's community maps), with height, floors, roof shape, roof and wall colour and material when known
  * heights and floor counts for OpenStreetMap buildings that have none
  * land cover from ESA WorldCover (forest, shrubs, crops, bare ground) where the map says nothing

Overture (overturemaps.org) publishes every release as PMTiles archives of vector tiles (buildings: ODbL;
land cover: ESA WorldCover, CC BY 4.0). This reads only the tiles an area needs, with HTTP range requests, and
keeps them in a cache. The approach follows Arnis (github.com/louis-e/arnis, Apache-2.0); the code is our own.
"""
import gzip
import json
import math
import re
import struct
import urllib.request
from pathlib import Path
from xml.etree import ElementTree as ET

LIST_URL = "https://overturemaps-us-west-2.s3.amazonaws.com/?list-type=2&prefix=release/&delimiter=/"
TILES_URL = "https://overturemaps-extras-us-west-2.s3.us-west-2.amazonaws.com/tiles/{release}/{name}.pmtiles"
FALLBACK_RELEASE = "2026-10-21.0"
USER_AGENT = "place2mc (hobby project; github.com/AstroAli5/place2mc)"
BUILDING_ZOOM, BASE_ZOOM = 14, 13
# land cover kinds worth drawing (grass and built-up land look like the default ground already)
LAND_COVER = {"forest", "mangrove", "shrub", "wetland", "crop", "barren"}


def http_range(url, start, length, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Range": f"bytes={start}-{start + length - 1}"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read()
    if len(data) != length:
        raise IOError(f"asked for {length} bytes of {url}, got {len(data)}")
    return data


def latest_release(timeout=20):
    """The newest Overture release (they are named like 2026-10-21.0), or a known one if the list cannot be read."""
    try:
        req = urllib.request.Request(LIST_URL, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            root = ET.fromstring(resp.read())
        names = [el.text.strip("/").split("/")[-1] for el in root.iter() if el.tag.endswith("Prefix") and el.text
                 and re.fullmatch(r"release/\d{4}-\d\d-\d\d\.\d+/", el.text)]
        if names:
            return max(names, key=lambda n: (n.split(".")[0], int(n.split(".")[1])))
    except Exception:
        pass
    return FALLBACK_RELEASE


# --------------------------------------------------------------------------- PMTiles (version 3)

def _varint(buf, i):
    result = shift = 0
    while True:
        b = buf[i]
        i += 1
        result |= (b & 0x7F) << shift
        if b < 0x80:
            return result, i
        shift += 7


def decode_directory(buf):
    """A PMTiles directory: list of (tile_id, offset, length, run_length); run_length 0 means a leaf directory."""
    n, i = _varint(buf, 0)
    ids, last = [], 0
    for _ in range(n):
        d, i = _varint(buf, i)
        last += d
        ids.append(last)
    runs, lengths, offsets = [], [], []
    for out in (runs, lengths):
        for _ in range(n):
            v, i = _varint(buf, i)
            out.append(v)
    for k in range(n):
        v, i = _varint(buf, i)
        offsets.append(offsets[k - 1] + lengths[k - 1] if v == 0 and k > 0 else v - 1)
    return list(zip(ids, offsets, lengths, runs))


def zxy_to_tile_id(z, x, y):
    """Tiles are numbered zoom by zoom, along a Hilbert curve within each zoom (the PMTiles specification)."""
    acc = ((1 << (2 * z)) - 1) // 3
    d, s = 0, (1 << z) >> 1
    while s > 0:
        rx = 1 if x & s else 0
        ry = 1 if y & s else 0
        d += s * s * ((3 * rx) ^ ry)
        if ry == 0:
            if rx == 1:
                x, y = s - 1 - x, s - 1 - y
            x, y = y, x
        s >>= 1
    return acc + d


def _find(entries, tile_id):
    lo, hi = 0, len(entries) - 1
    while lo <= hi:
        mid = (lo + hi) // 2
        if entries[mid][0] < tile_id:
            lo = mid + 1
        elif entries[mid][0] > tile_id:
            hi = mid - 1
        else:
            return entries[mid]
    if hi >= 0:
        e = entries[hi]
        if e[3] == 0 or tile_id < e[0] + e[3]:
            return e
    return None


def _decompress(data, kind):
    if kind == 2:
        return gzip.decompress(data)
    if kind in (0, 1):
        return data
    raise ValueError(f"PMTiles compression {kind} is not supported")


class PMTiles:
    """Read single tiles from a PMTiles archive at a URL (or a local path), caching what it downloads."""

    def __init__(self, url, cache_dir=None, fetch=http_range):
        self.url, self.fetch = url, fetch
        self.cache = Path(cache_dir) if cache_dir else None
        head = self._read(0, 127, "header")
        if head[:7] != b"PMTiles" or head[7] != 3:
            raise ValueError(f"{url} is not a PMTiles version 3 archive")
        (self.root_off, self.root_len, _, _, self.leaf_off, _, self.data_off, _) = struct.unpack_from("<8Q", head, 8)
        self.internal_comp, self.tile_comp, self.tile_type, self.min_zoom, self.max_zoom = head[97:102]
        self.root = decode_directory(_decompress(self._read(self.root_off, self.root_len, "root"), self.internal_comp))

    def _read(self, offset, length, name):
        path = self.cache / f"{name}-{offset}-{length}.bin" if self.cache else None
        if path and path.exists():
            return path.read_bytes()
        if "://" not in self.url:
            with open(self.url, "rb") as fh:
                fh.seek(offset)
                data = fh.read(length)
        else:
            data = self.fetch(self.url, offset, length)
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        return data

    def tile(self, z, x, y):
        """The tile's bytes (decompressed), or None if the archive has no such tile."""
        tile_id, entries = zxy_to_tile_id(z, x, y), self.root
        for _ in range(4):                                      # root, then at most three levels of leaf directories
            e = _find(entries, tile_id)
            if e is None:
                return None
            _, offset, length, run = e
            if run:
                return _decompress(self._read(self.data_off + offset, length, "tile"), self.tile_comp)
            entries = decode_directory(_decompress(self._read(self.leaf_off + offset, length, "leaf"), self.internal_comp))
        return None


# --------------------------------------------------------------------------- Mapbox vector tiles

def _fields(buf):
    i, n = 0, len(buf)
    while i < n:
        key, i = _varint(buf, i)
        number, wire = key >> 3, key & 7
        if wire == 0:
            value, i = _varint(buf, i)
        elif wire == 1:
            value, i = buf[i:i + 8], i + 8
        elif wire == 2:
            length, i = _varint(buf, i)
            value, i = buf[i:i + length], i + length
        elif wire == 5:
            value, i = buf[i:i + 4], i + 4
        else:
            raise ValueError(f"unsupported protobuf wire type {wire}")
        yield number, wire, value


def _packed(buf):
    out, i = [], 0
    while i < len(buf):
        v, i = _varint(buf, i)
        out.append(v)
    return out


def _value(buf):
    for number, _, v in _fields(buf):
        if number == 1:
            return bytes(v).decode("utf-8", "replace")
        if number == 2:
            return struct.unpack("<f", v)[0]
        if number == 3:
            return struct.unpack("<d", v)[0]
        if number == 4:
            return v - (1 << 64) if v >= 1 << 63 else v
        if number == 5:
            return v
        if number == 6:
            return (v >> 1) ^ -(v & 1)
        if number == 7:
            return bool(v)
    return None


def _rings(geometry):
    """Geometry commands -> list of rings (lists of (x, y) in tile units)."""
    rings, ring, x, y, i = [], [], 0, 0, 0
    while i < len(geometry):
        command, count = geometry[i] & 7, geometry[i] >> 3
        i += 1
        if command in (1, 2):
            for _ in range(count):
                dx, dy = geometry[i], geometry[i + 1]
                i += 2
                x += (dx >> 1) ^ -(dx & 1)
                y += (dy >> 1) ^ -(dy & 1)
                if command == 1 and ring:
                    rings.append(ring)
                    ring = []
                ring.append((x, y))
        elif command == 7:
            if ring:
                rings.append(ring + [ring[0]])
                ring = []
    if ring:
        rings.append(ring)
    return rings


def _area2(ring):
    return sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(ring, ring[1:]))


def decode_tile(data):
    """{layer name: (extent, [(id, properties, [polygon: [outer ring, inner rings...]]), ...])} for polygon features."""
    layers = {}
    for number, _, layer_buf in _fields(data):
        if number != 3:
            continue
        name, extent, keys, values, raw = "", 4096, [], [], []
        for f, _, v in _fields(layer_buf):
            if f == 1:
                name = bytes(v).decode()
            elif f == 2:
                raw.append(v)
            elif f == 3:
                keys.append(bytes(v).decode())
            elif f == 4:
                values.append(_value(v))
            elif f == 5:
                extent = v
        features = []
        for feature_buf in raw:
            fid, tags, ftype, geometry = None, [], 0, []
            for f, _, v in _fields(feature_buf):
                if f == 1:
                    fid = v
                elif f == 2:
                    tags = _packed(v)
                elif f == 3:
                    ftype = v
                elif f == 4:
                    geometry = _packed(v)
            if ftype != 3:                                      # polygons only
                continue
            props = {keys[tags[k]]: values[tags[k + 1]] for k in range(0, len(tags) - 1, 2)}
            polygons = []
            for ring in _rings(geometry):
                area = _area2(ring)
                if area > 0 or not polygons:                    # exterior rings have a positive area (MVT 2.1)
                    polygons.append([ring])
                elif area < 0:
                    polygons[-1].append(ring)
            features.append((fid, props, polygons))
        layers[name] = (extent, features)
    return layers


# --------------------------------------------------------------------------- tiles of an area, as map objects

def tile_range(lat0, lon0, radius, z):
    m_lat = 111_320.0
    m_lon = 111_320.0 * math.cos(math.radians(lat0))
    s, n = lat0 - radius / m_lat, lat0 + radius / m_lat
    w, e = lon0 - radius / m_lon, lon0 + radius / m_lon
    count = 1 << z

    def tx(lon):
        return min(count - 1, max(0, int((lon + 180) / 360 * count)))

    def ty(lat):
        r = math.radians(lat)
        return min(count - 1, max(0, int((1 - math.log(math.tan(r) + 1 / math.cos(r)) / math.pi) / 2 * count)))
    return [(x, y) for x in range(tx(w), tx(e) + 1) for y in range(ty(n), ty(s) + 1)], (s, w, n, e)


def _to_latlon(z, tx, ty, extent, x, y):
    count = 1 << z
    lon = (tx + x / extent) / count * 360 - 180
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (ty + y / extent) / count))))
    return {"lat": round(lat, 7), "lon": round(lon, 7)}


def _element(new_id, tags, polygons, z, tx, ty, extent):
    geo = [[[_to_latlon(z, tx, ty, extent, x, y) for x, y in ring] for ring in poly] for poly in polygons]
    if len(geo) == 1 and len(geo[0]) == 1:
        return {"type": "way", "id": new_id, "tags": tags, "geometry": geo[0][0]}
    members = [{"type": "way", "role": "outer" if k == 0 else "inner", "geometry": ring}
               for poly in geo for k, ring in enumerate(poly)]
    return {"type": "relation", "id": new_id, "tags": dict(tags, type="multipolygon"), "members": members}


OSM_RECORD = re.compile(r"^([nwr])(\d+)")


def buildings_from_tiles(tiles, z=BUILDING_ZOOM):
    """tiles: [(x, y, decoded tile)]. Returns (new building map objects, {"way/123": {"height": .., "building:levels": ..}}
    for OpenStreetMap buildings that Overture knows a height for)."""
    best, heights = {}, {}
    for tx, ty, layers in tiles:
        extent, features = layers.get("building", (4096, []))
        for fid, props, polygons in features:
            if not polygons or props.get("is_underground") is True:
                continue
            key = props.get("id") or fid
            sources = props.get("sources")
            try:
                sources = json.loads(sources) if isinstance(sources, str) else sources or []
            except ValueError:
                sources = []
            from_osm = str(props.get("@geometry_source", "")).lower() == "openstreetmap" or (
                "@geometry_source" not in props and any(str(s.get("dataset", "")).lower() == "openstreetmap"
                                                        for s in sources if isinstance(s, dict)))
            if from_osm:
                for s in sources if isinstance(sources, list) else []:
                    m = OSM_RECORD.match(str(s.get("record_id", ""))) if isinstance(s, dict) else None
                    if m and str(s.get("dataset", "")).lower() == "openstreetmap":
                        extra = {}
                        if isinstance(props.get("height"), (int, float)) and props["height"] > 0:
                            extra["height"] = f"{props['height']:.1f}"
                        if isinstance(props.get("num_floors"), (int, float)) and props["num_floors"] > 0:
                            extra["building:levels"] = str(int(props["num_floors"]))
                        if extra:
                            heights[f"{ {'n': 'node', 'w': 'way', 'r': 'relation'}[m.group(1)] }/{m.group(2)}"] = extra
                continue
            size = sum(abs(_area2(poly[0])) for poly in polygons)
            if key not in best or size > best[key][0]:                  # a building cut by tile edges: keep the biggest piece
                best[key] = (size, props, polygons, tx, ty, extent)
    out = []
    for n, (_, props, polygons, tx, ty, extent) in enumerate(best.values()):
        tags = {"building": str(props.get("class") or "yes"), "source:place2mc": "overture"}
        for ours, theirs in (("height", "height"), ("roof:shape", "roof_shape"), ("roof:colour", "roof_color"),
                             ("building:colour", "facade_color"), ("building:material", "facade_material"),
                             ("roof:material", "roof_material")):
            v = props.get(theirs)
            if v not in (None, ""):
                tags[ours] = f"{v:.1f}" if isinstance(v, float) else str(v)
        if isinstance(props.get("num_floors"), (int, float)) and props["num_floors"] > 0:
            tags["building:levels"] = str(int(props["num_floors"]))
        out.append(_element(9_000_000_000_000 + n, tags, polygons, z, tx, ty, extent))
    return out, heights


def land_cover_from_tiles(tiles, z=BASE_ZOOM):
    out = []
    for tx, ty, layers in tiles:
        extent, features = layers.get("land_cover", (4096, []))
        for fid, props, polygons in features:
            kind = props.get("subtype")
            if kind in LAND_COVER and polygons:
                out.append(_element(9_500_000_000_000 + len(out), {"place2mc:land_cover": kind}, polygons, z, tx, ty, extent))
    return out


def fetch(lat, lon, radius, cache_dir=None, release=None, log=print, get=http_range):
    """Overture buildings and land cover for the square of half-side `radius` metres around lat, lon.
    Returns (map objects to add, heights for OpenStreetMap buildings)."""
    release = release or latest_release()
    cache = Path(cache_dir) / "overture" / release if cache_dir else None
    elements, heights = [], {}
    for name, z in (("buildings", BUILDING_ZOOM), ("base", BASE_ZOOM)):
        archive = PMTiles(TILES_URL.format(release=release, name=name), cache / name if cache else None, get)
        cells, _ = tile_range(lat, lon, radius + 50, z)
        decoded = []
        for x, y in cells:
            data = archive.tile(z, x, y)
            if data:
                decoded.append((x, y, decode_tile(data)))
        if name == "buildings":
            found, heights = buildings_from_tiles(decoded, z)
            log(f"Overture {release}: {len(found)} buildings the map does not have, heights for {len(heights)} it has")
        else:
            found = land_cover_from_tiles(decoded, z)
            log(f"Overture {release}: {len(found)} areas of forest, shrubs, crops and bare ground (ESA WorldCover)")
        elements += found
    return elements, heights


def add_heights(elements, heights):
    """Give OpenStreetMap buildings without a height the one Overture knows (never replaces a mapped value)."""
    n = 0
    for el in elements:
        extra = heights.get(f"{el.get('type')}/{el.get('id')}")
        tags = el.get("tags") or {}
        if extra and not any(k in tags for k in ("height", "building:height", "building:levels")):
            tags.update(extra)
            n += 1
    return n
