"""Material definitions.

Every cell in the world grid holds a material id. All behaviour is data-driven
from the numpy property arrays below so the simulation passes can stay fully
vectorized. If you add a material, add a row everywhere.
"""
import numpy as np

# --- ids -------------------------------------------------------------------
EMPTY    = 0
BEDROCK  = 1
STONE    = 2
DIRT     = 3
METAL    = 4
WOOD     = 5
ICE      = 6
GLASS    = 7
GRASS    = 8
CRYSTAL  = 9   # emissive decorative mineral
SAND     = 10
GRAVEL   = 11  # broken stone chunks
SNOW     = 12
ASH      = 13
EXPOWDER = 14  # explosive powder
WATER    = 15
OIL      = 16
ACID     = 17
LAVA     = 18
SLUDGE   = 19  # toxic
SLIME    = 20  # sticky
MAGIC    = 21  # glowing chaos liquid
NITRO    = 22  # liquid explosive
NAPALM   = 23  # sticky fire gel
SMOKE    = 24
STEAM    = 25
GAS      = 26  # flammable gas
TOXGAS   = 27
FIRE     = 28

N_MATS = 32

# --- phases ----------------------------------------------------------------
# ordering matters: "free to move into" is phase <= P_GAS, "solid ground"
# is phase >= P_POWDER. Keep it that way for fast comparisons.
P_EMPTY, P_GAS, P_LIQUID, P_POWDER, P_STATIC = 0, 1, 2, 3, 4

PHASE = np.zeros(N_MATS, np.uint8)
for m in (BEDROCK, STONE, DIRT, METAL, WOOD, ICE, GLASS, GRASS, CRYSTAL):
    PHASE[m] = P_STATIC
for m in (SAND, GRAVEL, SNOW, ASH, EXPOWDER):
    PHASE[m] = P_POWDER
for m in (WATER, OIL, ACID, LAVA, SLUDGE, SLIME, MAGIC, NITRO, NAPALM):
    PHASE[m] = P_LIQUID
for m in (SMOKE, STEAM, GAS, TOXGAS, FIRE):
    PHASE[m] = P_GAS

# density: heavier sinks below lighter (liquids/gases); powders sink in
# liquids whose density is lower than theirs.
DENSITY = np.zeros(N_MATS, np.int16)
DENSITY[[SAND, GRAVEL, SNOW, ASH, EXPOWDER]] = [150, 170, 60, 50, 130]
DENSITY[[WATER, OIL, ACID, LAVA, SLUDGE, SLIME, MAGIC, NITRO, NAPALM]] = \
    [100, 80, 105, 220, 115, 130, 95, 90, 110]
DENSITY[[SMOKE, STEAM, GAS, TOXGAS, FIRE]] = [5, 4, 6, 5, 3]
DENSITY[[BEDROCK, STONE, DIRT, METAL, WOOD, ICE, GLASS, GRASS, CRYSTAL]] = 255

# chance per tick to ignite when touching fire/heat (0 = inert)
FLAMMABLE = np.zeros(N_MATS, np.float32)
FLAMMABLE[WOOD] = 0.030
FLAMMABLE[GRASS] = 0.120
FLAMMABLE[OIL] = 0.550
FLAMMABLE[NAPALM] = 0.900
FLAMMABLE[GAS] = 0.700
FLAMMABLE[EXPOWDER] = 1.0     # detonates, handled specially
FLAMMABLE[NITRO] = 1.0        # detonates
FLAMMABLE[SLUDGE] = 0.015     # smoulders into toxic gas
FLAMMABLE[SLIME] = 0.004

# how many ticks a burning cell keeps burning
BURN_FUEL = np.zeros(N_MATS, np.uint8)
BURN_FUEL[WOOD] = 220
BURN_FUEL[GRASS] = 60
BURN_FUEL[OIL] = 120
BURN_FUEL[NAPALM] = 255
BURN_FUEL[GAS] = 8
BURN_FUEL[SLUDGE] = 90
BURN_FUEL[SLIME] = 120

# what's left when fuel runs out
BURN_RESIDUE = np.zeros(N_MATS, np.uint8)
BURN_RESIDUE[WOOD] = ASH
BURN_RESIDUE[GRASS] = ASH
BURN_RESIDUE[SLUDGE] = TOXGAS
# everything else -> EMPTY

# chance per tick that acid eats a touching cell
CORRODIBLE = np.zeros(N_MATS, np.float32)
CORRODIBLE[[DIRT, SAND, GRASS]] = 0.30
CORRODIBLE[[WOOD]] = 0.22
CORRODIBLE[[STONE, GRAVEL]] = 0.06
CORRODIBLE[[ICE, SNOW]] = 0.35
CORRODIBLE[[METAL]] = 0.015
CORRODIBLE[[GLASS, CRYSTAL]] = 0.01
CORRODIBLE[[ASH]] = 0.5

CONDUCTIVE = np.zeros(N_MATS, bool)
CONDUCTIVE[[METAL, WATER, SLUDGE]] = True

# explosion resistance: cells survive blasts whose local power < hardness
HARDNESS = np.zeros(N_MATS, np.int16)
HARDNESS[BEDROCK] = 32767
HARDNESS[METAL] = 95
HARDNESS[STONE] = 62
HARDNESS[GLASS] = 25
HARDNESS[WOOD] = 38
HARDNESS[CRYSTAL] = 30
HARDNESS[DIRT] = 18
HARDNESS[GRASS] = 10
HARDNESS[ICE] = 26
HARDNESS[[SAND, GRAVEL, SNOW, ASH, EXPOWDER]] = 8

# chance to SKIP a lateral move this tick (1 = doesn't spread)
VISCOSITY = np.zeros(N_MATS, np.float32)
VISCOSITY[[WATER, OIL, ACID, LAVA, SLUDGE, SLIME, MAGIC, NITRO, NAPALM]] = \
    [0.00, 0.25, 0.10, 0.88, 0.55, 0.92, 0.15, 0.10, 0.85]

# powder character. SLIDE is the chance per tick to topple down a
# diagonal (how lively it flows); CLUMPY powders only topple over steep
# edges (a 2-cell drop), so they hold slope-1 staircases forever — snow
# drifts and ash heaps stand steep while dry sand relaxes into flat cones.
SLIDE = np.zeros(N_MATS, np.float32)
SLIDE[[SAND, GRAVEL, SNOW, ASH, EXPOWDER]] = [0.75, 0.50, 0.35, 0.30, 0.65]
CLUMPY = np.zeros(N_MATS, bool)
CLUMPY[[SNOW, ASH]] = True

# stickiness: chance to cling instead of flowing while touching a static
# cell. Sticky gels coat walls and ceilings instead of running off them.
STICKY = np.zeros(N_MATS, np.float32)
STICKY[[SLIME, NAPALM, SLUDGE]] = [0.78, 0.88, 0.30]

# light emission (r, g, b) 0..255, additive, blurred by the renderer
EMISSION = np.zeros((N_MATS, 3), np.uint8)
EMISSION[LAVA] = (255, 110, 20)
EMISSION[FIRE] = (255, 160, 40)
EMISSION[NAPALM] = (200, 90, 20)
EMISSION[ACID] = (60, 220, 40)
EMISSION[MAGIC] = (170, 80, 255)
EMISSION[CRYSTAL] = (80, 180, 255)
EMISSION[SLUDGE] = (40, 120, 20)
EMISSION[TOXGAS] = (30, 80, 15)

# contact damage per second when a grub stands inside the material
CONTACT_DPS = np.zeros(N_MATS, np.float32)
CONTACT_DPS[LAVA] = 55
CONTACT_DPS[ACID] = 32
CONTACT_DPS[FIRE] = 18
CONTACT_DPS[NAPALM] = 30
CONTACT_DPS[TOXGAS] = 6

POISONOUS = np.zeros(N_MATS, bool)
POISONOUS[[SLUDGE, TOXGAS]] = True

# --- palette: 4 shades per material, RGB -----------------------------------
# wide, darker-biased value range: with per-pixel grain this reads like
# Noita's speckled materials instead of flat plastic
def _shades(base, spread=18):
    r, g, b = base
    out = []
    for k in (-2, -1, 0, 1):
        out.append((max(0, min(255, r + k * spread)),
                    max(0, min(255, g + k * spread)),
                    max(0, min(255, b + k * spread))))
    return out

PALETTE = np.zeros((N_MATS, 4, 3), np.uint8)
# Noita-ish art direction: dark, desaturated, earthy solids so that
# liquids, fire and anything emissive glows against the world.
_base = {
    EMPTY:    (0, 0, 0),
    BEDROCK:  (26, 23, 32),
    STONE:    (76, 72, 82),
    DIRT:     (94, 62, 42),
    METAL:    (118, 124, 136),
    WOOD:     (118, 78, 44),
    ICE:      (130, 182, 220),
    GLASS:    (150, 188, 200),
    GRASS:    (70, 134, 50),
    CRYSTAL:  (110, 190, 250),
    SAND:     (196, 160, 90),
    GRAVEL:   (98, 93, 100),
    SNOW:     (218, 226, 240),
    ASH:      (74, 71, 76),
    EXPOWDER: (180, 48, 48),
    WATER:    (28, 56, 104),
    OIL:      (42, 34, 28),
    ACID:     (108, 235, 48),
    LAVA:     (250, 110, 30),
    SLUDGE:   (78, 118, 34),
    SLIME:    (210, 96, 174),
    MAGIC:    (180, 90, 250),
    NITRO:    (76, 222, 158),
    NAPALM:   (230, 130, 42),
    SMOKE:    (52, 50, 58),
    STEAM:    (172, 180, 194),
    GAS:      (134, 144, 78),
    TOXGAS:   (78, 108, 42),
    FIRE:     (255, 170, 40),
}
for _m, _c in _base.items():
    PALETTE[_m] = _shades(_c)
# liquids and gases stay flatter — their texture is their motion
for _m in (WATER, OIL, ACID, SLUDGE, SLIME, NITRO, NAPALM,
           SMOKE, STEAM, GAS, TOXGAS):
    PALETTE[_m] = _shades(_base[_m], spread=6)
# fire gets a hotter ramp instead of even shades
PALETTE[FIRE] = [(255, 90, 20), (255, 140, 30), (255, 190, 60), (255, 235, 120)]
PALETTE[LAVA] = [(200, 60, 15), (235, 95, 20), (255, 130, 35), (255, 170, 60)]
PALETTE[MAGIC] = [(150, 60, 230), (180, 90, 250), (210, 120, 255), (240, 170, 255)]

NAMES = {
    EMPTY: "Erase", BEDROCK: "Bedrock", STONE: "Stone", DIRT: "Dirt",
    METAL: "Metal", WOOD: "Wood", ICE: "Ice", GLASS: "Glass", GRASS: "Grass",
    CRYSTAL: "Crystal", SAND: "Sand", GRAVEL: "Gravel", SNOW: "Snow",
    ASH: "Ash", EXPOWDER: "Boom Powder", WATER: "Water", OIL: "Oil",
    ACID: "Acid", LAVA: "Lava", SLUDGE: "Toxic Sludge", SLIME: "Slime",
    MAGIC: "Magic Goo", NITRO: "Nitro", NAPALM: "Napalm Gel",
    SMOKE: "Smoke", STEAM: "Steam", GAS: "Flammable Gas", TOXGAS: "Toxic Gas",
    FIRE: "Fire",
}

# materials a grub can stand on
SOLID = (PHASE == P_STATIC) | (PHASE == P_POWDER)
LIQUID = PHASE == P_LIQUID
