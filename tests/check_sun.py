"""Check the sun in the pictures: where it stands (east in the morning, north at noon south of the tropics, south at
noon in the north, below the horizon at night) and that a pillar casts a shadow on the side away from the sun.
Usage: python tests/check_sun.py"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import mcrender  # noqa: E402
import place2mc as p  # noqa: E402

problems = []


def expect(ok, what):
    print(("ok    " if ok else "WRONG ") + what)
    if not ok:
        problems.append(what)


# where the sun is: x east, y up, z south
s = mcrender.sun_direction(0, 12, 80)                     # the equator at noon near the March equinox
expect(s[1] > 0.99, f"equator, equinox, noon: sun overhead ({s.round(3)})")
s = mcrender.sun_direction(-26.08, 12, 323)               # Johannesburg, 19 November, noon
expect(s[2] < 0 and s[1] > 0.9, f"Johannesburg in November at noon: high and a little to the north ({s.round(3)})")
s = mcrender.sun_direction(51.5, 12, 323)                 # London, 19 November, noon
expect(s[2] > 0 and s[1] < 0.4, f"London in November at noon: low in the south ({s.round(3)})")
expect(mcrender.sun_direction(51.5, 9, 172)[0] > 0, "morning: the sun is in the east")
expect(mcrender.sun_direction(51.5, 16, 172)[0] < 0, "afternoon: the sun is in the west")
expect(mcrender.sun_direction(51.5, 23, 323)[1] < 0, "night: the sun is below the horizon")
look = mcrender.Look(-26.08, "sunset", day_of_year=323)
expect(17.5 < look.hour < 19.5, f"Johannesburg sunset on 19 November at {look.clock()} sun time")
night = mcrender.Look(-26.08, "night", day_of_year=323)
expect(night.night > 0.9 and night.sun_dir[1] > 0.5, "at night the moon lights the place from high up")

# a shadow: a 6-block stone pillar on grass, sun from the west in the afternoon
size = 40
vol = np.zeros((p.FLOOR + 20, size, size), dtype=np.uint8)
vol[:p.FLOOR] = p.STONE
vol[p.FLOOR] = p.GRASS
vol[p.FLOOR + 1:p.FLOOR + 7, 20, 20] = p.STONE
look = mcrender.Look(0.0, "afternoon", day_of_year=80)    # equator, equinox: the sun due west, 37 degrees up
tex, mean, solid = mcrender.make_textures(None)
light, blight = mcrender.sky_light(vol), mcrender.block_light(vol)
top = float(vol.shape[0])


def ground_colour(x, z):
    origin = np.array([[x + 0.5, top - 1, z + 0.5]])
    return mcrender.shade(vol, tex, mean, solid, light, origin, np.array([[1e-6, -1.0, 1e-6]]), top, (500, 600),
                          look=look, blight=blight)[0]


east = ground_colour(23, 20)        # 3 blocks east of the pillar: in its shadow (the sun is in the west)
west = ground_colour(17, 20)        # 3 blocks west: in the sun
far = ground_colour(30, 30)
expect(east.sum() < west.sum() * 0.8, f"the pillar's shadow falls to the east (east {east.round(2)}, west {west.round(2)})")
expect(abs(west.sum() - far.sum()) < 0.05, "open ground in the sun is lit the same everywhere")
plain = mcrender.shade(vol, tex, mean, solid, light, np.array([[23.5, top - 1, 20.5]]), np.array([[1e-6, -1.0, 1e-6]]),
                       top, (500, 600))[0]
plain_w = mcrender.shade(vol, tex, mean, solid, light, np.array([[17.5, top - 1, 20.5]]), np.array([[1e-6, -1.0, 1e-6]]),
                         top, (500, 600))[0]
expect(np.allclose(plain, plain_w), "the plain noon look has no shadows, like the game")
print("\n".join(problems) if problems else "sun OK")
