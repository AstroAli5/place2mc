"""Needs: pip install playwright, and Chrome. Open a walk-through in headless Chrome, stand somewhere, and save a picture for each time of day and shader level.
python shots_walk.py WALK.html OUT_DIR [x z yaw pitch] ['times' list like 2,3,4] [shader levels like 2,0]"""
import sys, json
from pathlib import Path
from playwright.sync_api import sync_playwright
path, out = sys.argv[1], Path(sys.argv[2]); out.mkdir(parents=True, exist_ok=True)
pose = [float(v) for v in sys.argv[3:7]] if len(sys.argv) > 6 else None
times = [int(v) for v in sys.argv[7].split(",")] if len(sys.argv) > 7 else [2]
levels = [int(v) for v in sys.argv[8].split(",")] if len(sys.argv) > 8 else [2]
lift = float(sys.argv[9]) if len(sys.argv) > 9 else 0
with sync_playwright() as p:
    b = p.chromium.launch(executable_path="/usr/bin/google-chrome",
                          args=["--no-sandbox", "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"])
    page = b.new_page(viewport={"width": 1280, "height": 720})
    logs = []
    page.on("console", lambda m: logs.append(m.text) if m.type in ("error", "warning") else None)
    page.on("pageerror", lambda e: logs.append("PAGE ERROR " + str(e)))
    page.goto("file://" + path)
    page.wait_for_function("!document.getElementById('go').disabled", timeout=300000)
    page.evaluate("window.__walk.play()")
    if pose:
        page.evaluate(f"""() => {{ const p = window.__walk.player; p.x = {pose[0]}; p.z = {pose[1]};
            p.y = 300; p.fly = false; p.vy = 0; while (!window.__walk.collides(p.x, p.y - 0.5, p.z) && p.y > 1) p.y -= 0.5;
            p.yaw = {pose[2]}; p.pitch = {pose[3]}; if ({lift} > 0) {{ p.fly = true; p.y += {lift}; }} }}""")
    page.wait_for_timeout(9000)               # let the chunks around the new spot build
    for lv in levels:
        for t in times:
            page.evaluate(f"window.__walk.setTime({t}); window.__walk.setShaders({lv})")
            page.wait_for_timeout(2500)
            name = out / f"walk_t{t}_g{lv}.png"
            page.screenshot(path=str(name))
            print("picture", name, page.inner_text("#hud").split("\n")[-1])
    print("console:", logs[:8] or "clean")
    b.close()
