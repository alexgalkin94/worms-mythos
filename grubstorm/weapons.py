"""The arsenal. Every weapon interacts with the material simulation."""
import math

from . import materials as M
from .constants import GRAVITY, WIND_ACCEL, GRUB_RADIUS
from .grub import solid_at, liquid_at
from .particles import KIND_MAT, KIND_SPARK, KIND_FX


# ========================================================== projectiles ====
class Projectile:
    def __init__(self, x, y, vx, vy, owner=None, gravity=1.0, wind=1.0,
                 radius=1.5, bounce=None, fuse=None, life=1500,
                 explode_r=10, explode_power=40, fire=False,
                 on_explode=None, on_tick=None, glyph="rocket",
                 color=(240, 240, 240), trail=None, homing=None,
                 proximity=None, arm_delay=20, drag_in_liquid=0.9,
                 sinks=True):
        self.x, self.y, self.vx, self.vy = float(x), float(y), float(vx), float(vy)
        self.owner = owner
        self.gravity, self.wind = gravity, wind
        self.radius = radius
        self.bounce = bounce
        self.fuse = fuse
        self.life = life
        self.explode_r, self.explode_power = explode_r, explode_power
        self.fire = fire
        self.on_explode = on_explode
        self.on_tick = on_tick
        self.glyph, self.color, self.trail = glyph, color, trail
        self.homing = homing
        self.proximity = proximity
        self.arm_delay = arm_delay
        self.drag_in_liquid = drag_in_liquid
        self.sinks = sinks
        self.age = 0
        self.alive = True
        self.resting = False
        self.passive = False      # armed & settled: doesn't block turn flow

    def explode(self, game):
        if not self.alive:
            return
        self.alive = False
        if self.on_explode:
            self.on_explode(game, self.x, self.y)
        elif self.explode_r > 0:
            game.apply_explosion(self.x, self.y, self.explode_r,
                                 self.explode_power, fire=self.fire)

    def update(self, game):
        if not self.alive:
            return False
        self.age += 1
        self.life -= 1
        if self.life <= 0:
            self.explode(game)
            return False
        if self.fuse is not None:
            self.fuse -= 1
            if self.fuse <= 0:
                self.explode(game)
                return False
        if self.on_tick:
            self.on_tick(game, self)
            if not self.alive:
                return False

        w = game.world
        in_liquid = liquid_at(w, self.x, self.y)
        g = GRAVITY * self.gravity * game.gravity_scale * w.gravity_dir
        self.vy += g
        self.vx += game.wind * self.wind * WIND_ACCEL
        if in_liquid:
            self.vx *= self.drag_in_liquid
            self.vy *= self.drag_in_liquid
            if not self.sinks:
                self.vy -= g * 1.6

        if self.homing is not None and self.age > 25:
            tx, ty = self.homing
            dx, dy = tx - self.x, ty - self.y
            d = math.hypot(dx, dy) or 1.0
            sp = math.hypot(self.vx, self.vy)
            steer = 0.10
            self.vx += (dx / d * sp - self.vx) * steer
            self.vy += (dy / d * sp - self.vy) * steer

        if self.proximity is not None and self.age > self.arm_delay:
            if self.resting:
                self.passive = True
            pr = self.proximity
            for gr in game.all_grubs():
                if gr.alive and gr is not self.owner and \
                        abs(gr.x - self.x) < pr and abs(gr.y - self.y) < pr \
                        and math.hypot(gr.x - self.x, gr.y - self.y) < pr:
                    self.fuse = min(self.fuse if self.fuse is not None else 40, 40)
                    self.passive = False
                    break

        # substepped movement so fast shells never tunnel through walls
        speed = math.hypot(self.vx, self.vy)
        steps = max(1, int(speed) + 1)
        sx, sy = self.vx / steps, self.vy / steps
        # only direct-impact shells (rockets) and armed proximity charges
        # detonate on touching a grub — grenades bounce off heads, as nature
        # intended
        contact_fused = self.bounce is None or \
            (self.proximity is not None and self.age > self.arm_delay)
        if contact_fused and self.age > 3:
            # candidates can't change mid-flight: a hit exits immediately
            cr = GRUB_RADIUS + self.radius
            targets = [gr for gr in game.all_grubs() if gr.alive and
                       (gr is not self.owner or self.age > 30)]
        else:
            targets = None
        for _ in range(steps):
            nx, ny = self.x + sx, self.y + sy
            # direct grub hit
            if targets is not None:
                for gr in targets:
                    if abs(gr.x - nx) < cr and abs(gr.y - ny) < cr and \
                            math.hypot(gr.x - nx, gr.y - ny) < cr:
                        self.x, self.y = nx, ny
                        self.explode(game)
                        return False
            if solid_at(w, nx, ny):
                if self.bounce is None:
                    self.x, self.y = nx, ny
                    self.explode(game)
                    return False
                # bounce: reflect off the cheap normal
                hit_x = solid_at(w, self.x + sx, self.y)
                hit_y = solid_at(w, self.x, self.y + sy)
                if hit_x or not (hit_x or hit_y):
                    self.vx = -self.vx * self.bounce
                    sx = -sx * self.bounce
                if hit_y or not (hit_x or hit_y):
                    self.vy = -self.vy * self.bounce
                    sy = -sy * self.bounce
                self.vx *= 0.85
                game.fx_event("tic", self.x, self.y, 1)
                if abs(self.vx) + abs(self.vy) < 0.15:
                    self.resting = True
                break
            self.x, self.y = nx, ny
        if self.y < -40 or self.y > w.h + 20 or self.x < -40 or self.x > w.w + 40:
            self.alive = False
            return False
        # trails
        if self.trail and self.age % 2 == 0:
            game.spawn_trail(self)
        return True


# ============================================================= entities ====
class BlackHole:
    """Pulls cells, particles, grubs and projectiles into a singularity."""
    def __init__(self, x, y, life=260, radius=46, strength=0.55):
        self.x, self.y = x, y
        self.life = life
        self.radius = radius
        self.strength = strength
        self.alive = True

    def update(self, game):
        self.life -= 1
        if self.life <= 0:
            self.alive = False
            game.apply_explosion(self.x, self.y, 14, 55, fire=False)
            return False
        w = game.world
        rng = game.rng
        # gulp cells: sample random points in the radius, fling them inward
        for _ in range(90):
            a = rng.random() * 2 * math.pi
            d = 3 + rng.random() * self.radius
            cx = int(self.x + math.cos(a) * d)
            cy = int(self.y + math.sin(a) * d)
            m = w.get(cx, cy)
            if m in (M.EMPTY, M.BEDROCK):
                continue
            w.set_cell(cx, cy, M.EMPTY)
            if d > 7 and rng.random() < 0.8:
                spd = 1.2 + rng.random()
                game.particles.spawn(cx, cy, -math.cos(a) * spd,
                                     -math.sin(a) * spd, m, KIND_MAT, 90)
        # pull entities
        for gr in game.all_grubs():
            if not gr.alive:
                continue
            dx, dy = self.x - gr.x, self.y - gr.y
            d = math.hypot(dx, dy)
            if d < self.radius * 1.6 and d > 0.1:
                f = self.strength * (1 - d / (self.radius * 1.6)) * 0.4
                gr.knockback(dx / d * f, dy / d * f)
                if d < 5:
                    gr.hurt(2.5, game)
        for p in game.projectiles:
            dx, dy = self.x - p.x, self.y - p.y
            d = math.hypot(dx, dy)
            if d < self.radius * 1.6 and d > 0.1:
                f = self.strength * (1 - d / (self.radius * 1.6))
                p.vx += dx / d * f
                p.vy += dy / d * f
        w.wake(self.x - self.radius, self.y - self.radius,
               self.x + self.radius, self.y + self.radius)
        return True


class Stream:
    """Continuous emitters: water cannon, freeze ray, spark gun, blowtorch,
    flame from gas canister... Emits along the owner's aim while it lives."""
    def __init__(self, grub, kind, life=90):
        self.grub = grub
        self.kind = kind
        self.life = life
        self.alive = True

    def update(self, game):
        self.life -= 1
        g = self.grub
        if not self.alive or self.life <= 0 or not g.alive:
            self.alive = False
            return False
        w, rng = game.world, game.rng
        ang = g.aim if g.facing == 1 else math.pi - g.aim
        dx, dy = math.cos(ang), math.sin(ang)
        ox, oy = g.x + dx * 4, g.y + dy * 4

        if self.kind == "water":
            for _ in range(4):
                sp = 2.4 + rng.random() * 0.8
                a = ang + (rng.random() - 0.5) * 0.18
                game.particles.spawn(ox, oy, math.cos(a) * sp, math.sin(a) * sp,
                                     M.WATER, KIND_MAT, 200)
            for gr in game.all_grubs():
                if gr is g or not gr.alive:
                    continue
                ddx, ddy = gr.x - ox, gr.y - oy
                d = math.hypot(ddx, ddy)
                if d < 70:
                    aim_dot = (ddx * dx + ddy * dy) / (d or 1)
                    if aim_dot > 0.92:
                        gr.knockback(dx * 0.35, dy * 0.35 - 0.08)
        elif self.kind == "freeze":
            reach = 2
            for i in range(2, 60):
                cx, cy = ox + dx * i, oy + dy * i
                if solid_at(w, cx, cy):
                    break
                reach = i
                w.temp[int(cy), int(cx)] = -250.0
                w.wake(cx - 1, cy - 1, cx + 1, cy + 1)
                if rng.random() < 0.25:
                    game.particles.spawn(cx, cy, dx * 0.5, dy * 0.5,
                                         M.SNOW, KIND_FX, 25)
            game.add_tracer(ox, oy, ox + dx * reach, oy + dy * reach, 2,
                            (170, 220, 255))
            # chill only what the beam actually touches
            for gr in game.all_grubs():
                if gr is g or not gr.alive:
                    continue
                t = (gr.x - ox) * dx + (gr.y - oy) * dy
                if 0 < t < reach + 4:
                    px, py = ox + dx * t, oy + dy * t
                    if math.hypot(gr.x - px, gr.y - py) < 5:
                        gr.hurt(0.3, game)
        elif self.kind == "spark":
            sp = 2.8
            a = ang + (rng.random() - 0.5) * 0.1
            game.particles.spawn(ox, oy, math.cos(a) * sp, math.sin(a) * sp,
                                 0, KIND_SPARK, 120)
            game.shock_check(ox + dx * 10, oy + dy * 10, 12, 0.35)
        elif self.kind == "torch":
            # melt a tunnel in front of the grub and shuffle forward
            tx, ty = g.x + dx * 4, g.y + dy * 2
            w.paint(tx, ty, 4, M.EMPTY, mode="erase")
            if w.in_bounds(int(tx), int(ty)):
                w.temp[int(ty), int(tx)] += 30
            if self.life % 3 == 0:
                g._move_horizontal(w, g.facing * 0.5)
            if self.life % 2 == 0:
                game.particles.spawn(tx, ty, dx * 0.4,
                                     dy * 0.4 - 0.2, M.FIRE, KIND_FX, 9)
            game.fx_event("torch", tx, ty, 1)
        elif self.kind == "drill":
            w.paint(g.x, g.y + 4, 4, M.EMPTY, mode="erase")
            # braced against the shaft walls: controlled descent, no
            # freefall, no fall damage while the drill runs
            g.vy = min(g.vy, 0.6)
            g.fall_peak_vy = 0.0
            if self.life % 2 == 0:
                game.particles.spawn(g.x + (rng.random() - .5) * 4, g.y + 4,
                                     (rng.random() - .5) * 0.8, -0.5,
                                     M.SMOKE, KIND_FX, 12)
            game.fx_event("torch", g.x, g.y + 4, 1)
        return True


# ============================================================== weapons ====
class WeaponSpec:
    def __init__(self, key, name, icon, ammo, fire_fn, charge=False,
                 target="aim", ends_turn=True, shots=1, desc="", super_=False,
                 category="boom"):
        self.key, self.name, self.icon = key, name, icon
        self.ammo = ammo            # default ammo, -1 = infinite
        self.fire_fn = fire_fn
        self.charge = charge
        self.target = target        # aim | click | drop | self
        self.ends_turn = ends_turn
        self.shots = shots
        self.desc = desc
        self.super_ = super_
        self.category = category


def _vel(angle, power, lo=1.2, hi=4.6):
    s = lo + power * (hi - lo)
    return math.cos(angle) * s, math.sin(angle) * s


def _muzzle(grub, angle, dist=5.0):
    return grub.x + math.cos(angle) * dist, grub.y + math.sin(angle) * dist - 1


# --- classic ---------------------------------------------------------------
def fire_bazooka(game, grub, angle, power, click):
    x, y = _muzzle(grub, angle)
    vx, vy = _vel(angle, power)
    game.add_projectile(Projectile(x, y, vx, vy, owner=grub, wind=1.0,
                                   explode_r=11, explode_power=46,
                                   glyph="rocket", color=(230, 80, 60),
                                   trail="smoke"))

def fire_grenade(game, grub, angle, power, click):
    x, y = _muzzle(grub, angle)
    vx, vy = _vel(angle, power)
    game.add_projectile(Projectile(x, y, vx, vy, owner=grub, wind=0,
                                   bounce=0.45, fuse=180, explode_r=11,
                                   explode_power=46, glyph="ball",
                                   color=(90, 200, 90)))

def fire_cluster(game, grub, angle, power, click):
    x, y = _muzzle(grub, angle)
    vx, vy = _vel(angle, power)
    def split(g, px, py):
        g.apply_explosion(px, py, 7, 30)
        for i in range(5):
            a = -math.pi / 2 + (i - 2) * 0.35 + (g.rng.random() - .5) * .2
            g.add_projectile(Projectile(px, py, math.cos(a) * 1.4,
                                        math.sin(a) * 1.6 - 0.6, owner=grub,
                                        bounce=0.4, fuse=70, explode_r=7,
                                        explode_power=30, glyph="ball",
                                        color=(240, 220, 80), radius=1.0))
    game.add_projectile(Projectile(x, y, vx, vy, owner=grub, wind=0,
                                   bounce=0.35, fuse=150, on_explode=split,
                                   glyph="ball", color=(240, 220, 80)))

def fire_shotgun(game, grub, angle, power, click):
    w = game.world
    x, y = _muzzle(grub, angle, 4)
    hit = w.raycast(x, y, math.cos(angle), math.sin(angle), 200)
    hx, hy = (hit[0], hit[1]) if hit else (x + math.cos(angle) * 200,
                                           y + math.sin(angle) * 200)
    for gr in game.all_grubs():
        if gr is grub or not gr.alive:
            continue
        # point-line distance along the shot
        ddx, ddy = gr.x - x, gr.y - y
        t = max(0, ddx * math.cos(angle) + ddy * math.sin(angle))
        px, py = x + math.cos(angle) * t, y + math.sin(angle) * t
        if math.hypot(gr.x - px, gr.y - py) < 4 and t < math.hypot(hx - x, hy - y) + 4:
            gr.hurt(22, game)
            gr.knockback(math.cos(angle) * 0.9, math.sin(angle) * 0.9 - 0.2)
    game.apply_explosion(hx, hy, 4, 22)
    game.add_tracer(x, y, hx, hy, 5, (255, 240, 180))
    game.fx_event("shot", x, y, 2)

def fire_hammer(game, grub, angle, power, click):
    for gr in game.all_grubs():
        if gr is grub or not gr.alive:
            continue
        d = math.hypot(gr.x - grub.x, gr.y - grub.y)
        if d < 9 and (gr.x - grub.x) * grub.facing >= -2:
            gr.hurt(18, game)
            gr.knockback(grub.facing * 1.8, -1.1)
    f = grub.facing
    for ang in (-0.9, -0.3, 0.3):
        game.add_tracer(grub.x, grub.y,
                        grub.x + f * 9 * math.cos(ang),
                        grub.y + 9 * math.sin(ang), 6, (255, 220, 150))
    game.fx_event("swing", grub.x, grub.y, 2)

def make_mine(x, y, vx=0.0, vy=0.0, owner=None):
    return Projectile(x, y, vx, vy, owner=owner, bounce=0.3, fuse=None,
                      life=10 ** 9, proximity=10, arm_delay=120,
                      explode_r=10, explode_power=42, glyph="mine",
                      color=(220, 60, 60))


def fire_mine(game, grub, angle, power, click):
    game.add_projectile(make_mine(grub.x + grub.facing * 4, grub.y - 2,
                                  grub.facing * 0.5, -0.4, owner=grub))

def fire_dynamite(game, grub, angle, power, click):
    game.add_projectile(Projectile(grub.x + grub.facing * 3, grub.y - 1,
                                   grub.facing * 0.3, -0.2, owner=grub,
                                   bounce=0.1, fuse=300, explode_r=18,
                                   explode_power=72, fire=True,
                                   glyph="tnt", color=(230, 50, 50)))

def fire_airstrike(game, grub, angle, power, click):
    cx = click[0] if click else grub.x
    side = 1 if (click and click[0] > game.world.w / 2) else -1
    for i in range(5):
        x = cx + (i - 2) * 9 - side * 30
        game.add_projectile(Projectile(x, -10 - i * 6, side * 1.0, 1.8,
                                       owner=None, wind=0, explode_r=9,
                                       explode_power=38, glyph="rocket",
                                       color=(230, 120, 60), trail="smoke"))

def fire_napalmstrike(game, grub, angle, power, click):
    cx = click[0] if click else grub.x
    for i in range(5):
        x = cx + (i - 2) * 9
        def drop(g, px, py):
            g.apply_explosion(px, py, 6, 24, fire=True)
            g.world.paint(px, py, 5, M.NAPALM, mode="fill")
            if g.world.in_bounds(int(px), int(py)):
                g.world.temp[int(py), int(px)] = 500
        game.add_projectile(Projectile(x, -10 - i * 6, 0, 1.6, wind=0,
                                       explode_r=0, on_explode=drop,
                                       glyph="ball", color=(255, 140, 40),
                                       trail="fire"))

def fire_homing(game, grub, angle, power, click):
    x, y = _muzzle(grub, angle)
    vx, vy = _vel(angle, max(0.4, power))
    tgt = click if click else (grub.x + grub.facing * 100, grub.y)
    game.add_projectile(Projectile(x, y, vx, vy, owner=grub, wind=0,
                                   homing=tgt, explode_r=10, explode_power=42,
                                   glyph="rocket", color=(255, 80, 160),
                                   trail="smoke"))

def fire_holy_melon(game, grub, angle, power, click):
    x, y = _muzzle(grub, angle)
    vx, vy = _vel(angle, power)
    def hallelujah(g, px, py):
        g.apply_explosion(px, py, 26, 110, fire=True)
        g.fx_event("choir", px, py, 3)
    game.add_projectile(Projectile(x, y, vx, vy, owner=grub, bounce=0.5,
                                   fuse=240, on_explode=hallelujah,
                                   glyph="melon", color=(120, 220, 80)))

# --- simulation weapons ------------------------------------------------
def _flask(mat, amount, r_blast=5, pwr=18, label="ball", col=(120, 230, 60)):
    def fire(game, grub, angle, power, click):
        x, y = _muzzle(grub, angle)
        vx, vy = _vel(angle, power)
        def smash(g, px, py):
            g.apply_explosion(px, py, r_blast, pwr, silentish=True)
            g.world.paint(px, py, amount, mat, mode="fill")
            g.world.wake(px - amount, py - amount, px + amount, py + amount)
            g.fx_event("splat", px, py, 2)
        game.add_projectile(Projectile(x, y, vx, vy, owner=grub,
                                       on_explode=smash, explode_r=0,
                                       glyph=label, color=col))
    return fire

fire_acid_flask = _flask(M.ACID, 7, col=(120, 230, 60))
fire_oil_flask = _flask(M.OIL, 8, col=(70, 60, 50))
fire_sludge_flask = _flask(M.SLUDGE, 7, col=(110, 150, 50))
fire_slime_flask = _flask(M.SLIME, 8, col=(230, 120, 190))

def fire_lava_bomb(game, grub, angle, power, click):
    x, y = _muzzle(grub, angle)
    vx, vy = _vel(angle, power)
    def erupt(g, px, py):
        g.apply_explosion(px, py, 9, 36, fire=True)
        g.world.paint(px, py, 6, M.LAVA, mode="fill")
        for _ in range(10):
            a = -math.pi / 2 + (g.rng.random() - .5) * 1.6
            sp = 1 + g.rng.random() * 1.6
            g.particles.spawn(px, py, math.cos(a) * sp, math.sin(a) * sp,
                              M.LAVA, KIND_MAT, 200)
    game.add_projectile(Projectile(x, y, vx, vy, owner=grub,
                                   on_explode=erupt, explode_r=0,
                                   glyph="ball", color=(255, 120, 30),
                                   trail="fire"))

def fire_powder_bomb(game, grub, angle, power, click):
    x, y = _muzzle(grub, angle)
    vx, vy = _vel(angle, power)
    def dust(g, px, py):
        g.world.paint(px, py, 9, M.EXPOWDER, mode="fill", noise=0.3)
        g.world.set_cell(px, py, M.FIRE, life=50)
        g.world.wake(px - 12, py - 12, px + 12, py + 12)
    game.add_projectile(Projectile(x, y, vx, vy, owner=grub, bounce=0.3,
                                   fuse=160, on_explode=dust, glyph="ball",
                                   color=(220, 90, 90)))

def fire_gas_canister(game, grub, angle, power, click):
    x, y = _muzzle(grub, angle)
    vx, vy = _vel(angle, power)
    def leak(g, p):
        if p.age % 3 == 0 and p.age > 20:
            g.world.paint(p.x, p.y - 1, 2, M.GAS, mode="fill")
            p.life -= 4
    game.add_projectile(Projectile(x, y, vx, vy, owner=grub, bounce=0.4,
                                   life=400, fuse=None, on_tick=leak,
                                   explode_r=5, explode_power=20,
                                   glyph="tnt", color=(150, 160, 90)))

def fire_steam_bomb(game, grub, angle, power, click):
    x, y = _muzzle(grub, angle)
    vx, vy = _vel(angle, power)
    def hiss(g, px, py):
        g.world.paint(px, py, 12, M.STEAM, mode="fill", life=200)
        g.apply_explosion(px, py, 4, 10, silentish=True)
        for gr in g.all_grubs():
            d = math.hypot(gr.x - px, gr.y - py)
            if d < 26 and gr.alive:
                f = (1 - d / 26) * 1.6
                gr.knockback((gr.x - px) / (d or 1) * f,
                             (gr.y - py) / (d or 1) * f - 0.3)
    game.add_projectile(Projectile(x, y, vx, vy, owner=grub, bounce=0.4,
                                   fuse=140, on_explode=hiss, glyph="ball",
                                   color=(200, 210, 220)))

def fire_blackhole(game, grub, angle, power, click):
    x, y = _muzzle(grub, angle)
    vx, vy = _vel(angle, power, 1.0, 3.6)
    def collapse(g, px, py):
        g.add_entity(BlackHole(px, py))
        g.fx_event("vortex", px, py, 3)
    game.add_projectile(Projectile(x, y, vx, vy, owner=grub, bounce=0.5,
                                   fuse=120, on_explode=collapse,
                                   glyph="hole", color=(160, 80, 255)))

def fire_lightning(game, grub, angle, power, click):
    cx = click[0] if click else grub.x + grub.facing * 60
    w = game.world
    hit = w.raycast(cx, 1, 0, 1, w.h, hit_liquid=True)
    hy = hit[1] if hit else w.h - 10
    game.fx_event("lightning", cx, hy, 4)
    game.apply_explosion(cx, hy, 6, 30)
    w.temp[max(0, int(hy) - 3):int(hy) + 4,
           max(0, int(cx) - 3):int(cx) + 4] += 400
    for _ in range(16):
        game.particles.spawn(cx + game.rng.random() * 6 - 3, hy - 2,
                             (game.rng.random() - .5) * 3,
                             -game.rng.random() * 1.5, 0, KIND_SPARK, 150)
    game.shock_check(cx, hy, 60, 30)

def fire_freeze(game, grub, angle, power, click):
    game.add_entity(Stream(grub, "freeze", life=100))

def fire_watercannon(game, grub, angle, power, click):
    game.add_entity(Stream(grub, "water", life=90))

def fire_sparkgun(game, grub, angle, power, click):
    game.add_entity(Stream(grub, "spark", life=70))

def fire_blowtorch(game, grub, angle, power, click):
    game.add_entity(Stream(grub, "torch", life=240))

def fire_drill(game, grub, angle, power, click):
    game.add_entity(Stream(grub, "drill", life=200))

TRANSMUTE_MAP = {
    M.STONE: M.SAND, M.DIRT: M.SAND, M.SAND: M.GLASS, M.WATER: M.ACID,
    M.METAL: M.GRAVEL, M.WOOD: M.SLIME, M.ICE: M.WATER, M.OIL: M.NITRO,
    M.GRASS: M.FIRE, M.SNOW: M.WATER, M.ACID: M.WATER, M.GRAVEL: M.SAND,
    M.SLUDGE: M.MAGIC, M.GLASS: M.CRYSTAL,
}

def fire_transmuter(game, grub, angle, power, click):
    if not click:
        return False
    cx, cy = click
    if math.hypot(cx - grub.x, cy - grub.y) > 110:
        game.toast(cx, cy, "OUT OF RANGE")
        game.fx_event("tic", grub.x, grub.y, 1)
        return False
    w = game.world
    d = w._disk(cx, cy, 14)
    if d is None:
        return
    sy, sx, d2 = d
    sub = w.mat[sy, sx]
    mask = d2 <= 14 * 14
    for src, dst in TRANSMUTE_MAP.items():
        sel = mask & (sub == src) & (game.npy_rng(sub.shape) < 0.9)
        sub[sel] = dst
    w.wake(cx - 16, cy - 16, cx + 16, cy + 16)
    game.fx_event("magic", cx, cy, 3)

def fire_liquefier(game, grub, angle, power, click):
    if not click:
        return False
    cx, cy = click
    if math.hypot(cx - grub.x, cy - grub.y) > 110:
        game.toast(cx, cy, "OUT OF RANGE")
        game.fx_event("tic", grub.x, grub.y, 1)
        return False
    w = game.world
    d = w._disk(cx, cy, 16)
    if d is None:
        return
    sy, sx, d2 = d
    sub = w.mat[sy, sx]
    mask = (d2 <= 16 * 16) & (M.PHASE[sub] == M.P_STATIC) & (sub != M.BEDROCK)
    remap = {M.STONE: M.GRAVEL, M.DIRT: M.SAND, M.METAL: M.GRAVEL,
             M.WOOD: M.SAND, M.ICE: M.WATER, M.GLASS: M.SAND,
             M.GRASS: M.SAND, M.CRYSTAL: M.SAND}
    for src, dst in remap.items():
        sub[mask & (sub == src)] = dst
    w.wake(cx - 18, cy - 18, cx + 18, cy + 18)
    game.fx_event("rumble", cx, cy, 3)

def fire_crystal_bomb(game, grub, angle, power, click):
    x, y = _muzzle(grub, angle)
    vx, vy = _vel(angle, power)
    def grow(g, px, py):
        for _ in range(8):
            a = g.rng.random() * 2 * math.pi
            d = g.rng.random() * 8
            g.world.paint(px + math.cos(a) * d, py + math.sin(a) * d,
                          2 + g.rng.random() * 2, M.CRYSTAL, mode="fill")
        g.fx_event("chime", px, py, 2)
    game.add_projectile(Projectile(x, y, vx, vy, owner=grub,
                                   on_explode=grow, explode_r=0,
                                   glyph="ball", color=(110, 190, 250)))

def fire_gravity_flip(game, grub, angle, power, click):
    game.flip_gravity(60 * 6)
    game.fx_event("flip", grub.x, grub.y, 4)

# --- movement / utility ------------------------------------------------
def fire_rope(game, grub, angle, power, click):
    grub.try_rope(game)

def fire_jetpack(game, grub, angle, power, click):
    grub.jetpack = not grub.jetpack
    if grub.jetpack:
        grub.fuel = 100.0

def fire_chute(game, grub, angle, power, click):
    grub.chute = not grub.chute

def fire_teleport(game, grub, angle, power, click):
    if not click:
        return False
    cx, cy = click
    w = game.world
    if grub.collides(w, cx, cy):
        game.toast(cx, cy, "BLOCKED")
        game.fx_event("tic", grub.x, grub.y, 1)
        return False              # blocked: keep your ammo and your turn
    game.fx_event("teleport", grub.x, grub.y, 2)
    grub.x, grub.y = float(cx), float(cy)
    grub.vx = grub.vy = 0.0
    game.fx_event("teleport", cx, cy, 2)

def fire_girder(game, grub, angle, power, click):
    if not click:
        return
    cx, cy = click
    # classic rule: girders only reach so far from your grub
    d = math.hypot(cx - grub.x, cy - grub.y)
    if d > 85:
        f = 85 / d
        cx = grub.x + (cx - grub.x) * f
        cy = grub.y + (cy - grub.y) * f
    w = game.world
    horizontal = abs(math.cos(angle)) > 0.5
    if horizontal:
        sub = w.mat[int(cy):int(cy) + 3, max(0, int(cx) - 14):int(cx) + 14]
    else:
        sub = w.mat[max(0, int(cy) - 14):int(cy) + 14, int(cx):int(cx) + 3]
    sub[M.PHASE[sub] <= M.P_GAS] = M.STONE      # fill air only, entomb no one
    w.wake(cx - 16, cy - 16, cx + 16, cy + 16)
    game.fx_event("clank", cx, cy, 2)


WEAPONS = [
    WeaponSpec("bazooka", "Bazooka", "🚀", -1, fire_bazooka, charge=True,
               desc="Classic rocket. Rides the wind.", category="boom"),
    WeaponSpec("grenade", "Grenade", "💣", -1, fire_grenade, charge=True,
               desc="3 second fuse. Bouncy.", category="boom"),
    WeaponSpec("cluster", "Cluster Bomb", "🍇", 3, fire_cluster, charge=True,
               desc="Splits into five angry children.", category="boom"),
    WeaponSpec("shotgun", "Shotgun", "🔫", 4, fire_shotgun, shots=2,
               desc="Two precise blasts per turn.", category="boom"),
    WeaponSpec("hammer", "Slap Hammer", "🔨", -1, fire_hammer,
               desc="Short range. Deeply disrespectful.", category="boom"),
    WeaponSpec("mine", "Mine", "🟠", 2, fire_mine,
               desc="Arms after a moment. Sneaky.", category="boom"),
    WeaponSpec("dynamite", "Dynamite", "🧨", 1, fire_dynamite,
               desc="Drop and RUN.", category="boom"),
    WeaponSpec("airstrike", "Airstrike", "✈️", 1, fire_airstrike,
               target="click", desc="Click a spot. Five rockets.", category="boom"),
    WeaponSpec("homing", "Homing Missile", "🎯", 2, fire_homing, charge=True,
               target="click", desc="Click a target, then fire.", category="boom"),
    WeaponSpec("melon", "Holy Melon", "🍉", 0, fire_holy_melon, charge=True,
               super_=True, desc="HALLELUJAH.", category="super"),
    # sim weapons
    WeaponSpec("acid", "Acid Flask", "🧪", 2, fire_acid_flask, charge=True,
               desc="Dissolves terrain and pride.", category="chem"),
    WeaponSpec("lavabomb", "Lava Bomb", "🌋", 1, fire_lava_bomb, charge=True,
               desc="Opens a personal volcano.", category="chem"),
    WeaponSpec("oil", "Oil Flask", "🛢️", 2, fire_oil_flask, charge=True,
               desc="Flammable. Combos beautifully.", category="chem"),
    WeaponSpec("napalm", "Napalm Strike", "🔥", 1, fire_napalmstrike,
               target="click", super_=True,
               desc="Sticky fire from above.", category="super"),
    WeaponSpec("gas", "Gas Canister", "💨", 2, fire_gas_canister, charge=True,
               desc="Leaks flammable gas. Add spark.", category="chem"),
    WeaponSpec("steam", "Steam Bomb", "♨️", 2, fire_steam_bomb, charge=True,
               desc="Scalding pressure blast.", category="chem"),
    WeaponSpec("powder", "Powder Bomb", "🎆", 2, fire_powder_bomb, charge=True,
               desc="Explosive dust, then a spark.", category="chem"),
    WeaponSpec("sludge", "Sludge Flask", "🤢", 2, fire_sludge_flask, charge=True,
               desc="Toxic. Poisons on contact.", category="chem"),
    WeaponSpec("slime", "Slime Trap", "🍬", 2, fire_slime_flask, charge=True,
               desc="Sticky area denial.", category="chem"),
    WeaponSpec("crystal", "Crystal Bomb", "💎", 1, fire_crystal_bomb, charge=True,
               desc="Grows a crystal barricade.", category="chem"),
    # energy
    WeaponSpec("water", "Water Cannon", "🌊", 2, fire_watercannon,
               desc="Push, flood, extinguish.", category="energy"),
    WeaponSpec("freeze", "Freeze Ray", "❄️", 2, fire_freeze,
               desc="Turns water into walkable ice.", category="energy"),
    WeaponSpec("spark", "Spark Gun", "⚡", 2, fire_sparkgun,
               desc="Electricity. Loves water & metal.", category="energy"),
    WeaponSpec("lightning", "Lightning Rod", "🌩️", 1, fire_lightning,
               target="click", desc="Smite a spot from the sky.", category="energy"),
    WeaponSpec("blackhole", "Black Hole", "🕳️", 0, fire_blackhole, charge=True,
               super_=True, desc="Eats... everything.", category="super"),
    WeaponSpec("transmute", "Transmuter", "🪄", 1, fire_transmuter,
               target="click", desc="Rewrites matter itself.", category="energy"),
    WeaponSpec("liquefy", "Liquefier", "🫠", 1, fire_liquefier,
               target="click", desc="Solid ground? Not anymore.", category="energy"),
    WeaponSpec("gravflip", "Gravity Flip", "🙃", 0, fire_gravity_flip,
               super_=True, desc="Six seconds of upside-down.", category="super"),
    # movement
    WeaponSpec("rope", "Ninja Rope", "🪢", -1, fire_rope, ends_turn=False,
               desc="Swing. Master it.", category="move"),
    WeaponSpec("jetpack", "Jetpack", "🎒", 1, fire_jetpack, ends_turn=False,
               desc="Limited fuel. Unlimited style.", category="move"),
    WeaponSpec("chute", "Parachute", "🪂", -1, fire_chute, ends_turn=False,
               desc="Float, drift with the wind.", category="move"),
    WeaponSpec("teleport", "Teleporter", "🌀", 1, fire_teleport, target="click",
               desc="Click to relocate.", category="move"),
    WeaponSpec("girder", "Girder", "🌉", 2, fire_girder, target="click",
               desc="Instant bridge.", category="move"),
    WeaponSpec("torch", "Blowtorch", "🔦", 1, fire_blowtorch, ends_turn=True,
               desc="Hold FIRE to tunnel sideways.", category="move"),
    WeaponSpec("drill", "Drill", "⛏️", 1, fire_drill, ends_turn=True,
               desc="Hold FIRE to dig down. Release to stop.", category="move"),
]

W_BY_KEY = {w.key: i for i, w in enumerate(WEAPONS)}

# casting range per weapon key (drawn as a ring when selected)
CAST_RANGE = {"transmute": 110, "liquefy": 110, "girder": 85}


def default_ammo(settings=None):
    ammo = {}
    for i, w in enumerate(WEAPONS):
        ammo[i] = w.ammo
        if settings:
            if settings.get("all_super") and w.super_:
                ammo[i] = 3
            if settings.get("one_shot"):
                pass
    return ammo
