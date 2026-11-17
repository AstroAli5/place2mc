"""Build bedrock_blocks.json: the Bedrock version of every block place2mc writes.

It uses data from PrismarineJS/minecraft-data: the Java-to-Bedrock block table (blocksJ2B), the Java block
defaults, and the list of every valid Bedrock block state, which each answer must match exactly.

usage: python -I tests/make_bedrock_table.py MCDATA_DIR
       (MCDATA_DIR/pc/1.21.4/blocks.json, MCDATA_DIR/bedrock/1.21.120/blocksJ2B.json and blockStates.json)
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import place2mc as p

root = Path(sys.argv[1])
java = {b["name"]: b for b in json.loads((root / "pc" / "1.21.4" / "blocks.json").read_text())}
j2b = json.loads((root / "bedrock" / "1.21.120" / "blocksJ2B.json").read_text())
valid = {}
for s in json.loads((root / "bedrock" / "1.21.120" / "blockStates.json").read_text()):
    valid[(s["name"], json.dumps({k: v["value"] for k, v in sorted(s["states"].items())}))] = s
JAVA_RENAMED = {"grass": "short_grass"}          # our 1.18.2 names that newer Java (and the table) call differently


def java_defaults(info):
    """Default value of every property, from the block's default state id."""
    names = [s["name"] for s in info.get("states", [])]
    values = [[str(v) for v in s["values"]] if s["type"] == "enum" else
              ["true", "false"] if s["type"] == "bool" else
              [str(n) for n in range(s["num_values"])] for s in info.get("states", [])]
    offset = info["defaultState"] - info["minStateId"]
    out = {}
    for name, vals in reversed(list(zip(names, values))):       # the last property changes fastest
        out[name] = vals[offset % len(vals)]
        offset //= len(vals)
    return out


table, problems = {}, []
for name, props in p.BLOCKS:
    short = name.split(":")[1]
    our_key = name + "[" + ",".join(f"{k}={v}" for k, v in sorted((props or {}).items())) + "]"
    jname = JAVA_RENAMED.get(short, short)
    info = java.get(jname)
    if info is None:
        problems.append(f"{our_key}: not a Java 1.21.4 block")
        continue
    full = java_defaults(info)
    full.update(props or {})
    if "waterlogged" in full:
        full["waterlogged"] = "false"
    jkey = f"minecraft:{jname}[" + ",".join(f"{k}={v}" for k, v in sorted(full.items())) + "]"
    target = j2b.get(jkey)
    if target is None:
        problems.append(f"{our_key}: no Bedrock translation for {jkey}")
        continue
    bname, _, rest = target.partition("[")
    bname = bname.split(":")[1]
    wanted = {}
    for pair in filter(None, rest.rstrip("]").split(",")):
        k, v = pair.split("=")
        wanted[k] = v
    # find the exact valid Bedrock state, and take its typed values
    match = None
    for (vname, vstates), s in valid.items():
        if vname != bname or set(s["states"]) != set(wanted):
            continue
        if all(str(s["states"][k]["value"]).lower() == wanted[k].lower()
               or (s["states"][k]["type"] == "byte" and str(s["states"][k]["value"]) == {"true": "1", "false": "0"}.get(wanted[k]))
               for k in wanted):
            match = s
            break
    if match is None:
        problems.append(f"{our_key}: {target} is not a valid Bedrock 1.21 block state")
        continue
    table[our_key] = {"name": "minecraft:" + bname,
                      "states": {k: [v["type"], v["value"]] for k, v in sorted(match["states"].items())},
                      "version": match["version"]}

out = Path(__file__).resolve().parent.parent / "bedrock_blocks.json"
out.write_text(json.dumps(table, indent=0, sort_keys=True))
print(f"{len(table)} of {len(p.BLOCKS)} blocks translated to valid Bedrock states -> {out.name}")
for line in problems:
    print("  PROBLEM:", line)
sys.exit(1 if problems else 0)
