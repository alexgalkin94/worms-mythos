"""Behavioural guarantees the fluid sim has regressed on in the past.

(a) Communicating vessels: a sealed U-shaped stone container with one
    column filled high must equalize — both surfaces within 1 row —
    within 3000 ticks, with the total water cell count conserved exactly
    (pressure flow/teleport are swaps; nothing may create or destroy).

(b) A pour into an open basin must end fully flat (no tilted frozen
    surface — the levelling/terrace-creep rules at work) and the world
    must actually go to sleep (activity == 0 for 50 consecutive ticks,
    then wake box + cooldown fully expired).

(c) A sleeping world is free: its state stays byte-identical across 100
    further ticks (no RNG draws, no jitter, no creep).
"""
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _harness as H

import numpy as np

from grubstorm.world import World
from grubstorm import materials as M

r = H.Runner("test_sim_behavior")

# ------------------------------------------------ (a) communicating vessels
w = World(7)
S = M.STONE
w.mat[120:230, 100:104] = S        # left wall
w.mat[120:230, 156:160] = S        # right wall
w.mat[226:230, 100:160] = S        # bottom
w.mat[120:217, 128:132] = S        # divider; duct open at y 217..225
w.mat[130:216, 104:128] = M.WATER  # left column filled HIGH
n_water0 = int((w.mat == M.WATER).sum())
w._wake_box = [0, w.h, 0, w.w]     # direct mat writes don't wake by design


def column_top(world, x0, x1):
    sub = world.mat[:, x0:x1] == M.WATER
    rows = np.nonzero(sub.any(axis=1))[0]
    return int(rows[0]) if len(rows) else None


t0 = time.perf_counter()
equalized_at = None
conserved = True
for t in range(1, 3001):
    w.step()
    w.events.clear()
    if t % 25 == 0:
        if int((w.mat == M.WATER).sum()) != n_water0:
            conserved = False
            break
        lt, rt = column_top(w, 104, 128), column_top(w, 132, 156)
        if (equalized_at is None and lt is not None and rt is not None
                and abs(lt - rt) <= 1):
            equalized_at = t
lt, rt = column_top(w, 104, 128), column_top(w, 132, 156)
r.check("U-tube: both surfaces within 1 row inside 3000 ticks",
        equalized_at is not None and lt is not None and rt is not None
        and abs(lt - rt) <= 1,
        f"equalized ~tick {equalized_at}, final tops L{lt}/R{rt}, "
        f"{time.perf_counter() - t0:.1f}s")
r.check("U-tube: water cell count conserved exactly (+-0)",
        conserved and int((w.mat == M.WATER).sum()) == n_water0,
        f"{n_water0} cells")

# --------------------------------------- (b) open basin: flat + asleep
w2 = World(11)
w2.mat[240:246, 1:479] = S         # basin floor
w2._wake_box = [0, w2.h, 0, w2.w]

t0 = time.perf_counter()
asleep_at = None
quiet_run = 0
slept_50 = None
for t in range(1, 8001):
    if t <= 240 and t % 2 == 0:    # lopsided pour near the left side
        w2.paint(80 + (t % 40), 60, 6, M.WATER, mode="fill")
    w2.step()
    w2.events.clear()
    quiet_run = quiet_run + 1 if w2.activity == 0 else 0
    if asleep_at is None and w2._wake_box is None and w2._wake_cool == 0:
        asleep_at = t              # fully parked: no region, no cooldown
    if slept_50 is None and quiet_run >= 50:
        slept_50 = t
    if asleep_at is not None and slept_50 is not None:
        break                      # parked AND 50 consecutive quiet ticks

band = w2.mat[:240, 1:479] == M.WATER
cols = np.nonzero(band.any(axis=0))[0]
tops = [int(np.nonzero(band[:, c])[0][0]) for c in cols]
r.check("Basin: world sleeps (activity == 0 for 50 consecutive ticks)",
        slept_50 is not None and asleep_at is not None,
        f"50 quiet ticks by {slept_50}, fully parked at {asleep_at}, "
        f"{time.perf_counter() - t0:.1f}s")
r.check("Basin: surface fully flat (top rows within 1 across all columns)",
        len(cols) > 0 and max(tops) - min(tops) <= 1,
        f"{len(cols)} water columns, tops {min(tops)}..{max(tops)}"
        if len(cols) else "no water found")

# ------------------------------ (c) sleeping world is byte-stable
baseline = H.world_hash(w2)
stable = True
for t in range(1, 101):
    w2.step()
    if H.world_hash(w2) != baseline:
        stable = False
        break
r.check("Sleep: settled world byte-identical across 100 ticks",
        stable, "" if stable else f"changed at sleeping tick {t}")

r.finish()
