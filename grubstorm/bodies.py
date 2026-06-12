"""Rigid props: crates, planks and stone blocks living inside the sand sim.

A body is an axis-aligned rect of real material cells. Each tick it erases
its cells, runs simple physics (gravity, buoyancy, terrain collision,
explosion impulses), and writes its cells back — so liquids pool against
it, worms stand on it, fire eats it and explosions shove it around. When
its hit points run out it bursts into loose material.

Deterministic: only game/world RNG, fixed update order, serialized into
net snapshots.
"""
import numpy as np

from . import materials as M
from .constants import GRAVITY

KINDS = {
    # w, h, material, hp, density (vs water 100)
    "crate": (7, 7, M.WOOD, 60, 55),
    "plank": (16, 4, M.WOOD, 45, 50),
    "block": (8, 6, M.STONE, 140, 230),
    "beam":  (14, 3, M.METAL, 160, 250),
}

_NO_CELLS = (np.empty(0, np.intp), np.empty(0, np.intp))

# plain-Python lookup: mat.item() + list beats stacked world.get/is_solid
# calls on tiny perimeter scans (same values, so bit-identical physics)
_SOLID = M.SOLID.tolist()


class RigidBody:
    def __init__(self, x, y, kind="crate"):
        self.kind = kind
        w, h, mat, hp, dens = KINDS[kind]
        self.x, self.y = float(x), float(y)      # centre
        self.w, self.h = w, h
        self.mat = mat
        self.hp = float(hp)
        self.dens = dens
        self.vx = self.vy = 0.0
        self.alive = True
        self.resting = False
        self.written = _NO_CELLS                  # (xs, ys) we own right now
        self._box = None                          # bbox of written cells

    # ------------------------------------------------------------ cells
    def _rect_clip(self, world):
        """Our rect clipped to the grid: (x0, x1, y0, y1) cell bounds."""
        x0 = int(self.x - self.w / 2)
        y0 = int(self.y - self.h / 2)
        return (max(0, x0), min(world.w, x0 + self.w),
                max(0, y0), min(world.h, y0 + self.h))

    def reclaim(self, world):
        """Rebuild ownership from the grid (snapshot restore): claim only
        rect cells that still hold our material."""
        x0, x1, y0, y1 = self._rect_clip(world)
        if x0 >= x1 or y0 >= y1:
            self.written = _NO_CELLS
            self._box = None
            return
        ys, xs = np.nonzero(world.mat[y0:y1, x0:x1] == self.mat)
        if len(xs):
            self.written = (xs + x0, ys + y0)
            self._box = (int(xs.min()) + x0, int(ys.min()) + y0,
                         int(xs.max()) + x0, int(ys.max()) + y0)
        else:
            self.written = _NO_CELLS
            self._box = None

    def erase(self, world):
        xs, ys = self.written
        changed = 0
        if len(xs):
            own = world.mat[ys, xs] == self.mat
            n_own = int(np.count_nonzero(own))
            changed = len(xs) - n_own                       # blasted/melted
            if n_own == len(xs):
                ox, oy = xs, ys           # intact: skip the fancy-index copy
            else:
                ox, oy = xs[own], ys[own]
            if n_own:
                changed += int(np.count_nonzero(world.burn[oy, ox]))  # on fire
                world.mat[oy, ox] = M.EMPTY
                world.burn[oy, ox] = 0
            bx0, by0, bx1, by1 = self._box
            world.wake(bx0 - 1, by0 - 1, bx1 + 1, by1 + 1)
        self.written = _NO_CELLS
        self._box = None
        if changed:
            self.hp -= changed * 1.5
            self.resting = False

    def write(self, world):
        x0, x1, y0, y1 = self._rect_clip(world)
        xs = ys = None
        if x0 < x1 and y0 < y1:
            sub = world.mat[y0:y1, x0:x1]
            place = M.PHASE[sub] <= M.P_LIQUID    # displace fluids & air
            pys, pxs = np.nonzero(place)
            if len(pxs):
                sub[place] = self.mat
                world.shade[y0:y1, x0:x1][place] = \
                    world.tex[y0:y1, x0:x1][place]
                xs, ys = pxs + x0, pys + y0
        if xs is None:
            self.written = _NO_CELLS
            self._box = None
            world.wake(int(self.x) - 1, int(self.y) - 1,
                       int(self.x) + 1, int(self.y) + 1)
        else:
            self.written = (xs, ys)
            self._box = (int(xs.min()), int(ys.min()),
                         int(xs.max()), int(ys.max()))
            bx0, by0, bx1, by1 = self._box
            world.wake(bx0 - 1, by0 - 1, bx1 + 1, by1 + 1)

    # ---------------------------------------------------------- physics
    def _solid_below(self, world):
        y = int(self.y + self.h / 2)
        x0 = int(self.x - self.w / 2)
        if 0 <= y < world.h:
            c0, c1 = max(0, x0), min(world.w, x0 + self.w)
            hits = self.w - (c1 - c0)             # off-grid reads bedrock
            if c0 < c1:
                hits += int(np.count_nonzero(M.SOLID[world.mat[y, c0:c1]]))
        else:
            hits = self.w
        return hits >= max(2, self.w // 4)

    def _liquid_frac(self, world):
        x0, x1, y0, y1 = self._rect_clip(world)
        if x0 >= x1 or y0 >= y1:
            return 0.0
        liq = int(np.count_nonzero(M.LIQUID[world.mat[y0:y1, x0:x1]]))
        return liq / (self.w * self.h)

    def update(self, game):
        if not self.alive:
            return False
        world = game.world
        if self.hp <= 0:
            self._shatter(game)
            return False
        # verify our cells occasionally even while resting (explosions!)
        # scheduled off game.tick so a restored snapshot checks on the
        # same tick as every other lockstep client
        if self.resting:
            if game.tick % 8 == 0:
                xs, ys = self.written
                if len(xs):
                    missing = int(len(xs) - np.count_nonzero(
                        world.mat[ys, xs] == self.mat))
                    burning = bool(world.burn[ys, xs].any())
                else:
                    missing, burning = 0, False
                if missing:
                    self.hp -= missing * 1.5
                    self.resting = False
                elif burning:
                    self.hp -= 4
                # did the floor vanish?
                elif not self._solid_below(world):
                    self.resting = False
            if self.resting:
                return True
        # dynamic step: lift out of the grid, move, settle back in
        self.erase(world)
        if self.hp <= 0:
            self._shatter(game)
            return False
        g = GRAVITY * game.gravity_scale * world.gravity_dir
        frac = self._liquid_frac(world)
        self.vy += g
        if frac > 0.05:                            # buoyancy + drag
            self.vy -= g * frac * (160.0 / max(40, self.dens))
            self.vx *= 0.92
            self.vy *= 0.9
        self.vy = max(-2.5, min(2.5, self.vy))
        # axis-stepped movement against the terrain
        for axis, v in (("y", self.vy), ("x", self.vx)):
            steps = int(abs(v)) + 1
            sv = v / steps
            for _ in range(steps):
                nx = self.x + (sv if axis == "x" else 0)
                ny = self.y + (sv if axis == "y" else 0)
                if self._blocked(world, nx, ny):
                    if axis == "y":
                        if abs(self.vy) > 1.6:
                            self.hp -= abs(self.vy) * 4   # crash damage
                        self.vy = 0.0
                        self.vx *= 0.5
                    else:
                        self.vx = 0.0
                    break
                self.x, self.y = nx, ny
        if abs(self.vx) < 0.05 and abs(self.vy) < 0.05 and \
                self._blocked(world, self.x, self.y + 1):
            self.resting = True
            self.vx = self.vy = 0.0
        self.write(world)
        # squash check: nothing left to write means we're inside terrain
        if len(self.written[0]) < self.w * self.h * 0.3:
            self.hp = 0
        return True

    def _blocked(self, world, x, y):
        x0 = int(x - self.w / 2)
        y0 = int(y - self.h / 2)
        x1, y1 = x0 + self.w, y0 + self.h
        if x0 < 0 or y0 < 0 or x1 > world.w or y1 > world.h:
            return True                            # off-grid reads bedrock
        item = world.mat.item
        yb = y1 - 1
        for cx in range(x0, x1):                   # top + bottom edges
            if _SOLID[item(y0, cx)] or _SOLID[item(yb, cx)]:
                return True
        xr = x1 - 1
        for cy in range(y0, y1):                   # left + right edges
            if _SOLID[item(cy, x0)] or _SOLID[item(cy, xr)]:
                return True
        return False

    def _shatter(self, game):
        """Burst into loose material — planks rain down as splinters."""
        self.alive = False
        self.erase(game.world)
        rng = game.rng
        from .particles import KIND_MAT
        for (i, j) in [(i, j) for j in range(self.h) for i in range(self.w)]:
            if rng.random() < 0.55:
                cx = self.x - self.w / 2 + i
                cy = self.y - self.h / 2 + j
                game.particles.spawn(cx, cy,
                                     (rng.random() - 0.5) * 1.6,
                                     -rng.random() * 1.2,
                                     self.mat, KIND_MAT, 200)
        game.fx_event("crack", self.x, self.y, 1.5)

    def impulse(self, ix, iy, dmg=0.0):
        self.vx += ix
        self.vy += iy
        self.hp -= dmg
        self.resting = False
