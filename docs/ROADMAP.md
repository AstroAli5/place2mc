# What was wrong, what changed, and what comes next

A look back at place2mc, and research into what would make it work well for any place on Earth
(November 2026).

## What was wrong (and is fixed in v1.3)

- **Every place looked like England.** Green grass, oak, birch and spruce, and a plains biome, even in a desert or
  a rainforest. Now each place gets the climate it really has, from the Köppen-Geiger climate map: its biome, its
  grass and leaf colours, sand or dry earth where nothing is mapped, acacia or jungle trees, dead bushes.
- **The sea was 2 blocks deep everywhere**, although the elevation tiles place2mc already downloads contain the
  depth of the sea (they include NOAA's ETOPO1). Now the sea goes down to its real depth (up to 32 blocks), with a
  gentle shelf near the coast, and lakes and rivers get deeper away from their banks (up to 8 blocks).
- **Bridges lay flat on the ground.** A road over a river cut the river in two, and a railway on a viaduct ran
  along the street below it. Now bridges are lifted to the right height, with ramps, walls along the edges and
  pillars, and whatever they cross stays open underneath. Rails on railway bridges climb and turn like rails on
  the ground.
- **Bare land seen from space became farmland.** ESA WorldCover's "bare" class is now bare ground: sand, gravel
  and earth, or desert sand in a dry climate.
- **A hidden bug since v1.0**: the code that builds inner walls reused the name of the water level, so the water
  surface reported to the rest of the program was wrong (it affected where you start and the map preview).
- **A mistake caught by the checks**: the first version of the deep sea never reached new worlds, because a new
  world counts as "building into a world folder" inside the program. Reading the world back block by block
  showed it.

Still not checked: opening the worlds in the real game, downloading live map and elevation data from the cloud
computer that builds this, and the walk-through's speed on a real phone.

## What the research found

**Arnis**, the best-known tool of this kind, released v2.5 "Metropolis" in February 2026 with Bedrock support,
elevation, a winter mode and a world scale setting
([Korben](https://korben.info/en/transform-real-world-minecraft-map-arnis.html)). It turns 3D models into blocks
for landmarks the map cannot describe, like the Statue of Liberty, and its author adds objects he notices on walks
(like trash bins) ([GitHub blog](https://github.blog/open-source/maintainers/arnis-turns-any-place-on-earth-into-a-minecraft-world)).
It now also makes Luanti and Hytale worlds, and has a browser version ([Arnis README](https://github.com/louis-e/arnis)).

**Climate.** The Köppen-Geiger maps of Beck et al. (2018) are free at 1 km resolution under CC BY 4.0
([figshare](https://figshare.com/articles/dataset/Present_and_future_K_ppen-Geiger_climate_classification_maps_at_1-km_resolution/6396959)).
Build The Earth's Terra++ has an add-on that maps Köppen classes to Minecraft biomes, and the Tellus mod uses
Köppen climate and ESA WorldCover for biomes and vegetation, and puts kelp and seagrass in water
([Tellus](https://github.com/giurca/Tellus)). place2mc now uses the Köppen map of Rubel et al. (2016), as shipped in
the kgcpy package (BSD-3), shrunk to 0.25 degrees (45 KB).

**Sea depth.** AWS Terrain Tiles are built by Tilezen's Joerd, whose sources include ETOPO1, a global
topography and bathymetry dataset ([Joerd](https://github.com/mapzen/joerd)). So the depth was there all along.

**Better elevation.** Copernicus GLO-30 (30 m, free on AWS as Cloud Optimized GeoTIFFs) is a surface model that
includes buildings and trees ([Copernicus DEM on AWS](https://registry.opendata.aws/copernicus-dem/)), so it is
not better for us than Terrain Tiles; FABDEM removes them but is non-commercial. Mapterhorn publishes Terrarium
tiles up to zoom 18 ([terrain sources](https://terrain-viewer.iconem.com/docs/features/terrain-sources/)), which
could give sharper hills where it has high-resolution data.

**Bridges.** [OSM2World](https://osm2world.org/blog/2026/06/03/ptf-roadmap-2026-osm-3d-terrain/) works out the
height of roads from the map and the terrain: every point starts at terrain height, ways keep the same height
where they join, and a higher layer keeps a minimum clearance over a lower one where they cross. In
OpenStreetMap, `layer` only says what is above what where ways cross; it is not a height
([OSM wiki](https://wiki.openstreetmap.org/wiki/Key:layer)). place2mc now does a simple version of this.

**Trees.** Meta and WRI's Canopy Height Maps (v2 released in 2026, about 1 m resolution, free on AWS) map tree
height everywhere ([AWS registry](https://registry.opendata.aws/dataforgood-fb-forestsv2/)). That would give real
trees, at their real height, in places where OpenStreetMap maps none.

**Building heights in the Global South.** Google's Open Buildings 2.5D Temporal dataset gives building heights
across Africa, South Asia, South-East Asia, Latin America and the Caribbean
([Google Research](https://sites.research.google/gr/open-buildings/temporal)). Most ALA buildings have no height
on any map; this would give them one.

## What was wrong in v1.3 (fixed in v1.4)

- Walls on bridges that run diagonally were loose posts: a wall block only joins the blocks beside it, and the
  wall cells only touched at their corners. The walls now follow the whole outline of the deck.
- A world built from a map file was named after the file's full path ("home claude compare monaco.osm"); it is
  now named after the file ("Monaco").
- On a phone held sideways the start screen was taller than the screen, so the walk button could be cut off;
  it now scrolls, and short screens hide the key list.
- The walk-through was silent, and swimming showed the sky instead of water.

## Researched for v1.5

The map data already holds a name for most streets (18 at ALA, 137 in Monaco) but place2mc showed none of them,
and "no signs" was on the known-limits list. Arnis's release notes list what it added for realism: real signs,
traffic signs and house numbers (3.1), trees from a canopy height map (3.1), roof overhangs, chimneys,
balconies and window shutters (2.5-2.6), street furniture such as hydrants and drinking water (2.7), snow
above the snow line (2.9). Street names were picked first because they make a place recognisable from the
ground. Traffic signs, house numbers and street furniture need map points that place2mc does not download
yet (stop signs, hydrants, addresses), so they come next.

## Researched for v1.6

OpenStreetMap maps stop signs as a point on the road that must stop, at the stopping point, with an optional
direction=forward/backward for the traffic it applies to; hydrants, lamps and bins are mapped as points where
they stand. place2mc did not download any of these points before. Spotify's embed player plays whole songs only
for someone logged in to Spotify in the same browser, otherwise 30-second previews (and often only previews on
phones); the Web Playback SDK would need a Spotify developer app and Premium, so the walk-through uses the embed.

## Researched for v1.7: how the game moves

"Reverse engineering Minecraft" here means learning how the game behaves from public sources and writing our own
code that does the same; nothing of Mojang's is copied or downloaded. The Minecraft Wiki and the projects that
re-create the game's physics agree: every tick (1/20 s) the player gets a push from the keys (which count 0.98),
0.1 on the ground (x1.3 sprinting), 0.02 in the air, 0.05 flying (x2 sprinting); after moving, speed keeps 0.546
of itself on the ground (block friction 0.6 x 0.91) and 0.91 in the air; the upward speed loses 0.08 and keeps
0.98; a jump starts at 0.42 (a sprint jump also pushes 0.2 forward); flying has no gravity and keeps 0.6 of its
up-down speed. The player is 0.6 x 1.8 blocks with the eyes at 1.62. Those rules give the wiki's speeds, and
tests/walk_physics.py checks eight of them. The code map in docs/code-map.md was made with graphify.

## What comes next

- [ ] Real tree heights and positions from Meta/WRI's canopy height maps
- [ ] Building heights from Google Open Buildings 2.5D where neither OpenStreetMap nor Overture has one
- [ ] Snow in winter in cold climates; coral in tropical seas
- [ ] Street-name signs and shop signs (Overture places has the names)
- [ ] Sharper hills from Mapterhorn where it has high-resolution data
- [ ] Bigger areas, built region by region
- [ ] Landmarks from 3D models, like Arnis
- [x] Climate: biomes, grass colour, ground and trees for every place
- [x] The sea at its real depth; deeper lakes and rivers
- [x] Bridges at the right height, tunnels left out
- [x] Seagrass and kelp under water, by climate (v1.4); Bedrock gets the water layer the game needs for them
- [x] Sounds in the walk-through: footsteps by surface, birds, crickets, waves, wind, original piano music (v1.4)
- [x] Street-name signs at every crossing, with the real names, in Java, Bedrock and the walk-through (v1.5).
  Arnis 3.1 added signs too; it writes Bedrock signs without text, place2mc writes the text in both editions
  (the Bedrock fields follow PocketMine-MP's sign code)
- [x] "Now" in the walk-through: the sun where it really is over the place this minute (longitude and the
  equation of time) (v1.5)
- [x] Road signs and street furniture from the map (v1.6): highway=stop and give_way (on the approach road, facing
  the traffic the direction tag or the nearer junction says, on the kerb side for left- or right-hand traffic),
  highway=bus_stop (name, shelter=yes), emergency=fire_hydrant, amenity=waste_basket, highway=street_lamp
- [x] Spotify in the walk-through: paste a link, Spotify's embed player plays it (v1.6)
- [x] The game's movement in the walk-through, tick by tick, measured by tests/walk_physics.py (v1.7)
- [x] Creative mode in the walk-through: break, place, pick, hotbar, kept in the browser (v1.7)
