"""The living world: a fully simulated falling-sand grid.

Every pass is vectorized numpy. All randomness comes from a seeded
np.random.Generator so lockstep multiplayer stays deterministic.
"""
import zlib
import numpy as np

from . import materials as M
from .constants import GRID_W, GRID_H, SIM_SUBSTEPS


def nz2(mask):
    """(ys, xs) of a 2-D bool mask. flatnonzero + divmod takes numpy's
    1-D fast path and is ~7x quicker than 2-D np.nonzero; the order
    (row-major) and values are identical."""
    idx = np.flatnonzero(mask)
    return np.divmod(idx, mask.shape[1])


def shift(a, dy, dx, fill):
    """out[y, x] = a[y + dy, x + dx], edges filled with `fill`."""
    out = np.full_like(a, fill)
    h, w = a.shape[0], a.shape[1]
    sy = slice(dy, None) if dy >= 0 else slice(None, dy)
    oy = slice(None, -dy if dy > 0 else None) if dy >= 0 else slice(-dy, None)
    sx = slice(dx, None) if dx >= 0 else slice(None, dx)
    ox = slice(None, -dx if dx > 0 else None) if dx >= 0 else slice(-dx, None)
    out[oy, ox] = a[sy, sx]
    return out


class World:
    def __init__(self, seed: int, w: int = GRID_W, h: int = GRID_H):
        self.w, self.h = w, h
        self.rng = np.random.default_rng(seed)
        self.mat = np.zeros((h, w), np.uint8)
        # Noita-style material grain: heavy per-pixel speckle, clustered by
        # larger patches, with faint horizontal strata running through it
        coarse = np.kron(self.rng.random((h // 6 + 2, w // 6 + 2)),
                         np.ones((6, 6)))[:h, :w]
        fine = self.rng.random((h, w))
        rows = np.kron(self.rng.random(h // 3 + 1), np.ones(3))[:h]
        strata = (np.sin(np.arange(h) * 0.45 + rows * 6.0) * 0.5 + 0.5)
        mix = coarse * 0.35 + fine * 0.5 + strata[:, None] * 0.15
        self.tex = np.clip(mix * 4.6 - 0.3, 0, 3.999).astype(np.uint8)
        self.shade = self.tex.copy()
        self.life = np.zeros((h, w), np.uint8)    # gas life / burn fuel
        self.burn = np.zeros((h, w), np.uint8)    # burning flag for fuels
        self.rest = np.zeros((h, w), np.uint8)    # liquid settle counter
        self.head = np.full((h, w), 0xFFFFFFFF, np.uint32)  # (y<<9|x) of
        # the highest connected surface: pressure head AND component id
        self.temp = np.full((h, w), 20.0, np.float32)
        self.moved = np.zeros((h, w), np.uint8)
        self.tick = 0
        self.wind = 0.0
        self.ambient = 20.0
        self.gravity_dir = 1                       # -1 in gravity-invert chaos
        self.activity = 0                          # moved cells last tick
        self.level_until = 0     # ticks: terrace creep allowed while open
        self.level_box = None    # where the last real gravity work happened
        self.settle_mode = False  # mapgen pre-settle: mechanics only
        self.pending_detonations: list[tuple[int, int, int]] = []  # x, y, mat
        self.events: list[dict] = []               # explosions etc, for fx/sfx
        # bedrock border so nothing escapes the toybox (bottom stays open
        # to the "ocean" in classic maps; mapgen decides — default sealed sides)
        self.mat[:, 0] = M.BEDROCK
        self.mat[:, -1] = M.BEDROCK
        self.mat[0, :] = 0                         # open sky
        self.water_level = h + 10                  # rows >= level are "ocean"
        self.phase = M.PHASE[self.mat]
        self.dens = M.DENSITY[self.mat]
        self._wake_box: list[int] | None = [0, h, 0, w]
        self._wake_cool = 0
        self._cool_box: list[int] | None = None
        self.render_dirty: list[int] | None = [0, h, 0, w]
        self.last_region = None
        self._ry0 = self._rx0 = 0

    # ------------------------------------------------------------- helpers
    def in_bounds(self, x, y):
        return 0 <= x < self.w and 0 <= y < self.h

    def get(self, x, y):
        if not self.in_bounds(int(x), int(y)):
            return M.BEDROCK
        return int(self.mat[int(y), int(x)])

    def is_solid(self, x, y):
        return bool(M.SOLID[self.get(x, y)])

    def is_liquid(self, x, y):
        return bool(M.LIQUID[self.get(x, y)])

    def set_cell(self, x, y, mat, life=0):
        if self.in_bounds(int(x), int(y)):
            xi, yi = int(x), int(y)
            self.mat[yi, xi] = mat
            self.life[yi, xi] = life
            self.burn[yi, xi] = 0
            y0, y1 = max(0, yi - 2), min(self.h, yi + 3)
            x0, x1 = max(0, xi - 2), min(self.w, xi + 3)
            self.rest[y0:y1, x0:x1] = 0   # edits disturb settled fluids
            self.wake(xi - 1, yi - 1, xi + 1, yi + 1)

    def _disk(self, x, y, r):
        """Return (slice_y, slice_x, mask) for a disk clipped to the grid."""
        x, y, r = int(x), int(y), int(np.ceil(r))
        x0, x1 = max(0, x - r), min(self.w, x + r + 1)
        y0, y1 = max(0, y - r), min(self.h, y + r + 1)
        if x0 >= x1 or y0 >= y1:
            return None
        yy, xx = np.mgrid[y0:y1, x0:x1]
        d2 = (yy - y) ** 2 + (xx - x) ** 2
        return slice(y0, y1), slice(x0, x1), d2

    def paint(self, x, y, r, mat, mode="fill", life=0, noise=0.0):
        """Fill a disk. mode: 'fill' only into empty/gas, 'replace' anything
        but bedrock, 'erase' clears."""
        d = self._disk(x, y, r)
        if d is None:
            return
        sy, sx, d2 = d
        mask = d2 <= r * r
        if noise > 0:
            mask &= self.rng.random(mask.shape) > noise * (np.sqrt(d2) / max(r, 1))
        sub = self.mat[sy, sx]
        if life == 0 and mat in (M.SMOKE, M.STEAM, M.TOXGAS, M.GAS):
            life = 200                # free gas always dissipates eventually
        if mode == "fill":
            mask &= (M.PHASE[sub] == M.P_EMPTY) | (M.PHASE[sub] == M.P_GAS)
        elif mode == "replace":
            mask &= sub != M.BEDROCK
        if mode == "erase":
            mask &= sub != M.BEDROCK
            sub[mask] = M.EMPTY
        else:
            sub[mask] = mat
        self.life[sy, sx][mask] = life
        self.burn[sy, sx][mask] = 0
        self.shade[sy, sx][mask] = self.tex[sy, sx][mask]
        self.rest[sy, sx] = 0         # let nearby liquids react to the edit
        self.wake(sx.start - 1, sy.start - 1, sx.stop + 1, sy.stop + 1)

    def raycast(self, x, y, dx, dy, max_len, hit_liquid=False):
        """March a ray; return (hx, hy, mat) of first solid cell or None."""
        length = max(1e-6, (dx * dx + dy * dy) ** 0.5)
        dx, dy = dx / length, dy / length
        for i in range(int(max_len)):
            x += dx
            y += dy
            m = self.get(x, y)
            if M.SOLID[m] or (hit_liquid and M.LIQUID[m]):
                return int(x), int(y), m
            if m == M.BEDROCK:
                return int(x), int(y), m
        return None

    # ------------------------------------------------------------ dynamics
    # Movement passes run only inside the "active region" — a bounding box
    # around everything that moved or reacted recently. Settled worlds cost
    # almost nothing. self.v_* are views into that region.
    def wake(self, x0, y0, x1, y1):
        """Mark a rectangle (inclusive coords) as active next step."""
        x0 = max(0, int(x0)); y0 = max(0, int(y0))
        x1 = min(self.w, int(x1) + 1); y1 = min(self.h, int(y1) + 1)
        if x0 >= x1 or y0 >= y1:
            return
        b = self._wake_box
        if b is None:
            self._wake_box = [y0, y1, x0, x1]
        else:
            b[0] = min(b[0], y0); b[1] = max(b[1], y1)
            b[2] = min(b[2], x0); b[3] = max(b[3], x1)
        d = self.render_dirty
        if d is None:
            self.render_dirty = [y0, y1, x0, x1]
        else:
            d[0] = min(d[0], y0); d[1] = max(d[1], y1)
            d[2] = min(d[2], x0); d[3] = max(d[3], x1)

    def _wake_mask(self, mask, oy=0, ox=0):
        rows = mask.any(1)
        if not rows.any():
            return
        cols = mask.any(0)
        y0 = int(rows.argmax()); y1 = len(rows) - int(rows[::-1].argmax())
        x0 = int(cols.argmax()); x1 = len(cols) - int(cols[::-1].argmax())
        self.wake(ox + x0, oy + y0, ox + x1 - 1, oy + y1 - 1)

    def _apply_moves(self, mask, dy, dx, reset_rest=True, keep_awake=True):
        # clip to the mask's bounding rows first: the full-region nonzero
        # scan dominated profiles when a big region had one small active
        # spot (and most rule masks are empty or tiny)
        rows = mask.any(1)
        if not rows.any():
            return 0
        r0 = int(rows.argmax())
        r1 = len(rows) - int(rows[::-1].argmax())
        # flatnonzero + divmod is ~7x faster than 2-D nonzero (one output
        # pass, no per-element coordinate writes), same indices
        idx = np.flatnonzero(mask[r0:r1])
        n = len(idx)
        ys, xs = np.divmod(idx, mask.shape[1])
        ys += r0
        # swap through FLAT indices into the contiguous base planes: the
        # source/target index arithmetic happens once instead of inside
        # every per-plane 2-D fancy index, and 1-D gathers take numpy's
        # fast path. Same cells, same values — bit-identical.
        wf = self.mat.shape[1]
        fs = (ys + self._ry0) * wf + (xs + self._rx0)
        fd = fs + (dy * wf + dx)
        for a in (self.mat, self.shade, self.life, self.burn,
                  self.temp, self.phase, self.dens, self.rest):
            ar = a.reshape(-1)
            tmp = ar[fd]                 # fancy gather already copies
            ar[fd] = ar[fs]
            ar[fs] = tmp
        mr = self.moved.reshape(-1)
        mr[fs] = 1
        mr[fd] = 1
        if reset_rest:
            # gravity-driven motion is "real" flow and wakes the cell;
            # flat lateral wandering carries its rest along and ages out
            rr = self.rest.reshape(-1)
            rr[fs] = 0
            rr[fd] = 0
        oy, ox = self._ry0, self._rx0
        x0 = ox + int(xs.min()) - 1; y0 = oy + int(ys.min()) - 1
        x1 = ox + int(xs.max()) + 1; y1 = oy + int(ys.max()) + 1
        if keep_awake:
            self.wake(x0, y0, x1, y1)
        else:
            # cosmetic-grade motion (ripple shuffling): repaint it, but it
            # must not keep the active region alive on its own — it rides
            # along while other rules do real work, then stops with them
            d = self.render_dirty
            y1 += 1; x1 += 1
            if d is None:
                self.render_dirty = [max(0, y0), min(self.h, y1),
                                     max(0, x0), min(self.w, x1)]
            else:
                d[0] = min(d[0], max(0, y0)); d[1] = max(d[1], min(self.h, y1))
                d[2] = min(d[2], max(0, x0)); d[3] = max(d[3], min(self.w, x1))
        return n

    @staticmethod
    def _bshift(a, dy, dx):
        """Boolean/int shift with False fill: out[y,x] = a[y+dy, x+dx]."""
        out = np.zeros_like(a)
        sy = slice(dy, None) if dy >= 0 else slice(None, dy)
        oy = slice(None, -dy if dy > 0 else None) if dy >= 0 else slice(-dy, None)
        sx = slice(dx, None) if dx >= 0 else slice(None, dx)
        ox = slice(None, -dx if dx > 0 else None) if dx >= 0 else slice(-dx, None)
        out[oy, ox] = a[sy, sx]
        return out

    def _powder_pass(self, parity, lateral=True):
        g = self.gravity_dir
        ph, dens, rnd = self.v_phase, self.v_dens, self.v_rnd
        powder = ph == M.P_POWDER
        if not powder.any():
            return 0
        free = ph <= M.P_GAS
        below_free = self._bshift(free, g, 0)
        below_liq = self._bshift(ph, g, 0) == M.P_LIQUID
        below_dens = self._bshift(dens, g, 0)
        sink = below_liq & (dens > below_dens) & (rnd < 0.55)
        n = self._apply_moves(powder & (below_free | sink), g, 0)
        if not lateral:                       # falling runs every substep,
            return n                          # toppling once per tick
        # diagonal slide (uniform direction per call avoids target conflicts).
        # SLIDE sets how lively a powder topples; CLUMPY powders (snow,
        # ash) only topple over steep 2-cell drops, so their piles hold
        # steep slopes while sand relaxes into wide flat cones.
        d = 1 if parity else -1
        for dd in (d, -d):
            ph = self.v_phase
            powder = ph == M.P_POWDER
            free = ph <= M.P_GAS
            diag = self._bshift(free, g, dd) & self._bshift(free, 0, dd)
            steep = self._bshift(free, 2 * g, dd)
            ok = powder & diag & (steep | ~M.CLUMPY[self.v_mat]) \
                & (rnd < M.SLIDE[self.v_mat])
            n += self._apply_moves(ok, g, dd)
        return n

    def _mark_level_work(self, real_work):
        """Open/extend the levelling window box around real gravity work."""
        if not real_work.any():
            return
        ys = np.nonzero(real_work.any(axis=1))[0]
        xs = np.nonzero(real_work.any(axis=0))[0]
        b = [int(ys[0]) + self._ry0 - 16, int(ys[-1]) + self._ry0 + 16,
             int(xs[0]) + self._rx0 - 16, int(xs[-1]) + self._rx0 + 16]
        if self.tick < self.level_until and self.level_box:
            lb = self.level_box
            b = [min(b[0], lb[0]), max(b[1], lb[1]),
                 min(b[2], lb[2]), max(b[3], lb[3])]
        self.level_box = b
        self.level_until = self.tick + 120

    def _liquid_pass(self, parity, lateral=True):
        """Vertical work (falling, sinking, slumping) runs every substep;
        the expensive lateral machinery — hydrostatic flow, pours, flat
        slosh, surface levelling — only needs one pass per tick."""
        g = self.gravity_dir
        ph, dens, rnd = self.v_phase, self.v_dens, self.v_rnd
        liq = ph == M.P_LIQUID
        if not liq.any():
            return 0
        visc = M.VISCOSITY[self.v_mat]
        # stickiness: gels touching a static cell cling to it — they coat
        # walls and dribble down them slowly instead of running off.
        # Statics never move inside this pass, so one mask serves all of it.
        stickv = M.STICKY[self.v_mat]
        if stickv.any():
            stat = ph == M.P_STATIC
            near_wall = (self._bshift(stat, 0, 1) | self._bshift(stat, 0, -1) |
                         self._bshift(stat, g, 0) | self._bshift(stat, -g, 0))
            cling = near_wall & (rnd < stickv)
            dribble = near_wall & (rnd < stickv * 0.55)
        else:
            cling = dribble = np.zeros_like(liq)
        n = 0
        real_work = np.zeros_like(liq)
        d = 1 if parity else -1
        if g == 1:
            # pressure service BEFORE gravity: a hole inside a pressurized
            # zone must be fed sideways first, or the column above always
            # slumps back into it and hydrostatic rises pump in place
            # forever. Uses last tick's head plane — one substep stale is
            # fine, the solver below refreshes it.
            yyv = (np.arange(liq.shape[0], dtype=np.int32)
                   + self._ry0)[:, None]
            deepv = liq & ((self.v_head >> 9).astype(np.int32) <= yyv - 2)
            if deepv.any():
                for dd in (d, -d):
                    ph = self.v_phase
                    liq2 = (ph == M.P_LIQUID) & (self.v_moved == 0)
                    fl0 = liq2 & deepv & ~cling & \
                        self._bshift(ph <= M.P_GAS, 0, dd) & (rnd > visc)
                    real_work |= fl0
                    n += self._apply_moves(fl0, 0, dd)
        ph = self.v_phase
        liq = ph == M.P_LIQUID
        free = ph <= M.P_GAS
        below_free = self._bshift(free, g, 0)
        below_liq = self._bshift(ph, g, 0) == M.P_LIQUID
        below_dens = self._bshift(dens, g, 0)
        sink = below_liq & (dens > below_dens) & (rnd < 0.4)
        fall = liq & (self.v_moved == 0) & below_free & ~dribble
        n += self._apply_moves(fall, g, 0)
        real_work |= fall               # gravity-driven moves, for levelling
        # density layering swaps are NOT "real work" for the levelling
        # window below — oil shuffling on the ocean would hold it open
        # forever while creep re-mixes what sinking un-mixes
        n_sink = self._apply_moves(liq & sink & ~below_free & ~dribble, g, 0)
        d = 1 if parity else -1
        # diagonal slump runs every substep; everything below is lateral
        for dd in (d, -d):
            ph = self.v_phase
            liq = ph == M.P_LIQUID
            free = ph <= M.P_GAS
            ok = liq & self._bshift(free, g, dd) & ~cling & \
                (rnd > visc * 0.5)
            real_work |= ok
            n += self._apply_moves(ok, g, dd)
        if not lateral:
            if n:
                self._mark_level_work(real_work)
            return n + n_sink
        # ---- communicating vessels: the hydrostatic HEAD plane ----------
        # head[cell] = y of the highest water surface connected to this
        # cell through liquid, min-propagated 6 steps per tick on a
        # persistent plane (rebuilt every 64 ticks so stale pressure dies
        # when its source column drains). Two rules act on it:
        #  - FLOW: any cell whose connected head stands >= 2 rows higher
        #    squeezes through an open side — tanks drain through tunnels,
        #    plugs inside pipes keep getting pushed.
        #  - RISE: a surface cell with head >= 3 rows higher climbs — the
        #    water comes back UP on the far side of the gap. The inflow
        #    that the same pressure drives refills the bubble it leaves.
        # At equilibrium (differences < 2) neither rule has candidates,
        # so settled pools still go fully to sleep.
        if g == 1:
            # the head solver runs on a 1-cell-extended window so values
            # PERSISTED outside the active region leak back in as boundary
            # conditions. A sleeping reservoir doesn't move, so its stored
            # head is still valid — without this, the pressure source
            # vanishes the moment the wake box shrinks away from it.
            ry0, ry1, rx0, rx1 = self.last_region
            ey0, ey1 = max(0, ry0 - 1), min(self.h, ry1 + 1)
            ex0, ex1 = max(0, rx0 - 1), min(self.w, rx1 + 1)
            # while work is in flight, the id solver runs on the SPAN of
            # that work (level box), not just the wake region: after a
            # surface migrates and its old id is invalidated, the fresh
            # id must re-flood the whole connecting duct — which usually
            # lies outside the small wake box around the teleport ends.
            if self.tick < self.level_until and self.level_box:
                lb = self.level_box
                ey0 = min(ey0, max(0, lb[0]))
                ey1 = max(ey1, min(self.h, lb[1] + 24))
                ex0 = min(ex0, max(0, lb[2]))
                ex1 = max(ex1, min(self.w, lb[3]))
            hde = self.head[ey0:ey1, ex0:ex1]
            BIG = np.uint32(0xFFFFFFFF)
            if self.tick % 64 == 0:
                # VALIDITY rebuild instead of a wipe: an id names its
                # source surface cell ((y<<9)|x) — kill only ids whose
                # source is no longer a liquid surface (stale pressure
                # ghosts). A wipe used to sever valid long-distance lines
                # whenever the connecting duct lay outside the window,
                # splitting one body into two 'individually level' halves
                # that then slept with a 40-cell difference standing.
                valid = hde != BIG
                sy2 = np.clip((hde >> np.uint32(9)).astype(np.intp),
                              0, self.h - 1)
                sx2 = np.clip((hde & np.uint32(511)).astype(np.intp),
                              0, self.w - 1)
                liq_g = M.PHASE[self.mat] == M.P_LIQUID
                airup = np.vstack([np.ones((1, self.w), bool),
                                   M.PHASE[self.mat[:-1]] <= M.P_GAS])
                surf_g = liq_g & airup
                hde[~(surf_g[sy2, sx2] & valid)] = BIG
            phe = M.PHASE[self.mat[ey0:ey1, ex0:ex1]]
            lqe = phe == M.P_LIQUID
            fre = phe <= M.P_GAS
            sfe = lqe & shift(fre, -1, 0, ey0 == 0)
            ids = ((np.arange(ey0, ey1, dtype=np.uint32) << 9)[:, None]
                   + np.arange(ex0, ex1, dtype=np.uint32)[None, :])
            np.minimum(hde, np.where(sfe, ids, BIG), out=hde)
            blocked = ~lqe
            # barrier plane: OR with all-ones forces BIG on blocked cells,
            # OR with 0 is identity — one pure vector op instead of a
            # boolean fancy-assignment (which was ~6x slower per sweep)
            barrier = np.where(blocked, np.uint32(BIG), np.uint32(0))
            np.bitwise_or(hde, barrier, out=hde)
            # in-place slice minimums (no shift allocations); the barrier
            # reset after EVERY direction is load-bearing — without it the
            # head value leaks one cell per sweep through solid walls and
            # water starts pressurizing across thin rock
            # full solver power only while equalization work is in
            # flight (the level window is open) — idle regions just keep
            # the lines warm at a fraction of the cost
            hot = self.tick < self.level_until
            if hot or self.tick % 2 == 0:
                for _ in range(5 if hot else 2):
                    np.minimum(hde[1:], hde[:-1], out=hde[1:])
                    np.bitwise_or(hde, barrier, out=hde)
                    np.minimum(hde[:-1], hde[1:], out=hde[:-1])
                    np.bitwise_or(hde, barrier, out=hde)
                    np.minimum(hde[:, 1:], hde[:, :-1], out=hde[:, 1:])
                    np.bitwise_or(hde, barrier, out=hde)
                    np.minimum(hde[:, :-1], hde[:, 1:], out=hde[:, :-1])
                    np.bitwise_or(hde, barrier, out=hde)
            # crop the extended window back to the region for the rules
            oy, ox = ry0 - ey0, rx0 - ex0
            hd = hde[oy:oy + (ry1 - ry0), ox:ox + (rx1 - rx0)]
            surfm = sfe[oy:oy + (ry1 - ry0), ox:ox + (rx1 - rx0)]
            yy = (np.arange(ry0, ry1, dtype=np.int32))[:, None]
            deep = (hd >> 9).astype(np.int32) <= yy - 2
            # two flow rounds per tick: pipe transport works by bubbles
            # walking backwards through the duct one cell per round, so
            # this directly doubles tunnel throughput
            for _round in range(2):
                round_n = 0
                for dd in (d, -d):
                    ph = self.v_phase
                    liq2 = (ph == M.P_LIQUID) & (self.v_moved == 0)
                    flow = liq2 & deep & ~cling & \
                        self._bshift(ph <= M.P_GAS, 0, dd) & (rnd > visc)
                    real_work |= flow
                    round_n += self._apply_moves(flow, 0, dd)
                n += round_n
                if not round_n:          # quiet round: round 2 won't differ
                    break
            # ---- pressure teleport (the Dwarf Fortress trick) ----------
            # Fluid under pressure doesn't crawl cell by cell: the top
            # cell of a body's HIGH surface jumps straight to the lowest
            # open seat on the same body's LOW surface. The id plane
            # guarantees both ends belong to one connected body (never
            # through walls), swaps conserve volume exactly, and a jump
            # only happens for a drop >= 2 — so equal pools are silent.
            # Deterministic: sorted matching, no probability gates.
            ph = self.v_phase
            liq2 = (ph == M.P_LIQUID) & (self.v_moved == 0)
            cand = liq2 & surfm & ~cling & \
                (hd != np.uint32(0xFFFFFFFF)) & (rnd > visc)
            if cand.any():
                cy, cx = nz2(cand)
                cid = hd[cy, cx]
                order = np.lexsort((cx, cy, cid))
                cy, cx, cid = cy[order], cx[order], cid[order]
                starts = np.nonzero(np.r_[True, cid[1:] != cid[:-1]])[0]
                ends = np.r_[starts[1:], len(cid)]
                src_y = []; src_x = []; dst_y = []; dst_x = []
                for s, e in zip(starts, ends):
                    k = 0
                    lo, hi = s, e - 1
                    while lo < hi and k < 16:
                        if cy[hi] - cy[lo] < 2:
                            break
                        src_y.append(cy[lo]); src_x.append(cx[lo])
                        dst_y.append(cy[hi] - 1); dst_x.append(cx[hi])
                        lo += 1; hi -= 1; k += 1
                if src_y:
                    sy_ = np.array(src_y); sx_ = np.array(src_x)
                    ty_ = np.array(dst_y); tx_ = np.array(dst_x)
                    for a in (self.v_mat, self.v_shade, self.v_life,
                              self.v_burn, self.v_temp, self.v_phase,
                              self.v_dens, self.v_rest):
                        tmp = a[ty_, tx_].copy()
                        a[ty_, tx_] = a[sy_, sx_]
                        a[sy_, sx_] = tmp
                    self.v_moved[sy_, sx_] = 1
                    self.v_moved[ty_, tx_] = 1
                    self.v_rest[sy_, sx_] = 0
                    self.v_rest[ty_, tx_] = 0
                    oy2, ox2 = self._ry0, self._rx0
                    ally = np.r_[sy_, ty_]; allx = np.r_[sx_, tx_]
                    self.wake(ox2 + int(allx.min()) - 1,
                              oy2 + int(ally.min()) - 1,
                              ox2 + int(allx.max()) + 1,
                              oy2 + int(ally.max()) + 1)
                    real_work[sy_, sx_] = True
                    real_work[ty_, tx_] = True
                    n += len(sy_)
        # lateral flow. Pouring over an edge is real flow (always allowed,
        # resets rest); sloshing on flat ground is gated by the rest counter
        # so big pools go to sleep instead of jittering forever.
        n_flat = 0
        for dd in (d, -d):
            ph = self.v_phase
            free = ph <= M.P_GAS
            liq = (ph == M.P_LIQUID) & (self.v_moved == 0) & ~cling
            grounded = liq & ~self._bshift(free, g, 0)
            side_free = self._bshift(free, 0, dd) & (rnd > visc) & \
                ((rnd < 0.5) if dd == d else (rnd >= 0.5))
            pour = self._bshift(free, g, dd)
            pouring = grounded & side_free & pour
            if pouring.any():
                # a pour hands its rest budget to the cells next in line
                # (uphill and above), so a draining mound keeps flowing
                # from the edge inward instead of aging out mid-cascade
                heir = self._bshift(pouring, 0, dd) | self._bshift(pouring, g, 0)
                self.v_rest[heir] = 0
            real_work |= pouring
            n += self._apply_moves(pouring, 0, dd)
            ph = self.v_phase
            liq = (ph == M.P_LIQUID) & (self.v_moved == 0) & ~cling
            grounded = liq & ~self._bshift(ph <= M.P_GAS, g, 0)
            flat = grounded & side_free & ~pour & \
                (self.v_rest < self.REST_K)
            n_flat += self._apply_moves(flat, 0, dd, reset_rest=False)
        # gravity did real work this tick (falls, slumps, pours — but NOT
        # flat sloshing or density layering): open the levelling window
        # WHERE it happened. While a spot stays inside the window box,
        # terrace creep below flattens stepped domes the pour rule can't
        # see; a far-away brook must not keep the whole ocean creeping,
        # and once the splash zone calms down nothing re-opens its box.
        # The box + deadline are plain sim state for lockstep snapshots.
        if n:
            self._mark_level_work(real_work)
        n += n_flat + n_sink
        # surface levelling: a surface cell slides one column downhill.
        # The always-on rule needs two monotonically descending columns
        # (so settled ripples can't trigger it); the boxed creep rule
        # also takes slope-1 terrace edges, eroding domes completely.
        creep_on = self.tick < self.level_until and self.level_box
        if creep_on:
            lb = self.level_box
            inbox = np.zeros_like(liq)
            y0 = max(0, lb[0] - self._ry0); y1 = max(0, lb[1] - self._ry0)
            x0 = max(0, lb[2] - self._rx0); x1 = max(0, lb[3] - self._rx0)
            inbox[y0:y1, x0:x1] = True
        for dd in (d, -d):
            ph = self.v_phase
            free = ph <= M.P_GAS
            # supported on liquid OR solid, both under the cell and at the
            # destination: surface steps must be able to march across stone
            # blobs and ledges, or scattered rocks pin a tilted surface in
            # place forever (each one parking a step it can't cross)
            surf = (ph == M.P_LIQUID) & (self.v_moved == 0) & ~cling & \
                ~self._bshift(free, g, 0) & \
                self._bshift(free, -g, 0) & \
                self._bshift(free, 0, dd) & \
                ~self._bshift(free, g, dd) & \
                (rnd > visc) & ((rnd < 0.5) if dd == d else (rnd >= 0.5))
            slide = surf & self._bshift(free, g, 2 * dd)
            n += self._apply_moves(slide, 0, dd)
            if creep_on:
                # creep fills one-cell hollows too (that's how ripples
                # annihilate). It keeps the region awake only while the
                # levelling window is open — the window itself only stays
                # open while real gravity work happens, so a flat pool
                # still winds down and sleeps within a couple of seconds.
                creep = surf & inbox & ~slide & \
                    ~self._bshift(free, 0, -dd)
                n += self._apply_moves(creep, 0, dd, reset_rest=False)
        return n

    def _gas_pass(self, parity):
        ph, rnd = self.v_phase, self.v_rnd
        gas = ph == M.P_GAS
        if not gas.any():
            return 0
        g = -self.gravity_dir
        empty = ph == M.P_EMPTY
        # fire clings to flammable fuel directly beneath it; other fire
        # lingers a little before floating up
        is_fire = self.v_mat == M.FIRE
        if is_fire.any():
            fuel_below = self._bshift(M.FLAMMABLE[self.v_mat] > 0,
                                      self.gravity_dir, 0)
            stuck = is_fire & fuel_below
            gas = gas & ~stuck
        else:
            stuck = np.zeros_like(gas)
        rise_p = np.where(is_fire, np.float32(0.45), np.float32(0.8))
        n = self._apply_moves(gas & self._bshift(empty, g, 0) & (rnd < rise_p),
                              g, 0)
        d = 1 if parity else -1
        if abs(self.wind) > 0.01:           # wind drifts gases
            d = 1 if self.wind > 0 else -1
        # like liquids, a cloud pinned under a ceiling calms down and rests:
        # diagonal rising is buoyancy (resets rest), sideways drift is not
        for dd in (d, -d):
            ph = self.v_phase
            awake = self.v_rest < self.REST_K
            gas = (ph == M.P_GAS) & (self.v_moved == 0) & awake & ~stuck
            empty = ph == M.P_EMPTY
            ok = gas & self._bshift(empty, g, dd) & (rnd < 0.6)
            n += self._apply_moves(ok, g, dd)
            ph = self.v_phase
            gas = (ph == M.P_GAS) & (self.v_moved == 0) & awake & ~stuck
            empty = ph == M.P_EMPTY
            ok = gas & self._bshift(empty, 0, dd) & \
                 ((rnd < 0.45) if dd == d else (rnd > 0.55))
            n += self._apply_moves(ok, 0, dd, reset_rest=False)
        return n

    # ------------------------------------------------------------ reactions
    # All reaction passes run on the active-region views (v_*). Anything that
    # keeps reacting (fire, burning fuel, idle acid against terrain) wakes
    # itself each tick, so it can never fall out of the region while active.
    def _fire_pass(self):
        mat, rng = self.v_mat, self.rng
        life, burn = self.v_life, self.v_burn
        oy, ox = self._ry0, self._rx0
        # fire consumes itself. Random rolls are drawn per CANDIDATE, not
        # per region cell — a full-region float field x4 dominated this
        # pass in profiles while the candidate sets stay tiny.
        fire = mat == M.FIRE
        if fire.any():
            self._wake_mask(fire, oy, ox)
            life[fire] = np.maximum(life[fire].astype(np.int16) - 1, 0).astype(np.uint8)
            dead = fire & (life == 0)
            ys, xs = nz2(dead)
            if len(ys):
                r = rng.random(len(ys))
                smoke = r < 0.25
                mat[ys[smoke], xs[smoke]] = M.SMOKE
                life[ys[smoke], xs[smoke]] = \
                    rng.integers(40, 120, int(smoke.sum())).astype(np.uint8)
                mat[ys[~smoke], xs[~smoke]] = M.EMPTY

        # ignition: anything flammable next to heat
        hot = fire | (mat == M.LAVA) | (burn > 0)
        near_hot = (self._bshift(hot, 1, 0) | self._bshift(hot, -1, 0) |
                    self._bshift(hot, 0, 1) | self._bshift(hot, 0, -1))
        # heat agitates settled fluids so fuel keeps flowing toward flames
        self.v_rest[near_hot | hot] = 0
        cand = near_hot & (M.FLAMMABLE[mat] > 0) & (burn == 0)
        ignite = np.zeros_like(cand)
        cys, cxs = nz2(cand)
        if len(cys):
            roll = rng.random(len(cys))
            lit = roll < M.FLAMMABLE[mat[cys, cxs]]
            ignite[cys[lit], cxs[lit]] = True
        if ignite.any():
            self._wake_mask(ignite, oy, ox)
            det = ignite & ((mat == M.EXPOWDER) | (mat == M.NITRO))
            if det.any():
                ys, xs = nz2(det)
                # queue a handful per tick; the rest chain later
                for i in range(min(len(ys), 4)):
                    self.pending_detonations.append(
                        (ox + int(xs[i]), oy + int(ys[i]),
                         int(mat[ys[i], xs[i]])))
                    mat[ys[i], xs[i]] = M.FIRE
                    life[ys[i], xs[i]] = 30
            gasify = ignite & (mat == M.GAS)
            mat[gasify] = M.FIRE
            life[gasify] = rng.integers(20, 50)
            rest = ignite & (M.BURN_FUEL[mat] > 0)
            burn[rest] = 1
            life[rest] = M.BURN_FUEL[mat[rest]]

        # burning cells: emit fire above, lose fuel, die to residue
        burning = burn > 0
        if burning.any():
            self._wake_mask(burning, oy, ox)
            up = -self.gravity_dir
            above_empty = self._bshift(mat == M.EMPTY, up, 0)
            emit = burning & above_empty
            emit[0 if up < 0 else -1, :] = False
            ys, xs = nz2(emit)
            if len(ys):
                keep = rng.random(len(ys)) < 0.22
                ys, xs = ys[keep], xs[keep]
                mat[ys + up, xs] = M.FIRE
                life[ys + up, xs] = rng.integers(25, 70, len(ys)).astype(np.uint8)
            life[burning] = np.maximum(life[burning].astype(np.int16) - 1, 0).astype(np.uint8)
            # liquids burn away faster
            ys, xs = nz2(burning & (M.PHASE[mat] == M.P_LIQUID))
            if len(ys):
                keep = rng.random(len(ys)) < 0.03
                ys, xs = ys[keep], xs[keep]
                mat[ys, xs] = M.FIRE
                life[ys, xs] = 40
                burn[ys, xs] = 0
            done = (burn > 0) & (life == 0)
            res = M.BURN_RESIDUE[mat[done]]
            mat[done] = res
            burn[done] = 0
            life[done] = np.where(res == M.TOXGAS, 160, 0).astype(np.uint8)
            # water next to burning puts it out
            water = mat == M.WATER
            water_near = (self._bshift(water, 1, 0) | self._bshift(water, -1, 0) |
                          self._bshift(water, 0, 1) | self._bshift(water, 0, -1))
            doused = (burn > 0) & water_near
            burn[doused] = 0

    def _acid_pass(self):
        mat, rng = self.v_mat, self.rng
        oy, ox = self._ry0, self._rx0
        acid = mat == M.ACID
        if not acid.any():
            return
        # only freshly disturbed acid corrodes; settled pools turn inert
        # (and therefore cheap) until an explosion or flow stirs them up
        fresh = self.v_rest < self.REST_K
        if not (acid & fresh).any():
            return
        # per-candidate rolls instead of a full-region random field
        roll = np.ones(mat.shape, np.float32)
        ays, axs = nz2(acid & fresh)
        roll[ays, axs] = rng.random(len(ays))
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            acid = (mat == M.ACID) & fresh
            if not acid.any():
                break
            nb = shift(mat, dy, dx, M.BEDROCK)
            eat = acid & (M.CORRODIBLE[nb] > 0) & \
                (roll < M.CORRODIBLE[nb] * 0.5)
            ys, xs = nz2(eat)
            if len(ys) == 0:
                continue
            ty, tx = ys + dy, xs + dx
            mat[ty, tx] = M.EMPTY
            # some acid is spent, sometimes leaving a toxic puff
            spend = rng.random(len(ys))
            mat[ys[spend < 0.30], xs[spend < 0.30]] = M.EMPTY
            puff = spend > 0.93
            mat[ys[puff], xs[puff]] = M.TOXGAS
            self.v_life[ys[puff], xs[puff]] = 140

    def _water_lava_pass(self):
        mat = self.v_mat
        oy, ox = self._ry0, self._rx0
        water = mat == M.WATER
        lava = mat == M.LAVA
        if not (water.any() and lava.any()):
            return
        lava_near = (self._bshift(lava, 1, 0) | self._bshift(lava, -1, 0) |
                     self._bshift(lava, 0, 1) | self._bshift(lava, 0, -1))
        boil = water & lava_near
        if boil.any():
            self._wake_mask(boil, oy, ox)
            mat[boil] = M.STEAM
            self.v_life[boil] = 180
        water_near = (self._bshift(water, 1, 0) | self._bshift(water, -1, 0) |
                      self._bshift(water, 0, 1) | self._bshift(water, 0, -1))
        quench = lava & water_near
        mat[quench] = M.STONE
        self.v_shade[quench] = 0  # dark obsidian look
        self.v_temp[quench] = 80

    def _gas_life_pass(self):
        # region-local: gas sealed away outside the region keeps (a feature —
        # dig into an old pocket and it's still there), free gas dissipates
        mat, life = self.v_mat, self.v_life
        fade = (mat == M.SMOKE) | (mat == M.STEAM) | (mat == M.TOXGAS)
        if self.tick % 6 == 0:        # flammable gas fades much more slowly
            fade |= mat == M.GAS
        if fade.any():
            life[fade] = np.maximum(life[fade].astype(np.int16) - 1, 0).astype(np.uint8)
            gone = fade & (life == 0)
            if gone.any():
                self._wake_mask(gone, self._ry0, self._rx0)
            mat[gone] = M.EMPTY

    def _temp_pass(self):
        mat, t = self.v_mat, self.v_temp
        burn, life = self.v_burn, self.v_life
        oy, ox = self._ry0, self._rx0
        # thermal bounding box: the f32 diffusion only needs to run where
        # heat sources sit or temperature actually deviates from ambient —
        # a water region without fire skips the whole pass
        src = (mat == M.LAVA) | (mat == M.FIRE) | (burn > 0) | \
              (mat == M.ICE) | (mat == M.SNOW)
        hot = src | (np.abs(t - self.ambient) > 0.6)
        rows = hot.any(1)
        if not rows.any():
            return
        cols = hot.any(0)
        r0 = max(0, int(rows.argmax()) - 2)
        r1 = min(hot.shape[0], len(rows) - int(rows[::-1].argmax()) + 2)
        c0 = max(0, int(cols.argmax()) - 2)
        c1 = min(hot.shape[1], len(cols) - int(cols[::-1].argmax()) + 2)
        mat = mat[r0:r1, c0:c1]
        t = t[r0:r1, c0:c1]
        burn = burn[r0:r1, c0:c1]
        life = life[r0:r1, c0:c1]
        oy += r0
        ox += c0
        # sources clamp temperature
        t[mat == M.LAVA] = 900.0
        np.maximum(t, 380.0, where=(mat == M.FIRE), out=t)
        np.maximum(t, 300.0, where=(burn > 0), out=t)
        np.minimum(t, -16.0, where=(mat == M.ICE), out=t)
        np.minimum(t, -8.0, where=(mat == M.SNOW), out=t)
        # diffuse
        avg = (shift(t, 1, 0, self.ambient) + shift(t, -1, 0, self.ambient) +
               shift(t, 0, 1, self.ambient) + shift(t, 0, -1, self.ambient)) * 0.25
        t += (avg - t) * 0.35
        t += (self.ambient - t) * 0.012
        # phase changes
        rng = self.rng
        roll = rng.random(mat.shape)
        def become(src_mask, new_mat, life_v=0, p=1.0):
            m = src_mask if p >= 1.0 else (src_mask & (roll < p))
            if m.any():
                self._wake_mask(m, oy, ox)
                mat[m] = new_mat
                if life_v:
                    life[m] = life_v
                burn[m] = 0
            return m
        become((mat == M.WATER) & (t > 102), M.STEAM, life_v=200, p=0.4)
        become((mat == M.WATER) & (t < -6), M.ICE)
        become((mat == M.ICE) & (t > 18), M.WATER, p=0.3)
        become((mat == M.SNOW) & (t > 10), M.WATER, p=0.3)
        become((mat == M.STEAM) & (t < 40), M.WATER, p=0.03)
        become((mat == M.LAVA) & (t < 430), M.STONE)
        hot_oil = (mat == M.OIL) & (t > 300) & (burn == 0)
        if hot_oil.any():
            self._wake_mask(hot_oil, oy, ox)
            burn[hot_oil] = 1
            life[hot_oil] = M.BURN_FUEL[M.OIL]
        boom = ((mat == M.NITRO) & (t > 210)) | ((mat == M.EXPOWDER) & (t > 280))
        if boom.any():
            ys, xs = nz2(boom)
            for i in range(min(len(ys), 3)):
                self.pending_detonations.append(
                    (ox + int(xs[i]), oy + int(ys[i]),
                     int(mat[ys[i], xs[i]])))
                mat[ys[i], xs[i]] = M.FIRE
                life[ys[i], xs[i]] = 30

    # ----------------------------------------------------------- explosions
    def explode(self, x, y, r, power, heat=120.0, make_fire=False,
                silent=False):
        """Carve a crater. Hardness lets metal/stone shrug off weak blasts."""
        d = self._disk(x, y, r)
        if d is not None:
            sy, sx, d2 = d
            dist = np.sqrt(d2)
            local = power * np.clip(1.15 - dist / max(r, 1), 0.0, 1.0) * 1.6
            sub = self.mat[sy, sx]
            destroy = (dist <= r) & (M.HARDNESS[sub] < local) & (sub != M.BEDROCK)
            # debris: sample some destroyed solid cells into particles
            solid_destroyed = destroy & (M.SOLID[sub])
            ys, xs = nz2(solid_destroyed)
            if len(ys):
                take = self.rng.permutation(len(ys))[:min(len(ys), 60)]
                for i in take:
                    self.events.append({
                        "type": "debris",
                        "x": sx.start + int(xs[i]), "y": sy.start + int(ys[i]),
                        "mat": int(sub[ys[i], xs[i]]),
                        "ox": x, "oy": y, "power": power,
                    })
            sub[destroy] = M.EMPTY
            self.burn[sy, sx][destroy] = 0
            self.rest[sy, sx] = 0     # liquids around the crater wake up
            # shave the 1px needles and shelves blasts leave behind —
            # they wedge worms into unwalkable slots
            solid2 = M.SOLID[sub]
            le = np.ones_like(solid2); re = np.ones_like(solid2)
            le[:, 1:] = ~solid2[:, :-1]
            re[:, :-1] = ~solid2[:, 1:]
            ue = np.ones_like(solid2); de = np.ones_like(solid2)
            ue[1:, :] = ~solid2[:-1, :]
            de[:-1, :] = ~solid2[1:, :]
            needle = solid2 & ((le & re) | (ue & de)) & \
                (dist <= r * 1.4) & (sub != M.BEDROCK)
            sub[needle] = M.EMPTY
            # heat + sparks of fire inside the blast
            self.temp[sy, sx] += heat * np.clip(1.0 - dist / max(r, 1), 0, 1) * 4
            if make_fire:
                fz = destroy & (self.rng.random(destroy.shape) < 0.18)
                sub[fz] = M.FIRE
                self.life[sy, sx][fz] = self.rng.integers(30, 80, int(fz.sum())).astype(np.uint8)
            # secondary: explosives caught in the blast chain-react
            chain = (dist <= r * 1.3) & ((sub == M.EXPOWDER) | (sub == M.NITRO))
            cys, cxs = nz2(chain)
            for i in range(min(len(cys), 5)):
                self.pending_detonations.append(
                    (sx.start + int(cxs[i]), sy.start + int(cys[i]),
                     int(sub[cys[i], cxs[i]])))
                sub[cys[i], cxs[i]] = M.FIRE
            # gas pockets in range deflagrate
            gasm = (dist <= r * 1.6) & (sub == M.GAS)
            sub[gasm] = M.FIRE
            self.life[sy, sx][gasm] = 40
            # splash liquids outward as droplets
            liq = (dist <= r * 1.25) & (M.LIQUID[sub])
            lys, lxs = nz2(liq)
            if len(lys):
                take = self.rng.permutation(len(lys))[:min(len(lys), 50)]
                for i in take:
                    self.events.append({
                        "type": "splash",
                        "x": sx.start + int(lxs[i]), "y": sy.start + int(lys[i]),
                        "mat": int(sub[lys[i], lxs[i]]),
                        "ox": x, "oy": y, "power": power,
                    })
                    sub[lys[i], lxs[i]] = M.EMPTY
        self.wake(x - r * 2, y - r * 2, x + r * 2, y + r * 2)
        if not silent:
            self.events.append({"type": "boom", "x": x, "y": y, "r": r,
                                "power": power})

    def _run_detonations(self):
        n = min(len(self.pending_detonations), 5)
        for _ in range(n):
            x, y, m = self.pending_detonations.pop(0)
            if m == M.NITRO:
                self.explode(x, y, 9, 55, heat=200, make_fire=True)
            else:
                self.explode(x, y, 7, 45, heat=260, make_fire=True)

    # ---------------------------------------------------------------- step
    MARGIN = 6
    REST_K = 30

    def step(self):
        self.tick += 1
        moves = 0
        # anti-stall: while the levelling window is open, re-pulse its box
        # awake every 16 ticks. Probability-gated flow can hit a quiet
        # streak mid-equalization and the wake box would die with real
        # pressure differences left; the pulse gives the spot fresh tries
        # until the window itself expires (then everything truly sleeps).
        if self.tick < self.level_until and self.level_box and \
                self.tick % 16 == 0 and not self.settle_mode:
            lb = self.level_box
            self.wake(lb[2], lb[0], lb[3], lb[1])
        box = self._wake_box
        self._wake_box = None
        self.last_region = None
        # box hysteresis: many flow rules are probability-gated, so a few
        # remaining candidates can all skip one tick by chance — without a
        # grace period that single quiet tick would freeze them forever
        if box is None and self._wake_cool > 0:
            self._wake_cool -= 1
            box = self._cool_box
        elif box is not None:
            self._wake_cool = 40
            self._cool_box = list(box)
        if box is not None:
            y0 = max(0, box[0] - self.MARGIN)
            y1 = min(self.h, box[1] + self.MARGIN)
            x0 = max(0, box[2] - self.MARGIN)
            x1 = min(self.w, box[3] + self.MARGIN)
            self.last_region = (y0, y1, x0, x1)
            self._ry0, self._rx0 = y0, x0
            sy, sx = slice(y0, y1), slice(x0, x1)
            # refresh the phase/density mirrors inside the region only;
            # outside it nothing moves, so stale values are never read.
            self.phase[sy, sx] = M.PHASE[self.mat[sy, sx]]
            self.dens[sy, sx] = M.DENSITY[self.mat[sy, sx]]
            self.v_mat = self.mat[sy, sx]
            self.v_shade = self.shade[sy, sx]
            self.v_life = self.life[sy, sx]
            self.v_burn = self.burn[sy, sx]
            self.v_rest = self.rest[sy, sx]
            self.v_head = self.head[sy, sx]
            self.v_temp = self.temp[sy, sx]
            self.v_phase = self.phase[sy, sx]
            self.v_dens = self.dens[sy, sx]
            self.v_moved = self.moved[sy, sx]
            self.v_rnd = self.rng.random((y1 - y0, x1 - x0), dtype=np.float32)
            for s in range(SIM_SUBSTEPS):
                self.v_moved[:] = 0
                parity = (self.tick + s) % 2 == 0
                moves += self._powder_pass(parity, lateral=(s == 0))
                moves += self._liquid_pass(parity, lateral=(s == 0))
            self.v_moved[:] = 0
            moves += self._gas_pass(self.tick % 2 == 0)
            # every fluid cell ages toward rest; real flow resets the clock
            ph = self.v_phase
            fluid = (ph == M.P_LIQUID) | (ph == M.P_GAS)
            inc = fluid & (self.v_rest < 255)
            self.v_rest[inc] += 1
            # reactions only happen where something is awake. During the
            # mapgen pre-settle the destructive chemistry pauses — fire,
            # acid and heat — so lava doesn't burn down the scenery before
            # the players arrive. Gases still age out and water still
            # quenches lava, or the settle would never go quiet.
            if not self.settle_mode:
                if self.tick % 2 == 1:
                    self._fire_pass()    # 30 Hz is plenty for flames
                if self.tick % 2 == 0:
                    self._acid_pass()
                if self.tick % 2 == 1:
                    self._temp_pass()
            self._water_lava_pass()
            self._gas_life_pass()
        self._run_detonations()
        self.activity = moves

    # ------------------------------------------------------------ snapshot
    def to_bytes(self) -> bytes:
        payload = b"".join([
            self.mat.tobytes(), self.shade.tobytes(), self.life.tobytes(),
            self.burn.tobytes(), self.rest.tobytes(),
            self.head.astype(np.uint32).tobytes(),
            self.temp.astype(np.float32).tobytes(),
        ])
        return zlib.compress(payload, 6)

    def from_bytes(self, data: bytes):
        raw = zlib.decompress(data)
        n = self.w * self.h
        self.mat = np.frombuffer(raw[:n], np.uint8).reshape(self.h, self.w).copy()
        self.shade = np.frombuffer(raw[n:2*n], np.uint8).reshape(self.h, self.w).copy()
        self.life = np.frombuffer(raw[2*n:3*n], np.uint8).reshape(self.h, self.w).copy()
        self.burn = np.frombuffer(raw[3*n:4*n], np.uint8).reshape(self.h, self.w).copy()
        self.rest = np.frombuffer(raw[4*n:5*n], np.uint8).reshape(self.h, self.w).copy()
        self.head = np.frombuffer(raw[5*n:9*n], np.uint32).reshape(self.h, self.w).copy()
        self.temp = np.frombuffer(raw[9*n:13*n], np.float32).reshape(self.h, self.w).copy()
