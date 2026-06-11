"""Match orchestration: turns, teams, wind, crates, sudden death, victory.

Game.step(inp) advances exactly one deterministic tick. The same input
stream + seed reproduces the same match on every machine (lockstep net play).
"""
import math
import random

import numpy as np

from . import materials as M
from .constants import (GRUB_HP, TURN_SECONDS, RETREAT_SECONDS,
                        SETTLE_TIMEOUT, SUDDEN_DEATH_AFTER, CRATE_CHANCE,
                        GRUB_NAMES, SLUDGE_POISON, GRUB_RADIUS)
from .world import World
from .mapgen import generate
from .grub import Grub
from .particles import Particles, KIND_MAT, KIND_SPARK, KIND_FX
from .weapons import WEAPONS, W_BY_KEY, Projectile, default_ammo


class InputFrame:
    """One tick of player intent. Serializable for net lockstep."""
    __slots__ = ("left", "right", "aim_up", "aim_down", "jump", "backflip",
                 "fire", "weapon", "click", "aim")

    AIM_NONE = 9999                # sentinel: keyboard aiming this tick

    def __init__(self):
        self.left = self.right = self.aim_up = self.aim_down = False
        self.jump = self.backflip = False
        self.fire = False
        self.weapon = -1
        self.click = None          # (x, y) world cells
        self.aim = None            # absolute world angle (mouse aiming)

    def encode(self):
        bits = (self.left | self.right << 1 | self.aim_up << 2 |
                self.aim_down << 3 | self.jump << 4 | self.backflip << 5 |
                self.fire << 6)
        c = [-1, -1] if self.click is None else [int(self.click[0]), int(self.click[1])]
        a = self.AIM_NONE if self.aim is None else int(round(self.aim * 1000))
        return [bits, self.weapon, c[0], c[1], a]

    @classmethod
    def decode(cls, data):
        f = cls()
        bits, f.weapon, cx, cy, a = data
        f.left = bool(bits & 1); f.right = bool(bits & 2)
        f.aim_up = bool(bits & 4); f.aim_down = bool(bits & 8)
        f.jump = bool(bits & 16); f.backflip = bool(bits & 32)
        f.fire = bool(bits & 64)
        f.click = None if cx < 0 else (cx, cy)
        f.aim = None if a == cls.AIM_NONE else a / 1000.0
        return f


class Team:
    def __init__(self, name, color_idx, control="local", n_grubs=4):
        self.name = name
        self.color_idx = color_idx
        self.control = control          # "local" | "bot:<persona>" | "net:<n>"
        self.n_grubs = n_grubs
        self.grubs: list[Grub] = []
        self.ammo: dict[int, int] = {}
        self.next_grub = 0
        self.damage_dealt = 0.0
        self.kills = 0
        self.shots = 0
        self.max_hit = 0.0

    def alive_grubs(self):
        return [g for g in self.grubs if g.alive]

    def total_hp(self):
        return sum(g.hp for g in self.alive_grubs())

    def pick_next(self):
        alive = self.alive_grubs()
        if not alive:
            return None
        self.next_grub %= len(self.grubs)
        for _ in range(len(self.grubs)):
            g = self.grubs[self.next_grub]
            self.next_grub = (self.next_grub + 1) % len(self.grubs)
            if g.alive:
                return g
        return None


class Crate:
    def __init__(self, x, kind):
        self.x, self.y = float(x), -6.0
        self.vy = 0.0
        self.kind = kind               # "health" | "weapon" | "trap"
        self.landed = False
        self.alive = True

    def update(self, game):
        w = game.world
        if not self.landed:
            self.vy = min(self.vy + 0.04, 0.55)     # parachute
            self.y += self.vy
            if w.is_solid(self.x, self.y + 3):
                self.landed = True
        else:
            if not w.is_solid(self.x, self.y + 3):
                self.y += 1.2                        # ground was destroyed
            m = w.get(self.x, self.y)
            if M.LIQUID[m] or m == M.FIRE:
                game.apply_explosion(self.x, self.y, 8, 30, fire=True)
                self.alive = False
                return False
        for gr in game.all_grubs():
            if gr.alive and math.hypot(gr.x - self.x, gr.y - self.y) < 5:
                game.collect_crate(self, gr)
                return False
        if self.y > w.h:
            self.alive = False
        return self.alive


class Game:
    PH_START, PH_ACTIVE, PH_RETREAT, PH_RESOLVE, PH_TURNEND, PH_OVER = range(6)

    def __init__(self, settings: dict):
        self.settings = settings
        seed = settings.get("seed", 1234)
        self.rng = random.Random(seed)
        biome = settings.get("biome", "island")
        self.spec = generate(biome, seed)
        self.world: World = self.spec.world
        self.gravity_scale = self.spec.gravity_scale * \
            (0.45 if settings.get("low_gravity") else 1.0)
        self.particles = Particles()
        self.projectiles: list[Projectile] = []
        self.entities: list = []
        self.crates: list[Crate] = []
        self.fx: list[tuple] = []             # (kind, x, y, mag) for AV layer
        self.tracers: list[list] = []         # [x0, y0, x1, y1, ttl, color]
        self.toasts: list[list] = []          # [x, y, text, ttl]
        self.floaters: list[list] = []        # [x, y, amount, ttl, team]
        self.ghosts: list[list] = []          # [x, y, ttl] souls going up
        self.headstones: list[tuple] = []

        # teams
        self.teams: list[Team] = []
        names = list(GRUB_NAMES)
        self.rng.shuffle(names)
        spawn_iter = list(self.spec.spawns)
        self.rng.shuffle(spawn_iter)
        ni = 0
        sky_weapons = [i for i, w in enumerate(WEAPONS)
                       if w.key in ("airstrike", "napalm", "lightning")]
        for tc in settings["teams"]:
            team = Team(tc["name"], tc["color_idx"], tc.get("control", "local"),
                        tc.get("n_grubs", 4))
            team.ammo = default_ammo(settings)
            if not self.spec.open_sky:
                for wi in sky_weapons:    # no sky under a cave ceiling
                    team.ammo[wi] = 0
            for _ in range(team.n_grubs):
                if spawn_iter:
                    sx, sy = spawn_iter.pop()
                else:
                    sx, sy = self.rng.randint(20, self.world.w - 20), 20
                g = Grub(sx, sy, names[ni % len(names)], len(self.teams),
                         settings.get("hp", GRUB_HP))
                ni += 1
                team.grubs.append(g)
            self.teams.append(team)

        # turn state
        self.tick = 0
        self.turn_no = 0
        self.phase = Game.PH_START
        self.phase_t = 60
        self.turn_team = 0
        self.active_grub: Grub | None = self.teams[0].pick_next()
        self.turn_timer = settings.get("turn_seconds", TURN_SECONDS) * 60
        self.weapon = W_BY_KEY["bazooka"]
        self.charge = 0.0
        self.charging = False
        self.shots_left = 1
        self.fired_this_turn = False
        self._shots_fired = 0
        self.wind = 0.0
        self._roll_wind()
        self.sudden_death = False
        self.sd_at = settings.get("sudden_death_at", SUDDEN_DEATH_AFTER) * 60
        self.grav_flip_t = 0
        self.winner: int | None = None
        self.prev_fire = False
        self.settle_t = 0
        self.focus = (self.active_grub.x, self.active_grub.y) \
            if self.active_grub else (self.world.w / 2, 50)
        self.banner = f"{self.teams[0].name} — {self.active_grub.name}!" \
            if self.active_grub else ""
        if settings.get("random_weapons"):
            self._force_random_weapon()

    # ------------------------------------------------------------- helpers
    def npy_rng(self, shape):
        return self.world.rng.random(shape)

    def all_grubs(self):
        for t in self.teams:
            for g in t.grubs:
                yield g

    def current_team(self) -> Team:
        return self.teams[self.turn_team]

    def add_projectile(self, p):
        self.projectiles.append(p)

    def add_entity(self, e):
        self.entities.append(e)

    def fx_event(self, kind, x, y, mag=1):
        if len(self.fx) < 300:
            self.fx.append((kind, float(x), float(y), float(mag)))

    def add_tracer(self, x0, y0, x1, y1, ttl, color):
        if len(self.tracers) < 80:
            self.tracers.append([float(x0), float(y0), float(x1), float(y1),
                                 int(ttl), color])

    def toast(self, x, y, text, ttl=70):
        """Small floating message in world space (e.g. 'out of range')."""
        if len(self.toasts) < 12:
            self.toasts.append([float(x), float(y), text, int(ttl)])

    def spawn_trail(self, p):
        if p.trail == "smoke":
            self.particles.spawn(p.x, p.y, 0, -0.1, M.SMOKE, KIND_FX, 30)
        elif p.trail == "fire":
            self.particles.spawn(p.x, p.y, 0, 0, M.FIRE, KIND_FX, 16)

    def _roll_wind(self):
        scale = self.settings.get("wind_scale", 1.0)
        self.wind = (self.rng.random() * 2 - 1) * scale
        self.world.wind = self.wind

    def flip_gravity(self, ticks):
        self.world.gravity_dir = -1
        self.grav_flip_t = ticks

    # -------------------------------------------------------------- combat
    def apply_explosion(self, x, y, r, power, fire=False, silentish=False):
        self.world.explode(x, y, r, power, make_fire=fire, silent=silentish)
        attacker = self.current_team() if self.teams else None
        for g in self.all_grubs():
            if not g.alive:
                continue
            d = math.hypot(g.x - x, g.y - y)
            blast = r * 2.2
            if d < blast:
                f = max(0.0, 1 - d / blast)
                dmg = 999 if self.settings.get("one_shot") else power * f * 0.8
                pre = g.alive
                g.hurt(dmg, self)
                if attacker is not None and g.team != self.turn_team:
                    attacker.damage_dealt += dmg
                    attacker.max_hit = max(attacker.max_hit, dmg)
                    if pre and not g.alive:
                        attacker.kills += 1
                nd = max(d, 2.0)
                g.knockback((g.x - x) / nd * f * power * 0.045,
                            (g.y - y) / nd * f * power * 0.045 - f * 0.5)
        if not silentish:
            self.fx_event("boom", x, y, min(4.0, power / 25))
        self.focus = (x, y)

    def shock_check(self, x, y, radius, dmg):
        """Electricity: hurts grubs near the strike; double if in liquid or
        touching metal (conduction)."""
        for g in self.all_grubs():
            if not g.alive:
                continue
            d = math.hypot(g.x - x, g.y - y)
            conducted = self.world.is_liquid(g.x, g.y + GRUB_RADIUS) or \
                M.CONDUCTIVE[self.world.get(g.x, g.y + GRUB_RADIUS + 1)]
            reach = radius * (2.2 if conducted else 1.0)
            if d < reach:
                g.hurt(dmg * (1.6 if conducted else 1.0) * max(0.25, 1 - d / reach), self)
                g.shock_t = 25
                self.fx_event("zap", g.x, g.y, 1)

    def magic_touch(self, grub):
        if getattr(grub, "magic_cd", 0) > self.tick - 90:
            return
        grub.magic_cd = self.tick
        roll = self.rng.random()
        self.fx_event("magic", grub.x, grub.y, 2)
        if roll < 0.25:
            grub.hp = min(grub.max_hp, grub.hp + 20)
        elif roll < 0.45:
            grub.hurt(12, self)
        elif roll < 0.65:
            sp = self.spec.spawns
            if sp:
                x, y = self.rng.choice(sp)
                grub.x, grub.y = float(x), float(y)
        elif roll < 0.8:
            self.world.paint(grub.x, grub.y - 8, 5, M.WATER, mode="fill")
        else:
            self.flip_gravity(150)

    def on_grub_death(self, grub):
        self.fx_event("death", grub.x, grub.y, 2)
        if grub.dmg_acc >= 1 and len(self.floaters) < 24:
            self.floaters.append([grub.x, grub.y - 8,
                                  int(round(grub.dmg_acc)), 80, grub.team])
            grub.dmg_acc = 0.0
        self.ghosts.append([grub.x, grub.y - 2, 150])
        self.headstones.append((grub.x, grub.y, grub.team))
        self.apply_explosion(grub.x, grub.y, 7, 26, silentish=False)

    def collect_crate(self, crate, grub):
        crate.alive = False
        team = self.teams[grub.team]
        if crate.kind == "trap":
            self.fx_event("trap", crate.x, crate.y, 2)
            self.apply_explosion(crate.x, crate.y, 9, 34, fire=True)
        elif crate.kind == "health":
            grub.hp = min(grub.max_hp, grub.hp + 30)
            grub.poisoned = False
            self.fx_event("heal", crate.x, crate.y, 2)
        else:
            pool = [i for i, w in enumerate(WEAPONS)
                    if w.ammo != -1 and w.category != "move"]
            wi = self.rng.choice(pool)
            team.ammo[wi] = team.ammo.get(wi, 0) + (2 if not WEAPONS[wi].super_ else 1)
            self.fx_event("pickup", crate.x, crate.y, 2)
            self.banner = f"{team.name} got {WEAPONS[wi].name}!"

    # ---------------------------------------------------------- turn logic
    def _living_teams(self):
        return [i for i, t in enumerate(self.teams) if t.alive_grubs()]

    def _check_over(self):
        living = self._living_teams()
        if len(living) <= 1:
            self.phase = Game.PH_OVER
            self.winner = living[0] if living else None
            self.fx_event("fanfare" if living else "draw",
                          self.world.w / 2, 40, 3)
            return True
        return False

    def _next_turn(self):
        if self._check_over():
            return
        living = self._living_teams()
        idx = self.turn_team
        for _ in range(len(self.teams)):
            idx = (idx + 1) % len(self.teams)
            if idx in living:
                break
        self.turn_team = idx
        self.turn_no += 1
        team = self.teams[idx]
        self.active_grub = team.pick_next()
        self.turn_timer = self.settings.get("turn_seconds", TURN_SECONDS) * 60
        self.phase = Game.PH_START
        self.phase_t = 55
        self.charge = 0.0
        self.charging = False
        self.shots_left = 1
        self.fired_this_turn = False
        self._shots_fired = 0
        self._roll_wind()
        if not WEAPONS[self.weapon].ends_turn or \
                self.current_team().ammo.get(self.weapon, 0) == 0:
            self.weapon = W_BY_KEY["bazooka"]
        if self.settings.get("random_weapons"):
            self._force_random_weapon()
        if self.active_grub:
            self.focus = (self.active_grub.x, self.active_grub.y)
            self.banner = f"{team.name} — {self.active_grub.name}!"

    def _force_random_weapon(self):
        pool = [i for i, w in enumerate(WEAPONS) if w.category != "move"]
        self.weapon = self.rng.choice(pool)
        self.current_team().ammo[self.weapon] = \
            max(1, self.current_team().ammo.get(self.weapon, 0))

    def _turn_end_chores(self):
        # poison ticks
        for g in self.all_grubs():
            if g.alive and g.poisoned:
                g.hp = max(1.0, g.hp - SLUDGE_POISON)
        # sudden death
        if not self.sudden_death and self.tick >= self.sd_at:
            self.sudden_death = True
            self.banner = "SUDDEN DEATH!"
            self.fx_event("alarm", self.world.w / 2, 30, 3)
            if self.settings.get("sd_mode", "flood") in ("onehp", "both"):
                for g in self.all_grubs():
                    if g.alive:
                        g.hp = min(g.hp, 1.0)
        if self.sudden_death and \
                self.settings.get("sd_mode", "flood") in ("flood", "both"):
            w = self.world
            w.water_level = max(20, w.water_level - 5)
            band = w.mat[w.water_level:w.h - 1, 1:-1]
            mt = self.spec.flood_mat
            band[(M.PHASE[band] == M.P_EMPTY) | (M.PHASE[band] == M.P_GAS)] = mt
            w.wake(0, w.water_level - 8, w.w, w.h)
        # crates
        chance = 1.0 if self.settings.get("crate_madness") else CRATE_CHANCE
        if self.rng.random() < chance:
            kind = self.rng.choices(["weapon", "health", "trap"],
                                    weights=[55, 30, 15])[0]
            if self.spec.open_sky:
                crate = Crate(self.rng.randint(20, self.world.w - 20), kind)
            else:
                # cave maps: drop the crate just above a walkable spot so it
                # doesn't land on the ceiling, unreachable forever
                if self.spec.spawns:
                    cx, cy = self.rng.choice(self.spec.spawns)
                else:
                    cx, cy = self.world.w // 2, 30
                crate = Crate(cx, kind)
                crate.y = float(cy - 10)
            self.crates.append(crate)
            self.fx_event("crate", crate.x, max(0, crate.y), 1)
        # reset per-turn bookkeeping
        for g in self.all_grubs():
            g.damage_taken_turn = 0.0

    # ----------------------------------------------------------- main step
    def step(self, inp: InputFrame | None):
        self.tick += 1
        w = self.world
        if self.grav_flip_t > 0:
            self.grav_flip_t -= 1
            if self.grav_flip_t == 0:
                w.gravity_dir = 1
        w.step()
        self.particles.step(w)
        self._consume_world_events()
        for tr in self.tracers:
            tr[4] -= 1
        self.tracers = [tr for tr in self.tracers if tr[4] > 0]
        for to in self.toasts:
            to[1] -= 0.12
            to[3] -= 1
        self.toasts = [to for to in self.toasts if to[3] > 0]
        for fl in self.floaters:
            fl[1] -= 0.18
            fl[3] -= 1
        self.floaters = [fl for fl in self.floaters if fl[3] > 0]
        for gh in self.ghosts:
            gh[1] -= 0.22
            gh[2] -= 1
        self.ghosts = [gh for gh in self.ghosts if gh[2] > 0 and gh[1] > -8]
        # classic damage numbers: pop the accumulated hit once it settles
        for t in self.teams:
            for gr in t.grubs:
                if gr.dmg_timer > 0:
                    gr.dmg_timer -= 1
                    if gr.dmg_timer == 0 and gr.dmg_acc >= 1:
                        if len(self.floaters) < 24:
                            self.floaters.append([gr.x, gr.y - 8,
                                                  int(round(gr.dmg_acc)),
                                                  80, gr.team])
                        gr.dmg_acc = 0.0
        # blowtorch/drill are hold-to-dig: releasing FIRE stops them
        if inp is not None and not inp.fire:
            from .weapons import Stream
            for e in self.entities:
                if isinstance(e, Stream) and e.kind in ("torch", "drill") \
                        and e.grub is self.active_grub:
                    e.alive = False

        # entities & projectiles always simulate
        self.projectiles = [p for p in self.projectiles if p.update(self)]
        self.entities = [e for e in self.entities if e.update(self)]
        self.crates = [c for c in self.crates if c.update(self)]

        # spark particles shock nearby grubs
        sparks = [i for i in self.particles.live_indices()
                  if self.particles.kind[i] == KIND_SPARK]
        if sparks:
            for g in self.all_grubs():
                if not g.alive:
                    continue
                for i in sparks[:40]:
                    if abs(self.particles.x[i] - g.x) < 4 and \
                            abs(self.particles.y[i] - g.y) < 4:
                        g.hurt(0.7, self)
                        g.shock_t = 20
                        break

        active = self.active_grub
        if self.phase == Game.PH_ACTIVE and active is not None and active.alive:
            self._handle_input(inp)
        elif self.phase == Game.PH_RETREAT and active is not None and \
                active.alive and inp is not None:
            self._handle_retreat_input(inp)
        for g in self.all_grubs():
            is_active = (g is active and self.phase in
                         (Game.PH_ACTIVE, Game.PH_RETREAT))
            g.update(self, inp if is_active else None, is_active)

        # camera focus: follow flying projectiles, then the active grub
        flying = [p for p in self.projectiles
                  if not p.resting and not p.passive]
        if flying:
            self.focus = (flying[-1].x, flying[-1].y)
        elif active is not None and active.alive and \
                self.phase in (Game.PH_ACTIVE, Game.PH_RETREAT, Game.PH_START):
            self.focus = (active.x, active.y)

        # phases
        if self.phase == Game.PH_START:
            self.phase_t -= 1
            if active is None or not active.alive:
                self._next_turn()
            elif self.phase_t <= 0:
                self.phase = Game.PH_ACTIVE
        elif self.phase == Game.PH_ACTIVE:
            self.turn_timer -= 1
            # classic rule: taking real damage ends your turn (the small
            # threshold lets you survive a lick of flame without losing it)
            if active is None or not active.alive or self.turn_timer <= 0 or \
                    active.damage_taken_turn > 6:
                self._begin_resolve()
        elif self.phase == Game.PH_RETREAT:
            self.phase_t -= 1
            self.turn_timer = min(self.turn_timer, RETREAT_SECONDS * 60)
            if self.phase_t <= 0 or active is None or not active.alive:
                self._begin_resolve()
        elif self.phase == Game.PH_RESOLVE:
            self.settle_t += 1
            live_proj = any(not p.passive for p in self.projectiles)
            busy = (live_proj or self.entities or
                    w.activity > 90 or len(w.pending_detonations) > 0)
            if not busy or self.settle_t > SETTLE_TIMEOUT * 60:
                self.phase = Game.PH_TURNEND
                self.phase_t = 18
                self._turn_end_chores()
        elif self.phase == Game.PH_TURNEND:
            self.phase_t -= 1
            busy = any(not p.passive for p in self.projectiles) or \
                w.activity > 110
            if self.phase_t <= 0 and not busy:
                self._next_turn()

        self.prev_fire = bool(inp.fire) if inp else False

    def _begin_resolve(self):
        self.phase = Game.PH_RESOLVE
        self.settle_t = 0
        self.charging = False
        if self.active_grub:
            self.active_grub.jetpack = False
            self.active_grub.roping = False

    def _handle_input(self, inp: InputFrame | None):
        if inp is None:
            return
        g = self.active_grub
        team = self.current_team()
        spec = WEAPONS[self.weapon]

        # weapon switching (not mid-charge, not after firing started)
        if inp.weapon >= 0 and inp.weapon < len(WEAPONS) and \
                not self.charging and not self.fired_this_turn:
            if team.ammo.get(inp.weapon, 0) != 0:
                self.weapon = inp.weapon
                spec = WEAPONS[self.weapon]

        # click weapons fire instantly on click
        if spec.target == "click" and inp.click and spec.key != "homing":
            self._fire(spec, 0.5, inp.click)
            return
        if spec.key == "homing" and inp.click:
            self._homing_target = inp.click
            self.fx_event("lockon", inp.click[0], inp.click[1], 1)

        fire_pressed = inp.fire and not self.prev_fire
        fire_released = (not inp.fire) and self.prev_fire

        if spec.charge:
            if fire_pressed:
                self.charging = True
                self.charge = 0.0
            if self.charging:
                if inp.fire:
                    self.charge = min(1.0, self.charge + 1 / 66)
                    if self.charge >= 1.0:
                        self._fire(spec, 1.0, getattr(self, "_homing_target", None))
                elif fire_released or not inp.fire:
                    self._fire(spec, self.charge,
                               getattr(self, "_homing_target", None))
        else:
            if fire_pressed:
                self._fire(spec, 0.6, getattr(self, "_homing_target", None))

    def _handle_retreat_input(self, inp):
        """While retreating you may still use movement tools: deploy the
        parachute, fire the rope, toggle the jetpack."""
        if inp.weapon >= 0 and inp.weapon < len(WEAPONS) and \
                not WEAPONS[inp.weapon].ends_turn and \
                self.current_team().ammo.get(inp.weapon, 0) != 0:
            self.weapon = inp.weapon
        spec = WEAPONS[self.weapon]
        fire_pressed = inp.fire and not self.prev_fire
        if fire_pressed and not spec.ends_turn:
            self._fire(spec, 0.6, None)

    def _fire(self, spec, power, click):
        g = self.active_grub
        team = self.current_team()
        self.charging = False
        if team.ammo.get(self.weapon, 0) == 0:
            return
        angle = g.aim if g.facing == 1 else math.pi - g.aim
        if spec.fire_fn(self, g, angle, power, click) is False:
            return                # blocked teleport etc: nothing is spent
        self._homing_target = None
        team.shots += 1
        self.fx_event("fire_" + spec.key, g.x, g.y, 1)
        # switching the jetpack OFF refunds nothing because it costs nothing
        if spec.key == "jetpack" and not g.jetpack:
            return
        # multi-shot weapons (shotgun) cost one ammo for the whole turn
        if team.ammo.get(self.weapon, 0) > 0 and \
                (spec.shots == 1 or self._shots_fired == 0):
            team.ammo[self.weapon] -= 1
        if spec.ends_turn:
            self.fired_this_turn = True
            # multi-shot weapons keep the turn until shots run out
            if spec.shots > 1:
                self._shots_fired += 1
                if self._shots_fired < spec.shots:
                    return
                self._shots_fired = 0
            self.phase = Game.PH_RETREAT
            self.phase_t = RETREAT_SECONDS * 60

    # ----------------------------------------------------------- snapshots
    def is_quiescent(self):
        """True when state is compact enough to snapshot for a rejoining
        player (no in-flight callbacks to serialize)."""
        # PH_START only: bots haven't begun planning yet, so a freshly
        # restored client's bot RNG usage stays in sync with everyone else's
        return (all(p.passive for p in self.projectiles) and
                not self.entities and
                not self.particles.alive.any() and
                self.phase == Game.PH_START and
                not self.world.pending_detonations)

    def serialize(self):
        import base64
        rs = self.rng.getstate()
        snap = {
            "tick": self.tick, "turn_no": self.turn_no, "phase": self.phase,
            "phase_t": self.phase_t, "turn_team": self.turn_team,
            "turn_timer": self.turn_timer, "weapon": self.weapon,
            "wind": self.wind, "sudden_death": self.sudden_death,
            "grav_flip_t": self.grav_flip_t, "gravity_dir": self.world.gravity_dir,
            "water_level": self.world.water_level,
            "world": base64.b64encode(self.world.to_bytes()).decode(),
            "world_tick": self.world.tick,
            # the active-region box shapes the size of per-step RNG draws,
            # so it is real simulation state — restore it exactly
            "wake_box": self.world._wake_box,
            "rng": [rs[0], list(rs[1]), rs[2]],
            "nprng": self.world.rng.bit_generator.state,
            "headstones": [list(h) for h in self.headstones],
            "mines": [[p.x, p.y, p.vx, p.vy, p.age]
                      for p in self.projectiles if p.passive],
            "crates": [[c.x, c.y, c.vy, c.kind, c.landed] for c in self.crates],
            "fired": self.fired_this_turn, "prev_fire": self.prev_fire,
            "active": None,
            "teams": [],
        }
        for ti, t in enumerate(self.teams):
            td = {"ammo": {str(k): v for k, v in t.ammo.items()},
                  "next_grub": t.next_grub, "dmg": t.damage_dealt,
                  "kills": t.kills, "grubs": []}
            for gi, g in enumerate(t.grubs):
                if g is self.active_grub:
                    snap["active"] = [ti, gi]
                td["grubs"].append({
                    "x": g.x, "y": g.y, "vx": g.vx, "vy": g.vy, "hp": g.hp,
                    "alive": g.alive, "facing": g.facing, "aim": g.aim,
                    "poisoned": g.poisoned, "fuel": g.fuel, "name": g.name,
                    "on_ground": g.on_ground, "flying": g.flying,
                    "fall_peak_vy": g.fall_peak_vy, "drown_t": g.drown_t,
                    "chute": g.chute, "jetpack": g.jetpack,
                    "roping": g.roping, "rope": [g.rope_ax, g.rope_ay,
                                                 g.rope_len],
                    "dmg_turn": g.damage_taken_turn,
                    "magic_cd": getattr(g, "magic_cd", -10 ** 9)})
            snap["teams"].append(td)
        return snap

    def restore(self, snap):
        import base64
        self.tick = snap["tick"]; self.turn_no = snap["turn_no"]
        self.phase = snap["phase"]; self.phase_t = snap["phase_t"]
        self.turn_team = snap["turn_team"]; self.turn_timer = snap["turn_timer"]
        self.weapon = snap["weapon"]; self.wind = snap["wind"]
        self.sudden_death = snap["sudden_death"]
        self.grav_flip_t = snap["grav_flip_t"]
        self.world.gravity_dir = snap["gravity_dir"]
        self.world.wind = self.wind
        self.world.water_level = snap["water_level"]
        self.world.from_bytes(base64.b64decode(snap["world"]))
        self.world.tick = snap["world_tick"]
        self.world._wake_box = list(snap["wake_box"]) \
            if snap["wake_box"] is not None else None
        self.world.render_dirty = [0, self.world.h, 0, self.world.w]
        # the phase/density mirrors must match the restored cells everywhere,
        # since future active regions assume out-of-region mirrors are valid
        self.world.phase = M.PHASE[self.world.mat]
        self.world.dens = M.DENSITY[self.world.mat]
        self.fired_this_turn = snap["fired"]
        self.prev_fire = snap["prev_fire"]
        rs = snap["rng"]
        self.rng.setstate((rs[0], tuple(rs[1]), rs[2]))
        self.world.rng.bit_generator.state = snap["nprng"]
        self.headstones = [tuple(h) for h in snap["headstones"]]
        self.crates = []
        for cx, cy, cvy, kind, landed in snap["crates"]:
            c = Crate(cx, kind)
            c.y, c.vy, c.landed = cy, cvy, landed
            self.crates.append(c)
        for t, td in zip(self.teams, snap["teams"]):
            t.ammo = {int(k): v for k, v in td["ammo"].items()}
            t.next_grub = td["next_grub"]
            t.damage_dealt = td["dmg"]; t.kills = td["kills"]
            for g, gd in zip(t.grubs, td["grubs"]):
                g.x, g.y, g.vx, g.vy = gd["x"], gd["y"], gd["vx"], gd["vy"]
                g.hp, g.alive, g.facing = gd["hp"], gd["alive"], gd["facing"]
                g.aim, g.poisoned, g.fuel = gd["aim"], gd["poisoned"], gd["fuel"]
                g.name = gd["name"]
                g.on_ground, g.flying = gd["on_ground"], gd["flying"]
                g.fall_peak_vy, g.drown_t = gd["fall_peak_vy"], gd["drown_t"]
                g.chute, g.jetpack, g.roping = (gd["chute"], gd["jetpack"],
                                                gd["roping"])
                g.rope_ax, g.rope_ay, g.rope_len = gd["rope"]
                g.damage_taken_turn = gd["dmg_turn"]
                g.magic_cd = gd["magic_cd"]
        self.projectiles.clear(); self.entities.clear()
        self.floaters = []
        self.ghosts = []
        from .weapons import make_mine
        for mx, my, mvx, mvy, mage in snap.get("mines", []):
            m = make_mine(mx, my, mvx, mvy)
            m.age, m.resting, m.passive = mage, True, True
            self.projectiles.append(m)
        self.particles = Particles()
        self.tracers = []
        a = snap["active"]
        self.active_grub = self.teams[a[0]].grubs[a[1]] if a else None
        self.charging = False
        self.charge = 0.0

    def _consume_world_events(self):
        for ev in self.world.events:
            t = ev["type"]
            if t == "debris":
                dx, dy = ev["x"] - ev["ox"], ev["y"] - ev["oy"]
                d = math.hypot(dx, dy) or 1
                sp = ev["power"] * 0.03 * (0.4 + self.rng.random())
                self.particles.spawn(ev["x"], ev["y"], dx / d * sp,
                                     dy / d * sp - 0.8, ev["mat"],
                                     KIND_MAT, 240)
            elif t == "splash":
                dx, dy = ev["x"] - ev["ox"], ev["y"] - ev["oy"]
                d = math.hypot(dx, dy) or 1
                sp = ev["power"] * 0.025 * (0.4 + self.rng.random())
                self.particles.spawn(ev["x"], ev["y"], dx / d * sp,
                                     dy / d * sp - 0.6, ev["mat"],
                                     KIND_MAT, 240)
            elif t == "boom":
                self.fx_event("boom", ev["x"], ev["y"],
                              min(4.0, ev["power"] / 25))
        self.world.events.clear()
