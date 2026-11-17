"""Download the block data that tests/check_blocks.py checks against (PrismarineJS/minecraft-data, extracted
from the real game). Only a few JSON files per version are fetched.

usage: python -I tests/get_mcdata.py OUT_DIR [VERSION_DIR ...]      (default versions: 1.18 1.20 1.21.4 26.1)
then:  python -I tests/check_blocks.py OUT_DIR 1.18 1.20 1.21.4 26.1
"""
import sys
import urllib.error
import urllib.request
from pathlib import Path

BASE = "https://raw.githubusercontent.com/PrismarineJS/minecraft-data/master/data/pc/{version}/{name}.json"
out = Path(sys.argv[1])
versions = sys.argv[2:] or ["1.18", "1.20", "1.21.4", "26.1"]
for version in versions:
    for name in ("blocks", "blockCollisionShapes", "biomes"):
        target = out / "pc" / version / f"{name}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            with urllib.request.urlopen(BASE.format(version=version, name=name), timeout=120) as resp:
                target.write_bytes(resp.read())
            print("got", target)
        except urllib.error.HTTPError as exc:            # some versions share files with an older one
            print("not published for", version, name, f"({exc.code})")
