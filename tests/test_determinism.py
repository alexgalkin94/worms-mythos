"""Lockstep determinism: identical seeds + identical inputs must produce
bit-identical simulation state on every run.

Part A — raw World: two Worlds with the same seed receive the same scripted
chaotic painting (10 materials, script fixed by random.Random(1)) and run
1500 ticks; the full state hash (mat/shade/life/burn/rest/head/temp — the
same planes World.to_bytes snapshots) is compared every 25 ticks.

Part B — full bot match through Game + Bot: bots only consume game.rng and
game state, so two matches from the same settings must stay in lockstep.
The world hash here is the exact notion net play uses for desync detection
(sha256 of world.mat, see app.py / net.py), extended with grub state.
"""
import sys
import os
import random
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _harness as H

H.init_pygame()

from grubstorm.world import World
from grubstorm.game import Game
from grubstorm.ai import Bot
from grubstorm import materials as M

r = H.Runner("test_determinism")

# ------------------------------------------------------- part A: raw World
N_TICKS = 1500
HASH_EVERY = 25
WORLD_SEED = 42

CHAOS_MATS = [M.WATER, M.SAND, M.LAVA, M.OIL, M.ACID,
              M.GAS, M.NITRO, M.EXPOWDER, M.SNOW, M.SLIME]


def make_script():
    """tick -> [(x, y, radius, mat, mode), ...], fixed by random.Random(1).
    Painting stops at tick 900 so the tail of the run also covers
    settling/sleeping behaviour."""
    rng = random.Random(1)
    script = {}
    for t in range(0, 900, 4):
        ops = []
        for _ in range(rng.randint(1, 2)):
            ops.append((rng.randint(80, 300), rng.randint(30, 180),
                        rng.randint(3, 8), rng.choice(CHAOS_MATS),
                        "fill" if rng.random() < 0.7 else "replace"))
        script[t] = ops
    return script


def run_world(script):
    w = World(WORLD_SEED)
    hashes = []
    for t in range(1, N_TICKS + 1):
        for (x, y, rad, mat, mode) in script.get(t, ()):
            w.paint(x, y, rad, mat, mode=mode)
        w.step()
        w.events.clear()           # fx events are render-side, drop them
        if t % HASH_EVERY == 0:
            hashes.append(H.world_hash(w))
    return hashes


script = make_script()
t0 = time.perf_counter()
h1 = run_world(script)
t_run1 = time.perf_counter() - t0
h2 = run_world(script)
r.info(f"world run: {N_TICKS} ticks, {t_run1:.1f}s "
       f"({t_run1 / N_TICKS * 1000:.1f} ms/tick)")

div = H.first_divergence(h1, h2)
r.check("World chaos: 1500-tick state hashes identical (every 25 ticks)",
        div is None,
        "no divergence" if div is None else
        f"first divergence at tick {(div + 1) * HASH_EVERY}")

# -------------------------------------------------- part B: full bot match
BOT_TICKS = 1800
BOT_SETTINGS = {
    "seed": 1337, "biome": "island", "turn_seconds": 10,
    "teams": [
        {"name": "A", "color_idx": 0, "n_grubs": 2, "control": "bot:normal"},
        {"name": "B", "color_idx": 1, "n_grubs": 2, "control": "bot:tactical"},
    ],
}


def run_match():
    g = Game(dict(BOT_SETTINGS))
    bots = {0: Bot("normal"), 1: Bot("tactical")}
    hashes = []
    for t in range(1, BOT_TICKS + 1):
        if g.phase == Game.PH_OVER:
            g.step(None)
        else:
            g.step(bots[g.turn_team].act(g))
        g.fx.clear()               # render-side fx, consumed by Renderer
        if t % HASH_EVERY == 0:
            hashes.append(H.game_hash(g))
    return hashes


t0 = time.perf_counter()
m1 = run_match()
t_run1 = time.perf_counter() - t0
m2 = run_match()
r.info(f"bot match run: {BOT_TICKS} ticks, {t_run1:.1f}s "
       f"({t_run1 / BOT_TICKS * 1000:.2f} ms/tick incl. mapgen)")

div = H.first_divergence(m1, m2)
r.check("Bot match: 1800-tick world+grub hashes identical (every 25 ticks)",
        div is None,
        "no divergence" if div is None else
        f"first divergence at tick {(div + 1) * HASH_EVERY}")

r.finish()
