"""Rigid props: crates, planks and stone blocks living inside the sand sim.

A body is an axis-aligned rect of real material cells. Each tick it erases
its cells, runs simple physics (gravity, buoyancy, terrain collision,
explosion impulses), and writes its cells back — so liquids pool against
it, worms stand on it, fire eats it and explosions shove it around. When
its hit points run out it bursts into loose material.

Deterministic: only game/world RNG, fixed update order, serialized into
net snapshots.
"""
from . import materials as M
from .constants import GRAVITY

KINDS = {
    # w, h, material, hp, density (vs water 100)
    "crate": (7, 7, M.WOOD, 60, 55),
    "plank": (16, 4, M.WOOD, 45, 50),
    "block": (8, 6, M.STONE, 140, 230),
    "beam":  (14, 3, M.METAL, 160, 250),
}


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
        self.written = []                         # cells we own right now
        self.check_t = 0

    # ------------------------------------------------------------ cells
    def _rect_cells(self):
        x0 = int(self.x - self.w / 2)
        y0 = int(self.y - self.h / 2)
        return [(x0 + i, y0 + j) for j in range(self.h)
                for i in range(self.w)]

    def erase(self, world):
        changed = 0
        for (cx, cy) in self.written:
            if world.in_bounds(cx, cy) and world.mat[cy, cx] == self.mat:
                if world.burn[cy, cx]:
                    changed += 1                  # we are on fire
                world.mat[cy, cx] = M.EMPTY
                world.burn[cy, cx] = 0
            else:
                changed += 1                      # cell melted/blasted away
        if self.written:
            xs = [c[0] for c in self.written]
            ys = [c[1] for c in self.written]
            world.wake(min(xs) - 1, min(ys) - 1, max(xs) + 1, max(ys) + 1)
        self.written = []
        if changed:
            self.hp -= changed * 1.5
            self.resting = False

    def write(self, world):
        self.written = []
        for (cx, cy) in self._rect_cells():
            if not world.in_bounds(cx, cy):
                continue
            m = world.mat[cy, cx]
            if M.PHASE[m] <= M.P_LIQUID:          # displace fluids & air
                world.mat[cy, cx] = self.mat
                world.shade[cy, cx] = world.tex[cy, cx]
                self.written.append((cx, cy))
        xs = [c[0] for c in self.written] or [int(self.x)]
        ys = [c[1] for c in self.written] or [int(self.y)]
        world.wake(min(xs) - 1, min(ys) - 1, max(xs) + 1, max(ys) + 1)

    # ---------------------------------------------------------- physics
    def _solid_below(self, world):
        y = int(self.y + self.h / 2)
        x0 = int(self.x - self.w / 2)
        hits = 0
        for i in range(self.w):
            m = world.get(x0 + i, y)
            if M.SOLID[m]:
                hits += 1
        return hits >= max(2, self.w // 4)

    def _liquid_frac(self, world):
        cells = self._rect_cells()
        if not cells:
            return 0.0
        liq = sum(1 for (cx, cy) in cells if world.is_liquid(cx, cy))
        return liq / len(cells)

    def update(self, game):
        if not self.alive:
            return False
        world = game.world
        if self.hp <= 0:
            self._shatter(game)
            return False
        # verify our cells occasionally even while resting (explosions!)
        if self.resting:
            self.check_t += 1
            if self.check_t % 8 == 0:
                missing = sum(1 for (cx, cy) in self.written
                              if not world.in_bounds(cx, cy)
                              or world.mat[cy, cx] != self.mat)
                burning = any(world.burn[cy, cx] for (cx, cy) in self.written
                              if world.in_bounds(cx, cy))
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
        if len(self.written) < self.w * self.h * 0.3:
            self.hp = 0
        return True

    def _blocked(self, world, x, y):
        x0 = int(x - self.w / 2)
        y0 = int(y - self.h / 2)
        for i in range(self.w):
            for j in (0, self.h - 1):
                m = world.get(x0 + i, y0 + j)
                if M.SOLID[m]:
                    return True
        for j in range(self.h):
            for i in (0, self.w - 1):
                m = world.get(x0 + i, y0 + j)
                if M.SOLID[m]:
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
