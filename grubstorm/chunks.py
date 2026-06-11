"""Noita-style chunk map assembly.

Maps are stitched from hand-authored ASCII chunk templates instead of raw
noise. Templates use abstract material symbols that each biome maps to its
own materials, so one library of set-pieces serves every arena:

    .  air                    ~  pool liquid
    #  primary solid (rock)   o  hazard liquid/gas
    %  topsoil / secondary    *  treasure vein
    =  platform beam          ?  ragged 50% rock
    B  buried barrel          C  emissive crystal     G  gas pocket

Rasterization samples each cell with a random +-2 wobble, so straight
template edges come out as organic, ragged borders. Surface chunks carry
edge-height tags and are chained wang-tile style so skylines connect;
underground chunks all keep a mid corridor open at both edges. A few
erosion worms are carved across the seams afterwards as procedural glue.
"""
import numpy as np

from . import materials as M

CH = 4                # cells per template character
CHUNK_W = 15          # template width in characters (15 * 4 = 60 cells)

# ---------------------------------------------------------------- library --
# Surface chunks: 17 rows. Tags (left, right) are edge ground heights:
# 0 = low (ground at char-row 12), 1 = mid (row 8), 2 = high (row 4).
SURFACE = [
    # rolling lowland with a pond dug into it
    (0, 0, """
...............
...............
...............
...............
...............
...............
...............
...............
...............
.......??......
.....?%%%%?....
...?%%%%%%%?...
%%%%%%~~~%%%%%%
%%%%%~~~~~%%%%%
####%%%%%%%####
###############
###############
"""),
    # low flats with a wooden watch platform
    (0, 0, """
...............
...............
...............
...............
...............
.....======....
...............
...............
..====.........
...............
...............
...............
%%%%%%%%%%%%%%%
%%%%%%%%%%%%%%%
######*########
###############
###############
"""),
    # climb from low to high mesa
    (0, 2, """
...............
...............
...............
...........????
.........?%%%%%
.........?%%%%%
........?%%%%%%
.......?%%%%%%%
......?%%%%%%%%
.....?%%%%#####
....?%%%%######
..??%%%%#######
%%%%%%%########
%%%%%##########
###############
###############
###############
"""),
    # drop from high mesa to low
    (2, 0, """
...............
...............
...............
????...........
%%%%%?.........
%%%%%?.........
%%%%%%?........
%%%%%%%?.......
%%%%%%%%?......
#####%%%%?.....
######%%%%?....
#######%%%%??..
########%%%%%%%
##########%%%%%
###############
###############
###############
"""),
    # mid plateau cracked by a ravine with a bridge
    (1, 1, """
...............
...............
...............
...............
...............
...............
...............
....=========..
%%%%?.......?%%
%%%%?.......?%%
####?.......?##
####?..~~~..?##
####?.~~~~~.?##
#####?~~~~~?###
######?????####
###############
###############
"""),
    # mid ground with a buried treasure vault
    (1, 1, """
...............
...............
...............
...............
...............
...............
...............
......???......
%%%%%%%%%%%%%%%
%%%%%%%%%%%%%%%
####...........
####.***.B.***.
####...........
###############
###############
###############
###############
"""),
    # twin spires with a hanging walkway
    (1, 1, """
...............
...............
....??...??....
...?%%?.?%%?...
...?%%===%%?...
...?%%?.?%%?...
...?%%?.?%%?...
...?%%?.?%%?...
%%%%%%?.?%%%%%%
%%%%%%?.?%%%%%%
######?.?######
######?.?######
######???######
###############
###############
###############
###############
"""),
    # high battlements with crystal growth
    (2, 2, """
...............
...............
...............
..??...........
%%%%?..C..??...
%%%%%???????%%%
%%%%%%%%%%%%%%%
####%%%####%%##
###############
######...######
#####..G..#####
######...######
######?.?######
###############
###############
###############
###############
"""),
    # high saddle dipping to mid
    (2, 1, """
...............
...............
...............
????...........
%%%%??.........
%%%%%%??.......
######%%??.....
#######%%%??...
########%%%%%%%
##########%%%%%
###############
######o########
###############
###############
###############
###############
###############
"""),
    # mid rising to high
    (1, 2, """
...............
...............
...............
..........????%
.........?%%%%%
........?%%%%%%
......??%%%%%%%
%%%%%%%%%%%%###
%%%%%%%%#######
###############
#####*#########
###############
###############
###############
###############
###############
###############
"""),
    # low ragged badlands with oil seep
    (0, 1, """
...............
...............
...............
...............
...............
...............
...............
.........??%%%%
......??%%%%%%%
....?%%%%%%%%%%
..?%%%%%%%%%%%%
?%%%%%%%%%%%%%%
%%%%%%%oo%%%%%%
%%%%%%oooo%%%%%
######oooo#####
###############
###############
"""),
    # mid falling to low over a barrel cache
    (1, 0, """
...............
...............
...............
...............
...............
...............
...............
????...........
%%%%%??........
%%%%%%%??......
######%%%??....
#######%%%%????
########%%%%%%%
####.B.########
###############
###############
###############
"""),
    # collapsing arch over a deep gap
    (1, 1, """
...............
...............
...............
....???????....
...??.....??...
..??.......??..
%%%?.........?%
%%%?.........?%
####?.......?##
####?.......?##
####?..~~~..?##
#####?.~~~.?###
######??.??####
###############
###############
###############
###############
"""),
    # low marsh with reeds and a buried cache
    (0, 0, """
...............
...............
...............
...............
...............
...............
...............
...............
...............
..?.?......?.?.
%%%%%~~%%~~%%%%
%%%%%~~%%~~%%%%
%%%%%%%%%%%%%%%
####*###*######
####.B.########
###############
###############
"""),
    # high fortress wall with a gate hole
    (2, 2, """
...............
...............
..=========....
%%%?......?%%%%
%%%?......?%%%%
####?....?#####
####?....?#####
####......#####
####......#####
###############
######C########
###############
###############
###############
###############
###############
###############
"""),
    # tiered ledges down left-to-right
    (2, 0, """
...............
...............
...............
%%%%??.........
%%%%%%.........
#####%%??......
......?%%%.....
.......####....
..........?%?..
...........%%%%
########...%%%%
########...####
###############
###############
###############
###############
###############
"""),
]

# Underground chunks: 18 rows. The corridor contract: char-rows 7-10 are
# open at both edge columns, and the outer border is '#', so any two
# chunks join seamlessly.
UNDER = [
    # great hall with pillars
    """
###############
###############
####.......####
###.........###
##....===....##
##...........##
##..##...##..##
.....#...#.....
.....#...#.....
......???......
...............
##...........##
###....*....###
####.......####
######...######
###############
###############
###############
""",
    # flooded chamber
    """
###############
###############
###############
####......#####
###........####
##..........###
##...........##
...............
...............
......~~~......
....~~~~~~~....
##.~~~~~~~~~.##
##~~~~~~~~~~~##
###~~~~~~~~~###
#####~~~~~#####
###############
###############
###############
""",
    # cross tunnels with a shaft up
    """
######...######
######...######
######...######
######...######
#####.....#####
#####.....#####
#####.....#####
...............
...............
...............
#####.....#####
######...######
######...######
######...######
######...######
######...######
###############
###############
""",
    # treasure vein vault
    """
###############
###############
####*****######
###*******#####
###**#####.####
####......B####
####...........
...............
...............
.....######....
##...##C.##..##
##...##..##..##
###..........##
####........###
###############
###############
###############
###############
""",
    # gas-pocketed warren
    """
###############
####...########
###..G..#######
####...########
########...####
#######..G..###
########...####
...............
......???......
...............
####...########
###.....#######
###..o..#######
####...########
###############
###############
###############
###############
""",
    # collapsed gallery with hanging slabs
    """
###############
###############
##..#####...###
##...###.....##
##?...#...?..##
##??.....??..##
###?.....?...##
....??..?......
...............
...............
##.....??....##
##....????...##
###..??????..##
###############
###############
###############
###############
###############
""",
    # underground lake with an island
    """
###############
###############
###############
###..........##
##............##
##.....=.......
......===......
...............
...............
..~~~..*..~~~..
.~~~~~???~~~~~.
#~~~~#####~~~~#
##~~~#####~~~##
###############
###############
###############
###############
###############
""",
    # dense rock with a winding crack
    """
###############
###############
######.########
#####..########
#####.#########
####..#########
####.##########
...............
.......o.......
...............
#########..####
##########.####
#########..####
#########.#####
########..#####
###############
###############
###############
""",
    """
###############
##....#########
##.G..#########
##....##...####
###.....C..####
####....=..####
####...........
...............
....~~~........
...##~~##......
##.##~~~##...##
##..#####....##
###..........##
###############
###############
###############
###############
###############
""",
    """
###############
###############
###...#...#####
##..*.#.*..####
##..#####..####
##..........###
...............
.....?????.....
...............
##...........##
###.B.....o..##
####.......####
###############
###############
###############
###############
###############
###############
""",
]

# Deep chunks: 12 rows, same corridor contract on rows 4-7.
DEEP = [
    """
###############
###############
####.......####
###..*...*..###
...............
...............
......~~~......
....~~~~~~~....
###~~~~~~~~~###
###############
###############
###############
""",
    """
###############
######...######
#####.....#####
####...B...####
...............
...............
...............
####..***..####
#####*****#####
###############
###############
###############
""",
    """
###############
###############
##.....########
##..G..########
.......????....
...............
...............
....????.......
########..o..##
########.....##
###############
###############
""",
    """
###############
###############
###############
####.......####
............###
......=........
...............
..C.........*..
###############
###############
###############
###############
""",
]


def _parse(tpl, rows_target):
    rows = [r for r in tpl.strip("\n").split("\n")]
    rows = rows[:rows_target]
    while len(rows) < rows_target:        # pad with bedrock-y bottom rows
        rows.append("#" * CHUNK_W)
    return np.array([[ord(c) for c in row.ljust(CHUNK_W, ".")[:CHUNK_W]]
                     for row in rows], dtype=np.int32)


SURF_ROWS, UNDER_ROWS, DEEP_ROWS = 17, 18, 12
_SURFACE = [(l, r, _parse(t, SURF_ROWS)) for (l, r, t) in SURFACE]
_UNDER = [_parse(t, UNDER_ROWS) for t in UNDER]
_DEEP = [_parse(t, DEEP_ROWS) for t in DEEP]


def _rasterize(world, rng, grid, x0, y0, mapping, markers, wobble=2):
    """Stamp a parsed template onto the world with organic edge wobble."""
    hc, wc = grid.shape
    H, W = hc * CH, wc * CH
    y0, x0 = int(y0), int(x0)
    H = min(H, world.h - y0)
    W = min(W, world.w - x0)
    if H <= 0 or W <= 0:
        return
    jy = np.arange(H)[:, None] + rng.integers(-wobble, wobble + 1, (H, W))
    jx = np.arange(W)[None, :] + rng.integers(-wobble, wobble + 1, (H, W))
    cy = np.clip(jy // CH, 0, hc - 1)
    cx = np.clip(jx // CH, 0, wc - 1)
    sym = grid[cy, cx]
    out = world.mat[y0:y0 + H, x0:x0 + W]
    roll = rng.random((H, W))
    solid_a = mapping.get("#", M.STONE)
    for ch, spec in mapping.items():
        mask = sym == ord(ch)
        if not mask.any():
            continue
        if ch == "?":
            out[mask & (roll < 0.5)] = spec
        elif ch == "*":
            out[mask & (roll < 0.8)] = spec
            out[mask & (roll >= 0.8)] = solid_a
        else:
            out[mask] = spec
    out[sym == ord(".")] = M.EMPTY
    # markers: feature positions for post-placement (cell centres)
    for ch in "BCG":
        if ch not in markers:
            continue
        ys, xs = np.nonzero(grid == ord(ch))
        for y, x in zip(ys, xs):
            markers[ch].append((x0 + int(x) * CH + CH // 2,
                                y0 + int(y) * CH + CH // 2))
        # fill the marker char area with rock so features sit inside matter
        mask = sym == ord(ch)
        out[mask] = solid_a


def _chain_surface(rng, n):
    """Pick a wang-chained row of surface chunks (edge heights match)."""
    chain = []
    cur = int(rng.integers(0, 3))
    for _ in range(n):
        fits = [t for t in _SURFACE if t[0] == cur]
        l, r, grid = fits[int(rng.integers(0, len(fits)))]
        chain.append(grid)
        cur = r
    return chain


def _erosion_worms(world, rng, y_min, y_max, n=3):
    """Procedural glue: winding tunnels carved across chunk seams, plus a
    couple of steep shafts that stitch the bands together vertically."""
    for _ in range(n):
        x = float(rng.integers(20, world.w - 20))
        y = float(rng.integers(y_min, y_max))
        ang = float(rng.random()) * 2 * np.pi
        for _ in range(int(rng.integers(60, 140))):
            world.paint(x, y, int(rng.integers(2, 5)), M.EMPTY, mode="erase")
            ang += float(rng.random() - 0.5) * 0.9
            x += np.cos(ang) * 3
            y += np.sin(ang) * 1.6
            if not (10 < x < world.w - 10 and y_min < y < y_max):
                break
    for _ in range(2):
        x = float(rng.integers(40, world.w - 40))
        y = float(y_min)
        while y < y_max - 4:
            world.paint(x, y, int(rng.integers(2, 4)), M.EMPTY, mode="erase")
            x += float(rng.random() - 0.5) * 5
            y += 2.2


def build(world, rng, mapping, sky=56, sealed=False, features=None):
    """Assemble a full map from chunk bands. Returns marker positions.

    Bands: [sky | surface 68 | under 72 | deep 48 | bedrock floor], or for
    sealed maps a rock cap followed by three underground bands.
    """
    markers = {"B": [], "C": [], "G": []}
    cols = world.w // (CHUNK_W * CH)
    if not sealed:
        y = sky
        for c, grid in enumerate(_chain_surface(rng, cols)):
            _rasterize(world, rng, grid, c * CHUNK_W * CH, y, mapping, markers)
        y += SURF_ROWS * CH
        bands = (_UNDER, _DEEP)
        glue_top = sky + SURF_ROWS * CH
    else:
        cap = 14
        world.mat[:cap, :] = mapping.get("#", M.STONE)
        y = cap
        bands = (_UNDER, _UNDER, _DEEP, _DEEP)
        glue_top = cap + 8
    for lib in bands:
        for c in range(cols):
            grid = lib[int(rng.integers(0, len(lib)))]
            # vertical jitter so the bands don't read as ruled lines;
            # the corridor contract is tall enough to absorb +-4 cells
            jy = int(rng.integers(-4, 5))
            _rasterize(world, rng, grid, c * CHUNK_W * CH, y + jy,
                       mapping, markers)
        y += lib[0].shape[0] * CH
    # everything below the deepest band stays open — the ocean lives there
    _erosion_worms(world, rng, glue_top, min(world.h - 24, y))
    _dress_caves(world, rng, mapping.get("#", M.STONE), glue_top,
                 min(world.h - 24, y))
    world.mat[:, 0] = M.BEDROCK
    world.mat[:, -1] = M.BEDROCK
    return markers


def _dress_caves(world, rng, rock, y_min, y_max, n=46):
    """Stalactites and floor mounds: kills the last ruler-straight chunk
    seams and makes corridors read as caves."""
    band = world.mat[y_min:y_max]
    solid = M.SOLID[band]
    below_open = np.zeros_like(solid)
    below_open[:-1] = ~solid[1:]
    ceil_ys, ceil_xs = np.nonzero(solid & below_open)
    if len(ceil_ys) == 0:
        return
    picks = rng.permutation(len(ceil_ys))[:n]
    for i in picks:
        x, y = int(ceil_xs[i]), y_min + int(ceil_ys[i])
        length = int(rng.integers(2, 7))
        if rng.random() < 0.5:                    # stalactite
            for k in range(length):
                world.paint(x + float(rng.random() - 0.5) * 2, y + 1 + k,
                            max(1.0, 2.2 - k * 0.4), rock, mode="fill")
        else:                                     # floor mound below it
            ys = np.nonzero(M.SOLID[world.mat[y:, x]])[0]
            if len(ys) > 1:
                fy = y + int(ys[1])
                world.paint(x, fy, float(rng.integers(2, 5)), rock,
                            mode="fill", noise=0.3)


def place_features(world, rng, markers, crystal=M.CRYSTAL, gas=M.GAS,
                   barrels=True):
    from .mapgen import _barrel_at
    for (x, y) in markers["C"]:
        world.paint(x, y, int(rng.integers(3, 6)), crystal, mode="replace",
                    noise=0.2)
    for (x, y) in markers["G"]:
        world.paint(x, y, int(rng.integers(4, 8)), gas, mode="replace",
                    life=255, noise=0.15)
    if barrels:
        for (x, y) in markers["B"]:
            _barrel_at(world, x, y)
