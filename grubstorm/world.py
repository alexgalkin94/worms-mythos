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
        self.temp = np.full((h, w), 20.0, np.float32)
        self.moved = np.zeros((h, w), np.uint8)
        self.tick = 0
        self.wind = 0.0
        self.ambient = 20.0
        self.gravity_dir = 1                       # -1 in gravity-invert chaos
        self.activity = 0                          # moved cells last tick
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

    def _apply_moves(self, mask, dy, dx, reset_rest=True):
        ys, xs = np.nonzero(mask)
        n = len(ys)
        if n == 0:
            return 0
        ty, tx = ys + dy, xs + dx
        for a in (self.v_mat, self.v_shade, self.v_life, self.v_burn,
                  self.v_temp, self.v_phase, self.v_dens, self.v_rest):
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
        self.wake(ox + int(xs.min()) - 1, oy + int(ys.min()) - 1,
                  ox + int(xs.max()) + 1, oy + int(ys.max()) + 1)
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

    def _powder_pass(self, parity):
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
        # diagonal slide (uniform direction per call avoids target conflicts)
        d = 1 if parity else -1
        for dd in (d, -d):
            ph = self.v_phase
            powder = ph == M.P_POWDER
            free = ph <= M.P_GAS
            ok = powder & self._bshift(free, g, dd) & self._bshift(free, 0, dd) \
                 & (rnd < 0.7)
            n += self._apply_moves(ok, g, dd)
        return n

    def _liquid_pass(self, parity):
        g = self.gravity_dir
        ph, dens, rnd = self.v_phase, self.v_dens, self.v_rnd
        liq = ph == M.P_LIQUID
        if not liq.any():
            return 0
        free = ph <= M.P_GAS
        below_free = self._bshift(free, g, 0)
        below_liq = self._bshift(ph, g, 0) == M.P_LIQUID
        below_dens = self._bshift(dens, g, 0)
        sink = below_liq & (dens > below_dens) & (rnd < 0.4)
        n = self._apply_moves(liq & (below_free | sink), g, 0)
        d = 1 if parity else -1
        for dd in (d, -d):
            ph = self.v_phase
            liq = ph == M.P_LIQUID
            free = ph <= M.P_GAS
            ok = liq & self._bshift(free, g, dd)
            n += self._apply_moves(ok, g, dd)
        # lateral flow. Pouring over an edge is real flow (always allowed,
        # resets rest); sloshing on flat ground is gated by the rest counter
        # so big pools go to sleep instead of jittering forever.
        visc = M.VISCOSITY[self.v_mat]
        for dd in (d, -d):
            ph = self.v_phase
            free = ph <= M.P_GAS
            liq = (ph == M.P_LIQUID) & (self.v_moved == 0)
            grounded = liq & ~self._bshift(free, g, 0)
            side_free = self._bshift(free, 0, dd) & (rnd > visc) & \
                ((rnd < 0.5) if dd == d else (rnd >= 0.5))
            pour = self._bshift(free, g, dd)
            n += self._apply_moves(grounded & side_free & pour, 0, dd)
            ph = self.v_phase
            liq = (ph == M.P_LIQUID) & (self.v_moved == 0)
            grounded = liq & ~self._bshift(ph <= M.P_GAS, g, 0)
            flat = grounded & side_free & ~pour & \
                (self.v_rest < self.REST_K)
            n += self._apply_moves(flat, 0, dd, reset_rest=False)
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
        # fire consumes itself
        fire = mat == M.FIRE
        if fire.any():
            self._wake_mask(fire, oy, ox)
            life[fire] = np.maximum(life[fire].astype(np.int16) - 1, 0).astype(np.uint8)
            dead = fire & (life == 0)
            r = rng.random(mat.shape)
            mat[dead & (r < 0.25)] = M.SMOKE
            life[dead & (r < 0.25)] = rng.integers(40, 120)
            mat[dead & (r >= 0.25)] = M.EMPTY

        # ignition: anything flammable next to heat
        hot = fire | (mat == M.LAVA) | (burn > 0)
        near_hot = (self._bshift(hot, 1, 0) | self._bshift(hot, -1, 0) |
                    self._bshift(hot, 0, 1) | self._bshift(hot, 0, -1))
        # heat agitates settled fluids so fuel keeps flowing toward flames
        self.v_rest[near_hot | hot] = 0
        flam = M.FLAMMABLE[mat]
        roll = rng.random(mat.shape)
        ignite = near_hot & (flam > 0) & (roll < flam) & (burn == 0)
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
            emit = burning & above_empty & (rng.random(mat.shape) < 0.22)
            emit[0 if up < 0 else -1, :] = False
            ys, xs = np.nonzero(emit)
            if len(ys):
                mat[ys + up, xs] = M.FIRE
                life[ys + up, xs] = rng.integers(25, 70, len(ys)).astype(np.uint8)
            life[burning] = np.maximum(life[burning].astype(np.int16) - 1, 0).astype(np.uint8)
            # liquids burn away faster
            consumed = burning & (M.PHASE[mat] == M.P_LIQUID) & \
                       (rng.random(mat.shape) < 0.03)
            mat[consumed] = M.FIRE
            life[consumed] = 40
            burn[consumed] = 0
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
        roll = rng.random(mat.shape)
        # only freshly disturbed acid corrodes; settled pools turn inert
        # (and therefore cheap) until an explosion or flow stirs them up
        fresh = self.v_rest < self.REST_K
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
        box = self._wake_box
        self._wake_box = None
        self.last_region = None
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
            self.v_temp = self.temp[sy, sx]
            self.v_phase = self.phase[sy, sx]
            self.v_dens = self.dens[sy, sx]
            self.v_moved = self.moved[sy, sx]
            self.v_rnd = self.rng.random((y1 - y0, x1 - x0), dtype=np.float32)
            for s in range(SIM_SUBSTEPS):
                self.v_moved[:] = 0
                parity = (self.tick + s) % 2 == 0
                moves += self._powder_pass(parity)
                moves += self._liquid_pass(parity)
            self.v_moved[:] = 0
            moves += self._gas_pass(self.tick % 2 == 0)
            # every fluid cell ages toward rest; real flow resets the clock
            ph = self.v_phase
            fluid = (ph == M.P_LIQUID) | (ph == M.P_GAS)
            inc = fluid & (self.v_rest < 255)
            self.v_rest[inc] += 1
            # reactions only happen where something is awake
            self._fire_pass()
            if self.tick % 2 == 0:
                self._acid_pass()
            self._water_lava_pass()
            if self.tick % 2 == 1:
                self._temp_pass()
            self._gas_life_pass()
        self._run_detonations()
        self.activity = moves

    # ------------------------------------------------------------ snapshot
    def to_bytes(self) -> bytes:
        payload = b"".join([
            self.mat.tobytes(), self.shade.tobytes(), self.life.tobytes(),
            self.burn.tobytes(), self.rest.tobytes(),
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
        self.temp = np.frombuffer(raw[5*n:5*n+4*n], np.float32).reshape(self.h, self.w).copy()
