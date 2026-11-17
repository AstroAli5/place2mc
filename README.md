# place2mc

Pick any real place on a map, press Convert, and walk around it in Minecraft. place2mc downloads the streets,
buildings, water, parks and trees from OpenStreetMap and the hills from public elevation data, and builds them
at 1 block = 1 metre. It makes a ready-to-play Java world, a Bedrock `.mcworld` file, or both.

![Standing in a street of the converted town](docs/street.jpg)

| | |
|---|---|
| ![Rooftops](docs/rooftops.jpg) | ![Zebra crossing](docs/zebra-crossing.jpg) |
| ![Fences, garden walls, hedge and railway](docs/fences-walls-rails.jpg) | ![Fenced garden](docs/garden-fence.jpg) |

These pictures are made by place2mc itself (`mcrender.py`), drawn the way the game draws a world at noon. On a
computer with Minecraft Java installed it uses the game's own textures from your install; the pictures above
use the built-in look-alike textures.

## Use it

1. Install Python 3 from [python.org](https://www.python.org/downloads/) (on Windows, tick "Add python.exe to PATH").
2. Download this repository (green **Code** button, **Download ZIP**) and unzip it.
3. Double-click **start-windows.bat** (Windows) or **start-mac.command** (Mac). A page opens in your browser.
4. Search for a place or click on the map, choose the size and the edition, and press **Convert**.
5. When it says Done:
   - **Java**: open Minecraft, go to Singleplayer, and the new world is in the list. You start on a street in the middle.
   - **Bedrock**: press **Add to Bedrock** (on Windows, Minecraft opens and imports the world), or send the
     `.mcworld` file to your phone or tablet and open it there.

New worlds are Creative, Peaceful, always noon, with no mobs, and empty around the built square.

| Edition | Works with |
|---|---|
| Java | 1.21.4 or newer for new worlds; 1.18 to 1.21.3 with `--world` (build into a Superflat world you made) |
| Bedrock | 1.21.120 or newer on Windows, Android, iPhone and iPad (anything that opens a `.mcworld` file) |

## Command line

```
pip install numpy pillow
python place2mc.py "Needham, Massachusetts"                       # new Java world
python place2mc.py "Needham, Massachusetts" --edition both --screenshots
python place2mc.py --center 51.5007,-0.1246 --radius 500 --edition bedrock
```

Other options: `--osm-file map.osm` (work offline from a saved map), `--flat` (no hills), `--preview-only`,
`--textures PATH` (a game .jar or resource pack for the pictures). `python place2mc.py --help` lists them all.

## What it builds

- Hills from elevation data, with dirt over stone, and the sea, lakes and rivers at their real places
- Buildings at their mapped height (or a guess from their type and size): walls with windows and floors
  inside, shop windows on the street side, doors, flat roofs with rooftop units, sloped roofs with eaves
- Roads at their real width with sidewalks, centre lines, zebra crossings, street lamps, and half slabs where
  a street climbs
- Fences, garden walls and hedges, railway and tram tracks, benches
- Parks, forests, lawns, street trees and mapped trees, grass and flowers, and the matching biomes

## How it was checked, and what was not

The worlds were read back block by block with independent readers: nbtlib and anvil-parser2 for Java,
and for Bedrock a separate Python reader plus rusty-leveldb (the database library Arnis uses). Every block
name and state was checked against the game's own block lists from PrismarineJS/minecraft-data for Java 1.18,
1.21.4 and Bedrock 1.21.120. The app was tested end to end in a browser.

Not checked yet: opening the worlds in the real game (the computer this was built on has no Minecraft),
live map and elevation downloads (it had no access to those servers; tests used saved map files and test
elevation tiles), and the game's own textures for the pictures.

## Credits

- Map data © [OpenStreetMap](https://www.openstreetmap.org/copyright) contributors (ODbL). Worlds built from
  it contain OpenStreetMap data, so credit it if you share them.
- Elevation: Terrain Tiles on AWS (Mapzen / Tilezen), from SRTM, USGS and other public sources.
- Map in the app: [Leaflet](https://leafletjs.com) (BSD-2-Clause, licence in `app/leaflet/LICENSE`).
- [Arnis](https://github.com/louis-e/arnis) (Apache-2.0) does the same job in more detail; its Bedrock worlds
  were the reference for the Bedrock file layout and settings used here.
- Block lists for the tests: [PrismarineJS/minecraft-data](https://github.com/PrismarineJS/minecraft-data).
- No Minecraft files are included or copied. Not an official Minecraft product; not approved by or associated
  with Mojang or Microsoft.
