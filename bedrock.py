"""
Bedrock Edition export for place2mc: writes the same blocks as a .mcworld file (double-click to import).

A Bedrock world is a folder with level.dat (little-endian NBT behind an 8-byte header) and a LevelDB
database (db/) holding one record per chunk part. The layout follows Arnis (github.com/louis-e/arnis,
Apache-2.0), whose Bedrock worlds open in the game: chunk format 42, sub-chunk format 9, worlds saved as
Bedrock 1.21.120. Block names and states come from bedrock_blocks.json, made by
tests/make_bedrock_table.py from PrismarineJS/minecraft-data and checked against every valid Bedrock state.
"""
import json
import struct
import time
import zipfile
import zlib
from pathlib import Path

import numpy as np

import place2mc as p

SAVED_WITH = [1, 21, 120, 0, 0]
NETWORK_VERSION = 859            # the protocol of Bedrock 1.21.120
CHUNK_VERSION = 42               # chunk format of 1.21.120
MIN_SECTION, MAX_SECTION = -4, 19     # the overworld from y=-64 to y=319, in 16-block sections
BIOME_IDS = {"minecraft:plains": 1, "minecraft:forest": 4, "minecraft:river": 7, "minecraft:ocean": 0,
             "minecraft:beach": 16, "minecraft:savanna": 35, "minecraft:desert": 2, "minecraft:jungle": 21,
             "minecraft:sparse_jungle": 23, "minecraft:taiga": 5, "minecraft:snowy_plains": 12,
             "minecraft:snowy_taiga": 30}    # Bedrock's numbers: jungle_edge, ice_plains, cold_taiga in its own names
TABLE_FILE = Path(__file__).resolve().parent / "bedrock_blocks.json"

# --------------------------------------------------------------------------- little-endian NBT

_TYPES = {"byte": 1, "short": 2, "int": 3, "long": 4, "float": 5, "double": 6, "string": 8}


def _s(text):
    raw = text.encode("utf-8")
    return struct.pack("<H", len(raw)) + raw


def _value(kind, value):
    if kind == "byte":
        return struct.pack("<b", int(value))
    if kind == "short":
        return struct.pack("<h", int(value))
    if kind == "int":
        return struct.pack("<i", int(value))
    if kind == "long":
        return struct.pack("<q", int(value))
    if kind == "float":
        return struct.pack("<f", float(value))
    if kind == "double":
        return struct.pack("<d", float(value))
    if kind == "string":
        return _s(str(value))
    raise ValueError(kind)


def le_compound(fields, name=""):
    """fields: list of (name, kind, value); kind is one of _TYPES, "compound" (value = fields) or
    "list:int" (value = list of ints)."""
    out = b"\x0a" + _s(name)
    for key, kind, value in fields:
        if kind == "compound":
            out += le_compound(value, key)
        elif kind == "list:int":
            out += b"\x09" + _s(key) + b"\x03" + struct.pack("<i", len(value)) + b"".join(
                struct.pack("<i", v) for v in value)
        else:
            out += bytes([_TYPES[kind]]) + _s(key) + _value(kind, value)
    return out + b"\x00"


# --------------------------------------------------------------------------- LevelDB (the db folder)

def _crc32c_table():
    table = []
    for i in range(256):
        c = i
        for _ in range(8):
            c = (c >> 1) ^ 0x82F63B78 if c & 1 else c >> 1
        table.append(c)
    return table


_CRC = _crc32c_table()


def crc32c(data):
    crc = 0xFFFFFFFF
    for b in data:
        crc = _CRC[(crc ^ b) & 0xFF] ^ (crc >> 8)
    return crc ^ 0xFFFFFFFF


def _mask(crc):
    return ((((crc >> 15) | (crc << 17)) & 0xFFFFFFFF) + 0xA282EAD8) & 0xFFFFFFFF


def _varint(n):
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _block(entries, restart_every=16):
    """A LevelDB block of (key, value) pairs, keys already sorted, with shared-prefix keys and restarts."""
    out, restarts, last = bytearray(), [], b""
    for i, (key, value) in enumerate(entries):
        if i % restart_every == 0:
            restarts.append(len(out))
            shared = 0
        else:
            shared = 0
            for a, b in zip(last, key):
                if a != b:
                    break
                shared += 1
        out += _varint(shared) + _varint(len(key) - shared) + _varint(len(value)) + key[shared:] + value
        last = key
    for r in restarts or [0]:
        out += struct.pack("<I", r)
    out += struct.pack("<I", len(restarts or [0]))
    return bytes(out)


RAW_DEFLATE = 4      # the block compression Bedrock itself writes (Mojang's LevelDB: 2 = zlib, 4 = raw deflate)


def _with_trailer(contents):
    packer = zlib.compressobj(6, zlib.DEFLATED, -15)
    packed = packer.compress(contents) + packer.flush()
    return packed, packed + bytes([RAW_DEFLATE]) + struct.pack("<I", _mask(crc32c(packed + bytes([RAW_DEFLATE]))))


def _handle(offset, size):
    return _varint(offset) + _varint(size)


def write_table(path, items, block_size=16384):
    """One sorted table file. items: sorted list of (internal key, value)."""
    data, index = bytearray(), []
    batch, batch_size = [], 0
    for item in items + [None]:
        if batch and (item is None or batch_size >= block_size):
            packed, stored = _with_trailer(_block(batch))
            index.append((batch[-1][0], _handle(len(data), len(packed))))
            data += stored
            batch, batch_size = [], 0
        if item is None:
            break
        batch.append(item)
        batch_size += len(item[0]) + len(item[1])
    packed, stored = _with_trailer(_block([]))
    meta_handle = _handle(len(data), len(packed))
    data += stored
    packed, stored = _with_trailer(_block(index, restart_every=1))
    index_handle = _handle(len(data), len(packed))
    data += stored
    footer = meta_handle + index_handle
    data += footer + b"\x00" * (40 - len(footer)) + bytes.fromhex("57fb808b247547db")
    Path(path).write_bytes(bytes(data))
    return len(data)


def _log_record(payload):
    """One record in LevelDB's log format (used by the MANIFEST), in a single 32 KB block."""
    assert len(payload) + 7 <= 32768
    return struct.pack("<IHB", _mask(crc32c(b"\x01" + payload)), len(payload), 1) + payload


def write_leveldb(db_dir, records):
    """A complete LevelDB database holding records (dict of bytes key -> bytes value)."""
    db_dir = Path(db_dir)
    db_dir.mkdir(parents=True, exist_ok=True)
    keys = sorted(records)
    items = [(k + struct.pack("<Q", (seq << 8) | 1), records[k]) for seq, k in enumerate(keys, 1)]
    table_number, manifest_number, next_file = 2, 3, 5
    size = write_table(db_dir / f"{table_number:06d}.ldb", items)
    edit = (_varint(1) + _varint(len(b"leveldb.BytewiseComparator")) + b"leveldb.BytewiseComparator"
            + _varint(2) + _varint(4)                    # log number (no log file: nothing to replay)
            + _varint(3) + _varint(next_file)
            + _varint(4) + _varint(len(items))           # last sequence number
            + _varint(7) + _varint(0) + _varint(table_number) + _varint(size)
            + _varint(len(items[0][0])) + items[0][0] + _varint(len(items[-1][0])) + items[-1][0])
    (db_dir / f"MANIFEST-{manifest_number:06d}").write_bytes(_log_record(edit))
    (db_dir / "CURRENT").write_text(f"MANIFEST-{manifest_number:06d}\n")


# --------------------------------------------------------------------------- chunks

def load_table():
    table = json.loads(TABLE_FILE.read_text())
    entries = []
    for name, props in p.BLOCKS:
        key = name + "[" + ",".join(f"{k}={v}" for k, v in sorted((props or {}).items())) + "]"
        if key not in table:
            raise SystemExit(f"bedrock_blocks.json has no Bedrock version of {key}; run tests/make_bedrock_table.py")
        entries.append(table[key])
    return entries


def palette_entry(entry):
    fields = [("name", "string", entry["name"]),
              ("states", "compound", [(k, kind, v) for k, (kind, v) in sorted(entry["states"].items())]),
              ("version", "int", entry["version"])]
    return le_compound(fields)


def pack_words(index, bits):
    per_word = 32 // bits
    words = -(-4096 // per_word)
    padded = np.zeros(words * per_word, dtype=np.uint32)
    padded[:4096] = index
    shifts = np.arange(per_word, dtype=np.uint32) * np.uint32(bits)
    return np.bitwise_or.reduce(padded.reshape(words, per_word) << shifts, axis=1).astype("<u4").tobytes()


def bits_for(count):
    for bits in (1, 2, 3, 4, 5, 6, 8, 16):
        if count <= (1 << bits):
            return bits
    return 16


# blocks that are always full of water (seagrass, kelp): Bedrock keeps their water in a second block layer
WET = np.zeros(256, dtype=bool)
WET[list(p.UNDERWATER_PLANTS)] = True


def encode_subchunk(section, y_index, palette_bytes):
    """section: block numbers [16 y, 16 z, 16 x]; Bedrock wants them in x, z, y order. Layer 0 holds the blocks;
    where some are full of water a layer 1 holds that water (air everywhere else), as the game stores it."""
    xzy = np.ascontiguousarray(section.transpose(2, 1, 0)).ravel()
    used, index = np.unique(xzy, return_inverse=True)
    bits = bits_for(len(used))
    layer0 = (bytes([bits << 1]) + pack_words(index, bits)
              + struct.pack("<I", len(used)) + b"".join(palette_bytes[int(b)] for b in used))
    wet = WET[xzy]
    if not wet.any():
        return bytes([9, 1, y_index & 0xFF]) + layer0
    layer1 = (bytes([1 << 1]) + pack_words(wet.astype(np.uint32), 1)
              + struct.pack("<I", 2) + palette_bytes[p.AIR] + palette_bytes[p.WATER])
    return bytes([9, 2, y_index & 0xFF]) + layer0 + layer1


def data3d(column, biomes):
    """Heightmap (height above the top block of each column) and the biomes, one record per chunk."""
    nonair = column != p.AIR
    any_block = nonair.any(axis=0)
    top = column.shape[0] - 1 - np.argmax(nonair[::-1], axis=0)
    heights = np.where(any_block, top + 1 + p.Y_MIN, 0).astype("<i2")      # [z, x]; 0 for empty columns
    out = heights.ravel().tobytes()                          # column index z * 16 + x
    ids = [BIOME_IDS[p.BIOMES[int(b)]] for b in np.asarray(biomes).ravel()]   # 4x4 cells, (z, x)
    pal = sorted(set(ids), key=ids.index)
    if len(pal) == 1:
        out += bytes([1]) + struct.pack("<i", pal[0])
    else:
        bits = bits_for(len(pal))
        cell = np.array([pal.index(i) for i in ids]).reshape(4, 4)
        xs, zs = np.indices((16, 16))
        xzy = np.repeat(cell[zs // 4, xs // 4][:, :, None], 16, axis=2).ravel()   # x, z, y order
        out += bytes([(bits << 1) | 1]) + pack_words(xzy, bits) + struct.pack("<i", len(pal))
        out += b"".join(struct.pack("<i", i) for i in pal)
    out += b"\xff" * (MAX_SECTION - MIN_SECTION)           # every higher section: "same as below"
    return out


SIGN_COLOURS = {"white": 0xFFFFFFFF, "red": 0xFFB02E26, "black": 0xFF000000}     # ARGB, as the game stores it


def sign_entity(x, y, z, lines, colour="white"):
    """A sign's text as Bedrock saves it (the field set PocketMine-MP writes): the same text on both sides."""
    argb = SIGN_COLOURS[colour]
    side = [("Text", "string", "\n".join(lines).rstrip("\n")), ("SignTextColor", "int", argb - (1 << 32)),
            ("IgnoreLighting", "byte", 0), ("PersistFormatting", "byte", 1)]
    return le_compound([("id", "string", "Sign"), ("x", "int", x), ("y", "int", y), ("z", "int", z),
                        ("isMovable", "byte", 1), ("FrontText", "compound", side), ("BackText", "compound", side),
                        ("IsWaxed", "byte", 1)])


def bed_entity(x, y, z, colour=14):
    """A bed's block entity as Bedrock saves it: its colour (14 = red) lives here, not in the block."""
    return le_compound([("id", "string", "Bed"), ("x", "int", x), ("y", "int", y), ("z", "int", z),
                        ("isMovable", "byte", 1), ("color", "byte", colour)])


def chunk_records(vol, biomes, palette_bytes, signs=()):
    """signs: (layer, z, x, rotation, lines, colour) of every sign block; their text goes in each chunk's block-entity
    record (key type 49), one little-endian NBT compound after another."""
    size = vol.shape[1]
    offset = size // 32
    records = {}
    by_chunk = {}
    for y, z, x, _, lines, colour in signs:
        by_chunk.setdefault((x // 16, z // 16), []).append(
            sign_entity(x - size // 2, y + p.Y_MIN, z - size // 2, lines, colour))
    for y, z, x in zip(*np.nonzero(np.isin(vol, p.BED_BLOCKS))):    # every bed half: its colour
        by_chunk.setdefault((int(x) // 16, int(z) // 16), []).append(
            bed_entity(int(x) - size // 2, int(y) + p.Y_MIN, int(z) - size // 2))
    for cz_i in range(size // 16):
        for cx_i in range(size // 16):
            cx, cz = cx_i - offset, cz_i - offset
            column = vol[:, cz_i * 16:(cz_i + 1) * 16, cx_i * 16:(cx_i + 1) * 16]
            prefix = struct.pack("<ii", cx, cz)
            records[prefix + bytes([44])] = bytes([CHUNK_VERSION])
            records[prefix + bytes([54])] = struct.pack("<i", 2)              # finished generating
            chunk_biomes = biomes[cz_i * 4:(cz_i + 1) * 4, cx_i * 4:(cx_i + 1) * 4]
            records[prefix + bytes([43])] = data3d(column, chunk_biomes)
            if (cx_i, cz_i) in by_chunk:
                records[prefix + bytes([49])] = b"".join(by_chunk[(cx_i, cz_i)])
            for s in range(column.shape[0] // 16):
                section = column[s * 16:(s + 1) * 16]
                if not section.any():
                    continue
                y_index = s + MIN_SECTION
                records[prefix + bytes([47, y_index & 0xFF])] = encode_subchunk(section, y_index, palette_bytes)
    return records


# --------------------------------------------------------------------------- level.dat and the .mcworld file

def level_dat(name, spawn):
    void = '{"biome_id":1,"encoding_version":6,"preset_id":"TheVoid","world_version":"version.post_1_18"}'
    b = lambda k, v: (k, "byte", v)
    i = lambda k, v: (k, "int", v)
    fields = [
        i("StorageVersion", 10), i("NetworkVersion", NETWORK_VERSION), i("WorldVersion", 1),
        ("InventoryVersion", "string", ".".join(map(str, SAVED_WITH[:3]))),
        ("lastOpenedWithVersion", "list:int", SAVED_WITH), ("MinimumCompatibleClientVersion", "list:int", SAVED_WITH),
        ("LevelName", "string", name), ("RandomSeed", "long", int(time.time()) & 0x7FFFFFFF),
        i("SpawnX", spawn[0]), i("SpawnY", spawn[1]), i("SpawnZ", spawn[2]),
        i("Generator", 2), ("FlatWorldLayers", "string", void), b("spawnMobs", 0),
        i("GameType", 1), i("Difficulty", 0), b("ForceGameType", 0),
        ("LastPlayed", "long", int(time.time())), ("Time", "long", 6000), ("currentTick", "long", 0),
        b("commandsEnabled", 1), b("cheatsEnabled", 1), b("commandblocksenabled", 1), b("commandblockoutput", 1),
        b("MultiplayerGame", 1), b("MultiplayerGameIntent", 1), b("LANBroadcast", 1), b("LANBroadcastIntent", 1),
        i("XBLBroadcastIntent", 3), i("PlatformBroadcastIntent", 3), i("Platform", 2),
        b("dodaylightcycle", 0), b("doweathercycle", 0), b("domobspawning", 0), b("domobloot", 1),
        b("dotiledrops", 1), b("doentitydrops", 1), b("dofiretick", 0), b("mobgriefing", 0),
        b("naturalregeneration", 1), b("pvp", 1), b("keepinventory", 0), b("sendcommandfeedback", 1),
        b("showcoordinates", 1), b("showdeathmessages", 1), b("tntexplodes", 1), b("respawnblocksexplode", 1),
        b("projectilescanbreakblocks", 1), b("drowningdamage", 1), b("falldamage", 1), b("firedamage", 1),
        b("freezedamage", 1), ("rainLevel", "float", 0.0), i("rainTime", 100000), ("lightningLevel", "float", 0.0),
        i("lightningTime", 100000), i("NetherScale", 8), i("spawnradius", 0), i("randomtickspeed", 1),
        i("functioncommandlimit", 10000), i("maxcommandchainlength", 65535), i("serverChunkTickRange", 4),
        i("limitedWorldDepth", 16), i("limitedWorldWidth", 16), i("LimitedWorldOriginX", spawn[0]),
        i("LimitedWorldOriginY", spawn[1]), i("LimitedWorldOriginZ", spawn[2]),
        ("worldStartCount", "long", 4294967294),
        b("bonusChestEnabled", 0), b("bonusChestSpawned", 0), b("hasBeenLoadedInCreative", 1),
        b("hasLockedBehaviorPack", 0), b("hasLockedResourcePack", 0), b("immutableWorld", 0),
        b("isFromLockedTemplate", 0), b("isFromWorldTemplate", 0), b("isSingleUseWorld", 0),
        b("isWorldTemplateOptionLocked", 0), b("texturePacksRequired", 0), b("useMsaGamertagsOnly", 0),
        b("CenterMapsToOrigin", 0), b("ConfirmedPlatformLockedContent", 0), b("educationFeaturesEnabled", 0),
        b("startWithMapEnabled", 0), b("requiresCopiedPackRemovalCheck", 0), b("SpawnV1Villagers", 0),
        b("IsHardcore", 0), b("isCreatedInEditor", 0), b("isExportedFromEditor", 0), b("isRandomSeedAllowed", 0),
        b("HasUncompleteWorldFileOnDisk", 0), b("PlayerHasDied", 0), b("doinsomnia", 0),
        b("doimmediaterespawn", 0), b("dolimitedcrafting", 0), b("recipesunlock", 1), b("showtags", 1),
        b("showrecipemessages", 1), b("showbordereffect", 1), b("showdaysplayed", 0), b("locatorbar", 1),
        b("tntexplosiondropdecay", 1),
        ("experiments", "compound", [b("experiments_ever_used", 0), b("saved_with_toggled_experiments", 0)]),
        i("editorWorldType", 0), i("eduOffer", 0), ("BiomeOverride", "string", ""), ("prid", "string", ""),
        i("playerssleepingpercentage", 100), i("permissionsLevel", 0), i("playerPermissionsLevel", 1),
        i("daylightCycle", 0),
    ]
    body = le_compound(fields)
    return struct.pack("<ii", 10, len(body)) + body


def export_mcworld(vol, biomes, out_path, name, spawn, icon=None, signs=(), autumn=False):
    """Write a .mcworld file (a zip of the world folder) for Minecraft Bedrock 1.21.120 and newer."""
    out_path = Path(out_path)
    entries = load_table()
    palette_bytes = [palette_entry(e) for e in entries]
    stage = out_path.with_suffix(".bedrock")
    if stage.exists():
        import shutil
        shutil.rmtree(stage)
    write_leveldb(stage / "db", chunk_records(vol, biomes, palette_bytes, signs))
    (stage / "level.dat").write_bytes(level_dat(name, spawn))
    (stage / "levelname.txt").write_text(name, encoding="utf-8")
    if icon is not None:
        icon.convert("RGB").resize((256, 144)).save(stage / "world_icon.jpeg", quality=88)
    if autumn:                                          # autumn leaves: a resource pack of the world's own
        import autumn as autumn_pack
        autumn_pack.bedrock_pack(stage, name)
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
        for path in sorted(stage.rglob("*")):
            if path.is_file():
                z.write(path, path.relative_to(stage).as_posix())
    import shutil
    shutil.rmtree(stage)
    return out_path
