"""Snapshot round-trips: a serialized world/match restored into a FRESH
object must continue bit-identically to the original.

Part A — World.to_bytes/from_bytes: a busy world is snapshotted into a
fresh World (different seed on purpose). from_bytes restores the seven
cell planes; the remaining sim state (tick, np RNG state, wake/cool boxes,
levelling window, phase/density mirrors...) is synced exactly the way
Game.serialize/restore does for reconnect snapshots — this test documents
that contract. Both worlds then run 600 more ticks in lockstep.

Part B — Game.serialize/restore (the reconnect path): run a bot match to a
quiescent PH_START boundary (the only point the host snapshots at, see
Game.is_quiescent), serialize, push the snapshot through JSON like net.py
does, restore into a fresh Game with fresh Bot objects, and run both
matches 600 ticks in lockstep.
"""
import sys
import os
import json
import random
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _harness as H

H.init_pygame()

from grubstorm.world import World
from grubstorm.game import Game
from grubstorm.ai import Bot
from grubstorm import materials as M

r = H.Runner("test_snapshot")

# ------------------------------------------------ part A: World round-trip
MATS = [M.WATER, M.SAND, M.LAVA, M.OIL, M.ACID,
        M.GAS, M.NITRO, M.EXPOWDER, M.SNOW, M.SLIME]


def busy_world(seed=99, ticks=300):
    w = World(seed)
    rng = random.Random(5)
    for t in range(1, ticks + 1):
        if t % 4 == 0:
            w.paint(rng.randint(100, 280), rng.randint(30, 150),
                    rng.randint(3, 7), rng.choice(MATS), mode="fill")
        w.step()
        w.events.clear()
    return w


def restore_into_fresh(src, fresh_seed=123456):
    """from_bytes + the extra sim state Game.restore syncs alongside it."""
    dst = World(fresh_seed)
    dst.from_bytes(src.to_bytes())
    dst.tick = src.tick
    dst.rng.bit_generator.state = src.rng.bit_generator.state
    dst.gravity_dir = src.gravity_dir
    dst.wind = src.wind
    dst.ambient = src.ambient
    dst.water_level = src.water_level
    dst.level_until = src.level_until
    dst.level_box = list(src.level_box) if src.level_box else None
    dst._wake_box = list(src._wake_box) if src._wake_box else None
    dst._wake_cool = src._wake_cool
    dst._cool_box = list(src._cool_box) if src._cool_box else None
    dst.pending_detonations = list(src.pending_detonations)
    # phase/density mirrors are rebuilt by from_bytes itself; replacing
    # the arrays afterwards would orphan the bound flat swap views
    return dst


t0 = time.perf_counter()
src = busy_world()
snap = src.to_bytes()
r.info(f"busy world built in {time.perf_counter() - t0:.1f}s, "
       f"snapshot {len(snap)} bytes (compressed)")
dst = restore_into_fresh(src)

r.check("World: restored planes hash-identical to source",
        H.world_hash(src) == H.world_hash(dst))
r.check("World: snapshot is compressed and non-trivial",
        0 < len(snap) < src.w * src.h * 13)

div_at = None
for t in range(1, 601):
    src.step(); src.events.clear()
    dst.step(); dst.events.clear()
    if t % 25 == 0 and H.world_hash(src) != H.world_hash(dst):
        div_at = t
        break
r.check("World: 600-tick continuation identical (hash every 25 ticks)",
        div_at is None,
        "no divergence" if div_at is None else f"diverged at tick {div_at}")

# ------------------------------------ part B: Game reconnect snapshot path
SETTINGS = {
    "seed": 4242, "biome": "desert", "turn_seconds": 8,
    "teams": [
        {"name": "A", "color_idx": 0, "n_grubs": 2, "control": "bot:normal"},
        {"name": "B", "color_idx": 1, "n_grubs": 2, "control": "bot:dumb"},
    ],
}


def make_bots():
    return {0: Bot("normal"), 1: Bot("dumb")}


def step_match(g, bots):
    if g.phase == Game.PH_OVER:
        g.step(None)
    else:
        g.step(bots[g.turn_team].act(g))
    g.fx.clear()


g1 = Game(dict(SETTINGS))
bots1 = make_bots()
t0 = time.perf_counter()
quiescent_tick = None
for t in range(1, 20001):
    step_match(g1, bots1)
    if t > 600 and g1.is_quiescent():
        quiescent_tick = t
        break
r.check("Game: reached a quiescent snapshot point",
        quiescent_tick is not None,
        f"tick {quiescent_tick}, turn {g1.turn_no}, "
        f"{time.perf_counter() - t0:.1f}s" if quiescent_tick else
        "no quiescent PH_START within 20000 ticks")
if quiescent_tick is None:
    r.finish()

# serialize -> JSON wire format -> restore, exactly like net.py ships it
snap = json.loads(json.dumps(g1.serialize()))
r.info(f"game snapshot: {len(json.dumps(snap))} JSON bytes")

g2 = Game(dict(SETTINGS))
g2.restore(snap)
bots2 = make_bots()                # a rejoiner starts with fresh bot objects

r.check("Game: restored state hash-identical to source",
        H.game_hash(g1) == H.game_hash(g2),
        H.game_hash(g1))

div_at = None
for t in range(1, 601):
    step_match(g1, bots1)
    step_match(g2, bots2)
    if t % 25 == 0 and H.game_hash(g1) != H.game_hash(g2):
        div_at = t
        break
r.check("Game: 600-tick post-restore lockstep (hash every 25 ticks)",
        div_at is None,
        "no divergence" if div_at is None else f"diverged at tick {div_at}")

r.finish()
