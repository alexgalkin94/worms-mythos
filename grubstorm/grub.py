"""Grubs: the tiny unhinged creatures you command."""
import math

from . import materials as M
from .constants import (GRAVITY, GRUB_RADIUS, GRUB_WALK_SPEED, GRUB_STEP_UP,
                        GRUB_SCRAMBLE, GRUB_JUMP_VY, GRUB_JUMP_VX,
                        FALL_DMG_MIN_VY, FALL_DMG_SCALE, TERMINAL_VY,
                        DROWN_DPS)

# body sample offsets for material contact (relative to centre)
_BODY = [(0, 0), (-2, 0), (2, 0), (0, -2), (0, 2), (-1, -1), (1, 1)]

# speech-bubble one-liners (cosmetic; picked without touching the sim RNG)
OUCH_QUIPS = ["OUCH!", "OOF!", "MEDIC!", "THAT HURT!", "HEY!", "OW OW OW"]
GLOAT_QUIPS = ["GOTCHA!", "HEH HEH.", "BYE BYE!", "SO LONG!", "TOO EASY."]
OOPS_QUIPS = ["OOPS.", "UH OH.", "NOT IDEAL.", "MY BAD."]


class Grub:
    def __init__(self, x, y, name, team_idx, hp):
        self.x, self.y = float(x), float(y)
        self.vx, self.vy = 0.0, 0.0
        self.name = name
        self.team = team_idx
        self.hp = float(hp)
        self.max_hp = hp
        self.facing = 1
        self.aim = 0.0               # radians, 0 = horizontal, +down
        self.alive = True
        self.poisoned = False
        self.on_ground = False
        self.flying = False          # knocked back, no control
        self.fall_peak_vy = 0.0
        self.drown_t = 0.0
        self.burn_t = 0              # visual: recently in fire
        self.shock_t = 0
        # movement tools
        self.roping = False
        self.rope_ax = self.rope_ay = 0.0
        self.rope_len = 0.0
        self.jetpack = False
        self.fuel = 100.0
        self.chute = False
        self.drilling = 0            # ticks remaining
        self.torching = 0
        self.anim = 0.0              # walk cycle phase (render)
        self.damage_taken_turn = 0.0
        self.coyote = 0              # grace ticks to jump after a ledge
        self.jump_buf = 0            # buffered jump press before landing
        self.dmg_acc = 0.0           # accumulating hit for the damage popup
        self.dmg_timer = 0
        self.recoil = 0              # render: kick-back after firing
        self.quip = ""               # speech bubble text (render only)
        self.quip_t = 0

    # ----------------------------------------------------------- collision
    def _solid_at(self, world, x, y):
        return world.is_solid(x, y)

    def collides(self, world, x, y):
        """Body collision: a narrow capsule (2 wide, ~5 tall) instead of a
        fat circle, so the worm fits its own tunnels and doesn't snag on
        every stray pixel."""
        for dx in (-1.1, 1.1):
            for dy in (-2.4, -0.8, 0.8, 2.4):
                if self._solid_at(world, x + dx, y + dy):
                    return True
        return self._solid_at(world, x, y)

    def _embedded(self, world):
        """Grazing one ragged pixel is fine; actual burial is not."""
        if self._solid_at(world, self.x, self.y):
            return True
        hits = 0
        for dx in (-1.1, 1.1):
            for dy in (-2.4, -0.8, 0.8, 2.4):
                if self._solid_at(world, self.x + dx, self.y + dy):
                    hits += 1
        return hits >= 3

    def resolve_embed(self, world):
        """Buried by sand, debris, ice or a careless girder? Pop out of the
        terrain instead of being stuck forever (classic Worms behavior)."""
        if not self._embedded(world):
            return
        for r in range(1, 10):
            for dx, dy in ((0, -r), (0, r), (-r, 0), (r, 0),
                           (-r, -r), (r, -r), (-r, r), (r, r)):
                if not self.collides(world, self.x + dx, self.y + dy):
                    self.x += dx
                    self.y += dy
                    self.vx = self.vy = 0.0
                    return
        # fully entombed: shrug the material off
        world.paint(self.x, self.y, 4, 0, mode="erase")
        self.vx = self.vy = 0.0

    def ground_below(self, world):
        """Grounded iff dropping one pixel would collide — exactly the same
        capsule the movement uses, so resting and standing never disagree."""
        return self.collides(world, self.x, self.y + 1)

    def head_in_liquid(self, world):
        return world.is_liquid(self.x, self.y - 2)

    def material_under(self, world):
        return world.get(self.x, self.y + GRUB_RADIUS + 1)

    # -------------------------------------------------------------- damage
    def hurt(self, dmg, game=None):
        if not self.alive:
            return
        self.hp -= dmg
        self.damage_taken_turn += dmg
        self.dmg_acc += dmg
        self.dmg_timer = 40
        if self.hp <= 0:
            self.hp = 0
            self.die(game)
        elif dmg >= 18 and self.quip_t < 30:
            self.say(OUCH_QUIPS, salt=int(dmg))

    def die(self, game=None):
        if not self.alive:
            return
        self.alive = False
        if game is not None:
            game.on_grub_death(self)

    def say(self, lines, salt=0, ttl=110):
        """Cosmetic speech bubble. The pick is a pure function of sim state
        so every lockstep client shows the same line for free."""
        self.quip = lines[(int(self.x * 7) + int(self.y * 3) + salt)
                          % len(lines)]
        self.quip_t = ttl

    def knockback(self, ix, iy):
        self.vx += ix
        self.vy += iy
        if abs(ix) + abs(iy) > 0.6:
            self.flying = True
            self.on_ground = False
            self.roping = False
            self.jetpack = False

    # ---------------------------------------------------------------- step
    def update(self, game, inp=None, active=False):
        if not self.alive:
            return
        world = game.world
        grav = GRAVITY * game.gravity_scale * world.gravity_dir

        # never stay fused with the terrain (falling sand, ice, debris...)
        self.resolve_embed(world)

        if self.burn_t > 0:
            self.burn_t -= 1
        if self.shock_t > 0:
            self.shock_t -= 1
        if self.recoil > 0:
            self.recoil -= 1
        if self.quip_t > 0:
            self.quip_t -= 1

        if self.roping:
            self._update_rope(game, inp, active, grav)
        elif self.jetpack and active:
            self._update_jetpack(game, inp, grav)
        else:
            self._update_walker(game, inp, active, grav)

        self._update_hazards(game)

    def _move_horizontal(self, world, dx):
        """Walk with step-up assist and slope glue. Returns True if moved."""
        moved = False
        for try_dx in (dx, dx * 0.5):
            nx = self.x + try_dx
            if not self.collides(world, nx, self.y):
                self.x = nx
                moved = True
                break
            stepped = False
            for up in range(1, GRUB_STEP_UP + 1):
                if not self.collides(world, nx, self.y - up):
                    self.x = nx
                    self.y -= up
                    moved = stepped = True
                    break
            if stepped:
                break
            # scramble: short rough walls (crater rims) are clawed up one
            # pixel per tick instead of being a hard stop
            for h in range(GRUB_STEP_UP + 1, GRUB_SCRAMBLE + 1):
                if not self.collides(world, nx, self.y - h):
                    if not self.collides(world, self.x, self.y - 1):
                        self.y -= 1
                        return True
                    break
        if moved and not self.ground_below(world):
            # slope glue: hug gentle downhills instead of going airborne
            y0 = self.y
            for _ in range(GRUB_STEP_UP + 1):
                if self.collides(world, self.x, self.y + 1):
                    break
                self.y += 1
                if self.ground_below(world):
                    break
            if not self.ground_below(world):
                self.y = y0           # real ledge: let gravity handle it
        return moved

    def _update_walker(self, game, inp, active, grav):
        world = game.world
        ground = self.ground_below(world)
        under = self.material_under(world)
        in_liquid = world.is_liquid(self.x, self.y)

        # friction / control
        slippery = under == M.ICE
        sticky = under == M.SLIME or world.get(self.x, self.y) == M.SLIME

        if ground and abs(self.vy) < 1.2:
            if self.flying and abs(self.vx) < 0.4:
                self.flying = False
            if not self.on_ground:                # landing transition
                if self.fall_peak_vy > FALL_DMG_MIN_VY and not in_liquid:
                    dmg = (self.fall_peak_vy - FALL_DMG_MIN_VY) * FALL_DMG_SCALE
                    self.hurt(dmg, game)
                    game.fx_event("thud", self.x, self.y, dmg)
                self.chute = False                # folds on touchdown only,
            self.on_ground = True                 # so it can be pre-armed
            self.fall_peak_vy = 0.0
            self.vy = 0.0
            self.vx *= 0.99 if slippery else 0.6
            self.coyote = 9                       # jump grace after ledges
        else:
            self.on_ground = False
            if self.coyote > 0:
                self.coyote -= 1
        if self.jump_buf > 0:
            self.jump_buf -= 1

        if active and inp is not None and not self.flying:
            speed = GRUB_WALK_SPEED * (0.35 if sticky else 1.0)
            if inp.left or inp.right:
                d = -1 if inp.left else 1
                if inp.aim is None and self.facing != d:
                    self.facing = d               # classic tap-to-turn
                elif self.on_ground:
                    self._move_horizontal(world, d * speed)
                    self.anim += 0.3
                elif in_liquid:                     # swim weakly
                    self.vx += d * 0.05
            # aiming: mouse sets the angle outright, keys nudge it
            if inp.aim is not None:
                a = inp.aim
                self.facing = 1 if abs(a) <= math.pi / 2 else -1
                rel = a if self.facing == 1 else math.pi - a
                rel = (rel + math.pi) % (2 * math.pi) - math.pi
                self.aim = max(-math.pi / 2, min(math.pi / 2, rel))
            if inp.aim_up:
                self.aim = max(-math.pi / 2, self.aim - 0.035)
            if inp.aim_down:
                self.aim = min(math.pi / 2, self.aim + 0.035)
            # buffered + coyote jumping: presses land even with sloppy timing
            if inp.jump:
                self.jump_buf = 8
            if self.jump_buf > 0 and (self.on_ground or self.coyote > 0):
                self.jump_buf = 0
                self.coyote = 0
                self.vy = GRUB_JUMP_VY * (0.7 if in_liquid else 1.0)
                self.vx = self.facing * GRUB_JUMP_VX
                self.on_ground = False
            if inp.backflip and (self.on_ground or self.coyote > 0):
                self.coyote = 0
                self.vy = GRUB_JUMP_VY * 1.25
                self.vx = -self.facing * GRUB_JUMP_VX * 0.6
                self.on_ground = False

        # gravity & integration
        if not self.on_ground:
            g = grav
            if in_liquid:
                g *= 0.35
                self.vx *= 0.92
                self.vy *= 0.92
            if self.chute and self.vy > 0.5:
                self.vy = 0.5
                self.vx += game.wind * 18 * GRAVITY
            self.vy = min(self.vy + g, TERMINAL_VY)
            self.fall_peak_vy = max(self.fall_peak_vy, self.vy)
            # horizontal
            nx = self.x + self.vx
            if self.collides(world, nx, self.y):
                if not self.collides(world, nx, self.y - 2):
                    self.y -= 2
                    self.x = nx
                else:
                    self.vx *= -0.35
            else:
                self.x = nx
            ny = self.y + self.vy
            if self.collides(world, self.x, ny):
                if self.vy > 0:
                    self.on_ground = True
                self.vy = 0.0
            else:
                self.y = ny
        else:
            # ground slide (ice or leftover knockback)
            if abs(self.vx) > 0.05:
                if not self._move_horizontal(world, self.vx * 0.5):
                    self.vx = 0.0
                self.vx *= 0.995 if slippery else 0.85
            if not self.ground_below(world):
                self.on_ground = False

    def _update_rope(self, game, inp, active, grav):
        world = game.world
        ax, ay = self.rope_ax, self.rope_ay
        self.vy += grav
        if active and inp is not None:
            if inp.left:
                self.vx -= 0.06
            if inp.right:
                self.vx += 0.06
            if inp.aim_up:
                self.rope_len = max(8.0, self.rope_len - 0.8)
            if inp.aim_down:
                self.rope_len = min(110.0, self.rope_len + 0.8)
            if inp.jump:
                self.roping = False
                self.vy -= 0.6
                return
        nx, ny = self.x + self.vx, self.y + self.vy
        dx, dy = nx - ax, ny - ay
        d = math.hypot(dx, dy)
        if d > self.rope_len:
            dx, dy = dx / d, dy / d
            nx, ny = ax + dx * self.rope_len, ay + dy * self.rope_len
            # remove radial velocity
            vr = self.vx * dx + self.vy * dy
            self.vx -= vr * dx
            self.vy -= vr * dy
        if self.collides(world, nx, ny):
            self.vx *= -0.4
            self.vy *= -0.4
            if self.collides(world, self.x, self.y):
                self.roping = False
        else:
            self.x, self.y = nx, ny
        self.vx *= 0.995
        self.fall_peak_vy = 0.0

    def _update_jetpack(self, game, inp, grav):
        world = game.world
        self.vy += grav * 0.6
        if inp is not None and self.fuel > 0:
            thrust = 0.0
            if inp.jump or inp.aim_up:
                self.vy -= 0.22
                thrust += 1
            if inp.left:
                self.vx -= 0.08
                self.facing = -1
                thrust += 0.5
            if inp.right:
                self.vx += 0.08
                self.facing = 1
                thrust += 0.5
            if thrust:
                self.fuel -= 0.35 * thrust
                game.fx_event("jet", self.x, self.y + 3, 1)
        if self.fuel <= 0:
            self.jetpack = False
        self.vx = max(-1.6, min(1.6, self.vx))
        self.vy = max(-1.6, min(self.vy, TERMINAL_VY))
        nx, ny = self.x + self.vx, self.y + self.vy
        if not self.collides(world, nx, self.y):
            self.x = nx
        else:
            self.vx = 0
        if not self.collides(world, self.x, ny):
            self.y = ny
        else:
            if self.vy > 0:
                self.jetpack = False
            self.vy = 0
        self.fall_peak_vy = 0.0

    def _update_hazards(self, game):
        world = game.world
        dt = 1 / 60
        # contact damage from materials touching the body
        worst = 0.0
        poisoned = False
        for dx, dy in _BODY:
            m = world.get(self.x + dx, self.y + dy)
            dps = float(M.CONTACT_DPS[m])
            if dps > worst:
                worst = dps
                if m in (M.LAVA, M.FIRE, M.NAPALM):
                    self.burn_t = 30
            if M.POISONOUS[m]:
                poisoned = True
            if m == M.MAGIC:
                game.magic_touch(self)
        if worst > 0:
            self.hurt(worst * dt, game)
        if poisoned:
            self.poisoned = True
        # drowning: head under liquid
        if self.head_in_liquid(world) and world.is_liquid(self.x, self.y - 4):
            self.drown_t += dt
            if self.drown_t > 0.8:
                self.hurt(DROWN_DPS * dt, game)
                if int(self.drown_t * 60) % 12 == 0:
                    game.fx_event("bubble", self.x, self.y - 3, 1)
        else:
            self.drown_t = 0.0
        # fell into the ocean band -> rapid doom
        if self.y > world.h - 4:
            self.hurt(400, game)

    def try_rope(self, game):
        world = game.world
        dx = math.cos(self.aim) * self.facing
        dy = math.sin(self.aim)
        if dy > -0.1:   # ropes only make sense upwards-ish
            dy = -abs(dy) - 0.3
        hit = world.raycast(self.x, self.y, dx, dy, 90)
        if hit:
            hx, hy, _ = hit
            self.roping = True
            self.rope_ax, self.rope_ay = float(hx), float(hy)
            self.rope_len = max(8.0, math.hypot(hx - self.x, hy - self.y) - 1)
            return True
        return False
