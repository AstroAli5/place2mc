"""A small, independent reader for LevelDB databases as Minecraft Bedrock uses them (the db folder of a world).

It reads the table files that the MANIFEST lists and returns every key and value. It understands
uncompressed blocks (type 0), zlib (type 2) and raw deflate (type 4), and checks every block's checksum.
"""
import struct
import zlib
from pathlib import Path

MAGIC = bytes.fromhex("57fb808b247547db")


def _crc32c_table():
    table = []
    for i in range(256):
        c = i
        for _ in range(8):
            c = (c >> 1) ^ 0x82F63B78 if c & 1 else c >> 1
        table.append(c)
    return table


_T = _crc32c_table()


def crc32c(data, crc=0):
    crc ^= 0xFFFFFFFF
    for b in data:
        crc = _T[(crc ^ b) & 0xFF] ^ (crc >> 8)
    return crc ^ 0xFFFFFFFF


def unmask(m):
    rot = (m - 0xA282EAD8) & 0xFFFFFFFF
    return ((rot >> 17) | (rot << 15)) & 0xFFFFFFFF


def varint(buf, pos):
    result = shift = 0
    while True:
        b = buf[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, pos
        shift += 7


def read_block(data, offset, size):
    contents, kind = data[offset:offset + size], data[offset + size]
    stored = struct.unpack("<I", data[offset + size + 1:offset + size + 5])[0]
    assert unmask(stored) == crc32c(contents + bytes([kind])), f"bad checksum in block at {offset}"
    if kind == 0:
        return contents
    if kind == 2:
        return zlib.decompress(contents)
    if kind == 4:
        return zlib.decompress(contents, -15)
    raise ValueError(f"unknown block compression {kind}")


def block_entries(block):
    n_restarts = struct.unpack("<I", block[-4:])[0]
    end = len(block) - 4 - 4 * n_restarts
    pos, key = 0, b""
    while pos < end:
        shared, pos = varint(block, pos)
        unshared, pos = varint(block, pos)
        vlen, pos = varint(block, pos)
        key = key[:shared] + block[pos:pos + unshared]
        pos += unshared
        yield key, block[pos:pos + vlen]
        pos += vlen


def read_table(path):
    data = Path(path).read_bytes()
    assert data[-8:] == MAGIC, "not a LevelDB table"
    footer = data[-48:]
    _, p = varint(footer, 0)
    _, p = varint(footer, p)
    index_off, p = varint(footer, p)
    index_size, p = varint(footer, p)
    for _, handle in block_entries(read_block(data, index_off, index_size)):
        off, q = varint(handle, 0)
        size, q = varint(handle, q)
        for ikey, value in block_entries(read_block(data, off, size)):
            tag = struct.unpack("<Q", ikey[-8:])[0]
            yield ikey[:-8], tag >> 8, tag & 0xFF, value


def manifest_files(db):
    """Table file numbers the MANIFEST lists as live."""
    name = (Path(db) / "CURRENT").read_text().strip()
    data = (Path(db) / name).read_bytes()
    records, pos, pending = [], 0, b""
    while pos + 7 <= len(data):
        block_left = 32768 - pos % 32768
        if block_left < 7:
            pos += block_left
            continue
        masked, length, kind = struct.unpack("<IHB", data[pos:pos + 7])
        payload = data[pos + 7:pos + 7 + length]
        assert unmask(masked) == crc32c(bytes([kind]) + payload), "bad MANIFEST checksum"
        pos += 7 + length
        if kind in (1, 4):                    # full or last fragment
            records.append(pending + payload)
            pending = b""
        elif kind in (2, 3):
            pending += payload
        if length == 0 and kind == 0:
            break
    live = {}
    for rec in records:
        p = 0
        while p < len(rec):
            tag, p = varint(rec, p)
            if tag == 1:                                # comparator name
                n, p = varint(rec, p)
                p += n
            elif tag in (2, 3, 4, 9):                   # log number, next file, last sequence, prev log
                _, p = varint(rec, p)
            elif tag == 5:                              # compact pointer
                _, p = varint(rec, p)
                n, p = varint(rec, p)
                p += n
            elif tag == 6:                              # deleted file
                level, p = varint(rec, p)
                num, p = varint(rec, p)
                live.pop(num, None)
            elif tag == 7:                              # new file
                level, p = varint(rec, p)
                num, p = varint(rec, p)
                _, p = varint(rec, p)
                for _ in range(2):
                    n, p = varint(rec, p)
                    p += n
                live[num] = level
            else:
                raise ValueError(f"unknown MANIFEST tag {tag}")
    return live


def read_db(db):
    """{key: value} of the newest version of every key in the database."""
    out = {}
    newest = {}
    for num in manifest_files(db):
        for key, seq, kind, value in read_table(Path(db) / f"{num:06d}.ldb"):
            if key not in newest or seq > newest[key]:
                newest[key] = seq
                if kind == 1:
                    out[key] = value
                else:
                    out.pop(key, None)
    return out
