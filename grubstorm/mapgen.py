"""Procedural biome map generation.

Each biome builds a World plus presentation/rule hints. Maps are seeded, so
the same seed + biome gives the identical map on every lockstep client.
"""
import numpy as np

from . import materials as M
from .world import World
from .constants import GRID_W, GRID_H


def _vnoise(rng, h, w, cell):
    """Bilinear value noise in [0,1)."""
    gh, gw = h // cell + 2, w // cell + 2
    g = rng.random((gh, gw))
    ys = np.arange(h) / cell
    xs = np.arange(w) / cell
    y0 = ys.astype(int); x0 = xs.astype(int)
    fy = (ys - y0)[:, None]; fx = (xs - x0)[None, :]
    a = g[y0][:, x0]; b = g[y0][:, x0 + 1]
    c = g[y0 + 1][:, x0]; d = g[y0 + 1][:, x0 + 1]
    return (a * (1 - fx) + b * fx) * (1 - fy) + (c * (1 - fx) + d * fx) * fy


def _fbm(rng, h, w, base_cell, octaves=3):
    out = np.zeros((h, w))
    amp, total = 1.0, 0.0
    cell = base_cell
    for _ in range(octaves):
        out += amp * _vnoise(rng, h, w, max(2, cell))
        total += amp
        amp *= 0.5
        cell //= 2
    return out / total


class MapSpec:
    def __init__(self, name, world, **kw):
        self.name = name
        self.world: World = world
        self.sky_top = kw.get("sky_top", (24, 28, 48))
        self.sky_bottom = kw.get("sky_bottom", (60, 70, 110))
        self.ambient = kw.get("ambient", 20.0)
        self.gravity_scale = kw.get("gravity_scale", 1.0)
        self.flood_mat = kw.get("flood_mat", M.WATER)  # sudden-death riser
        self.spawns = kw.get("spawns", [])
        self.decor = kw.get("decor", "stars")          # bg particle theme
        self.light = kw.get("light", 1.0)              # ambient light level
        self.open_sky = kw.get("open_sky", True)       # airstrikes possible?


BIOMES = [
    "island", "volcano", "sewer", "tundra", "desert", "cavern",
    "junkyard", "mine", "lab", "candy", "moon",
]

BIOME_LABELS = {
    "island":   ("Grubtide Isle", "Sunny island, water caves, wooden forts"),
    "volcano":  ("Mt. Kaboom", "Lava pockets, ash, explosive veins"),
    "sewer":    ("The Drips", "Acid pipes and toxic sludge"),
    "tundra":   ("Frostbite Flats", "Ice caves, snow, frozen lakes"),
    "desert":   ("Dune & Doom", "Sand avalanches, buried oil"),
    "cavern":   ("Gloomhollow", "Dark cave, glowing crystals, magic goo"),
    "junkyard": ("Scrapheap", "Metal beams, oil barrels, gas leaks"),
    "mine":     ("Powderkeg Mine", "Wood supports and explosive dust"),
    "lab":      ("Lab 13", "Shelves of dangerous chemicals"),
    "candy":    ("Goopland", "Sticky slime and sugar cliffs"),
    "moon":     ("Lunar Lounge", "Low gravity, leaking gas, metal domes"),
}


def _heightmap(rng, w, base, amp, cell=60):
    n = _fbm(rng, 1, w, cell, 3)[0]
    return (base + (n - 0.5) * 2 * amp).astype(int)


def _fill_below(world, heights, mat, depth=None):
    yy = np.arange(world.h)[:, None]
    mask = yy >= heights[None, :]
    if depth is not None:
        mask &= yy < (heights[None, :] + depth)
    world.mat[mask] = mat


def _pockets(world, rng, n, mat, rmin, rmax, ymin, ymax, life=0,
             solid_only=True):
    for _ in range(n):
        x = int(rng.integers(8, world.w - 8))
        y = int(rng.integers(ymin, ymax))
        if solid_only and not world.is_solid(x, y):
            continue
        r = int(rng.integers(rmin, rmax + 1))
        world.paint(x, y, r, mat, mode="replace", life=life, noise=0.25)


def _grass_tops(world, grass=M.GRASS, on=M.DIRT):
    mat = world.mat
    above = np.roll(mat, 1, axis=0)
    above[0] = M.EMPTY
    tops = (mat == on) & (above == M.EMPTY)
    mat[tops] = grass


def _platforms(world, rng, n, mat=M.WOOD):
    for _ in range(n):
        x = int(rng.integers(30, world.w - 60))
        y = int(rng.integers(40, world.h - 80))
        length = int(rng.integers(20, 50))
        world.mat[y:y + 3, x:x + length] = mat


def _ocean(world, rows=14, mat=M.WATER):
    """Open water at the bottom of the map — fall in and you drown."""
    world.mat[-1, :] = M.BEDROCK
    band = world.mat[-rows - 1:-1, :]
    band[band == M.EMPTY] = mat
    world.water_level = world.h - rows - 1


def _barrels(world, rng, n):
    """Buried explosive barrels: a metal shell around a nitro core."""
    for _ in range(n):
        x = int(rng.integers(20, world.w - 20))
        y = int(rng.integers(30, world.h - 30))
        if not world.is_solid(x, y):
            continue
        world.paint(x, y, 4, M.METAL, mode="replace")
        world.paint(x, y, 2, M.NITRO, mode="replace")


def _find_spawns(world, n=16):
    """Walkable surface spots with head clearance, spread across the map."""
    spawns = []
    xs = np.linspace(14, world.w - 14, n * 4).astype(int)
    rng = np.random.default_rng(1234)
    xs = rng.permutation(xs)
    for x in xs:
        col = world.mat[:, x]
        solid = M.SOLID[col]
        # every empty->solid transition is a candidate floor
        floor = solid[1:] & ~solid[:-1]
        for y in np.nonzero(floor)[0] + 1:
            y = int(y)
            if y < 14 or y > world.h - 18:
                continue
            above = col[y - 9:y]
            if np.any(M.SOLID[above]) or np.any(M.LIQUID[above]):
                continue
            if int(col[y]) == M.EXPOWDER or M.CONTACT_DPS[int(col[y])] > 0:
                continue
            spawns.append((int(x), y - 4))
            break
        if len(spawns) >= n:
            break
    return spawns


def generate(biome: str, seed: int, w: int = GRID_W, h: int = GRID_H) -> MapSpec:
    rng = np.random.default_rng(seed ^ 0xB10B35)
    world = World(seed, w, h)
    if biome.startswith("map:"):
        data = np.load(biome[4:])
        world.mat[:] = data["mat"]
        world.shade[:] = data["shade"]
        _ocean(world, 10)
        spec = MapSpec(biome, world, sky_top=(24, 28, 48),
                       sky_bottom=(70, 80, 120), decor="stars", light=0.9)
    else:
        fn = _GENERATORS.get(biome, _gen_island)
        spec = fn(world, rng)
    spec.spawns = _find_spawns(world)
    world.ambient = spec.ambient
    # let the freshly painted world have one full-grid wake
    world._wake_box = [0, h, 0, w]
    return spec


# --------------------------------------------------------------- biomes ----
def _gen_island(world, rng):
    h, w = world.h, world.w
    surf = _heightmap(rng, w, int(h * 0.52), int(h * 0.22))
    _fill_below(world, surf, M.DIRT)
    _fill_below(world, surf + 26, M.STONE)
    # caves
    cav = _fbm(rng, h, w, 36, 3)
    yy = np.arange(h)[:, None]
    carve = (cav > 0.62) & (yy > surf[None, :] + 6)
    world.mat[carve] = M.EMPTY
    # water caves
    _pockets(world, rng, 6, M.WATER, 6, 12, int(h * 0.6), h - 30)
    _pockets(world, rng, 3, M.OIL, 4, 8, int(h * 0.65), h - 30)
    _platforms(world, rng, 4, M.WOOD)
    _grass_tops(world)
    _barrels(world, rng, 4)
    _ocean(world)
    return MapSpec("island", world, sky_top=(36, 62, 110),
                   sky_bottom=(196, 110, 74), ambient=22, decor="clouds",
                   light=1.0)


def _gen_volcano(world, rng):
    h, w = world.h, world.w
    mid = w // 2
    xs = np.arange(w)
    cone = (int(h * 0.78) - (np.maximum(0, 90 - np.abs(xs - mid)) * 1.4)).astype(int)
    rough = _heightmap(rng, w, int(h * 0.62), int(h * 0.10))
    surf = np.minimum(cone, rough)
    _fill_below(world, surf, M.STONE)
    cav = _fbm(rng, h, w, 30, 3)
    yy = np.arange(h)[:, None]
    world.mat[(cav > 0.64) & (yy > surf[None, :] + 5)] = M.EMPTY
    # crater full of lava + buried lava pockets + powder veins
    world.paint(mid, surf[mid] + 4, 14, M.LAVA, mode="replace")
    _pockets(world, rng, 7, M.LAVA, 5, 11, int(h * 0.55), h - 25)
    _pockets(world, rng, 6, M.EXPOWDER, 4, 8, int(h * 0.5), h - 30)
    _pockets(world, rng, 4, M.GAS, 5, 9, int(h * 0.5), h - 30)
    world.mat[(_fbm(rng, h, w, 24, 2) > 0.74) & (yy > surf[None, :])] = M.GRAVEL
    _ocean(world, 12, M.LAVA)
    return MapSpec("volcano", world, sky_top=(22, 8, 14),
                   sky_bottom=(96, 30, 20), ambient=36, flood_mat=M.LAVA,
                   decor="embers", light=0.85)


def _gen_sewer(world, rng):
    h, w = world.h, world.w
    world.mat[:] = M.STONE
    world.mat[:, 0] = world.mat[:, -1] = M.BEDROCK
    # carve rooms and tunnels
    rooms = _fbm(rng, h, w, 40, 3)
    world.mat[rooms > 0.52] = M.EMPTY
    for y in range(40, h - 20, 44):                  # horizontal pipe galleries
        world.mat[y:y + 14, 10:w - 10] = M.EMPTY
        world.mat[y + 14:y + 17, 10:w - 10] = M.METAL
    _pockets(world, rng, 8, M.ACID, 5, 10, 30, h - 24, solid_only=False)
    _pockets(world, rng, 8, M.SLUDGE, 5, 10, 30, h - 24, solid_only=False)
    _pockets(world, rng, 5, M.TOXGAS, 5, 8, 20, h - 40, solid_only=False, life=255)
    _pockets(world, rng, 4, M.WOOD, 4, 7, 40, h - 30)
    world.mat[0:6, :] = M.BEDROCK                    # sealed ceiling
    _ocean(world, 12, M.ACID)
    return MapSpec("sewer", world, sky_top=(10, 18, 12),
                   sky_bottom=(28, 48, 30), ambient=18, flood_mat=M.ACID,
                   decor="drips", light=0.6, open_sky=False)


def _gen_tundra(world, rng):
    h, w = world.h, world.w
    surf = _heightmap(rng, w, int(h * 0.5), int(h * 0.2))
    _fill_below(world, surf, M.SNOW, 8)
    _fill_below(world, surf + 8, M.ICE, 24)
    _fill_below(world, surf + 32, M.STONE)
    cav = _fbm(rng, h, w, 34, 3)
    yy = np.arange(h)[:, None]
    world.mat[(cav > 0.63) & (yy > surf[None, :] + 10)] = M.EMPTY
    _pockets(world, rng, 6, M.WATER, 6, 12, int(h * 0.6), h - 26)
    _pockets(world, rng, 3, M.CRYSTAL, 4, 7, int(h * 0.55), h - 30)
    _ocean(world)
    return MapSpec("tundra", world, sky_top=(12, 18, 36),
                   sky_bottom=(86, 118, 156), ambient=-12, decor="snow",
                   light=0.95)


def _gen_desert(world, rng):
    h, w = world.h, world.w
    surf = _heightmap(rng, w, int(h * 0.5), int(h * 0.24), cell=80)
    _fill_below(world, surf, M.SAND, 20)
    _fill_below(world, surf + 20, M.DIRT, 16)
    _fill_below(world, surf + 36, M.STONE)
    cav = _fbm(rng, h, w, 38, 3)
    yy = np.arange(h)[:, None]
    world.mat[(cav > 0.66) & (yy > surf[None, :] + 12)] = M.EMPTY
    _pockets(world, rng, 6, M.OIL, 6, 12, int(h * 0.62), h - 26)
    _pockets(world, rng, 4, M.GAS, 5, 8, int(h * 0.55), h - 30)
    _barrels(world, rng, 3)
    _ocean(world)
    return MapSpec("desert", world, sky_top=(64, 38, 28),
                   sky_bottom=(180, 122, 64), ambient=34, decor="dust",
                   light=1.05)


def _gen_cavern(world, rng):
    h, w = world.h, world.w
    world.mat[:] = M.STONE
    world.mat[:, 0] = world.mat[:, -1] = M.BEDROCK
    cav = _fbm(rng, h, w, 44, 4)
    world.mat[cav > 0.5] = M.EMPTY
    dirt = _fbm(rng, h, w, 20, 2)
    world.mat[(world.mat == M.STONE) & (dirt > 0.6)] = M.DIRT
    _pockets(world, rng, 7, M.CRYSTAL, 3, 7, 20, h - 24)
    _pockets(world, rng, 5, M.MAGIC, 4, 9, 40, h - 24, solid_only=False)
    _pockets(world, rng, 5, M.WATER, 5, 10, 40, h - 24, solid_only=False)
    _pockets(world, rng, 3, M.GAS, 5, 8, 20, h - 40, solid_only=False)
    world.mat[0:6, :] = M.BEDROCK
    _ocean(world, 10)
    return MapSpec("cavern", world, sky_top=(8, 6, 16),
                   sky_bottom=(20, 14, 40), ambient=14, decor="spores",
                   light=0.45, open_sky=False)


def _gen_junkyard(world, rng):
    h, w = world.h, world.w
    surf = _heightmap(rng, w, int(h * 0.6), int(h * 0.12))
    _fill_below(world, surf, M.DIRT)
    _fill_below(world, surf + 18, M.STONE)
    # stacks of junk: metal slabs, wood, gravel piles
    for _ in range(14):
        x = int(rng.integers(20, w - 60))
        y = int(rng.integers(int(h * 0.25), int(h * 0.6)))
        kind = rng.integers(0, 3)
        if kind == 0:
            world.mat[y:y + 4, x:x + int(rng.integers(18, 44))] = M.METAL
        elif kind == 1:
            world.mat[y:y + 3, x:x + int(rng.integers(14, 30))] = M.WOOD
        else:
            world.paint(x, y, int(rng.integers(5, 10)), M.GRAVEL, mode="replace")
    _pockets(world, rng, 6, M.OIL, 5, 11, int(h * 0.55), h - 26)
    _barrels(world, rng, 7)
    _pockets(world, rng, 3, M.GAS, 4, 8, int(h * 0.4), h - 40)
    _grass_tops(world)
    _ocean(world, 12, M.SLUDGE)
    return MapSpec("junkyard", world, sky_top=(26, 20, 22),
                   sky_bottom=(92, 66, 48), ambient=20, flood_mat=M.SLUDGE,
                   decor="dust", light=0.8)


def _gen_mine(world, rng):
    h, w = world.h, world.w
    world.mat[:] = M.DIRT
    world.mat[:, 0] = world.mat[:, -1] = M.BEDROCK
    rock = _fbm(rng, h, w, 26, 2)
    world.mat[rock > 0.62] = M.STONE
    # tunnels with wood supports
    cav = _fbm(rng, h, w, 36, 3)
    tunnels = cav > 0.58
    world.mat[tunnels] = M.EMPTY
    for x in range(16, w - 16, 22):
        col = world.mat[:, x]
        ys = np.nonzero(col == M.EMPTY)[0]
        if len(ys):
            y = ys[-1]
            world.mat[max(0, y - 12):y + 1, x:x + 2] = M.WOOD
    _pockets(world, rng, 10, M.EXPOWDER, 4, 9, 20, h - 24)
    _pockets(world, rng, 6, M.GAS, 5, 9, 20, h - 40, solid_only=False)
    _pockets(world, rng, 4, M.WATER, 5, 9, 40, h - 26, solid_only=False)
    _pockets(world, rng, 3, M.NITRO, 3, 5, 60, h - 30)
    world.mat[0:6, :] = M.BEDROCK
    _ocean(world, 10)
    return MapSpec("mine", world, sky_top=(14, 10, 8),
                   sky_bottom=(50, 36, 24), ambient=18, decor="dust",
                   light=0.5, open_sky=False)


def _gen_lab(world, rng):
    h, w = world.h, world.w
    # big concrete box with floors
    world.mat[:] = M.EMPTY
    world.mat[:, 0:4] = M.STONE
    world.mat[:, -4:] = M.STONE
    world.mat[0:4, :] = M.STONE
    world.mat[-6:, :] = M.STONE
    floors = range(int(h * 0.22), h - 20, int(h * 0.2))
    for i, y in enumerate(floors):
        gap = int(rng.integers(30, w - 70))
        world.mat[y:y + 4, 4:gap] = M.METAL
        world.mat[y:y + 4, gap + 44:w - 4] = M.METAL
        # chemical vats on each floor
        for _ in range(3):
            x = int(rng.integers(30, w - 30))
            chem = rng.choice([M.ACID, M.NITRO, M.MAGIC, M.SLUDGE, M.WATER])
            world.mat[y - 8:y, x - 5:x + 5] = M.GLASS
            world.paint(x, y - 5, 4, int(chem), mode="replace")
    _pockets(world, rng, 4, M.GAS, 4, 7, 20, h - 40, solid_only=False)
    _platforms(world, rng, 5, M.METAL)
    world.mat[:, 0] = world.mat[:, -1] = M.BEDROCK
    world.mat[-1, :] = M.BEDROCK
    return MapSpec("lab", world, sky_top=(18, 22, 30),
                   sky_bottom=(40, 50, 70), ambient=20, decor="sparks",
                   light=0.75, open_sky=False)


def _gen_candy(world, rng):
    h, w = world.h, world.w
    surf = _heightmap(rng, w, int(h * 0.5), int(h * 0.26), cell=50)
    _fill_below(world, surf, M.SLIME, 10)
    _fill_below(world, surf + 10, M.SAND, 18)     # "sugar"
    _fill_below(world, surf + 28, M.STONE)
    cav = _fbm(rng, h, w, 32, 3)
    yy = np.arange(h)[:, None]
    world.mat[(cav > 0.64) & (yy > surf[None, :] + 8)] = M.EMPTY
    _pockets(world, rng, 8, M.SLIME, 5, 11, int(h * 0.5), h - 26, solid_only=False)
    _pockets(world, rng, 4, M.MAGIC, 4, 8, int(h * 0.55), h - 26)
    _ocean(world, 12, M.SLIME)
    return MapSpec("candy", world, sky_top=(38, 14, 42),
                   sky_bottom=(176, 96, 140), ambient=22, flood_mat=M.SLIME,
                   decor="bubbles", light=0.95)


def _gen_moon(world, rng):
    h, w = world.h, world.w
    surf = _heightmap(rng, w, int(h * 0.62), int(h * 0.14), cell=70)
    _fill_below(world, surf, M.GRAVEL, 10)
    _fill_below(world, surf + 10, M.STONE)
    cav = _fbm(rng, h, w, 30, 3)
    yy = np.arange(h)[:, None]
    world.mat[(cav > 0.66) & (yy > surf[None, :] + 6)] = M.EMPTY
    # metal domes with gas inside
    for _ in range(4):
        x = int(rng.integers(40, w - 40))
        sy = int(surf[x])
        world.paint(x, sy, 16, M.METAL, mode="replace")
        world.paint(x, sy, 13, M.GAS, mode="replace", life=0)
        world.paint(x, sy + 6, 8, M.STONE, mode="replace")
    _pockets(world, rng, 4, M.CRYSTAL, 3, 6, int(h * 0.6), h - 26)
    _ocean(world, 8)
    return MapSpec("moon", world, sky_top=(4, 4, 10),
                   sky_bottom=(16, 16, 30), ambient=-30, gravity_scale=0.45,
                   decor="stars", light=0.7)


_GENERATORS = {
    "island": _gen_island, "volcano": _gen_volcano, "sewer": _gen_sewer,
    "tundra": _gen_tundra, "desert": _gen_desert, "cavern": _gen_cavern,
    "junkyard": _gen_junkyard, "mine": _gen_mine, "lab": _gen_lab,
    "candy": _gen_candy, "moon": _gen_moon,
}
