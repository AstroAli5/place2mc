"""Needs: pip install playwright, and Chrome. Measures the walk-through's movement against the game's numbers (Minecraft
Wiki, Player and Transportation pages): walking 4.317 blocks a second, sprinting 5.612, sneaking 1.295, flying 10.89,
sprint-flying 21.78, sprint-jumping about 7.13 on average, a jump 1.2522 blocks high that lands after 12 ticks.
Builds a flat test world (128 x 128 grass) and runs the physics tick by tick, without drawing.

usage: python tests/walk_physics.py"""
import sys, tempfile
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import place2mc as p
import walkdata
from playwright.sync_api import sync_playwright

ids = {name.split(":")[1]: i for i, (name, props) in enumerate(p.BLOCKS) if not props}
size, layers, floor = 128, 96, 8
vol = np.zeros((layers, size, size), dtype=np.uint8)
vol[:floor] = ids["stone"]
vol[floor] = ids["grass_block"]
page_path = Path(tempfile.mkdtemp()) / "physics.html"
walkdata.export(vol, page_path, (size // 2, floor + 1, size // 2), "Physics test")

WANT = {"walk": (4.317, 0.01), "sprint": (5.612, 0.01), "sneak": (1.295, 0.01), "fly": (10.89, 0.02),
        "sprint fly": (21.78, 0.04), "sprint jump": (7.127, 0.15), "jump height": (1.2522, 0.0005), "jump ticks": (12, 0)}
problems = []
with sync_playwright() as pw:
    b = pw.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-sandbox", "--use-angle=swiftshader",
                                                                            "--enable-unsafe-swiftshader"])
    page = b.new_page(viewport={"width": 320, "height": 200})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(page_path.as_uri())
    page.wait_for_function("!document.getElementById('go').disabled", timeout=300000)
    got = page.evaluate(f"""() => {{
        const w = window.__walk, p = w.player, ticks = n => {{ for (let i = 0; i < n; i++) w.update(1 / 20); }};
        const reset = (y) => {{ Object.assign(p, {{ x: 64.5, y: y ?? {floor + 1}, z: 120.5, vx: 0, vy: 0, vz: 0, yaw: 0,
                                    fly: false, sprint: false, onGround: false }}); w.keys.clear(); ticks(5); }};
        const speed = (keys, setup, warm, n) => {{
            reset(setup === "fly" ? 60 : undefined);
            if (setup === "fly") p.fly = true;
            keys.forEach(k => w.keys.add(k)); ticks(warm);
            const z0 = p.z; ticks(n); const v = (z0 - p.z) / (n / 20);
            w.keys.clear(); return v;
        }};
        const out = {{}};
        out.walk = speed(["KeyW"], "", 40, 60);
        out.sprint = speed(["KeyW", "ControlLeft"], "", 40, 60);
        out.sneak = speed(["KeyW", "ShiftLeft"], "", 40, 60);
        out.fly = speed(["KeyW"], "fly", 60, 40);
        out["sprint fly"] = speed(["KeyW", "ControlLeft"], "fly", 80, 40);
        out["sprint jump"] = speed(["KeyW", "ControlLeft", "Space"], "", 48, 96);    // eight jumps
        reset(); const y0 = p.y; let top = y0, n = 0;
        w.keys.add("Space"); ticks(1); w.keys.delete("Space"); n = 1;
        while (!p.onGround && n < 60) {{ ticks(1); n++; top = Math.max(top, p.y); }}
        out["jump height"] = top - y0; out["jump ticks"] = n;
        return out; }}""")
    b.close()
for k, (want, tol) in WANT.items():
    ok = abs(got[k] - want) <= tol
    print(f"{k:12s} {got[k]:9.4f}   game {want}   {'OK' if ok else 'WRONG'}")
    if not ok:
        problems.append(k)
if errors:
    problems.append("page errors: " + "; ".join(errors))
print("physics OK" if not problems else "problems: " + ", ".join(problems))
sys.exit(1 if problems else 0)
