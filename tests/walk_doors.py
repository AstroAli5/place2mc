"""Doors in the walk-through open and shut like the game's: right click a door (or tap the place button) and both
halves turn open, you can walk through, and a second click shuts it again.

    python tests/walk_doors.py "OUT/<name>/Walk around <name>.html"
"""
import sys
from playwright.sync_api import sync_playwright

path = sys.argv[1]
problems = []
with sync_playwright() as pw:
    b = pw.chromium.launch(executable_path="/usr/bin/google-chrome",
                           args=["--no-sandbox", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
    page = b.new_page(viewport={"width": 800, "height": 450})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto("file://" + path)
    page.wait_for_function("!document.getElementById('go').disabled", timeout=300000)
    page.click("#go")
    found = page.evaluate("""() => {
        const w = window.__walk, W = w.world, B = W.info.blocks, p = w.player;
        const x0 = Math.floor(p.x), z0 = Math.floor(p.z);
        for (let r = 1; r < 200; r++) for (let dz = -r; dz <= r; dz++) for (let dx = -r; dx <= r; dx++) {
          if (Math.max(Math.abs(dx), Math.abs(dz)) !== r) continue;
          const x = x0 + dx, z = z0 + dz;
          for (let y = 1; y < W.H - 1; y++) {
            const b = W.get(x, y, z), info = B[b];
            if (!info || !info.name.endsWith('_door') || info.props.half !== 'lower' || info.props.open !== 'false') continue;
            const f = { north: [0, -1], south: [0, 1], west: [-1, 0], east: [1, 0] }[info.props.facing];
            const sx = x - f[0] * 2, sz = z - f[1] * 2;               // stand two blocks in front of it
            if (W.get(sx, y, sz) || W.get(sx, y + 1, sz) || !W.get(sx, y - 1, sz)) continue;
            return { x, y, z, sx, sz, facing: info.props.facing, name: info.name };
          }
        }
        return null; }""")
    print("door:", found)
    if not found:
        sys.exit("no door with room to stand in front of it")
    yaw = {"north": 0, "east": 1.5708, "south": 3.14159, "west": -1.5708}[found["facing"]]
    state = page.evaluate(f"""() => {{
        const w = window.__walk, W = w.world, p = w.player, B = W.info.blocks, d = {found};
        p.fly = false; p.x = d.sx + 0.5; p.z = d.sz + 0.5; p.y = d.y; p.vx = p.vy = p.vz = 0; p.yaw = {yaw}; p.pitch = 0;
        const out = [];
        const look = () => {{ const a = w.aimed(); return a ? [a.x, a.y, a.z] : null; }};
        const halves = () => [B[W.get(d.x, d.y, d.z)].props.open, B[W.get(d.x, d.y + 1, d.z)].props.open];
        const shut = W.collide[W.get(d.x, d.y, d.z)][0];                         // where the shut panel stands
        const through = () => !w.collides(d.x + (shut[0] + shut[3]) / 2, d.y, d.z + (shut[2] + shut[5]) / 2);
        out.push({{ aim: look(), halves: halves(), through: through() }});
        w.placeBlock();
        out.push({{ aim: look(), halves: halves(), through: through() }});
        w.placeBlock();
        out.push({{ aim: look(), halves: halves(), through: through() }});
        return {{ out, doors: w.sound.doors || 0, saved: w.myBlocks.size }}; }}""")
    page.wait_for_timeout(1500)
    label = page.evaluate("() => document.getElementById('place').textContent")
    b.close()

shut, opened, shut_again = state["out"]
print("closed:", shut, "\nafter a right click:", opened, "\nafter another:", shut_again, "\nlabel:", repr(label))
if shut["halves"] != ["false", "false"] or shut["through"]:
    problems.append("the door should start shut and block the way")
if opened["halves"] != ["true", "true"]:
    problems.append(f"a right click should open both halves, got {opened['halves']}")
if not opened["through"]:
    problems.append("an open door should let you through")
if shut_again["halves"] != ["false", "false"] or shut_again["through"]:
    problems.append("a second right click should shut it")
if state["saved"]:
    problems.append("opening a door should not count as your building")
if "the door" not in label:
    problems.append("no hint to open the door when looking at it")
if errors:
    problems.append(f"page errors: {errors[:3]}")
for line in problems:
    print("  PROBLEM", line)
print("doors OK" if not problems else f"{len(problems)} problems")
sys.exit(1 if problems else 0)
