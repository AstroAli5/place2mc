"""Needs: pip install playwright, and Chrome. Fly forward for 2 seconds of game time at each speed level (- and = keys)
and print how far the player got."""
import sys
from playwright.sync_api import sync_playwright
path = sys.argv[1]
with sync_playwright() as p:
    b = p.chromium.launch(executable_path="/usr/bin/google-chrome",
                          args=["--no-sandbox", "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"])
    page = b.new_page(viewport={"width": 1280, "height": 720})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto("file://" + path)
    page.wait_for_function("!document.getElementById('go').disabled", timeout=300000)
    page.evaluate("window.__walk.play()")
    page.wait_for_timeout(500)
    for level in "1234":                                # "-" back to normal, then "=" for each faster speed
        if level == "1":
            for _ in range(3):
                page.keyboard.press("Minus")
        else:
            page.keyboard.press("Equal")
        moved = page.evaluate("""() => {
            const w = window.__walk, p = w.player;
            p.fly = true; p.y += 30; p.vy = 0;              // fly above the roofs so nothing is in the way
            const x0 = p.x, z0 = p.z;
            w.keys.add("KeyW");
            for (let i = 0; i < 40; i++) w.update(0.05);
            w.keys.delete("KeyW");
            return Math.hypot(p.x - x0, p.z - z0);
        }""")
        print(f"speed {level}: {moved:.1f} blocks in 2 s of flying")
    page.wait_for_timeout(1500)
    print("hud:", page.inner_text("#hud").replace("\n", " | "))
    page.screenshot(path="/tmp/walk_speed.png")
    print("page errors:", errors or "none")
    b.close()
