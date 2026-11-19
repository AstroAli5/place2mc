# Example: African Leadership Academy, Johannesburg

- `Walk around ALA.html`: [open it in your browser](https://raw.githack.com/AstroAli5/place2mc/main/examples/ala/Walk%20around%20ALA.html),
  or download it and open it, to walk around the campus (W A S D and the mouse, T for the time of day).
- `ala-tags.json`: what OpenStreetMap does not know yet about ALA, taken from the school's campus photos: dark green
  metal roofs, walls of red face brick or white plaster, two storeys. Which building is brick and which is white is
  a guess: each building has a note saying where it is, so change `building:material` (`brick` or `plaster`) to match.

Make it again (with the app's map data or a saved map file):

```
python place2mc.py --center -26.07538,27.91607 --radius 400 --tags examples/ala/ala-tags.json --edition both
```

OpenStreetMap has only 11 of the campus buildings, and none of the warehouses around it, so the rest of the
campus is lawn and trees. Anyone can add buildings at openstreetmap.org (free account, "Edit", trace from the
aerial photo); the next conversion picks them up, and so does every other tool that uses the map.
