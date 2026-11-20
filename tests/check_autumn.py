"""The autumn leaves packs: the Java resource pack (its pack.mcmeta, a model per leaf that is not tinted, textures in
autumn colours with the leaves' holes kept) and the Bedrock world pack (manifest, the world's list of its packs, client
biomes with an autumn foliage colour).

    python tests/check_autumn.py"""
import io
import json
import sys
import tempfile
import uuid
import zipfile
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import autumn  # noqa: E402

fails = []


def check(ok, text):
    print(("  ok    " if ok else "  FAIL  ") + text)
    if not ok:
        fails.append(text)


import datetime as dt
check(autumn.autumn_amount(40.05, dt.date(2026, 11, 20)) == 1.0, "Lancaster on 20 November: full autumn")
check(autumn.autumn_amount(40.05, dt.date(2026, 7, 1)) == 0.0, "Lancaster in July: summer")
check(autumn.autumn_amount(-26.1, dt.date(2026, 11, 20)) == 0.0, "Johannesburg in November: spring, no autumn")
check(autumn.autumn_amount(-33.9, dt.date(2026, 5, 1)) > 0.5, "Cape Town in May: autumn")
check(autumn.autumn_amount(10.0, dt.date(2026, 11, 1)) == 0.0, "the tropics: no autumn")

with tempfile.TemporaryDirectory() as tmp:
    path = autumn.java_pack(Path(tmp) / "pack.zip", "Test Place")
    z = zipfile.ZipFile(path)
    names = set(z.namelist())
    meta = json.loads(z.read("pack.mcmeta"))["pack"]
    check(meta["pack_format"] == 46 and meta["supported_formats"][0] == 46 and meta["min_format"] == 46,
          f"pack.mcmeta for Java 1.21.4 on: {meta}")
    for kind in ("oak", "birch", "dark_oak"):
        model = json.loads(z.read(f"assets/minecraft/models/block/{kind}_leaves.json"))
        check(model["parent"] == "minecraft:block/cube_all", f"{kind} leaves: an untinted model")
        tex = model["textures"]["all"].replace("place2mc:", "assets/place2mc/textures/") + ".png"
        check(tex in names, f"{kind} leaves: its texture is in the pack ({tex})")
        img = np.asarray(Image.open(io.BytesIO(z.read(tex))).convert("RGBA")).astype(int)
        orig = np.asarray(Image.open(autumn.LEAVES / f"{kind}_leaves.png").convert("RGBA")).astype(int)
        check(img.shape == orig.shape and (img[..., 3] == orig[..., 3]).all(), f"{kind}: same size, same holes")
        leaf = img[..., 3] > 0
        r, g, b = (img[..., c][leaf].mean() for c in range(3))
        check(r > g > b, f"{kind}: autumn colours (mean red {r:.0f} > green {g:.0f} > blue {b:.0f})")
    check("assets/minecraft/models/block/spruce_leaves.json" not in names, "spruce stays green (no override)")
    check("CREDITS.txt" in names and "CC BY-SA" in z.read("CREDITS.txt").decode(), "the licence notice is in the pack")

    world = Path(tmp) / "world"
    world.mkdir()
    pack = autumn.bedrock_pack(world, "Test Place")
    manifest = json.loads((pack / "manifest.json").read_text())
    listed = json.loads((world / "world_resource_packs.json").read_text())
    check(manifest["format_version"] == 2 and manifest["modules"][0]["type"] == "resources", "Bedrock manifest")
    uuid.UUID(manifest["header"]["uuid"]), uuid.UUID(manifest["modules"][0]["uuid"])
    check(listed == [{"pack_id": manifest["header"]["uuid"], "version": manifest["header"]["version"]}],
          "world_resource_packs.json names the pack")
    for biome in ("plains", "forest", "river"):
        doc = json.loads((pack / "biomes" / f"{biome}.client_biome.json").read_text())["minecraft:client_biome"]
        comp = doc["components"]
        check(doc["description"]["identifier"] == f"minecraft:{biome}" and
              comp["minecraft:foliage_appearance"]["color"].startswith("#") and "minecraft:fog_appearance" in comp,
              f"{biome}: autumn foliage, fog and water kept")
print("check_autumn: " + ("all OK" if not fails else f"{len(fails)} FAILED"))
sys.exit(1 if fails else 0)
