"""Read the blocks of any Minecraft Java world (1.18+ chunk format) back into a 3D array.

Used to compare worlds made by different tools on the same map. Returns (names, vol, origin):
names is the list of block states (name, frozen properties) indexed by the numbers in vol[y, z, x].
"""
import io
import struct
import zlib
from pathlib import Path

import nbtlib
import numpy as np

KEEP_PROPS = {"facing", "half", "type", "hinge", "open", "axis"}     # the properties that change a block's shape


def chunks_of(world):
    for path in sorted((Path(world) / "region").glob("r.*.mca")):
        raw = path.read_bytes()
        for i in range(1024):
            entry = struct.unpack(">I", raw[4 * i:4 * i + 4])[0]
            if not entry:
                continue
            off = entry >> 8
            length, comp = struct.unpack(">IB", raw[off * 4096:off * 4096 + 5])
            data = raw[off * 4096 + 5:off * 4096 + 4 + length]
            data = zlib.decompress(data) if comp == 2 else __import__("gzip").decompress(data)
            yield nbtlib.File.parse(io.BytesIO(data))


def load(world, area=None):
    """area: (x0, z0, x1, z1) in block coordinates to keep, or None for every chunk present."""
    sections, names, index = [], [], {}
    for root in chunks_of(world):
        cx, cz = int(root["xPos"]), int(root["zPos"])
        if area and not (area[0] <= cx * 16 + 15 and cx * 16 <= area[2] and area[1] <= cz * 16 + 15 and cz * 16 <= area[3]):
            continue
        for sec in root.get("sections", []):
            bs = sec.get("block_states")
            if bs is None:
                continue
            pal = []
            for e in bs["palette"]:
                props = tuple(sorted((str(k), str(v)) for k, v in e.get("Properties", {}).items() if str(k) in KEEP_PROPS))
                key = (str(e["Name"]), props)
                if key not in index:
                    index[key] = len(names)
                    names.append(key)
                pal.append(index[key])
            pal = np.array(pal, dtype=np.int32)
            if len(pal) == 1:
                blocks = np.full(4096, pal[0], dtype=np.int32)
            else:
                bits = max(4, (len(pal) - 1).bit_length())
                per = 64 // bits
                longs = np.array([int(v) & (2 ** 64 - 1) for v in bs["data"]], dtype=np.uint64)
                shifts = (np.arange(per, dtype=np.uint64) * np.uint64(bits))
                idx = ((longs[:, None] >> shifts[None, :]) & np.uint64((1 << bits) - 1)).ravel()[:4096]
                blocks = pal[idx.astype(np.int64)]
            sections.append((cx, cz, int(sec["Y"]), blocks.reshape(16, 16, 16)))
    xs = [s[0] for s in sections]
    zs = [s[1] for s in sections]
    ys = [s[2] for s in sections]
    x0, z0, y0 = min(xs) * 16, min(zs) * 16, min(ys) * 16
    vol = np.zeros(((max(ys) + 1) * 16 - y0, (max(zs) + 1) * 16 - z0, (max(xs) + 1) * 16 - x0), dtype=np.int32)
    air = index.get(("minecraft:air", ()))
    if air is None:
        air = len(names)
        names.append(("minecraft:air", ()))
    vol[:] = air
    for cx, cz, sy, b in sections:
        vol[sy * 16 - y0:sy * 16 - y0 + 16, cz * 16 - z0:cz * 16 - z0 + 16, cx * 16 - x0:cx * 16 - x0 + 16] = b
    return names, vol, (x0, y0, z0)
