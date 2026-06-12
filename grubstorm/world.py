"""The living world: a fully simulated falling-sand grid.

Every pass is vectorized numpy. All randomness comes from a seeded
np.random.Generator so lockstep multiplayer stays deterministic.
"""
import zlib
import numpy as np

from . import materials as M
from .constants import GRID_W, GRID_H, SIM_SUBSTEPS


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
        self.lmass = np.zeros((h, w), np.float32)  # liquid mass (pressure)
        self.temp = np.full((h, w), 20.0, np.float32)
        self.moved = np.zeros((h, w), np.uint8)
        self.tick = 0
        self.wind = 0.0
        self.ambient = 20.0
        self.gravity_dir = 1                       # -1 in gravity-invert chaos
        self.activity = 0                          # moved cells last tick
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
        self._wake_boxes: list[list[int]] = [[0, h, 0, w]]
        self._wake_cool = 0
        self._cool_boxes: list[list[int]] = []
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
    WAKE_GAP = 24        # boxes closer than this merge into one
    MAX_BOXES = 5

    def wake(self, x0, y0, x1, y1):
        """Mark a rectangle (inclusive coords) as active next step.
        Disjoint activity pockets get their own boxes so one dripping
        brook can't stretch the region across the whole sleeping map."""
        x0 = max(0, int(x0)); y0 = max(0, int(y0))
        x1 = min(self.w, int(x1) + 1); y1 = min(self.h, int(y1) + 1)
        if x0 >= x1 or y0 >= y1:
            return
        new = [y0, y1, x0, x1]
        gap = self.WAKE_GAP
        boxes = self._wake_boxes
        merged = True
        while merged:
            merged = False
            for i, b in enumerate(boxes):
                if (new[0] - gap < b[1] and b[0] - gap < new[1] and
                        new[2] - gap < b[3] and b[2] - gap < new[3]):
                    new = [min(new[0], b[0]), max(new[1], b[1]),
                           min(new[2], b[2]), max(new[3], b[3])]
                    boxes.pop(i)
                    merged = True
                    break
        boxes.append(new)
        if len(boxes) > self.MAX_BOXES:
            # merge the two closest boxes (deterministic order)
            best, bi, bj = None, 0, 1
            for i in range(len(boxes)):
                for j in range(i + 1, len(boxes)):
                    a, b = boxes[i], boxes[j]
                    dy = max(0, max(a[0], b[0]) - min(a[1], b[1]))
                    dx = max(0, max(a[2], b[2]) - min(a[3], b[3]))
                    d2 = dy * dy + dx * dx
                    if best is None or d2 < best:
                        best, bi, bj = d2, i, j
            a, b = boxes[bi], boxes[bj]
            boxes[bi] = [min(a[0], b[0]), max(a[1], b[1]),
                         min(a[2], b[2]), max(a[3], b[3])]
            boxes.pop(bj)
        d = self.render_dirty
        if d is None:
            self.render_dirty = [y0, y1, x0, x1]
        else:
            d[0] = min(d[0], y0); d[1] = max(d[1], y1)
            d[2] = min(d[2], x0); d[3] = max(d[3], x1)

    @property
    def _wake_box(self):
        """Compat view: the union of all active boxes (None if asleep)."""
        if not self._wake_boxes:
            return None
        b = self._wake_boxes[0]
        out = list(b)
        for b in self._wake_boxes[1:]:
            out = [min(out[0], b[0]), max(out[1], b[1]),
                   min(out[2], b[2]), max(out[3], b[3])]
        return out

    @_wake_box.setter
    def _wake_box(self, box):
        self._wake_boxes = [] if box is None else [list(box)]

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
        ys, xs = np.nonzero(mask[r0:r1])
        ys += r0
        n = len(ys)
        ty, tx = ys + dy, xs + dx
        for a in (self.v_mat, self.v_shade, self.v_life, self.v_burn,
                  self.v_temp, self.v_phase, self.v_dens, self.v_rest,
                  self.v_lmass):
            tmp = a[ty, tx].copy()
            a[ty, tx] = a[ys, xs]
            a[ys, xs] = tmp
        self.v_moved[ys, xs] = 1
        self.v_moved[ty, tx] = 1
        if reset_rest:
            # gravity-driven motion is "real" flow and wakes the cell;
            # flat lateral wandering carries its rest along and ages out
            self.v_rest[ys, xs] = 0
            self.v_rest[ty, tx] = 0
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

    # --- compressible-liquid constants (w-shadow.com mass model) ---------
    LMAX = np.float32(1.0)       # normal mass capacity of a cell
    LCOMP = np.float32(0.03)     # extra capacity per cell of depth
    LMIN_SEE = np.float32(0.10)  # a cell materializes above this mass
    LMIN_KEEP = np.float32(0.04) # and evaporates below this one
    LFLOW_MIN = np.float32(0.0015)

    def _stable_bottom(self, total):
        """Of `total` mass shared by two stacked cells, how much belongs in
        the lower one. Slight compressibility IS the pressure model: the
        excess that doesn't fit gets pushed back up, so U-tubes equalize."""
        out = total.copy()
        LMAX, LCOMP = self.LMAX, self.LCOMP
        mid = (total > LMAX) & (total < 2 * LMAX + LCOMP)
        out[mid] = (LMAX * LMAX + total[mid] * LCOMP) / (LMAX + LCOMP)
        hi = total >= 2 * LMAX + LCOMP
        out[hi] = (total[hi] + LCOMP) * np.float32(0.5)
        return out

    def _liquid_pass(self, parity, lateral=True):
        """Liquids are a compressible-mass cellular automaton (the classic
        w-shadow model): every liquid cell carries mass, mass flows down,
        sideways and — when compressed — UP. Hydrostatic pressure, tank
        drains, U-tubes and level surfaces all emerge from one rule set.
        Cells materialize/vanish from the mass field; density layering
        between different liquids stays a discrete swap."""
        g = self.gravity_dir
        ph = self.v_phase
        liq = ph == M.P_LIQUID
        m = self.v_lmass
        if not liq.any():
            if m.any():
                m[:] = 0.0       # mat was edited away (explosions, spells)
            return 0
        # sync the mass plane with the material plane: edits rule. Air
        # cells may carry sub-visible residue mass (it gets re-collected),
        # anything else solidified/burned/blasted away takes its mass.
        kill = ~liq & (ph != M.P_EMPTY)
        m[kill] = 0.0
        np.copyto(m, self.LMAX, where=liq & (m <= 0))
        rnd, dens = self.v_rnd, self.v_dens
        visc = M.VISCOSITY[self.v_mat]
        # density layering between different liquids (oil floats on water)
        below_liq = self._bshift(ph, g, 0) == M.P_LIQUID
        below_dens = self._bshift(dens, g, 0)
        # density layering also spends the rest budget: mixed pockets
        # stratify once and go quiet; an explosion stirring them re-arms
        # the clock and they re-stratify. Without the gate one sealed
        # water+oil cave churns its swap/flow cycle forever.
        sink = liq & (self.v_moved == 0) & below_liq & \
            (dens > below_dens) & (rnd < 0.4) & \
            (self.v_rest < self.REST_K)
        n = self._apply_moves(sink, g, 0, reset_rest=False)
        # discrete bulk transport: falling, diagonal slumping and pouring
        # over edges move whole cells (their mass rides along in the swap).
        # This is what makes splashes lively and towers collapse fast —
        # the mass automaton below is diffusive and only excels at the
        # fine grade: pressure, U-tubes and dead-level surfaces.
        free = ph <= M.P_GAS
        fall = liq & (self.v_moved == 0) & self._bshift(free, g, 0)
        # falling does NOT renew the rest budget: the vertical mass work
        # below does that where it matters. Otherwise one air bubble
        # circulating inside a sealed pocket re-arms the lateral rules
        # forever and buried oil/nitro pockets never sleep.
        n += self._apply_moves(fall, g, 0, reset_rest=False)
        d = 1 if parity else -1
        # slumping and pouring spend the rest budget instead of renewing
        # it: fresh splashes are lively (rest 0), but a settled shoreline
        # can't ping-pong its own edge cells awake forever
        awake = self.v_rest < self.REST_K
        for dd in (d, -d):
            ph = self.v_phase
            liq2 = (ph == M.P_LIQUID) & (self.v_moved == 0) & awake
            free = ph <= M.P_GAS
            ok = liq2 & self._bshift(free, g, dd) & (rnd > visc * 0.5)
            n += self._apply_moves(ok, g, dd, reset_rest=False)
        for dd in (d, -d):
            ph = self.v_phase
            liq2 = (ph == M.P_LIQUID) & (self.v_moved == 0) & awake
            free = ph <= M.P_GAS
            pour = liq2 & ~self._bshift(free, g, 0) & \
                self._bshift(free, 0, dd) & self._bshift(free, g, dd) & \
                (rnd > visc)
            n += self._apply_moves(pour, 0, dd, reset_rest=False)
        # fine grade: the compressible-mass automaton
        ph = self.v_phase
        liq = ph == M.P_LIQUID
        for art in np.unique(self.v_mat[liq]):
            n += self._mass_flow(int(art), g)
        return n

    def _mass_flow(self, art, g):
        mat, ph = self.v_mat, self.v_phase
        mine = mat == art
        # the map ocean is decorative with a fixed level (sudden death
        # raises it discretely) — exempting it from the mass automaton
        # keeps a 480-wide always-on water body from dominating every
        # tick. Discrete splash rules still run there.
        if self.water_level < self.h:
            oy = self.water_level - self._ry0
            if oy < mine.shape[0]:
                mine[max(0, oy):] = False
        # air-borne residue mass joins whichever liquid works the area
        carry = mine | (ph == M.P_EMPTY)
        m = np.where(carry, np.maximum(self.v_lmass, np.float32(0.0)),
                     np.float32(0.0))
        # where this liquid may spread: empty cells, gas (displaced), self
        open_ = (ph == M.P_EMPTY) | (ph == M.P_GAS) | mine
        rate = np.float32(1.0 - M.VISCOSITY[art])
        if M.STICKY[art] > 0:
            stat = ph == M.P_STATIC
            near = (self._bshift(stat, 0, 1) | self._bshift(stat, 0, -1) |
                    self._bshift(stat, g, 0) | self._bshift(stat, -g, 0))
            ratem = np.where(near, rate * np.float32(1.0 - M.STICKY[art] * 0.9),
                             rate)
        else:
            ratem = rate
        # DOWN: settle toward the two-cell stable distribution
        mb = shift(m, g, 0, 0.0)
        dn = np.clip(self._stable_bottom(m + mb) - mb, 0.0, m) * ratem
        dn[~(carry & self._bshift(open_, g, 0))] = 0.0
        # SIDEWAYS: equalize with each neighbour by a quarter of the gap.
        # Thin films stop creeping below LMIN_SEE so pools don't smear out
        # into invisible sheets across the whole map. Lateral diffusion
        # also spends the rest budget: vertical work (compression, rises)
        # keeps re-arming it, pure sideways wandering dies out and lets
        # big waters go back to sleep.
        # only REAL cells spread sideways: air residue may only sink
        # (dn uses `carry`) until a pool absorbs it — otherwise every
        # liquid species in the region keeps shuffling the same residue
        # back and forth forever and the map never sleeps
        spread_ok = mine & (m > self.LMIN_SEE) & \
            (self.v_rest < self.REST_K * 4)
        ml = shift(m, 0, -1, 0.0)
        sl = np.clip((m - ml) * np.float32(0.25), 0.0, m) * ratem
        sl[~(spread_ok & self._bshift(open_, 0, -1))] = 0.0
        mr = shift(m, 0, 1, 0.0)
        sr = np.clip((m - mr) * np.float32(0.25), 0.0, m) * ratem
        sr[~(spread_ok & self._bshift(open_, 0, 1))] = 0.0
        # UP: only compressed mass rises — this is the pressure release.
        # Under-relaxed by half: a cell squeezed between two stacked
        # partners gets contradictory dn/up targets under Jacobi updates
        # and would flip-flop above the flow cutoff forever; halving the
        # correction makes the oscillation decay below it within ticks.
        ma = shift(m, -g, 0, 0.0)
        up = np.clip(m - self._stable_bottom(m + ma), 0.0, m) * ratem \
            * np.float32(0.5)
        # up also spends the rest budget: during real drainage the
        # accompanying falls keep re-arming it, while a sealed pocket's
        # up/dn micro-cycle (lift a crumb, drip it back, forever) runs
        # out of clock and the pocket finally sleeps
        up[~(mine & self._bshift(open_, -g, 0) &
             (self.v_rest < self.REST_K * 4))] = 0.0
        # NOTE: residue in air may fall and spread but never pushes UP —
        # only real, visible water carries pressure
        # conservation: never ship more than the cell holds
        out = dn + sl + sr + up
        over = (out > m) & (out > 0)
        if over.any():
            f = m[over] / out[over]
            dn[over] *= f; sl[over] *= f; sr[over] *= f; up[over] *= f
        for fl in (dn, sl, sr, up):
            fl[fl < self.LFLOW_MIN] = 0.0
        moved = (dn > 0) | (sl > 0) | (sr > 0) | (up > 0)
        if not moved.any():
            return 0
        # DOWNWARD mass work is real gravity activity: refresh the rest
        # clock there (and at the receiving cells). UP does NOT re-arm:
        # a sealed pocket teleporting its air bubble via appear/vanish
        # cycles up-flows forever, and counting those as work kept whole
        # maps awake. Pressure equalization is sustained by the falls and
        # pours that accompany any REAL drainage instead.
        vert = dn > 0.01
        if vert.any():
            self.v_rest[vert] = 0
            self.v_rest[shift(dn, -g, 0, 0.0) > 0.01] = 0
        new_m = m - (dn + sl + sr + up)
        new_m += shift(dn, -g, 0, 0.0)
        new_m += shift(sl, 0, 1, 0.0)
        new_m += shift(sr, 0, -1, 0.0)
        new_m += shift(up, g, 0, 0.0)
        # materialize / evaporate cells from the mass field (hysteresis)
        appear = ~mine & (new_m > self.LMIN_SEE) & open_
        vanish = mine & (new_m < self.LMIN_KEEP)
        if appear.any():
            mat[appear] = art
            self.v_phase[appear] = M.P_LIQUID
            self.v_dens[appear] = M.DENSITY[art]
            self.v_shade[appear] = self.v_tex[appear]
            self.v_life[appear] = 0
            self.v_burn[appear] = 0
        if vanish.any():
            # the CELL evaporates, its residue mass stays in the air cell
            # and gets re-collected — mass is conserved
            mat[vanish] = M.EMPTY
            self.v_phase[vanish] = M.P_EMPTY
            self.v_dens[vanish] = 0
        # relief valve: nothing holds more than the deepest legitimate
        # hydrostatic load — impact zones under a hose otherwise build
        # monster cells that then read as a sunken water level
        cap = self.LMAX + 60 * self.LCOMP
        burst = new_m > cap
        if burst.any():
            exc = np.where(burst, new_m - cap, np.float32(0.0))
            target_open = shift(open_ | mine, g, 0, False)
            exc[~target_open] = 0.0
            new_m -= exc
            new_m += shift(exc, g, 0, 0.0)
        write = carry | appear | (new_m != m)
        self.v_lmass[write] = new_m[write]
        touched = moved | appear | vanish
        self._wake_mask(touched, self._ry0, self._rx0)
        return int(np.count_nonzero(moved))

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
            ys, xs = np.nonzero(dead)
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
        cys, cxs = np.nonzero(cand)
        if len(cys):
            roll = rng.random(len(cys))
            lit = roll < M.FLAMMABLE[mat[cys, cxs]]
            ignite[cys[lit], cxs[lit]] = True
        if ignite.any():
            self._wake_mask(ignite, oy, ox)
            det = ignite & ((mat == M.EXPOWDER) | (mat == M.NITRO))
            if det.any():
                ys, xs = np.nonzero(det)
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
            ys, xs = np.nonzero(emit)
            if len(ys):
                keep = rng.random(len(ys)) < 0.22
                ys, xs = ys[keep], xs[keep]
                mat[ys + up, xs] = M.FIRE
                life[ys + up, xs] = rng.integers(25, 70, len(ys)).astype(np.uint8)
            life[burning] = np.maximum(life[burning].astype(np.int16) - 1, 0).astype(np.uint8)
            # liquids burn away faster
            ys, xs = np.nonzero(burning & (M.PHASE[mat] == M.P_LIQUID))
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
        ays, axs = np.nonzero(acid & fresh)
        roll[ays, axs] = rng.random(len(ays))
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            acid = (mat == M.ACID) & fresh
            if not acid.any():
                break
            nb = shift(mat, dy, dx, M.BEDROCK)
            eat = acid & (M.CORRODIBLE[nb] > 0) & \
                (roll < M.CORRODIBLE[nb] * 0.5)
            ys, xs = np.nonzero(eat)
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
            ys, xs = np.nonzero(boom)
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
            ys, xs = np.nonzero(solid_destroyed)
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
            cys, cxs = np.nonzero(chain)
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
            lys, lxs = np.nonzero(liq)
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
        boxes = self._wake_boxes
        self._wake_boxes = []
        self.last_region = None
        # box hysteresis: many flow rules are probability-gated, so a few
        # remaining candidates can all skip one tick by chance — without a
        # grace period that single quiet tick would freeze them forever
        if not boxes and self._wake_cool > 0:
            self._wake_cool -= 1
            boxes = [list(b) for b in self._cool_boxes]
        elif boxes:
            self._wake_cool = 10
            self._cool_boxes = [list(b) for b in boxes]
        for box in boxes:
            moves += self._step_region(box)
        self._run_detonations()
        self.activity = moves

    def _step_region(self, box):
        """Run one tick of simulation inside one active box. Distant
        activity pockets get their own small boxes (a brook on the left
        must not stretch the region across a sleeping ocean)."""
        moves = 0
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
        self.v_lmass = self.lmass[sy, sx]
        self.v_tex = self.tex[sy, sx]
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
            self._fire_pass()
            if self.tick % 2 == 0:
                self._acid_pass()
            if self.tick % 2 == 1:
                self._temp_pass()
        self._water_lava_pass()
        self._gas_life_pass()
        return moves

    # ------------------------------------------------------------ snapshot
    def to_bytes(self) -> bytes:
        payload = b"".join([
            self.mat.tobytes(), self.shade.tobytes(), self.life.tobytes(),
            self.burn.tobytes(), self.rest.tobytes(),
            self.lmass.astype(np.float32).tobytes(),
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
        self.lmass = np.frombuffer(raw[5*n:9*n], np.float32).reshape(self.h, self.w).copy()
        self.temp = np.frombuffer(raw[9*n:13*n], np.float32).reshape(self.h, self.w).copy()
