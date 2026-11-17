# place2mc

Ali's personal project: a tool that turns any real-world place into Minecraft. He picks a place on a map in a
local web app and presses Convert; the tool downloads its map from OpenStreetMap and its hills from AWS
Terrain Tiles, builds them at 1 block = 1 metre, and makes a ready-to-play Java world and/or a Bedrock
.mcworld file. He plays both editions. He wants the result to feel like walking around his own Minecraft,
with as much detail as Arnis. Ali is a student; explain what you changed in plain words, and show results
(pictures, test output) rather than describing them.

If the fable-playbook skill is available, follow it on every task.

## Files

- `place2mc_app.py` + `app/index.html`: the app. A small local web server (Python standard library only) with
  a Leaflet map (bundled in `app/leaflet`, BSD-2). Runs place2mc.py as a subprocess and shows the log, the
  pictures and buttons (Add to Bedrock, show folders). `start-windows.bat` / `start-mac.command` start it.
- `place2mc.py`: the converter (needs only `numpy` and `pillow`). Map data -> 2D surface map (`build_map`, which
  also leaves fences, rails and benches in `build_map.details`) -> 3D block volume (`build_volume`) -> Java
  region files (`write_world`) and level.dat (`write_level_dat`), new world in the saves folder by default.
- `bedrock.py`: Bedrock export. Own LevelDB writer (raw-deflate blocks, like the game), chunk format 42,
  sub-chunk format 9, level.dat with the field set of a Bedrock 1.21.120 save. Block names/states come from
  `bedrock_blocks.json`, made by `tests/make_bedrock_table.py`. **Regenerate that table whenever a block is
  added to place2mc.py**, or Bedrock export stops with a message.
- `walkdata.py` + `app/walk.html`: the walk-through. walkdata writes `Walk around <name>.html` into the world
  folder: the viewer with the world inside it (block columns as runs, gzip, base64; texture atlas; block table).
  The viewer is plain WebGL2 (no libraries): chunk meshes with the game's sky light, face shading and smooth
  lighting, see-through leaves/plants, water and glass, fog, clouds; WASD, mouse (pointer lock, or drag when
  a page viewer refuses it), jump, auto-jump, step-up, sprint, fly, collisions; touch controls on phones.
  Test it with headless Chrome: `--use-angle=swiftshader --enable-unsafe-swiftshader` (about 6 fps there).
- `textures/pixel-perfection-ce/`: the Minecraft-style textures used when Minecraft Java is not installed
  (CC BY-SA 4.0, keep CREDITS.md and LICENSE.md with them). Add any texture a new block needs from
  github.com/Athemis/PixelPerfectionCE, or give it a look-alike in mcrender.OLD_TEXTURE_NAMES.
- `overture.py`: Overture Maps (newest release from the S3 listing, PMTiles v3 over HTTP range requests, our own
  MVT decoder): buildings OSM lacks (z14 `building` layer; ones whose `@geometry_source` is OpenStreetMap are
  skipped but give their height/num_floors to the OSM building via `sources[].record_id`), and ESA WorldCover
  land cover (z13 `land_cover` layer of base.pmtiles) as `place2mc:land_cover` areas. build_map skips an
  Overture building where an OSM building already covers 30% of it. Checked by `tests/check_overture.py`
  against mapbox-vector-tile and pmtiles (pip), and on ALA's real tiles by running it in Ali's browser with Pyodide.
- `mcrender.py`: draws pictures the way the game does at noon (sky light levels, face shading, smooth
  lighting, fog by render distance, clouds, block shapes for stairs/slabs/doors/fences/walls/rails/lanterns).
  Uses the real textures from the user's own Minecraft Java install or a resource pack when found, built-in
  textures otherwise. Nothing from the game is copied anywhere.
- `tests/`: see "Check after every change".

## Run

```
pip install numpy pillow
python place2mc_app.py                                   # the app
python place2mc.py "Needham, Massachusetts" --edition both --screenshots
```

Useful options: `--radius 400`, `--center LAT,LON`, `--osm-file map.osm` (offline), `--flat` (no hills),
`--saves DIR`, `--world DIR` (build into an existing world), `--preview-only`, `--textures PATH`.

## Check after every change

```
pip install anvil-parser2 nbtlib
python tests/get_mcdata.py mcdata                                   # once
python tests/make_bedrock_table.py mcdata                           # after adding or changing a block
python tests/check_blocks.py mcdata 1.18 1.21.4                     # names, states, shapes vs the game
python tests/check_details.py out.png                               # fences, walls, rails, zebras, benches
python tests/fake_tiles.py TILES LAT,LON 608                        # test hills without internet
python place2mc.py --osm-file MAP --center LAT,LON --radius 300 --saves OUT --tile-cache TILES --edition both
python tests/verify_world.py MAP LAT,LON 300 "OUT/<name>" TILES     # Java, read back with 2 libraries
python tests/verify_bedrock.py MAP LAT,LON 300 "OUT/<name>/<name>.mcworld" mcdata [ARNIS.mcworld] TILES
```

For a second, independent check of a Bedrock database, `ldbcheck` (a 60-line Rust program on rusty-leveldb,
the library Arnis uses) was built in the first session; rebuild it if needed. Then look at
`place2mc_preview.png` and the `--screenshots` pictures before saying a change is done.

## Rules

- Accuracy first: streets, buildings and water must sit where they are in the real place, at real size.
- Java chunks are stamped 1.18.2 (data version 2975) so newer versions upgrade them; level.dat is written in
  the 1.21.4 format, so new worlds need Java 1.21.4+. Only use block names that exist in 1.18.2; the game
  renames old names itself (`grass` -> `short_grass`). The volume is uint8: fewer than 256 block states.
- Fences and walls store their connections (the game does not recompute them on load): use the FENCES /
  GARDEN_WALLS tables. Leaves must stay persistent=true or they decay.
- New worlds are void around the build, so water on the edge is walled in with invisible barrier blocks.
- Keep `--osm-file` and `--flat` working, so everything can be tested offline.
- Never say "tested in Minecraft" unless Ali says he opened the world. Say what was checked and what was not.

## State (v0.9)

v0.9: Ali: "make me move faster", "consider the heights", "even the smallest details". Walk-through speed keys 1-4
(×1/×2/×4/×8, touch button). Interiors (bookshelf, spruce_slab top as desk, spruce stair chairs, hanging lanterns),
car parks (white bay lines every 3 blocks, 2x4 concrete cars with a glass top), traffic lights (highway=traffic_signals:
post + lime/yellow/red concrete), building=roof/carport and covered=yes footways as roofs on pillars (spec field 11
"open": no doors, no planting, no furniture). Heights: building:levels x 3 m for homes, x 4 m for others; unknown
heights 1-3 floors; --tags "inside" rules take min_area so sheds keep their size. check_blocks now uses the game's
real default state (it found the standing lantern drawn 1 px too high). check_details covers all of these.

v0.8: Ali wants "any place perfectly converted". Overture buildings + land cover (see overture.py), unlabelled
buildings under 40 m2 become sheds, over 150 m2 in an industrial zone become industrial; the walk-through
credits Overture and ESA when they were used. ALA: 237 buildings instead of 11, 1247 trees instead of 357.
Offline testing: `--overture-file` (and `--save-overture`); the checks take OVERTURE=file like TAGS=file.


v0.7: Ali said ALA "is not ALA at all". From ALA's campus photos (africanleadershipacademy.smugmug.com) and
an aerial view: red face brick or white walls, dark green metal pitched roofs, green covered walkways, many
trees, inside an industrial park of unmapped warehouses. Added: building/roof colour and material tags
(`wall_from_tags`, `roof_from_tags`, hue-weighted `nearest_block`), roof materials dark prismarine (green),
red nether bricks (dark red), polished andesite (grey); `--tags FILE` (`apply_tag_file`) to add tags the map
lacks; campus grounds drawn as park; `examples/ala/`. The bundled pack draws dark prismarine with green
terracotta (BUNDLED_STANDINS), since the pack's own is grey-blue. Only 11 ALA buildings are on OpenStreetMap;
covered walkways and the missing buildings would need map data (or Ali adding them on openstreetmap.org).
Do not trace buildings from Esri or Google imagery into the repo (licences).


v0.6 (asked for by Ali: "walk through it like I'm in the game", "look like Minecraft", more detail): the
walk-through, the bundled Pixel Perfection CE textures, doors every 45 m of wall, lamps along footpaths,
bushes and flowers along walls on lawns. Ali has no Minecraft on the computer he uses, so the walk-through is
how he sees the result; ALA's walk-through was published as a claude.ai page for him.


Checked in a cloud workspace with no Minecraft, no access to OpenStreetMap's live servers or the elevation
servers: on map files of Monaco and West Oakland and a made-up map, flat and on test hills, the Java world
reads back with nbtlib and anvil-parser2, the Bedrock world reads back block for block with our reader and
with rusty-leveldb, every block state is valid for Java 1.18 / 1.21.4 and Bedrock 1.21.120, the Bedrock
level.dat has the same fields and types as a working Arnis world, and the app works end to end in headless
Chrome. Not yet checked: opening worlds in the real game (either edition), live downloads, real elevation,
a real game .jar for textures, the Windows/Mac start scripts on a real computer.

Real place tried so far: African Leadership Academy (Ali's school), map data fetched through Ali's Chrome
because the cloud workspace cannot reach map servers. That run found and fixed: coordinates south of the
equator broke the command line; the street picture's camera could stand on a grey roof; industrial land was
a gravel desert; campus outlines (amenity=college/school) were not downloaded; campus buildings were guessed
5-7 storeys. Overpass was over capacity that day, so downloads now fall back to api.openstreetmap.org.

Known limits: sea 2 blocks deep; a few doors on steep slopes open onto a drop; big hilly areas need ~2 GB of
memory; rails and fences are flat-ground objects (no bridges or tunnels at height); no interiors, no signs.

## Similar tools (for ideas)

- Arnis (github.com/louis-e/arnis, Apache-2.0, Rust, ~110k lines of element code) does the same job in far
  more detail: interiors, street signs, traffic lights, cars, trains, bridges, stations, ESA WorldCover land
  cover, Overture buildings. Its Bedrock writer was the reference for ours. If you adapt code from Arnis,
  keep its Apache-2.0 notice.
- Terra++ (Build The Earth mod): elevation, biomes, tree cover and ocean depth from public data; /tpll.

Next ideas, roughly in order: whatever breaks in Ali's first real test; inner walls between rooms;
street-name signs; bridges and tunnels at the right height; light blocks for night;
building region by region for bigger areas.
