#!/usr/bin/env python3
"""
place2mc - turn a real-world place into a Minecraft Java Edition world (1.18+).

How it works: it downloads the map of the place from OpenStreetMap (roads,
buildings, water, parks, trees) and its hills from AWS Terrain Tiles, turns
them into blocks at 1 block = 1 metre, and writes the blocks into the region
files of a Minecraft world you choose.

Setup (once):
    pip install numpy pillow

Use:
    1. In Minecraft, create a new world: Creative, World Type = Superflat (Peaceful keeps slimes away).
       Then quit to the title screen. The world must not be open while the tool runs.
    2. python place2mc.py                      (asks for the place and the world)
       or: python place2mc.py "Needham, Massachusetts" --world "PATH/TO/saves/MyFlatWorld"
    3. Open the world and run the /tp command the tool printed. The centre of the place is at x=0, z=0.

Other options:
    --radius 400              half the side of the square to build, in metres (default 300)
    --center LAT,LON          use exact coordinates instead of a place name (for example --center 42.2810,-71.2372)
    --flat                    flat ground instead of the real hills (hills need internet the first time)
    --osm-file map.osm        work offline from a file exported from openstreetmap.org
    --preview-only            only make the preview picture, do not write the world
    --screenshots             also draw game-style 3D pictures of the result (needs mcrender.py): in the
                              afternoon sun with shadows, plus sunset and night
    --picture-time TIME       morning, midday, afternoon, sunset or night (default afternoon)
    --plain-pictures          draw the pictures in the game's plain noon light instead (faster)
    --textures PATH           textures for the pictures: a game .jar or a resource pack (default: your own
                              Minecraft Java install if it is found, otherwise built-in textures)

Region files the tool replaces are first copied to *.bak files, but use a fresh
world anyway. Map data (c) OpenStreetMap contributors, ODbL. Elevation data:
AWS Terrain Tiles (Mapzen / Tilezen and their sources).
"""
import argparse
import base64
import bz2
import gzip
import io
import json
import math
import os
import re
import shutil
import struct
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zlib
from pathlib import Path

import numpy as np
from collections import deque
from PIL import Image, ImageDraw

VERSION = "1.9"
USER_AGENT = f"place2mc/{VERSION} (hobby project)"
OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]
# when every Overpass server is busy: OpenStreetMap's own map service (up to 50,000 points per area)
OSM_MAP_URL = "https://api.openstreetmap.org/api/0.6/map?bbox={w:.6f},{s:.6f},{e:.6f},{n:.6f}"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"

DATA_VERSION = 2975   # Minecraft 1.18.2, the oldest version every block here exists in; newer versions upgrade it on load
Y_MIN = -64           # bottom of the world since 1.18
GROUND = 3            # layer index of the surface block (y = -61, same as a default Superflat world)
FLOOR = GROUND + 1    # first layer above the ground (y = -60)
MAX_RADIUS = 1000
MAX_VOLUME_BYTES = 1_500_000_000

# --------------------------------------------------------------------------- blocks

BLOCKS = []      # index -> (name, properties or None)
_BLOCK_IDS = {}


def block(name, **props):
    key = (name, tuple(sorted(props.items())))
    if key not in _BLOCK_IDS:
        _BLOCK_IDS[key] = len(BLOCKS)
        BLOCKS.append(("minecraft:" + name, dict(props) or None))
    return _BLOCK_IDS[key]


AIR = block("air")
BEDROCK = block("bedrock")
DIRT = block("dirt")
GRASS = block("grass_block")
SAND = block("sand")
WATER = block("water", level="0")
GRAVEL = block("gravel")
SMOOTH_STONE = block("smooth_stone")
ROAD = block("gray_concrete")
PAVED = block("light_gray_concrete")
DIRT_PATH = block("dirt_path")
COARSE_DIRT = block("coarse_dirt")
PITCH = block("green_concrete")
GLASS = block("glass")
STONE = block("stone")
BARRIER = block("barrier")      # invisible solid wall: keeps the sea from pouring off the edge of the built area
# plants that grow on grass, like in the game's own plains and forests ("grass" is renamed "short_grass" in 1.20.3;
# the game renames it by itself when it loads these 1.18.2 chunks)
SHORT_GRASS = block("grass")
TALL_GRASS = (block("tall_grass", half="lower"), block("tall_grass", half="upper"))
FERN = block("fern")
FLOWERS = [block(n) for n in ("dandelion", "poppy", "oxeye_daisy", "cornflower", "azure_bluet")]
BIOMES = ["minecraft:plains", "minecraft:forest", "minecraft:river", "minecraft:ocean", "minecraft:beach",
          "minecraft:savanna", "minecraft:desert", "minecraft:jungle", "minecraft:sparse_jungle", "minecraft:taiga",
          "minecraft:snowy_plains", "minecraft:snowy_taiga"]
PLAINS, FOREST, RIVER, OCEAN, BEACH = range(5)
STONE_BRICKS = block("stone_bricks")
BRICK = block("bricks")
POLISHED_ANDESITE = block("polished_andesite")
ANDESITE = block("andesite")
COBBLE = block("cobblestone")
PODZOL = block("podzol")
WHITE_CONCRETE = block("white_concrete")
ROAD_WORN = block("gray_concrete_powder")
PAVED_WORN = block("light_gray_concrete_powder")
GLASSES = [GLASS, block("light_blue_stained_glass"), block("gray_stained_glass"), block("light_blue_stained_glass")]
BASES = [POLISHED_ANDESITE, STONE_BRICKS, SMOOTH_STONE]
ROOF_UNITS = [block("iron_block"), SMOOTH_STONE, STONE_BRICKS]
# leaves are "persistent" so they never decay
TREES = [
    (block("oak_log"), block("oak_leaves", persistent="true")),
    (block("birch_log"), block("birch_leaves", persistent="true")),
    (block("spruce_log"), block("spruce_leaves", persistent="true")),
    (block("acacia_log"), block("acacia_leaves", persistent="true")),
    (block("jungle_log"), block("jungle_leaves", persistent="true")),
]
DEAD_BUSH = block("dead_bush")
# life under water. The game counts these as always full of water (Java: no waterlogged property needed; Bedrock:
# bedrock.py puts water in the second block layer of their cells, or the plants break)
SEAGRASS = block("seagrass")
TALL_SEAGRASS = (block("tall_seagrass", half="lower"), block("tall_seagrass", half="upper"))
KELP = block("kelp", age="25")                    # the top of a kelp stalk, fully grown so it stays as built
KELP_PLANT = block("kelp_plant")
UNDERWATER_PLANTS = (SEAGRASS, *TALL_SEAGRASS, KELP, KELP_PLANT)
# street-name signs: a dark board with white letters on a post, turned in 16 steps like the game's standing signs
# (rotation 0 faces south, 4 west, 8 north, 12 east)
SIGNS = [block("dark_oak_sign", rotation=str(r)) for r in range(16)]
# road signs from the map (eight directions each): STOP in white on a red board, GIVE WAY in red on a white one
STOP_SIGNS = [block("crimson_sign", rotation=str(r)) for r in range(0, 16, 2)]
GIVE_WAY_SIGNS = [block("birch_sign", rotation=str(r)) for r in range(0, 16, 2)]
HYDRANT = block("red_nether_brick_wall", up="true", **{side: "none" for side in ("north", "south", "west", "east")})
LITTER_BIN = block("composter", level="0")             # an empty composter: a wooden bin

# ---------------------------------------------------------------------------------------------- climate
# Every place gets the biome, grass colour, bare ground and trees of its climate, from the Köppen-Geiger map of
# Rubel et al. (2016), shipped with the kgcpy package (BSD-3), reduced to 0.25 degrees in data/koppen.png.
KOPPEN = ("Af Am As Aw BSh BSk BWh BWk Cfa Cfb Cfc Csa Csb Csc Cwa Cwb Cwc Dfa Dfb Dfc Dfd Dsa Dsb Dsc Dsd "
          "Dwa Dwb Dwc Dwd EF ET Ocean").split()
CLIMATES = {   # biome for open land, biome for woods, ground where nothing is mapped, trees, grass and leaf colours
    "tropical": dict(biomes=("jungle", "jungle"), ground="green",
                     trees={"jungle": 6, "oak": 5, "round": 4, "acacia": 3}, grass=(89, 201, 60), foliage=(48, 187, 11)),
    "savanna": dict(biomes=("savanna", "sparse_jungle"), ground="dry",
                    trees={"acacia": 9, "oak": 5, "round": 2}, grass=(191, 183, 85), foliage=(174, 164, 42)),
    "desert": dict(biomes=("desert", "savanna"), ground="desert",
                   trees={"acacia": 9, "oak": 2}, grass=(191, 183, 85), foliage=(174, 164, 42)),
    "steppe": dict(biomes=("savanna", "savanna"), ground="dry",
                   trees={"acacia": 5, "oak": 6, "round": 2}, grass=(176, 182, 86), foliage=(150, 160, 48)),
    "mediterranean": dict(biomes=("plains", "forest"), ground="green",
                          trees={"oak": 9, "round": 6, "spruce": 2, "acacia": 2}, grass=(150, 186, 84), foliage=(115, 165, 50)),
    "highland": dict(biomes=("plains", "forest"), ground="green",
                     trees={"oak": 8, "round": 6, "birch": 2, "acacia": 2, "spruce": 1}, grass=(145, 189, 89), foliage=(119, 171, 47)),
    "temperate": dict(biomes=("plains", "forest"), ground="green",
                      trees={"oak": 8, "round": 4, "birch": 4, "spruce": 4}, grass=(145, 189, 89), foliage=(119, 171, 47)),
    "continental": dict(biomes=("plains", "forest"), ground="green",
                        trees={"oak": 6, "round": 3, "birch": 6, "spruce": 5}, grass=(140, 186, 98), foliage=(112, 168, 62)),
    "boreal": dict(biomes=("taiga", "taiga"), ground="green",
                   trees={"spruce": 12, "birch": 4}, grass=(134, 183, 131), foliage=(104, 164, 100)),
    "polar": dict(biomes=("snowy_plains", "snowy_taiga"), ground="tundra",
                  trees={"spruce": 1}, grass=(128, 180, 151), foliage=(96, 161, 123)),
}
CLIMATE_OF = {"A": "tropical", "As": "savanna", "Aw": "savanna", "BW": "desert", "BS": "steppe", "Cs": "mediterranean",
              "Cw": "highland", "Cf": "temperate", "Da": "continental", "Db": "continental", "Dc": "boreal",
              "Dd": "boreal", "E": "polar"}
CLIMATE = dict(CLIMATES["temperate"], code="Cfb", kind="temperate")    # until a place is chosen


def koppen_at(lat, lon):
    """Köppen-Geiger code (like "Cfb") at a point; a coastal point that falls in the sea takes the nearest land."""
    from PIL import Image
    grid = np.asarray(Image.open(Path(__file__).resolve().parent / "data" / "koppen.png"))
    h, w = grid.shape
    y, x = min(int((90 - lat) / 180 * h), h - 1), int((lon + 180) / 360 * w) % w
    if 1 <= grid[y, x] <= 31:
        return KOPPEN[int(grid[y, x]) - 1]
    for r in range(1, 5):                         # a point by the sea: the commonest climate up to a degree inland
        window = grid[max(0, y - r):y + r + 1, [(x + dx) % w for dx in range(-r, r + 1)]]
        land = window[(window >= 1) & (window <= 31)]
        if len(land):
            values, counts = np.unique(land, return_counts=True)
            return KOPPEN[int(values[np.argmax(counts)]) - 1]
    return "Cfb"


def set_climate(lat=None, lon=None, code=None):
    """Choose the biomes, ground, trees and colours for a place (or for a Köppen code given directly)."""
    code = code or koppen_at(lat, lon)
    if code[0] == "A":
        kind = CLIMATE_OF.get(code, "tropical")
    elif code[0] in "BC":
        kind = CLIMATE_OF[code[:2]]
    elif code[0] == "D":
        kind = CLIMATE_OF["Da" if code[2] in "ab" else "Dc"]
    else:
        kind = "polar"
    CLIMATE.clear()
    CLIMATE.update(CLIMATES[kind], code=code, kind=kind)
    ground = CLIMATE["ground"]
    SURFACE_MIX[S_GRASS] = {"green": [(GRASS, 1)],
                            "dry": [(GRASS, 70), (COARSE_DIRT, 22), (DIRT, 8)],
                            "desert": [(SAND, 76), (COARSE_DIRT, 14), (GRAVEL, 10)],
                            "tundra": [(GRASS, 55), (COARSE_DIRT, 20), (GRAVEL, 15), (STONE, 10)]}[ground]
    SURFACE_MIX[S_BARE] = ([(SAND, 70), (COARSE_DIRT, 15), (GRAVEL, 15)] if ground == "desert" else
                           [(COARSE_DIRT, 45), (GRAVEL, 25), (DIRT, 15), (STONE, 15)])
    SURFACE_MIX[S_INDUSTRIAL] = ([(SAND, 45), (GRAVEL, 22), (COARSE_DIRT, 23), (ANDESITE, 10)] if ground == "desert"
                                 else [(GRASS, 62), (GRAVEL, 16), (COARSE_DIRT, 12), (ANDESITE, 10)])
    if ground in ("dry", "desert", "tundra"):
        PATCHY.add(S_GRASS)
    else:
        PATCHY.discard(S_GRASS)
    return CLIMATE

FACINGS = ("north", "south", "west", "east")          # index 0..3, used for stairs and doors
NORTH, SOUTH, WEST, EAST = range(4)
LAMP_POST = block("dark_oak_fence")
LANTERN = block("lantern")
# inside buildings, parked cars, traffic lights
BOOKSHELF = block("bookshelf")
DESK = block("spruce_slab", type="top")
CEILING_LAMP = block("lantern", hanging="true")
CAR_COLOURS = [block(n) for n in ("white_concrete", "black_concrete", "red_concrete", "blue_concrete", "light_gray_concrete",
                                  "white_concrete", "black_concrete", "light_gray_concrete")]
SIGNAL_LIGHTS = [block("lime_concrete"), block("yellow_concrete"), block("red_concrete")]    # green, amber, red
DOORS = [{(f, half): block(name, facing=f, half=half, hinge="left", open="false", powered="false")
          for f in FACINGS for half in ("lower", "upper")}
         for name in ("dark_oak_door", "spruce_door", "birch_door")]
HOUSE_FLOOR = block("spruce_planks")
# half-height steps where a road or sidewalk goes up a block, so you can walk up without jumping
ROAD_SLAB = block("polished_deepslate_slab", type="bottom")
PAVING_SLAB = block("smooth_stone_slab", type="bottom")

# fences, walls, hedges, benches and railway tracks. Fences and walls store which sides they join (the game does
# not work it out again when it loads a world), so every combination is listed and build_volume picks the one
# that matches the neighbours. Bit i of the index is side i: north, south, west, east (the order of FACINGS).
FENCES = [block("oak_fence", **{side: str(bool(bits >> i & 1)).lower() for i, side in enumerate(FACINGS)})
          for bits in range(16)]
# walls have a post, except a straight run (joined north and south only, or west and east only)
GARDEN_WALLS = [block("stone_brick_wall", up="false" if bits in (0b0011, 0b1100) else "true",
                      **{side: "low" if bits >> i & 1 else "none" for i, side in enumerate(FACINGS)})
                for bits in range(16)]
BRICK_WALLS = [block("brick_wall", up="false" if bits in (0b0011, 0b1100) else "true",
                     **{side: "low" if bits >> i & 1 else "none" for i, side in enumerate(FACINGS)})
               for bits in range(16)]
HEDGE = TREES[0][1]
RAIL_SHAPES = ("north_south", "east_west", "ascending_north", "ascending_south", "ascending_west", "ascending_east",
               "south_east", "south_west", "north_west", "north_east")
RAILS = {shape: block("rail", shape=shape) for shape in RAIL_SHAPES}
# barrier kinds painted on the map; heights come from the map's height tag
B_NONE, B_FENCE, B_WALL, B_HEDGE, B_BRICK = range(5)
BARRIERS = {"fence": B_FENCE, "guard_rail": B_FENCE, "railing": B_FENCE, "handrail": B_FENCE,
            "wall": B_WALL, "retaining_wall": B_WALL, "city_wall": B_WALL, "hedge": B_HEDGE}
# railway tracks that get rails (others, like abandoned lines, are left out)
TRACKS = {"rail", "light_rail", "narrow_gauge", "tram", "subway", "monorail", "funicular", "preserved", "miniature"}

HOUSE_WALLS = [block(n) for n in ("bricks", "white_concrete", "oak_planks", "spruce_planks",
                                  "birch_planks", "white_terracotta", "smooth_sandstone", "terracotta")]
BIG_WALLS = [block(n) for n in ("light_gray_concrete", "white_concrete", "bricks", "stone_bricks",
                                "quartz_block", "smooth_sandstone", "polished_andesite")]
PLAIN_WALLS = [block(n) for n in ("light_gray_concrete", "stone_bricks", "polished_andesite")]
# pitched roofs: the full block, and the stairs made of the same material for the sloped edges
ROOF_MATERIALS = [("dark_oak_planks", "dark_oak_stairs"), ("spruce_planks", "spruce_stairs"),
                  ("deepslate_tiles", "deepslate_tile_stairs"), ("bricks", "brick_stairs"),
                  ("polished_blackstone_bricks", "polished_blackstone_brick_stairs"),
                  ("stone_bricks", "stone_brick_stairs")]
# used only when the map (or a --tags file) gives a roof colour: green and red metal roofs, light grey sheeting
TAG_ROOF_MATERIALS = [("dark_prismarine", "dark_prismarine_stairs"), ("red_nether_bricks", "red_nether_brick_stairs"),
                      ("polished_andesite", "polished_andesite_stairs")]
PITCHED_ROOFS = [block(full) for full, _ in ROOF_MATERIALS]
ROOF_STAIRS = {block(full): [block(stairs, facing=f, half="bottom", shape="straight") for f in FACINGS]
               for full, stairs in ROOF_MATERIALS + TAG_ROOF_MATERIALS}
TAG_WALLS = [block(n) for n in ("yellow_terracotta", "light_gray_terracotta")]
DEEPSLATE_ROOF = block("deepslate_tiles")
# inside buildings, by use (all blocks the converter has already)
BIRCH_PLANKS, QUARTZ, IRON_BLOCK = block("birch_planks"), block("quartz_block"), block("iron_block")
RED_CONCRETE, BLUE_CONCRETE, BLACK_CONCRETE = block("red_concrete"), block("blue_concrete"), block("black_concrete")
SPRUCE_PLANKS, DARK_OAK_PLANKS_BLOCK = block("spruce_planks"), block("dark_oak_planks")            # slate grey: measured sloped roofs the map says nothing about
FLAT_ROOFS = [block(n) for n in ("smooth_stone", "gray_concrete", "light_gray_concrete", "stone_bricks")]

# rooms (v1.9): beds (the game draws a bed from its block entity: write_world and bedrock.py add one for every bed
# block), carpets in offices and lounges
BEDS = {(f, part): block("red_bed", facing=f, occupied="false", part=part) for f in FACINGS for part in ("head", "foot")}
BED_BLOCKS = np.array(sorted(BEDS.values()), dtype=np.uint8)
GRAY_CARPET, RED_CARPET = block("gray_carpet"), block("red_carpet")
YELLOW_CONCRETE = block("yellow_concrete")
# what you can walk through (doors open; carpets and signs are thin) for the floor plan's own check (join_pockets)
WALKABLE = np.array([n in ("minecraft:air", "minecraft:lantern") or n.endswith(("_door", "_carpet", "_sign"))
                     for n, _ in BLOCKS] + [False] * (256 - len(BLOCKS)))
# what join_pockets never takes away to open a way: stairs (flights and chairs), beds, the outside walls' glass
KEEP_IN_PLACE = np.array([n.endswith(("_stairs", "_bed", "glass")) for n, _ in BLOCKS] + [True] * (256 - len(BLOCKS)))

assert len(BLOCKS) < 256, "too many block types for the uint8 volume"

# rough colours for the preview picture
BLOCK_RGB = {
    "grass_block": (112, 168, 78), "sand": (222, 210, 160), "water": (64, 110, 220), "gravel": (136, 130, 126),
    "smooth_stone": (168, 168, 168), "gray_concrete": (62, 66, 70), "light_gray_concrete": (130, 130, 124),
    "dirt_path": (150, 122, 70), "coarse_dirt": (122, 90, 60), "green_concrete": (78, 96, 40),
    "bricks": (150, 88, 72), "white_concrete": (214, 218, 218), "oak_planks": (168, 136, 84),
    "spruce_planks": (116, 86, 50), "birch_planks": (198, 182, 124), "white_terracotta": (212, 180, 162),
    "smooth_sandstone": (224, 214, 170), "terracotta": (152, 94, 68), "stone_bricks": (122, 122, 122),
    "quartz_block": (236, 230, 222), "polished_andesite": (134, 136, 136), "dark_oak_planks": (68, 44, 22),
    "deepslate_tiles": (56, 56, 58), "gray_terracotta": (58, 42, 36), "brown_terracotta": (78, 52, 36),
    "andesite": (136, 136, 137), "cobblestone": (122, 122, 122), "podzol": (92, 64, 26),
    "gray_concrete_powder": (76, 81, 84), "light_gray_concrete_powder": (154, 154, 148), "iron_block": (220, 220, 220),
    "dirt": (134, 96, 67), "polished_blackstone_bricks": (48, 42, 50), "stone": (125, 125, 125),
    "dark_prismarine": (52, 92, 76), "red_nether_bricks": (69, 7, 9), "yellow_terracotta": (186, 133, 35),
    "light_gray_terracotta": (135, 107, 98), "oak_fence": (168, 136, 84), "bookshelf": (117, 94, 59),
    "dark_oak_sign": (68, 44, 22), "crimson_sign": (101, 48, 70), "birch_sign": (196, 180, 122),
    "red_nether_brick_wall": (69, 7, 9), "composter": (112, 70, 31),
    "spruce_slab": (114, 84, 48), "black_concrete": (8, 10, 15), "red_concrete": (142, 33, 33),
    "lime_concrete": (94, 169, 24), "yellow_concrete": (241, 175, 21), "blue_concrete": (45, 47, 143), "dark_prismarine_stairs": (52, 92, 76),
    "red_nether_brick_stairs": (69, 7, 9), "polished_andesite_stairs": (134, 136, 136), "stone_brick_wall": (122, 122, 122), "brick_wall": (150, 97, 83), "oak_leaves": (58, 128, 44), "rail": (110, 96, 80),
}

# surface classes painted on the 2D map (one byte per block column). S_GRASS is also "nothing mapped here".
(S_GRASS, S_FOREST, S_PARK, S_FARM, S_SAND, S_WATER, S_PAVED, S_SIDEWALK, S_ROAD, S_PATH, S_FOOT, S_PITCH, S_GRAVEL,
 S_LAWN, S_PLAZA, S_QUAY, S_LINE, S_PARKING, S_SEA, S_INDUSTRIAL, S_BARE) = range(21)
# each surface is a weighted mix of blocks, so big areas do not look like one flat colour
SURFACE_MIX = {
    S_GRASS: [(GRASS, 1)], S_LAWN: [(GRASS, 1)], S_PARK: [(GRASS, 1)],
    S_FOREST: [(GRASS, 78), (PODZOL, 14), (COARSE_DIRT, 8)],
    S_FARM: [(COARSE_DIRT, 60), (DIRT, 40)],
    S_SAND: [(SAND, 1)], S_WATER: [(WATER, 1)], S_SEA: [(WATER, 1)], S_PITCH: [(PITCH, 1)], S_LINE: [(WHITE_CONCRETE, 1)],
    S_PAVED: [(PAVED, 82), (PAVED_WORN, 18)],
    S_SIDEWALK: [(SMOOTH_STONE, 88), (POLISHED_ANDESITE, 12)],
    S_ROAD: [(ROAD, 90), (ROAD_WORN, 10)],
    S_PARKING: [(ROAD, 85), (ROAD_WORN, 15)],
    S_PATH: [(DIRT_PATH, 85), (COARSE_DIRT, 15)],
    S_FOOT: [(SMOOTH_STONE, 80), (ANDESITE, 20)],
    S_GRAVEL: [(GRAVEL, 85), (COBBLE, 15)],
    S_PLAZA: [(SMOOTH_STONE, 55), (POLISHED_ANDESITE, 30), (ANDESITE, 15)],
    S_QUAY: [(STONE_BRICKS, 70), (COBBLE, 15), (ANDESITE, 15)],
    # industrial land: the yards between buildings, in patches of grass, gravel and bare ground
    S_INDUSTRIAL: [(GRASS, 62), (GRAVEL, 16), (COARSE_DIRT, 12), (ANDESITE, 10)],
    S_BARE: [(COARSE_DIRT, 45), (GRAVEL, 25), (DIRT, 15), (STONE, 15)],          # bare ground seen from space
}
PATCHY = {S_INDUSTRIAL, S_BARE}         # surfaces mixed in patches several blocks across, not block by block
SURFACE_BLOCK = {surface: mix[0][0] for surface, mix in SURFACE_MIX.items()}   # main block, used for the preview

# highway type -> (width in blocks, surface class, has sidewalks)
ROADS = {
    "motorway": (14, S_ROAD, False), "trunk": (12, S_ROAD, False), "primary": (10, S_ROAD, True),
    "secondary": (9, S_ROAD, True), "tertiary": (8, S_ROAD, True), "residential": (6, S_ROAD, True),
    "unclassified": (6, S_ROAD, False), "living_street": (5, S_ROAD, False), "service": (4, S_ROAD, False),
    "busway": (5, S_ROAD, False), "raceway": (8, S_ROAD, False), "pedestrian": (5, S_FOOT, False),
    "footway": (2, S_FOOT, False), "steps": (2, S_FOOT, False), "cycleway": (2, S_FOOT, False),
    "path": (2, S_PATH, False), "track": (3, S_PATH, False), "bridleway": (2, S_PATH, False),
}
WATERWAYS = {"river": 10, "canal": 6, "stream": 2, "ditch": 1, "drain": 1}

# area tags -> (surface class, draw priority; higher is drawn later, on top)
AREAS = {
    ("landuse", "forest"): (S_FOREST, 1), ("natural", "wood"): (S_FOREST, 1),
    ("landuse", "grass"): (S_LAWN, 1), ("landuse", "meadow"): (S_LAWN, 1), ("natural", "grassland"): (S_LAWN, 1),
    ("natural", "heath"): (S_LAWN, 1), ("natural", "scrub"): (S_PARK, 1), ("landuse", "cemetery"): (S_PARK, 1),
    ("landuse", "village_green"): (S_PARK, 1), ("landuse", "recreation_ground"): (S_PARK, 1),
    ("landuse", "orchard"): (S_PARK, 1), ("landuse", "allotments"): (S_FARM, 1),
    ("landuse", "farmland"): (S_FARM, 0), ("landuse", "farmyard"): (S_FARM, 0), ("landuse", "vineyard"): (S_FARM, 0),
    ("landuse", "commercial"): (S_PAVED, 0), ("landuse", "retail"): (S_PAVED, 0),
    ("landuse", "industrial"): (S_INDUSTRIAL, 0), ("landuse", "railway"): (S_GRAVEL, 0),
    ("landuse", "quarry"): (S_GRAVEL, 0), ("landuse", "construction"): (S_GRAVEL, 0),
    ("natural", "bare_rock"): (S_GRAVEL, 1), ("natural", "beach"): (S_SAND, 2), ("natural", "sand"): (S_SAND, 2),
    ("leisure", "park"): (S_PARK, 2), ("leisure", "garden"): (S_PARK, 2), ("leisure", "golf_course"): (S_PARK, 2),
    ("leisure", "dog_park"): (S_PARK, 2), ("leisure", "common"): (S_PARK, 2),
    ("leisure", "pitch"): (S_PITCH, 3), ("leisure", "playground"): (S_SAND, 3), ("leisure", "track"): (S_FOOT, 3),
    ("amenity", "parking"): (S_PARKING, 3),
    # land cover seen from space (ESA WorldCover, through Overture): trees beat a general land use like
    # "industrial" or a campus, but not parks' own paths, pitches, parking or water; crops and bare ground only
    # show where the map says nothing
    ("place2mc:land_cover", "forest"): (S_FOREST, 2.5), ("place2mc:land_cover", "mangrove"): (S_FOREST, 2.5),
    ("place2mc:land_cover", "shrub"): (S_PARK, 1.5), ("place2mc:land_cover", "wetland"): (S_PARK, 1.5),
    ("place2mc:land_cover", "crop"): (S_FARM, -1), ("place2mc:land_cover", "barren"): (S_BARE, -1),
    # school and college grounds: lawn, drawn over the landuse around them and under their pitches and gardens
    ("amenity", "school"): (S_PARK, 1), ("amenity", "college"): (S_PARK, 1), ("amenity", "university"): (S_PARK, 1),
    ("amenity", "kindergarten"): (S_PARK, 1),
    ("natural", "water"): (S_WATER, 4), ("landuse", "basin"): (S_WATER, 4), ("landuse", "reservoir"): (S_WATER, 4),
    ("waterway", "riverbank"): (S_WATER, 4), ("waterway", "dock"): (S_WATER, 4), ("leisure", "swimming_pool"): (S_WATER, 4),
}

CAMPUS_TYPES = {"school", "college", "university", "kindergarten"}
HOUSE_TYPES = {"house", "detached", "residential", "semidetached_house", "terrace", "bungalow", "cabin",
               "farm", "static_caravan"}
SMALL_TYPES = {"garage", "garages", "shed", "hut", "carport", "roof", "kiosk", "greenhouse"}
PLAIN_TYPES = {"industrial", "warehouse", "hangar", "barn", "farm_auxiliary", "service"}
SHOP_TYPES = {"yes", "retail", "commercial", "apartments", "office", "hotel", "mixed_use", "mixed"}
# ground that people walk or drive on, used to put doors, shop windows and lamps on the street side
STREET_SURFACES = (S_ROAD, S_LINE, S_SIDEWALK, S_FOOT, S_PLAZA, S_PAVED, S_PARKING, S_PATH, S_QUAY)
# number of floors (lowest, highest) to guess when the map gives no height; one floor is 4 blocks
FLOORS = {"apartments": (3, 6), "office": (3, 6), "hotel": (3, 7), "commercial": (1, 3), "retail": (1, 2),
          "industrial": (2, 3), "warehouse": (2, 3), "school": (1, 3), "university": (2, 4), "hospital": (2, 5),
          "civic": (2, 4), "public": (2, 4), "church": (3, 4), "chapel": (2, 2), "cathedral": (5, 6),
          "mosque": (3, 4), "train_station": (2, 3), "barn": (2, 2)}


# --------------------------------------------------------------------------- map data

def http_get_json(url, data=None, timeout=180):
    req = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT, "Accept-Language": "en"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def geocode(place):
    """Place name -> (lat, lon, full name), using OpenStreetMap's Nominatim search."""
    url = NOMINATIM_URL + "?" + urllib.parse.urlencode({"q": place, "format": "jsonv2", "limit": 1})
    results = http_get_json(url, timeout=30)
    if not results:
        sys.exit(f'Could not find a place called "{place}". Try adding the country or state.')
    return float(results[0]["lat"]), float(results[0]["lon"]), results[0].get("display_name", place)


def metres_per_degree(lat):
    phi = math.radians(lat)
    m_lat = 111132.92 - 559.82 * math.cos(2 * phi) + 1.175 * math.cos(4 * phi)
    m_lon = 111412.84 * math.cos(phi) - 93.5 * math.cos(3 * phi)
    return m_lat, m_lon


def overpass_query(lat, lon, radius):
    m_lat, m_lon = metres_per_degree(lat)
    r = radius + 50
    s, n = lat - r / m_lat, lat + r / m_lat
    w, e = lon - r / m_lon, lon + r / m_lon
    return f"""[out:json][timeout:120][bbox:{s:.6f},{w:.6f},{n:.6f},{e:.6f}];
(
  way["building"]; relation["building"];
  way["highway"];
  way["railway"~"^(rail|light_rail|narrow_gauge|tram|subway|monorail|funicular|preserved|miniature|platform)$"];
  way["barrier"~"^(fence|guard_rail|railing|handrail|wall|retaining_wall|city_wall|hedge)$"];
  way["waterway"]; relation["waterway"];
  way["natural"]; relation["natural"];
  way["landuse"]; relation["landuse"];
  way["leisure"]; relation["leisure"];
  way["amenity"="parking"];
  way["amenity"~"^(school|college|university|kindergarten)$"]; relation["amenity"~"^(school|college|university|kindergarten)$"];
  way["man_made"~"^(pier|breakwater|groyne)$"];
  node["natural"="tree"];
  node["highway"="crossing"];
  node["amenity"="bench"];
  node["highway"="traffic_signals"];
  node["highway"~"^(stop|give_way|bus_stop|street_lamp)$"];
  node["emergency"="fire_hydrant"];
  node["amenity"~"^(waste_basket|bicycle_parking|post_box|picnic_table)$"];
  node["leisure"="outdoor_seating"]; node["tourism"="artwork"]; node["man_made"="flagpole"];
  node["power"~"^(tower|portal)$"];
);
out geom;"""


def download_osm(lat, lon, radius):
    data = urllib.parse.urlencode({"data": overpass_query(lat, lon, radius)}).encode("utf-8")
    last_error = None
    for url in OVERPASS_URLS:
        try:
            print(f"Downloading map data from {urllib.parse.urlparse(url).netloc} ...")
            return http_get_json(url, data=data, timeout=90)["elements"]
        except Exception as exc:  # try the next server
            last_error = exc
            print(f"  failed: {exc}")
    m_lat, m_lon = metres_per_degree(lat)
    r = radius + 50
    url = OSM_MAP_URL.format(w=lon - r / m_lon, s=lat - r / m_lat, e=lon + r / m_lon, n=lat + r / m_lat)
    try:
        print("Downloading map data from api.openstreetmap.org instead ...")
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=180) as resp:
            return parse_osm_xml(io.BytesIO(resp.read()))[0]
    except Exception as exc:
        print(f"  failed: {exc}" + ("  (the area has too many map points for this service: try a smaller size)"
                                     if "400" in str(exc) else ""))
        last_error = exc
    sys.exit(f"Could not download map data ({last_error}). Check your internet connection and try again in a minute.")


# --------------------------------------------------------------------------- elevation

TILE_URL = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"
TILE_ZOOM = 15          # about 3-5 m per pixel, the finest these tiles go
MAX_RELIEF = 300        # highest hill above the lowest point, in blocks (the world is 384 blocks tall)


def decode_terrarium(rgb):
    """Terrarium tiles store metres as (red * 256 + green + blue / 256) - 32768."""
    rgb = rgb.astype(np.float64)
    return rgb[..., 0] * 256 + rgb[..., 1] + rgb[..., 2] / 256 - 32768


def fetch_tile(z, x, y, cache_dir):
    """One elevation tile as an RGB array. Kept in cache_dir so the same area is only downloaded once."""
    path = Path(cache_dir) / str(z) / str(x) / f"{y}.png" if cache_dir else None
    if path and path.exists():
        return np.array(Image.open(path).convert("RGB"))
    req = urllib.request.Request(TILE_URL.format(z=z, x=x, y=y), headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = resp.read()
    if path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return np.array(Image.open(io.BytesIO(data)).convert("RGB"))


def elevation_grid(lat0, lon0, size, cache_dir=None, zoom=TILE_ZOOM, fetch=fetch_tile):
    """Height in metres of every block column of the size x size square around lat0, lon0."""
    m_lat, m_lon = metres_per_degree(lat0)
    half = size / 2
    offsets = np.arange(size) + 0.5 - half
    lats = lat0 - offsets / m_lat                      # one per row (z, north to south)
    lons = lon0 + offsets / m_lon                      # one per column (x, west to east)
    n = 2 ** zoom
    gx = (lons + 180) / 360 * n * 256 - 0.5            # global pixel position, measured from pixel centres
    lat_r = np.radians(lats)
    gy = (1 - np.log(np.tan(lat_r) + 1 / np.cos(lat_r)) / math.pi) / 2 * n * 256 - 0.5
    tx0, tx1 = int(math.floor(gx.min() / 256)), int(math.floor((gx.max() + 1) / 256))
    ty0, ty1 = int(math.floor(gy.min() / 256)), int(math.floor((gy.max() + 1) / 256))
    mosaic = np.zeros(((ty1 - ty0 + 1) * 256, (tx1 - tx0 + 1) * 256))
    for ty in range(ty0, ty1 + 1):
        for tx in range(tx0, tx1 + 1):
            tile = decode_terrarium(fetch(zoom, tx % n, ty, cache_dir))
            mosaic[(ty - ty0) * 256:(ty - ty0 + 1) * 256, (tx - tx0) * 256:(tx - tx0 + 1) * 256] = tile
    px, py = gx - tx0 * 256, gy - ty0 * 256           # bilinear interpolation between tile pixels
    x0 = np.clip(np.floor(px).astype(int), 0, mosaic.shape[1] - 2)
    y0 = np.clip(np.floor(py).astype(int), 0, mosaic.shape[0] - 2)
    fx = np.clip(px - x0, 0, 1)[None, :]
    fy = np.clip(py - y0, 0, 1)[:, None]
    a, b = mosaic[y0][:, x0], mosaic[y0][:, x0 + 1]
    c, d = mosaic[y0 + 1][:, x0], mosaic[y0 + 1][:, x0 + 1]
    return (a * (1 - fx) + b * fx) * (1 - fy) + (c * (1 - fx) + d * fx) * fy


def box_blur(values, r):
    """Average over the square of side 2r+1 around each cell."""
    k = 2 * r + 1
    c = np.pad(values, r, mode="edge").cumsum(0).cumsum(1)
    c = np.pad(c, ((1, 0), (1, 0)))
    return (c[k:, k:] - c[:-k, k:] - c[k:, :-k] + c[:-k, :-k]) / float(k * k)


def terrain_layers(elev, ground, blur=2):
    """Metres -> the layer of the top block of every column, and the layer of the sea surface.
    The lowest land sits at y=-61 like a Superflat world. With a coast, sea level is y=-61 and land below sea
    level is raised to it, so the sea lies level with the flat world around the build and cannot spill out."""
    if blur:                                           # smooth out noise in the data (roughly 9 m across for tiles)
        elev = box_blur(box_blur(elev, blur), blur)
    sea = ground == S_SEA
    land = ~sea
    lowest = float(elev[land].min()) if land.any() else 0.0
    if sea.any():
        lowest = 0.0
    relief = elev - lowest
    scale = 1.0
    if relief.max() > MAX_RELIEF:
        scale = MAX_RELIEF / relief.max()
        print(f"Note: hills are {relief.max():.0f} m high here; heights are squeezed to {scale:.2f}x to fit the world.")
    layers = GROUND + np.round(np.clip(relief, 0, None) * scale).astype(np.int32)
    sea_level = GROUND + int(round(-lowest * scale))
    depth = np.where(sea, np.clip(-elev * scale, 0, None), 0.0)     # how deep the sea is (the tiles include it)
    return layers, sea_level, depth


def make_terrain(lat, lon, size, ground, cache_dir=None, fetch=fetch_tile, elev=None):
    """(layers, sea_level) for hills, or None for flat ground if the elevation data cannot be had.
    elev: the ground in metres for every block column (from a laser scan), instead of the elevation tiles."""
    blur = 2
    if elev is not None:
        blur = 1                                       # 1 m laser-scan ground: only take the edge off single cells
    else:
        try:
            elev = elevation_grid(lat, lon, size, cache_dir, fetch=fetch)
        except Exception as exc:
            print(f"Could not get elevation data ({exc}); building on flat ground instead.")
            return None
    layers, sea_level, depth = terrain_layers(elev, ground, blur=blur)
    # ramp the outer edge down to the flat world around the build, so it ends in a slope rather than a cliff
    band = max(1, min(48, size // 8))
    z, x = np.indices(layers.shape)
    edge = np.minimum(np.minimum(z, size - 1 - z), np.minimum(x, size - 1 - x))
    w = np.clip(edge / band, 0, 1)
    w = w * w * (3 - 2 * w)
    layers = GROUND + np.round((layers - GROUND) * w).astype(np.int32)
    print(f"Hills: {elev.min():.0f} m to {elev.max():.0f} m above sea level "
          f"({int(layers.max() - layers.min())} blocks from lowest to highest point)")
    return layers, sea_level, depth


def open_maybe_compressed(path):
    name = str(path).lower()
    if name.endswith(".gz"):
        return gzip.open(path, "rb")
    if name.endswith(".bz2"):
        return bz2.open(path, "rb")
    return open(path, "rb")


def load_lidar(path, lat0, lon0, size):
    """Read a LiDAR summary made by app/lidar.js (USGS 3DEP laser scans): the bare ground, the height of the roof above
    the ground in every 1 m cell of a building footprint, and every tree (x, z, height, crown radius, in blocks and
    metres). It must be made for the same centre and size as this run."""
    data = json.loads(Path(path).read_text())
    if data.get("format") == "place2mc-browser-1":           # the map and the scan in one file
        data = data["lidar"]
    if abs(data["lat0"] - lat0) > 1e-6 or abs(data["lon0"] - lon0) > 1e-6 or data["size"] != size:
        sys.exit(f"{path} was made for {data['lat0']},{data['lon0']} and {data['size']} blocks, not "
                 f"{lat0},{lon0} and {size}: use --center {data['lat0']},{data['lon0']} --radius {data['size'] // 2}.")

    def unpack(text, dtype):
        return np.frombuffer(gzip.decompress(base64.b64decode(text)), dtype=dtype)

    d = data["dtm"]
    n, step = d["n"], d["step"]
    coarse = np.cumsum(unpack(d["rowDiffInt16"], "<i2").reshape(n, n).astype(np.int64), axis=1) * d["unit"] + d["low"]
    # back to 1 m cells: bilinear between the centres of the coarse cells
    pos = (np.arange(size) + 0.5) / step - 0.5
    i0 = np.clip(np.floor(pos).astype(int), 0, n - 2)
    f = np.clip(pos - i0, 0, 1)
    rows = coarse[i0] * (1 - f)[:, None] + coarse[i0 + 1] * f[:, None]
    dtm = rows[:, i0] * (1 - f)[None, :] + rows[:, i0 + 1] * f[None, :]
    r = data["roofs"]
    runs = unpack(r["runsUint32"], "<u4")
    codes = unpack(r["codesUint8"], np.uint8)
    inside = np.repeat(np.arange(len(runs)) % 2 == 1, runs.astype(np.int64))
    if inside.size != size * size or inside.sum() != codes.size:
        sys.exit(f"{path} is damaged (its building cells do not add up).")
    roof = np.full(size * size, np.nan)
    roof[inside] = np.where(codes == 255, np.nan, codes * r["step"] + r["offset"])
    t = data["trees"]
    trees = unpack(t["xzhrInt16"], "<i2").reshape(-1, 4).astype(float) * t["unit"] if t["count"] else np.zeros((0, 4))
    trees = clean_trees(trees)
    unmapped = np.full(size * size, np.nan)
    u = data.get("unmapped")
    if u and u.get("cells"):
        u_runs = unpack(u["runsUint32"], "<u4")
        u_codes = unpack(u["codesUint8"], np.uint8)
        u_in = np.repeat(np.arange(len(u_runs)) % 2 == 1, u_runs.astype(np.int64))
        if u_in.size == size * size and u_in.sum() == u_codes.size:
            unmapped[u_in] = np.where(u_codes == 255, np.nan, u_codes * u["step"] + u["offset"])
    return {"dtm": dtm, "roof": roof.reshape(size, size), "trees": trees, "points": data.get("points"),
            "source": data.get("ept", ""), "unmapped": unmapped.reshape(size, size)}


def browser_elements(data):
    """The map in a file made by app/fetch-template.html: rows of [kind, id, tags, points] with the points as
    differences in 1e-7 degrees from the centre, back to Overpass's JSON (with geometry)."""
    def decode(flat):
        pts, lat, lon = [], data["osmBase"][0], data["osmBase"][1]
        for i in range(0, len(flat), 2):
            lat, lon = lat + flat[i], lon + flat[i + 1]
            pts.append({"lat": lat / 1e7, "lon": lon / 1e7})
        return pts

    elements = []
    for kind, osm_id, tags, points in data["rows"]:
        if kind == "n":
            p = decode(points)[0]
            elements.append({"type": "node", "id": osm_id, "lat": p["lat"], "lon": p["lon"], "tags": tags})
        elif kind == "w":
            elements.append({"type": "way", "id": osm_id, "tags": tags, "geometry": decode(points)})
        else:
            elements.append({"type": "relation", "id": osm_id, "tags": tags,
                             "members": [{"type": "way", "role": role, "geometry": decode(g)} for role, g in points]})
    return elements


def load_osm_file(path):
    """Read a saved Overpass .json file or an .osm XML export. Returns (elements, bounds or None)."""
    with open_maybe_compressed(path) as fh:
        head = fh.read(64).lstrip()
    if head.startswith(b"{"):
        with open_maybe_compressed(path) as fh:
            data = json.loads(fh.read().decode("utf-8"))
        elements = browser_elements(data) if data.get("format") == "place2mc-browser-1" else data["elements"]
        pts = [(p["lat"], p["lon"]) for el in elements for p in el.get("geometry") or [] if p]
        bounds = (min(p[0] for p in pts), min(p[1] for p in pts), max(p[0] for p in pts), max(p[1] for p in pts)) if pts else None
        return elements, bounds
    with open_maybe_compressed(path) as fh:
        return parse_osm_xml(fh)


POINT_HIGHWAYS = ("crossing", "traffic_signals", "stop", "give_way", "bus_stop", "street_lamp")


def parse_osm_xml(fh):
    """OpenStreetMap XML (an .osm export, or the map service's answer) -> (elements, bounds or None), in the
    same form as an Overpass answer. Keeps tagged ways and relations, and the points place2mc uses."""
    nodes, ways, elements, bounds = {}, {}, [], None
    if True:
        for _, el in ET.iterparse(fh, events=("end",)):
            if el.tag == "bounds":
                bounds = tuple(float(el.get(k)) for k in ("minlat", "minlon", "maxlat", "maxlon"))
            elif el.tag == "node":
                nid, lat, lon = int(el.get("id")), float(el.get("lat")), float(el.get("lon"))
                nodes[nid] = (lat, lon)
                tags = {t.get("k"): t.get("v") for t in el.findall("tag")}
                if tags.get("natural") == "tree" or tags.get("highway") in POINT_HIGHWAYS or \
                        tags.get("amenity") in ("bench", "waste_basket", "bicycle_parking", "post_box", "picnic_table") \
                        or tags.get("emergency") == "fire_hydrant" or tags.get("leisure") == "outdoor_seating" or \
                        tags.get("tourism") == "artwork" or tags.get("man_made") == "flagpole" or \
                        tags.get("power") in ("tower", "portal"):
                    elements.append({"type": "node", "id": nid, "lat": lat, "lon": lon, "tags": tags})
                el.clear()
            elif el.tag == "way":
                geom = [{"lat": nodes[r][0], "lon": nodes[r][1]}
                        for r in (int(nd.get("ref")) for nd in el.findall("nd")) if r in nodes]
                tags = {t.get("k"): t.get("v") for t in el.findall("tag")}
                wid = int(el.get("id"))
                ways[wid] = geom
                if tags:
                    elements.append({"type": "way", "id": wid, "geometry": geom, "tags": tags})
                el.clear()
            elif el.tag == "relation":
                tags = {t.get("k"): t.get("v") for t in el.findall("tag")}
                members = [{"type": "way", "role": m.get("role") or "", "geometry": ways.get(int(m.get("ref")), [])}
                           for m in el.findall("member") if m.get("type") == "way"]
                elements.append({"type": "relation", "id": int(el.get("id")), "members": members, "tags": tags})
                el.clear()
    if bounds is None and nodes:
        lats, lons = [p[0] for p in nodes.values()], [p[1] for p in nodes.values()]
        bounds = (min(lats), min(lons), max(lats), max(lons))
    return elements, bounds


def assemble_rings(ways):
    """Join way pieces that share end points into closed rings."""
    pool = [list(w) for w in ways if len(w) >= 2]
    rings = []
    while pool:
        cur = pool.pop()
        while cur[0] != cur[-1]:
            for i, w in enumerate(pool):
                if w[0] == cur[-1]:
                    cur += w[1:]
                elif w[-1] == cur[-1]:
                    cur += w[-2::-1]
                else:
                    continue
                pool.pop(i)
                break
            else:
                break
        if len(cur) >= 4 and cur[0] == cur[-1]:
            rings.append(cur)
    return rings


# --------------------------------------------------------------------------- building the map

def parse_height(tags):
    """Building height in blocks from the map tags, or None if the map does not say."""
    for key in ("height", "building:height"):
        raw = tags.get(key)
        if raw:
            try:
                text = raw.lower().replace(",", ".").strip()
                if text.endswith("ft") or "'" in text:
                    return max(3, round(float(text.replace("ft", "").replace("'", "").split()[0]) * 0.3048))
                return max(3, round(float(text.replace("m", "").split()[0])))
            except (ValueError, IndexError):
                pass
    return None


def parse_levels(tags):
    """building:levels as a whole number of floors above ground, or None."""
    try:
        levels = round(float(tags.get("building:levels", "").replace(",", ".")))
    except ValueError:
        return None
    return max(1, min(levels, 100)) if levels > 0 else None


# colours and materials from the map's tags (building:colour, building:material, roof:colour, roof:material)
NAMED_COLOURS = {"white": (240, 240, 240), "black": (30, 30, 30), "grey": (128, 128, 128), "gray": (128, 128, 128),
                 "silver": (192, 192, 192), "red": (170, 40, 30), "maroon": (110, 25, 20), "brown": (120, 80, 50),
                 "beige": (225, 210, 180), "cream": (240, 230, 200), "tan": (210, 180, 140), "yellow": (220, 190, 70),
                 "orange": (220, 120, 40), "green": (60, 130, 60), "darkgreen": (40, 80, 50), "blue": (50, 80, 160),
                 "navy": (30, 40, 90), "pink": (230, 170, 180), "lightgrey": (200, 200, 200), "darkgrey": (70, 70, 70)}
WALL_MATERIALS = {"brick": "bricks", "bricks": "bricks", "stone": "stone_bricks", "concrete": "light_gray_concrete",
                  "plaster": "white_concrete", "render": "white_concrete", "stucco": "white_concrete",
                  "wood": "oak_planks", "timber_framing": "oak_planks", "sandstone": "smooth_sandstone",
                  "metal": "light_gray_concrete", "glass": "light_blue_stained_glass"}
ROOF_MATERIAL_COLOURS = {"metal": (125, 128, 130), "roof_tiles": (150, 70, 50), "tile": (150, 70, 50),
                         "slate": (60, 60, 65), "concrete": (150, 150, 150), "thatch": (140, 110, 60),
                         "asphalt": (60, 60, 60), "tar_paper": (60, 60, 60), "copper": (90, 140, 110)}


def parse_colour(text):
    text = (text or "").strip().lower().replace(" ", "").replace("_", "")
    if text.startswith("#") and len(text) in (4, 7):
        h = text[1:] if len(text) == 7 else "".join(c * 2 for c in text[1:])
        try:
            return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
        except ValueError:
            return None
    return NAMED_COLOURS.get(text)


def nearest_block(rgb, blocks):
    """The block whose colour is closest, counting the hue twice as much as the brightness (dark green should
    become a dark green block, not a dark grey one)."""
    def dist(other):
        m1, m2 = sum(rgb) / 3, sum(other) / 3
        return sum((a - c) ** 2 + 2 * ((a - m1) - (c - m2)) ** 2 for a, c in zip(rgb, other))
    names = [BLOCKS[b][0].split(":")[1] for b in blocks]
    return min(zip(blocks, names), key=lambda bn: dist(BLOCK_RGB[bn[1]]))[0]


def wall_from_tags(tags):
    colour = parse_colour(tags.get("building:colour"))
    if colour:
        return nearest_block(colour, HOUSE_WALLS + BIG_WALLS + TAG_WALLS)
    material = WALL_MATERIALS.get((tags.get("building:material") or "").lower())
    return block(material) if material else None


def roof_from_tags(tags, pitched):
    colour = parse_colour(tags.get("roof:colour")) or ROOF_MATERIAL_COLOURS.get((tags.get("roof:material") or "").lower())
    if not colour:
        return None
    return nearest_block(colour, list(ROOF_STAIRS) if pitched else FLAT_ROOFS + list(ROOF_STAIRS))


def apply_tag_file(elements, path):
    """A --tags file adds map tags the map does not have yet (they never replace tags the map has):
    {"objects": {"way/123": {tags}}, "inside": [{"area": "way/456", "tags": {tags}, "min_area": 60}]}; and "replace":
    {"way/123": {tags}} corrects tags the map has wrong (a misspelt name, say). "inside" gives
    its tags to every building whose middle lies inside that area (and, with min_area, whose footprint is at least
    that many square metres, so sheds and kiosks keep their own size)."""
    rules = json.loads(Path(path).read_text(encoding="utf-8"))
    by_id = {f"{el.get('type')}/{el.get('id')}": el for el in elements}
    changed = 0
    for key, extra in (rules.get("objects") or {}).items():
        el = by_id.get(key)
        if el is not None:
            tags = el.setdefault("tags", {})
            for k, v in extra.items():
                tags.setdefault(k, v)
            changed += 1
    for key, fixed in (rules.get("replace") or {}).items():   # corrections: these do replace the map's tags
        el = by_id.get(key)
        if el is not None:
            el.setdefault("tags", {}).update(fixed)
            changed += 1
    for rule in rules.get("inside") or []:
        area = by_id.get(rule.get("area"))
        ring = [(p["lat"], p["lon"]) for p in (area or {}).get("geometry") or [] if p]
        if len(ring) < 3:
            continue
        for el in elements:
            tags = el.get("tags") or {}
            if tags.get("building") in (None, "no"):
                continue
            pts = [(p["lat"], p["lon"]) for p in el.get("geometry") or [] if p] or \
                  [(p["lat"], p["lon"]) for m in el.get("members") or [] if m.get("role") != "inner"
                   for p in m.get("geometry") or [] if p]
            if len(pts) >= 3 and rule.get("min_area"):
                k = 111320 * math.cos(math.radians(pts[0][0]))
                metres = [((q[1] - pts[0][1]) * k, (q[0] - pts[0][0]) * 110540) for q in pts]
                if polygon_area(metres) < rule["min_area"]:
                    continue
            if pts and point_in_ring(sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts), ring):
                for k, v in rule.get("tags", {}).items():
                    tags.setdefault(k, v)
                el["tags"] = tags
                changed += 1
    return changed


def point_in_ring(lat, lon, ring):
    inside = False
    for (y1, x1), (y2, x2) in zip(ring, ring[1:] + ring[:1]):
        if (y1 > lat) != (y2 > lat) and lon < x1 + (lat - y1) * (x2 - x1) / (y2 - y1):
            inside = not inside
    return inside


def polygon_area(pts):
    return abs(sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1]))) / 2


def build_map(elements, lat0, lon0, size, climate=None):
    """Paint the map data onto 2D arrays. Returns everything build_volume needs. Also sets the place's climate
    (or the Köppen code given as climate), which build_volume uses for ground, trees and biomes."""
    set_climate(lat0, lon0, climate)
    m_lat, m_lon = metres_per_degree(lat0)
    half = size / 2

    def px(pt):
        return ((pt[1] - lon0) * m_lon + half, (lat0 - pt[0]) * m_lat + half)

    def touches(pts):
        if not pts:
            return False
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        return max(xs) >= -20 and min(xs) < size + 20 and max(ys) >= -20 and min(ys) < size + 20

    areas, roads, waterways, rails, buildings, trees, coast, piers, marked = [], [], [], [], [], [], [], [], []
    crossing_ways, crossing_nodes, barriers, benches, campuses, industrial, signals = [], [], [], [], [], [], []
    bridges = []          # (width, surface class, line, layer, is a railway)
    streets = []          # (name or "", width, line) of every road cars drive on
    furniture = []        # (kind, x, z, tags): stop and give-way signs, bus stops, lamps, hydrants, bins
    pitches, bleachers = [], []   # (sport, ring) of every pitch with its sport; rings of stands
    for el in elements:
        tags = el.get("tags") or {}
        kind = el.get("type")
        if kind == "node":
            if tags.get("natural") == "tree":
                trees.append(px((el["lat"], el["lon"])))
            elif tags.get("highway") == "crossing" and marked_crossing(tags):
                crossing_nodes.append(px((el["lat"], el["lon"])))
            elif tags.get("amenity") == "bench":
                benches.append(px((el["lat"], el["lon"])))
            elif tags.get("highway") == "traffic_signals":
                signals.append(px((el["lat"], el["lon"])))
            else:                                       # road signs, bus stops, lamps, hydrants and bins
                kind_ = {"stop": "stop", "give_way": "give_way", "bus_stop": "bus_stop",
                         "street_lamp": "lamp"}.get(tags.get("highway")) or (
                    "hydrant" if tags.get("emergency") == "fire_hydrant" else
                    "bin" if tags.get("amenity") == "waste_basket" else
                    "bike" if tags.get("amenity") == "bicycle_parking" else
                    "post_box" if tags.get("amenity") == "post_box" else
                    "seating" if tags.get("leisure") == "outdoor_seating" or tags.get("amenity") == "picnic_table" else
                    "statue" if tags.get("tourism") == "artwork" and tags.get("artwork_type") in
                    ("statue", "sculpture", "bust", None) else
                    "flagpole" if tags.get("man_made") == "flagpole" else
                    "power" if tags.get("power") in ("tower", "portal") else None)
                if kind_:
                    x_, z_ = px((el["lat"], el["lon"]))
                    furniture.append((kind_, x_, z_, tags))
            continue
        if kind == "way":
            geom = [(p["lat"], p["lon"]) for p in el.get("geometry") or [] if p]
            closed = len(geom) >= 4 and geom[0] == geom[-1]
            outers, inners = ([geom] if closed else []), []
        elif kind == "relation":
            if tags.get("type") != "multipolygon":
                continue
            geom, closed = [], True
            parts = {"outer": [], "inner": []}
            for m in el.get("members") or []:
                pts = [(p["lat"], p["lon"]) for p in m.get("geometry") or [] if p]
                parts["inner" if m.get("role") == "inner" else "outer"].append(pts)
            outers, inners = assemble_rings(parts["outer"]), assemble_rings(parts["inner"])
        else:
            continue
        outers = [[px(p) for p in ring] for ring in outers]
        inners = [[px(p) for p in ring] for ring in inners]
        line = [px(p) for p in geom]
        if not (touches(line) or any(touches(r) for r in outers)):
            continue

        if tags.get("leisure") == "pitch" and tags.get("sport") and outers:
            pitches.append((tags["sport"].split(";")[0], outers[0]))
        if tags.get("leisure") == "bleachers" and outers:
            bleachers.append(outers[0])
        if tags.get("barrier") in BARRIERS and kind == "way" and len(line) >= 2:
            kind_ = BARRIERS[tags["barrier"]]
            material = " ".join(tags.get(k, "") for k in ("material", "wall", "barrier:material", "wall:material"))
            if kind_ == B_WALL and "brick" in material:
                kind_ = B_BRICK                 # a face-brick wall, like the ones round many South African sites
            barriers.append((kind_, barrier_height(tags), line))     # an area may have one too
        if tags.get("building") not in (None, "no") and tags.get("location") != "underground":
            if outers:
                buildings.append((el.get("id", 0), tags, outers, inners, kind == "relation"))
            continue
        highway = tags.get("highway")
        if highway and kind == "way" and len(line) >= 2:
            base = highway[:-5] if highway.endswith("_link") else highway
            if (tags.get("covered") == "yes" and base in ("footway", "path", "pedestrian", "cycleway", "steps")
                    and tags.get("tunnel") in (None, "no") and not tags.get("layer", "0").startswith("-")
                    and tags.get("indoor") in (None, "no")):
                # a covered walkway: a roof on pillars over the path, one rectangle per straight stretch
                pieces = []
                for (x1, y1), (x2, y2) in zip(line, line[1:]):
                    length = math.hypot(x2 - x1, y2 - y1)
                    if length < 0.5:
                        continue
                    ux, uy = (x2 - x1) / length, (y2 - y1) / length
                    nx, ny = -uy * 1.6, ux * 1.6
                    a, b = (x1 - ux * 0.5, y1 - uy * 0.5), (x2 + ux * 0.5, y2 + uy * 0.5)
                    rect = [(a[0] + nx, a[1] + ny), (b[0] + nx, b[1] + ny), (b[0] - nx, b[1] - ny), (a[0] - nx, a[1] - ny)]
                    pieces.append(rect + rect[:1])
                if pieces:
                    roof_tags = {k: v for k, v in tags.items() if k.startswith("roof:")}
                    buildings.append((el.get("id", 0), dict(roof_tags, building="roof", **{"place2mc:walkway": "yes"}),
                                      pieces, [], False))
            if base in ROADS and is_bridge(tags):              # built up in the air later, not on the ground
                width, surface, _ = ROADS[base]
                try:
                    width = max(width, round(float(tags["lanes"]) * 3.5))
                except (KeyError, ValueError):
                    pass
                bridges.append((width, surface, line, bridge_layer(tags), False))
                continue
            if base in ROADS and tags.get("tunnel") != "yes":
                width, surface, sidewalk = ROADS[base]
                if tags.get("area") == "yes" and closed:
                    areas.append((3, surface, outers, inners))
                    continue
                try:
                    width = max(width, round(float(tags["lanes"]) * 3.5))
                except (KeyError, ValueError):
                    pass
                if highway.endswith("_link"):
                    width = max(4, width - 3)
                roads.append((width, surface, sidewalk, line))
                if surface == S_ROAD:                  # for the street-name signs at the crossings
                    streets.append(((tags.get("name") or "").strip(), width, line))
                if surface == S_ROAD and width >= 8 and tags.get("oneway") != "yes":
                    marked.append(line)
                if any(tags.get(k) == "crossing" for k in ("footway", "cycleway", "path")) and marked_crossing(tags):
                    crossing_ways.append(line)
            continue
        railway = tags.get("railway")
        if railway and kind == "way" and len(line) >= 2:
            underground = tags.get("tunnel") in ("yes", "building_passage") or tags.get("location") == "underground"
            if railway in TRACKS and not underground and is_bridge(tags):
                bridges.append((4, S_GRAVEL, line, bridge_layer(tags), True))
            elif railway in TRACKS and not underground:
                rails.append((railway, line))
            elif railway == "platform" and not underground:
                if closed:
                    areas.append((3, S_PAVED, outers, inners))
                else:
                    roads.append((3, S_PAVED, False, line))
            continue
        if tags.get("waterway") in WATERWAYS and kind == "way" and not closed and len(line) >= 2:
            if tags.get("tunnel") not in ("yes", "culvert"):
                width = WATERWAYS[tags["waterway"]]
                try:
                    width = max(1, round(float(tags["width"])))
                except (KeyError, ValueError):
                    pass
                waterways.append((width, line))
            continue
        if tags.get("man_made") in ("pier", "breakwater", "groyne"):
            if outers or len(line) >= 2:
                piers.append((outers, line))
            continue
        if tags.get("natural") == "tree_row" and kind == "way" and len(line) >= 2:
            for (x1, y1), (x2, y2) in zip(line, line[1:]):          # a tree every 7 metres along the row
                steps = max(1, int(math.hypot(x2 - x1, y2 - y1) // 7))
                trees.extend((x1 + (x2 - x1) * i / steps, y1 + (y2 - y1) * i / steps) for i in range(steps))
            continue
        if tags.get("natural") == "coastline" and kind == "way" and len(line) >= 2:
            coast.append(line)
            continue
        if tags.get("amenity") == "parking" and tags.get("parking") in ("underground", "multi-storey"):
            continue
        if tags.get("amenity") in CAMPUS_TYPES and outers:
            campuses.append(outers)
        if tags.get("landuse") == "industrial" and outers:
            industrial.append(outers)
        for key in ("natural", "landuse", "leisure", "waterway", "amenity", "place2mc:land_cover"):
            hit = AREAS.get((key, tags.get(key)))
            if hit and outers:
                areas.append((hit[1], hit[0], outers, inners))
                break

    ground_img = Image.new("L", (size, size), S_GRASS)
    draw = ImageDraw.Draw(ground_img)

    def fill(image, drawer, value, outers, inners):
        if not inners:
            for ring in outers:
                drawer.polygon(ring, fill=value)
            return
        mask = Image.new("1", (size, size), 0)
        mdraw = ImageDraw.Draw(mask)
        for ring in outers:
            mdraw.polygon(ring, fill=1)
        for ring in inners:
            mdraw.polygon(ring, fill=0)
        image.paste(value, mask=mask)

    for _, surface, outers, inners in sorted(areas, key=lambda a: a[0]):
        fill(ground_img, draw, surface, outers, inners)
    sea = flood_sea(coast, size)
    if sea is not None:
        ground_img.paste(S_SEA, mask=sea)
    for width, line in waterways:
        draw.line(line, fill=S_WATER, width=width, joint="curve")
    for outers, line in piers:
        if outers:
            fill(ground_img, draw, S_QUAY, outers, [])
        else:
            draw.line(line, fill=S_QUAY, width=3, joint="curve")
    for railway, line in rails:
        if railway != "tram":                       # trams run on the street; trains get a gravel track bed
            draw.line(line, fill=S_GRAVEL, width=3, joint="curve")
    roads.sort(key=lambda r: r[0])
    for width, surface, sidewalk, line in roads:
        if sidewalk:
            draw.line(line, fill=S_SIDEWALK, width=width + 4, joint="curve")
    for width, surface, sidewalk, line in roads:
        draw.line(line, fill=surface, width=width, joint="curve")

    # school and college grounds: buildings there without a mapped height get the usual height of a school
    campus_img = Image.new("1", (size, size), 0)
    campus_draw = ImageDraw.Draw(campus_img)
    for outers in campuses:
        for ring in outers:
            campus_draw.polygon(ring, fill=1)
    campus_mask = np.array(campus_img, dtype=bool)
    # industrial areas: a building there with no type or height is a warehouse or a workshop
    works_img = Image.new("1", (size, size), 0)
    works_draw = ImageDraw.Draw(works_img)
    for outers in industrial:
        for ring in outers:
            works_draw.polygon(ring, fill=1)
    works_mask = np.array(works_img, dtype=bool)

    def middle_in(mask, outers):
        xs = [x for ring in outers for x, _ in ring]
        ys = [y for ring in outers for _, y in ring]
        cx, cy = int(sum(xs) / len(xs)), int(sum(ys) / len(ys))
        return 0 <= cx < size and 0 <= cy < size and mask[cy, cx]

    def on_campus(outers):
        return middle_in(campus_mask, outers)

    # buildings: an image whose pixel value is the building number (0 = no building)
    bimg = Image.new("I", (size, size), 0)
    bdraw = ImageDraw.Draw(bimg)
    # per building: height, wall, roof, pitched roof, windows, floor height, glass, base/trim block, window spacing
    # ... door kind (index into DOORS), shop windows on the ground floor
    specs = [(0, AIR, AIR, 0, 0, 3, GLASS, AIR, 4, 0, 0, 0)]
    no_lidar = set()
    build_map.no_lidar = no_lidar
    building_names = {}                                 # building number -> its name, for signs and labels
    build_map.building_names = building_names
    building_uses = ["office"]                          # building number -> what it is used for (building_use)
    build_map.building_uses = building_uses
    # OpenStreetMap's buildings first (relations first, so buildings inside a courtyard are kept), then Overture's
    # (covered walkways before buildings, so a building drawn over one keeps its own walls)
    buildings.sort(key=lambda b: (b[1].get("source:place2mc") == "overture", "place2mc:walkway" not in b[1], not b[4]))
    from_overture = 0
    for osm_id, tags, outers, inners, _ in buildings:
        if tags.get("source:place2mc") == "overture" and already_built(bimg, outers, size):
            continue
        from_overture += tags.get("source:place2mc") == "overture"
        btype = tags.get("building", "yes")
        area = sum(polygon_area(r) for r in outers)
        if btype == "yes" and area < 40:                 # a shed, a kiosk or a shack: one storey
            btype = "shed"
        elif btype == "yes" and area >= 150 and middle_in(works_mask, outers) and not on_campus(outers):
            btype = "industrial"
        pick = (osm_id * 2654435761) >> 8
        house = btype in HOUSE_TYPES or (btype == "yes" and area < 250)
        height, levels = parse_height(tags), parse_levels(tags)
        homes = house or btype in ("apartments", "residential", "dormitory")
        if height is None and levels:   # floors of homes are about 3 m from floor to floor, others about 4 m
            height = levels * (3 if homes else 4) + 1
        if height is None:              # the map does not say, so guess from the kind and size of building
            if btype in SMALL_TYPES:
                height = 3
            elif house:                 # one storey for small houses, one or two for bigger ones
                height = 4 if area < 120 or pick % 3 else 7
            else:
                # most buildings in the world have one to three floors: small ones low, big ones a little higher
                low, high = FLOORS.get(btype, (1, 3) if on_campus(outers) else (1, 2) if area < 600 else
                                       (2, 3) if area < 2500 else (2, 5))
                height = (low + (pick >> 3) % (high - low + 1)) * 4 + 1
        height = min(height, 300)
        roof_shape = tags.get("roof:shape")
        pitched = (roof_shape not in (None, "flat")) or (roof_shape is None and house and height <= 10)
        storey, glass, spacing = 4, GLASSES[(pick >> 6) % len(GLASSES)], 3 + (pick >> 9) % 2
        if btype in SMALL_TYPES:
            wall, windows = (STONE_BRICKS if pick % 2 else HOUSE_WALLS[2]), False
            base, storey = wall, 3
        elif btype in PLAIN_TYPES:
            wall, windows, base = PLAIN_WALLS[pick % len(PLAIN_WALLS)], False, STONE_BRICKS
        elif btype in ("church", "cathedral", "chapel"):
            wall, windows, base, spacing = STONE_BRICKS, True, STONE_BRICKS, 4
        elif pitched:
            wall, windows = HOUSE_WALLS[pick % len(HOUSE_WALLS)], True
            base, storey, glass, spacing = (STONE_BRICKS if pick % 3 == 0 else wall), 3, GLASS, 4
        else:
            wall, windows = BIG_WALLS[pick % len(BIG_WALLS)], True
            base = BASES[(pick >> 12) % len(BASES)]
        if levels and btype not in SMALL_TYPES:   # the map says how many floors: make each floor that tall
            storey = max(3, min(6, round((height - 1) / levels)))
        tag_wall = wall_from_tags(tags)
        if tag_wall is not None:
            wall = tag_wall
            if base not in (STONE_BRICKS, POLISHED_ANDESITE, SMOOTH_STONE):
                base = wall
        roofs = PITCHED_ROOFS if pitched else FLAT_ROOFS
        tag_roof = roof_from_tags(tags, pitched)
        if tag_roof is not None:
            roofs = [tag_roof]
        door = (pick >> 14) % len(DOORS) if pitched else 0
        shop = (not pitched and windows and height >= 8 and btype not in PLAIN_TYPES
                and (btype in SHOP_TYPES or "shop" in tags or "amenity" in tags))
        specs.append((height, wall, roofs[(pick >> 4) % len(roofs)], pitched, windows, storey, glass, base, spacing,
                      door, shop, btype in ("roof", "carport")))
        fill(bimg, bdraw, len(specs) - 1, outers, inners)
        if tags.get("place2mc:lidar") == "no":          # built after the laser scan: use its tags, not the scan
            no_lidar.add(len(specs) - 1)
        if tags.get("name"):
            building_names[len(specs) - 1] = tags["name"]
        building_uses.append(building_use(tags))

    counts = {"buildings": len(specs) - 1, "roads": len(roads), "areas": len(areas), "coastlines": len(coast),
              "waterways": len(waterways), "railways": len(rails), "mapped trees": len(trees),
              "zebra crossings": len(crossing_ways) + len(crossing_nodes), "fences, walls and hedges": len(barriers),
              "benches": len(benches), "traffic lights": len(signals), "buildings from Overture": from_overture}
    ground = np.array(ground_img, dtype=np.uint8)
    bmap = np.array(bimg, dtype=np.int32)
    for line in marked:                     # dashed white centre line on the bigger two-way roads
        travelled = 0.0
        for (x1, y1), (x2, y2) in zip(line, line[1:]):
            length = math.hypot(x2 - x1, y2 - y1)
            for i in range(int(length) + 1):
                if (travelled + i) % 8 < 4:
                    x = int(x1 + (x2 - x1) * i / max(length, 1e-9))
                    y = int(y1 + (y2 - y1) * i / max(length, 1e-9))
                    if 0 <= x < size and 0 <= y < size and ground[y, x] == S_ROAD:
                        ground[y, x] = S_LINE
            travelled += length
    # zebra crossings: white stripes along the traffic, one block wide, across the road where people cross
    road_lines = [(width, line) for width, surface, _, line in roads if surface == S_ROAD]
    for line in crossing_ways:
        for a, b in zip(line, line[1:]):
            paint_zebra(ground, a, b)
    for node in crossing_nodes:
        if any(math.hypot(node[0] - x, node[1] - y) < 4 for line in crossing_ways for x, y in line):
            continue                                # already painted from the footway that crosses here
        across = road_across(node, road_lines)
        if across:
            paint_zebra(ground, *across)

    # pitches: the lines of their sport, and where the goals, nets and hoops stand
    sports = []
    for sport, ring in pitches:
        frame = pitch_frame(ring)
        if frame is None:
            continue
        cells, u, v, half_l, half_w, axis_u, axis_v = frame(size)
        lines = sport_lines(sport, u, v, half_l, half_w)
        ok = np.isin(ground[cells[:, 1], cells[:, 0]], (S_PITCH, S_GRASS, S_PARK, S_FOOT, S_PAVED, S_SAND))
        ground[cells[lines & ok, 1], cells[lines & ok, 0]] = S_LINE
        sports.append((sport, cells, u, v, half_l, half_w, axis_u, axis_v))
    stands = []
    for ring in bleachers:
        frame = pitch_frame(ring)
        if frame is not None:
            stands.append(frame(size))

    # fences, walls, hedges and rails: drawn so every step goes to a side neighbour, so they join up in the game
    barrier_kind = np.zeros((size, size), dtype=np.uint8)
    barrier_tall = np.zeros((size, size), dtype=np.uint8)
    for kind_, tall, line in barriers:
        for x, y in line_cells(line, size):
            barrier_kind[y, x], barrier_tall[y, x] = kind_, tall
    track = np.zeros((size, size), dtype=bool)
    for _, line in rails:
        for x, y in line_cells(line, size):
            track[y, x] = True
    build_map.details = {"barriers": (barrier_kind, barrier_tall), "rails": track, "bridges": bridges,
                         "streets": streets, "furniture": furniture, "left_traffic": drives_on_left(lat0, lon0),
                         "give_way_text": "YIELD" if 15 <= lat0 <= 72 and -170 <= lon0 <= -50 else "GIVE WAY",
                         "signals": [(int(x), int(y)) for x, y in signals if 0 <= x < size and 0 <= y < size],
                         "benches": [(int(x), int(y)) for x, y in benches if 0 <= x < size and 0 <= y < size],
                         "sports": sports, "stands": stands}
    sea_arr = np.array(sea) > 0 if sea is not None else np.zeros((size, size), dtype=bool)
    return refine_ground(ground, bmap, sea_arr), bmap, specs, trees, counts


def pitch_frame(ring):
    """The rectangle a pitch (or a stand) is drawn in: its centre, long axis u and short axis v (from the outline's
    corners), half its length and width. Returns a function of the map size giving the cells inside the outline and
    their (u, v), or None for a tiny outline."""
    pts = np.array(ring[:-1] if len(ring) > 3 and ring[0] == ring[-1] else ring, dtype=float)
    if len(pts) < 3:
        return None
    c = pts.mean(axis=0)
    best = None
    for i in range(len(pts)):                            # the outline's edges: the tightest box lies along one
        e = pts[(i + 1) % len(pts)] - pts[i]
        n = math.hypot(*e)
        if n < 1e-6:
            continue
        ax = e / n
        ay = np.array([-ax[1], ax[0]])
        a, b = (pts - c) @ ax, (pts - c) @ ay
        area = (a.max() - a.min()) * (b.max() - b.min())
        if best is None or area < best[0]:
            best = (area, ax, ay, a, b)
    _, ax, ay, a, b = best
    c = c + ax * (a.max() + a.min()) / 2 + ay * (b.max() + b.min()) / 2
    half = np.array([(a.max() - a.min()) / 2, (b.max() - b.min()) / 2])
    if half[1] > half[0]:
        ax, ay, half = ay, -ax, half[::-1]
    if half[1] < 2:
        return None

    def frame(size):
        lo = np.floor(pts.min(axis=0)).astype(int)
        hi = np.ceil(pts.max(axis=0)).astype(int)
        xs, zs = np.meshgrid(np.arange(max(lo[0], 0), min(hi[0] + 1, size)),
                             np.arange(max(lo[1], 0), min(hi[1] + 1, size)))
        xs, zs = xs.ravel(), zs.ravel()
        inside = points_in_polygon(xs + 0.5, zs + 0.5, pts)
        cells = np.stack([xs[inside], zs[inside]], axis=1)
        d = cells + 0.5 - c
        return cells, d @ ax, d @ ay, float(half[0]), float(half[1]), ax, ay
    return frame


def points_in_polygon(x, y, poly):
    """Even-odd test of many points against one polygon."""
    inside = np.zeros(len(x), bool)
    n = len(poly)
    for i in range(n):
        (x1, y1), (x2, y2) = poly[i], poly[(i + 1) % n]
        cross = (y1 > y) != (y2 > y)
        with np.errstate(divide="ignore", invalid="ignore"):
            at = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
        inside ^= cross & (x < at)
    return inside


def sport_lines(sport, u, v, half_l, half_w):
    """The painted lines of a pitch, in metres from its centre (u along it, v across): the outline 1 m in, the
    halfway line, and each sport's own marks at their real sizes (soccer: 9.15 m circle, 16.5 m boxes; tennis: 6.4 m
    service lines; basketball: 1.8 m circle, 5.8 m keys; pickleball: 2.13 m kitchens;
    American football: a yard line every 10 yards)."""
    au, av = np.abs(u), np.abs(v)
    edge_l, edge_w = half_l - 1, half_w - 1
    line = ((np.abs(au - edge_l) < 0.5) & (av <= edge_w + 0.5)) | ((np.abs(av - edge_w) < 0.5) & (au <= edge_l + 0.5))
    if sport in ("baseball", "softball", "boules", "golf", "equestrian", "athletics", "running"):
        return np.zeros(len(u), bool)
    line |= (au < 0.5) & (av <= edge_w)
    if sport == "soccer" and half_l > 30:
        line |= np.abs(np.hypot(u, v) - 9.15) < 0.5
        box = (au >= edge_l - 16.5) & (av <= 20.15)
        line |= box & ((np.abs(au - (edge_l - 16.5)) < 0.5) | (np.abs(av - 20.15) < 0.5))
    elif sport == "american_football":
        line |= (np.abs(((au + 4.57) % 9.144) - 4.57) < 0.5) & (au <= edge_l - 9) & (av <= edge_w)
    elif sport == "tennis":                                # at 1 block a metre only the service lines fit cleanly
        line |= (np.abs(au - 6.4) < 0.5) & (av <= edge_w - 1.37)
    elif sport == "basketball":
        line |= np.abs(np.hypot(u, v) - 1.8) < 0.5
        key = (au >= edge_l - 5.8) & (av <= 2.45)
        line |= key & ((np.abs(au - (edge_l - 5.8)) < 0.5) | (np.abs(av - 2.45) < 0.5))
    elif sport == "pickleball":
        line |= (np.abs(au - 2.13) < 0.5) & (av <= edge_w)
    return line


def already_built(bimg, outers, size, share=0.3):
    """Does an earlier (OpenStreetMap) building already cover this footprint? Then Overture's copy is left out."""
    xs = [x for ring in outers for x, _ in ring]
    ys = [y for ring in outers for _, y in ring]
    x0, y0 = max(0, int(min(xs))), max(0, int(min(ys)))
    x1, y1 = min(size, int(max(xs)) + 1), min(size, int(max(ys)) + 1)
    if x1 <= x0 or y1 <= y0:
        return True                                     # outside the square
    mask = Image.new("1", (x1 - x0, y1 - y0), 0)
    md = ImageDraw.Draw(mask)
    for ring in outers:
        md.polygon([(x - x0, y - y0) for x, y in ring], fill=1)
    m = np.array(mask, dtype=bool)
    if not m.any():
        return True
    return (np.array(bimg.crop((x0, y0, x1, y1)))[m] > 0).mean() > share


def marked_crossing(tags):
    """Does this crossing have markings on the road (a zebra or lines)?"""
    if tags.get("crossing:markings") in ("no", "unmarked") or tags.get("crossing") in ("unmarked", "no", "informal"):
        return False
    return (tags.get("crossing") in ("zebra", "marked", "uncontrolled", "traffic_signals")
            or tags.get("crossing_ref") == "zebra" or tags.get("crossing:markings") not in (None, "no"))


def barrier_height(tags):
    """Height in blocks of a fence, wall or hedge: the map's height if it has one, else a usual height."""
    try:
        metres = float(str(tags.get("height", "")).replace("m", "").strip())
    except ValueError:
        metres = {"city_wall": 4.0, "hedge": 1.4}.get(tags.get("barrier"), 1.0)
    return int(min(max(round(metres), 1), 6))


def is_bridge(tags):
    """A way on a bridge (bridge=yes, viaduct, ...) that is above the ground, not a bridge at layer 0 or below."""
    return tags.get("bridge") not in (None, "no") and bridge_layer(tags) >= 1


def bridge_layer(tags):
    try:
        return int(float(tags.get("layer", "1")))
    except ValueError:
        return 1


def line_cells(points, size):
    """Every cell a polyline passes through, in order, each one a side neighbour of the one before (never a
    diagonal step), so fences and rails drawn along it join up. Cells outside the map are left out."""
    cells, last = [], None
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        x, y = math.floor(x0), math.floor(y0)
        steps = abs(math.floor(x1) - x) + abs(math.floor(y1) - y)
        if steps > 4 * size:
            continue
        dx, dy = x1 - x0, y1 - y0
        sx, sy = (1 if dx > 0 else -1), (1 if dy > 0 else -1)
        tdx = abs(1 / dx) if dx else math.inf
        tdy = abs(1 / dy) if dy else math.inf
        tmx = ((x + (sx > 0)) - x0) / dx if dx else math.inf
        tmy = ((y + (sy > 0)) - y0) / dy if dy else math.inf
        for i in range(steps + 1):
            if (x, y) != last and 0 <= x < size and 0 <= y < size:
                cells.append((x, y))
            last = (x, y)
            if tmx < tmy:
                x, tmx = x + sx, tmx + tdx
            else:
                y, tmy = y + sy, tmy + tdy
    return cells


def paint_zebra(ground, a, b, half_width=1.6):
    """White stripes on the road cells near the segment a-b (the way people walk across), alternating one
    block white, one block road along it."""
    size = ground.shape[0]
    (ax, ay), (bx, by) = a, b
    length = math.hypot(bx - ax, by - ay)
    if length < 1:
        return
    ux, uy = (bx - ax) / length, (by - ay) / length
    x0, x1 = max(0, int(min(ax, bx)) - 3), min(size, int(max(ax, bx)) + 4)
    y0, y1 = max(0, int(min(ay, by)) - 3), min(size, int(max(ay, by)) + 4)
    if x0 >= x1 or y0 >= y1:
        return
    ys, xs = np.mgrid[y0:y1, x0:x1] + 0.5
    along = (xs - ax) * ux + (ys - ay) * uy
    across = (ys - ay) * ux - (xs - ax) * uy
    region = ground[y0:y1, x0:x1]
    band = (along >= -0.5) & (along <= length + 0.5) & (np.abs(across) <= half_width) & np.isin(region, (S_ROAD, S_LINE))
    region[band] = np.where(np.floor(along) % 2 == 0, S_LINE, S_ROAD)[band]


def road_across(point, road_lines):
    """For a crossing mapped as a single point on a road: the segment straight across that road there."""
    px_, py_ = point
    best = None
    for width, line in road_lines:
        for (x1, y1), (x2, y2) in zip(line, line[1:]):
            dx, dy = x2 - x1, y2 - y1
            seg = dx * dx + dy * dy
            if seg < 1e-9:
                continue
            t = min(max(((px_ - x1) * dx + (py_ - y1) * dy) / seg, 0), 1)
            dist = math.hypot(px_ - (x1 + t * dx), py_ - (y1 + t * dy))
            if dist < width / 2 + 1 and (best is None or dist < best[0]):
                best = (dist, width, dx / math.sqrt(seg), dy / math.sqrt(seg))
    if best is None:
        return None
    _, width, ux, uy = best
    reach = width / 2 + 1
    return (px_ + uy * reach, py_ - ux * reach), (px_ - uy * reach, py_ + ux * reach)


def window_fraction(mask, r):
    """Share of True cells in the square of side 2r+1 around each cell."""
    k = 2 * r + 1
    c = np.pad(mask, r, mode="edge").astype(np.int32).cumsum(0).cumsum(1)
    c = np.pad(c, ((1, 0), (1, 0)))
    return (c[k:, k:] - c[:-k, k:] - c[k:, :-k] + c[:-k, :-k]) / float(k * k)


def refine_ground(ground, bmap, sea):
    """Ground with nothing mapped on it starts as grass. Turn it into stone along the sea and paving in built-up areas."""
    ground = ground.copy()
    if sea.any():
        ground[(ground == S_GRASS) & ~sea & (window_fraction(sea, 4) > 0)] = S_QUAY
    built = (bmap > 0) | np.isin(ground, (S_ROAD, S_LINE, S_SIDEWALK, S_PAVED, S_FOOT, S_QUAY, S_PARKING))
    ground[(ground == S_GRASS) & (window_fraction(built, 16) > 0.55)] = S_PLAZA
    return ground


def flood_sea(coastlines, size):
    """The map draws the coast as lines with land on the left and sea on the right.
    Fill the sea side. Returns a mask image, or None when there is no coast or the result looks wrong."""
    if not coastlines:
        return None
    mask = Image.new("L", (size, size), 0)
    drawer = ImageDraw.Draw(mask)
    for line in coastlines:
        drawer.line(line, fill=1, width=2)
    water_seeds, land_seeds = [], []
    for line in coastlines:
        for (x1, y1), (x2, y2) in zip(line, line[1:]):
            length = math.hypot(x2 - x1, y2 - y1)
            if length < 3:
                continue
            nx, ny = -(y2 - y1) / length, (x2 - x1) / length      # points to the right of the direction of travel
            mx, my = (x1 + x2) / 2, (y1 + y2) / 2
            water_seeds.append((int(mx + 3 * nx), int(my + 3 * ny)))
            land_seeds.append((int(mx - 3 * nx), int(my - 3 * ny)))
    pixels = mask.load()
    on_map = lambda p: 0 <= p[0] < size and 0 <= p[1] < size
    for seed in water_seeds:
        if on_map(seed) and pixels[seed] == 0:
            ImageDraw.floodfill(mask, seed, 2)
    land = [p for p in land_seeds if on_map(p) and pixels[p] != 1]
    flooded = sum(1 for p in land if pixels[p] == 2)
    if land and flooded > 0.05 * len(land):
        print("Warning: the coastline data is incomplete here, so the sea was left out.")
        return None
    return mask.point(lambda v: 255 if v == 2 else 0)


def interior_of(ids):
    """True where a pixel and its four neighbours all belong to the same building."""
    p = np.pad(ids, 1)
    c = p[1:-1, 1:-1]
    return (c > 0) & (c == p[:-2, 1:-1]) & (c == p[2:, 1:-1]) & (c == p[1:-1, :-2]) & (c == p[1:-1, 2:])


def leaf_layer(vol, y, z, x, radius, leaves, trim_corners=True):
    for dz in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            if trim_corners and radius and abs(dx) == radius and abs(dz) == radius:
                continue
            if vol[y, z + dz, x + dx] == AIR:
                vol[y, z + dz, x + dx] = leaves


def plant_tree(vol, z, x, rng, small=False, base=FLOOR):
    """Six kinds of tree, in the mix of the place's climate. A normal tree fits within 3 blocks of the trunk, a small
    one within 2."""
    kinds = CLIMATE["trees"]
    names = list(kinds)
    weights = np.array([kinds[n] for n in names], dtype=float)
    kind = names[int(np.searchsorted(np.cumsum(weights) / weights.sum(), rng.random(), side="right"))]
    if small and kind in ("round", "jungle"):
        kind = "oak"
    roll = {"oak": 0, "round": 8, "birch": 12, "spruce": 16, "acacia": 20, "jungle": 24}[kind]
    if roll == 20:                                 # acacia: a bare trunk and a flat crown, as on the savanna
        log, leaves = TREES[3]
        trunk = int(rng.integers(4, 6))
        shape = [(trunk, 2 if small else 3), (trunk + 1, 1 if small else 2)]
    elif roll == 24:                               # jungle tree: tall, with a broad crown
        log, leaves = TREES[4]
        trunk = int(rng.integers(8, 12))
        shape = [(trunk - 2, 3), (trunk - 1, 3), (trunk, 2), (trunk + 1, 1)]
    elif roll < 8:                                 # ordinary oak
        log, leaves = TREES[0]
        trunk = int(rng.integers(4, 7))
        shape = [(trunk - 2, 2), (trunk - 1, 2), (trunk, 1), (trunk + 1, 0)]
    elif roll < 12:                                # big round oak
        log, leaves = TREES[0]
        trunk = int(rng.integers(6, 9))
        shape = [(trunk - 3, 2), (trunk - 2, 3), (trunk - 1, 3), (trunk, 3), (trunk + 1, 2), (trunk + 2, 1)]
    elif roll < 16:                                # slim birch
        log, leaves = TREES[1]
        trunk = int(rng.integers(5, 8))
        shape = [(trunk - 2, 2), (trunk - 1, 2), (trunk, 1), (trunk + 1, 1)]
    else:                                          # pointed spruce
        log, leaves = TREES[2]
        trunk = int(rng.integers(6, 10))
        shape = [(dy, 2 if (trunk - dy) % 2 else 1) for dy in range(2, trunk)] + [(trunk, 1), (trunk + 1, 0)]
    for dy, radius in shape:
        leaf_layer(vol, base + dy, z, x, radius, leaves)
    vol[base:base + trunk, z, x] = log


def neighbours(ids):
    """The value of the north, south, west and east neighbour of every cell (0 outside the map)."""
    p = np.pad(ids, 1)
    return p[:-2, 1:-1], p[2:, 1:-1], p[1:-1, :-2], p[1:-1, 2:]


def inward_facing(ids):
    """For cells on the edge of a shape: the direction pointing into the shape (NORTH..EAST), or -1.
    Stairs and doors use it: a roof edge on the south side gets stairs facing north, so it slopes down to the south."""
    n, s_, w, e = (nb != ids for nb in neighbours(ids))
    facing = np.full(ids.shape, -1, dtype=np.int8)
    facing[e & ~w] = WEST
    facing[w & ~e] = EAST
    facing[s_ & ~n] = NORTH
    facing[n & ~s_] = SOUTH
    open_sides = n.astype(np.int8) + s_ + w + e
    return facing, open_sides


def local_min(values, r):
    """Smallest value in the square of side 2r+1 around each cell."""
    out = values.copy()
    for axis in (0, 1):
        src = out.copy()
        for d in range(1, r + 1):
            out = np.minimum(out, np.roll(src, d, axis))
            out = np.minimum(out, np.roll(src, -d, axis))
    return out


def building_bases(bmap, g, n_buildings):
    """Ground layer each building stands on: a low point of its footprint (a quarter of it is lower), so on a
    slope the building gets a stone foundation on the downhill side and is dug in on the uphill side."""
    base = np.zeros(n_buildings, dtype=np.int32)
    mask = bmap > 0
    if mask.any():
        ids, vals = bmap[mask], g[mask]
        order = np.lexsort((vals, ids))
        ids, vals = ids[order], vals[order]
        starts = np.flatnonzero(np.r_[True, ids[1:] != ids[:-1]])
        ends = np.r_[starts[1:], len(ids)]
        base[ids[starts]] = vals[starts + (ends - starts - 1) // 4]
    return base


def distance_from(mask, limit=64):
    """Blocks from each cell to the nearest cell of mask (counting diagonal steps as one), up to limit."""
    dist = np.where(mask, 0, limit).astype(np.int32)
    reached = mask.copy()
    for k in range(1, limit):
        grown = reached | (window_fraction(reached, 1) > 0)
        new = grown & ~reached
        if not new.any():
            break
        dist[new] = k
        reached = grown
    return dist


def lay_rails(vol, track, base):
    """Rails on the cells of track, on top of the blocks at layer base: each one turns or climbs to join the rails
    beside it, as the game would lay them."""
    size = track.shape[0]
    track_g = np.where(track, base, -100)
    joins = [nb > 0 for nb in neighbours(track.astype(np.int32))]         # north, south, west, east
    up = [nb == base + 1 for nb in neighbours(track_g)]
    n, s_, w, e = joins
    shape = np.full((size, size), RAILS["north_south"], dtype=np.uint8)
    shape[(w | e) & ~(n | s_)] = RAILS["east_west"]
    for (a, b), name in (((s_, e), "south_east"), ((s_, w), "south_west"), ((n, w), "north_west"),
                         ((n, e), "north_east")):
        shape[a & b & ~(n & s_) & ~(w & e)] = RAILS[name]
    for d, name in enumerate(("ascending_north", "ascending_south", "ascending_west", "ascending_east")):
        shape[track & up[d]] = RAILS[name]
    z_i, x_i = np.nonzero(track)
    y = base[z_i, x_i] + 1
    free = vol[y, z_i, x_i] == AIR
    vol[y[free], z_i[free], x_i[free]] = shape[z_i[free], x_i[free]]
    return int(free.sum())


def sign_lines(name, width=15):
    """A street name on the four lines of a sign: whole words, at most `width` letters a line, in the middle."""
    lines, line = [], ""
    for word in name.split():
        while len(word) > width:                       # a very long word: cut it
            lines.append(word[:width])
            word = word[width:]
        if line and len(line) + 1 + len(word) > width:
            lines.append(line)
            line = word
        else:
            line = (line + " " + word).strip()
    if line:
        lines.append(line)
    lines = lines[:4]
    top = (4 - len(lines)) // 2
    return [""] * top + lines + [""] * (4 - top - len(lines))


def plan_street_signs(streets, size):
    """Where the street-name signs go. At every crossing (a point where three or more road ends meet), each named
    road there gets a sign: beside that road, just past the crossing, turned to face the crossing so you read it
    when you look down the street. The same name is not repeated within 24 blocks. Returns
    [(x, z, rotation 0-15, name)] in block coordinates."""
    at = {}
    for i, (name, width, line) in enumerate(streets):
        for k, (x, z) in enumerate(line):
            at.setdefault((round(x, 1), round(z, 1)), []).append((i, k))
    plans, used = [], []
    for (jx, jz), members in sorted(at.items()):
        if not (8 <= jx < size - 8 and 8 <= jz < size - 8):
            continue
        degree = sum((k > 0) + (k < len(streets[i][2]) - 1) for i, k in members)
        if degree < 3:
            continue
        widest = max(streets[i][1] for i, _ in members)
        done = set()
        for i, k in members:
            name, width, line = streets[i]
            if not name or name in done:
                continue
            nxt = line[k + 1] if k + 1 < len(line) else line[k - 1]
            ax, az = nxt[0] - jx, nxt[1] - jz
            length = math.hypot(ax, az)
            if length < 1:
                continue
            ax, az = ax / length, az / length
            along = min(widest / 2 + 2.5, length * 0.8)            # just past the crossing, on this road
            side = width / 2 + 1.5                                   # on its verge or pavement
            x, z = jx + ax * along - az * side, jz + az * along + ax * side
            if any(n == name and math.hypot(x - ux, z - uz) < 24 or math.hypot(x - ux, z - uz) < 3
                   for ux, uz, n in used):
                continue
            # facing the crossing: rotation r faces (-sin, cos) of r * 22.5 degrees
            rotation = int(round(math.degrees(math.atan2(ax, -az)) / 22.5)) % 16
            plans.append((x, z, rotation, name))
            used.append((x, z, name))
            done.add(name)
    return plans


# where traffic keeps left (about 75 countries). Rough boxes (south, west, north, east): good away from the borders
# of right-hand neighbours, so a road sign near such a border can stand on the other side of the road.
LEFT_TRAFFIC = [
    (51.0, -11.0, 61.0, 2.0), (49.95, -11.0, 51.0, 1.5),     # Britain and Ireland (not the French coast)
    (35.8, 14.1, 36.1, 14.6), (34.5, 32.2, 35.8, 34.7),     # Malta, Cyprus
    (-35.0, 11.0, -17.5, 41.0),        # South Africa, Lesotho, Eswatini, Namibia, Botswana, Zimbabwe, Mozambique
    (-17.5, 22.5, -13.5, 41.0), (-13.5, 30.5, -8.0, 41.0),  # Zambia, Malawi, northern Mozambique, south Tanzania
    (-8.0, 31.0, 4.5, 42.0), (-1.0, 30.0, 4.5, 31.0),       # Tanzania, Kenya, Uganda
    (5.0, 66.0, 30.0, 92.5), (30.0, 70.5, 37.0, 80.0),      # India, Pakistan, Bangladesh, Nepal, Bhutan, Sri Lanka
    (12.5, 99.0, 18.0, 102.0), (14.4, 102.0, 18.0, 105.6),  # Thailand: the middle, the north-east,
    (18.0, 98.5, 20.4, 100.5), (5.6, 98.2, 12.5, 102.0),    # the north, the south
    (-11.0, 95.0, 7.5, 119.5), (-11.0, 119.5, 4.5, 141.0),  # Malaysia, Singapore, Brunei, Indonesia, Timor
    (22.1, 113.5, 22.6, 114.5),        # Hong Kong, Macau
    (30.5, 129.5, 41.6, 142.1), (41.3, 139.3, 45.6, 146.0), (24.0, 122.5, 30.5, 132.0),   # Japan
    (-48.0, 112.0, -1.0, 162.0), (-48.0, 165.0, -33.0, 179.9),  # Australia, Papua New Guinea, New Zealand
    (3.0, -60.5, 9.0, -54.0),          # Guyana, Suriname
    (17.5, -78.5, 18.6, -76.0), (22.6, -80.0, 27.5, -72.5),  # Jamaica, the Bahamas
    (10.0, -62.0, 13.5, -59.0),        # Trinidad and Tobago, Barbados, the Windwards
]


def drives_on_left(lat, lon):
    return any(s <= lat <= n and w <= lon <= e for s, w, n, e in LEFT_TRAFFIC)


def nearest_road(streets, x, z, reach=20):
    """The road nearest to (x, z) within `reach` blocks: (distance, index of the road, index of its segment,
    unit direction of that segment as drawn, closest point), or None."""
    best = None
    for i, (_, width, line) in enumerate(streets):
        for k in range(len(line) - 1):
            (x0, z0), (x1, z1) = line[k], line[k + 1]
            dx, dz = x1 - x0, z1 - z0
            seg = dx * dx + dz * dz
            if seg < 1e-6:
                continue
            t = min(1, max(0, ((x - x0) * dx + (z - z0) * dz) / seg))
            cx, cz = x0 + t * dx, z0 + t * dz
            d = math.hypot(x - cx, z - cz)
            if d <= reach and (best is None or d < best[0]):
                length = math.sqrt(seg)
                best = (d, i, k, (dx / length, dz / length), (cx, cz))
    return best


def facing_rotation(fx, fz, steps=16):
    """The standing-sign rotation (0-15) that faces the direction (fx, fz); steps=8 keeps to even rotations."""
    r = math.degrees(math.atan2(-fx, fz)) / 22.5
    if steps == 8:
        return int(round(r / 2)) * 2 % 16
    return int(round(r)) % 16


def free_spot(keep_off, x, z, size, reach=3):
    """The cell nearest to (x, z) that is not in keep_off, within `reach` blocks, as (x, z) ints, or None."""
    ix, iz = int(x), int(z)
    for r in range(reach + 1):
        cells = [(ix + dx, iz + dz) for dx in range(-r, r + 1) for dz in range(-r, r + 1) if max(abs(dx), abs(dz)) == r]
        cells.sort(key=lambda c: (c[0] + 0.5 - x) ** 2 + (c[1] + 0.5 - z) ** 2)
        for cx, cz in cells:
            if 2 <= cx < size - 2 and 2 <= cz < size - 2 and not keep_off[cz, cx]:
                return cx, cz
    return None


def cardinal(dx, dz):
    """The compass direction (NORTH..EAST) nearest to a direction on the map (x east, z south)."""
    if abs(dx) > abs(dz):
        return EAST if dx > 0 else WEST
    return SOUTH if dz > 0 else NORTH


def place_extras(vol, g, details, keep_off, has_building, size, low, signs):
    """Real things from the map that are not roads or buildings. Returns {what: how many}."""
    count = {"statues": 0, "bicycle racks": 0, "outdoor tables": 0, "post boxes": 0, "flagpoles": 0,
             "power towers": 0, "goals, nets and hoops": 0, "stands": 0}
    layers = vol.shape[0]
    seat = ROOF_STAIRS[block("spruce_planks")]

    def column(x, z, height):
        """The first layer above the ground at (x, z) when `height` blocks there are free, else None."""
        if not (1 <= x < size - 1 and 1 <= z < size - 1) or has_building[z, x]:
            return None
        y = int(g[z, x]) + 1
        if y + height + 1 >= layers or any(vol[y + k, z, x] not in low for k in range(height)):
            return None
        return y

    for kind_, fx, fz, tags in details.get("furniture", []):
        if kind_ not in ("bike", "post_box", "seating", "statue", "flagpole", "power"):
            continue
        spot = free_spot(keep_off, fx, fz, size)
        if spot is None:
            continue
        x, z = spot
        if kind_ == "bike":                         # three hoops in a row, joined like the game's fences
            ys = [column(x + k, z, 2) for k in (-1, 0, 1)]
            if None in ys:
                continue
            for k, bits in zip((-1, 0, 1), (8, 12, 4)):
                vol[ys[k + 1], z, x + k] = FENCES[bits]
            keep_off[z, x - 1:x + 2] = True
            count["bicycle racks"] += 1
        elif kind_ == "post_box":                   # a blue USPS-style box
            y = column(x, z, 2)
            if y is not None:
                vol[y, z, x] = block("blue_concrete")
                keep_off[z, x] = True
                count["post boxes"] += 1
        elif kind_ == "seating":                    # a table with a chair on each side
            ys = [column(x + k, z, 2) for k in (-1, 0, 1)]
            if None in ys or len(set(ys)) > 1:
                continue
            vol[ys[1], z, x] = DESK
            vol[ys[0], z, x - 1] = seat[WEST]
            vol[ys[2], z, x + 1] = seat[EAST]
            keep_off[z, x - 1:x + 2] = True
            count["outdoor tables"] += 1
        elif kind_ == "statue":                     # a stone plinth, the figure on it, its name on a sign in front
            y = column(x, z, 5)
            yf = column(x, z + 1, 2)
            if y is None:
                continue
            marble = "marble" in tags.get("material", "") or tags.get("artwork_type") == "sculpture"
            figure = QUARTZ if marble else BLACK_CONCRETE
            vol[y:y + 2, z, x] = STONE_BRICKS
            vol[y + 2:y + 4, z, x] = figure
            if yf is not None and tags.get("name"):
                vol[yf, z + 1, x] = SIGNS[0]
                lines = sign_lines(tags["name"])
                if tags.get("artist_name") and len([t for t in lines if t]) < 4:
                    lines = ([t for t in lines if t] + ["by " + tags["artist_name"][:15]] + ["", "", ""])[:4]
                signs.append((yf, z + 1, x, 0, lines, "white"))
            keep_off[max(0, z - 1):z + 3, max(0, x - 1):x + 2] = True
            count["statues"] += 1
        elif kind_ == "flagpole":                   # a pole 10 high and a flag at the top
            y = column(x, z, 11)
            if y is None or column(x + 1, z, 11) is None:
                continue
            vol[y:y + 10, z, x] = FENCES[0]
            flag = [[block("blue_concrete"), RED_CONCRETE, RED_CONCRETE], [WHITE_CONCRETE] * 3]
            for row, top in enumerate((y + 9, y + 8)):
                for k in range(3):
                    if x + 1 + k < size and vol[top, z, x + 1 + k] in low:
                        vol[top, z, x + 1 + k] = flag[row][k]
            keep_off[z, x] = True
            count["flagpoles"] += 1
        elif kind_ == "power":                      # a mast with two cross-arms (the map gives no height)
            try:
                height = int(float(tags.get("height", "").split()[0]))
            except (ValueError, IndexError):
                height = 20
            height = max(8, min(height, layers - int(g[z, x]) - 3))
            y = column(x, z, height)
            if y is None:
                continue
            vol[y:y + height, z, x] = GARDEN_WALLS[0]
            for arm in (y + height - 1, y + height - 4):
                for k in range(-3, 4):
                    if 0 <= x + k < size and (k == 0 or vol[arm, z, x + k] in low):
                        vol[arm, z, x + k] = GARDEN_WALLS[0b1100]
            keep_off[z, x] = True
            count["power towers"] += 1

    def nearest(cells, u, v, uu, vv):
        return cells[int(np.argmin((u - uu) ** 2 + (v - vv) ** 2))]

    for sport, cells, u, v, half_l, half_w, ax, ay in details.get("sports", []):
        put = []                                    # (x, z, layer above the ground, block)
        if sport in ("soccer", "american_football") or (sport == "soccer" and half_l > 10):
            for end in (-1, 1):
                if sport == "soccer":               # goal: 7.32 x 2.44 m (3 x 1.5 on small pitches)
                    half_goal = 3.66 if half_l > 30 else 1.5
                    line = (np.abs(u - end * (half_l - 1)) < 0.5) & (np.abs(v) <= half_goal + 0.5)
                    for x, z in cells[line]:
                        put.append((x, z, 2, WHITE_CONCRETE))
                    for side in (-1, 1):
                        x, z = nearest(cells, u, v, end * (half_l - 1), side * half_goal)
                        put += [(x, z, 0, WHITE_CONCRETE), (x, z, 1, WHITE_CONCRETE)]
                else:                               # goalposts: a yellow post, a bar 5.6 m wide, uprights
                    x, z = nearest(cells, u, v, end * (half_l - 0.5), 0)
                    put += [(x, z, k, YELLOW_CONCRETE) for k in range(3)]
                    bar = (np.abs(u - end * (half_l - 0.5)) < 0.5) & (np.abs(v) <= 2.8)
                    put += [(x, z, 3, YELLOW_CONCRETE) for x, z in cells[bar]]
                    for side in (-1, 1):
                        x, z = nearest(cells, u, v, end * (half_l - 0.5), side * 2.8)
                        put += [(x, z, k, YELLOW_CONCRETE) for k in range(4, 9)]
        elif sport in ("tennis", "pickleball", "volleyball", "badminton"):
            net = set(map(tuple, cells[(np.abs(u) < 0.5) & (np.abs(v) <= half_w - 0.5)]))
            for x, z in net:
                bits = sum(1 << i for i, (dz, dx) in enumerate(STEP) if (x + dx, z + dz) in net)
                put.append((x, z, 0, FENCES[bits]))
        elif sport == "basketball":                 # a pole and a backboard at each end
            for end in (-1, 1):
                x, z = nearest(cells, u, v, end * (half_l - 1.2), 0)
                put += [(x, z, k, LAMP_POST) for k in range(3)] + [(x, z, 3, WHITE_CONCRETE)]
        if not put:
            continue
        for x, z, k, blk in put:
            y = int(g[z, x]) + 1 + k
            if y < layers and not has_building[z, x] and vol[y, z, x] in low:
                vol[y, z, x] = blk
                keep_off[z, x] = True
        count["goals, nets and hoops"] += 1

    # stands: rows that rise one block each away from the nearest pitch, seats facing it
    pitch_centres = [cells.mean(axis=0) for _, cells, *_ in details.get("sports", [])]
    steps = ROOF_STAIRS[block("polished_andesite")]
    for cells, u, v, half_l, half_w, ax, ay in details.get("stands", []):
        if not len(cells) or not pitch_centres:
            continue
        centre = cells.mean(axis=0)
        field = min(pitch_centres, key=lambda c: float(np.hypot(*(c - centre))))
        towards = float(np.dot(field - centre, ay))
        s = 1 if towards > 0 else -1                # +v runs towards the pitch when s is 1
        row = np.floor(half_w - s * v).astype(int)  # 0 = the front row
        away = cardinal(*(-s * ay))
        for (x, z), r in zip(cells, row):
            if has_building[z, x]:
                continue
            y = int(g[z, x]) + 1
            h = min(max(r, 0), 10)
            if y + h + 2 >= layers:
                continue
            vol[y:y + h, z, x] = STONE_BRICKS
            vol[y + h, z, x] = steps[away]          # back to the top of the stand
            keep_off[z, x] = True
        count["stands"] += 1
    return count


def build_bridges(vol, bridges, g, level, water, ground, has_building, ground_track=None, bed=None):
    """Roads and railways on bridges, at a height worked out the way OSM2World does it (the map only says that a
    bridge passes over something): the deck meets the ground at both ends, stays clear of what it crosses (2 blocks of
    headroom over water, 4 over roads and rails, 5 more per extra layer), and climbs no steeper than 1 in 3 so rails
    can follow. A two-block deck with walls along both edges, on stone pillars every 12 blocks that keep out of
    roads and tracks underneath. Returns (deck and pillar blocks placed, rails laid on bridges)."""
    if not bridges:
        return 0, 0
    size = g.shape[0]
    layers = vol.shape[0]
    under = np.where(water, level, g)                            # the top of what is below each cell
    busy = np.isin(ground, (S_ROAD, S_LINE, S_PARKING)) | (ground_track if ground_track is not None else False)
    deck = np.full((size, size), -1, dtype=np.int32)             # layer of the deck's top block
    edge = np.full((size, size), -1, dtype=np.int32)             # the walls along the sides: layer of their deck
    rail_deck = np.zeros((size, size), dtype=bool)
    surface_of = np.zeros((size, size), dtype=np.uint8)
    pillars = []
    for width, surface, line, layer, railway in bridges:
        pts = np.asarray(line, dtype=float)
        seg = np.diff(pts, axis=0)
        lengths = np.hypot(seg[:, 0], seg[:, 1])
        total = float(lengths.sum())
        if total < 1:
            continue
        s_at = np.arange(0, total + 0.5, 0.5)                    # a point every half block along the bridge
        cum = np.r_[0, np.cumsum(lengths)]
        i = np.clip(np.searchsorted(cum, s_at, side="right") - 1, 0, len(seg) - 1)
        t = ((s_at - cum[i]) / np.maximum(lengths[i], 1e-9))[:, None]
        p = pts[i] + seg[i] * t
        d = seg[i] / np.maximum(lengths[i], 1e-9)[:, None]       # direction, and across it
        nrm = np.stack([-d[:, 1], d[:, 0]], axis=1)
        inside = (p[:, 0] >= 0) & (p[:, 0] < size) & (p[:, 1] >= 0) & (p[:, 1] < size)
        if inside.sum() < 2:
            continue
        p, d, nrm, s_at = p[inside], d[inside], nrm[inside], s_at[inside]
        half = width / 2
        across = np.arange(-half, half + 0.01, 0.5)
        cx = np.clip((p[:, None, 0] + nrm[:, None, 0] * across[None]).astype(int), 0, size - 1)
        cz = np.clip((p[:, None, 1] + nrm[:, None, 1] * across[None]).astype(int), 0, size - 1)
        below = under[cz, cx].max(axis=1)
        crossing_busy = busy[cz, cx].any(axis=1) & (s_at > s_at[0] + half + 2) & (s_at < s_at[-1] - half - 2)
        # (the road leading onto the bridge at each end is not something it has to clear)
        crossing_water = water[cz, cx].any(axis=1)
        clearance = np.where(crossing_busy, 5, np.where(crossing_water, 3, 0)) + 5 * (layer - 1) * (
            crossing_busy | crossing_water)                       # over plain ground the deck may sit on it
        need = below + clearance
        x0, z0 = int(p[0, 0]), int(p[0, 1])
        x1, z1 = int(p[-1, 0]), int(p[-1, 1])
        ends = np.interp(s_at, [s_at[0], s_at[-1]], [g[z0, x0], g[z1, x1]])
        h = np.maximum(ends, need).astype(float)
        step = 0.5 / 3                                           # at most 1 block up per 3 along
        for k in range(1, len(h)):
            h[k] = max(h[k], h[k - 1] - step)
        for k in range(len(h) - 2, -1, -1):
            h[k] = max(h[k], h[k + 1] - step)
        h = np.round(h).astype(np.int32)
        hh = np.broadcast_to(h[:, None], cx.shape)
        deck[cz, cx] = np.maximum(deck[cz, cx], hh)
        surface_of[cz, cx] = surface
        if railway:
            mid = np.abs(across) < 0.75
            rail_deck[cz[:, mid], cx[:, mid]] = True
        # the walls: every cell around this deck, at the height of the deck next to it, but not across the two
        # ends (a wall block joins only the blocks at its sides, so on a diagonal bridge it takes the corner cells too)
        zlo, xlo = max(int(cz.min()) - 1, 0), max(int(cx.min()) - 1, 0)
        zhi, xhi = min(int(cz.max()) + 2, size), min(int(cx.max()) + 2, size)
        kmap = np.full((zhi - zlo, xhi - xlo), -1, dtype=np.int32)      # which sample made each deck cell
        kmap[cz - zlo, cx - xlo] = np.broadcast_to(np.arange(len(h))[:, None], cz.shape)
        pad, (H, W) = np.pad(kmap, 1, constant_values=-1), kmap.shape
        near = np.full(kmap.shape, -1, dtype=np.int32)
        for dz in (-1, 0, 1):                                    # sides and corners: a diagonal outline
            for dx in (-1, 0, 1):                                # then joins up side to side
                near = np.maximum(near, pad[1 + dz:1 + dz + H, 1 + dx:1 + dx + W])
        zz, xx = np.nonzero((kmap < 0) & (near >= 0))
        k = near[zz, xx]
        along = s_at[k] + (xx + xlo + 0.5 - p[k, 0]) * d[k, 0] + (zz + zlo + 0.5 - p[k, 1]) * d[k, 1]
        keep = (along > s_at[0] + 0.5) & (along < s_at[-1] - 0.5)
        ez, ex = zz[keep] + zlo, xx[keep] + xlo
        np.maximum.at(edge, (ez, ex), h[k[keep]])
        for k in range(0, len(h), 24):                           # pillars every 12 blocks, at both edges
            if h[k] - below[k] < 3:
                continue
            for side in (-1, 1):
                px = int(np.clip(p[k, 0] + nrm[k, 0] * side * max(half - 1, 0.5), 0, size - 1))
                pz = int(np.clip(p[k, 1] + nrm[k, 1] * side * max(half - 1, 0.5), 0, size - 1))
                if not busy[pz, px] and not has_building[pz, px]:
                    pillars.append((pz, px, int(h[k])))
    placed = 0
    deck_block = {S_ROAD: ROAD, S_PATH: SMOOTH_STONE, S_FOOT: SMOOTH_STONE, S_PAVED: PAVED, S_GRAVEL: GRAVEL,
                  S_SIDEWALK: SMOOTH_STONE}
    zz, xx = np.nonzero((deck >= 0) & ~has_building)
    for z, x in zip(zz, xx):
        y = int(deck[z, x])
        if not 2 <= y < layers - 4:
            continue
        vol[y, z, x] = deck_block.get(int(surface_of[z, x]), ROAD)
        if y - under[z, x] >= 4:                                  # a thicker deck where there is room under it
            vol[y - 1, z, x] = STONE_BRICKS
        for up in range(1, 4):                                   # room to walk: no leaves or grass on the deck
            if vol[y + up, z, x] not in (AIR, LANTERN, LAMP_POST):
                vol[y + up, z, x] = AIR
        placed += 2
    walls = (edge >= 0) & (deck < 0) & ~has_building
    wall_h = np.where(walls, edge, -100)
    same = [nb == wall_h for nb in neighbours(wall_h)]            # join walls at the same height: N, S, W, E
    bits = sum(same[i].astype(np.int32) << i for i in range(4))
    for z, x in zip(*np.nonzero(walls)):
        y = int(edge[z, x])
        if 2 <= y < layers - 2:
            vol[y, z, x] = STONE_BRICKS
            vol[y + 1, z, x] = GARDEN_WALLS[int(bits[z, x])]
            placed += 2
    for z, x, y in pillars:
        bottom = int(bed[z, x]) if (water[z, x] and bed is not None) else int(under[z, x]) + 1   # from the river bed
        for yy in range(max(1, bottom), y - 1):
            vol[yy, z, x] = STONE_BRICKS
            placed += 1
    rails = 0
    if rail_deck.any():
        rails = lay_rails(vol, rail_deck & (deck >= 0), np.where(deck >= 0, deck, 0))
    return placed, rails


AREA_KEYS = ("leisure", "amenity", "landuse", "tourism", "historic", "natural", "place", "sport")


def place_labels(elements, lat, lon, size, bmap, details, step=2):
    """Where am I: the names of buildings, streets and named areas (parks, greens, campuses, fields) on a grid of
    `step`-block cells, for the walk-through to show. Returns (names, uint16 array (3, n, n): building, street, area),
    0 = no name."""
    names, index = [""], {}

    def idx(name):
        if name not in index:
            index[name] = len(names)
            names.append(name)
        return index[name]

    n = -(-size // step)
    layers = np.zeros((3, n, n), dtype=np.uint16)
    lut = np.zeros(int(bmap.max()) + 1, dtype=np.uint16)
    for b, name in (getattr(build_map, "building_names", {}) or {}).items():
        if b < len(lut):
            lut[b] = idx(name)
    layers[0] = lut[bmap][step // 2::step, step // 2::step][:n, :n]
    img = Image.new("I", (size, size), 0)
    draw = ImageDraw.Draw(img)
    for name, width, line in (details or {}).get("streets", []):
        if name and len(line) >= 2:
            draw.line([tuple(pt) for pt in line], fill=idx(name), width=max(3, int(width) + 3))
    layers[1] = np.array(img, dtype=np.int64)[step // 2::step, step // 2::step][:n, :n].astype(np.uint16)
    m_lat, m_lon = metres_per_degree(lat)
    px = lambda q: ((q["lon"] - lon) * m_lon + size / 2, (lat - q["lat"]) * m_lat + size / 2)
    areas = []
    for el in elements:
        tags = el.get("tags") or {}
        if not tags.get("name") or tags.get("building") or tags.get("highway") or not any(k in tags for k in AREA_KEYS):
            continue
        if el.get("type") == "way" and el.get("geometry") and len(el["geometry"]) >= 4 \
                and el["geometry"][0] == el["geometry"][-1]:
            rings = [[px(q) for q in el["geometry"]]]
        elif el.get("type") == "relation":
            rings = [[px(q) for q in m["geometry"]] for m in el.get("members", [])
                     if m.get("role") != "inner" and m.get("geometry") and len(m["geometry"]) >= 2]
        else:
            continue
        pts = [pt for r in rings for pt in r]
        if not pts:
            continue
        xs, zs = [pt[0] for pt in pts], [pt[1] for pt in pts]
        if max(xs) < 0 or min(xs) >= size or max(zs) < 0 or min(zs) >= size:
            continue
        areas.append(((max(xs) - min(xs)) * (max(zs) - min(zs)), tags["name"], rings))
    img = Image.new("I", (size, size), 0)
    draw = ImageDraw.Draw(img)
    for _, name, rings in sorted(areas, key=lambda a: -a[0]):      # the big ones first, so smaller ones show on top
        joined = rings if len(rings) == 1 else join_lines(rings)
        for r in joined:
            if len(r) >= 3:
                draw.polygon(r, fill=idx(name))
    layers[2] = np.array(img, dtype=np.int64)[step // 2::step, step // 2::step][:n, :n].astype(np.uint16)
    return names, layers


def join_lines(lines):
    """Join the pieces of a relation's outline into closed rings (pieces meet end to end)."""
    pool = [list(line) for line in lines]
    out = []
    while pool:
        cur = pool.pop(0)
        grown = True
        while grown and cur[0] != cur[-1]:
            grown = False
            for i, line in enumerate(pool):
                if line[0] == cur[-1]:
                    cur += line[1:]
                elif line[-1] == cur[-1]:
                    cur += line[::-1][1:]
                else:
                    continue
                pool.pop(i)
                grown = True
                break
        out.append(cur)
    return out


def prepare_lidar(path, lat, lon, size, ground, bmap, specs):
    """load_lidar, then: buildings tagged place2mc:lidar=no (built after the scan) keep their own heights, and the
    buildings the scan found but the map lacks are added. Returns (lidar, how many buildings were added)."""
    lidar = load_lidar(path, lat, lon, size)
    skip = list(getattr(build_map, "no_lidar", ()))
    if skip:
        lidar["roof"][np.isin(bmap, skip)] = np.nan
        lidar["unmapped"][np.isin(bmap, skip)] = np.nan
    return lidar, add_scanned_buildings(ground, bmap, specs, lidar)


def add_scanned_buildings(ground, bmap, specs, lidar, min_cells=30):
    """Buildings the laser scan found but the map does not have (app/lidar.js: patches of hard roof at least 3 m up).
    A patch touching a mapped building is part of it (a tower, bay or porch its outline leaves out) and joins it. Any
    other patch becomes a building with the measured roof; patches mostly over roads, rails or water (lorries, bridges)
    are left out. Returns how many buildings were added; bmap, specs and lidar["roof"] are changed in place."""
    rel = lidar.get("unmapped")
    if rel is None:
        return 0
    size = bmap.shape[0]
    cand = np.isfinite(rel) & (bmap == 0)
    ring_ok = np.isfinite(lidar["roof"]) & (lidar["roof"] >= 2.5) & (bmap == 0)    # measured just outside an outline
    seen = np.zeros_like(cand)
    added = 0
    busy = np.isin(ground, (S_ROAD, S_LINE, S_WATER, S_SEA, S_PARKING))
    for start in np.flatnonzero(cand):
        if seen.flat[start]:
            continue
        stack, cells = [int(start)], []
        seen.flat[start] = True
        while stack:
            i = stack.pop()
            cells.append(i)
            z, x = divmod(i, size)
            for zz, xx in ((z - 1, x), (z + 1, x), (z, x - 1), (z, x + 1)):
                if 0 <= zz < size and 0 <= xx < size and cand[zz, xx] and not seen[zz, xx]:
                    seen[zz, xx] = True
                    stack.append(zz * size + xx)
        cells = np.array(cells)
        # next to a mapped building: a tower, bay or porch its outline leaves out, so it joins that building. Between
        # the two there may be one ring of cells the scan reader kept with the building (it measures one cell beyond
        # each outline): those bridge the gap
        cz, cx = np.divmod(cells, size)
        nb_ids, bridge = [], []
        for z, x in zip(cz, cx):
            for z2, x2 in ((z - 1, x), (z + 1, x), (z, x - 1), (z, x + 1)):
                if not (0 <= z2 < size and 0 <= x2 < size):
                    continue
                if bmap[z2, x2] > 0:
                    nb_ids.append(bmap[z2, x2])
                elif ring_ok[z2, x2] and not cand[z2, x2]:
                    for z3, x3 in ((z2 - 1, x2), (z2 + 1, x2), (z2, x2 - 1), (z2, x2 + 1)):
                        if 0 <= z3 < size and 0 <= x3 < size and bmap[z3, x3] > 0:
                            nb_ids.append(bmap[z3, x3])
                            bridge.append(z2 * size + x2)
        if nb_ids:
            owner = max(set(nb_ids), key=nb_ids.count)
            bmap.flat[cells] = owner
            lidar["roof"].flat[cells] = rel.flat[cells]
            if bridge:
                bridge = np.unique(bridge)
                bmap.flat[bridge] = owner
            attached = getattr(add_scanned_buildings, "attached", 0)
            add_scanned_buildings.attached = attached + len(cells)
            continue
        if len(cells) < min_cells or busy.flat[cells].mean() > 0.3:
            continue
        pick = (int(cells[0]) * 2654435761) >> 8
        house = len(cells) < 250
        top = float(np.nanmedian(rel.flat[cells]))
        sloped = np.nanpercentile(rel.flat[cells], 90) - np.nanpercentile(rel.flat[cells], 10) >= 1.5
        if house:   # most small buildings round here are brick houses
            wall = BRICK if pick % 5 < 3 else HOUSE_WALLS[pick % len(HOUSE_WALLS)]
            roof, storey, glass, spacing, base = (DEEPSLATE_ROOF if sloped else FLAT_ROOFS[1]), 3, GLASS, 4, wall
        else:
            wall = BIG_WALLS[pick % len(BIG_WALLS)]
            roof, storey, glass, spacing = (DEEPSLATE_ROOF if sloped else FLAT_ROOFS[pick % len(FLAT_ROOFS)]), 4, \
                GLASSES[(pick >> 6) % len(GLASSES)], 3
            base = BASES[(pick >> 12) % len(BASES)]
        specs.append((max(3, int(round(top))), wall, roof, sloped, True, storey, glass, base, spacing,
                      (pick >> 14) % len(DOORS) if sloped else 0, False, False))
        bmap.flat[cells] = len(specs) - 1
        lidar["roof"].flat[cells] = rel.flat[cells]
        uses = getattr(build_map, "building_uses", None)
        if uses is not None:
            uses.append("home" if house else "office")
        added += 1
    return added


def clean_roof_cells(value, bmap, use):
    """Take out what on a roof is not the building. Branches over a roof (the scan's highest echo there is a tree) show as
    thin strips and specks well above the roof: those cells become gaps, to be filled from the roof around them. Towers,
    cupolas and steeples stand as compact blocks and are kept, peak and all. Then a 3 x 3 median within each building
    smooths the rest of the roof (the scan's noise), so a flat roof does not come out as a field of one-block bumps."""
    size = bmap.shape[0]
    out = value.copy()
    keep_peak = np.zeros(value.shape, dtype=bool)          # towers, spires, pinnacles: not smoothed away
    ids = np.unique(bmap[use])
    zz, xx = np.nonzero(use)
    order = np.argsort(bmap[zz, xx], kind="stable")
    zz, xx = zz[order], xx[order]
    bounds = np.searchsorted(bmap[zz, xx], np.r_[ids, ids[-1] + 1]) if len(ids) else []
    for k, b in enumerate(ids):
        cz, cx = zz[bounds[k]:bounds[k + 1]], xx[bounds[k]:bounds[k + 1]]
        v = out[cz, cx]
        fin = np.isfinite(v)
        if fin.sum() < 6:
            continue
        lid_level = np.percentile(v[fin], 90)
        high = fin & (v > lid_level + 2.5)
        if not high.any():
            continue
        # group the high cells (8 neighbours) and keep the compact ones
        z0, x0 = cz.min(), cx.min()
        grid = np.zeros((cz.max() - z0 + 3, cx.max() - x0 + 3), dtype=bool)
        grid[cz[high] - z0 + 1, cx[high] - x0 + 1] = True
        seen = np.zeros_like(grid)
        for sz, sx in zip(*np.nonzero(grid)):
            if seen[sz, sx]:
                continue
            stack, part = [(sz, sx)], []
            seen[sz, sx] = True
            while stack:
                a, c = stack.pop()
                part.append((a, c))
                for da in (-1, 0, 1):
                    for dc in (-1, 0, 1):
                        na, nc = a + da, c + dc
                        if grid[na, nc] and not seen[na, nc]:
                            seen[na, nc] = True
                            stack.append((na, nc))
            pa = np.array(part)
            h, w = np.ptp(pa[:, 0]) + 1, np.ptp(pa[:, 1]) + 1
            compact = len(pa) >= 6 and min(h, w) >= 2 and len(pa) >= 0.55 * h * w and max(h, w) <= 3 * min(h, w) + 2
            if not compact:
                out[pa[:, 0] + z0 - 1, pa[:, 1] + x0 - 1] = np.nan
            else:
                keep_peak[pa[:, 0] + z0 - 1, pa[:, 1] + x0 - 1] = True
    # 3 x 3 median within the same building (NaN-aware)
    stack_v = []
    for dz in (-1, 0, 1):
        for dx in (-1, 0, 1):
            nb_v = np.roll(np.roll(out, dz, 0), dx, 1)
            nb_b = np.roll(np.roll(bmap, dz, 0), dx, 1)
            stack_v.append(np.where(nb_b == bmap, nb_v, np.nan))
    with np.errstate(all="ignore"):
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            med = np.nanmedian(np.stack(stack_v), axis=0)
    smoothed = np.where(keep_peak, out, med)
    return np.where(use & np.isfinite(out), smoothed, np.where(use, np.nan, out))


def clean_trees(trees, min_crown=1.0):
    """The scan's tree tops, tidied: tops with a crown under 1 m from the trunk are poles, wires and lamp posts; a lower
    top inside a taller tree's crown is a branch of that tree, not a tree of its own."""
    if len(trees) == 0:
        return trees
    # and a "tree" far taller than it is wide (crown under 8% of its height) is a spire, a mast or a pinnacle the
    # map's outline leaves out, not a tree
    # A narrow top 8-14 m up with a crown under 1.5 m is a utility pole with its cross-arms and wires, which line the
    # streets here, not a tree
    pole = (trees[:, 2] >= 8) & (trees[:, 2] <= 14) & (trees[:, 3] < 1.5) & (trees[:, 3] < 0.15 * trees[:, 2])
    t = trees[(trees[:, 3] >= min_crown) & (trees[:, 3] >= 0.08 * trees[:, 2]) & ~pole]
    t = t[np.argsort(-t[:, 2], kind="stable")]
    cell = 8.0
    buckets = {}
    keep = []
    for row in t:
        x, z, h, r = row
        bx, bz = int(x // cell), int(z // cell)
        hit = False
        for gx in range(bx - 2, bx + 3):
            for gz in range(bz - 2, bz + 3):
                for kx, kz, kr in buckets.get((gx, gz), ()):
                    if (x - kx) ** 2 + (z - kz) ** 2 < kr * kr:
                        hit = True
                        break
                if hit:
                    break
            if hit:
                break
        if hit:
            continue
        keep.append(row)
        buckets.setdefault((bx, bz), []).append((x, z, r))
    return np.array(keep).reshape(-1, 4)


# what is inside a building: open halls have no floors in between
HALL_USES = ("gym", "pool", "theatre", "chapel")
INSIDE_USES = ("library", "dorm", "gym", "pool", "theatre", "chapel", "dining", "museum", "lab", "classroom", "office",
               "home", "shop")


def building_use(tags):
    """What a building is used for, so its inside can be built to match: place2mc:inside if a tag file says it, else
    from its tags and name."""
    t = tags or {}
    said = (t.get("place2mc:inside") or "").lower()
    if said in INSIDE_USES:
        return said
    name = (t.get("name") or "").lower()
    b, a, sport = t.get("building", ""), t.get("amenity", ""), t.get("sport", "")
    words = lambda *ks: any(k in name for k in ks)
    if a == "library" or words("library"):
        return "library"
    if sport == "swimming" or words("aquatic", "natatorium", "swimming"):
        return "pool"
    if b in ("dormitory",) or words("college house", "residence hall", "dormitor"):
        return "dorm"
    if t.get("leisure") in ("sports_centre", "sports_hall") or b in ("sports_hall", "sports_centre", "gymnasium") \
            or words("gym", "physical education", "fitness", "arena", "field house", "fieldhouse"):
        return "gym"
    if a in ("theatre", "cinema", "arts_centre") or words("theatre", "theater", "performing arts", "auditorium",
                                                           "concert", "music hall"):
        return "theatre"
    if b in ("chapel", "church", "cathedral", "temple", "mosque", "synagogue") or a == "place_of_worship" \
            or words("chapel", "church"):
        return "chapel"
    if a in ("restaurant", "fast_food", "cafe", "food_court", "pub", "bar") or words("dining", "refectory", "cafeteria"):
        return "dining"
    if a in ("museum",) or t.get("tourism") in ("museum", "gallery") or words("museum", "gallery"):
        return "museum"
    if words("laborator", "science"):
        return "lab"
    if b in ("school", "university", "college") or a in ("school", "university", "college"):
        return "classroom"
    if b in ("retail", "supermarket", "commercial", "kiosk") or "shop" in t:
        return "shop"
    if b in HOUSE_TYPES or b in ("apartments", "residential", "terrace"):
        return "home"
    return "office"


def furnish_by_use(vol, bmap, uses, inside, floor0, height, storey, top, rng):
    """Build the inside of each building to match its use: bookshelf rows in a library, bedrooms along a corridor in
    a dorm, a court with bleachers in a gym, a pool with lanes, seats facing a stage, pews facing an altar, tables in
    a dining hall, display cases in a museum, lab benches, desks facing a board in classrooms. `uses`: the use of
    every building number; `top`: the layer of the roof above each cell. Returns how many blocks were placed."""
    size = bmap.shape[0]
    layers = vol.shape[0]
    seat = ROOF_STAIRS[block("spruce_planks")]
    pew = ROOF_STAIRS[block("dark_oak_planks")]
    red_seat = ROOF_STAIRS[block("red_nether_bricks")]
    placed = 0
    ids = np.unique(bmap[inside])
    for b in ids:
        use = uses[b] if b < len(uses) else "office"
        if use in ROOMED_USES:                # rooms off corridors: plan_rooms
            continue
        cz, cx = np.nonzero((bmap == b) & inside)
        if len(cz) < 12:
            continue
        z0, x0 = cz.min(), cx.min()
        long_x = np.ptp(cx) >= np.ptp(cz)
        u = (cx - x0) if long_x else (cz - z0)                  # along the building
        v = (cz - z0) if long_x else (cx - x0)                  # across it
        umax, vmax = u.max(), v.max()
        f0 = floor0[cz, cx]
        h = height[cz, cx]
        st = np.maximum(storey[cz, cx], 3)
        ceiling = np.minimum(top[cz, cx] - 1, layers - 2)
        free = lambda y: vol[np.clip(y, 0, layers - 1), cz, cx] == AIR

        def put(mask, y, blk):
            nonlocal placed
            m = mask & free(y) & (y < ceiling) & (y >= 0)
            if np.ndim(blk):
                vol[y[m], cz[m], cx[m]] = blk[m]
            else:
                vol[y[m], cz[m], cx[m]] = blk
            placed += int(m.sum())

        # toward the higher u (the far end) / lower u, as stair facings, in this building's frame
        to_far = EAST if long_x else SOUTH
        to_near = WEST if long_x else NORTH
        storeys = [(f0 + j * st + (j > 0)) for j in range(int((h // st).max()) + 1)]
        if use in ("gym", "pool", "theatre", "chapel"):
            lamp = (u % 6 == 3) & (v % 6 == 3)
            m = lamp & (ceiling > f0 + 3)
            vol[ceiling[m], cz[m], cx[m]] = CEILING_LAMP
            placed += int(m.sum())
        else:                                 # libraries, dining halls, museums: lamps on every floor
            lamp = (u % 6 == 3) & (v % 6 == 3)
            for j, y in enumerate(storeys):
                ceil = np.minimum(f0 + (j + 1) * st - 1, ceiling)
                m = lamp & (ceil > y + 1)
                m &= vol[np.clip(ceil, 0, layers - 1), cz, cx] == AIR
                vol[ceil[m], cz[m], cx[m]] = CEILING_LAMP
                placed += int(m.sum())
        if use == "gym":                      # a court: light wood, white lines, bleachers along one side
            vol[(f0 - 1), cz, cx] = BIRCH_PLANKS
            line = ((u == 2) | (u == umax - 2) | (v == 2) | (v == vmax - 2) | (u == umax // 2)) & \
                (u >= 2) & (u <= umax - 2) & (v >= 2) & (v <= vmax - 2)
            vol[(f0 - 1)[line], cz[line], cx[line]] = WHITE_CONCRETE
            for row in range(3):              # bleachers: three rising rows facing the court
                m = (v == row) & (u >= 3) & (u <= umax - 3)
                face = SOUTH if long_x else EAST
                for up in range(row + 1):
                    put(m, f0 + up, seat[face] if up == row else BIRCH_PLANKS)
        elif use == "pool":                   # a pool with lanes, two blocks deep, a stone deck round it
            basin = (u >= 3) & (u <= umax - 3) & (v >= 3) & (v <= vmax - 3)
            vol[(f0 - 1)[~basin], cz[~basin], cx[~basin]] = SMOOTH_STONE
            for d in (1, 2):
                vol[(f0 - d)[basin], cz[basin], cx[basin]] = WATER
            bottom = f0 - 3
            vol[bottom[basin], cz[basin], cx[basin]] = WHITE_CONCRETE
            lane = basin & ((v - 3) % 3 == 1) & (u >= 4) & (u <= umax - 4)
            vol[bottom[lane], cz[lane], cx[lane]] = BLUE_CONCRETE
            placed += int(basin.sum()) * 3
        elif use in ("theatre", "chapel"):    # a stage or an altar at the far end, seats or pews facing it
            stage = u >= umax - 4
            if use == "theatre":
                put(stage, f0, DARK_OAK_PLANKS_BLOCK)
            else:
                put(stage & (u == umax - 2) & (np.abs(v - vmax / 2) <= 1.5), f0, QUARTZ)
            aisle = np.abs(v - vmax / 2) < 1
            rows = (u >= 2) & (u <= umax - 7) & (u % 2 == 0) & ~aisle & (v >= 1) & (v <= vmax - 1)
            put(rows, f0, (red_seat if use == "theatre" else pew)[to_near])
        elif use == "library":                # shelves in rows across, two high, aisles between; tables by the windows
            rows = (u % 3 == 1) & (u >= 2) & (u <= umax - 2) & (v >= 3) & (v <= vmax - 3)
            tables = (v == 1) & (u % 4 == 2)
            for y in storeys:
                ok = y + 2 < f0 + h
                put(rows & ok, y, BOOKSHELF)
                put(rows & ok, y + 1, BOOKSHELF)
                put(tables & ok, y, DESK)
        elif use == "dining":                 # tables in rows with chairs, a serving counter along one end
            table = (u % 3 == 1) & (v % 4 == 2) & (u >= 2) & (u <= umax - 4) & (v >= 2) & (v <= vmax - 2)
            chair_a = (u % 3 == 1) & (v % 4 == 1) & (u >= 2) & (u <= umax - 4) & (v >= 1)
            chair_b = (u % 3 == 1) & (v % 4 == 3) & (u >= 2) & (u <= umax - 4) & (v <= vmax - 1)
            counter = (u == umax - 1) & (v >= 2) & (v <= vmax - 2)
            across_lo, across_hi = (NORTH, SOUTH) if long_x else (WEST, EAST)
            for y in storeys[:1]:
                put(table, y, DESK)
                put(chair_a, y, seat[across_lo])
                put(chair_b, y, seat[across_hi])
                put(counter, y, POLISHED_ANDESITE)
        elif use == "museum":                 # display cases on pedestals in a grid
            case = (u % 4 == 2) & (v % 4 == 2) & (u >= 2) & (u <= umax - 2) & (v >= 2) & (v <= vmax - 2)
            for y in storeys:
                ok = y + 2 < f0 + h
                put(case & ok, y, QUARTZ)
                put(case & ok, y + 1, GLASS)
    return placed


# Rooms: how wide each room is along the outside wall (counting one partition) and how deep it may go from the wall,
# by what the building is used for. A double room in an F&M residence hall is about 4 x 4 m (bed 41" x 85", desk
# 36" x 24", wardrobe 30" x 24" x 78", from F&M's housing pages); offices about 3 x 4 m; classrooms 7 x 6 m.
ROOM_PLANS = {"dorm": (5, 4), "office": (4, 4), "home": (4, 3), "classroom": (8, 6), "lab": (8, 6), "shop": (4, 4)}
ROOMED_USES = tuple(ROOM_PLANS)


def _runs(grid):
    """For every cell of a 2D mask: cells to its edge going up the first axis (a) and down it (b), itself included."""
    a = np.zeros(grid.shape, np.int32)
    b = np.zeros(grid.shape, np.int32)
    for i in range(grid.shape[0]):
        a[i] = (a[i - 1] + 1) * grid[i] if i else grid[i]
    for i in range(grid.shape[0] - 1, -1, -1):
        b[i] = (b[i + 1] + 1) * grid[i] if i < grid.shape[0] - 1 else grid[i]
    return a, b


def wall_frame(cz, cx):
    """Where each inside cell of a building stands against its walls, whatever the building's shape: d, how many
    cells from the nearer outside wall (1 = right against it), measured along the shorter way out; across_z, True
    when that way runs north-south; wall_dir, the direction of that wall (NORTH..EAST); width, the building's width
    that way; along, the coordinate along the wall (x or z), so rooms on both sides of a corridor line up."""
    z0, x0 = cz.min(), cx.min()
    Z, X = cz - z0, cx - x0
    grid = np.zeros((Z.max() + 1, X.max() + 1), bool)
    grid[Z, X] = True
    za, zb = (r[Z, X] for r in _runs(grid))
    xa, xb = (r.T[Z, X] for r in _runs(grid.T))
    dz, dx = np.minimum(za, zb), np.minimum(xa, xb)
    across_z = dz <= dx
    d = np.where(across_z, dz, dx)
    width = np.where(across_z, za + zb - 1, xa + xb - 1)
    wall_dir = np.where(across_z, np.where(za <= zb, NORTH, SOUTH), np.where(xa <= xb, WEST, EAST))
    along = np.where(across_z, cx, cz)
    return d, across_z, wall_dir, width, along


OPPOSITE = (SOUTH, NORTH, EAST, WEST)
STEP = ((-1, 0), (1, 0), (0, -1), (0, 1))            # (dz, dx) one cell towards NORTH, SOUTH, WEST, EAST


def storey_layers(f0, h, st, top):
    """(first free layer, first layer above the room) of every storey of a building: the ground floor stands on its
    floor block, the others on the floors build_volume lays every `st` layers."""
    out, j = [], 0
    while True:
        lo = f0 + j * st + (1 if j else 0)
        hi = min(f0 + (j + 1) * st, f0 + h, top)
        if hi - lo < 2:
            return out
        out.append((lo, hi))
        j += 1


def plan_rooms(vol, bmap, uses, mask, floor0, height, storey, top, entrances, rng):
    """Rooms off a corridor on every floor of offices, homes, residence halls, classroom and lab buildings: a ring of
    rooms along the outside walls (so every room has windows), a wall with a real door into each room, a corridor
    behind, a hall from every front door to the corridor, and in each room the furniture of its kind with a lamp.
    mask: the inside cells to plan; entrances: (z, x, building, inward direction) of every outside door.
    Returns (blocks placed, cells to keep clear for the stairs: building -> set of (z, x))."""
    layers = vol.shape[0]
    seat_wood = ROOF_STAIRS[block("spruce_planks")]
    sofa = ROOF_STAIRS[block("red_nether_bricks")]
    placed = 0
    lobby_of = {}
    by_building = {}
    for z, x, b, inward in entrances:
        by_building.setdefault(b, []).append((z, x, inward))
    ids = [b for b in np.unique(bmap[mask]) if b < len(uses) and uses[b] in ROOMED_USES]
    for b in ids:
        use = uses[b]
        cz, cx = np.nonzero((bmap == b) & mask)
        if len(cz) < 12:
            continue
        n_room, deepest = ROOM_PLANS[use]
        d, across_z, wall_dir, width, along = wall_frame(cz, cx)
        depth = np.minimum(deepest, (width - 3) // 2)          # leave a wall and a corridor at least 1 wide
        has_rooms = depth >= 2
        room = has_rooms & (d <= depth)
        room_wall = has_rooms & (d == depth + 1)
        a = along % n_room                                      # place across the room, 0 = the partition
        # deep buildings: a second ring of rooms on the inner side of a corridor 2 wide (no windows: studies,
        # kitchens, bathrooms, meeting rooms, more classrooms and labs), and an open hall in the very middle
        e = d - (depth + 4)                                     # cells from the inner ring's wall, 1 = by its door
        inner_depth = np.minimum(4, ((width + 1) // 2) - (depth + 4))
        inner_wall = has_rooms & (e == 0) & (inner_depth >= 2) & (use != "home")
        inner_room = has_rooms & (e >= 1) & (e <= inner_depth) & (inner_depth >= 2) & (use != "home")
        corridor = ~room & ~room_wall & ~inner_wall & ~inner_room
        partition = (room | inner_room) & (a == 0)
        door = (room_wall | inner_wall) & (a == n_room // 2)
        f0, h, st, roof_top = int(floor0[cz[0], cx[0]]), int(height[cz, cx].max()), max(int(storey[cz[0], cx[0]]), 3), \
            int(top[cz, cx].max())
        storeys = storey_layers(f0, h, st, roof_top)
        under_roof = top[cz, cx]                           # nothing goes up into a cell's roof
        if not storeys:
            continue
        # a hall from each front door straight in to the corridor, three cells wide, on the ground floor
        lobby = np.zeros(len(cz), bool)
        here = {(int(z), int(x)): i for i, (z, x) in enumerate(zip(cz, cx))}
        for z, x, inward in by_building.get(b, []):
            dz, dx = STEP[inward]
            for k in range(1, int(depth.max()) + 4):
                for side in (-1, 0, 1):
                    i = here.get((z + dz * k + dx * side, x + dx * k + dz * side))
                    if i is not None:
                        lobby[i] = True
        lobby_of[b] = {(int(cz[i]), int(cx[i])) for i in np.flatnonzero(lobby)}
        wood_door = DOORS[1]
        for j, (lo, hi) in enumerate(storeys):
            free = lambda y: vol[np.clip(y, 0, layers - 1), cz, cx] == AIR

            def put(m, y, blk):
                nonlocal placed
                y = np.broadcast_to(np.asarray(y), cz.shape)
                m = m & (y < hi) & (y < under_roof) & free(y)
                vol[y[m], cz[m], cx[m]] = blk[m] if np.ndim(blk) else blk
                placed += int(m.sum())

            open_here = lobby if j == 0 else np.zeros(len(cz), bool)
            if use == "shop" and j == 0:                      # a shop floor is one open room
                open_here = np.ones(len(cz), bool)
            wall_cells = (partition | (room_wall & ~door)) & ~open_here
            for y in range(lo, hi):
                put(wall_cells, y, WHITE_CONCRETE)
            for y in range(lo + 2, hi):                       # the wall above each door
                put(door & ~open_here, y, WHITE_CONCRETE)
            for y in range(lo, hi):                           # the inner ring's wall (its doors below)
                put(inner_wall & ~door & ~open_here, y, WHITE_CONCRETE)
            # the doors, facing into the room they open
            for f in range(4):
                into = np.where(inner_wall, np.array(OPPOSITE)[wall_dir], wall_dir)
                m = door & ~open_here & (into == f) & free(lo) & free(lo + 1) & (lo + 1 < under_roof)
                vol[lo, cz[m], cx[m]] = wood_door[(FACINGS[f], "lower")]
                vol[lo + 1, cz[m], cx[m]] = wood_door[(FACINGS[f], "upper")]
                placed += 2 * int(m.sum())
            # what each room is: on the ground floor of a residence hall, the rooms by the front door are the
            # lounge and the laundry (F&M: "central lounge and laundry room on the ground floor"); one room on each
            # upper floor is the floor's lounge; the rest are bedrooms, offices, classrooms or labs
            room_id = along // n_room
            kind = np.full(len(cz), use, dtype=object)
            if use == "dorm":
                near_door = np.zeros(len(cz), bool)
                for z, x, _ in by_building.get(b, []):
                    near_door |= (np.abs(cz - z) + np.abs(cx - x)) <= 14
                rid = np.where(room, room_id * 4 + wall_dir, -1)
                if j == 0:
                    lounge_ids = np.unique(rid[room & near_door])
                    kind[np.isin(rid, lounge_ids[::2])] = "lounge"
                    kind[np.isin(rid, lounge_ids[1::2])] = "laundry"
                else:
                    ids_here = np.unique(rid[room])
                    if len(ids_here):
                        kind[rid == ids_here[len(ids_here) // 2]] = "lounge"
            if use == "home":
                kind[room_id % 2 == 1] = "living"
            inner_kinds = {"dorm": ("study", "kitchen", "bathroom"), "office": ("meeting", "kitchen", "bathroom"),
                           "classroom": ("classroom", "classroom", "bathroom"), "lab": ("lab", "lab", "bathroom"),
                           "shop": ("meeting", "kitchen", "bathroom")}[use] if use != "home" else ("home",)
            ring_id = np.where(inner_room, room_id * 4 + wall_dir, 0)
            kind[inner_room] = np.array(inner_kinds, dtype=object)[ring_id[inner_room] % len(inner_kinds)]
            ok = room & ~open_here
            out = wall_dir                                    # towards the window wall
            back = np.array(OPPOSITE)[wall_dir]               # towards the corridor
            k = lambda name: ok & (kind == name)
            # residence hall bedroom (a double: two beds, nightstands, desks with chairs, wardrobes)
            bed_room = k("dorm")
            for f in range(4):
                m = bed_room & (out == f)
                put(m & (d == 1) & ((a == 1) | (a == n_room - 1)), lo, BEDS[(FACINGS[f], "head")])
                put(m & (d == 2) & ((a == 1) | (a == n_room - 1)), lo, BEDS[(FACINGS[f], "foot")])
            put(bed_room & (d == 1) & (a > 1) & (a < n_room - 1), lo, DESK)                    # nightstands
            put(bed_room & (d == depth) & (depth >= 3) & ((a == 1) | (a == n_room - 1)), lo, DESK)
            for f in range(4):
                m = bed_room & (depth >= 4) & (d == depth - 1) & ((a == 1) | (a == n_room - 1)) & (out == f)
                put(m, lo, seat_wood[f])                       # back to the beds, facing the desk
            ward = bed_room & (a == n_room // 2 + 1) & (d >= depth - 1) & (depth >= 3)
            put(ward, lo, SPRUCE_PLANKS)
            put(ward, lo + 1, SPRUCE_PLANKS)
            # lounge: sofas along the window wall, a low table, a bookcase
            m_l = k("lounge") | k("living")
            for f in range(4):
                put(m_l & (d == 1) & (a > 0) & (out == f), lo, sofa[f])          # back to the wall
            put(m_l & (d == 3) & (a == n_room // 2), lo, DESK)
            put(m_l & (d == depth) & (a == 1) & (depth >= 3), lo, BOOKSHELF)
            put(m_l & (d == depth) & (a == 1) & (depth >= 3), lo + 1, BOOKSHELF)
            put(m_l & (a > 0) & (d >= 2) & (d < depth), lo, RED_CARPET)
            # laundry: washers along the window wall, dryers stacked on them, a folding table
            m_w = k("laundry")
            put(m_w & (d == 1) & (a > 0), lo, IRON_BLOCK)
            put(m_w & (d == 1) & (a > 0), lo + 1, QUARTZ)
            put(m_w & (d == 3) & (a > 0) & (a < n_room - 1), lo, DESK)
            # office: a desk under the window with a chair, a bookcase, a carpet
            m_o = k("office") | k("shop")
            put(m_o & (d == 1) & (a == 1), lo, DESK)
            for f in range(4):
                put(m_o & (d == 2) & (a == 1) & (out == f), lo, seat_wood[OPPOSITE[f]])    # facing the desk
            put(m_o & (d == 1) & (a == n_room - 1), lo, BOOKSHELF)
            put(m_o & (d == 1) & (a == n_room - 1), lo + 1, BOOKSHELF)
            put(m_o & (a > 0) & (d >= 2), lo, GRAY_CARPET)
            # home bedroom
            m_h = k("home")
            for f in range(4):
                m = m_h & (out == f) & (a == 1)
                put(m & (d == 1), lo, BEDS[(FACINGS[f], "head")])
                put(m & (d == 2), lo, BEDS[(FACINGS[f], "foot")])
            put(m_h & (d == 1) & (a == n_room - 1), lo, SPRUCE_PLANKS)
            put(m_h & (d == 1) & (a == n_room - 1), lo + 1, SPRUCE_PLANKS)
            # classroom: a board on the partition wall, rows of desks with chairs facing it, the teacher's desk
            m_c = k("classroom")
            m_board = partition & (d >= 2) & (d < depth) & ~open_here & np.isin(room_id, np.unique(room_id[m_c]))
            m_board &= vol[min(lo + 1, layers - 1), cz, cx] == WHITE_CONCRETE
            if lo + 1 < hi:
                vol[lo + 1, cz[m_board], cx[m_board]] = BLACK_CONCRETE
            put(m_c & (a == 1) & (d == depth // 2 + 1), lo, DESK)                            # the teacher's desk
            desks = m_c & (a >= 3) & (a % 2 == 1) & (d >= 1) & (d <= depth - 1)
            put(desks, lo, DESK)
            chairs = m_c & (a >= 4) & (a % 2 == 0) & (d >= 1) & (d <= depth - 1)
            east_west = across_z                               # rows face the partition at a == 0
            put(chairs & east_west, lo, seat_wood[EAST])
            put(chairs & ~east_west, lo, seat_wood[SOUTH])
            # lab: benches down the middle with stools, fume cupboards along the window wall
            m_b = k("lab")
            put(m_b & (d == 1) & (a % 3 == 1), lo, IRON_BLOCK)
            put(m_b & (d == 1) & (a % 3 == 1), lo + 1, GLASS)
            bench = m_b & (d == 3) & (a >= 1) & (a <= n_room - 2)
            put(bench, lo, POLISHED_ANDESITE)
            for f in range(4):
                put(m_b & (d == 4) & (a % 2 == 1) & (a <= n_room - 2) & (out == f), lo, seat_wood[OPPOSITE[f]])
            # a shop floor: rows of shelves, kept clear of the way in
            if use == "shop" and j == 0:
                m_s = (d >= 2) & (d % 3 == 2) & (along % 5 != 0) & ~lobby
                put(m_s, lo, BOOKSHELF)
                put(m_s, lo + 1, BOOKSHELF)
            # the inner ring (e counts from its wall; its far end is e == inner_depth)
            ok_in = inner_room & ~open_here
            far = e == inner_depth
            m_st = ok_in & (kind == "study")                   # F&M: a study room on each floor
            put(m_st & far & (a % 2 == 1), lo, DESK)
            for f in range(4):
                put(m_st & (e == inner_depth - 1) & (a % 2 == 1) & (wall_dir == f), lo, seat_wood[f])
            put(m_st & (a == 1) & (e < inner_depth - 1) & (e > 1), lo, BOOKSHELF)
            put(m_st & (a == 1) & (e < inner_depth - 1) & (e > 1), lo + 1, BOOKSHELF)
            m_k = ok_in & (kind == "kitchen")                  # a counter, a stove, a fridge, a table
            put(m_k & far & (a > 0), lo, POLISHED_ANDESITE)
            put(m_k & far & (a == 2), lo, BLACK_CONCRETE)
            put(m_k & far & (a == n_room - 1), lo + 1, IRON_BLOCK)
            put(m_k & (e == 2) & (a == n_room // 2) & (inner_depth >= 4), lo, DESK)
            m_b = ok_in & (kind == "bathroom")                 # basins along the far wall, stalls
            put(m_b & far & (a % 2 == 1), lo, QUARTZ)
            put(m_b & (a > 0) & (e < inner_depth) & (e > 1) & (a % 2 == 0), lo, WHITE_CONCRETE)
            put(m_b & (a > 0) & (e < inner_depth) & (e > 1) & (a % 2 == 0), lo + 1, WHITE_CONCRETE)
            m_m = ok_in & (kind == "meeting")                  # a long table with chairs round it
            put(m_m & (e >= 2) & (e <= inner_depth - 1) & (a >= 2) & (a <= n_room - 2), lo, DESK)
            put(m_m & (e >= 2) & (e <= inner_depth - 1) & (a == 1) & across_z, lo, seat_wood[WEST])
            put(m_m & (e >= 2) & (e <= inner_depth - 1) & (a == 1) & ~across_z, lo, seat_wood[NORTH])
            m_ic = ok_in & (kind == "classroom")
            put(m_ic & (a >= 3) & (a % 2 == 1) & (e >= 1) & (e <= inner_depth - 1), lo, DESK)
            m_il = ok_in & (kind == "lab")
            put(m_il & far & (a % 3 == 1), lo, IRON_BLOCK)
            put(m_il & (e == 2) & (a >= 1) & (a <= n_room - 2), lo, POLISHED_ANDESITE)
            placed += join_pockets(vol, cz, cx, lo, (corridor & ~inner_room) | open_here, wall_dir)
            # a lamp in the middle of every room and every 6 cells along the corridors and halls
            lamp_room = (room & ~open_here & (a == n_room // 2) & (d == np.maximum((depth + 1) // 2, 1))) | \
                (inner_room & ~open_here & (a == n_room // 2) & (e == np.maximum((inner_depth + 1) // 2, 1)))
            lamp_hall = (corridor | open_here) & (along % 6 == 3) & \
                np.where(has_rooms & ~open_here, (d == depth + 2) | ((d > depth + 4) & (d % 6 == 3)), d % 6 == 3)
            m = (lamp_room | lamp_hall) & (hi - 1 > lo + 1)
            put(m, hi - 1, CEILING_LAMP)
    return placed, lobby_of


def join_pockets(vol, cz, cx, lo, seeds, wall_dir=None):
    """Walk one floor of a building from `seeds` (its corridors, front hall or stair landings; doors open, furniture
    in the way): wherever a bit of floor cannot be reached (a corner room without a door, a nook closed off by
    shelves), put a door in the wall between it and the reached floor, or take away the piece of furniture in the
    way; one opening for each closed-off bit, round after round. Returns blocks changed."""
    if lo + 1 >= vol.shape[0]:
        return 0
    walk = WALKABLE[vol[lo, cz, cx]] & WALKABLE[vol[lo + 1, cz, cx]]
    index = {(int(z), int(x)): i for i, (z, x) in enumerate(zip(cz, cx))}
    nbr = np.full((len(cz), 4), -1, np.int64)
    for f, (dz, dx) in enumerate(STEP):
        nbr[:, f] = [index.get((int(z) + dz, int(x) + dx), -1) for z, x in zip(cz, cx)]
    keep = KEEP_IN_PLACE

    def flood(start, allowed):
        seen = np.zeros(len(cz), bool)
        seen[start] = True
        todo = deque(np.flatnonzero(seen).tolist())
        while todo:
            i = todo.popleft()
            for k in nbr[i]:
                if k >= 0 and allowed[k] and not seen[k]:
                    seen[k] = True
                    todo.append(k)
        return seen

    changed = 0
    for _ in range(12):
        start = seeds & walk
        if not start.any():
            return changed
        seen = flood(start, walk)
        lost = walk & ~seen
        if not lost.any():
            return changed
        opened = False
        done = np.zeros(len(cz), bool)
        for i in np.flatnonzero(lost):
            if done[i]:
                continue
            part = flood(np.eye(1, len(cz), i, dtype=bool)[0], lost)      # this closed-off bit
            done |= part
            for j in np.flatnonzero(part):
                hit = False
                for f in range(4):
                    k = nbr[j, f]
                    if k < 0 or walk[k]:
                        continue
                    far = nbr[k, f]
                    if far < 0 or not seen[far]:
                        continue
                    z, x = int(cz[k]), int(cx[k])
                    if keep[vol[lo, z, x]] or keep[vol[lo + 1, z, x]]:
                        continue
                    if vol[lo, z, x] == WHITE_CONCRETE and vol[lo + 1, z, x] == WHITE_CONCRETE:
                        vol[lo, z, x] = DOORS[1][(FACINGS[OPPOSITE[f]], "lower")]     # opening into the bit
                        vol[lo + 1, z, x] = DOORS[1][(FACINGS[OPPOSITE[f]], "upper")]
                    else:
                        vol[lo, z, x] = AIR
                        if not WALKABLE[vol[lo + 1, z, x]]:
                            vol[lo + 1, z, x] = AIR
                    walk[k] = True
                    changed += 2
                    hit = opened = True
                    break
                if hit:
                    break
        if not opened:
            return changed
    return changed


def free_stairwell(cz, cx, here, clear, doors, length, floors_under):
    """A place for two lanes of stairs `length` long anywhere inside a building, along x or along z: every cell
    inside, none in the front hall or within 3 blocks of a door. Picks the one nearest a front door. Returns
    (score, cells by position as [lane A, lane B], run, None) or None."""
    z0, x0 = int(cz.min()), int(cx.min())
    ok = np.zeros((int(cz.max()) - z0 + 1, int(cx.max()) - x0 + 1), bool)
    ok[cz - z0, cx - x0] = True
    for (z, x) in clear:
        if 0 <= z - z0 < ok.shape[0] and 0 <= x - x0 < ok.shape[1]:
            ok[z - z0, x - x0] = False
    for dz_, dx_ in doors:
        ok[max(0, dz_ - z0 - 3):max(0, dz_ - z0 + 4), max(0, dx_ - x0 - 3):max(0, dx_ - x0 + 4)] = False
    s = np.zeros((ok.shape[0] + 1, ok.shape[1] + 1), np.int32)
    s[1:, 1:] = ok.cumsum(0).cumsum(1)
    best = None
    for run, (h, w) in (((0, 1), (2, length)), ((1, 0), (length, 2))):
        if ok.shape[0] < h or ok.shape[1] < w:
            continue
        full = s[h:, w:] - s[:-h, w:] - s[h:, :-w] + s[:-h, :-w] == h * w
        for tz, tx in zip(*np.nonzero(full)):
            z, x = int(tz) + z0, int(tx) + x0
            lanes = [[here[(z + run[0] * pos + run[1] * lane, x + run[1] * pos + run[0] * lane)]
                      for lane in (0, 1)] for pos in range(length)]
            score = min((abs(z - dz_) + abs(x - dx_) for dz_, dx_ in doors), default=0) \
                - 1000 * floors_under([k for lane in lanes for k in lane])
            if best is None or score < best[0]:
                best = (score, lanes, run, None)
    return best


def add_stairs(vol, bmap, uses, mask, floor0, height, storey, top, entrances, keep_clear):
    """A switch-back staircase in every building with more than one floor, so you can walk up to every floor: two
    lanes side by side against an outside wall near the front door, one flight up each way, a landing at each floor,
    and holes in the floors above the flights. Returns how many staircases were built."""
    layers = vol.shape[0]
    built = 0
    add_stairs.missed = {}
    near_doors = {}
    for z, x, b, _ in entrances:
        near_doors.setdefault(b, []).append((z, x))
    for b in np.unique(bmap[mask]):
        if b >= len(uses) or uses[b] in HALL_USES:
            continue
        cz, cx = np.nonzero((bmap == b) & mask)
        if len(cz) < 20:
            add_stairs.missed[int(b)] = "small"
            continue
        f0, st = int(floor0[cz[0], cx[0]]), max(int(storey[cz[0], cx[0]]), 3)
        h = int(height[cz, cx].max())
        tops = top[cz, cx]
        floors_under = lambda ks: len(storey_layers(f0, h, st, int(tops[ks].min())))    # floors a stair reaches
        if len(storey_layers(f0, h, st, int(tops.max()))) < 2:
            add_stairs.missed[int(b)] = f"one floor under the roof {f0} {h} {st} {int(tops.max())} {int(tops.min())}"
            continue
        d, across_z, wall_dir, width, along = wall_frame(cz, cx)
        here = {(int(z), int(x)): i for i, (z, x) in enumerate(zip(cz, cx))}
        add_stairs.missed[int(b)] = "no straight wall"     # until a place is found
        doors = near_doors.get(b, [])
        clear = keep_clear.get(b, set())
        length = st + 3
        best = None
        for i in np.flatnonzero((d == 1) & (width >= 5)):
            f = int(wall_dir[i])
            inward = OPPOSITE[f]
            run = (0, 1) if across_z[i] else (1, 0)           # along the wall: east or south
            cells = []
            for pos in range(length):
                lane = []
                for lane_d in (0, 1):
                    z = int(cz[i]) + run[0] * pos + STEP[inward][0] * lane_d
                    x = int(cx[i]) + run[1] * pos + STEP[inward][1] * lane_d
                    k = here.get((z, x))
                    if k is None or d[k] != 1 + lane_d or wall_dir[k] != f or (z, x) in clear:
                        break
                    lane.append(k)
                if len(lane) < 2:
                    break
                cells.append(lane)
            if len(cells) < length:
                continue
            ks = [k for lane in cells for k in lane]
            if any(abs(int(cz[k]) - dz_) <= 3 and abs(int(cx[k]) - dx_) <= 3 for k in ks for dz_, dx_ in doors):
                continue
            score = min((abs(int(cz[i]) - dz_) + abs(int(cx[i]) - dx_) for dz_, dx_ in doors), default=0) \
                - 1000 * floors_under(ks)                # in the tallest part, then nearest the front door
            if best is None or score < best[0]:
                best = (score, cells, run, inward)
        free_standing = best is None
        if free_standing:
            # no straight run of wall (walls at an angle to the block grid come out stepped): a stairwell standing
            # free inside, two lanes side by side, wherever it fits nearest the front door
            best = free_stairwell(cz, cx, here, clear, doors, length, floors_under)
        if best is None:
            continue
        _, cells, run, inward = best
        storeys = storey_layers(f0, h, st, int(tops[[k for lane in cells for k in lane]].min()))
        if len(storeys) < 2:
            add_stairs.missed[int(b)] = "no place under the high part of the roof"
            continue
        del add_stairs.missed[int(b)]
        rise_dir = {(0, 1): EAST, (1, 0): SOUTH}[run]
        down_dir = OPPOSITE[rise_dir]
        steps = ROOF_STAIRS[block("stone_bricks")] if uses[b] not in ("home", "dorm") else ROOF_STAIRS[block("spruce_planks")]
        floor_blk = int(vol[storeys[1][0] - 1, cz[cells[0][0]], cx[cells[0][0]]])
        zs = np.array([[cz[k] for k in lane] for lane in cells])      # [position, lane]
        xs = np.array([[cx[k] for k in lane] for lane in cells])
        # in front of the stairs, up to the corridor: a clear stair hall on every floor (no partitions, furniture or
        # wall to the corridor), so each landing joins the rest of its floor; round a free-standing stairwell, a
        # clear ring one block wide
        ks = np.array([k for lane in cells for k in lane])
        if free_standing:
            ring = {(int(cz[k]) + dz, int(cx[k]) + dx) for k in ks for dz in (-1, 0, 1) for dx in (-1, 0, 1)}
            open_up = np.array(sorted({here[c] for c in ring if c in here} - set(ks.tolist())), dtype=int)
        else:
            span = np.unique(along[ks])
            plan = ROOM_PLANS.get(uses[b])
            reach = np.minimum(plan[1], (width - 3) // 2) + 1 if plan else np.full(len(cz), 4)
            open_up = np.flatnonzero(np.isin(along, span) & (d >= 3) & (d <= np.maximum(reach, 3)))
        for lo, hi in storeys:
            for y in range(lo, hi):
                v = vol[y, cz[open_up], cx[open_up]]
                vol[y, cz[open_up], cx[open_up]] = np.where(v == CEILING_LAMP, v, AIR)
        # clear the whole stairwell, every floor, then lay the floors again and the flights
        y0, y1 = storeys[0][0], storeys[-1][1]
        vol[y0:y1, zs, xs] = AIR
        for lo, _ in storeys[1:]:
            vol[lo - 1, zs, xs] = floor_blk
        pos, sign = 0, 1
        for j in range(len(storeys) - 1):
            lane = j % 2
            rise = storeys[j + 1][0] - storeys[j][0]
            facing = rise_dir if sign > 0 else down_dir
            for i in range(1, rise + 1):
                p_ = pos + sign * i
                if not 0 <= p_ < length:
                    break
                y = storeys[j][0] + i - 1
                vol[y, zs[p_, lane], xs[p_, lane]] = steps[facing]
                for above in range(y + 1, min(y + 3, layers)):     # head room over every step but the last
                    if i < rise:
                        vol[above, zs[p_, lane], xs[p_, lane]] = AIR
            pos, sign = pos + sign * (rise + 1), -sign
        # every floor joined to its stairs: open a way into any part of it that cannot be reached from them
        landing = np.zeros(len(cz), bool)
        landing[open_up] = True
        landing[ks] = True
        for j, (lo, hi) in enumerate(storeys):
            seeds = landing.copy()
            if j == 0:
                for dz_, dx_ in doors:
                    seeds |= (np.abs(cz - dz_) <= 2) & (np.abs(cx - dx_) <= 2)
            join_pockets(vol, cz, cx, lo, seeds)
        built += 1
    return built


def measured_roofs(bmap, g, floor0, rel):
    """The layer of the top block of the roof in every building cell the laser scan measured (0 elsewhere).
    rel: metres from the ground to the roof, NaN where the scan has nothing. A building counts as measured when most
    of it has a roof at least 2 m up (a building put up after the scan has none). Gaps and cells where the outline on
    the map overhangs the real wall take the nearest measured roof; nothing goes below the building's lowest tenth."""
    size = bmap.shape[0]
    out = np.zeros((size, size), dtype=np.int32)
    good = np.isfinite(rel) & (rel >= 2.0) & (bmap > 0)
    n_all = np.bincount(bmap.ravel(), minlength=int(bmap.max()) + 1)
    n_good = np.bincount(bmap[good], minlength=len(n_all))
    ok = (n_good >= 4) & (n_good >= 0.5 * n_all)
    ok[0] = False
    use = ok[bmap]
    if not use.any():
        return out
    value = np.where(good & use, rel, np.nan)
    value = clean_roof_cells(value, bmap, use)
    for _ in range(8):                  # fill gaps from the measured cells beside them (side by side, not corners:
        hole = use & np.isnan(value)    # an outline cell outside a gable takes the gable's height, not less)
        if not hole.any():
            break
        total, count = np.zeros((size, size)), np.zeros((size, size))
        for dz, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            nb_v = np.roll(np.roll(value, dz, 0), dx, 1)
            nb_b = np.roll(np.roll(bmap, dz, 0), dx, 1)
            same = (nb_b == bmap) & np.isfinite(nb_v)
            total += np.where(same, nb_v, 0)
            count += same
        value = np.where(hole & (count > 0), total / np.maximum(count, 1), value)
    ids = bmap[use]
    vals = value[use]
    low = np.zeros(len(n_all))
    for b in np.flatnonzero(ok):                            # the building's lowest tenth
        v = vals[ids == b]
        v = v[np.isfinite(v)]
        low[b] = np.percentile(v, 10) if len(v) else 2.0
    value = np.where(use, np.fmax(value, low[bmap]), np.nan)
    value = np.where(np.isnan(value), low[bmap], value)
    out[use] = np.maximum(g[use] + np.round(value[use]).astype(np.int32), floor0[use] + 2)
    return out


def draw_measured_roofs(vol, bmap, measured, mtop, eave_layer, wall, roof, sloped, stairs_of):
    """Roofs as the laser scan saw them. Every cell gets its roof block at its measured height, and what is under it
    down to its lower neighbours is filled in: roof blocks where the roof steps gently (a slope), the wall block where
    it drops 3 or more (the sides of towers, gables, dormers, chimneys). On sloped roofs a block whose one higher
    neighbour is opposite a lower one becomes a stair, so the roof slopes as it does in the game."""
    size = bmap.shape[0]
    zz, xx = np.indices((size, size))
    low_nb = np.full((size, size), 1 << 20, dtype=np.int64)
    nbs = []
    for dz, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):        # north, south, west, east
        zs, xs = np.clip(zz + dz, 0, size - 1), np.clip(xx + dx, 0, size - 1)
        inside = (bmap[zs, xs] == bmap) & measured[zs, xs] & ((zs != zz) | (xs != xx))
        t = np.where(inside, mtop[zs, xs], -1)              # outside the building counts as low
        nbs.append(t)
        low_nb = np.minimum(low_nb, np.where(inside, t, eave_layer - 1))
    # under each roof block: down to one above the lowest neighbour, but not below the eaves (the walls are there)
    start = np.maximum(np.maximum(low_nb + 1, eave_layer), 0)
    drop = mtop - low_nb
    for y in range(int(start[measured].min()), int(mtop.max()) + 1):
        m = measured & (y >= start) & (y < mtop)
        if not m.any():
            continue
        steep = drop >= 3
        vol[y][m & steep] = wall[m & steep]
        vol[y][m & ~steep] = roof[m & ~steep]
    vol[mtop[measured], zz[measured], xx[measured]] = roof[measured]
    # stairs: the foot of each step of a slope (one neighbour one block higher, the opposite one not higher), facing
    # up the slope; not against towers or walls (a neighbour more than one block higher)
    higher = [nb > mtop for nb in nbs]
    n_higher = sum(h.astype(np.int8) for h in higher)
    opposite = (1, 0, 3, 2)
    for d in range(4):
        m = measured & sloped & (n_higher == 1) & (nbs[d] - mtop == 1) & (nbs[opposite[d]] <= mtop) \
            & (mtop - nbs[opposite[d]] <= 2)
        vol[mtop[m], zz[m], xx[m]] = stairs_of[roof[m], d]


def plant_measured_tree(vol, z, x, height, crown, rng, base, conifer=False):
    """A tree as the laser scan measured it: `height` blocks from the ground to the top of the crown, a crown `crown`
    blocks across from the trunk. Broadleaf trees get a rounded crown on a trunk about a third of the height; conifers a
    pointed one that starts low."""
    layers, size = vol.shape[0], vol.shape[1]
    height = int(max(3, min(height, layers - base - 2)))
    crown = max(1.0, min(float(crown), 9.0))
    if conifer:
        log, leaves = TREES[2]
        bottom = base + max(1, int(round(height * 0.2)))
    else:
        log, leaves = TREES[1] if rng.random() < 0.2 else TREES[0]
        bottom = base + max(2, int(round(height * (0.38 if height > 6 else 0.3))))
    top = base + height - 1
    mid = (bottom + top) / 2
    half_h = max((top - bottom) / 2, 0.5)
    r = int(math.ceil(crown))
    for y in range(bottom, top + 1):
        t = (y - mid) / half_h
        if conifer:
            rad = crown * (top + 0.5 - y) / max(top + 0.5 - bottom, 1)
        else:
            rad = crown * math.sqrt(max(0.0, 1 - min(1.0, t * t) * 0.85))
        z0, z1, x0, x1 = max(z - r, 0), min(z + r + 1, size), max(x - r, 0), min(x + r + 1, size)
        dz, dx = np.ogrid[z0 - z:z1 - z, x0 - x:x1 - x]
        dist = np.sqrt(dz * dz + dx * dx)
        edge = rng.random(dist.shape) < 0.45                  # a ragged outline, not a perfect ball
        leaf = (dist <= rad - 0.5) | ((dist <= rad + 0.3) & edge) | (dist <= 1.0)   # always round the trunk
        layer = vol[y, z0:z1, x0:x1]
        layer[leaf & (layer == AIR)] = leaves
    vol[base:top - 1, z, x] = log                          # the trunk stops inside the crown, never pokes out
    vol[top, z, x] = leaves


def build_volume(ground, bmap, specs, trees, seed=0, terrain=None, details=None, deep_water=True, lidar=None):
    """Turn the 2D maps into a 3D array of block numbers: vol[layer, z, x].
    terrain: None for flat ground, or (layer of the top block of every column, layer of the sea surface).
    details: build_map.details (fences, walls, hedges, rails and benches), or None to leave them out.
    lidar: load_lidar's result, or None. With it, buildings get their measured roofs (every 1 m of the roof at its
    real height, towers and chimneys included) and the trees are the measured ones, at their real height and width."""
    details = details or {}
    size = ground.shape[0]
    rng = np.random.default_rng(seed)
    spec = np.array(specs, dtype=np.int32)

    def per_pixel(column, dtype=np.int32):
        return spec[:, column][bmap].astype(dtype)

    height, wall, roof = per_pixel(0), per_pixel(1, np.uint8), per_pixel(2, np.uint8)
    pitched, windows, storey = per_pixel(3, bool), per_pixel(4, bool), per_pixel(5)
    glass, trim_block, spacing = per_pixel(6, np.uint8), per_pixel(7, np.uint8), per_pixel(8)
    shop = per_pixel(10, bool)
    is_open = per_pixel(11, bool)                      # a roof on pillars: shelters, carports, covered walkways
    has_building = bmap > 0
    if terrain is None:
        g, sea_level = np.full((size, size), GROUND, dtype=np.int32), GROUND
    else:
        g, sea_level = terrain[0].astype(np.int32).copy(), int(terrain[1])
    zz, xx = np.indices((size, size))

    # water: the sea at sea level, lakes and rivers at the lowest nearby bank. They get deeper away from the shore:
    # the sea down to its real depth where the elevation data has it (a gentle shelf near the coast, at most 32
    # blocks), lakes and rivers up to 8 blocks. In a new world everything is lifted to make room below.
    sea = (ground == S_SEA) & ~has_building
    water = (np.isin(ground, (S_WATER, S_SEA))) & ~has_building
    level = np.where(water, local_min(np.where(water, g, 1 << 20), 3), g)
    level = np.minimum(level, g)
    level[sea] = sea_level
    if deep_water and water.any():
        shore = distance_from(~water, 96)
        depth = np.where(sea, np.minimum(2 + shore * 0.35, 32), np.clip(1 + shore * 0.5, 2, 8))
        if terrain is not None and len(terrain) > 2:                 # the sea floor from the elevation data
            real = np.maximum(np.asarray(terrain[2], dtype=float), 2)
            depth = np.where(sea, np.minimum(depth, real) if sea.any() and real[sea].max() > 2 else depth, depth)
        depth = np.where(water, np.round(depth), 0).astype(np.int32)
        lift = max(0, int((depth - (level - 1))[water].max()))
        g, level, sea_level = g + lift, level + lift, sea_level + lift
        bed = np.where(water, level - depth, level - 2)
    else:
        bed = np.where(sea, np.minimum(g, sea_level - 2), level - 2)
    bed = np.clip(bed, 1, None)
    level[water] = np.maximum(level[water], bed[water] + 1)
    # banks: land right next to water must be at least as high as the water, or the water would spill out
    water_level = np.where(water, level, -1)
    beside = np.max(np.stack(neighbours(water_level)), axis=0)
    bank = ~water & ~has_building & (beside > g)
    g[bank] = beside[bank]

    # buildings stand on one level each
    base = building_bases(bmap, g, len(specs))[bmap]          # layer of the floor block
    floor0 = base + 1                                          # first layer inside
    top_of_ground = np.where(has_building, base, np.where(water, bed, g))

    # measured roofs: the layer of the top block of the roof in every cell of a building the laser scan saw
    mtop = measured_roofs(bmap, g, floor0, lidar["roof"]) if lidar is not None else np.zeros((size, size), np.int32)
    measured = mtop > 0
    eave = np.zeros((size, size), dtype=np.int32)
    sloped = np.zeros((size, size), dtype=bool)
    if measured.any():
        # the walls (floors, windows, rooms) go up to the eaves: the lower quarter of the roof's edge
        edge_cells = measured & ~interior_of(bmap)
        ids = bmap[edge_cells]
        rise = (mtop - floor0)[edge_cells]
        order = np.lexsort((rise, ids))
        ids, rise = ids[order], rise[order]
        starts = np.flatnonzero(np.r_[True, ids[1:] != ids[:-1]])
        ends = np.r_[starts[1:], len(ids)]
        per_building = np.zeros(len(specs), dtype=np.int32)
        per_building[ids[starts]] = rise[starts + (ends - starts - 1) // 4]
        eave = np.where(measured, np.maximum(per_building[bmap], 3), 0)
        height = np.where(measured, eave, height)
        # a sloped roof: the roof rises well above the eaves somewhere, over a good part of the building
        spread = np.zeros(len(specs))
        hi = np.where(measured, mtop - floor0 - eave, 0)
        np.add.at(spread, bmap[measured], (hi[measured] >= 2).astype(float))
        count = np.bincount(bmap[measured], minlength=len(specs)).astype(float)
        sloped_b = spread / np.maximum(count, 1) > 0.3
        sloped = measured & sloped_b[bmap]
        stair_roofs = np.array(sorted(ROOF_STAIRS), dtype=np.uint8)
        needs = sloped & ~np.isin(roof, stair_roofs)
        roof = np.where(needs, DEEPSLATE_ROOF, roof).astype(np.uint8)       # slate grey unless the map says
        flat_m = measured & ~sloped & np.isin(roof, stair_roofs)
        roof = np.where(flat_m, FLAT_ROOFS[1], roof).astype(np.uint8)
        pitched = np.where(measured, sloped, pitched)

    tallest = int((floor0 + height)[has_building].max()) if has_building.any() else 0
    tallest = max(tallest, int(mtop.max()) + 1)
    top = max(int(g.max()) + 16, tallest + 16, int(level.max()) + 2, FLOOR + 27)
    layers = -(-(top + 1) // 16) * 16
    if layers * size * size > MAX_VOLUME_BYTES:
        sys.exit("This area is too big or too hilly for this version. Try a smaller --radius, or --flat.")
    vol = np.zeros((layers, size, size), dtype=np.uint8)

    # ground: stone, 3 blocks of dirt, then the surface; every surface class is a weighted mix of blocks
    noise = rng.random((size, size))
    patches = box_blur(rng.random((size, size)), 3).ravel()     # the same mixing, in patches several blocks across
    patches[np.argsort(patches)] = (np.arange(patches.size) + 0.5) / patches.size
    patches = patches.reshape(size, size)
    surface = np.zeros((size, size), dtype=np.uint8)
    for cls, mix in SURFACE_MIX.items():
        m = ground == cls
        if m.any():
            edges = np.cumsum([w for _, w in mix], dtype=float)
            choice = np.minimum(np.searchsorted(edges / edges[-1], (patches if cls in PATCHY else noise)[m],
                                                side="right"), len(mix) - 1)
            surface[m] = np.array([blk for blk, _ in mix], dtype=np.uint8)[choice]
    vol[0] = BEDROCK
    for y in range(1, int(top_of_ground.max()) + 1):
        layer = vol[y]
        below = y < top_of_ground
        layer[below & (y < top_of_ground - 3)] = STONE
        layer[below & (y >= top_of_ground - 3)] = DIRT
        layer[has_building & (y > g) & (y < base)] = STONE_BRICKS        # foundation on the downhill side
        layer[y == top_of_ground] = surface[y == top_of_ground]
    vol[base[has_building], zz[has_building], xx[has_building]] = SMOOTH_STONE
    deep_bed = water & (level - bed > 7)                         # sand near the shore, gravel further out
    vol[bed[water], zz[water], xx[water]] = np.where(deep_bed[water] & ((zz + xx * 3)[water] % 5 < 3), GRAVEL, SAND)
    for d in range(1, int((level - bed)[water].max()) + 1 if water.any() else 0):
        m = water & (bed + d <= level)
        vol[(bed + d)[m], zz[m], xx[m]] = WATER
    # seagrass and tall seagrass on the bed, and kelp reaching up to just under the surface; none in frozen seas
    # (as in the game), none on the outer ring, next to the void
    kind = CLIMATE.get("kind")
    water_life = {"seagrass": 0, "kelp": 0}
    if water.any() and kind != "polar":
        life = np.random.default_rng(seed + 101)
        r = life.random((size, size))
        depth = np.where(water, level - bed, 0)
        inner = np.zeros((size, size), dtype=bool)
        inner[3:-3, 3:-3] = True
        inner &= water
        grass_m = inner & (depth >= 2) & (r < 0.26)
        tall_m = inner & (depth >= 3) & (r >= 0.26) & (r < 0.33)
        # kelp forests grow in cool seas (North Sea, Pacific coasts); warm seas have seagrass meadows instead
        forest = {"temperate": 1, "continental": 1, "boreal": 0.8, "highland": 0.5, "mediterranean": 0.2,
                  "steppe": 0.2, "desert": 0.1}.get(kind, 0)
        kelp_m = inner & sea & (depth >= 4) & (r >= 0.33) & (r < 0.33 + 0.45 * forest * (patches > 0.6))
        vol[(bed + 1)[grass_m], zz[grass_m], xx[grass_m]] = SEAGRASS
        vol[(bed + 1)[tall_m], zz[tall_m], xx[tall_m]] = TALL_SEAGRASS[0]
        vol[(bed + 2)[tall_m], zz[tall_m], xx[tall_m]] = TALL_SEAGRASS[1]
        for z, x in zip(*np.nonzero(kelp_m)):
            top = int(bed[z, x]) + int(life.integers(max(2, int(depth[z, x]) // 3), int(depth[z, x])))   # below the surface
            vol[int(bed[z, x]) + 1:top, z, x] = KELP_PLANT
            vol[top, z, x] = KELP
        water_life = {"seagrass": int(grass_m.sum() + tall_m.sum()), "kelp": int(kelp_m.sum())}

    # the world around the built area is empty, so wall in the water on the outermost ring with invisible blocks
    edge = np.zeros((size, size), dtype=bool)
    edge[[0, -1], :] = True
    edge[:, [0, -1]] = True
    for y in range(1, int(level.max()) + 1 if water.any() else 0):
        m = edge & (vol[y] == WATER)
        vol[y][m] = BARRIER

    street = np.isin(ground, STREET_SURFACES) & ~has_building
    near_street = window_fraction(street, 2) > 0

    # walls: floors of `storey` blocks, each with a trim band and two rows of windows.
    # Facing a street, the ground floor of bigger buildings is a shop front: tall glass between pillars.
    inner = interior_of(bmap)
    walls = has_building & ~inner
    facing, open_sides = inward_facing(bmap)
    corner = walls & (open_sides != 1)
    p = np.pad(bmap, 1)
    faces_north_south = (p[:-2, 1:-1] != bmap) | (p[2:, 1:-1] != bmap)
    along = np.where(faces_north_south, xx, zz)        # count along the wall, whichever way it runs
    window_cols = walls & windows & (along % spacing < 2)
    shop_front = walls & shop & near_street & ~corner & (along % 4 != 0)
    floor_block = np.where(pitched, HOUSE_FLOOR, SMOOTH_STONE).astype(np.uint8)
    pillar = corner | (along % 4 == 0)
    uses = list(getattr(build_map, "building_uses", []) or [])
    uses += ["office"] * (len(specs) - len(uses))
    hall_px = np.isin(bmap, [i for i, u in enumerate(uses) if u in HALL_USES]) & has_building
    special_px = np.isin(bmap, [i for i, u in enumerate(uses) if u not in ("office", "home", "shop")]) & has_building
    for k in range(int(height.max())):
        m = walls & (height > k) & (~is_open | pillar)
        if not m.any():
            break
        value = wall.copy()
        row = k % storey
        trim = row == 0
        value[trim] = trim_block[trim]
        win = window_cols & (height > k + 1) & ((row == 1) | (row == 2))
        value[win] = glass[win]
        if 1 <= k <= 3:
            sf = shop_front & (height > 5)
            value[sf] = glass[sf]
        vol[(floor0 + k)[m], zz[m], xx[m]] = value[m]
        if k:                                          # a floor inside, so you see storeys through the windows
            f = inner & (height > k + 1) & (row == 0) & ~hall_px
            vol[(floor0 + k)[f], zz[f], xx[f]] = floor_block[f]
    # under a tall measured roof (above the eaves the walls stop at), more floors where there is head room: the
    # top floors of a building with a steep roof live in its roof, as in the old halls of F&M
    inside_h = height.copy()
    if measured.any():
        room_above = np.where(measured, mtop - floor0, 0)
        for k in range(1, int(room_above.max())):
            f = inner & measured & (k >= height - 1) & (k % storey == 0) & (room_above > k + 3) & ~hall_px
            f &= vol[np.clip(floor0 + k, 0, vol.shape[0] - 1), zz, xx] == AIR
            vol[(floor0 + k)[f], zz[f], xx[f]] = floor_block[f]
        inside_h = np.where(measured & inner, np.maximum(height, room_above), height)

    # doors: one per building, in a straight stretch of wall, on the street side when there is one
    door_ok = walls & ~corner & (height >= 4) & (facing >= 0) & ~is_open
    cells = rng.permutation(np.flatnonzero(door_ok))
    cz_, cx_ = np.divmod(cells, size)
    # facing points into the building, so the outside cell is one step the other way (north is -z)
    out = np.array([(1, 0), (-1, 0), (0, 1), (0, -1)])[facing.ravel()[cells]]      # for NORTH, SOUTH, WEST, EAST
    oz, ox = np.clip(cz_ + out[:, 0], 0, size - 1), np.clip(cx_ + out[:, 1], 0, size - 1)
    level_access = (g[oz, ox] == base[cz_, cx_]) & ~has_building[oz, ox]
    rank = (~level_access).astype(int) * 2 + (~near_street.ravel()[cells]).astype(int)
    cells = cells[np.argsort(rank, kind="stable")]      # level with the outside first, then the street side
    # one door per 45 blocks of wall (at least one, at most four), at least 20 blocks apart
    perimeter = np.bincount(bmap[walls], minlength=len(specs))
    want = np.clip(perimeter // 45, 1, 4)
    chosen, placed_at = [], {}
    for cell in cells:
        building = int(bmap.ravel()[cell])
        spots = placed_at.setdefault(building, [])
        if len(spots) >= want[building]:
            continue
        z, x = divmod(int(cell), size)
        if all(abs(z - pz) + abs(x - px) >= 20 for pz, px in spots):
            spots.append((z, x))
            chosen.append((cell, building))
    doors_placed, doorways = 0, []
    door_list = list(chosen)                           # (cell, building) of every door, for the name signs later
    for cell, building in chosen:
        z, x = divmod(int(cell), size)
        f = FACINGS[int(facing[z, x])]
        kind = DOORS[int(spec[building, 9])]
        y = int(floor0[z, x])
        vol[y, z, x], vol[y + 1, z, x] = kind[(f, "lower")], kind[(f, "upper")]
        dz, dx = ((1, 0), (-1, 0), (0, 1), (0, -1))[int(facing[z, x])]      # the cell just outside the door
        oz, ox = min(max(z + dz, 0), size - 1), min(max(x + dx, 0), size - 1)
        if not has_building[oz, ox]:
            doorways.append((y, oz, ox))
        doors_placed += 1

    # inside: rooms off corridors (plan_rooms) in offices, homes, shops, residence halls, classroom and lab
    # buildings, with real doors, beds, desks and lamps; the other uses get the inside of what they are
    # (furnish_by_use); then a staircase up through every building with more than one floor (add_stairs)
    roomy = inner & windows & ~is_open
    entrances = []
    for cell, building in chosen:
        z, x = divmod(int(cell), size)
        entrances.append((z, x, building, int(facing[z, x])))
    roof_layer = np.where(measured, mtop, floor0 + height)
    furnished, keep_clear = plan_rooms(vol, bmap, uses, roomy, floor0, inside_h, storey, roof_layer, entrances, rng)
    near_door = np.zeros((size, size), dtype=bool)
    for cell, _ in chosen:
        z, x = divmod(int(cell), size)
        near_door[max(0, z - 2):z + 3, max(0, x - 2):x + 3] = True
    special_inside = roomy & special_px & ~near_door
    furnished += furnish_by_use(vol, bmap, uses, special_inside, floor0, inside_h, storey, roof_layer, rng)
    staircases = add_stairs(vol, bmap, uses, roomy, floor0, inside_h, storey, roof_layer, entrances, keep_clear)
    inner_walls = 0

    # flat roofs: one layer, a raised edge on the taller ones, and rooftop units (a stair head, air conditioning)
    m = has_building & ~pitched & ~measured
    vol[(floor0 + height)[m], zz[m], xx[m]] = roof[m]
    flat = has_building & ~pitched & (height >= 8) & ~measured
    m = walls & flat
    vol[(floor0 + height + 1)[m], zz[m], xx[m]] = wall[m]
    deep = np.where(flat, bmap, 0)
    for _ in range(4):
        deep = np.where(interior_of(deep), deep, 0)
    cells = rng.permutation(np.flatnonzero(deep))
    owner = deep.ravel()[cells]
    order = np.argsort(owner, kind="stable")
    cells, owner = cells[order], owner[order]
    starts = np.flatnonzero(np.r_[True, owner[1:] != owner[:-1]]) if len(owner) else []
    for start, end in zip(starts, list(starts[1:]) + [len(cells)]):
        if end - start < 40:
            continue
        want, placed = min(6, 1 + (end - start) // 150), []
        for cell in cells[start:min(end, start + 300)]:
            z, x = divmod(int(cell), size)
            if any(abs(z - pz) < 6 and abs(x - px) < 6 for pz, px in placed):
                continue
            y = int(floor0[z, x] + height[z, x]) + 1
            if not placed:                              # stair head: a small box in the wall material
                vol[y:y + 3, z - 1:z + 2, x - 1:x + 2] = wall[z, x]
                vol[y + 3, z - 1:z + 2, x - 1:x + 2] = roof[z, x]
            else:                                       # air conditioning unit
                vol[y, z:z + 2, x:x + 2] = ROOF_UNITS[len(placed) % len(ROOF_UNITS)]
            placed.append((z, x))
            if len(placed) >= want:
                break

    # pitched roofs: layers that step inwards, with stairs on every edge so the roof slopes, and eaves that
    # stick out one block past the walls
    stairs_of = np.repeat(np.arange(256, dtype=np.uint8)[:, None], 4, axis=1)
    for full, variants in ROOF_STAIRS.items():
        stairs_of[full] = variants
    pb = np.where(pitched & ~measured, bmap, 0)
    building_base = building_bases(bmap, g, len(specs))
    for direction, nb in enumerate(neighbours(pb)):
        m = (nb > 0) & ~has_building
        z_i, x_i = np.nonzero(m)
        owner = nb[z_i, x_i]
        y = building_base[owner] + spec[owner, 0]
        free = vol[y, z_i, x_i] == AIR
        vol[y[free], z_i[free], x_i[free]] = stairs_of[spec[owner, 2], direction][free]
    cur = pb
    for step in range(10):
        mask = cur > 0
        if not mask.any():
            break
        nxt = np.where(interior_of(cur), cur, 0)
        vol[(floor0 + height + step)[mask], zz[mask], xx[mask]] = roof[mask]
        edge_facing, _ = inward_facing(cur)
        ring = mask & (nxt == 0) & (edge_facing >= 0)
        vol[(floor0 + height + step)[ring], zz[ring], xx[ring]] = stairs_of[roof[ring], edge_facing[ring]]
        cur = nxt

    if measured.any():
        draw_measured_roofs(vol, bmap, measured, mtop, floor0 + eave, wall, roof, sloped, stairs_of)

    # railway tracks: each rail turns or climbs to join the rails beside it, as the game would lay them
    rails_laid = 0
    track = details.get("rails")
    if track is not None:
        rails_laid = lay_rails(vol, track & ~has_building & ~water, g)

    # fences, garden walls and hedges from the map; left open where a road or path goes through
    barrier_cells = 0
    if details.get("barriers") is not None:
        kind_map, tall = details["barriers"]
        ok = (kind_map > 0) & ~has_building & ~water & ~np.isin(ground, (S_ROAD, S_LINE, S_PARKING, S_FOOT, S_PATH,
                                                                          S_SIDEWALK))
        kind_map = np.where(ok, kind_map, 0)
        for kind_ in (B_FENCE, B_WALL, B_BRICK, B_HEDGE):
            here = kind_map == kind_
            if not here.any():
                continue
            # fences and walls join each other and the walls of buildings
            friends = here | (has_building if kind_ != B_HEDGE else False)
            bits = sum((nb > 0).astype(np.int32) << i for i, nb in enumerate(neighbours(friends.astype(np.int32))))
            z_i, x_i = np.nonzero(here)
            base_y = g[z_i, x_i] + 1
            h = tall[z_i, x_i].astype(np.int32)
            if kind_ == B_FENCE:
                h = np.minimum(h, 2)
            for k in range(int(h.max())):
                m = (h > k) & (vol[base_y + k, z_i, x_i] == AIR)
                zk, xk, yk = z_i[m], x_i[m], base_y[m] + k
                if kind_ == B_HEDGE:
                    vol[yk, zk, xk] = HEDGE
                elif kind_ == B_FENCE:
                    vol[yk, zk, xk] = np.array(FENCES, dtype=np.uint8)[bits[zk, xk]]
                else:
                    top_row = h[m] == k + 1                # the top of a wall is a wall block; below it, stone bricks
                    caps = np.array(BRICK_WALLS if kind_ == B_BRICK else GARDEN_WALLS, dtype=np.uint8)[bits[zk, xk]]
                    vol[yk, zk, xk] = np.where(top_row, caps, BRICK if kind_ == B_BRICK else STONE_BRICKS)
                barrier_cells += int(m.sum()) if k == 0 else 0

    # street lamps along the sidewalks of the bigger roads, roughly every 20 blocks
    taken = np.zeros((size, size), dtype=bool)
    road = np.isin(ground, (S_ROAD, S_LINE))
    lamp_ok = (ground == S_SIDEWALK) & (window_fraction(road, 1) > 0) & ~(window_fraction(has_building, 1) > 0)
    blocked = np.zeros((size, size), dtype=bool)
    lamps = real_lamps = 0
    # the street lamps the map has, where it has them; the made-up ones below keep 16 blocks away from these
    keep_lamp = np.isin(ground, (S_ROAD, S_LINE, S_PARKING)) | has_building | water
    for kind_, fx, fz, _ in details.get("furniture", []):
        spot = free_spot(keep_lamp, fx, fz, size) if kind_ == "lamp" else None
        if spot is None:
            continue
        x, z = spot
        y = int(g[z, x]) + 1
        if y + 6 >= vol.shape[0] or (vol[y:y + 5, z, x] != AIR).any():
            continue
        vol[y:y + 4, z, x] = LAMP_POST
        vol[y + 4, z, x] = LANTERN
        blocked[max(0, z - 16):z + 17, max(0, x - 16):x + 17] = True
        taken[z, x] = True
        lamps += 1
        real_lamps += 1
    for cell in rng.permutation(np.flatnonzero(lamp_ok)):
        z, x = divmod(int(cell), size)
        y = int(g[z, x]) + 1
        if blocked[z, x] or vol[y, z, x] != AIR:
            continue
        vol[y:y + 4, z, x] = LAMP_POST
        vol[y + 4, z, x] = LANTERN
        blocked[max(0, z - 16):z + 17, max(0, x - 16):x + 17] = True
        taken[z, x] = True
        lamps += 1

    # streets with no sidewalk (common outside town centres): tall lamps on the verge, roughly every 30 blocks
    verge = np.isin(ground, (S_GRASS, S_PARK, S_LAWN, S_INDUSTRIAL, S_FARM)) & ~has_building & ~water
    verge_ok = verge & (window_fraction(road, 1) > 0) & ~(window_fraction(ground == S_SIDEWALK, 3) > 0) & \
        ~(window_fraction(has_building, 2) > 0)
    for cell in rng.permutation(np.flatnonzero(verge_ok)):
        z, x = divmod(int(cell), size)
        y = int(g[z, x]) + 1
        if blocked[z, x] or (vol[y:y + 6, z, x] != AIR).any():
            continue
        vol[y:y + 5, z, x] = LAMP_POST
        vol[y + 5, z, x] = LANTERN
        blocked[max(0, z - 14):z + 15, max(0, x - 14):x + 15] = True
        taken[z, x] = True
        lamps += 1

    # lamps along footpaths and park paths too, on the grass beside the path, roughly every 16 blocks
    path = np.isin(ground, (S_FOOT, S_PATH))
    lawn = np.isin(ground, (S_GRASS, S_PARK, S_LAWN)) & ~has_building
    path_lamp_ok = lawn & (window_fraction(path, 1) > 0) & ~(window_fraction(has_building, 2) > 0) & ~water
    for cell in rng.permutation(np.flatnonzero(path_lamp_ok)):
        z, x = divmod(int(cell), size)
        y = int(g[z, x]) + 1
        if blocked[z, x] or vol[y, z, x] != AIR:
            continue
        vol[y:y + 3, z, x] = LAMP_POST
        vol[y + 3, z, x] = LANTERN
        blocked[max(0, z - 15):z + 16, max(0, x - 15):x + 16] = True
        taken[z, x] = True
        lamps += 1

    # half slabs where a road or sidewalk climbs one block
    paving = np.isin(ground, (S_SIDEWALK, S_FOOT, S_PLAZA, S_QUAY, S_PAVED)) & ~has_building
    driving = np.isin(ground, (S_ROAD, S_LINE, S_PARKING)) & ~has_building
    walkway = (paving | driving) & ~water
    slabs = 0
    for nb_g, nb_walk in zip(neighbours(np.where(walkway, g, -10)), neighbours(walkway.astype(np.int32))):
        m = walkway & (nb_walk > 0) & (nb_g == g + 1)
        m &= vol[np.minimum(g + 1, layers - 1), zz, xx] == AIR
        z_i, x_i = np.nonzero(m)
        vol[g[m] + 1, z_i, x_i] = np.where(driving[m], ROAD_SLAB, PAVING_SLAB)
        slabs += len(z_i)

    # car parks: white lines between bays three blocks wide, in rows five deep with a driving lane between, and
    # parked cars in about half of the bays
    park = (ground == S_PARKING) & ~has_building & ~water
    cars = 0
    if park.any():
        band = zz % 13
        in_bay = (band < 5) | (band >= 8)
        line_cells = park & in_bay & (xx % 3 == 0)
        vol[g[line_cells], zz[line_cells], xx[line_cells]] = WHITE_CONCRETE
        for z, x in zip(*np.nonzero(park & (xx % 3 == 1) & ((band == 0) | (band == 8)))):
            if z + 4 > size or x + 2 > size or rng.random() > 0.5:
                continue
            foot = (slice(z, z + 4), slice(x, x + 2))
            y = int(g[z, x]) + 1
            if not park[foot].all() or (g[foot] != g[z, x]).any() or (vol[y:y + 2][(slice(None),) + foot] != AIR).any():
                continue
            colour = CAR_COLOURS[int(rng.integers(0, len(CAR_COLOURS)))]
            vol[y, z:z + 4, x:x + 2] = colour                 # the body
            vol[y + 1, z + 1:z + 3, x:x + 2] = GLASS           # the cabin
            cars += 1

    # traffic lights from the map: a pole beside the road with a signal head on top
    signals = 0
    road_or_building = np.isin(ground, (S_ROAD, S_LINE, S_PARKING)) | has_building | water
    for sx, sz in details.get("signals", []):
        best = None
        for r in range(1, 9):
            for dz in range(-r, r + 1):
                for dx in (-r, r) if abs(dz) != r else range(-r, r + 1):
                    z, x = sz + dz, sx + dx
                    if 0 <= z < size and 0 <= x < size and not road_or_building[z, x] and road_or_building[
                            max(0, z - 1):z + 2, max(0, x - 1):x + 2].any():
                        best = (z, x)
                        break
                if best:
                    break
            if best:
                break
        if best:
            z, x = best
            y = int(g[z, x]) + 1
            if (vol[y:y + 6, z, x] == AIR).all():
                vol[y:y + 3, z, x] = LAMP_POST
                vol[y + 3:y + 6, z, x] = SIGNAL_LIGHTS
                signals += 1

    # benches from the map: a wooden stair block with its back to the nearest building, or away from the road
    benches = 0
    seat = ROOF_STAIRS[block("spruce_planks")]
    road_near = np.isin(ground, (S_ROAD, S_LINE))
    for x, z in details.get("benches", []):
        if not (2 <= x < size - 2 and 2 <= z < size - 2) or has_building[z, x] or water[z, x]:
            continue
        y = int(g[z, x]) + 1
        if vol[y, z, x] != AIR or vol[y + 1, z, x] != AIR:
            continue
        back = None
        for reach in range(1, 6):                   # the back goes against a building, else away from the road
            for d, (dz, dx) in enumerate(((-1, 0), (1, 0), (0, -1), (0, 1))):
                zz2, xx2 = min(max(z + dz * reach, 0), size - 1), min(max(x + dx * reach, 0), size - 1)
                if back is None and has_building[zz2, xx2]:
                    back = d
                elif back is None and road_near[zz2, xx2]:
                    back = (SOUTH, NORTH, EAST, WEST)[d]
            if back is not None:
                break
        vol[y, z, x] = seat[NORTH if back is None else back]
        benches += 1

    # trees: the ones on the map (street trees too, with a small tree pit), plus random ones in forests,
    # parks, lawns and yards, and low bushes in parks and lawns
    green = (S_GRASS, S_FOREST, S_PARK, S_LAWN)
    paved = (S_SIDEWALK, S_PLAZA, S_PAVED, S_FOOT, S_QUAY)
    spots = [(int(x), int(z), True) for x, z in trees]
    for surface_class, per_tree in ((S_FOREST, 30), (S_PARK, 220), (S_LAWN, 300), (S_GRASS, 450)):
        cells = np.flatnonzero(ground == surface_class)
        if len(cells) >= per_tree:
            chosen = rng.choice(cells, size=len(cells) // per_tree, replace=False)
            spots += [(int(c % size), int(c // size), False) for c in chosen]
    planted = 0
    if lidar is not None:
        # the laser scan found every tree: plant those, where they stand, as tall and as wide as they are; none made up.
        # The trunk goes under the top of the crown, or on the nearest spot that is not a road, a building or water
        spots = []
        no_trunk = has_building | water | np.isin(ground, (S_ROAD, S_LINE, S_PARKING)) | (details.get("rails") if details.get("rails") is not None else False)
        for tx, tz, th, tr in lidar["trees"]:
            x, z = int(tx), int(tz)
            best = None
            for d in range(0, 4):
                for dz in range(-d, d + 1):
                    for dx in range(-d, d + 1):
                        if max(abs(dz), abs(dx)) != d:
                            continue
                        zz_, xx_ = z + dz, x + dx
                        if 2 <= zz_ < size - 2 and 2 <= xx_ < size - 2 and not no_trunk[zz_, xx_] \
                                and not taken[zz_, xx_]:
                            best = (zz_, xx_)
                            break
                    if best:
                        break
                if best:
                    break
            if best is None:
                continue
            z, x = best
            y = int(g[z, x]) + 1
            if vol[y, z, x] != AIR:
                continue
            taken[z, x] = True
            if ground[z, x] in paved:
                vol[y - 1, z, x] = COARSE_DIRT
            # conifers: only clearly narrow, pointed crowns (the 2019 scan was flown with the leaves off, which makes
            # some broadleaf crowns look narrower than they are in summer)
            plant_measured_tree(vol, z, x, round(th), tr, rng, y, conifer=tr < 0.13 * th and th >= 8)
            planted += 1
        spots = []
    for x, z, mapped in spots:
        if not (4 <= x < size - 4 and 4 <= z < size - 4):
            continue
        on_paving = mapped and ground[z, x] in paved
        if ground[z, x] not in green and not on_paving:
            continue
        reach = 2 if on_paving else 3
        y = int(g[z, x]) + 1
        if has_building[z - reach:z + reach + 1, x - reach:x + reach + 1].any() \
                or taken[z - 2:z + 3, x - 2:x + 3].any() or vol[y, z, x] != AIR:
            continue
        taken[z, x] = True
        planted += 1
        if on_paving:
            vol[y - 1, z, x] = COARSE_DIRT
        plant_tree(vol, z, x, rng, small=on_paving, base=y)
    cells = np.flatnonzero(np.isin(ground, (S_PARK, S_LAWN)) & ~has_building)
    if len(cells):
        for c in rng.choice(cells, size=len(cells) // 90, replace=False):
            z, x = divmod(int(c), size)
            y = int(g[z, x]) + 1
            if vol[y, z, x] == AIR:
                vol[y, z, x] = TREES[0][1]

    # planting along the walls of buildings that stand on lawns: bushes and flowers, not in front of doors
    closed = has_building & ~is_open                   # not round shelters and covered walkways: people walk in there
    beside = lawn & ~has_building & (window_fraction(closed, 1) > 0) & ~(window_fraction(has_building & is_open, 1) > 0)
    for y, oz, ox in doorways:
        beside[max(0, oz - 1):oz + 2, max(0, ox - 1):ox + 2] = False
    roll_b = rng.random((size, size))
    kind_b = rng.integers(0, len(FLOWERS), (size, size))
    bush = beside & (roll_b < 0.42) & (vol[g + 1, zz, xx] == AIR)
    z_i, x_i = np.nonzero(bush)
    vol[g[bush] + 1, z_i, x_i] = np.where(rng.random(len(z_i)) < 0.75, TREES[0][1], TREES[2][1])
    tall = bush & (roll_b < 0.10) & (vol[g + 2, zz, xx] == AIR)
    vol[g[tall] + 2, np.nonzero(tall)[0], np.nonzero(tall)[1]] = TREES[0][1]
    bed = beside & (roll_b >= 0.42) & (roll_b < (0.50 if lidar is not None else 0.72)) & (vol[g + 1, zz, xx] == AIR)
    vol[g[bed] + 1, np.nonzero(bed)[0], np.nonzero(bed)[1]] = np.array(FLOWERS, dtype=np.uint8)[kind_b[bed]]
    landscaping = int(bush.sum() + bed.sum())

    # grass, tall grass, ferns and flowers on grass blocks, as in the game's own plains and forests
    roll = rng.random((size, size))
    kind = rng.integers(0, len(FLOWERS), (size, size))
    open_grass = (vol[g, zz, xx] == GRASS) & (vol[g + 1, zz, xx] == AIR) & ~has_building
    rates = {S_GRASS: (0.22, 0.04, 0.0, 0.02), S_PARK: (0.10, 0.01, 0.0, 0.025), S_LAWN: (0.05, 0.0, 0.0, 0.01),
             S_FOREST: (0.16, 0.03, 0.07, 0.006), S_INDUSTRIAL: (0.10, 0.02, 0.0, 0.004)}  # grass, tall grass, fern, flowers
    if lidar is not None:            # a measured place: mown lawns and parks, as they are, not a wildflower meadow
        rates.update({S_GRASS: (0.10, 0.01, 0.0, 0.003), S_PARK: (0.04, 0.0, 0.0, 0.002), S_LAWN: (0.03, 0.0, 0.0, 0.0)})
    plants = 0
    for cls, (short, tall, fern, flower) in rates.items():
        here = open_grass & (ground == cls)
        cut = np.cumsum([short, tall, fern, flower])
        for which, (lo, hi) in enumerate(zip(np.r_[0, cut[:-1]], cut)):
            m = here & (roll >= lo) & (roll < hi)
            if which == 1:
                m &= vol[g + 2, zz, xx] == AIR
            z_i, x_i = np.nonzero(m)
            y = g[m] + 1
            if which == 0:
                vol[y, z_i, x_i] = SHORT_GRASS
            elif which == 1:
                vol[y, z_i, x_i], vol[y + 1, z_i, x_i] = TALL_GRASS
            elif which == 2:
                vol[y, z_i, x_i] = FERN
            else:
                vol[y, z_i, x_i] = np.array(FLOWERS, dtype=np.uint8)[kind[m]]
            plants += len(z_i)
    if CLIMATE["ground"] in ("dry", "desert"):        # dead bushes on the sand and dry earth
        bare_ground = np.isin(vol[g, zz, xx], (SAND, COARSE_DIRT)) & (vol[g + 1, zz, xx] == AIR) & ~has_building & \
            np.isin(ground, (S_GRASS, S_BARE, S_INDUSTRIAL)) & (roll < (0.02 if CLIMATE["ground"] == "desert" else 0.008))
        z_i, x_i = np.nonzero(bare_ground)
        vol[g[bare_ground] + 1, z_i, x_i] = DEAD_BUSH
        plants += len(z_i)
    bridge_blocks, bridge_rails = build_bridges(vol, details.get("bridges", []), g, level, water, ground,
                                                has_building, details.get("rails"), bed)
    # street-name signs: a fence post with the sign on top, on the verge beside the road, never on the road itself
    signs = []
    low = {AIR, SHORT_GRASS, FERN, DEAD_BUSH, *FLOWERS, *TALL_GRASS}
    track_here = details.get("rails") if details.get("rails") is not None else np.zeros((size, size), dtype=bool)
    keep_off = np.isin(ground, (S_ROAD, S_LINE, S_PARKING)) | has_building | water | track_here
    for y, oz, ox in doorways:                         # and out of the way of every door
        keep_off[max(0, oz - 1):oz + 2, max(0, ox - 1):ox + 2] = True
    for sx, sz, rotation, name in plan_street_signs(details.get("streets", []), size):
        fx, fz = -math.sin(math.radians(rotation * 22.5)), math.cos(math.radians(rotation * 22.5))   # it faces
        for step in (0, 1, 2, 3):                        # further from the road if it landed on it
            x, z = int(sx + fz * step), int(sz - fx * step)
            if 2 <= x < size - 2 and 2 <= z < size - 2 and not keep_off[z, x]:
                break
        else:
            continue
        y = int(g[z, x]) + 1
        if y + 2 >= vol.shape[0] or vol[y, z, x] not in low or vol[y + 1, z, x] not in low:
            continue
        vol[y, z, x] = FENCES[0]
        vol[y + 1, z, x] = SIGNS[rotation]
        if vol[y + 2, z, x] in low:
            vol[y + 2, z, x] = AIR
        signs.append((y + 1, z, x, rotation, sign_lines(name), "white"))

    street_signs = len(signs)
    # a sign with the building's name in front of its main door, facing the way in, as campuses and towns have them
    names = getattr(build_map, "building_names", {}) or {}
    named_signs = 0
    done = set()
    for cell, building in door_list:
        name = names.get(int(building))
        if not name or building in done:
            continue
        z, x = divmod(int(cell), size)
        f = int(facing[z, x])
        dz, dx = ((1, 0), (-1, 0), (0, 1), (0, -1))[f]            # out of the door
        sz, sx = (-dx, dz)                                        # to the side, off the path into the door
        for out_step, side in ((2, 2), (2, -2), (3, 2), (3, -2), (2, 3), (2, -3)):
            tz, tx = z + dz * out_step + sz * side, x + dx * out_step + sx * side
            if not (2 <= tx < size - 2 and 2 <= tz < size - 2) or keep_off[tz, tx] or has_building[tz, tx]:
                continue
            y = int(g[tz, tx]) + 1
            if y + 2 >= vol.shape[0] or vol[y, tz, tx] not in low or vol[y + 1, tz, tx] not in low:
                continue
            rotation = facing_rotation(dx, dz)                    # the text faces people coming to the door
            vol[y, tz, tx] = SIGNS[rotation]
            if vol[y + 1, tz, tx] in low:
                vol[y + 1, tz, tx] = AIR
            signs.append((y, tz, tx, rotation, sign_lines(name), "white"))
            keep_off[tz, tx] = True
            done.add(building)
            named_signs += 1
            break
    # road signs, bus stops, hydrants and bins from the map
    left = details.get("left_traffic", False)
    streets = details.get("streets", [])
    seat = ROOF_STAIRS[block("spruce_planks")]
    placed = {"stop signs": 0, "give way signs": 0, "bus stops": 0, "bus shelters": 0, "fire hydrants": 0,
              "litter bins": 0}

    def free_column(x, z, height):
        y = int(g[z, x]) + 1
        if y + height + 1 >= vol.shape[0] or any(vol[y + k, z, x] not in low for k in range(height)):
            return None
        return y

    for kind_, fx, fz, tags in details.get("furniture", []):
        if kind_ in ("hydrant", "bin"):
            spot = free_spot(keep_off, fx, fz, size)
            y = spot and free_column(spot[0], spot[1], 2)
            if y:
                vol[y, spot[1], spot[0]] = HYDRANT if kind_ == "hydrant" else LITTER_BIN
                placed["fire hydrants" if kind_ == "hydrant" else "litter bins"] += 1
            continue
        if kind_ not in ("stop", "give_way", "bus_stop"):
            continue
        near = nearest_road(streets, fx, fz, reach=15)
        if near is None:
            continue
        dist, i, k, (dx, dz), (cx, cz) = near
        width, line = streets[i][1], streets[i][2]
        if kind_ == "bus_stop":
            # traffic on the stop's side of the road: with the way as drawn on the right in right-hand countries
            on_right = (fx - cx) * -dz + (fz - cz) * dx >= 0 if dist >= width / 2 + 0.5 else not left
            tx, tz = (dx, dz) if on_right != left else (-dx, -dz)
        else:
            # which way the traffic that must stop goes: the direction tag, else towards the nearer end of the road
            way_dir = tags.get(f"{kind_}:direction") or tags.get("direction") or tags.get("traffic_sign:direction")
            if way_dir not in ("forward", "backward"):
                lengths = [math.dist(line[j], line[j + 1]) for j in range(len(line) - 1)]
                done = sum(lengths[:k]) + math.dist(line[k], (cx, cz))
                way_dir = "forward" if sum(lengths) - done < done else "backward"
            tx, tz = (dx, dz) if way_dir == "forward" else (-dx, -dz)
        sx, sz = (tz, -tx) if left else (-tz, tx)          # the kerb side for that traffic: left or right of it
        if kind_ == "bus_stop" and dist >= width / 2 + 0.5:
            px_, pz_ = fx, fz                                 # mapped beside the road, where the pole stands
        else:
            px_, pz_ = cx + sx * (width / 2 + 1.2), cz + sz * (width / 2 + 1.2)
        spot = free_spot(keep_off, px_, pz_, size)
        if spot is None:
            continue
        x, z = spot
        tall = 3 if kind_ == "bus_stop" else 2
        y = free_column(x, z, tall + 1)
        if y is None:
            continue
        if kind_ == "bus_stop":                             # a pole with the sign on top, facing the oncoming bus
            rotation = facing_rotation(-tx, -tz)
            vol[y:y + 2, z, x] = FENCES[0]
            vol[y + 2, z, x] = SIGNS[rotation]
            name = (tags.get("name") or "").strip()
            words = [l for l in sign_lines(name) if l][:3] if name else []
            lines = ["BUS"] + words + [""] * (3 - len(words)) if words else ["", "BUS", "STOP", ""]
            signs.append((y + 2, z, x, rotation, lines, "white"))
            placed["bus stops"] += 1
            if tags.get("shelter") == "yes":                # a glass shelter behind the pole, along the road
                ux, uz = (1, 0) if abs(tx) >= abs(tz) else (0, 1)
                ux, uz = (ux, uz) if ux * tx + uz * tz >= 0 else (-ux, -uz)
                vx, vz = (1, 0) if abs(sx) >= abs(sz) else (0, 1)
                vx, vz = (vx, vz) if vx * sx + vz * sz >= 0 else (-vx, -vz)
                cells = [(x + ux * a + vx * b, z + uz * a + vz * b) for a in range(1, 5) for b in (0, 1)]
                if all(2 <= cx_ < size - 2 and 2 <= cz_ < size - 2 and not keep_off[cz_, cx_] and
                       int(g[cz_, cx_]) + 1 == y and all(vol[y + h, cz_, cx_] in low for h in range(3))
                       for cx_, cz_ in cells):
                    back = {(0, -1): NORTH, (0, 1): SOUTH, (-1, 0): WEST, (1, 0): EAST}[(vx, vz)]
                    for a in range(1, 5):
                        for b in (0, 1):
                            cx_, cz_ = x + ux * a + vx * b, z + uz * a + vz * b
                            vol[y:y + 2, cz_, cx_] = GLASS if b == 1 else AIR
                            vol[y + 2, cz_, cx_] = PAVING_SLAB
                            if b == 0 and a in (2, 3):
                                vol[y, cz_, cx_] = seat[back]          # a bench with its back to the glass
                    placed["bus shelters"] += 1
        else:
            rotation = facing_rotation(-tx, -tz, steps=8)  # facing the drivers who must stop
            vol[y, z, x] = FENCES[0]
            if kind_ == "stop":
                vol[y + 1, z, x] = STOP_SIGNS[rotation // 2]
                signs.append((y + 1, z, x, rotation, ["", "STOP", "", ""], "white"))
                placed["stop signs"] += 1
            else:
                vol[y + 1, z, x] = GIVE_WAY_SIGNS[rotation // 2]
                signs.append((y + 1, z, x, rotation, ["", details.get("give_way_text", "GIVE WAY"), "", ""], "red"))
                placed["give way signs"] += 1
        keep_off[max(0, z - 1):z + 2, max(0, x - 1):x + 2] = True
    # more of what the map has: statues with their name, bicycle racks, outdoor tables, post boxes, flagpoles,
    # power towers; goals, nets and hoops on the pitches of their sport; stands that rise away from the pitch
    extras = place_extras(vol, g, details, keep_off, has_building, size, low, signs)
    build_volume.signs = signs
    for y, oz, ox in doorways:                         # keep the way out of every door clear (on a slope this
        vol[y:y + 2, oz, ox] = AIR                     # digs a doorway into the hillside), done last so nothing
                                                       # placed later can fill it
    build_volume.stats = {"trees": planted, "street lamps": lamps, "doors": doors_placed, "plants and flowers": plants,
                          "half slabs on slopes": slabs, "rails": rails_laid, "fence, wall and hedge blocks": barrier_cells,
                          "benches": benches, "bushes and flowers by buildings": landscaping,
                          "pieces of furniture, walls, doors and lamps inside": furnished, "staircases": staircases,
                          **{k: v for k, v in extras.items() if v}, "parked cars": cars, "traffic lights": signals,
                          "bridge blocks": bridge_blocks, "rails on bridges": bridge_rails,
                          "seagrass": water_life["seagrass"], "kelp stalks": water_life["kelp"],
                          "street-name signs": street_signs, "building name signs": named_signs, "street lamps from the map": real_lamps, **placed}
    build_volume.surface = np.where(water, level, top_of_ground)
    fix_beds(vol)
    hanging = (vol[:-1] == CEILING_LAMP) & (vol[1:] == AIR)        # a hanging lamp needs something to hang from
    vol[:-1][hanging] = AIR
    return vol, planted


def fix_beds(vol):
    """A bed is two blocks, its head and its foot; take away any half whose other half is missing (something else
    took that cell), or the game would show half a bed."""
    for f in range(4):
        dz, dx = STEP[f]                               # from the foot to the head is the bed's facing
        head, foot = BEDS[(FACINGS[f], "head")], BEDS[(FACINGS[f], "foot")]
        for this, other, sz, sx in ((head, foot, -dz, -dx), (foot, head, dz, dx)):
            y, z, x = np.nonzero(vol == this)
            if not len(y):
                continue
            oz, ox = np.clip(z + sz, 0, vol.shape[1] - 1), np.clip(x + sx, 0, vol.shape[2] - 1)
            lonely = vol[y, oz, ox] != other
            vol[y[lonely], z[lonely], x[lonely]] = AIR


def biome_map(ground):
    """One biome per 4x4 block column, the way the game stores them: the climate's biomes for open land and for
    woods, and river, ocean or beach."""
    n = ground.shape[0] // 4
    cells = ground[:n * 4, :n * 4].reshape(n, 4, n, 4).transpose(0, 2, 1, 3).reshape(n, n, 16)
    share = lambda classes: np.isin(cells, classes).mean(axis=2)
    land, woods = (BIOMES.index("minecraft:" + b) for b in CLIMATE["biomes"])
    out = np.full((n, n), land, dtype=np.uint8)
    out[share((S_FOREST,)) > 0.5] = woods
    sea_near = window_fraction(share((S_SEA,)) > 0, 2) > 0
    out[(share((S_SAND,)) > 0.5) & sea_near] = BEACH
    out[share((S_WATER,)) > 0.5] = RIVER
    out[share((S_SEA,)) > 0.5] = OCEAN
    return out


def make_preview(ground, bmap, specs, vol, heights=None):
    """Top-down picture of what will be built, with hills shaded."""
    rgb_of = np.zeros((len(BLOCKS), 3), dtype=np.uint8)
    for i, (name, _) in enumerate(BLOCKS):
        rgb_of[i] = BLOCK_RGB.get(name.split(":")[1], (200, 0, 200))
    lut = np.zeros(256, dtype=np.uint8)
    for surface, blk in SURFACE_BLOCK.items():
        lut[surface] = blk
    img = rgb_of[lut[ground]]
    spec = np.array(specs, dtype=np.int32)
    has_building = bmap > 0
    img[has_building] = rgb_of[spec[:, 2][bmap][has_building]]
    img[(bmap > 0) & ~interior_of(bmap)] //= 2            # dark outline around each building
    leaf_ids = [leaves for _, leaves in TREES]
    canopy = np.zeros(ground.shape, dtype=bool)
    for y in range(vol.shape[0]):
        canopy |= np.isin(vol[y], leaf_ids)
    img[canopy & ~has_building] = (44, 110, 44)
    if heights is not None and heights.max() > heights.min():
        slope = heights.astype(float) - np.roll(np.roll(heights, 1, 0), -1, 1)
        img = np.clip(img * (1 + 0.12 * np.clip(slope, -3, 3))[..., None], 0, 255).astype(np.uint8)
    return Image.fromarray(img)


# --------------------------------------------------------------------------- writing Minecraft files

def _name(text):
    raw = text.encode("utf-8")
    return struct.pack(">H", len(raw)) + raw


def nbt_byte(name, value):
    return b"\x01" + _name(name) + struct.pack(">b", value)


def nbt_int(name, value):
    return b"\x03" + _name(name) + struct.pack(">i", value)


def nbt_long(name, value):
    return b"\x04" + _name(name) + struct.pack(">q", value)


def nbt_string(name, value):
    return b"\x08" + _name(name) + _name(value)


def nbt_list(name, element_type, payloads):
    return b"\x09" + _name(name) + bytes([element_type]) + struct.pack(">i", len(payloads)) + b"".join(payloads)


def nbt_compound(name, payload):
    return b"\x0a" + _name(name) + payload + b"\x00"


def nbt_float(name, value):
    return b"\x05" + _name(name) + struct.pack(">f", value)


def nbt_double(name, value):
    return b"\x06" + _name(name) + struct.pack(">d", value)


def nbt_long_array(name, raw, count):
    return b"\x0c" + _name(name) + struct.pack(">i", count) + raw


def palette_entry(block_id):
    name, props = BLOCKS[block_id]
    payload = nbt_string("Name", name)
    if props:
        payload += nbt_compound("Properties", b"".join(nbt_string(k, v) for k, v in sorted(props.items())))
    return payload + b"\x00"


def pack_indices(index, bits, count_values):
    per_long = 64 // bits
    count = -(-count_values // per_long)
    padded = np.zeros(count * per_long, dtype=np.uint64)
    padded[:count_values] = index
    shifts = np.arange(per_long, dtype=np.uint64) * np.uint64(bits)
    return np.bitwise_or.reduce(padded.reshape(count, per_long) << shifts, axis=1), count


def encode_section(blocks, section_y, biomes=None):
    """blocks: 4096 block numbers in y, z, x order. biomes: 4x4 biome numbers (z, x) for this chunk, or None."""
    used, index = np.unique(blocks, return_inverse=True)
    states = nbt_list("palette", 10, [palette_entry(int(b)) for b in used])
    if len(used) > 1:
        bits = max(4, int(len(used) - 1).bit_length())
        per_long = 64 // bits
        count = -(-4096 // per_long)
        padded = np.zeros(count * per_long, dtype=np.uint64)
        padded[:4096] = index
        shifts = np.arange(per_long, dtype=np.uint64) * np.uint64(bits)
        longs = np.bitwise_or.reduce(padded.reshape(count, per_long) << shifts, axis=1)
        states += nbt_long_array("data", longs.astype(">u8").tobytes(), count)
    cells = np.tile(np.asarray(biomes if biomes is not None else np.zeros((4, 4)), dtype=np.int64).ravel(), 4)
    used_b, index_b = np.unique(cells, return_inverse=True)       # 4 x 4 x 4 cells in y, z, x order
    biome_tag = nbt_list("palette", 8, [_name(BIOMES[int(b)]) for b in used_b])
    if len(used_b) > 1:
        longs, count = pack_indices(index_b, int(len(used_b) - 1).bit_length(), 64)
        biome_tag += nbt_long_array("data", longs.astype(">u8").tobytes(), count)
    return (nbt_byte("Y", section_y) + nbt_compound("block_states", states)
            + nbt_compound("biomes", biome_tag) + b"\x00")


def sign_entity(x, y, z, lines, colour="white"):
    """A sign's text, as Minecraft 1.18 stores it (four JSON lines; the game moves them to the front of the sign when
    it upgrades the chunk). x, y, z: world coordinates."""
    payload = nbt_string("id", "minecraft:sign") + nbt_int("x", x) + nbt_int("y", y) + nbt_int("z", z)
    payload += nbt_byte("keepPacked", 0)
    for i, line in enumerate(lines[:4]):
        payload += nbt_string(f"Text{i + 1}", json.dumps({"text": line}, ensure_ascii=False))
    return payload + nbt_string("Color", colour) + nbt_byte("GlowingText", 0) + b"\x00"


def bed_entity(x, y, z):
    """The block entity the game draws a bed from (its colour comes from the block's name)."""
    return nbt_string("id", "minecraft:bed") + nbt_int("x", x) + nbt_int("y", y) + nbt_int("z", z) + \
        nbt_byte("keepPacked", 0) + b"\x00"


def encode_chunk(column, cx, cz, biomes=None, entities=()):
    """column: array [layer, 16, 16] for one chunk. biomes: 4x4 biome numbers or None. entities: the payloads of
    its block entities (sign_entity)."""
    nonair = np.flatnonzero(column.reshape(column.shape[0], -1).any(axis=1))
    n_sections = (int(nonair[-1]) // 16 + 1) if len(nonair) else 1
    sections = [encode_section(column[s * 16:(s + 1) * 16].ravel(), Y_MIN // 16 + s, biomes)
                for s in range(n_sections)]
    payload = (
        nbt_int("DataVersion", DATA_VERSION) + nbt_int("xPos", cx) + nbt_int("yPos", Y_MIN // 16) + nbt_int("zPos", cz)
        + nbt_string("Status", "minecraft:full") + nbt_long("LastUpdate", 0) + nbt_long("InhabitedTime", 0)
        + nbt_byte("isLightOn", 0)                     # 0 = the game works out the lighting when it loads the chunk
        + nbt_list("sections", 10, sections)
        + (nbt_list("block_entities", 10, list(entities)) if entities else nbt_list("block_entities", 0, []))
        + nbt_list("block_ticks", 0, []) + nbt_list("fluid_ticks", 0, [])
        + nbt_compound("Heightmaps", b"")
        + nbt_compound("structures", nbt_compound("References", b"") + nbt_compound("starts", b""))
    )
    return nbt_compound("", payload)


def write_region(path, chunks):
    """chunks: {(local x, local z): raw chunk bytes} for one 32x32-chunk region file."""
    locations, timestamps, body = bytearray(4096), bytearray(4096), bytearray()
    sector, now = 2, int(time.time())
    for (lx, lz), raw in sorted(chunks.items()):
        packed = zlib.compress(raw, 6)
        record = struct.pack(">IB", len(packed) + 1, 2) + packed
        n_sectors = -(-len(record) // 4096)
        record += b"\x00" * (n_sectors * 4096 - len(record))
        slot = 4 * (lx + lz * 32)
        locations[slot:slot + 4] = struct.pack(">I", (sector << 8) | n_sectors)
        timestamps[slot:slot + 4] = struct.pack(">I", now)
        body += record
        sector += n_sectors
    path.write_bytes(bytes(locations) + bytes(timestamps) + bytes(body))


def write_world(vol, world_dir, biomes=None, signs=()):
    """signs: (layer, z, x, rotation, lines, colour) for the text of every sign block (build_volume.signs)."""
    size = vol.shape[1]
    offset = size // 32            # chunk number of the west/north edge is -offset
    by_chunk = {}
    for y, z, x, _, lines, colour in signs:
        by_chunk.setdefault((x // 16, z // 16), []).append(
            sign_entity(x - size // 2, y + Y_MIN, z - size // 2, lines, colour))
    for y, z, x in zip(*np.nonzero(np.isin(vol, BED_BLOCKS))):      # every bed half needs its block entity
        by_chunk.setdefault((int(x) // 16, int(z) // 16), []).append(
            bed_entity(int(x) - size // 2, int(y) + Y_MIN, int(z) - size // 2))
    regions = {}
    for cz_i in range(size // 16):
        for cx_i in range(size // 16):
            cx, cz = cx_i - offset, cz_i - offset
            chunk_biomes = biomes[cz_i * 4:(cz_i + 1) * 4, cx_i * 4:(cx_i + 1) * 4] if biomes is not None else None
            raw = encode_chunk(vol[:, cz_i * 16:(cz_i + 1) * 16, cx_i * 16:(cx_i + 1) * 16], cx, cz, chunk_biomes,
                               by_chunk.get((cx_i, cz_i), ()))
            regions.setdefault((cx >> 5, cz >> 5), {})[(cx & 31, cz & 31)] = raw
    region_dir = Path(world_dir) / "region"
    region_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    for (rx, rz), chunks in regions.items():
        path = region_dir / f"r.{rx}.{rz}.mca"
        if path.exists():
            shutil.copy2(path, path.with_name(path.name + f".bak-{stamp}"))
        write_region(path, chunks)
    return len(regions), sum(len(c) for c in regions.values())


# --------------------------------------------------------------------------- ready-to-play worlds

LEVEL_DATA_VERSION = 4189      # level.dat in the format Minecraft 1.21.4 saves it; newer versions upgrade it on load


def saves_folder():
    """The Minecraft Java saves folder on Windows, macOS or Linux, or None."""
    home = Path.home()
    for saves in (Path(os.environ.get("APPDATA", "")) / ".minecraft" / "saves",
                  home / "Library" / "Application Support" / "minecraft" / "saves", home / ".minecraft" / "saves"):
        if saves.is_dir():
            return saves
    return None


def safe_folder_name(text):
    keep = "".join(c if c.isalnum() or c in " -_,.&'()" else " " for c in text)
    return " ".join(keep.split())[:60] or "place2mc world"


def choose_spawn(vol, ground, surface, near=None):
    """A spot on a street or path near the middle (or near `near`, (x, z) in blocks from the north-west corner) with
    room to stand, in Minecraft coordinates (x, y, z)."""
    size = ground.shape[0]
    zz, xx = np.indices(ground.shape)
    cx, cz = near if near is not None else (size / 2, size / 2)
    standable = np.isin(ground, STREET_SURFACES) | np.isin(ground, (S_GRASS, S_PARK, S_LAWN))
    feet = np.minimum(surface + 1, vol.shape[0] - 3)
    standable &= (vol[feet, zz, xx] == AIR) & (vol[feet + 1, zz, xx] == AIR) & (vol[surface, zz, xx] != WATER)
    order = np.argsort(((zz - cz) ** 2 + (xx - cx) ** 2).ravel(), kind="stable")
    for cell in order:
        z, x = divmod(int(cell), size)
        if standable[z, x]:
            return x - size // 2, Y_MIN + int(feet[z, x]), z - size // 2
    return 0, Y_MIN + int(surface[size // 2, size // 2]) + 2, 0


def write_level_dat(world_dir, name, spawn, seed=None):
    """level.dat for a new creative, peaceful world that is empty outside the converted area (a void world),
    with the same fields and types Minecraft 1.21.4 saves. It is always noon and never rains."""
    seed = int(time.time() * 1000) & ((1 << 63) - 1) if seed is None else seed
    strings = lambda items: [_name(v) for v in items]
    rules = {"doDaylightCycle": "false", "doWeatherCycle": "false", "doMobSpawning": "false",
             "doFireTick": "false", "spawnRadius": "0", "doInsomnia": "false"}
    flat_void = (nbt_string("biome", "minecraft:the_void") + nbt_byte("features", 0) + nbt_byte("lakes", 0)
                 + nbt_list("layers", 10, [nbt_int("height", 1) + nbt_string("block", "minecraft:air") + b"\x00"])
                 + nbt_list("structure_overrides", 0, []))           # an empty list, stored the way the game does
    dimensions = (
        nbt_compound("minecraft:overworld", nbt_string("type", "minecraft:overworld") + nbt_compound(
            "generator", nbt_string("type", "minecraft:flat") + nbt_compound("settings", flat_void)))
        + nbt_compound("minecraft:the_nether", nbt_string("type", "minecraft:the_nether") + nbt_compound(
            "generator", nbt_string("type", "minecraft:noise") + nbt_string("settings", "minecraft:nether")
            + nbt_compound("biome_source", nbt_string("type", "minecraft:multi_noise")
                           + nbt_string("preset", "minecraft:nether"))))
        + nbt_compound("minecraft:the_end", nbt_string("type", "minecraft:the_end") + nbt_compound(
            "generator", nbt_string("type", "minecraft:noise") + nbt_string("settings", "minecraft:end")
            + nbt_compound("biome_source", nbt_string("type", "minecraft:the_end")))))
    data = (
        nbt_int("DataVersion", LEVEL_DATA_VERSION) + nbt_int("version", 19133)
        + nbt_compound("Version", nbt_int("Id", LEVEL_DATA_VERSION) + nbt_string("Name", "1.21.4")
                       + nbt_string("Series", "main") + nbt_byte("Snapshot", 0))
        + nbt_string("LevelName", name) + nbt_int("GameType", 1) + nbt_byte("Difficulty", 0)
        + nbt_byte("DifficultyLocked", 0) + nbt_byte("allowCommands", 1) + nbt_byte("hardcore", 0)
        + nbt_byte("initialized", 1) + nbt_byte("WasModded", 0)
        + nbt_int("SpawnX", spawn[0]) + nbt_int("SpawnY", spawn[1]) + nbt_int("SpawnZ", spawn[2])
        + nbt_float("SpawnAngle", 0.0)
        + nbt_long("Time", 0) + nbt_long("DayTime", 6000) + nbt_long("LastPlayed", int(time.time() * 1000))
        + nbt_byte("raining", 0) + nbt_int("rainTime", 0) + nbt_byte("thundering", 0) + nbt_int("thunderTime", 0)
        + nbt_int("clearWeatherTime", 0)
        + nbt_list("ServerBrands", 8, strings(["vanilla"]))
        + nbt_compound("DataPacks", nbt_list("Enabled", 8, strings(["vanilla"]))
                       + nbt_list("Disabled", 8, strings(["minecart_improvements", "redstone_experiments",
                                                          "trade_rebalance"])))
        + nbt_compound("GameRules", b"".join(nbt_string(k, v) for k, v in rules.items()))
        + nbt_compound("WorldGenSettings", nbt_long("seed", seed - (1 << 63) if seed >= (1 << 63) else seed)
                       + nbt_byte("generate_features", 0) + nbt_byte("bonus_chest", 0)
                       + nbt_compound("dimensions", dimensions))
        + nbt_double("BorderCenterX", 0.0) + nbt_double("BorderCenterZ", 0.0) + nbt_double("BorderSize", 59999968.0)
        + nbt_double("BorderSafeZone", 5.0) + nbt_double("BorderWarningBlocks", 5.0)
        + nbt_double("BorderWarningTime", 15.0) + nbt_double("BorderDamagePerBlock", 0.2)
        + nbt_double("BorderSizeLerpTarget", 59999968.0) + nbt_long("BorderSizeLerpTime", 0)
        + nbt_int("WanderingTraderSpawnDelay", 24000) + nbt_int("WanderingTraderSpawnChance", 25)
        + nbt_compound("CustomBossEvents", b"") + nbt_list("ScheduledEvents", 0, [])
    )
    Path(world_dir).mkdir(parents=True, exist_ok=True)
    with gzip.open(Path(world_dir) / "level.dat", "wb") as fh:
        fh.write(nbt_compound("", nbt_compound("Data", data)))


def new_world_folder(saves, name):
    """A world folder that does not exist yet: "name", or "name (2)", "name (3)", ..."""
    base = safe_folder_name(name)
    folder, n = Path(saves) / base, 2
    while folder.exists():
        folder, n = Path(saves) / f"{base} ({n})", n + 1
    return folder


# --------------------------------------------------------------------------- command line

def find_worlds():
    home = Path.home()
    candidates = [Path(os.environ.get("APPDATA", "")) / ".minecraft" / "saves",
                  home / "Library" / "Application Support" / "minecraft" / "saves",
                  home / ".minecraft" / "saves"]
    worlds = []
    for saves in candidates:
        if saves.is_dir():
            worlds += sorted(p for p in saves.iterdir() if (p / "level.dat").exists())
    return worlds


def ask_world():
    worlds = find_worlds()
    if worlds:
        print("\nYour Minecraft worlds:")
        for i, w in enumerate(worlds, 1):
            print(f"  {i}. {w.name}")
        answer = input("Number of the world to build in (use a fresh Superflat world), or a folder path: ").strip()
        if answer.isdigit() and 1 <= int(answer) <= len(worlds):
            return worlds[int(answer) - 1]
        return Path(answer.strip('"'))
    return Path(input("Path to the Minecraft world folder: ").strip().strip('"'))


def main():
    try:    # place names can contain letters the Windows console cannot print
        sys.stdout.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass
    ap = argparse.ArgumentParser(description="Turn a real-world place into a Minecraft world: Java (1.18+) and Bedrock (1.21.120+).")
    ap.add_argument("place", nargs="?", help='place name, for example "Needham, Massachusetts"')
    ap.add_argument("--center", help="LAT,LON to use instead of a place name")
    ap.add_argument("--radius", type=int, help=f"half the side of the square to build, in metres (default 300, max {MAX_RADIUS})")
    ap.add_argument("--world", help="an existing Minecraft world folder to build into (for Java 1.18 to 1.21.3)")
    ap.add_argument("--new-world", metavar="NAME",
                    help="create a new ready-to-play world with this name (the default; needs Java 1.21.4 or newer)")
    ap.add_argument("--saves", help="Minecraft saves folder for new worlds (found automatically if not given)")
    ap.add_argument("--edition", choices=("java", "bedrock", "both"), default="java",
                    help="java: a world in your Java saves folder; bedrock: a .mcworld file; both")
    ap.add_argument("--osm-file", help="use a saved .osm / .json map file instead of downloading")
    ap.add_argument("--save-osm", help="save the downloaded map data to this .json file")
    ap.add_argument("--preview-only", action="store_true", help="only make the preview picture")
    ap.add_argument("--screenshots", action="store_true", help="also draw game-style 3D pictures (needs mcrender.py next to this file)")
    ap.add_argument("--climate", metavar="CODE",
                    help="Köppen climate code to use instead of the place's own, e.g. BWh (hot desert), Af (rainforest)")
    ap.add_argument("--season", default="today", choices=("today", "autumn", "summer"),
                    help="leaf colours in the game worlds: autumn colours when today is autumn at the place "
                         "(default), always autumn, or always summer-green")
    ap.add_argument("--picture-time", default="afternoon", choices=("morning", "midday", "afternoon", "sunset", "night"),
                    help="time of day for the --screenshots pictures: the real sun at this place today (default afternoon)")
    ap.add_argument("--plain-pictures", action="store_true",
                    help="draw the --screenshots pictures in the game's plain noon light, without sun and shadows (faster)")
    ap.add_argument("--no-walk", action="store_true", help="do not make the walk-through page")
    ap.add_argument("--no-overture", action="store_true",
                    help="do not add buildings and land cover from Overture Maps where OpenStreetMap has none")
    ap.add_argument("--overture-file", help="use Overture data saved earlier with --save-overture (works offline)")
    ap.add_argument("--save-overture", help="save the Overture data downloaded for this area to a .json file")
    ap.add_argument("--spawn", help="LAT,LON[,FACING]: start there instead of near the middle, looking FACING degrees "
                                    "from north (90 = east) in the walk-through")
    ap.add_argument("--lidar", help="a LiDAR summary made by app/lidar.js for this centre and size: the real ground, the "
                                    "measured roof of every building and every tree at its real height")
    ap.add_argument("--tags", help='a .json file of map tags to add (colours, materials, floors) where the map has '
                                   'none, for example examples/ala/ala-tags.json')
    ap.add_argument("--textures", default="auto",
                    help='textures for the pictures and the walk-through: "auto" (your Minecraft Java if installed, else '
                         'the Minecraft-style pack that comes with place2mc), "pack", "builtin", or a game .jar or resource pack')
    ap.add_argument("--flat", action="store_true", help="build on flat ground instead of the real hills")
    ap.add_argument("--tile-cache", default=str(Path.home() / ".place2mc" / "elevation"),
                    help="folder that keeps downloaded elevation tiles, so an area is only downloaded once")
    ap.add_argument("--force", action="store_true", help="write even if the folder has no level.dat")
    # coordinates south of the equator start with a minus sign, which would be read as an option:
    # "--center -26.07,27.91" becomes "--center=-26.07,27.91"
    argv = sys.argv[1:]
    for i in range(len(argv) - 1):
        if argv[i] == "--center" and re.match(r"^-?\d", argv[i + 1]):
            argv[i:i + 2] = [f"--center={argv[i + 1]}", ""]
    args = ap.parse_args([a for a in argv if a != ""])

    if not (args.place or args.center or args.osm_file):
        args.place = input("Which place? (for example: Needham, Massachusetts) ").strip()
    file_label = (Path(args.osm_file).name.split(".")[0].replace("_", " ").replace("-", " ").title()
                  if args.osm_file else None)                    # monaco.osm -> "Monaco"
    if not args.world and not args.preview_only:
        saves = Path(args.saves) if args.saves else saves_folder()
        if args.edition == "bedrock" or (saves is None and args.edition == "both"):
            saves = Path(args.saves) if args.saves else Path.home() / "place2mc worlds"   # the .mcworld goes here
            saves.mkdir(parents=True, exist_ok=True)
        if saves is None:
            sys.exit("Could not find your Minecraft Java saves folder. Open Minecraft Java once, or give --saves FOLDER.")
        args.world = str(new_world_folder(saves, args.new_world or args.place or file_label or "place2mc world"))
        args.creating = True
    else:
        args.creating = False

    elements, bounds, label = None, None, args.new_world or args.place or file_label or args.center
    if args.osm_file:
        print(f"Reading {args.osm_file} ...")
        elements, bounds = load_osm_file(args.osm_file)
    radius = args.radius or 300
    if args.center:
        lat, lon = (float(v) for v in args.center.split(","))
    elif args.osm_file:
        if bounds is None:
            sys.exit("This map file has no bounds. Add --center LAT,LON.")
        lat, lon = (bounds[0] + bounds[2]) / 2, (bounds[1] + bounds[3]) / 2
        if not args.radius:
            m_lat, m_lon = metres_per_degree(lat)
            radius = int(min(MAX_RADIUS, max(50, min((bounds[2] - bounds[0]) * m_lat, (bounds[3] - bounds[1]) * m_lon) / 2)))
    else:
        lat, lon, label = geocode(args.place)
        print(f"Found: {label}")
    if not 20 <= radius <= MAX_RADIUS:
        sys.exit(f"--radius must be between 20 and {MAX_RADIUS} metres in this version.")
    size = -(-2 * radius // 32) * 32     # side of the square in blocks, rounded up to whole regions of chunks

    downloaded = elements is None
    if elements is None:
        elements = download_osm(lat, lon, size / 2)
        if args.save_osm:
            Path(args.save_osm).write_text(json.dumps({"elements": elements}))
    if args.overture_file or (downloaded and not args.no_overture):
        sys.path.insert(0, str(Path(__file__).resolve().parent))      # overture.py sits next to this file
        import overture
        extra, heights = [], {}
        try:
            if args.overture_file:
                saved = json.loads(Path(args.overture_file).read_text())
                extra, heights = saved["elements"], saved.get("heights", {})
            else:
                print("Downloading buildings and land cover the map is missing, from Overture Maps ...")
                extra, heights = overture.fetch(lat, lon, size / 2, cache_dir=args.tile_cache)
                if args.save_overture:
                    Path(args.save_overture).write_text(json.dumps({"elements": extra, "heights": heights}))
        except Exception as exc:                        # Overture only adds detail: carry on without it
            print(f"  Overture Maps could not be read ({exc}); going on with OpenStreetMap only.")
        filled = overture.add_heights(elements, heights)
        elements = elements + extra
        if extra or filled:
            print(f"Overture: {len(extra)} map objects added, heights for {filled} OpenStreetMap buildings")
    if args.tags:
        print(f"Added tags from {args.tags} to {apply_tag_file(elements, args.tags)} map objects")
    print(f"Centre {lat:.5f}, {lon:.5f}  |  {size} x {size} blocks  |  {len(elements)} map objects")

    ground, bmap, specs, trees, counts = build_map(elements, lat, lon, size, climate=args.climate)
    climate = CLIMATE
    print(f"Climate: {climate['code']} ({climate['kind']}): {climate['biomes'][0]} biome, "
          f"{', '.join(k for k in climate['trees'])} trees")
    sys.path.insert(0, str(Path(__file__).resolve().parent))          # mcrender.py and walkdata.py sit here too
    try:
        import mcrender
        mcrender.set_climate_tints(climate["grass"], climate["foliage"])
    except ImportError:
        pass
    print("Found: " + ", ".join(f"{v} {k}" for k, v in counts.items()))
    if counts["buildings"] + counts["roads"] == 0:
        print("Warning: no buildings or roads here. Is the place right, and is it mapped on openstreetmap.org?")
    lidar = None
    if args.lidar:
        lidar, extra = prepare_lidar(args.lidar, lat, lon, size, ground, bmap, specs)
        print(f"LiDAR: {lidar['points'] or 0:,} laser points, {len(lidar['trees'])} trees, "
              f"{int(np.isfinite(lidar['roof']).sum()):,} roof cells measured, {extra} buildings the map lacks")
    terrain = None if args.flat else make_terrain(lat, lon, size, ground, args.tile_cache,
                                                  elev=lidar["dtm"] if lidar is not None else None)
    vol, planted = build_volume(ground, bmap, specs, trees, terrain=terrain, details=build_map.details,
                                deep_water=args.creating or not args.world,   # an existing world keeps its sea at y=-61
                                lidar=lidar)
    print("Added " + ", ".join(f"{v} {k}" for k, v in build_volume.stats.items()) + ".")
    print(f"Tallest point: y={Y_MIN + int(np.flatnonzero(vol.reshape(vol.shape[0], -1).any(axis=1))[-1])}")

    out_dir = Path(args.world) if args.world else Path(".")
    if args.world and not args.creating and not (out_dir / "level.dat").exists() and not args.force:
        sys.exit(f"{out_dir} does not look like a Minecraft world (no level.dat). Create the world in Minecraft first, "
                 "or leave out --world to get a new ready-to-play world.")
    out_dir.mkdir(parents=True, exist_ok=True)
    preview = out_dir / "place2mc_preview.png"
    make_preview(ground, bmap, specs, vol, build_volume.surface).save(preview)
    print(f"Preview picture: {preview}")
    if args.screenshots:
        sys.path.insert(0, str(Path(__file__).resolve().parent))     # mcrender.py sits next to this file
        try:
            import mcrender
        except ImportError:
            sys.exit("--screenshots needs mcrender.py in the same folder as place2mc.py.")
        for path in mcrender.standard_views(vol, out_dir, textures=args.textures, latlon=(lat, lon),
                                            time=args.picture_time, plain=args.plain_pictures):
            print(f"Picture: {path}")
    if args.preview_only:
        return
    near, spawn_yaw = None, None
    if args.spawn:                          # LAT,LON[,FACING]: where to start, and which way to look (degrees, 0 north)
        parts = [float(v) for v in args.spawn.split(",")]
        m_lat, m_lon = metres_per_degree(lat)
        near = ((parts[1] - lon) * m_lon + size / 2, (lat - parts[0]) * m_lat + size / 2)
        spawn_yaw = math.radians(parts[2]) if len(parts) > 2 else None
    spawn = choose_spawn(vol, ground, build_volume.surface, near)
    biomes = biome_map(ground)
    world_name = safe_folder_name(args.new_world or label)
    # autumn: the leaves' colours for today's date here (or --season), as a resource pack for each edition
    sys.path.insert(0, str(Path(__file__).resolve().parent))          # autumn.py sits next to this file
    import autumn
    autumn_now = {"autumn": True, "summer": False}.get(args.season, autumn.autumn_amount(lat) >= 0.5)
    if autumn_now and args.edition in ("java", "both"):
        pack = autumn.java_pack(out_dir / f"Autumn leaves for {world_name} (Java resource pack).zip", world_name)
        print(f"Autumn leaves: {pack} (Java: Options > Resource Packs > Open Pack Folder, put it there, turn it on)")
    if not args.no_walk:
        sys.path.insert(0, str(Path(__file__).resolve().parent))      # walkdata.py and mcrender.py sit next to this file
        import walkdata
        credit = "Map data © OpenStreetMap contributors" + (
            ", Overture Maps Foundation, ESA WorldCover" if any(e.get("id", 0) >= 9_000_000_000_000 for e in elements) else "") + (
            " · Heights: USGS 3DEP LiDAR" if lidar is not None else "")
        labels = place_labels(elements, lat, lon, size, bmap, build_map.details)
        walk = walkdata.export(vol, out_dir / f"Walk around {world_name}.html", spawn, world_name, textures=args.textures,
                               credit=credit, latlon=(lat, lon), signs=getattr(build_volume, "signs", ()), spawn_yaw=spawn_yaw,
                               labels=labels)
        print(f"Walk-through: {walk} (open it in a browser to walk around, no Minecraft needed)")
        print(f"WALK: {walk.resolve()}")
    if args.edition in ("bedrock", "both"):
        sys.path.insert(0, str(Path(__file__).resolve().parent))      # bedrock.py sits next to this file
        import bedrock
        mcworld = bedrock.export_mcworld(vol, biomes, out_dir / f"{world_name}.mcworld", world_name, spawn,
                                         Image.open(preview), signs=getattr(build_volume, "signs", ()),
                                         autumn=autumn_now)
        print(f"Bedrock world: {mcworld} (double-click it to add it to Minecraft Bedrock 1.21.120 or newer)")
        print(f"MCWORLD: {mcworld.resolve()}")
        if args.edition == "bedrock":
            print(f"WORLD: {out_dir.resolve()}")
            return
    n_regions, n_chunks = write_world(vol, out_dir, biomes, getattr(build_volume, "signs", ()))
    print(f"Wrote {n_chunks} chunks in {n_regions} region files to {out_dir / 'region'}")
    if args.creating:
        write_level_dat(out_dir, world_name, spawn)
        icon = Image.open(preview).convert("RGB")
        side = min(icon.size) // 2
        cx, cy = icon.size[0] // 2, icon.size[1] // 2
        icon.crop((cx - side // 2, cy - side // 2, cx + side // 2, cy + side // 2)).resize((64, 64)).save(out_dir / "icon.png")
        print(f"Done. Your new world \"{out_dir.name}\" is in the Minecraft world list (Java 1.21.4 or newer). "
              f"You start on a street near the middle of {label}.")
    else:
        print(f"Done. Open the world and run:  /tp {spawn[0]} {spawn[1]} {spawn[2]}   (a street near the middle of {label})")
    print(f"WORLD: {out_dir.resolve()}")


if __name__ == "__main__":
    main()
