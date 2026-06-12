"""Simulation particles: flying debris, liquid droplets, sparks.

These are part of the deterministic simulation (debris re-deposits into the
grid), so they use the world's RNG. Pure eye-candy lives in render.py.
"""
import numpy as np

from . import materials as M
from .constants import GRAVITY

KIND_MAT = 0      # deposits its material when it lands
KIND_SPARK = 1    # electricity: crawls along conductive cells, hurts grubs
KIND_FX = 2       # visual only (embers, glow motes)

MAX_PARTICLES = 3000


class Particles:
    def __init__(self):
        n = MAX_PARTICLES
        self.x = np.zeros(n, np.float32)
        self.y = np.zeros(n, np.float32)
        self.vx = np.zeros(n, np.float32)
        self.vy = np.zeros(n, np.float32)
        self.mat = np.zeros(n, np.uint8)
        self.kind = np.zeros(n, np.uint8)
        self.life = np.zeros(n, np.int16)
        self.alive = np.zeros(n, bool)
        self._cursor = 0

    def spawn(self, x, y, vx, vy, mat=0, kind=KIND_MAT, life=600):
        i = self._cursor
        self._cursor = (i + 1) % MAX_PARTICLES
        self.x[i], self.y[i] = x, y
        self.vx[i], self.vy[i] = vx, vy
        self.mat[i], self.kind[i], self.life[i] = mat, kind, life
        self.alive[i] = True

    def burst(self, rng, x, y, n, mat, speed, kind=KIND_MAT, life=600,
              up_bias=0.6):
        for _ in range(n):
            a = rng.random() * 2 * np.pi
            s = speed * (0.3 + rng.random() * 0.7)
            self.spawn(x, y, np.cos(a) * s,
                       np.sin(a) * s - speed * up_bias * rng.random(),
                       mat, kind, life)

    def step(self, world):
        idx = np.nonzero(self.alive)[0]
        if len(idx) == 0:
            return
        self.life[idx] -= 1
        self.vy[idx] += GRAVITY * 1.4 * world.gravity_dir
        self.x[idx] += self.vx[idx]
        self.y[idx] += self.vy[idx]
        xi = self.x[idx].astype(int)
        yi = self.y[idx].astype(int)
        oob = (xi < 1) | (xi >= world.w - 1) | (yi < 1) | (yi >= world.h - 1)
        dead = oob | (self.life[idx] <= 0)
        self.alive[idx[dead]] = False
        live = idx[~dead]
        if len(live) == 0:
            return
        xi, yi = xi[~dead], yi[~dead]
        cell = world.mat[yi, xi]
        ph = M.PHASE[cell]
        blocked = ph >= M.P_LIQUID
        if not blocked.any():
            return
        kinds = self.kind[live]
        # visual motes just die on contact (vectorized: no per-kind effects)
        self.alive[live[blocked & (kinds == KIND_FX)]] = False
        # debris deposits one cell back where it came from, in index order
        # — two grains aiming at one cell: the first one claims it
        for j in np.nonzero(blocked & (kinds == KIND_MAT))[0]:
            i = live[j]
            bx = int(self.x[i] - self.vx[i])
            by = int(self.y[i] - self.vy[i])
            if world.in_bounds(bx, by) and \
                    M.PHASE[world.mat[by, bx]] <= M.P_GAS:
                world.set_cell(bx, by, int(self.mat[i]))
            self.alive[i] = False
        for j in np.nonzero(blocked & (kinds == KIND_SPARK))[0]:
            i = live[j]
            # crawl along the surface: pick a conductive neighbour
            if M.CONDUCTIVE[cell[j]]:
                self.vx[i] *= 0.8
                self.vy[i] = -abs(self.vy[i]) * 0.4
                world.temp[yi[j], xi[j]] += 40
            else:
                self.vx[i] = -self.vx[i] * 0.5
                self.vy[i] = -self.vy[i] * 0.5

    def live_indices(self):
        return np.nonzero(self.alive)[0]
