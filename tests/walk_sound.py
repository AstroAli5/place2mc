"""Needs: pip install playwright, and Chrome. Walk in a walk-through with the sound on (headless Chrome): count the
footsteps, piano notes and bird chirps, switch to night for the crickets, and record what the page plays to
/tmp/walk_sound.webm (ffmpeg turns it into a WAV or M4A). Fails on any page error.

usage: python tests/walk_sound.py "WALK.html" [X Z]     (X Z: where to stand first, in blocks from the corner)"""
import sys, json, base64
from playwright.sync_api import sync_playwright
path = sys.argv[1]
pose = [float(v) for v in sys.argv[2:4]] if len(sys.argv) > 3 else None
with sync_playwright() as p:
    b = p.chromium.launch(executable_path="/usr/bin/google-chrome",
                          args=["--no-sandbox", "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist",
                                "--autoplay-policy=no-user-gesture-required"])
    page = b.new_page(viewport={"width": 960, "height": 540})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.goto("file://" + path)
    page.wait_for_function("!document.getElementById('go').disabled", timeout=300000)
    page.click("#go")
    if pose:
        page.evaluate(f"""() => {{ const p = window.__walk.player; p.x = {pose[0]}; p.z = {pose[1]};
            p.y = 300; p.vy = 0; while (!window.__walk.collides(p.x, p.y - 0.5, p.z) && p.y > 1) p.y -= 0.5; }}""")
        page.wait_for_timeout(6000)
    page.wait_for_timeout(1500)
    # record the page's sound: tap the master output into a recorder
    page.evaluate("""() => {
        const s = window.__walk.sound, ctx = s.ctx, dest = ctx.createMediaStreamDestination();
        s.master.connect(dest);
        window.__chunks = []; const rec = new MediaRecorder(dest.stream);
        rec.ondataavailable = e => window.__chunks.push(e.data); rec.start(); window.__rec = rec; window.__t0 = ctx.currentTime;
    }""")
    page.evaluate("window.__walk.setTime(2)")
    for i in range(14):                       # walk forward, stepping the physics like the game loop does
        page.evaluate("""() => { const w = window.__walk; w.keys.add('KeyW');
            for (let k = 0; k < 10; k++) w.update(0.05); w.keys.delete('KeyW'); }""")
        page.wait_for_timeout(700)
    stats = page.evaluate("""() => { const s = window.__walk.sound;
        return { state: s.ctx.state, steps: s.steps, notes: s.notes, chirps: s.chirps, waterNear: +(s.waterNear || 0).toFixed(2),
                 water: +s.water.gain.value.toFixed(3), wind: +s.wind.gain.value.toFixed(3) }; }""")
    print("day:", stats)
    night_at = page.evaluate("() => { window.__walk.setTime(4); return window.__walk.sound.ctx.currentTime - window.__t0; }")
    print("night from second", round(night_at, 1))
    page.wait_for_timeout(4000)
    print("night:", page.evaluate("() => ({ chirps: window.__walk.sound.chirps, notes: window.__walk.sound.notes })"))
    data = page.evaluate("""async () => { window.__rec.stop(); await new Promise(r => setTimeout(r, 500));
        const buf = await new Blob(window.__chunks).arrayBuffer(); let s = '';
        const u = new Uint8Array(buf); for (let i = 0; i < u.length; i++) s += String.fromCharCode(u[i]); return btoa(s); }""")
    open("/tmp/walk_sound.webm", "wb").write(base64.b64decode(data))
    print("hud:", page.inner_text("#hud").split("\n")[-1])
    print("errors:", errors or "none")
    b.close()
    if errors or stats["steps"] < 5 or stats["notes"] < 5 or stats["state"] != "running":
        sys.exit("sound check FAILED")
    print("sound OK")
