"""
Data for the walk-through (app/walk.html): the built world in a compact form a web page can draw.

One HTML file that opens anywhere (double-click it, or send it to a phone): the viewer (app/walk.html) with the
world inside it, as window.WALK_DATA = {info, bin, atlas}:
  bin    base64 of gzip of every block column from the bottom up, as runs of (block number, length 1-255)
  info   size, where you start, and for every block number: its name and how to draw it
         (shape boxes, crossed plant pictures, see-through, light) and how it collides
  atlas  base64 PNG, 16 x 16 textures stacked in one column: block b's top is tile 2b, its sides tile 2b+1
"""
import base64
import io
import gzip
import json
from pathlib import Path

import numpy as np
from PIL import Image

import mcrender
import place2mc as p


def column_runs(vol):
    """Every column (z, then x), bottom to top, as (block, run length) byte pairs. Returns bytes."""
    layers, size_z, size_x = vol.shape
    cols = vol.reshape(layers, -1).T                    # one row per column, bottom to top
    out = bytearray()
    change = np.ones((cols.shape[0], layers), dtype=bool)
    change[:, 1:] = cols[:, 1:] != cols[:, :-1]
    for c in range(cols.shape[0]):
        starts = np.flatnonzero(change[c])
        ends = np.r_[starts[1:], layers]
        for s, e in zip(starts, ends):
            b, n = int(cols[c, s]), int(e - s)
            while n > 0:
                take = min(n, 255)
                out += bytes((b, take))
                n -= take
    return bytes(out)


def block_table():
    """How the viewer should draw each block, from the same tables the pictures use."""
    names = [n.split(":")[1] for n, _ in p.BLOCKS]
    table = []
    for i, (full, props) in enumerate(p.BLOCKS):
        name = names[i]
        boxes = []
        if mcrender.BOXED[i]:
            for b in range(mcrender.MAX_BOXES):
                lo, hi = mcrender.BOX_LO[i, b], mcrender.BOX_HI[i, b]
                if (lo <= hi).all():
                    boxes.append([round(float(v), 4) for v in (*lo, *hi)])
        cross = bool(mcrender.CROSS[i])
        invisible = bool(mcrender.INVISIBLE[i]) or name == "air"
        # what you bump into: full blocks; fences and walls 1.5 tall like the game; nothing for plants, rails, water
        if invisible and name != "barrier":
            collide = []
        elif name == "barrier":
            collide = [[0, 0, 0, 1, 1, 1]]
        elif cross or name in ("rail", "water", "lantern") or name.endswith("_door"):
            collide = [] if not name.endswith("_door") else boxes
        elif name.endswith(("_fence", "_wall")):
            collide = [[min(b[0] for b in boxes), 0, min(b[2] for b in boxes), max(b[3] for b in boxes), 1.5,
                        max(b[5] for b in boxes)]]
        elif boxes:
            collide = boxes
        else:
            collide = [[0, 0, 0, 1, 1, 1]]
        translucent = {"water": 0.62, "glass": 0.18}.get(name, 0.5 if name.endswith("_stained_glass") else 0)
        table.append({
            "name": name, "props": props or {},
            "invisible": invisible,
            "opaque": bool(mcrender.OPAQUE[i]) and not invisible,          # stops light, hides faces next to it
            "cube": not boxes and not cross and not invisible,
            "boxes": boxes, "cross": cross,
            "leaves": bool(mcrender.SEE_THROUGH[i]),
            "translucent": translucent,
            "lightCost": int(mcrender.LIGHT_COST[i]),
            "skyStop": bool(mcrender._BLOCKS_SKY_BELOW[i]) and not invisible,
            "glow": bool(mcrender.GLOWING[i]),
            "wet": bool(mcrender.UNDERWATER[i]),                          # seagrass, kelp: water in the same cell
            "collide": collide,
        })
    return table


VIEWER = Path(__file__).resolve().parent / "app" / "walk.html"


def export(vol, out_path, spawn, name, textures="auto", credit="Map data © OpenStreetMap contributors", latlon=None):
    """Write the walk-through HTML file. spawn: where the feet are, in Minecraft coordinates (x, y, z), as
    choose_spawn gives it; the centre of the built square is x=0, z=0."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    used = np.flatnonzero(vol.reshape(vol.shape[0], -1).any(axis=1))
    layers = int(used[-1]) + 3 if len(used) else 1
    vol = vol[:layers]
    packed = gzip.compress(column_runs(vol), 6)
    source = mcrender.resolve_textures(textures)
    tex, mean, solid = mcrender.make_textures(source)
    n = tex.shape[0]
    rgba = np.zeros((n * 2 * 16, 16, 4), dtype=np.uint8)
    for b in range(n):
        for face in range(2):
            tile = (2 * b + face) * 16
            rgba[tile:tile + 16, :, :3] = np.clip(tex[b, face] * 255 + 0.5, 0, 255).astype(np.uint8)
            rgba[tile:tile + 16, :, 3] = np.where(solid[b, face], 255, 0)
    png = io.BytesIO()
    Image.fromarray(rgba, "RGBA").save(png, "PNG", optimize=True)
    info = {
        "format": 1, "name": name, "credit": credit, "sizeX": int(vol.shape[2]), "sizeZ": int(vol.shape[1]), "layers": layers,
        "yMin": p.Y_MIN,
        "spawn": [spawn[0] + vol.shape[2] // 2 + 0.5, spawn[1] - p.Y_MIN, spawn[2] + vol.shape[1] // 2 + 0.5],
        "lightmap": [round(float(v), 4) for v in mcrender.LIGHTMAP],
        "faceShade": mcrender.FACE_SHADE, "aoLevels": [float(v) for v in mcrender.AO_LEVELS],
        "zenith": [round(float(v), 4) for v in mcrender.ZENITH], "horizon": [round(float(v), 4) for v in mcrender.HORIZON],
        "textures": ("Pixel Perfection CE (CC BY-SA 4.0)" if source is not None and source.path == mcrender.BUNDLED_TEXTURES
                     else "your Minecraft" if source is not None else "built-in"),
        "blocks": block_table(),
    }
    if latlon:                                  # for the sun: where it stands over this place on the day you walk
        info["lat"], info["lon"] = round(float(latlon[0]), 5), round(float(latlon[1]), 5)
    data = {"info": info, "bin": base64.b64encode(packed).decode("ascii"),
            "atlas": base64.b64encode(png.getvalue()).decode("ascii")}
    page = VIEWER.read_text(encoding="utf-8")
    inject = "<script>window.WALK_DATA = " + json.dumps(data, separators=(",", ":")).replace("</", "<\\/") + ";</script>\n"
    marker = '<script>\n"use strict";'
    assert page.count(marker) == 1, "app/walk.html changed: cannot find where to put the world"
    page = page.replace(marker, inject + marker)
    page = page.replace("<title>place2mc walk-through</title>", f"<title>{_html(name)} – walk around</title>")
    out_path.write_text(page, encoding="utf-8")
    return out_path


def _html(text):
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
