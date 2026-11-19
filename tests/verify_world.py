"""Check a world written by place2mc with two independent readers (anvil-parser2 and nbtlib).

usage: python -I tests/verify_world.py OSM_FILE LAT,LON RADIUS WORLD_DIR [TILE_CACHE]
       (give TILE_CACHE if the world was built on hills from that elevation tile folder)
"""
import os
import io, random, struct, sys, zlib
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import numpy as np
import json
import anvil, nbtlib
import place2mc as p

osm_file, center, radius, world = sys.argv[1], sys.argv[2], int(sys.argv[3]), Path(sys.argv[4])
tile_cache = sys.argv[5] if len(sys.argv) > 5 else None
lat, lon = (float(v) for v in center.split(","))
size = -(-2 * radius // 32) * 32
elements, _ = p.load_osm_file(osm_file)
if os.environ.get("OVERTURE"):      # the --overture-file the world was made with
    import json, overture
    _saved = json.loads(open(os.environ["OVERTURE"]).read())
    overture.add_heights(elements, _saved.get("heights", {}))
    elements = elements + _saved["elements"]
if os.environ.get("TAGS"):          # the --tags file the world was made with
    p.apply_tag_file(elements, os.environ["TAGS"])
ground, bmap, specs, trees, counts = p.build_map(elements, lat, lon, size)
def cache_only(z, x, y, cache_dir):
    path = Path(cache_dir) / str(z) / str(x) / f"{y}.png"
    if not path.exists():
        raise FileNotFoundError(path)
    return p.fetch_tile(z, x, y, cache_dir)

lidar = p.prepare_lidar(os.environ["LIDAR"], lat, lon, size, ground, bmap, specs)[0] if os.environ.get("LIDAR") else None
if lidar is not None:                # the --lidar file the world was made with: the scan's ground, roofs and trees
    terrain = p.make_terrain(lat, lon, size, ground, elev=lidar["dtm"])
else:
    terrain = p.make_terrain(lat, lon, size, ground, tile_cache, fetch=cache_only) if tile_cache else None
vol, planted = p.build_volume(ground, bmap, specs, trees, terrain=terrain, details=p.build_map.details, lidar=lidar)
biomes = p.biome_map(ground)
half = size // 2
print("expected volume", vol.shape, "non-air blocks", int((vol != p.AIR).sum()))

# 1. every chunk parses with nbtlib and has the expected structure
n_chunks = 0
single = {}     # (cx, cz, section y) -> the one block that fills a section stored without a data array
sign_text = {}  # (x, y, z) world -> the four lines, from the block entities
for path in sorted((world / "region").glob("r.*.mca")):
    raw = path.read_bytes()
    assert len(raw) % 4096 == 0, "region file is not a whole number of sectors"
    rx, rz = (int(v) for v in path.name.split(".")[1:3])
    for i in range(1024):
        entry = struct.unpack(">I", raw[4 * i:4 * i + 4])[0]
        if not entry:
            continue
        off, cnt = entry >> 8, entry & 0xFF
        length, comp = struct.unpack(">IB", raw[off * 4096:off * 4096 + 5])
        assert comp == 2 and length <= cnt * 4096
        data = zlib.decompress(raw[off * 4096 + 5:off * 4096 + 4 + length])
        root = nbtlib.File.parse(io.BytesIO(data))
        assert int(root["DataVersion"]) == p.DATA_VERSION and str(root["Status"]) == "minecraft:full"
        assert int(root["xPos"]) == rx * 32 + i % 32 and int(root["zPos"]) == rz * 32 + i // 32, "chunk in wrong slot"
        cx, cz = int(root["xPos"]), int(root["zPos"])
        for be in root["block_entities"]:
            assert str(be["id"]) == "minecraft:sign", be
            x, y, z = int(be["x"]), int(be["y"]), int(be["z"])
            assert x >> 4 == cx and z >> 4 == cz, "block entity in the wrong chunk"
            sign_text[(x, y, z)] = ([json.loads(str(be[f"Text{i}"]))["text"] for i in range(1, 5)], str(be["Color"]))
        want_biomes = biomes[(cz + size // 32) * 4:(cz + size // 32) * 4 + 4, (cx + size // 32) * 4:(cx + size // 32) * 4 + 4]
        for sec in root["sections"]:
            pal = sec["block_states"]["palette"]
            if len(pal) > 1:
                bits = max(4, (len(pal) - 1).bit_length())
                assert len(sec["block_states"]["data"]) == -(-4096 // (64 // bits)), "wrong data length"
            else:
                assert "data" not in sec["block_states"]
                entry = pal[0]
                single[(cx, cz, int(sec["Y"]))] = (str(entry["Name"]),
                                                   {str(k): str(v) for k, v in entry.get("Properties", {}).items()})
            # biomes: decode the 4x4x4 cells and compare with the biome map
            bpal = [str(b) for b in sec["biomes"]["palette"]]
            if len(bpal) > 1:
                bits = (len(bpal) - 1).bit_length()
                per = 64 // bits
                longs = [int(v) & (2 ** 64 - 1) for v in sec["biomes"]["data"]]
                assert len(longs) == -(-64 // per), "wrong biome data length"
                cells = [bpal[(longs[i // per] >> (bits * (i % per))) & ((1 << bits) - 1)] for i in range(64)]
            else:
                assert "data" not in sec["biomes"]
                cells = bpal * 64
            want = [p.BIOMES[int(b)] for b in want_biomes.ravel()] * 4
            assert cells == want, ("biomes differ", cx, cz)
        n_chunks += 1
print("nbtlib: parsed", n_chunks, "chunks, expected", (size // 16) ** 2, "| biomes match in every section")
# every sign block has its text, at the same place, and nothing else has any
want_text = {(x - size // 2, y + p.Y_MIN, z - size // 2): (lines, colour) for y, z, x, r, lines, colour in p.build_volume.signs}
assert sign_text == want_text, ("sign texts differ", len(sign_text), len(want_text))
sign_blocks = int(np.isin(vol, p.SIGNS + p.STOP_SIGNS + p.GIVE_WAY_SIGNS).sum())
assert sign_blocks == len(want_text), ("sign blocks without text", sign_blocks, len(want_text))
print(f"signs: {len(sign_text)} with their text in the chunks, e.g. {next(iter(sign_text.values()), None)}")
assert n_chunks == (size // 16) ** 2

# 2. blocks read back with anvil-parser2 match what the converter meant to place
random.seed(1)
regions, chunks, checked, by_name = {}, {}, 0, {}
def read(x, y, z):
    cx, cz = x >> 4, z >> 4
    # anvil-parser2 reads a section filled with one block (no data array) as air, so answer those from the palette
    if (cx, cz, y >> 4) in single:
        return single[(cx, cz, y >> 4)]
    if (cx, cz) not in chunks:
        key = (cx >> 5, cz >> 5)
        if key not in regions:
            regions[key] = anvil.Region.from_file(str(world / "region" / f"r.{key[0]}.{key[1]}.mca"))
        chunks[(cx, cz)] = regions[key].get_chunk(cx, cz)
    b = chunks[(cx, cz)].get_block(x & 15, y, z & 15)
    return f"{b.namespace}:{b.id}", dict(b.properties or {})
targets = [(random.randrange(-half, half), random.randrange(p.Y_MIN, p.Y_MIN + vol.shape[0]), random.randrange(-half, half)) for _ in range(30000)]
# plus every non-air block above the ground in a strip through the middle
zz, xx = np.nonzero(bmap[half - 8:half + 8] > 0)
targets += [(int(x) - half, y, int(z) - 8) for z, x in zip(zz, xx) for y in range(p.Y_MIN + p.FLOOR, p.Y_MIN + vol.shape[0])][:40000]
for x, y, z in targets:
    want_name, want_props = p.BLOCKS[int(vol[y - p.Y_MIN, z + half, x + half])]
    got_name, got_props = read(x, y, z)
    got_props = {k: str(v) for k, v in got_props.items()}
    assert got_name == want_name and got_props == (want_props or {}), (x, y, z, want_name, want_props, got_name, got_props)
    by_name[got_name] = by_name.get(got_name, 0) + 1
    checked += 1
print("anvil-parser2:", checked, "blocks checked, all match")
print({k.split(":")[1]: v for k, v in sorted(by_name.items(), key=lambda kv: -kv[1])})
