"""Check every block place2mc writes against block data extracted from the real game
(PrismarineJS/minecraft-data, https://github.com/PrismarineJS/minecraft-data).

For each game version it checks that:
  1. the block exists,
  2. every property name we set exists on that block, and every value is allowed,
  3. the shapes mcrender draws for stairs, doors, slabs and lanterns match the game's collision shapes; for fences
     and walls (whose collision boxes are taller and wider than what the game draws) that they join the same sides;
     rails and plants have no collision box in the game.

usage: python -I tests/check_blocks.py MCDATA_DIR VERSION_DIR [VERSION_DIR ...]
       (MCDATA_DIR/pc/<version>/blocks.json and blockCollisionShapes.json)
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import numpy as np
import place2mc as p
import mcrender
import walkdata

root = Path(sys.argv[1])
problems = 0
# blocks the game renamed; worlds stamped with an older version get the new name when the game loads them
RENAMED = {"grass": "short_grass"}
def sides_reached(boxes):
    out = set()
    for x0, y0, z0, x1, y1, z1 in boxes:
        out |= {side for side, hit in (("north", z0 <= 0), ("south", z1 >= 1), ("west", x0 <= 0), ("east", x1 >= 1),
                                       ("middle", x0 <= .5 <= x1 and z0 <= .5 <= z1)) if hit}
    return sorted(out)


def voxels(boxes):
    """The sixteenths of a block a list of boxes fills."""
    out = set()
    for x0, y0, z0, x1, y1, z1 in boxes:
        for i in range(round(x0 * 16), round(x1 * 16)):
            for j in range(round(y0 * 16), round(y1 * 16)):
                for k in range(round(z0 * 16), round(z1 * 16)):
                    out.add((i, j, k))
    return out


for version in sys.argv[2:]:
    blocks = {b["name"]: b for b in json.loads((root / "pc" / version / "blocks.json").read_text())}
    shapes_file = root / "pc" / version / "blockCollisionShapes.json"
    shapes = json.loads(shapes_file.read_text()) if shapes_file.exists() else None
    bad, shape_checks, renamed = [], 0, set()
    biomes_file = root / "pc" / version / "biomes.json"
    if biomes_file.exists():
        known = {"minecraft:" + b["name"] for b in json.loads(biomes_file.read_text())}
        bad += [f"biome {b} does not exist" for b in p.BIOMES if b not in known]
    for i, (full_name, props) in enumerate(p.BLOCKS):
        name = full_name.split(":")[1]
        info = blocks.get(name)
        if info is None and name in RENAMED and RENAMED[name] in blocks:
            renamed.add(f"{name} -> {RENAMED[name]}")
            name = RENAMED[name]
            info = blocks[name]
        if info is None:
            bad.append(f"{name}: no such block")
            continue
        states = {s["name"]: s for s in info.get("states", [])}
        for key, value in (props or {}).items():
            if key not in states:
                bad.append(f"{name}: no property '{key}'")
                continue
            allowed = [str(v) for v in states[key].get("values", [])] if states[key]["type"] == "enum" else \
                ["true", "false"] if states[key]["type"] == "bool" else \
                [str(n) for n in range(states[key].get("num_values", 0))]
            if str(value) not in allowed:
                bad.append(f"{name}: {key}={value} not in {allowed}")
        # compare the render shape with the game's collision shape for this exact block state
        if shapes is not None and (mcrender.BOXED[i] or mcrender.CROSS[i]):
            order = [s["name"] for s in info["states"]]
            values = {s["name"]: ([str(v) for v in s["values"]] if s["type"] == "enum" else
                                  ["true", "false"] if s["type"] == "bool" else
                                  [str(n) for n in range(s["num_values"])]) for s in info["states"]}
            # the game's default state (what a block written without properties becomes), decoded from its number
            rest, full = info["defaultState"] - info["minStateId"], {}
            for k in reversed(order):                               # the last property changes fastest
                rest, full[k] = divmod(rest, len(values[k]))[0], values[k][rest % len(values[k])]
            if "waterlogged" in full:
                full["waterlogged"] = "false"
            if name.endswith("_fence"):
                full.update({"north": "false", "south": "false", "east": "false", "west": "false"})
            if name.endswith("_wall"):
                full.update({"north": "none", "south": "none", "east": "none", "west": "none", "up": "true"})
            full.update(props or {})
            index = 0
            for k in order:                                         # the last property changes fastest
                index = index * len(values[k]) + values[k].index(full[k])
            entry = shapes["blocks"][name]
            shape_id = entry[index] if isinstance(entry, list) else entry
            game = sorted(tuple(round(c, 4) for c in box) for box in shapes["shapes"][str(shape_id)])
            ours = sorted(tuple(round(float(c), 4) for c in tuple(mcrender.BOX_LO[i, b]) + tuple(mcrender.BOX_HI[i, b]))
                          for b in range(mcrender.MAX_BOXES) if mcrender.BOX_LO[i, b, 0] <= mcrender.BOX_HI[i, b, 0])
            shape_checks += 1
            if name.endswith(("_fence", "_wall")):
                # the game makes these 1.5 blocks tall so players cannot jump over, and wider than drawn:
                # compare which sides of the block they reach and whether they stand in the middle
                game, ours = sides_reached(game), sides_reached(ours)
            if name.endswith("_sign"):
                # drawn by the game's sign renderer, not a block model, and walked through: no collision box
                if game:
                    bad.append(f"{name}: the game gives this sign a collision box {game}")
            elif mcrender.CROSS[i] or name == "rail":
                # drawn as two crossed pictures; in the game these plants have no collision box at all
                if game:
                    bad.append(f"{name}: drawn as a walk-through plant but the game gives it a collision box {game}")
            elif game != ours and voxels(game) != voxels(ours):     # the same shape cut into other boxes is fine
                bad.append(f"{name} {props}: render shape {ours} but game shape {game}")
    # the walk-through's open doors (walkdata.DOOR_OPEN) against the game's shape for every facing and hinge
    if shapes is not None and "dark_oak_door" in blocks:
        info = blocks["dark_oak_door"]
        order = [s["name"] for s in info["states"]]
        values = {s["name"]: ([str(v) for v in s["values"]] if s["type"] == "enum" else ["true", "false"])
                  for s in info["states"]}
        for (facing, hinge), (lo, hi) in walkdata.DOOR_OPEN.items():
            full = {"facing": facing, "half": "lower", "hinge": hinge, "open": "true", "powered": "false"}
            index = 0
            for k in order:
                index = index * len(values[k]) + values[k].index(full[k])
            entry = shapes["blocks"]["dark_oak_door"]
            game = [tuple(round(c, 4) for c in box) for box in shapes["shapes"][str(entry[index])]]
            ours = [tuple(round(float(c), 4) for c in (*lo, *hi))]
            shape_checks += 1
            if game != ours:
                bad.append(f"open door {facing}/{hinge}: walk-through shape {ours} but game shape {game}")
    problems += len(bad)
    print(f"Minecraft {version}: {len(p.BLOCKS)} block states checked, {shape_checks} shapes compared, "
          f"{'all OK' if not bad else str(len(bad)) + ' problems'}"
          + (f" (renamed by the game on load: {', '.join(sorted(renamed))})" if renamed else ""))
    for line in bad[:30]:
        print("   ", line)
sys.exit(1 if problems else 0)
