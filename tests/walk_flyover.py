"""Needs: pip install playwright, and Chrome. A flight round the ALA campus in the walk-through (headless Chrome), saved as frames for a GIF.
python flyover.py WALK.html OUT.npy CX CZ RADIUS HEIGHT FRAMES"""
import math
import sys

import numpy as np
from PIL import Image
from playwright.sync_api import sync_playwright

path, out = sys.argv[1], sys.argv[2]
cx, cz, radius, height, count = (float(v) for v in sys.argv[3:8])
count = int(count)
frames = []
with sync_playwright() as p:
    b = p.chromium.launch(executable_path="/usr/bin/google-chrome",
                          args=["--no-sandbox", "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"])
    page = b.new_page(viewport={"width": 720, "height": 405})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto("file://" + path)
    page.wait_for_function("!document.getElementById('go').disabled", timeout=300000)
    page.evaluate("window.__walk.play()")
    page.add_style_tag(content="#hud, #cross, #help, #buttons, #stick, .touch { display: none !important; }")
    page.evaluate("window.__walk.setTime(2); window.__walk.setShaders(2)")
    ground = page.evaluate(f"""() => {{ const w = window.__walk, p = w.player; let y = 300;
        while (!w.collides({cx}, y - 0.5, {cz}) && y > 1) y -= 0.5; return y; }}""")
    for i in range(count):
        a = 2 * math.pi * i / count * 0.5 + 0.6          # half a circle round the campus
        x, z = cx + radius * math.cos(a), cz + radius * math.sin(a)
        yaw = math.atan2(cx - x, -(cz - z))                # look at the middle (yaw 0 looks north, -z)
        dist = math.hypot(cx - x, cz - z)
        pitch = -math.atan2(height - 4, dist)
        page.evaluate(f"""() => {{ const p = window.__walk.player; p.fly = true; p.vy = 0;
            p.x = {x}; p.z = {z}; p.y = {ground + height}; p.yaw = {yaw}; p.pitch = {pitch}; }}""")
        page.evaluate("window.__walk.buildAll()")          # every chunk in view, before the picture
        page.wait_for_timeout(2200 if i else 6000)         # and a frame or two for the shadow map
        page.screenshot(path="/tmp/fly_frame.png")
        frames.append(np.asarray(Image.open("/tmp/fly_frame.png").convert("RGB")))
        print(i, flush=True)
    print("page errors:", errors or "none")
    b.close()
np.save(out, np.stack(frames))
