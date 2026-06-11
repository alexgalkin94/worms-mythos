"""Bots. Imperfect on purpose — they should be funny, not oppressive.

Bots are deterministic (they only use game.rng and game state), so in
online play every client computes identical bot behaviour locally.
"""
import math

from .constants import GRAVITY, MAX_WIND
from .game import Game, InputFrame
from .weapons import WEAPONS, W_BY_KEY

PERSONAS = {
    # aim_err (rad), think_quality, chaos, walk, use_supers
    "dumb":     dict(err=0.28, quality=0.3, chaos=0.5, walk=0.2, supers=0.3),
    "normal":   dict(err=0.13, quality=0.6, chaos=0.25, walk=0.5, supers=0.5),
    "tactical": dict(err=0.06, quality=0.9, chaos=0.12, walk=0.8, supers=0.6),
    "evil":     dict(err=0.03, quality=1.0, chaos=0.20, walk=0.9, supers=1.0),
}

PERSONA_LABELS = {
    "dumb": "Chaotic Dummy", "normal": "Regular Joe",
    "tactical": "Tactician", "evil": "Evil Genius",
}


def _simulate_shot(game, x0, y0, angle, power, wind_affected, owner):
    """Cheap ballistic integration against the terrain. Returns (x, y) of
    impact or None if it flies off."""
    w = game.world
    vx = math.cos(angle) * (1.2 + power * 3.4)
    vy = math.sin(angle) * (1.2 + power * 3.4)
    x, y = x0 + math.cos(angle) * 6, y0 + math.sin(angle) * 6 - 1
    for _ in range(400):
        vy += GRAVITY * game.gravity_scale
        if wind_affected:
            vx += game.wind * MAX_WIND * 14
        sp = math.hypot(vx, vy)
        steps = max(1, int(sp) + 1)
        for _ in range(steps):
            x += vx / steps
            y += vy / steps
            if x < 1 or x >= w.w - 1 or y >= w.h - 1:
                return None
            if y > 0 and w.is_solid(x, y):
                return (x, y)
    return None


class Bot:
    def __init__(self, persona="normal"):
        self.persona = PERSONAS.get(persona, PERSONAS["normal"])
        self.persona_key = persona
        self.plan = None
        self.planned_turn = -1

    # ------------------------------------------------------------ planning
    def _pick_target(self, game, me):
        rng = game.rng
        enemies = [g for g in game.all_grubs()
                   if g.alive and g.team != me.team]
        if not enemies:
            return None
        if self.persona_key == "evil":
            return min(enemies, key=lambda g: g.hp)
        if rng.random() < self.persona["chaos"] * 0.3:
            return rng.choice(enemies)
        return min(enemies, key=lambda g: math.hypot(g.x - me.x, g.y - me.y))

    def _solve_arc(self, game, me, target, wind_affected):
        """Search angle/power space for a shot that lands near the target."""
        best, best_d = None, 1e9
        n_angles = 7 + int(self.persona["quality"] * 14)
        for i in range(n_angles):
            ang = -math.pi * 0.95 + (i / max(1, n_angles - 1)) * math.pi * 0.9
            # mirror sampling around both directions
            for a in (ang, math.pi - ang):
                for power in (0.35, 0.55, 0.75, 0.95):
                    hit = _simulate_shot(game, me.x, me.y, a, power,
                                         wind_affected, me)
                    if hit is None:
                        continue
                    d = math.hypot(hit[0] - target.x, hit[1] - target.y)
                    dself = math.hypot(hit[0] - me.x, hit[1] - me.y)
                    if dself < 16:           # don't blow yourself up
                        continue
                    if d < best_d:
                        best_d = d
                        best = (a, power, d)
        if best and best[2] < 26:
            return best
        return None

    def _choose(self, game, me, target):
        rng = game.rng
        team = game.current_team()
        ammo = team.ammo
        dist = math.hypot(target.x - me.x, target.y - me.y)

        def has(key):
            return ammo.get(W_BY_KEY[key], 0) != 0

        # melee when right next to someone
        if dist < 10 and has("hammer"):
            return dict(kind="melee", weapon=W_BY_KEY["hammer"],
                        face=1 if target.x > me.x else -1)
        # super weapons for the dramatic
        if rng.random() < self.persona["supers"] * 0.25:
            for key in ("melon", "blackhole", "napalm", "lightning"):
                if has(key):
                    spec = WEAPONS[W_BY_KEY[key]]
                    if spec.target == "click":
                        return dict(kind="click", weapon=W_BY_KEY[key],
                                    click=(int(target.x), int(target.y - 2)))
        # chaos picks
        if rng.random() < self.persona["chaos"] * 0.5:
            for key in ("acid", "lavabomb", "cluster", "powder", "gas",
                        "sludge"):
                if has(key) and rng.random() < 0.5:
                    sol = self._solve_arc(game, me, target, False)
                    if sol:
                        return dict(kind="arc", weapon=W_BY_KEY[key],
                                    angle=sol[0], power=sol[1])
        # the meat: bazooka / grenade arcs
        sol = self._solve_arc(game, me, target, True)
        if sol and has("bazooka"):
            return dict(kind="arc", weapon=W_BY_KEY["bazooka"],
                        angle=sol[0], power=sol[1])
        sol = self._solve_arc(game, me, target, False)
        if sol and has("grenade"):
            return dict(kind="arc", weapon=W_BY_KEY["grenade"],
                        angle=sol[0], power=sol[1])
        # open sky above target? airstrike
        if has("airstrike"):
            clear = not game.world.raycast(target.x, 2, 0, 1,
                                           max(2, target.y - 8))
            if clear:
                return dict(kind="click", weapon=W_BY_KEY["airstrike"],
                            click=(int(target.x), int(target.y)))
        # walk closer, then lob a grenade roughly at them
        ang = math.atan2(target.y - me.y - 10, target.x - me.x)
        return dict(kind="arc", weapon=W_BY_KEY["grenade"] if has("grenade")
                    else W_BY_KEY["bazooka"],
                    angle=ang - 0.4, power=min(1.0, dist / 150 + 0.3),
                    walk_to=target.x)

    def _make_plan(self, game, me):
        rng = game.rng
        target = self._pick_target(game, me)
        if target is None:
            return dict(kind="skip", t=0)
        plan = self._choose(game, me, target)
        # personality: aim error
        if "angle" in plan:
            plan["angle"] += (rng.random() * 2 - 1) * self.persona["err"]
            plan["power"] = max(0.15, min(1.0,
                plan["power"] + (rng.random() * 2 - 1) * self.persona["err"]))
        plan["t"] = 0
        plan["walk_ticks"] = 0
        if plan.get("walk_to") is not None and \
                rng.random() < self.persona["walk"]:
            plan["walk_ticks"] = 150
        return plan

    # ------------------------------------------------------------ steering
    def act(self, game: Game) -> InputFrame:
        inp = InputFrame()
        me = game.active_grub
        if me is None or not me.alive or game.phase != Game.PH_ACTIVE:
            self.plan = None
            return inp
        if self.plan is None or self.planned_turn != game.turn_no:
            self.plan = self._make_plan(game, me)
            self.planned_turn = game.turn_no
        plan = self.plan
        plan["t"] += 1
        t = plan["t"]

        if plan["kind"] == "skip":
            return inp

        # phase 1: optional approach walk
        if plan.get("walk_ticks", 0) > 0:
            plan["walk_ticks"] -= 1
            tx = plan.get("walk_to", me.x)
            if abs(tx - me.x) > 12:
                # cliff probe: don't walk into a long drop
                d = 1 if tx > me.x else -1
                drop = 0
                for dy in range(28):
                    if game.world.is_solid(me.x + d * 5, me.y + dy):
                        break
                    drop += 1
                if drop < 24:
                    inp.right = d > 0
                    inp.left = d < 0
                    if game.world.is_solid(me.x + d * 4, me.y) and t % 30 < 2:
                        inp.jump = True
                else:
                    plan["walk_ticks"] = 0
            else:
                plan["walk_ticks"] = 0
            return inp

        # phase 2: select weapon
        if t < 40:
            inp.weapon = plan["weapon"]
            return inp

        # click weapons: just click
        if plan["kind"] == "click":
            if t == 45:
                inp.weapon = plan["weapon"]
                inp.click = plan["click"]
            return inp

        if plan["kind"] == "melee":
            me.facing = plan["face"]
            if t == 50:
                inp.fire = True
            return inp

        # arc weapons: set aim directly (deterministic), then charge & release
        ang = plan["angle"]
        me.facing = 1 if math.cos(ang) >= 0 else -1
        me.aim = ang if me.facing == 1 else math.pi - ang
        me.aim = max(-math.pi / 2, min(math.pi / 2, me.aim))
        spec = WEAPONS[plan["weapon"]]
        charge_ticks = int(plan["power"] * 70) if spec.charge else 1
        if t < 60:
            return inp
        if t < 60 + charge_ticks:
            inp.fire = True
        # after that: fire is released automatically -> shot happens
        return inp
