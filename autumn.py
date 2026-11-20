"""Autumn leaves for the Minecraft worlds. The game has no seasons: a leaf block takes its colour from the biome. So
the colour has to come from a resource pack.

Java: a separate resource pack (a .zip you add in Options > Resource Packs) that draws oak, dark oak, birch, acacia
and jungle leaves in their autumn colours (each from its own texture, so oaks are orange, red and brown and birches
yellow) and leaves spruce green, as conifers stay. It is not put in the world as resources.zip on purpose: the game
refuses to open a world whose bundled pack it finds incompatible, and this one could not be tried in the real game.

Bedrock: a world resource pack inside the .mcworld that gives the world's biomes an autumn foliage colour
("minecraft:foliage_appearance" of a client biome, format 1.21.120 as in Mojang's bedrock-samples). Bedrock applies a
world's own packs by itself; if a version cannot read it, it is left out and the world still opens.

The leaf textures are made from Pixel Perfection CE's (CC BY-SA 4.0, see textures/pixel-perfection-ce/CREDITS.md):
the derived textures are CC BY-SA 4.0 too, and the pack carries that notice.
"""
import datetime
import io
import json
import uuid
import zipfile
from pathlib import Path

import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
LEAVES = HERE / "textures" / "pixel-perfection-ce" / "assets" / "minecraft" / "textures" / "block"
# the colours each kind of tree turns, from early to late autumn (red maple and oak orange, birch and hickory yellow)
TURNS = {
    "oak": [(196, 96, 34), (178, 58, 38), (150, 92, 44), (208, 140, 46), (122, 70, 40)],
    "dark_oak": [(140, 52, 34), (118, 64, 40), (166, 84, 40)],
    "birch": [(222, 182, 52), (206, 160, 40), (232, 200, 80)],
    "acacia": [(200, 120, 40), (180, 90, 40)],
    "jungle": [(190, 110, 40), (160, 80, 40)],
}


def autumn_amount(lat, day=None):
    """0 to 1: how far into autumn a place is on a day, the same rule as the walk-through's (leaves turn from late
    September, are fullest in late October and November, and fall by mid-December; none in the tropics or far north)."""
    day = day or datetime.date.today()
    if abs(lat) < 28 or abs(lat) > 60:
        return 0.0
    d = (day.timetuple().tm_yday + (182 if lat < 0 else 0)) % 365
    if d < 270 or d > 350:
        return 0.0
    if d < 298:
        return (d - 270) / 28
    return 1.0 if d <= 330 else 1 - (d - 330) / 40


def autumn_texture(kind, seed=0):
    """A leaf texture in autumn colours: each leaf pixel takes one of the tree's colours, its shading from the
    original grey texture, its holes (alpha) unchanged."""
    img = Image.open(LEAVES / f"{kind}_leaves.png").convert("RGBA")
    a = np.asarray(img).astype(float)
    grey = a[..., :3].mean(axis=2, keepdims=True) / 255
    rng = np.random.default_rng(seed)
    colours = np.array(TURNS[kind], float)
    pick = colours[rng.integers(0, len(colours), size=a.shape[:2])]
    shade = 0.55 + 0.75 * grey                     # the grey texture's light and dark, kept
    out = a.copy()
    out[..., :3] = np.clip(pick * shade, 0, 255)
    return Image.fromarray(out.astype(np.uint8), "RGBA")


def java_pack(path, name):
    """The Java resource pack, written to `path` (.zip). Pack format 46 is Java 1.21.4; newer versions are named in
    supported_formats / min_format / max_format, the fields each version reads."""
    meta = {"pack": {"description": f"Autumn leaves for {name} (place2mc)", "pack_format": 46,
                     "supported_formats": [46, 999], "min_format": 46, "max_format": 999}}
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("pack.mcmeta", json.dumps(meta, indent=2))
        for i, kind in enumerate(k for k in TURNS if (LEAVES / f"{k}_leaves.png").exists()):
            buf = io.BytesIO()
            autumn_texture(kind, seed=i).save(buf, "PNG")
            z.writestr(f"assets/place2mc/textures/block/autumn_{kind}_leaves.png", buf.getvalue())
            # the game's own leaf model is tinted by the biome; this one is not, so the texture's colours show
            model = {"parent": "minecraft:block/cube_all", "textures": {"all": f"place2mc:block/autumn_{kind}_leaves"}}
            z.writestr(f"assets/minecraft/models/block/{kind}_leaves.json", json.dumps(model, indent=2))
        z.writestr("CREDITS.txt", CREDITS)
        icon = io.BytesIO()
        autumn_texture("oak").resize((64, 64), Image.NEAREST).save(icon, "PNG")
        z.writestr("pack.png", icon.getvalue())
    return Path(path)


# what Mojang's vanilla client biomes say for the biomes place2mc uses (fog, water, music, light), so the pack only
# changes the leaves: Bedrock reads a pack's client biome instead of the game's own, whole
CLIENT_BIOMES = {
    "plains": ("#44AFF5", "minecraft:fog_plains", None),
    "forest": ("#1E97F2", "minecraft:fog_forest", {"music_definition": "forest"}),
    "river": ("#0084FF", "minecraft:fog_river", {"underwater_music": True}),
}
UNDERWATER = {"underwater_addition": {"asset": "ambient.underwater.additions", "chance": 0.01},
              "underwater_loop": "ambient.underwater.loop"}
FOLIAGE = "#C0702E"                                     # an autumn oak's orange-brown, multiplied with the leaf


def bedrock_pack(world_dir, name):
    """A resource pack inside a Bedrock world folder (before it is zipped to .mcworld): resource_packs/<pack> and
    world_resource_packs.json, which Bedrock reads to apply it."""
    world_dir = Path(world_dir)
    pack_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "place2mc-autumn-" + name))
    module_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "place2mc-autumn-module-" + name))
    pack = world_dir / "resource_packs" / "place2mc_autumn"
    (pack / "biomes").mkdir(parents=True, exist_ok=True)
    manifest = {"format_version": 2,
                "header": {"name": "Autumn leaves (place2mc)", "description": f"Autumn colours for {name}",
                           "uuid": pack_id, "version": [1, 0, 0], "min_engine_version": [1, 21, 120]},
                "modules": [{"type": "resources", "uuid": module_id, "version": [1, 0, 0]}]}
    (pack / "manifest.json").write_text(json.dumps(manifest, indent=2))
    for biome, (water, fog, music) in CLIENT_BIOMES.items():
        doc = {"format_version": "1.21.120", "minecraft:client_biome": {
            "description": {"identifier": f"minecraft:{biome}"},
            "components": {
                "minecraft:fog_appearance": {"fog_identifier": fog},
                "minecraft:water_appearance": {"surface_color": water},
                "minecraft:foliage_appearance": {"color": FOLIAGE},
                "minecraft:atmosphere_identifier": {"atmosphere_identifier": "minecraft:default_atmospherics"},
                "minecraft:color_grading_identifier": {"color_grading_identifier": "minecraft:default_color_grading"},
                "minecraft:lighting_identifier": {"lighting_identifier": "minecraft:default_lighting"},
                "minecraft:water_identifier": {"water_identifier": "minecraft:default_water"},
                "minecraft:ambient_sounds": UNDERWATER}}}
        if music:
            doc["minecraft:client_biome"]["components"]["minecraft:biome_music"] = music
        (pack / "biomes" / f"{biome}.client_biome.json").write_text(json.dumps(doc, indent=2))
    (pack / "CREDITS.txt").write_text(CREDITS)
    (world_dir / "world_resource_packs.json").write_text(json.dumps([{"pack_id": pack_id, "version": [1, 0, 0]}],
                                                                    indent=2))
    return pack


CREDITS = """Autumn leaves for a place2mc world.
Leaf textures made from Pixel Perfection Chorus Edition (github.com/Athemis/PixelPerfectionCE), which is licensed
CC BY-SA 4.0 (creativecommons.org/licenses/by-sa/4.0): these changed textures are shared under the same licence.
Nothing in this pack comes from Minecraft itself.
"""
