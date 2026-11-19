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
- `mcrender.py`: draws pictures by casting rays through the blocks (block shapes for stairs/slabs/doors/fences/
  walls/rails/lanterns, smooth lighting, fog by render distance, clouds). With a `Look` (the default in
  `standard_views`): the real sun for the place and date, shadow rays towards it (leaves cast dappled shadows),
  lantern light (`block_light`), water mirroring the sky with a sun glint, sun disc, stars and moon, the same
  film curve and colours as the walk-through. `look=None` / `--plain-pictures`: the game's plain noon light.
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
python tests/check_sun.py                                           # sun position, shadows in the pictures
python tests/walk_sound.py "OUT/<name>/Walk around <name>.html"     # footsteps, music, birds; no page errors
python tests/walk_physics.py                                        # the walk-through moves like the game
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

## State (v1.8)

v1.8: Ali: "try on Franklin & Marshall College, every detail, no mistakes in heights, search every website" (he will
send it to F&M). Measured heights from USGS 3DEP LiDAR (public domain): app/lidar.js reads the Entwine Point Tiles on
usgs-lidar-public (laz-perf, Apache-2.0, for LAZ) in a browser and keeps: the ground (class 2/9 minimum, gaps filled,
every 2 m), the roof above the ground in every 1 m cell of every mapped outline plus one ring (highest lone echo, else
highest first echo), patches of hard roof the map lacks (>= 30 m2, >= 3 m, >= 75% lone echoes; >= 4 m2 when touching
an outline: towers/bays), and tree tops (local maxima of the canopy, crown = where it falls below 45%). The workspace
cannot reach the data or receive it from Chrome (the extension blocks base64 output), so app/fetch-template.html +
tools/make_fetch_page.py make one page (decoder inside) that saves place2mc-data.json (format place2mc-browser-1: map
rows + lidar); it ran in Ali's browser through raw.githack.com and the file came back via the device bridge
(Downloads). place2mc: --lidar FILE (load_lidar, prepare_lidar: place2mc:lidar=no tag skips buildings built after the
scan, add_scanned_buildings adds/attaches the map's missing buildings), ground from the scan (blur 1), measured_roofs
(clean_roof_cells: thin/speckled high cells = branches -> gaps, compact high blocks = towers kept unsmoothed, 3x3 median
elsewhere; gaps from side neighbours), draw_measured_roofs (walls where a roof drops >= 3, roof blocks on gentle
steps, stairs at the foot of each 1-block step), measured trees only (clean_trees: crown >= 1 m and >= 8% of height,
lower tops inside a taller crown dropped; trunk under the top, conifer if crown < 13% of height and >= 8 m).
tests/check_lidar.py, tests/lidar_js_test.mjs, tests/fetch_page_test.py; verify_world/verify_bedrock take LIDAR=file.
F&M (examples/fandm): centre 40.04966,-76.31705 radius 768 (the campus relation 7735060 fits with the edge ramp
outside it), 2019 scan PA_Northcentral_B1_2019 (11.4 M points in the square, classes 1/2 only), 866 mapped + ~500
scanned buildings, ~12,000 trees; 93.5% of roof cells of the 86 named buildings within 1 m of the scan (0.6 m mean);
Old Main's tower 36 blocks (scan 36.0 m, archives 121 ft = 36.9 m). Tags: every campus building brick (Old Main and
Steinman checked on photos), sloped roofs slate #505055 (-> deepslate tiles; #56575c gave dark prismarine), Steinman
white flat roof, Winter Visual Arts Center (2020, after the scan) 3 storeys 15 m white/glass from published facts.
Fixed: inland water with hills crashed (real[sea].max() on no sea); world names keep & ' ( ).
Also: --spawn LAT,LON[,FACING] (choose_spawn near a point; walk.html info.spawnYaw); the walk-through credits the
USGS scan. F&M delivered to Ali's Downloads (Java zip, .mcworld, walk-through) and published as a claude.ai page; the
walk-through is in examples/fandm (raw.githack.com link in its README). Not checked: the real game, leaf season (trees
are summer-green), colours of buildings other than Old Main and Steinman (assumed brick), the interiors (made up).

## State (v1.7)

v1.7: Ali: "reverse engineer whole Minecraft" + github.com/Graphify-Labs/graphify. Read as: learn how the game behaves
from public sources and re-create it in our own code; never decompile or copy Mojang code or assets (the repo is
public; the workspace cannot reach Mojang's downloads, Maven or the wiki anyway). walk.html movement is now the
game's tick physics (TICK = 1/20, tick(): keys x0.98, push 0.1 ground / x1.3 sprint, 0.02 air, 0.05 fly / x2,
friction 0.546 ground, 0.91 air/fly, 0.8 water; vy = (vy - 0.08) * 0.98, jump 0.42 + sprint push 0.2, jumpDelay 10,
fly vy += +-0.15 then x0.6; sneak Shift x0.3, eye 1.27); velocities are blocks per tick; drawing interpolates
between ticks (before{}); view bobbing (V), FOV x1.15 sprinting, x1.1 flying. tests/walk_physics.py measures
walk/sprint/sneak/fly/sprint-fly/sprint-jump/jump height/jump ticks on a flat world: all match the wiki.
Creative mode: World.set + edits (applied when a chunk is decoded again), aimed() (DDA, reach 5), outline (line
shader), left break / right place / middle pick, hotbar of 9 blocks (1-9, wheel), touch buttons, rebuild() of the
chunk at once and its 8 neighbours via `stale`; kept in localStorage as block name+props, "Undo my building".
Speed keys moved from 1-4 to - and = (1-9 are the hotbar, as in the game). Map credit moved to the top right.
graphify (pip install graphifyy; `graphify update DIR`, tree-sitter, no LLM for code) made docs/code-map.md; on
Arnis's src it finds 23,104 nodes / 63,765 edges (useful to find how Arnis does something).
README redone for v1.7 ("put all of this on GitHub, make it look cool, every detail, the Spotify thing"): picture
gallery, What's inside, Controls table, physics table, docs/creative.gif (a house going up block by block, made
in ALA's walk-through), Sound and Spotify steps, Real details, What's new table. docs/social-preview.png
(1280x640) is the repo's social preview. The About description, website (ALA walk-through on raw.githack.com) and
14 topics were set through Ali's Chrome (the workspace proxy refuses repository settings writes), and
social-preview.png was uploaded there too; gh api repos/AstroAli5/place2mc reads them back.

## State (v1.6)

v1.6: Ali: "link it with Spotify so I can play my own songs" + "more realistic, search how, edit without asking,
update GitHub". Spotify: green button on the walk-through's start screen, paste a link (open.spotify.com/... or
spotify:type:id, any of track/album/playlist/artist/episode/show) -> an embed iframe from open.spotify.com on that
screen (allow encrypted-media); the generated piano stops (sound.spotify); M does not control Spotify (Esc and use
its player); the last link is kept in localStorage. Whole songs only when logged in to Spotify in that browser,
else 30 s previews. Not checked against the real Spotify (the workspace cannot reach it; Chrome was offline).
Street furniture: the Overpass query and parse_osm_xml now keep node highway=stop/give_way/bus_stop/street_lamp,
emergency=fire_hydrant, amenity=waste_basket (build_map.details["furniture"]); real lamps are placed first and
block made-up lamps within 16; stop (crimson_sign, white STOP) and give way (birch_sign, red GIVE WAY / YIELD in
North America) use 8 rotations, on a fence post, facing the stopping traffic (direction tag, else towards the
nearer end of the road), on the kerb side (drives_on_left: LEFT_TRAFFIC boxes, checked on 30 cities); bus stops:
2-post pole + dark oak sign "BUS"/name, shelter=yes -> 4x2 glass back wall, slab roof, bench; hydrant =
red_nether_brick_wall post; bin = empty composter (5 boxes, as the game). Signs are (layer, z, x, rotation, lines,
colour) everywhere; Java Color, Bedrock SignTextColor ARGB (bedrock.SIGN_COLOURS). 231 block states.
Monaco's saved map has 9 bus stops (4 inside 300 m get one); ALA's saved map has none of these points.

## State (v1.5)

v1.5: Ali: "continue making it more realistic, search how, edit without asking, update GitHub". Street-name
signs: build_map keeps every car road as details["streets"] (name, width, line); plan_street_signs finds crossings
(rounded shared vertices with 3+ road ends) and puts one sign per named road there, beside it, facing the
crossing, the same name not within 24 blocks; build_volume places FENCES[0] + SIGNS[rotation] (dark_oak_sign,
16 rotations) off roads, buildings, water, rails and doorways, and records build_volume.signs (layer, z, x,
rotation, 4 lines). Java: chunk block_entities in the 1.18 format (Text1-4 JSON, Color white; the game moves them
to front_text). Bedrock: key type 49 records, PocketMine-MP's field set (id Sign, FrontText/BackText with Text,
SignTextColor -1, IgnoreLighting, PersistFormatting; IsWaxed). verify_world / verify_bedrock compare every sign
text; check_blocks expects no collision box for signs; check_details has a named T-junction. walk.html draws
signs at their true angle (SIGN_BOXES) and their text from a canvas atlas (256x128 a sign when the GPU allows
4096 textures, else 128x64) with its own small shader; mcrender draws the shape turned to the nearest quarter,
without text. Walk-through time "now": solarNow() = UTC + longitude/15 + equation of time.

## State (v1.4)

v1.4: Ali: same as v1.3 + "put the sound of walking and put Warmth by C418 in the background". Warmth is Mojang's
copyrighted music: never bundle, download or recreate it. Instead walk.html makes all sound with Web Audio (no
files): footsteps by the block underfoot (materialOf: grass, stone, wood, sand, gravel, snow, water), landing
thud, original generated piano (pentatonic phrases over a chord walk, long rests, lower and softer at night),
birds by day, crickets at night (reset when T changes the time), waves by how much water is within 16 blocks,
wind by height, muffled under water; M mutes the music, N / "Play your own music" plays a file from the user's
device (object URL, never uploaded); ♪ touch button. window.__walk.sound has counters for tests
(tests/walk_sound.py records the output). Also: seagrass, tall seagrass and kelp (UNDERWATER_PLANTS; kelp
forests by climate: none in polar/tropical/savanna, 0.2 in mediterranean); Bedrock stores their water in
sub-chunk layer 1 (bedrock.WET; verify_bedrock checks layer 1); walk.html draws water in their cells
(world.wet) and blue-green fog with the eye under water; mcrender cannot place its camera under water.
Bridge walls = the 8-neighbour outline of each deck (minus the ends), so diagonal walls join. Worlds from
--osm-file are named after the file. The start screen scrolls and hides the key table below 560 px high.

## State (v1.3)

v1.3: Ali: "see what you did wrong, deep search how to improve it for any place in the world, make GitHub cooler".
Research and the list of what was wrong: docs/ROADMAP.md. Added: climate (set_climate/koppen_at, data/koppen.png
= Rubel 2016 Köppen map from kgcpy, BSD-3, 0.25 deg; CLIMATES: biomes, S_GRASS ground mix, tree mix incl. acacia
and jungle, grass/foliage tints via mcrender.set_climate_tints; build_map sets it, --climate CODE overrides);
S_BARE for WorldCover "barren"; deep water (terrain_layers returns sea depth from the tiles' ETOPO1; shelf
2+0.35*dist up to 32, lakes/rivers 1+0.5*dist up to 8; the whole world is lifted to make room; only for new
worlds, deep_water=args.creating or not args.world); bridges (is_bridge: bridge!=no and layer>=1; build_bridges:
deck height = max(ground at the ends, under + clearance 3 over water / 5 over roads and rails), slope <= 1/3,
walls (GARDEN_WALLS joined at equal height), pillars every 12 blocks off roads; lay_rails shared by ground and
deck rails). Fixed: inner-wall code overwrote `level` (water surface) since v1.0. check_details covers bridges,
the tunnel, the lake depth; it reads the ground layer from build_volume.surface because deep water lifts land.

## State (v1.2)

v1.2: Ali: "edit the GitHub page, cool pictures, simulations, make it so good". README redone: header with badges,
a day-to-night time-lapse GIF of ALA (mcrender, Look(hour=...)), a fly-over GIF of the walk-through
(tests/walk_flyover.py; window.__walk.buildAll() builds every chunk in view first), the map next to the world from
above, a mermaid diagram, and a live link to ALA's walk-through through raw.githack.com (serves the HTML from the
repo). The repo's About description and topics cannot be changed from the cloud workspace (the proxy refuses
repository settings), so Ali sets them himself; GitHub Pages likewise.

## State (v1.1)

v1.1: Ali: "do it" (sun and shadows in the pictures too) + "make it look so cool". mcrender.Look ports the
walk-through's lighting(); `--screenshots` now makes player/rooftops/overview at `--picture-time` (default
afternoon) plus rooftops at sunset and the street at night; `--plain-pictures` for the old look. The haze in the
pictures scales with render distance (0.256 / fog end). README pictures redone in the new look (ALA, Monaco at
sunset, the details test map at a low London sun). ALA: 5 pictures in about a minute; the app test (Monaco,
hills, both editions, pictures) takes about 5 minutes in this workspace.

## State (v1.0)

v1.0: Ali: "make it realistic and specific, make it look sick". Walk-through shaders (G: sun + shadows / sun / plain):
real sun position from info.lat and today's date (solar formulas in walk.html), T cycles morning/midday/afternoon/
sunset/night; 2048 shadow map from the sun (redrawn at most 4x a second, only chunks it sees); block light from
lanterns (BFS like sky light, warm, strong only after dark); water with waves, sky reflection and sun glint; waving
leaves/grass; filmic curve, saturation, haze, vignette; sun disc, stars, moon, tinted clouds. Vertices are 12 floats
(sky, block light, AO, face*8+kind). Converter: inner walls (white concrete every 9 blocks, doorways, per floor);
brick boundary walls (barrier=wall + material=brick -> bricks + brick_wall, BRICK_WALLS table); tall verge lamps
on streets with no sidewalk. ALA: Street View (Printech Ave, Oct 2026) shows a ~2.6 m red face-brick boundary
wall, brick gatehouse with green hip roof, dashed centre line, dirt verges: the campus way now has
barrier=wall/material=brick in ala-tags.json. ALA map data refreshed 2026-11-18 (2 traffic signals, just
outside the 400 m radius).

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

Known limits: a few doors on steep slopes open onto a drop; big hilly areas need ~2 GB of
memory; fences are flat-ground objects; bridges are worked out from the map alone (no real deck heights); no traffic
signs other than stop/give way, no house numbers yet; the pictures draw signs without their text.

## Similar tools (for ideas)

- Arnis (github.com/louis-e/arnis, Apache-2.0, Rust, ~110k lines of element code) does the same job in far
  more detail: interiors, street signs, traffic lights, cars, trains, bridges, stations, ESA WorldCover land
  cover, Overture buildings. Its Bedrock writer was the reference for ours. If you adapt code from Arnis,
  keep its Apache-2.0 notice.
- Terra++ (Build The Earth mod): elevation, biomes, tree cover and ocean depth from public data; /tpll.

Next ideas: see docs/ROADMAP.md (canopy heights, Open Buildings 2.5D heights, kelp/seagrass, signs, Mapterhorn,
region by region).
