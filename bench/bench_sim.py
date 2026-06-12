"""Simulation tick benchmarks: fixed-seed scenarios, per-tick timings.

    python bench/bench_sim.py            # full run (600+ ticks/scenario)
    python bench/bench_sim.py --quick    # 200 ticks/scenario

Scenarios:
  lava_brush       a 10-radius lava brush dabbing in a basin
  water_brush      same brush shape, water (cheap fluid, fast levelling)
  chaos_10mat      the determinism-suite chaos script: 10 materials
  lava_spam_sweep  the worst-case complaint: TWO 25-radius lava discs
                   painted EVERY tick, sweeping across the whole map

All seeds are fixed; timings vary with the machine but cell-for-cell the
work is identical run to run. Reports median / p95 / max tick ms.
"""
import os
import random
import sys
import time

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from grubstorm.world import World
from grubstorm import materials as M

QUICK = "--quick" in sys.argv[1:]
N_TICKS = 200 if QUICK else 600

CHAOS_MATS = [M.WATER, M.SAND, M.LAVA, M.OIL, M.ACID,
              M.GAS, M.NITRO, M.EXPOWDER, M.SNOW, M.SLIME]


def floor_world(seed):
    w = World(seed)
    w.mat[240:246, 1:479] = M.STONE
    w._wake_box = [0, w.h, 0, w.w]
    return w


# ----------------------------------------------------------- scenarios ----
def scn_lava_brush(t, w, state):
    if t <= N_TICKS * 0.7:
        w.paint(120 + (t * 5) % 240, 80, 10, M.LAVA, mode="fill")


def scn_water_brush(t, w, state):
    if t <= N_TICKS * 0.7:
        w.paint(120 + (t * 5) % 240, 80, 10, M.WATER, mode="fill")


def scn_chaos(t, w, state):
    rng = state["rng"]
    if t % 3 == 0 and t <= N_TICKS * 0.8:
        for _ in range(rng.randint(1, 2)):
            w.paint(rng.randint(60, 420), rng.randint(20, 200),
                    rng.randint(3, 9), rng.choice(CHAOS_MATS),
                    mode="fill" if rng.random() < 0.7 else "replace")


def scn_lava_spam(t, w, state):
    # two 25-radius lava discs per tick, sweeping the full map width
    x = 40 + (t * 3) % 400
    w.paint(x, 60, 25, M.LAVA, mode="fill")
    w.paint((x + 200) % 400 + 40, 110, 25, M.LAVA, mode="fill")


SCENARIOS = [
    ("lava_brush", scn_lava_brush),
    ("water_brush", scn_water_brush),
    ("chaos_10mat", scn_chaos),
    ("lava_spam_sweep", scn_lava_spam),
]


def pct(sorted_vals, p):
    return sorted_vals[min(len(sorted_vals) - 1,
                           int(p / 100.0 * len(sorted_vals)))]


def run_scenario(name, fn):
    w = floor_world(31337)
    state = {"rng": random.Random(1)}
    tick_ms = []
    t_all = time.perf_counter()
    for t in range(1, N_TICKS + 1):
        fn(t, w, state)
        t0 = time.perf_counter()
        w.step()
        tick_ms.append((time.perf_counter() - t0) * 1000.0)
        w.events.clear()
    total = time.perf_counter() - t_all
    s = sorted(tick_ms)
    return (name, len(tick_ms), pct(s, 50), pct(s, 95), s[-1], total,
            w.activity)


def main():
    mode = "QUICK" if QUICK else "FULL"
    print(f"bench_sim [{mode}]  grid 480x270, {N_TICKS} ticks/scenario, "
          f"fixed seeds")
    hdr = (f"{'scenario':<17s}{'ticks':>6s}{'med ms':>9s}{'p95 ms':>9s}"
           f"{'max ms':>9s}{'total s':>9s}{'end act':>9s}")
    print(hdr)
    print("-" * len(hdr))
    for name, fn in SCENARIOS:
        name_, n, med, p95, mx, total, act = run_scenario(name, fn)
        print(f"{name_:<17s}{n:>6d}{med:>9.2f}{p95:>9.2f}{mx:>9.2f}"
              f"{total:>9.2f}{act:>9d}", flush=True)
    print("done.")


if __name__ == "__main__":
    main()
