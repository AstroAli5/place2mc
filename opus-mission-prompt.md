Use the fable-playbook skill for this task. Before you start, tell me which steps of the playbook you are following.

MISSION
You are continuing place2mc, a tool that turns any real-world place into Minecraft. I pick a place on a map in the place2mc app and press Convert; it downloads the map of that place from OpenStreetMap and the hills from elevation data, and makes a ready-to-play Java world and a Bedrock .mcworld file (I play both). Version 0.5 (the repository: place2mc_app.py, place2mc.py, bedrock.py, mcrender.py, tests) already does this end to end. I want it to feel like walking around my own Minecraft, with detail like Arnis. Your job is to make the worlds look more like the real place, one improvement at a time, without breaking what already works.

ACCURACY IS THE #1 PRIORITY. Streets, buildings and water must sit where they sit in the real place, at real size. A nice-looking feature that moves things to the wrong spot, or that produces a world Minecraft cannot open, is a failed feature.

CONFIG

* Tool: place2mc.py (the converter) and mcrender.py (draws game-style pictures with --screenshots). Needs only numpy and pillow.
* Target: Minecraft Java 1.21.4 or newer for new worlds (1.18+ with --world), and Bedrock 1.21.120 or newer
* Scale: 1 block = 1 metre. The centre of the place is at x=0, z=0. North is -z.
* Ground: real hills from AWS Terrain Tiles (the same source Arnis and Terra++ use), lowest point and sea level at y=-61. --flat turns hills off. New worlds are void around the build.
* Map data: OpenStreetMap through the Overpass API. Place search through Nominatim. Offline mode with --osm-file.
* Output: a new world folder in my Java saves (level.dat, region files, icon), and/or a .mcworld file. --world still builds into an existing world.
* Look: recognisable real town that looks like a good Minecraft city build. Not photorealistic.
* Pictures: mcrender.py uses the real textures from my own Minecraft Java install or a resource pack when it finds one, and built-in textures otherwise.

CURRENT STATE (v0.5)

* Builds: hills with dirt over stone; buildings on one level each with a stone foundation on the downhill side; roads, sidewalks and half slabs on every one-block step; lamps, doors (with a clear doorway, dug into the slope if needed), shop windows, floors inside, rooftop units, stair roofs with eaves; lakes and rivers at their lowest bank with raised banks so water cannot spill; the sea level with the flat world; parks, forests, yard and street trees; short grass, tall grass, ferns and flowers on grass; biomes (plains, forest, river, ocean, beach) so colours change in the game; zebra crossings, fences, garden walls, hedges, railway and tram tracks, benches
* Pictures: see-through leaves, crossed plant pictures, hill-aware cameras; the game's own textures from my Minecraft install or a resource pack if found
* Checked: Monaco and West Oakland map files, flat and on test hills (Terrarium tiles generated from a known landscape: decoding matches Mapzen's worked example, heights within 1 cm). World files read back with anvil-parser2 and nbtlib, every sampled block and every biome matched. No water spills, nothing floating, no hollow space under buildings, every doorway clear. Every block, property, value and biome checked against PrismarineJS/minecraft-data for 1.18, 1.20, 1.21.4 and 26.1, and the shapes of stairs, doors, fences, lanterns and slabs match the game. A 2 km x 2 km area takes about 12 s flat, 26 s and 1.8 GB of memory on hills with 280 blocks of relief.
* Bedrock (v0.5): read back block for block with our own reader and with rusty-leveldb (the library Arnis uses); every block state valid for Bedrock 1.21.120; level.dat has the same fields and types as a working Arnis world. The app was tested end to end in headless Chrome.
* NOT checked yet: opening the worlds in the real game (either edition), real elevation tiles and live map downloads (earlier sessions could not reach those servers), a real game .jar for textures, and the start scripts on a real Windows or Mac computer.
* Known limits: the sea is only 2 blocks deep (it has to sit at the flat world's level); a few doors on steep slopes open onto a drop of 2+ blocks; big hilly areas need about 2 GB of memory; outside the built square the world stays Superflat.

FIRST STEP
Ask me what happened when I ran the tool and opened the world in Minecraft. If anything failed (an error message, missing chunks, dark areas, wrong blocks), fix that before adding anything new.

IMPROVEMENT PRIORITIES, IN ORDER

1. Anything broken in my real test
2. Test the hills on a real place and tune the smoothing and building foundations
3. Better buildings: wall and roof colours from the map tags (building:colour, roof:colour, building:material), gabled roofs, building:part for towers and complex shapes
4. Road details: parking lines, bridges and tunnels at the right height
5. Street-name signs, traffic lights, light blocks for night, building interiors (the Arnis-style detail I asked for)
6. Bigger areas: build region by region so memory stays low. The limit now is a 1000 m radius.

RULES

* Change one thing at a time. After each change, run the tool on the same map file, look at place2mc_preview.png and the --screenshots pictures, and compare them with the previous ones.
* After every change to how blocks are written, run tests/make_bedrock_table.py, tests/check_blocks.py, tests/check_details.py, tests/verify_world.py and tests/verify_bedrock.py (CLAUDE.md has the commands).
* Keep --osm-file working so it can be tested without internet.
* Use only block names that exist in Minecraft 1.18 and newer. Leaves must stay persistent=true or they decay.
* Never say "tested in Minecraft" unless I told you I opened the world. Say what you checked and what you could not check.
* Reference: Arnis (open source, Apache-2.0) and Terra++ (the Build The Earth mod) do the same job. Arnis has building interiors and a map to pick the area; Terra++ uses tree-cover data and has /tpll to teleport by latitude and longitude. Look at them for ideas, and tell me if you reuse anything from them.
* Every chunk is stamped Minecraft 1.18.2 (data version 2975) so newer versions upgrade it on load; only use block names that exist in 1.18.2 and let the game rename them (tests/check_blocks.py knows grass -> short_grass).
