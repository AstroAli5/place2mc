# Code map of place2mc (made with graphify)

Made by running [graphify](https://github.com/Graphify-Labs/graphify) (`pip install graphifyy`, then
`graphify update FOLDER`) on place2mc.py, bedrock.py, walkdata.py, mcrender.py, overture.py and place2mc_app.py. It
reads the code with tree-sitter (no AI, nothing leaves the computer) and finds which functions call which. On Arnis
(github.com/louis-e/arnis, about 110k lines of Rust) the same run finds 23,104 parts and 63,765 links, with Block and
WorldEditor at the centre.

## Corpus Check
- 6 files · ~48,721 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 357 nodes · 808 edges · 10 communities
- Extraction: 100% EXTRACTED · 0% INFERRED · 0% AMBIGUOUS · INFERRED: 1 edges (avg confidence: 0.85)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- numpy
- place2mc.py
- build_map
- overture.py
- bedrock.py
- build_volume
- place2mc_app.py
- walkdata.py
- TextureSource
- elevation_grid

## God Nodes (most connected - your core abstractions)
1. `build_map()` - 29 edges
2. `build_volume()` - 25 edges
3. `main()` - 22 edges
4. `_make_textures()` - 19 edges
5. `_clip()` - 17 edges
6. `write_level_dat()` - 14 edges
7. `shade()` - 13 edges
8. `_name()` - 13 edges
9. `_rng()` - 12 edges
10. `export()` - 12 edges

## Surprising Connections (you probably didn't know these)
- `export()` --calls--> `resolve_textures()`  [EXTRACTED]
  walkdata.py → mcrender.py
- `export()` --calls--> `make_textures()`  [EXTRACTED]
  walkdata.py → mcrender.py

## Import Cycles
- None detected.

## Communities (10 total, 0 thin omitted)

### Community 0 - "numpy"
Cohesion: 0.07
Nodes (44): bark(), block_light(), blotchy(), boards(), box_span(), brick_wall(), _clip(), _cloud_map() (+36 more)

### Community 1 - "place2mc.py"
Cohesion: 0.08
Nodes (37): apply_tag_file(), ask_world(), choose_spawn(), download_osm(), encode_chunk(), encode_section(), find_worlds(), geocode() (+29 more)

### Community 2 - "build_map"
Cohesion: 0.05
Nodes (23): already_built(), assemble_rings(), barrier_height(), block(), bridge_layer(), build_map(), middle_in(), on_campus() (+15 more)

### Community 3 - "overture.py"
Cohesion: 0.09
Nodes (20): add_heights(), _area2(), buildings_from_tiles(), decode_directory(), decode_tile(), _decompress(), _element(), fetch() (+12 more)

### Community 4 - "bedrock.py"
Cohesion: 0.11
Nodes (22): bits_for(), _block(), chunk_records(), crc32c(), data3d(), encode_subchunk(), export_mcworld(), _handle() (+14 more)

### Community 5 - "build_volume"
Cohesion: 0.06
Nodes (18): biome_map(), build_bridges(), build_volume(), building_bases(), distance_from(), facing_rotation(), free_spot(), inward_facing() (+10 more)

### Community 6 - "place2mc_app.py"
Cohesion: 0.09
Nodes (5): Handler, Job, open_folder(), search(), saves_folder()

### Community 7 - "walkdata.py"
Cohesion: 0.18
Nodes (5): fetch_tile(), block_table(), column_runs(), export(), _html()

### Community 8 - "TextureSource"
Cohesion: 0.20
Nodes (3): find_minecraft_jar(), resolve_textures(), TextureSource

### Community 9 - "elevation_grid"
Cohesion: 0.20
Nodes (5): box_blur(), decode_terrarium(), elevation_grid(), make_terrain(), terrain_layers()

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `build_map()` connect `build_map` to `numpy`, `place2mc.py`, `overture.py`, `build_volume`?**
  _High betweenness centrality (0.048) - this node is a cross-community bridge._
- **Should `numpy` be split into smaller, more focused modules?**
  _Cohesion score 0.06664198445020363 - nodes in this community are weakly interconnected._
- **Why does `TextureSource` connect `TextureSource` to `numpy`?**
  _High betweenness centrality (0.029) - this node is a cross-community bridge._
- **Should `place2mc.py` be split into smaller, more focused modules?**
  _Cohesion score 0.07597895967270601 - nodes in this community are weakly interconnected._
- **Why does `build_volume()` connect `build_volume` to `numpy`, `place2mc.py`, `build_map`, `overture.py`, `elevation_grid`?**
  _High betweenness centrality (0.027) - this node is a cross-community bridge._
- **Should `build_map` be split into smaller, more focused modules?**
  _Cohesion score 0.05226480836236934 - nodes in this community are weakly interconnected._
- **Should `overture.py` be split into smaller, more focused modules?**
  _Cohesion score 0.08780487804878048 - nodes in this community are weakly interconnected._