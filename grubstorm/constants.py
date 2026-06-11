"""Global tuning constants for GRUBSTORM."""

# --- Simulation grid -------------------------------------------------------
GRID_W = 480
GRID_H = 270
CELL_SCALE = 3                      # screen pixels per sim cell
VIEW_W = GRID_W * CELL_SCALE        # 1440
VIEW_H = GRID_H * CELL_SCALE        # 810

FPS = 60
SIM_SUBSTEPS = 2                    # liquid/powder passes per frame (snappier flow)

# --- Physics ---------------------------------------------------------------
GRAVITY = 0.12                      # cells / tick^2 for entities
MAX_WIND = 0.045                    # horizontal accel on wind-affected projectiles
WIND_ACCEL = 0.012                  # per-tick wind acceleration on projectiles
TERMINAL_VY = 4.0

GRUB_RADIUS = 2.6
GRUB_WALK_SPEED = 0.55
GRUB_STEP_UP = 4                    # max pixels a grub can step up
GRUB_SCRAMBLE = 10                  # short rough walls are climbed slowly
GRUB_JUMP_VY = -2.25
GRUB_JUMP_VX = 0.9
FALL_DMG_MIN_VY = 2.6
FALL_DMG_SCALE = 16.0

# --- Turn structure --------------------------------------------------------
TURN_SECONDS = 45
RETREAT_SECONDS = 3
SETTLE_TIMEOUT = 9.0                # max seconds to wait for world to calm down
SUDDEN_DEATH_AFTER = 8 * 60        # seconds of match time
CRATE_CHANCE = 0.35                # chance per turn-end

# --- Health ----------------------------------------------------------------
GRUB_HP = 100
DROWN_DPS = 26
LAVA_DPS = 55
ACID_DPS = 32
FIRE_DPS = 18
SLUDGE_POISON = 4                   # poison damage applied at turn end
SHOCK_DPS = 40

# --- Defaults --------------------------------------------------------------
DEFAULT_PORT = 31999

TEAM_COLORS = [
    ((255, 92, 92),  "Crimson"),
    ((86, 156, 255), "Azure"),
    ((120, 220, 100), "Venom"),
    ((255, 200, 70), "Goldrush"),
    ((210, 120, 255), "Hex"),
    ((90, 230, 220), "Tide"),
    ((255, 140, 200), "Bubble"),
    ((200, 200, 210), "Chrome"),
]

# colorblind-friendly alternative palette (Okabe-Ito inspired)
TEAM_COLORS_CB = [
    ((230, 159, 0),  "Amber"),
    ((86, 180, 233), "Sky"),
    ((0, 158, 115),  "Teal"),
    ((240, 228, 66), "Lemon"),
    ((0, 114, 178),  "Cobalt"),
    ((213, 94, 0),   "Rust"),
    ((204, 121, 167), "Orchid"),
    ((220, 220, 220), "Silver"),
]

GRUB_NAMES = [
    "Borp", "Mibble", "Sgt. Crumb", "Wobbles", "Gnasher", "Pip", "Doomlet",
    "Squib", "Captain Moist", "Fizz", "Grub Norris", "Tater", "Bumble",
    "Lord Splat", "Nugget", "Wiggly", "Dr. Boom", "Pickle", "Zorp", "Chunk",
    "Sir Loin", "Meep", "Gristle", "Bap", "Snug", "Kaboomba", "Toast",
    "Mr. Drip", "Flopsy", "Grimble", "Yeet", "Soggy", "Biscuit", "Plonk",
]

TEAM_NAME_POOL = [
    "Mud Maulers", "Acid Reflux", "The Soggy Bottoms", "Lava Lads",
    "Static Cling", "Boom Friends", "The Unhinged", "Crate Expectations",
    "Sludge Patrol", "Frostbite Club", "Oily Boyz", "The Detonators",
]
