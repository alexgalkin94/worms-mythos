"""The long battery: end-to-end robustness under generous wall-time caps.

(a) A full bot match (with sudden death armed) must run to completion —
    no exceptions, a winner or draw declared — and we report ticks and
    ms/tick so perf regressions show up in CI logs.

(b) Every biome in mapgen.BIOMES must generate + pre-settle without
    exceptions and within a per-biome time budget; per-biome startup
    seconds are reported. Budgets are calibrated ~3x above the slowest
    biome observed on a heavily loaded CI VM.
"""
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _harness as H

H.init_pygame()

from grubstorm.game import Game
from grubstorm.ai import Bot
from grubstorm.mapgen import generate, BIOMES

r = H.Runner("test_battery")

# ----------------------------------------------- (a) bot match completion
MATCH_WALL_CAP = 300.0             # seconds (observed ~9s; 25%-noise VM)
MATCH_TICK_CAP = 60 * 60 * 12      # 12 minutes of game time
SETTINGS = {
    "seed": 777, "biome": "island", "turn_seconds": 8,
    "sudden_death_at": 90, "sd_mode": "both",
    "teams": [
        {"name": "A", "color_idx": 0, "n_grubs": 2, "control": "bot:tactical"},
        {"name": "B", "color_idx": 1, "n_grubs": 2, "control": "bot:normal"},
    ],
}

t0 = time.perf_counter()
err = None
ticks = 0
game = None
try:
    game = Game(dict(SETTINGS))
    bots = {0: Bot("tactical"), 1: Bot("normal")}
    build_s = time.perf_counter() - t0
    t0 = time.perf_counter()
    while game.phase != Game.PH_OVER and ticks < MATCH_TICK_CAP:
        game.step(bots[game.turn_team].act(game))
        game.fx.clear()
        ticks += 1
        if time.perf_counter() - t0 > MATCH_WALL_CAP:
            break
except Exception as e:             # noqa: BLE001 — the test IS the catch
    import traceback
    traceback.print_exc()
    err = e

dt = time.perf_counter() - t0
r.check("Bot match: no exceptions", err is None, repr(err) if err else "")
if game is not None and err is None:
    done = game.phase == Game.PH_OVER
    winner = ("draw" if game.winner is None else
              game.teams[game.winner].name) if done else "n/a"
    r.check("Bot match: ran to completion within caps", done,
            f"{ticks} ticks in {dt:.1f}s "
            f"({dt / max(1, ticks) * 1000:.2f} ms/tick), "
            f"build {build_s:.1f}s, winner: {winner}")
    r.info(f"turns played: {game.turn_no}, "
           f"sudden death: {game.sudden_death}")

# ------------------------------------------------- (b) mapgen, every biome
BIOME_BUDGET_S = 60.0              # worst observed ~21s on a loaded VM

r.check("Mapgen: BIOMES list is non-empty", len(BIOMES) > 0,
        f"{len(BIOMES)} biomes")
for biome in BIOMES:
    t0 = time.perf_counter()
    err = None
    spec = None
    try:
        spec = generate(biome, 31337)
    except Exception as e:         # noqa: BLE001
        import traceback
        traceback.print_exc()
        err = e
    dt = time.perf_counter() - t0
    ok = (err is None and spec is not None and dt <= BIOME_BUDGET_S
          and len(spec.spawns) >= 1)
    detail = (f"{dt:5.2f}s, {len(spec.spawns)} spawns, "
              f"{len(spec.bodies)} props" if err is None else repr(err))
    r.check(f"Mapgen: {biome:10s} generates + pre-settles", ok, detail)

r.finish()
