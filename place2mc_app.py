#!/usr/bin/env python3
"""
place2mc app: pick any place on a map, press Convert, and get a ready-to-play Minecraft world.

Start it by double-clicking start-windows.bat (Windows) or start-mac.command (Mac), or run:
    python place2mc_app.py

It opens a page in your web browser. Everything runs on your own computer: the page talks only to this
program (at 127.0.0.1), and this program runs place2mc.py to do the converting.
"""
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
APP_DIR = HERE / "app"
sys.path.insert(0, str(HERE))
import place2mc  # noqa: E402  (only for the saves folder and the place search)

STATIC = {"/": ("index.html", "text/html; charset=utf-8"),
          "/leaflet/leaflet.js": ("leaflet/leaflet.js", "text/javascript"),
          "/leaflet/leaflet.css": ("leaflet/leaflet.css", "text/css")}
for _img in ("layers.png", "layers-2x.png", "marker-icon.png", "marker-icon-2x.png", "marker-shadow.png"):
    STATIC[f"/leaflet/images/{_img}"] = (f"leaflet/images/{_img}", "image/png")


class Job:
    """One conversion at a time, run as a separate program so a crash cannot take the app down."""

    def __init__(self):
        self.lock = threading.Lock()
        self.process = None
        self.log = []
        self.done = True
        self.ok = False
        self.world = None
        self.mcworld = None
        self.walk = None
        self.pictures = []

    def start(self, args):
        with self.lock:
            if not self.done:
                return False
            self.log, self.done, self.ok, self.world, self.mcworld, self.pictures = [], False, False, None, None, []
            self.walk = None
        cmd = [sys.executable, "-u", str(HERE / "place2mc.py")] + args
        self.process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                        encoding="utf-8", errors="replace", cwd=str(HERE))
        threading.Thread(target=self._read, daemon=True).start()
        return True

    def _read(self):
        for line in self.process.stdout:
            line = line.rstrip()
            with self.lock:
                self.log.append(line)
                for key, attr in (("WORLD: ", "world"), ("MCWORLD: ", "mcworld"), ("WALK: ", "walk")):
                    if line.startswith(key):
                        setattr(self, attr, line[len(key):])
                if line.startswith("Picture: ") or line.startswith("Preview picture: "):
                    self.pictures.append(line.split(": ", 1)[1])
        code = self.process.wait()
        with self.lock:
            self.done, self.ok = True, code == 0
            if code:
                self.log.append(f"(stopped with an error, code {code})")

    def status(self):
        with self.lock:
            return {"running": not self.done, "ok": self.ok, "log": self.log[-400:], "world": self.world,
                    "mcworld": self.mcworld, "walk": "/walk" if self.walk else None,
                    "pictures": [f"/picture?i={i}&t={int(time.time())}" for i in range(len(self.pictures))]}


JOB = Job()
EXTRA_ARGS = []          # for testing without internet, e.g. --osm-file and --tile-cache


def search(query):
    url = place2mc.NOMINATIM_URL + "?" + urllib.parse.urlencode({"q": query, "format": "jsonv2", "limit": 6})
    req = urllib.request.Request(url, headers={"User-Agent": place2mc.USER_AGENT, "Accept-Language": "en"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        results = json.loads(resp.read().decode("utf-8"))
    return [{"name": r.get("display_name", ""), "lat": float(r["lat"]), "lon": float(r["lon"])} for r in results]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):          # keep the console quiet
        pass

    def send(self, code, body, kind="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(url.query)
        if url.path in STATIC:
            name, kind = STATIC[url.path]
            return self.send(200, (APP_DIR / name).read_bytes(), kind)
        if url.path == "/api/status":
            return self.send(200, JOB.status())
        if url.path == "/api/info":
            saves = place2mc.saves_folder()
            return self.send(200, {"saves": str(saves) if saves else None, "offline_test": bool(EXTRA_ARGS)})
        if url.path == "/api/search":
            try:
                return self.send(200, search(query.get("q", [""])[0][:200]))
            except Exception as exc:
                reason = str(getattr(exc, "reason", "") or exc) or type(exc).__name__
                return self.send(502, {"error": f"Search failed ({reason})"})
        if url.path == "/walk":                     # the walk-through page of the last world made
            path = Path(JOB.walk) if JOB.walk else None
            if not path or path.suffix != ".html" or not path.is_file():
                return self.send(404, {"error": "No walk-through yet. Convert a place first."})
            return self.send(200, path.read_bytes(), "text/html; charset=utf-8")
        if url.path == "/picture":
            try:
                path = Path(JOB.pictures[int(query.get("i", ["-1"])[0])])
            except (ValueError, IndexError):
                return self.send(404, {"error": "no such picture"})
            if path.suffix.lower() != ".png" or not path.is_file():
                return self.send(404, {"error": "no such picture"})
            return self.send(200, path.read_bytes(), "image/png")
        return self.send(404, {"error": "not found"})

    def do_POST(self):
        url = urllib.parse.urlparse(self.path)
        length = min(int(self.headers.get("Content-Length", 0)), 100_000)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return self.send(400, {"error": "bad request"})
        if url.path == "/api/convert":
            try:
                lat, lon = float(body["lat"]), float(body["lon"])
                radius = int(body.get("radius", 300))
            except (KeyError, TypeError, ValueError):
                return self.send(400, {"error": "Pick a place on the map first."})
            if not (-85 < lat < 85 and -180 <= lon <= 180 and 50 <= radius <= place2mc.MAX_RADIUS):
                return self.send(400, {"error": "That place or size is out of range."})
            name = re.sub(r"[^\w ,.\-']", " ", str(body.get("name", "")), flags=re.UNICODE).strip()[:60]
            edition = body.get("edition", "java")
            if edition not in ("java", "bedrock", "both"):
                return self.send(400, {"error": "Unknown edition."})
            args = [f"--center={lat:.6f},{lon:.6f}", "--radius", str(radius),
                    "--new-world", name or f"place2mc {lat:.4f}, {lon:.4f}", "--edition", edition]
            if not body.get("hills", True):
                args.append("--flat")
            if body.get("pictures", True):
                args.append("--screenshots")
            if not JOB.start(args + EXTRA_ARGS):
                return self.send(409, {"error": "A conversion is already running."})
            return self.send(200, {"started": True})
        if url.path == "/api/add-to-bedrock":
            mcworld = JOB.mcworld
            if not mcworld or not Path(mcworld).exists():
                return self.send(404, {"error": "No Bedrock world yet."})
            if sys.platform.startswith("win"):
                try:
                    os.startfile(mcworld)      # noqa: S606 (Windows hands a .mcworld to Minecraft, which imports it)
                    return self.send(200, {"imported": True})
                except OSError:                # Minecraft Bedrock is not installed on this PC
                    pass
            return self.send(200, {"imported": False, "opened": open_folder(Path(mcworld).parent)})
        if url.path == "/api/open-folder":
            folder = JOB.world if body.get("which") == "world" else str(place2mc.saves_folder() or "")
            if folder and Path(folder).exists():
                target = Path(folder) if Path(folder).is_dir() else Path(folder).parent
                if open_folder(target):
                    return self.send(200, {"opened": str(target)})
                return self.send(500, {"error": f"Could not open the folder. It is here: {target}"})
            return self.send(404, {"error": "Folder not found."})
        return self.send(404, {"error": "not found"})


def open_folder(folder):
    """Show a folder in Explorer, Finder or the Linux file manager. Returns False if that is not possible."""
    try:
        if sys.platform.startswith("win"):
            os.startfile(str(folder))                     # noqa: S606 (our own folder)
        else:
            subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(folder)])
        return True
    except OSError:
        return False


def main():
    global EXTRA_ARGS
    import argparse
    ap = argparse.ArgumentParser(description="The place2mc app: pick a place on a map and convert it.")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true", help="do not open the browser")
    ap.add_argument("--extra", nargs=argparse.REMAINDER, default=[],
                    help="extra arguments passed to place2mc.py (for testing, e.g. --osm-file map.osm)")
    args = ap.parse_args()
    EXTRA_ARGS = args.extra
    server = None
    for port in range(args.port, args.port + 20):
        try:
            server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
            break
        except OSError:
            continue
    if server is None:
        sys.exit("Could not start the app: no free port.")
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    print(f"place2mc is running at {url}  (close this window to stop it)", flush=True)
    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
