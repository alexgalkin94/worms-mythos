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
def _shades(base, spread=14):
    r, g, b = base
    out = []
    for k in (-1, 0, 1, 2):
        out.append((max(0, min(255, r + k * spread)),
                    max(0, min(255, g + k * spread)),
                    max(0, min(255, b + k * spread))))
    return out

PALETTE = np.zeros((N_MATS, 4, 3), np.uint8)
_base = {
    EMPTY:    (0, 0, 0),
    BEDROCK:  (38, 34, 44),
    STONE:    (110, 108, 118),
    DIRT:     (124, 86, 58),
    METAL:    (146, 152, 164),
    WOOD:     (150, 104, 58),
    ICE:      (160, 210, 240),
    GLASS:    (180, 215, 225),
    GRASS:    (92, 168, 62),
    CRYSTAL:  (110, 190, 250),
    SAND:     (216, 184, 110),
    GRAVEL:   (130, 126, 132),
    SNOW:     (235, 240, 250),
    ASH:      (95, 92, 96),
    EXPOWDER: (200, 60, 60),
    WATER:    (48, 110, 200),
    OIL:      (60, 48, 40),
    ACID:     (120, 230, 60),
    LAVA:     (250, 110, 30),
    SLUDGE:   (90, 130, 40),
    SLIME:    (230, 120, 190),
    MAGIC:    (180, 90, 250),
    NITRO:    (90, 230, 170),
    NAPALM:   (235, 140, 50),
    SMOKE:    (70, 68, 74),
    STEAM:    (190, 198, 210),
    GAS:      (150, 160, 90),
    TOXGAS:   (90, 120, 50),
    FIRE:     (255, 170, 40),
}
for _m, _c in _base.items():
    PALETTE[_m] = _shades(_c)
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
