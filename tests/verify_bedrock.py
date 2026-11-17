"""Check a .mcworld written by place2mc: read the database back, decode every block, and compare with the
volume the converter built; check every palette entry is a valid Bedrock state; and compare level.dat and the
chunk records with a reference Bedrock world (for example one made by Arnis).

usage: python -I tests/verify_bedrock.py OSM_FILE LAT,LON RADIUS MCWORLD MCDATA_DIR [REFERENCE_MCWORLD] [TILE_CACHE]
"""
import io
import json
import struct
import sys
import tempfile
import zipfile
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import nbtlib
import numpy as np
import place2mc as p
import bedrock
import read_leveldb

osm, center, radius, mcworld, mcdata = sys.argv[1:6]
reference = sys.argv[6] if len(sys.argv) > 6 and sys.argv[6] != "-" else None
tiles = sys.argv[7] if len(sys.argv) > 7 else None
lat, lon = (float(v) for v in center.split(","))
size = -(-2 * int(radius) // 32) * 32
els, _ = p.load_osm_file(osm)
ground, bmap, specs, trees, _ = p.build_map(els, lat, lon, size)
terrain = p.make_terrain(lat, lon, size, ground, tiles) if tiles else None
vol, _ = p.build_volume(ground, bmap, specs, trees, terrain=terrain, details=p.build_map.details)
biomes = p.biome_map(ground)


def unpack(path):
    folder = Path(tempfile.mkdtemp())
    zipfile.ZipFile(path).extractall(folder)
    return folder


ours = unpack(mcworld)
db = read_leveldb.read_db(ours / "db")                      # also checks every block checksum
print(f"database: {len(db)} records, every block checksum correct")

# 1. every palette entry is a real Bedrock state
valid = set()
for s in json.loads((Path(mcdata) / "bedrock" / "1.21.120" / "blockStates.json").read_text()):
    valid.add((s["name"], tuple(sorted((k, v["value"]) for k, v in s["states"].items())), s["version"]))
entries = bedrock.load_table()
lookup = {}
for i, e in enumerate(entries):
    key = (e["name"].split(":")[1], tuple(sorted((k, v) for k, (_, v) in e["states"].items())))
    lookup.setdefault(key, i)

# 2. decode every sub-chunk and compare with the volume
offset = size // 32
seen_palette, checked, wrong, chunks = set(), 0, 0, set()
for key, value in db.items():
    if len(key) != 10 or key[8] != 47:
        continue
    cx, cz = struct.unpack("<ii", key[:8])
    y_index = struct.unpack("b", key[9:10])[0]
    assert value[0] == 9 and value[1] == 1 and struct.unpack("b", value[2:3])[0] == y_index
    bits = value[3] >> 1
    per = 32 // bits
    nwords = -(-4096 // per)
    words = np.frombuffer(value[4:4 + 4 * nwords], dtype="<u4")
    shifts = np.arange(per, dtype=np.uint32) * np.uint32(bits)
    idx = ((words[:, None] >> shifts[None, :]) & np.uint32((1 << bits) - 1)).ravel()[:4096]
    pos = 4 + 4 * nwords
    count = struct.unpack("<I", value[pos:pos + 4])[0]
    buf = io.BytesIO(value[pos + 4:])
    pal = []
    for _ in range(count):
        e = nbtlib.File.parse(buf, byteorder="little")         # an independent NBT reader
        name = str(e["name"]).split(":")[1]
        states = tuple(sorted((str(k), (int(v) if isinstance(v, (nbtlib.Byte, nbtlib.Int)) else str(v)))
                              for k, v in e["states"].items()))
        assert (name, states, int(e["version"])) in valid, f"not a valid Bedrock state: {name} {states}"
        seen_palette.add(name)
        pal.append(lookup[(name, states)])
    got = np.array(pal)[idx].reshape(16, 16, 16).transpose(2, 1, 0)   # x, z, y -> y, z, x
    s = y_index - bedrock.MIN_SECTION
    want = vol[s * 16:(s + 1) * 16, (cz + offset) * 16:(cz + offset + 1) * 16, (cx + offset) * 16:(cx + offset + 1) * 16]
    # the volume's block numbers can differ from the decoded ones only where two of ours share a Bedrock state
    same = np.array([entries[a] == entries[b] for a, b in zip(got.ravel(), want.ravel())]).reshape(got.shape)
    wrong += int((~same).sum())
    checked += 4096
    chunks.add((cx, cz))
expected_chunks = (size // 16) ** 2
nonempty = sum(1 for s in range(vol.shape[0] // 16) for cz in range(size // 16) for cx in range(size // 16)
               if vol[s * 16:(s + 1) * 16, cz * 16:(cz + 1) * 16, cx * 16:(cx + 1) * 16].any())
subchunks = sum(1 for k in db if len(k) == 10 and k[8] == 47)
print(f"sub-chunks: {subchunks} (expected {nonempty}), blocks compared: {checked}, wrong: {wrong}")
print(f"palette entries all valid Bedrock 1.21 states; {len(seen_palette)} different blocks used")
assert wrong == 0 and subchunks == nonempty

# 3. the other chunk records, and the heights and biomes
tags = Counter(k[8] for k in db if len(k) == 9)
print("per-chunk records:", dict(tags), "for", expected_chunks, "chunks")
assert tags[44] == tags[54] == tags[43] == expected_chunks
assert all(v == bytes([bedrock.CHUNK_VERSION]) for k, v in db.items() if len(k) == 9 and k[8] == 44)
assert all(v == struct.pack("<i", 2) for k, v in db.items() if len(k) == 9 and k[8] == 54)
bad_height = 0
for k, v in db.items():
    if len(k) == 9 and k[8] == 43:
        cx, cz = struct.unpack("<ii", k[:8])
        heights = np.frombuffer(v[:512], dtype="<i2").reshape(16, 16)
        col = vol[:, (cz + offset) * 16:(cz + offset + 1) * 16, (cx + offset) * 16:(cx + offset + 1) * 16]
        nonair = col != p.AIR
        top = col.shape[0] - 1 - np.argmax(nonair[::-1], axis=0)
        want = np.where(nonair.any(axis=0), top + 1 + p.Y_MIN, 0)
        bad_height += int((heights != want).sum())
        assert v[512] & 1 == 1, "biome storage must be in the runtime-id form"
        assert v[-(bedrock.MAX_SECTION - bedrock.MIN_SECTION):] == b"\xff" * (bedrock.MAX_SECTION - bedrock.MIN_SECTION)
print("heightmap values wrong:", bad_height)
assert bad_height == 0

# 4. level.dat, and the comparison with the reference world
raw = (ours / "level.dat").read_bytes()
version, length = struct.unpack("<ii", raw[:8])
level = nbtlib.File.parse(io.BytesIO(raw[8:]), byteorder="little")
assert version == 10 and length == len(raw) - 8
print(f"level.dat: {len(level)} fields, name {str(level['LevelName'])!r}, spawn "
      f"{int(level['SpawnX'])} {int(level['SpawnY'])} {int(level['SpawnZ'])}, game mode {int(level['GameType'])}")
if reference:
    ref = unpack(reference)
    rraw = (ref / "level.dat").read_bytes()
    rlevel = nbtlib.File.parse(io.BytesIO(rraw[8:]), byteorder="little")
    diff = [k for k in set(level) | set(rlevel) if k not in level or k not in rlevel or type(level[k]) is not type(rlevel[k])]
    print("level.dat fields that differ in name or type from the reference:", diff or "none")
    rdb = read_leveldb.read_db(ref / "db")
    rtags = Counter(k[8] for k in rdb if len(k) == 9)
    rver = {v for k, v in rdb.items() if len(k) == 9 and k[8] == 44}
    print("reference per-chunk records:", dict(rtags), "| chunk version", rver, "| ours", {bytes([bedrock.CHUNK_VERSION])})
    sample = next(v for k, v in rdb.items() if len(k) == 10 and k[8] == 47)
    print("reference sub-chunk header:", sample[:4].hex(), "| ours:", next(v for k, v in db.items() if len(k) == 10 and k[8] == 47)[:4].hex())
    files = sorted(x.name for x in ours.iterdir()), sorted(x.name for x in ref.iterdir())
    print("files in the .mcworld: ours", files[0], "| reference", files[1])
print("Bedrock world OK")
