#!/usr/bin/env python3
"""
mcrender - draw game-style 3D pictures of a block world made by place2mc, without opening Minecraft.

It follows a ray from the camera through the block grid for every pixel, then colours the block face
it hits with a 16x16 pixel texture, sunlight, shadows, soft corner shading, distance fog, sky and clouds.

By default the textures are simple ones generated in this file. With textures="auto" (place2mc does this
for --screenshots) it uses the real textures from your own Minecraft Java installation if it finds one,
or you can give it a game .jar, a resource pack .zip, or an unpacked resource pack folder.
Nothing from the game is copied anywhere; the textures are only read to draw the pictures.

Use it through place2mc:   python place2mc.py "Some place" --world PATH --screenshots
"""
import io
import math
import os
import zipfile
import zlib
from pathlib import Path

import numpy as np
from PIL import Image

import place2mc as p

T = 16  # texture size in pixels

# --------------------------------------------------------------------------- textures


def _rng(name):
    return np.random.default_rng(zlib.crc32(name.encode()))


def _clip(a):
    return np.clip(a, 0, 255).astype(np.uint8)


def speckled(rgb, amp, name):
    return _clip(np.array(rgb) + _rng(name).integers(-amp, amp + 1, (T, T, 1)))


def blotchy(rgb, amp, name):
    rng = _rng(name)
    low = rng.integers(-amp, amp + 1, (4, 4, 1)).repeat(4, 0).repeat(4, 1)
    fine = rng.integers(-amp // 2, amp // 2 + 1, (T, T, 1))
    return _clip(np.array(rgb) + low + fine)


def brick_wall(brick, mortar, name, bw=8, bh=4, amp=8):
    rng = _rng(name)
    img = np.array(brick) + rng.integers(-amp, amp + 1, (T, T, 1))
    for row in range(T // bh):
        offset = (bw // 2) if row % 2 else 0
        for col in range(-1, T // bw + 1):
            tint = int(rng.integers(-14, 15))
            x0, x1 = max(0, col * bw + offset), min(T, (col + 1) * bw + offset)
            img[row * bh:(row + 1) * bh, x0:x1] += tint
            if 0 <= (col + 1) * bw + offset - 1 < T:
                img[row * bh:(row + 1) * bh, (col + 1) * bw + offset - 1] = mortar
        img[(row + 1) * bh - 1] = mortar
    return _clip(img)


def boards(rgb, name):
    rng = _rng(name)
    img = np.array(rgb) + rng.integers(-5, 6, (T, 1, 1)) + rng.integers(-4, 5, (T, T, 1))
    for b in range(4):
        img[b * 4 + 3] = np.array(rgb) * 0.68
        img[b * 4:b * 4 + 3, int(rng.integers(1, T - 1))] = np.array(rgb) * 0.72
    return _clip(img)


def framed(rgb, amp, name, edge=0.86):
    img = speckled(rgb, amp, name).astype(float)
    img[0], img[-1], img[:, 0], img[:, -1] = img[0] * edge, img[-1] * edge, img[:, 0] * edge, img[:, -1] * edge
    return _clip(img)


def glass_pane(tint, name):
    img = np.full((T, T, 3), tint, dtype=float)
    img[0], img[-1], img[:, 0], img[:, -1] = (235, 240, 245), (150, 165, 180), (235, 240, 245), (150, 165, 180)
    for i in range(3, 8):                      # a diagonal glint
        img[i, 10 - i] = np.minimum(np.array(tint) + 45, 255)
    return _clip(img)


def bark(rgb, name, marks=None):
    rng = _rng(name)
    img = np.array(rgb) + rng.integers(-12, 13, (1, T, 1)) + rng.integers(-6, 7, (T, T, 1))
    if marks is not None:
        for _ in range(7):
            y, x = int(rng.integers(0, T)), int(rng.integers(0, T - 3))
            img[y, x:x + 3] = marks
    return _clip(img)


def rings(wood, bark_rgb, name):
    img = np.array(speckled(wood, 5, name), dtype=float)
    yy, xx = np.indices((T, T))
    r = np.maximum(abs(yy - 7.5), abs(xx - 7.5))
    img[r > 6.6] = bark_rgb
    img[(r > 2) & (r < 3)] *= 0.86
    img[(r > 4) & (r < 5)] *= 0.86
    return _clip(img)


def leaves(rgb, name):
    rng = _rng(name)
    img = np.array(rgb) + rng.integers(-22, 23, (T, T, 1))
    holes = rng.random((T, T)) < 0.22
    img[holes] = np.array(rgb) * 0.45
    return _clip(img)


def leaf_mask(name):
    """Where the built-in leaves texture is solid (the same random holes leaves() darkens)."""
    rng = _rng(name)
    rng.integers(-22, 23, (T, T, 1))
    return ~(rng.random((T, T)) < 0.22)


def plant(kind):
    """Built-in textures for the small plants: (picture, where it is solid)."""
    rng = _rng("plant " + kind)
    img = np.zeros((T, T, 3), dtype=float)
    solid = np.zeros((T, T), dtype=bool)
    greens = np.array([(70, 128, 44), (88, 150, 52), (104, 166, 62), (60, 112, 38)], dtype=float)
    if kind in ("grass", "tall_grass/lower", "tall_grass/upper"):
        for x in range(1, T - 1, 2):
            if kind == "tall_grass/lower":
                top = int(rng.integers(0, 3))
            elif kind == "tall_grass/upper":
                top = int(rng.integers(4, 13))
            else:
                top = int(rng.integers(5, 12))
            x = x + int(rng.integers(0, 2))
            solid[top:, x] = True
            img[top:, x] = greens[int(rng.integers(0, 4))] * np.linspace(1.15, 0.8, T - top)[:, None]
    elif kind == "fern":
        for y in range(2, T):
            reach = min(7, (y - 1) // 2 + 1)
            for x in range(8 - reach, 8 + reach):
                if (x + y) % 3 != 0:
                    solid[y, x] = True
                    img[y, x] = greens[(x + y) % 4] * 0.85
        solid[:, 7] = True
        img[:, 7] = greens[3]
    else:                                        # flowers: a stem, two leaves and a coloured head
        petals = {"dandelion": (250, 210, 40), "poppy": (214, 40, 36), "oxeye_daisy": (240, 240, 232),
                  "cornflower": (76, 104, 220), "azure_bluet": (226, 236, 246)}[kind]
        solid[7:, 7:9] = True
        img[7:, 7:9] = greens[0]
        solid[10:12, 5:7] = solid[11:13, 9:11] = True
        img[10:12, 5:7] = img[11:13, 9:11] = greens[1]
        yy, xx = np.indices((T, T))
        head = (yy - 4.5) ** 2 + (xx - 7.5) ** 2 < 9.5
        solid[head] = True
        img[head] = petals
        if kind in ("oxeye_daisy", "poppy"):
            middle = (yy - 4.5) ** 2 + (xx - 7.5) ** 2 < 2.5
            img[middle] = (236, 196, 40) if kind == "oxeye_daisy" else (30, 24, 20)
    return _clip(img), solid


def door(wood, name):
    img = np.array(boards(wood, name), dtype=float)
    img[:, :2] *= 0.7
    img[:, -2:] *= 0.7
    return _clip(img)


def door_upper(wood, name):
    img = np.array(door(wood, name), dtype=float)
    img[3:11, 3:13] = (150, 185, 205)               # small window
    img[3:11, 7:9] = np.array(wood) * 0.7
    return _clip(img)


def lantern():
    img = np.full((T, T, 3), (54, 58, 66), dtype=float)
    img[4:13, 4:12] = (255, 196, 92)
    img[6:11, 6:10] = (255, 236, 170)
    return _clip(img)


# block -> (top texture, side texture) when the file names do not follow the usual pattern
TEXTURE_NAMES = {
    "grass_block": ("grass_block_top", "grass_block_side"), "smooth_sandstone": ("sandstone_top", "sandstone_top"),
    "water": ("water_still", "water_still"), "smooth_stone": ("smooth_stone", "smooth_stone"),
}
STAIRS_MATERIAL = {"brick": "bricks", "deepslate_tile": "deepslate_tiles", "stone_brick": "stone_bricks",
                   "polished_blackstone_brick": "polished_blackstone_bricks"}
# the game stores some textures in grey and colours them by biome; these are the plains colours
TINTS = {"grass_block_top": (145, 189, 89), "grass_block_side_overlay": (145, 189, 89),
         "short_grass": (145, 189, 89), "grass": (145, 189, 89), "fern": (145, 189, 89),
         "tall_grass_bottom": (145, 189, 89), "tall_grass_top": (145, 189, 89),
         "oak_leaves": (119, 171, 47), "birch_leaves": (128, 167, 85), "spruce_leaves": (97, 153, 97),
         "water_still": (63, 118, 228)}
# what shows through the see-through parts of a texture
BACKDROP = {"glass": (178, 205, 222), "leaves": (30, 62, 24), "door": (44, 34, 24), "rail": (132, 126, 122),
            "default": (40, 46, 56)}


def find_minecraft_jar():
    """The newest game .jar in the usual Minecraft Java folders on Windows, macOS and Linux, or None."""
    home = Path.home()
    roots = [Path(os.environ.get("APPDATA", "")) / ".minecraft", home / "Library" / "Application Support" / "minecraft",
             home / ".minecraft"]
    jars = [j for r in roots if (r / "versions").is_dir() for j in (r / "versions").glob("*/*.jar")]
    return max(jars, key=lambda j: j.stat().st_mtime) if jars else None


class TextureSource:
    """Reads block textures from a game .jar, a resource pack .zip, or an unpacked resource pack folder."""

    def __init__(self, path):
        self.path = Path(path)
        self.zip = zipfile.ZipFile(self.path) if self.path.is_file() else None
        self.missing = []

    def raw(self, name):
        inner = f"assets/minecraft/textures/block/{name}.png"
        try:
            data = self.zip.read(inner) if self.zip else (self.path / inner).read_bytes()
        except (KeyError, OSError):
            return None
        img = Image.open(io.BytesIO(data)).convert("RGBA")
        side = img.width
        img = img.crop((0, 0, side, side))                  # animated textures are a strip of frames; take the first
        if side != T:
            img = img.resize((T, T), Image.NEAREST)
        return np.array(img, dtype=float)

    def get(self, name, backdrop):
        """(picture laid over the backdrop, where the texture is solid), or None."""
        rgba = self.raw(name)
        if rgba is None:
            return None
        rgb, alpha = rgba[..., :3], rgba[..., 3:] / 255.0
        if name in TINTS:
            rgb = rgb * np.array(TINTS[name]) / 255.0
        if name == "grass_block_side":
            overlay = self.raw("grass_block_side_overlay")
            if overlay is not None:
                a = overlay[..., 3:] / 255.0
                rgb = rgb * (1 - a) + overlay[..., :3] * np.array(TINTS["grass_block_side_overlay"]) / 255.0 * a
        return _clip(rgb * alpha + np.array(backdrop) * (1 - alpha)), alpha[..., 0] > 0.5

    def block(self, name):
        """(top, side, top solid mask, side solid mask) for a block, or None if this pack does not have it."""
        base = name
        if name.endswith("_stairs"):
            stem = name[:-len("_stairs")]
            base = STAIRS_MATERIAL.get(stem, stem + "_planks")
        elif name.endswith("_fence"):
            base = name[:-len("_fence")] + "_planks"
        elif name.endswith("_wall"):
            stem = name[:-len("_wall")]
            base = STAIRS_MATERIAL.get(stem, stem)
        elif name.endswith("_slab"):
            base = name[:-len("_slab")]
        backdrop = BACKDROP["leaves"] if "leaves" in name else BACKDROP["glass"] if name == "glass" else \
            BACKDROP["door"] if "_door/" in name else BACKDROP["rail"] if name == "rail" else BACKDROP["default"]
        if "_door/" in name or name.startswith("tall_grass/"):
            kind, half = name.split("/")
            got = self.get(kind + ("_bottom" if half == "lower" else "_top"), backdrop)
            if got is None:
                self.missing.append(name)
                return None
            return got[0], got[0], got[1], got[1]
        if name == "grass":                              # the plant; called short_grass from 1.20.3
            got = self.get("short_grass", backdrop) or self.get("grass", backdrop)
            if got is None:
                self.missing.append(name)
                return None
            return got[0], got[0], got[1], got[1]
        top_name, side_name = TEXTURE_NAMES.get(base, (base + "_top", base + "_side"))
        top = self.get(top_name, backdrop)
        side = self.get(side_name, backdrop)
        plain = self.get(base, backdrop) if top is None or side is None else None
        top, side = top if top is not None else plain, side if side is not None else plain
        if top is None or side is None:
            self.missing.append(name)
            return None
        return top[0], side[0], top[1], side[1]


def resolve_textures(textures):
    """None -> built-in textures. "auto" -> the game's own textures if Minecraft is installed. Else a path."""
    if textures in (None, "", "builtin"):
        return None
    if textures == "auto":
        jar = find_minecraft_jar()
        return TextureSource(jar) if jar else None
    return TextureSource(textures)


_TEXTURE_CACHE = {}


def make_textures(textures=None):
    key = str(textures.path) if isinstance(textures, TextureSource) else None
    if key not in _TEXTURE_CACHE:
        _TEXTURE_CACHE[key] = _make_textures(textures)
    return _TEXTURE_CACHE[key]


def _make_textures(source=None):
    """Returns (tex, mean, solid): tex[block, 0 top / 1 side, y, x, rgb] as floats 0..1, the average colour of
    each, and solid[block, face, y, x], False where a see-through texture (leaves, plants) has a hole."""
    dirt = blotchy((134, 96, 67), 14, "dirt")
    grass_top = speckled((104, 168, 70), 13, "grass")
    grass_side = dirt.copy()
    rng = _rng("grass edge")
    for x in range(T):
        h = int(rng.integers(2, 5))
        grass_side[:h, x] = grass_top[:h, x]
    path_top = blotchy((158, 128, 74), 10, "path")
    path_side = dirt.copy()
    path_side[:2] = path_top[:2]
    podzol_top = blotchy((98, 66, 28), 12, "podzol")
    podzol_side = dirt.copy()
    podzol_side[:3] = podzol_top[:3]
    water = speckled((52, 104, 214), 6, "water").astype(float)
    wave = _rng("waves")
    for _ in range(5):
        y, x = int(wave.integers(0, T)), int(wave.integers(0, T - 5))
        water[y, x:x + 5] += 26
    stone = blotchy((126, 126, 126), 9, "stone")
    cobble = np.array(blotchy((120, 120, 120), 22, "cobble"), dtype=float)
    cobble[::5] *= 0.72
    cobble[:, 3::6] *= 0.76
    gravel = blotchy((132, 126, 122), 26, "gravel")
    sandstone = np.array(speckled((222, 212, 168), 6, "sandstone"), dtype=float)
    sandstone[:2] *= 0.93
    quartz = np.array(speckled((236, 231, 224), 3, "quartz"), dtype=float)
    quartz[5], quartz[11] = quartz[5] * 0.95, quartz[11] * 0.95
    iron = np.array(framed((222, 222, 222), 4, "iron", 0.8), dtype=float)
    iron[4::4] *= 0.93

    by_name = {
        "dirt": dirt, "coarse_dirt": blotchy((122, 88, 60), 24, "coarse"), "grass_block": (grass_top, grass_side),
        "dirt_path": (path_top, path_side), "podzol": (podzol_top, podzol_side),
        "sand": speckled((222, 210, 162), 9, "sand"), "gravel": gravel, "water": _clip(water),
        "stone": stone, "andesite": blotchy((138, 138, 140), 12, "andesite"), "cobblestone": _clip(cobble),
        "smooth_stone": framed((166, 166, 166), 5, "smooth", 0.84),
        "polished_andesite": framed((134, 138, 136), 9, "polished", 0.86),
        "stone_bricks": brick_wall((124, 124, 124), (88, 88, 88), "stone bricks", bw=8, bh=8, amp=7),
        "bricks": brick_wall((152, 86, 68), (176, 168, 160), "bricks"),
        "deepslate_tiles": brick_wall((58, 58, 62), (36, 36, 40), "deepslate", bw=8, bh=4, amp=4),
        "gray_concrete": speckled((58, 62, 66), 3, "asphalt"),
        "gray_concrete_powder": speckled((78, 82, 86), 9, "worn asphalt"),
        "light_gray_concrete": speckled((128, 128, 122), 3, "paving"),
        "light_gray_concrete_powder": speckled((152, 152, 146), 9, "worn paving"),
        "white_concrete": speckled((212, 216, 216), 3, "white"),
        "green_concrete": speckled((76, 96, 40), 4, "pitch"),
        "oak_planks": boards((166, 134, 82), "oak planks"), "spruce_planks": boards((114, 84, 48), "spruce planks"),
        "birch_planks": boards((196, 180, 122), "birch planks"), "dark_oak_planks": boards((68, 44, 22), "dark planks"),
        "terracotta": speckled((152, 94, 68), 4, "terracotta"),
        "white_terracotta": speckled((210, 178, 160), 4, "white terracotta"),
        "gray_terracotta": speckled((60, 44, 38), 4, "gray terracotta"),
        "brown_terracotta": speckled((80, 54, 38), 4, "brown terracotta"),
        "smooth_sandstone": _clip(sandstone), "quartz_block": _clip(quartz), "iron_block": _clip(iron),
        "glass": glass_pane((186, 216, 232), "glass"),
        "light_blue_stained_glass": glass_pane((120, 172, 220), "blue glass"),
        "gray_stained_glass": glass_pane((92, 104, 118), "gray glass"),
        "oak_log": (rings((180, 146, 90), (104, 82, 50), "oak rings"), bark((104, 82, 50), "oak bark")),
        "birch_log": (rings((196, 180, 122), (216, 214, 204), "birch rings"), bark((216, 214, 204), "birch bark", (48, 48, 44))),
        "spruce_log": (rings((114, 84, 48), (58, 40, 22), "spruce rings"), bark((58, 40, 22), "spruce bark")),
        "oak_leaves": leaves((58, 128, 44), "oak leaves"), "birch_leaves": leaves((104, 150, 70), "birch leaves"),
        "spruce_leaves": leaves((44, 92, 58), "spruce leaves"),
        "bedrock": blotchy((72, 72, 72), 30, "bedrock"),
        "polished_blackstone_bricks": brick_wall((52, 46, 56), (32, 28, 36), "blackstone", bw=8, bh=4, amp=5),
        "lantern": lantern(),
    }
    wood = {"dark_oak": (68, 44, 22), "spruce": (114, 84, 48), "birch": (196, 180, 122), "oak": (166, 134, 82)}
    for kind, rgb in wood.items():
        by_name[kind + "_fence"] = by_name[kind + "_planks"]
        by_name[kind + "_door/lower"] = door(rgb, kind + " door")
        by_name[kind + "_door/upper"] = door_upper(rgb, kind + " door")
    stairs = {"brick": "bricks", "deepslate_tile": "deepslate_tiles", "stone_brick": "stone_bricks",
              "polished_blackstone_brick": "polished_blackstone_bricks"}
    for base in list(wood) + list(stairs):
        by_name[base + "_stairs"] = by_name[stairs.get(base, base + "_planks")]
    by_name["stone_brick_wall"] = by_name["stone_bricks"]
    rail = np.array(by_name["gravel"], dtype=float)          # the built-in rail: wooden sleepers and two iron rails
    rail[1::4] = (rail[1::4] * 0.3 + np.array((112, 84, 52)) * 0.7)
    rail[2::4] = (rail[2::4] * 0.3 + np.array((100, 76, 46)) * 0.7)
    rail[:, 2:4] = rail[:, 12:14] = (150, 150, 152)
    by_name["rail"] = _clip(rail)
    by_name["smooth_stone_slab"] = by_name["smooth_stone"]
    by_name["polished_deepslate_slab"] = by_name["gray_concrete"]
    masks = {}
    for kind in ("oak", "birch", "spruce"):
        masks[kind + "_leaves"] = leaf_mask(kind + " leaves")
    for kind in ("grass", "tall_grass/lower", "tall_grass/upper", "fern", "dandelion", "poppy", "oxeye_daisy",
                 "cornflower", "azure_bluet"):
        by_name[kind], masks[kind] = plant(kind)
    tex = np.zeros((len(p.BLOCKS), 2, T, T, 3), dtype=np.float32)
    solid = np.ones((len(p.BLOCKS), 2, T, T), dtype=bool)
    for i, (full_name, props) in enumerate(p.BLOCKS):
        name = full_name.split(":")[1]
        if name.endswith("_door") or name == "tall_grass":
            name += "/" + props["half"]
        entry = source.block(name) if source is not None else None
        if entry is not None:
            top, side, solid[i, 0], solid[i, 1] = entry
        else:
            entry = by_name.get(name)
            if entry is None:
                entry = speckled(p.BLOCK_RGB.get(name, (200, 0, 200)), 6, name)
            top, side = entry if isinstance(entry, tuple) else (entry, entry)
            if name in masks:
                solid[i, 0] = solid[i, 1] = masks[name]
        tex[i, 0], tex[i, 1] = top / 255.0, side / 255.0
    if source is not None and source.missing:
        print(f"  ({len(source.missing)} block textures not found in {source.path.name}, using built-in ones: "
              + ", ".join(sorted(set(source.missing))[:8]) + ")")
    weights = solid[..., None].astype(np.float32)
    mean = (tex * weights).sum(axis=(2, 3)) / np.maximum(weights.sum(axis=(2, 3)), 1)
    return tex, mean, solid


# --------------------------------------------------------------------------- block shapes

MAX_BOXES = 5


def _shapes():
    """Up to MAX_BOXES boxes per block, in block units (x, y, z from 0 to 1), the shapes of the game's block
    models. Blocks not listed are full cubes."""
    lo = np.zeros((len(p.BLOCKS), MAX_BOXES, 3))
    hi = np.zeros((len(p.BLOCKS), MAX_BOXES, 3))
    hi[:, 0] = 1.0
    lo[:, 1:], hi[:, 1:] = 2.0, -1.0                     # boxes after the first are empty unless set
    partial = np.zeros(len(p.BLOCKS), dtype=bool)
    halves = {"north": ((0, .5, 0), (1, 1, .5)), "south": ((0, .5, .5), (1, 1, 1)),
              "west": ((0, .5, 0), (.5, 1, 1)), "east": ((.5, .5, 0), (1, 1, 1))}
    door_panel = {"north": ((0, 0, 13 / 16), (1, 1, 1)), "south": ((0, 0, 0), (1, 1, 3 / 16)),
                  "west": ((13 / 16, 0, 0), (1, 1, 1)), "east": ((0, 0, 0), (3 / 16, 1, 1))}
    for i, (full_name, props) in enumerate(p.BLOCKS):
        name = full_name.split(":")[1]
        boxes = None
        if name.endswith("_stairs"):
            boxes = [((0, 0, 0), (1, .5, 1)), halves[props["facing"]]]
        elif name.endswith("_door"):
            boxes = [door_panel[props["facing"]]]
        elif name.endswith("_fence"):                 # the post, and a bar to every side it joins
            boxes = [((6 / 16, 0, 6 / 16), (10 / 16, 1, 10 / 16))]
            arms = {"north": ((7 / 16, 6 / 16, 0), (9 / 16, 15 / 16, 6 / 16)),
                    "south": ((7 / 16, 6 / 16, 10 / 16), (9 / 16, 15 / 16, 1)),
                    "west": ((0, 6 / 16, 7 / 16), (6 / 16, 15 / 16, 9 / 16)),
                    "east": ((10 / 16, 6 / 16, 7 / 16), (1, 15 / 16, 9 / 16))}
            boxes += [arms[side] for side in arms if (props or {}).get(side) == "true"]
        elif name.endswith("_wall"):                  # the post (when it has one) and a low wall to every side
            boxes = [((4 / 16, 0, 4 / 16), (12 / 16, 1, 12 / 16))] if (props or {}).get("up") != "false" else []
            arms = {"north": ((5 / 16, 0, 0), (11 / 16, 14 / 16, 8 / 16)),
                    "south": ((5 / 16, 0, 8 / 16), (11 / 16, 14 / 16, 1)),
                    "west": ((0, 0, 5 / 16), (8 / 16, 14 / 16, 11 / 16)),
                    "east": ((8 / 16, 0, 5 / 16), (1, 14 / 16, 11 / 16))}
            boxes += [arms[side] for side in arms if (props or {}).get(side, "none") != "none"]
            boxes = boxes or [((4 / 16, 0, 4 / 16), (12 / 16, 1, 12 / 16))]
        elif name == "rail":                          # drawn flat (sloped rails too), one pixel thick
            boxes = [((0, 0, 0), (1, 1 / 16, 1))]
        elif name.endswith("_slab") and props.get("type") == "bottom":
            boxes = [((0, 0, 0), (1, .5, 1))]
        elif name == "lantern":                     # same two boxes as the game: the body and the small cap
            boxes = [((5 / 16, 1 / 16, 5 / 16), (11 / 16, 8 / 16, 11 / 16)),
                     ((6 / 16, 8 / 16, 6 / 16), (10 / 16, 10 / 16, 10 / 16))]
        if boxes:
            partial[i] = True
            for b, (l, h) in enumerate(boxes):
                lo[i, b], hi[i, b] = l, h
    return lo, hi, partial


BOX_LO, BOX_HI, BOXED = _shapes()
_names = [name.split(":")[1] for name, _ in p.BLOCKS]
CROSS = np.array([n in ("grass", "tall_grass", "fern", "dandelion", "poppy", "oxeye_daisy", "cornflower",
                        "azure_bluet") for n in _names])          # drawn as two crossed pictures, like in the game
SEE_THROUGH = np.array([n.endswith("_leaves") for n in _names])  # full cubes with holes ("fancy" leaves)
PARTIAL = BOXED | CROSS | SEE_THROUGH
INVISIBLE = np.array([n in ("barrier", "light") for n in _names])       # blocks the game does not draw
GLOWING = np.array([name == "minecraft:lantern" for name, _ in p.BLOCKS])


def hit_shape(origin, direction, cell, block, t_cell):
    """For rays that entered a cell holding a block that is not a full cube: does the ray hit the shape inside,
    and where. Returns (hit, t, axis)."""
    with np.errstate(divide="ignore", invalid="ignore"):
        inv = 1.0 / direction
    inv[~np.isfinite(inv)] = 1e30
    best_t = np.full(len(cell), np.inf)
    best_axis = np.ones(len(cell), dtype=np.int64)
    for b in range(MAX_BOXES):
        t1 = (cell + BOX_LO[block, b] - origin) * inv
        t2 = (cell + BOX_HI[block, b] - origin) * inv
        near = np.minimum(t1, t2)
        enter, leave = near.max(axis=1), np.maximum(t1, t2).min(axis=1)
        real = (BOX_LO[block, b] <= BOX_HI[block, b]).all(axis=1)       # the unused second box is marked lo > hi
        ok = real & (enter <= leave) & (leave > t_cell) & (enter < best_t)
        best_t = np.where(ok, np.maximum(enter, t_cell), best_t)
        best_axis = np.where(ok, near.argmax(axis=1), best_axis)
    return np.isfinite(best_t), best_t, best_axis


def cross_hit(origin, direction, cell, block, t_cell, solid):
    """Rays through a cell holding a plant: the two upright pictures on the cell's diagonals.
    Returns (hit, t, axis) with axis 3 for the x=z picture and 4 for the x+z=1 picture."""
    o = origin - cell
    best_t = np.full(len(cell), np.inf)
    best_axis = np.full(len(cell), 3, dtype=np.int64)
    with np.errstate(divide="ignore", invalid="ignore"):
        for axis_id, num, den in ((3, o[:, 2] - o[:, 0], direction[:, 0] - direction[:, 2]),
                                  (4, 1 - o[:, 0] - o[:, 2], direction[:, 0] + direction[:, 2])):
            t = num / den
            q = o + direction * t[:, None]
            ok = np.isfinite(t) & (t >= t_cell - 1e-9) & (q >= -1e-9).all(axis=1) & (q <= 1 + 1e-9).all(axis=1)
            ui = np.clip((np.nan_to_num(q[:, 0]) * T).astype(np.int64), 0, T - 1)
            vi = np.clip(((1 - np.nan_to_num(q[:, 1])) * T).astype(np.int64), 0, T - 1)
            if solid is not None:
                ok &= solid[block, 1, vi, ui]
            better = ok & (t < best_t)
            best_t = np.where(better, t, best_t)
            best_axis = np.where(better, axis_id, best_axis)
    return np.isfinite(best_t), best_t, best_axis


def shape_hit(origin, direction, cell, block, t_cell, axis_cell, solid):
    """Rays that entered a cell whose block is not a plain solid cube: boxes (stairs, doors, fences, lanterns),
    crossed plant pictures, or cubes with holes (leaves). Returns (hit, t, axis)."""
    ok = np.zeros(len(cell), dtype=bool)
    t = t_cell.astype(float).copy()
    axis = axis_cell.copy()
    for kind, fn in ((BOXED, None), (CROSS, None), (SEE_THROUGH, None)):
        m = np.flatnonzero(kind[block])
        if not len(m):
            continue
        if kind is BOXED:
            ok[m], t[m], axis[m] = hit_shape(origin[m], direction[m], cell[m], block[m], t_cell[m])
        elif kind is CROSS:
            ok[m], t[m], axis[m] = cross_hit(origin[m], direction[m], cell[m], block[m], t_cell[m], solid)
        elif solid is None:
            ok[m] = True
        else:                                          # is the texture solid where the ray enters the cube?
            q = origin[m] + direction[m] * t_cell[m][:, None] - cell[m]
            a = axis_cell[m]
            u = np.where(a == 0, q[:, 2], q[:, 0])
            v = np.where(a == 1, q[:, 2], 1.0 - q[:, 1])
            ui = np.clip((u * T).astype(np.int64), 0, T - 1)
            vi = np.clip((v * T).astype(np.int64), 0, T - 1)
            ok[m] = solid[block[m], (a != 1).astype(np.int64), vi, ui]
    return ok, t, axis


# --------------------------------------------------------------------------- ray walking

def march(vol, origin, direction, t_start, t_stop, first_axis=None, solid=None, passable=None):
    """Walk rays through the block grid (axes x, y, z). Returns block, axis, t, cell for every ray; block 0 = no hit."""
    n = len(origin)
    dims = np.array([vol.shape[2], vol.shape[0], vol.shape[1]])
    out_block = np.zeros(n, dtype=np.uint8)
    out_axis = np.ones(n, dtype=np.int64)
    out_t = np.full(n, np.inf)
    out_cell = np.zeros((n, 3), dtype=np.int64)
    if n == 0:
        return out_block, out_axis, out_t, out_cell
    with np.errstate(divide="ignore"):
        inv = 1.0 / direction
    inv[~np.isfinite(inv)] = 1e30
    pos = origin + direction * t_start[:, None]
    cell = np.clip(np.floor(pos).astype(np.int64), 0, dims - 1)
    step = np.where(direction >= 0, 1, -1).astype(np.int64)
    t_delta = np.abs(inv)
    t_max = t_start[:, None] + (cell + (step > 0) - pos) * inv
    idx = np.arange(n)

    first = vol[cell[:, 1], cell[:, 2], cell[:, 0]]
    hit = (first != 0) & ~INVISIBLE[first]
    if passable is not None:
        hit &= ~passable[first]
    first_t = t_start.copy()
    if first_axis is not None:
        out_axis[:] = first_axis
    part = np.flatnonzero(hit & PARTIAL[first])
    if len(part):
        ok, t_shape, axis_shape = shape_hit(origin[part], direction[part], cell[part], first[part], t_start[part],
                                            out_axis[part], solid)
        hit[part[~ok]] = False
        first_t[part[ok]], out_axis[part[ok]] = t_shape[ok], axis_shape[ok]
    if hit.any():
        out_block[hit], out_t[hit], out_cell[hit] = first[hit], first_t[hit], cell[hit]
        keep = ~hit
        idx, cell, step, t_max, t_delta, t_stop = idx[keep], cell[keep], step[keep], t_max[keep], t_delta[keep], t_stop[keep]

    while len(idx):
        rows = np.arange(len(idx))
        axis = np.argmin(t_max, axis=1)
        t = t_max[rows, axis]
        cell[rows, axis] += step[rows, axis]
        t_max[rows, axis] += t_delta[rows, axis]
        moved = cell[rows, axis]
        inside = (moved >= 0) & (moved < dims[axis]) & (t < t_stop)
        block = np.zeros(len(idx), dtype=np.uint8)
        block[inside] = vol[cell[inside, 1], cell[inside, 2], cell[inside, 0]]
        hit = (block != 0) & ~INVISIBLE[block]
        if passable is not None:
            hit &= ~passable[block]
        part = np.flatnonzero(hit & PARTIAL[block])
        if len(part):
            ray = idx[part]
            ok, t_shape, axis_shape = shape_hit(origin[ray], direction[ray], cell[part], block[part], t[part],
                                                axis[part], solid)
            hit[part[~ok]] = False
            t[part[ok]], axis[part[ok]] = t_shape[ok], axis_shape[ok]
        if hit.any():
            where = idx[hit]
            out_block[where], out_axis[where], out_t[where], out_cell[where] = block[hit], axis[hit], t[hit], cell[hit]
        keep = inside & ~hit
        if not keep.all():
            idx, cell, step, t_max, t_delta, t_stop = idx[keep], cell[keep], step[keep], t_max[keep], t_delta[keep], t_stop[keep]
    return out_block, out_axis, out_t, out_cell


def box_span(origin, direction, low, high):
    """Where each ray enters and leaves the box [low, high], and through which axis it enters."""
    with np.errstate(divide="ignore", invalid="ignore"):
        inv = 1.0 / direction
        t1, t2 = (low - origin) * inv, (high - origin) * inv
    t1, t2 = np.nan_to_num(t1, nan=-np.inf), np.nan_to_num(t2, nan=np.inf)
    near = np.minimum(t1, t2)
    return near.max(axis=1), np.maximum(t1, t2).min(axis=1), near.argmax(axis=1)


# --------------------------------------------------------------------------- shading

# Vanilla Minecraft at noon in a plains biome: the sky colour overhead and the fog colour at the horizon
ZENITH = np.array([120, 167, 255], dtype=np.float32) / 255
HORIZON = np.array([192, 216, 255], dtype=np.float32) / 255
CLOUD_LAYER = 192 + 64                      # clouds float at y=192; layers count up from y=-64


def _cloud_map():
    field = _rng("clouds").random((64, 64))
    blur = sum(np.roll(np.roll(field, dz, 0), dx, 1) for dz in range(-2, 3) for dx in range(-2, 3))
    return blur > np.quantile(blur, 0.72)


_CLOUDS = _cloud_map()
AO_LEVELS = np.array([0.50, 0.68, 0.84, 1.0], dtype=np.float32)

# How light moves, as in the game: most blocks stop it, leaves and water dim it by one level per block,
# and air, glass, plants, fences, lanterns and doors let it through.
# Slabs and stairs are lit in their own cell (light comes in from above and the sides) but, like the game,
# their full bottom stops sky light going straight down.
_LIGHT_PASSES = np.array([n in ("air", "glass", "light_blue_stained_glass", "gray_stained_glass", "lantern", "barrier")
                          or n.endswith(("_fence", "_door", "_slab", "_stairs", "_wall")) or n == "rail"
                          for n in _names]) | CROSS
_DIMS_LIGHT = np.array([n.endswith("_leaves") or n == "water" for n in _names])
OPAQUE = ~(_LIGHT_PASSES | _DIMS_LIGHT)
_BLOCKS_SKY_BELOW = OPAQUE | np.array([n.endswith(("_slab", "_stairs")) for n in _names])
# what darkens the corners next to it (smooth lighting): full, solid cubes only
AO_SOLID = OPAQUE & ~BOXED
LIGHT_COST = _DIMS_LIGHT.astype(np.int16)
# see-through surfaces drawn over what is behind them: (how much of the surface shows)
SEE_THROUGH_SURFACE = np.zeros(len(p.BLOCKS), dtype=np.float32)
SEE_THROUGH_SURFACE[p.WATER] = 0.55
SEE_THROUGH_SURFACE[p.GLASS] = 0.25
for _b in p.GLASSES[1:]:
    SEE_THROUGH_SURFACE[_b] = 0.5
LOOK_THROUGH = SEE_THROUGH_SURFACE > 0


def _lightmap():
    """Brightness of light levels 0..15 in daylight, the way the game turns levels into brightness
    (with the default brightness setting)."""
    f = np.arange(16) / 15.0
    f1 = f / (4.0 - 3.0 * f)
    return np.maximum(0.05, f1 * 0.5 + (1 - (1 - f1) ** 4) * 0.5).astype(np.float32)


LIGHTMAP = _lightmap()
# the game's fixed brightness for each face direction: top, bottom, north/south, east/west
FACE_SHADE = {"top": 1.0, "bottom": 0.5, "z": 0.8, "x": 0.6}
_LIGHT_CACHE = {}


def sky_light(vol):
    """Sky light level (0-15) of every cell: 15 under open sky, one less for every block it travels sideways or
    down into shade, as in the game."""
    key = (id(vol), vol.shape)
    if key in _LIGHT_CACHE:
        return _LIGHT_CACHE[key]
    opaque = OPAQUE[vol]
    cost = LIGHT_COST[vol]
    light = np.zeros(vol.shape, dtype=np.int16)
    level = np.full(vol.shape[1:], 15, dtype=np.int16)
    for y in range(vol.shape[0] - 1, -1, -1):          # straight down from the sky
        level = np.where(opaque[y], 0, np.maximum(level - cost[y], 0))
        light[y] = level
        level = np.where(_BLOCKS_SKY_BELOW[vol[y]], 0, level)
    for _ in range(15):                                 # then spreading sideways and down, losing 1 per block
        spread = np.zeros_like(light)
        spread[:-1] = np.maximum(spread[:-1], light[1:])
        spread[1:] = np.maximum(spread[1:], light[:-1])
        spread[:, :-1] = np.maximum(spread[:, :-1], light[:, 1:])
        spread[:, 1:] = np.maximum(spread[:, 1:], light[:, :-1])
        spread[:, :, :-1] = np.maximum(spread[:, :, :-1], light[:, :, 1:])
        spread[:, :, 1:] = np.maximum(spread[:, :, 1:], light[:, :, :-1])
        new = np.where(opaque, 0, np.maximum(light, spread - 1 - cost))
        if np.array_equal(new, light):
            break
        light = new
    light = light.astype(np.uint8)
    _LIGHT_CACHE.clear()
    _LIGHT_CACHE[key] = light
    return light


def sky_colour(origin, direction):
    up = np.clip(direction[:, 1], 0, 1)[:, None] ** 0.5
    colour = HORIZON * (1 - up) + ZENITH * up
    rising = direction[:, 1] > 0.015
    t = np.where(rising, (CLOUD_LAYER - origin[:, 1]) / np.where(rising, direction[:, 1], 1), 0)
    cx = np.floor((origin[:, 0] + direction[:, 0] * t) / 12).astype(np.int64) % _CLOUDS.shape[0]
    cz = np.floor((origin[:, 2] + direction[:, 2] * t) / 12).astype(np.int64) % _CLOUDS.shape[1]
    cover = (rising & _CLOUDS[cx, cz]) * np.clip(1.25 - t / 2600.0, 0, 1) * 0.92
    return colour * (1 - cover[:, None]) + cover[:, None]


def shade(vol, tex, mean, solid, light, origin, direction, top, fog, depth=0):
    """Colour of each ray. origin: (n, 3) or (3,). Rays through water or glass also show what is behind."""
    n = len(direction)
    origin = np.broadcast_to(np.asarray(origin, dtype=float), (n, 3))
    size_x, size_z = vol.shape[2], vol.shape[1]
    colour = sky_colour(origin, direction).astype(np.float32)
    below = direction[:, 1] < 0                                       # the void below the horizon: fog colour
    colour[below] = HORIZON

    t_in, t_out, axis_in = box_span(origin, direction, np.zeros(3), np.array([size_x, top, size_z], dtype=float))
    t_in = np.maximum(t_in, 0) + 1e-6
    go = np.flatnonzero(t_out > t_in)
    block = np.zeros(n, dtype=np.uint8)
    axis = np.ones(n, dtype=np.int64)
    t_hit = np.full(n, np.inf)
    cell = np.zeros((n, 3), dtype=np.int64)
    block[go], axis[go], t_hit[go], cell[go] = march(vol, origin[go], direction[go], t_in[go],
                                                     np.minimum(t_out[go], fog[1] + 2), axis_in[go], solid,
                                                     LOOK_THROUGH if depth else None)
    hits = np.flatnonzero(block != 0)
    if not len(hits):
        return colour
    d, ax, blk, t = direction[hits], axis[hits], block[hits], t_hit[hits]
    point = origin[hits] + d * t[:, None]
    frac = point - np.floor(point)
    rows = np.arange(len(hits))
    cube = ax < 3
    normal = np.zeros((len(hits), 3))
    normal[rows[cube], ax[cube]] = -np.sign(d[rows[cube], ax[cube]])
    for axis_id, nv in ((3, (1.0, 0.0, -1.0)), (4, (1.0, 0.0, 1.0))):      # the crossed plant pictures
        m = ax == axis_id
        nv = np.array(nv) / math.sqrt(2)
        normal[m] = np.where(((d[m] @ nv) < 0)[:, None], nv, -nv)
    local = point - cell[hits]

    # texture: top faces use x,z; side faces use the horizontal direction and height; plants use x and height
    u = np.where(ax == 0, frac[:, 2], frac[:, 0])
    v = np.where(ax == 1, frac[:, 2], 1.0 - frac[:, 1])
    u = np.where(cube, u, np.clip(local[:, 0], 0, 1))
    v = np.where(cube, v, np.clip(1.0 - local[:, 1], 0, 1))
    face = (ax != 1).astype(np.int64)
    texel = tex[blk, face, np.minimum((v * T).astype(np.int64), T - 1), np.minimum((u * T).astype(np.int64), T - 1)]
    blend = (np.clip((t - 60.0) / 160.0, 0, 1) * 0.85)[:, None]       # far away, textures melt into their average
    base = texel * (1 - blend) + mean[blk, face] * blend

    # light level: from the cell in front of the face (or the block's own cell, for shapes that are not full cubes)
    dims = np.array([size_x, vol.shape[0], size_z])
    own = cell[hits]
    front = np.where((cube & ~PARTIAL[blk])[:, None], own + normal.round().astype(np.int64), own)
    front = np.clip(front, 0, dims - 1)
    level = light[front[:, 1], front[:, 2], front[:, 0]]
    outside = ((own + normal.round().astype(np.int64) < 0) | (own + normal.round().astype(np.int64) >= dims)).any(axis=1)
    level = np.where(outside & cube, 15, level)
    bright = LIGHTMAP[level]
    bright[GLOWING[blk]] = 1.0

    # the game's fixed shade per face direction
    shade_face = np.where(ax == 1, np.where(normal[:, 1] > 0, FACE_SHADE["top"], FACE_SHADE["bottom"]),
                          np.where(ax == 2, FACE_SHADE["z"], FACE_SHADE["x"]))
    shade_face = np.where(cube, shade_face, 0.9)

    # smooth lighting: darker in corners (ambient occlusion), from the 8 blocks around the face
    ao = np.ones(len(hits), dtype=np.float32)
    grid = np.flatnonzero(cube)
    if len(grid):
        front_g = own[grid] + normal[grid].round().astype(np.int64)

        def is_solid(c):
            ok = ((c >= 0) & (c < dims)).all(axis=1)
            out = np.zeros(len(c), dtype=bool)
            b = vol[c[ok, 1], c[ok, 2], c[ok, 0]]
            out[ok] = AO_SOLID[b]
            return out

        a_axis = np.where(ax[grid] == 0, 1, 0)                 # the two axes that lie in the face
        b_axis = np.where(ax[grid] == 2, 1, 2)
        g_rows = np.arange(len(grid))
        fa, fb = frac[grid][g_rows, a_axis], frac[grid][g_rows, b_axis]

        def shifted(da, db):
            c = front_g.copy()
            c[g_rows, a_axis] += da
            c[g_rows, b_axis] += db
            return is_solid(c)

        total = np.zeros(len(grid), dtype=np.float32)
        for sa in (-1, 1):
            side_a = shifted(sa, 0)
            for sb in (-1, 1):
                side_b = shifted(0, sb)
                lvl = np.where(side_a & side_b, 0, 3 - (side_a.astype(int) + side_b + shifted(sa, sb)))
                total += AO_LEVELS[lvl] * (fa if sa > 0 else 1 - fa) * (fb if sb > 0 else 1 - fb)
        ao[grid] = total
    result = base * (bright * shade_face * ao)[:, None]

    # water and glass: blend the surface over what is behind it
    clear = np.flatnonzero(LOOK_THROUGH[blk])
    if len(clear) and depth == 0:
        behind = shade(vol, tex, mean, solid, light, point[clear] + d[clear] * 1e-3, d[clear], top,
                       (fog[0] - t[clear].min(), fog[1] - t[clear].min()), depth=1)
        keep = SEE_THROUGH_SURFACE[blk[clear]][:, None]
        result[clear] = result[clear] * keep + behind * (1 - keep)

    haze = np.clip((t - fog[0]) / (fog[1] - fog[0]), 0, 1)[:, None]  # the game's fog is linear
    colour[hits] = result * (1 - haze) + HORIZON * haze
    return colour


def render(vol, eye, target, width=1280, height=720, fov=70.0, ssaa=2, render_distance=16, textures=None,
           crosshair=False):
    """Draw the world as the game would at noon, from camera position `eye` looking at `target`.
    Coordinates are (x, layer, z) in blocks. fov is the game's field of view setting (vertical, default 70).
    render_distance is in chunks, like the game's setting: fog closes in near its end.
    textures: None (built-in) or a TextureSource."""
    tex, mean, solid = make_textures(textures)
    light = sky_light(vol)
    eye, target = np.array(eye, dtype=float), np.array(target, dtype=float)
    forward = target - eye
    forward /= np.linalg.norm(forward)
    right = np.cross(forward, (0.0, 1.0, 0.0))
    right /= np.linalg.norm(right)
    up = np.cross(right, forward)
    w, h = width * ssaa, height * ssaa
    half = math.tan(math.radians(fov) / 2)
    xs = (2 * (np.arange(w) + 0.5) / w - 1) * half * w / h
    ys = (1 - 2 * (np.arange(h) + 0.5) / h) * half
    top = float(np.flatnonzero(vol.reshape(vol.shape[0], -1).any(axis=1))[-1] + 2)
    distance = render_distance * 16.0
    fog = (distance - min(max(distance / 10, 4), 64), distance)
    out = np.zeros((h, w, 3), dtype=np.float32)
    band = max(1, 300_000 // w)
    for r0 in range(0, h, band):
        yy, xx = np.meshgrid(ys[r0:r0 + band], xs, indexing="ij")
        direction = forward + xx[..., None] * right + yy[..., None] * up
        direction = (direction / np.linalg.norm(direction, axis=-1, keepdims=True)).reshape(-1, 3)
        out[r0:r0 + band] = shade(vol, tex, mean, solid, light, eye, direction, top, fog).reshape(-1, w, 3)
    out = out.reshape(height, ssaa, width, ssaa, 3).mean(axis=(1, 3))
    img = (np.clip(out, 0, 1) * 255 + 0.5).astype(np.uint8)
    if crosshair:                                     # a plain + in the middle, like the game's aiming mark
        cy, cx, r = height // 2, width // 2, max(6, height // 70)
        thick = max(1, height // 360)
        for sl in ((slice(cy - thick, cy + thick + 1), slice(cx - r, cx + r + 1)),
                   (slice(cy - r, cy + r + 1), slice(cx - thick, cx + thick + 1))):
            img[sl] = 255 - img[sl]
    return Image.fromarray(img)


ROAD_BLOCKS = [p.ROAD, p.ROAD_WORN, p.WHITE_CONCRETE]


def road_layers(vol):
    """For every column: the layer of its road block, or -1 if it has none."""
    found = np.full(vol.shape[1:], -1, dtype=np.int64)
    for y in range(vol.shape[0]):
        found[np.isin(vol[y], ROAD_BLOCKS)] = y
    return found


def street_camera(vol):
    """A spot on a road near the middle, looking along the longest clear stretch of road."""
    size_z, size_x = vol.shape[1], vol.shape[2]
    layer = road_layers(vol)
    zz, xx = np.nonzero(layer >= 0)
    if not len(zz):
        return None
    ly = layer[zz, xx]
    clear = np.ones(len(zz), dtype=bool)
    for i in range(1, 13):
        above = np.minimum(ly + i, vol.shape[0] - 1)
        clear &= vol[above, zz, xx] == p.AIR
    road = np.zeros((size_z, size_x), dtype=bool)
    road[zz[clear], xx[clear]] = True
    zz, xx = np.nonzero(road)
    if not len(zz):
        return None
    order = np.argsort((zz - size_z / 2) ** 2 + (xx - size_x / 2) ** 2)[:600:6]
    best = None
    for i in order:
        z, x = int(zz[i]), int(xx[i])
        for angle in np.linspace(0, 2 * np.pi, 24, endpoint=False):
            dx, dz = math.cos(angle), math.sin(angle)
            n = 1
            while n < 160:
                cx, cz = int(x + dx * n), int(z + dz * n)
                if not (0 <= cx < size_x and 0 <= cz < size_z and road[cz, cx]
                        and abs(layer[cz, cx] - layer[z, x]) <= 6):
                    break
                n += 1
            if best is None or n > best[0]:
                best = (n, x, z, dx, dz)
    n, x, z, dx, dz = best
    ahead = min(40, n - 1)
    tz, tx = int(z + dz * ahead), int(x + dx * ahead)
    eye = (x + 0.5, layer[z, x] + 1 + 1.62, z + 0.5)              # the player's eyes are 1.62 blocks up
    return eye, (x + 0.5 + dx * 40, layer[tz, tx] + 1 + 1.62, z + 0.5 + dz * 40)


def roof_camera(vol):
    """Above the rooftops, a little way from the middle, looking at the middle."""
    size_z, size_x = vol.shape[1], vol.shape[2]
    cx, cz = size_x / 2, size_z / 2
    area = vol[:, int(cz) - 100:int(cz) + 100, int(cx) - 100:int(cx) + 100]
    solid = area.reshape(vol.shape[0], -1).any(axis=1)
    tallest = int(np.flatnonzero(solid)[-1])
    column = vol[:, int(cz), int(cx)]
    ground_here = int(np.flatnonzero(column != p.AIR)[-1]) if column.any() else p.FLOOR
    eye = (cx + 75, tallest + 28, cz + 85)
    return eye, (cx - 15, ground_here + 2, cz - 10)


def standard_views(vol, out_dir, width=1280, height=720, textures="auto"):
    """Three pictures of any place, drawn the way the game shows it at noon: standing in a street as a player
    (render distance 16), above the rooftops (16), and an overview from high up (32)."""
    source = resolve_textures(textures)
    print("Textures: " + (f"from {source.path}" if source else "built-in (no Minecraft Java installation found)"))
    sx, sz = vol.shape[2], vol.shape[1]
    column = vol[:, sz // 2, sx // 2]
    centre = (sx / 2, (int(np.flatnonzero(column != p.AIR)[-1]) if column.any() else p.FLOOR) + 4, sz / 2)
    top = int(np.flatnonzero(vol.reshape(vol.shape[0], -1).any(axis=1))[-1])
    views = []
    street = street_camera(vol)
    if street:
        views.append(("place2mc_view_player.png", street[0], street[1], 16, True))
    eye, target = roof_camera(vol)
    views.append(("place2mc_view_rooftops.png", eye, target, 16, False))
    views.append(("place2mc_view_overview.png", (sx * 0.92, max(150, top + 60), sz * 1.02), centre, 32, False))
    paths = []
    for name, eye, target, distance, aim in views:
        path = Path(out_dir) / name
        render(vol, eye, target, width, height, 70, render_distance=distance, textures=source,
               crosshair=aim).save(path)
        paths.append(path)
    return paths
