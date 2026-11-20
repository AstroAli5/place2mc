# Franklin & Marshall College

Franklin & Marshall College in Lancaster, Pennsylvania, built at 1 block = 1 metre from OpenStreetMap and the US
government's 2019 laser scan of the land (USGS 3DEP LiDAR): every roof at its measured height, metre by metre, with its
towers, cupolas, gables and chimneys; every tree where it stands, as tall and as wide as it is; the ground as scanned.
1.5 km x 1.5 km around the campus, Java and Bedrock.

![Old Main from the east lawn, in the walk-through](../../docs/fandm-walk.jpg)

**[Walk around it in your browser](https://raw.githack.com/AstroAli5/place2mc/main/examples/fandm/Walk%20around%20Franklin%20%26%20Marshall%20College.html)**
(about 4 MB; you start on the lawn in front of Old Main, looking at its tower).

![Old Main and its tower from above](../../docs/fandm-oldmain.jpg)

## How it was made

1. `Get F&M data.html` (made by `tools/make_fetch_page.py`) ran in a browser: OpenStreetMap's map, and 11.4 million laser
   points of the 2019 scan `PA_Northcentral_B1_2019`, boiled down to `place2mc-data.json`.
2. `python place2mc.py --osm-file place2mc-data.json --lidar place2mc-data.json --center 40.04966,-76.31705 --radius 768
   --tags examples/fandm/fandm-tags.json --spawn 40.045252,-76.319681,270 --edition both`

## How exact it is

Heights, compared on every 1 m of roof of the 86 named buildings: 93.5% within 1 m of the laser scan (0.6 m on average;
Minecraft rounds to whole blocks). Old Main's tower: 36 blocks; the scan measures 36.0 m and the college archives give
121 ft (36.9 m). The Java world reads back with two libraries, and the Bedrock world block for block (78 million blocks,
0 wrong).

| Building | Laser scan 2019: roof middle / top 5% / highest (m) | In the world, blocks above the ground | Roof within 1 m of the scan |
|---|---|---|---|
| Old Main | 15.5 / 18.5 / 36.0 | 16 / 18 / 36 | 94% |
| Goethean Hall | 13.5 / 16.0 / 17.0 | 14 / 16 / 16 | 98% |
| Diagnothian Hall | 14.0 / 16.5 / 16.5 | 14 / 16 / 16 | 96% |
| Hensel Hall | 12.0 / 17.0 / 28.0 | 12 / 16 / 28 | 96% |
| Keiper Liberal Arts | 14.5 / 21.0 / 29.5 | 14 / 21 / 29 | 86% |
| Shadek-Fackenthal Library | 13.0 / 16.5 / 26.5 | 13 / 16 / 20 | 94% |
| Steinman College Center | 9.5 / 10.5 / 12.5 | 10 / 10 / 12 | 97% |
| Stager Hall | 16.5 / 20.5 / 23.0 | 16 / 20 / 22 | 93% |
| Harris Center for Business, Government & Public Policy | 14.5 / 17.0 / 21.5 | 14 / 17 / 19 | 89% |
| Hackman Physical Sciences Laboratory | 16.0 / 17.5 / 22.0 | 16 / 18 / 22 | 96% |
| Barshinginer Life Sciences Building | 17.5 / 22.0 / 27.0 | 18 / 22 / 27 | 90% |
| Martin Library of the Sciences | 13.0 / 16.0 / 18.5 | 13 / 16 / 16 | 95% |
| Mayser Physical Education Center | 11.0 / 14.0 / 17.5 | 11 / 14 / 15 | 95% |
| Alumni Sports and Fitness Center | 15.0 / 16.0 / 19.5 | 15 / 16 / 20 | 98% |
| Kunkel Aquatic Center | 10.0 / 11.5 / 16.0 | 10 / 12 / 14 | 98% |
| Robert Roschel Performing Arts Center | 7.5 / 10.5 / 11.0 | 8 / 10 / 11 | 95% |
| Roschel College House | 16.0 / 20.5 / 27.5 | 16 / 20 / 28 | 95% |
| Ware College House | 13.0 / 15.0 / 15.5 | 13 / 15 / 16 | 93% |
| Weis College House | 12.0 / 15.0 / 17.0 | 12 / 15 / 16 | 91% |
| Bonchek College House | 12.0 / 14.5 / 14.5 | 12 / 14 / 14 | 96% |
| Brooks College House | 9.0 / 11.0 / 15.0 | 9 / 11 / 16 | 93% |
| Schnader Residence Hall | 10.0 / 12.0 / 14.5 | 10 / 12 / 14 | 92% |
| Thomas Residence Hall | 11.5 / 12.0 / 17.5 | 12 / 12 / 16 | 97% |
| Meyran Hall | 10.0 / 12.5 / 23.5 | 10 / 12 / 23 | 86% |
| Dietz Hall | 10.0 / 12.5 / 14.5 | 10 / 12 / 19 | 87% |
| Distler House | 8.0 / 13.0 / 21.5 | 8 / 12 / 13 | 87% |
| North Museum of Nature and Science | 7.5 / 11.0 / 23.0 | 8 / 11 / 23 | 97% |
| Lombardo Welcome Center | 10.5 / 12.5 / 14.0 | 10 / 12 / 14 | 97% |
| Winter Visual Arts Center | built after the scan | 16 / 17 / 29 | from published facts |
| Harwood Commons | 7.0 / 9.0 / 22.0 | 7 / 9 / 17 | 91% |
| Klehr Center for Jewish Life | 8.0 / 11.7 / 23.0 | 8 / 9 / 11 | 81% |
| Santee Chapel | 10.0 / 29.0 / 32.5 | 10 / 28 / 32 | 93% |
| Lark Building | 15.5 / 20.5 / 33.0 | 16 / 20 / 33 | 93% |

The "highest" column can differ where a tree hangs over a roof (the tree is drawn, the branch is not part of the roof),
or where a mast or spire is thinner than the scan can see.

## Knowing where you are

The walk-through tells you where you are at the bottom of the screen: *Inside Shadek-Fackenthal Library*, *By Old Main*,
*On College Avenue*, *In Buchanan Park*, and in the top corner which named building you are looking at. In Java and
Bedrock every named building has a sign with its name in front of its main door (447 name and street signs in all).

![Old Main's name sign at its door, in the walk-through](../../docs/fandm-sign.jpg)

## Inside the buildings

Every building is laid out and furnished for what it is used for, worked out from its name and tags, on every floor:

- Rooms along the outside walls (so every room has windows), a wall with a real wooden door into each room, a corridor
  behind, and in deep buildings a second ring of rooms on the inner side of the corridor. A hall runs from each front
  door to the corridor. Corner rooms and nooks that would be closed off get a door of their own.
- A staircase up through every building with more than one floor (684 of them): two flights side by side, a landing
  at each floor, a hole in the floor above. Floors that sit inside a tall measured roof are built too, so the old
  halls with steep slate roofs have their top floors.
- A lamp hanging in the middle of every room and along the corridors, so the insides are lit day and night.
- Residence halls and College Houses: double bedrooms of about 4 x 4 m, each with two real beds, two desks with chairs,
  nightstands and wardrobes (F&M's housing pages list a bed, desk, chair, dresser and wardrobe for every student); a
  lounge and a laundry by the front door; a lounge on every floor; studies, kitchens and bathrooms in the middle ring.
- Offices (Old Main, Stager Hall and the rest): a desk under the window, a chair, a bookcase, a carpet; meeting rooms
  with a long table in the middle.
- Classroom buildings: a board on the wall, rows of desks and chairs facing it, the teacher's desk. Labs: benches,
  stools and fume cupboards.
- The open buildings: bookshelf rows and reading tables in the libraries, pews facing an altar in Santee Chapel
  (Lancaster Theological Seminary, next door), seats facing a stage in Hensel Hall and the Roschel Performing Arts
  Center, a pool with lanes in the Kunkel Aquatic Center, courts with bleachers in Mayser and the Alumni Sports and
  Fitness Center, tables in the dining halls, display cases in the North Museum.

Walking in from the front door and up the stairs, a test reaches every floor of Old Main, Stager Hall, Keiper Liberal
Arts, Ware, Bonchek and Roschel College Houses, Schnader and Thomas Residence Halls and Steinman College Center, and
94-100% of their floor space. The real floor plans are not public, so the layout of rooms is made up; what each
building is used for is not.

![Four buildings with their roofs cut off: Schnader Hall's bedrooms off its corridor, Old Main's offices, the library's shelves, Ware College House's lounge and bedrooms](../../docs/fandm-insides.jpg)

![A bedroom in Schnader Residence Hall in the walk-through: the window, a bed, a nightstand, a wardrobe](../../docs/fandm-dorm.jpg)

![Inside Shadek-Fackenthal Library in the walk-through](../../docs/fandm-library.jpg)

## Doors

In Java and Bedrock the doors are wooden: right-click (or tap) one to open it, as always. In the walk-through it works
the same now: right-click a door (on a phone, tap the place button) and both halves swing open; the bottom of the screen
says "Right-click to open the door" when you look at one. Hold Shift to put a block on a door instead.

## Outside: what the map has

Everything below is drawn where OpenStreetMap has it:

- Sports: the lines of each pitch's sport at their real sizes (yard lines and goalposts on the football fields, the
  halfway line, centre circle, penalty boxes and goals on the soccer fields, service lines and nets on the tennis and
  pickleball courts, the keys and hoops on the basketball courts), and the two stands at Shadek Stadium, rising row by
  row away from the field.
- Statues with their name and artist on a sign (Abraham de Peyster, Harmony, Let the Kids Play), 21 bicycle racks,
  outdoor tables, 3 USPS post boxes, the flagpole, the masts of the power lines, bus stops, stop signs, fire hydrants.

![Shadek Stadium and the tennis courts](../../docs/fandm-sports.jpg)

## Autumn

The walk-through colours the leaves for the real date: orange, red, yellow and brown in October and November. Minecraft
itself has no seasons (a leaf takes its colour from the biome), so the game worlds get it from a resource pack:

- Java: `Autumn leaves for Franklin & Marshall College (Java resource pack).zip`. In Minecraft go to Options > Resource
  Packs > Open Pack Folder, put the zip there and turn it on. Oaks turn orange, red and brown, birches yellow; spruces
  stay green. It is a separate pack on purpose: Java refuses to open a world whose bundled pack it finds incompatible,
  and this one has not been tried in the real game.
- Bedrock: the pack is inside the .mcworld and applies by itself (an autumn foliage colour for the world's biomes).

## What is checked, and what is not

- Colours: checked building by building on Google Earth's 3D view of the campus (May 2024) and on photos of Old Main,
  Diagnothian Hall, Stager Hall and Steinman College Center: red brick with mid-grey slate roofs, except the Lombardo
  Welcome Center (light walls, white flat roof) and the white flat roofs of Steinman College Center and College Row.
- Names: ten are spelt as on F&M's own campus map, not as on OpenStreetMap (Shadek-Fackenthal Library, Huegel Alumni
  House, Wohlsen House, Office of Multicultural Affairs, Barshinger Life Sciences & Philosophy Building, Kunkel Aquatic
  Center, Roschel College House (the former New College House), Restaurants at Ben Franklin (D-Hall), Keiper Liberal
  Arts, and the Harris Center for Business, Government & Public Policy).
- Since the 2019 scan: the Winter Visual Arts Center (2020) is built from published facts (3 storeys, 15 m: a two-storey
  frame of 16-foot glass units on a white concrete base); the Lombardo Welcome Center (2024) is the renovated College
  Square building the scan measured, with its new light walls. Other changes since 2019 were not checked building by
  building.
- Trees: autumn colours in the walk-through by the real date, and in the game worlds through the autumn packs above.
- Made up to look right, because no public source has them: the layout of rooms (where each building's use is real),
  flowers and bushes, which parking spaces hold a car (cars come and go), and most street lamps (the map has none here).
  Pitch lines, goals, nets, hoops, stands, statues, bicycle racks, post boxes and the flagpole are where the map has
  them, at their real sizes; the shapes are simplified to blocks.
- Not opened in the real game: there is no Minecraft on the computer this was made with. Both worlds were read back
  block by block (Java with two libraries: 9,216 chunks, every sign's text, all 4,982 bed halves with their block
  entity; Bedrock: every block, every sign and bed record), and every block state, door shape, bed and carpet shape
  was compared with the game's own data for Java 1.18, 1.21.4 and 26.1.

Sources: © OpenStreetMap contributors (ODbL); USGS 3D Elevation Program (public domain); F&M Archives (Old Main, building
names); F&M campus map; F&M housing pages (room furniture and lounges); OpenStreetMap again for pitches, stands, statues and racks; Dezeen and The Architect's Newspaper (Winter Visual Arts
Center); Google Earth (colours, viewed only).
