"""Sandbox lab: paint materials, trigger chaos, save experiments as maps."""
import os
import math
import random
import time

import numpy as np
import pygame

from . import materials as M
from .constants import GRID_W, GRID_H
from .world import World
from .particles import Particles, KIND_MAT
from .grub import Grub
from .bodies import RigidBody
from .game import InputFrame
from .render import PAL_RGB
from .weapons import WEAPONS, W_BY_KEY
from .ui import ACCENT, ACCENT2, FG, DIM


class LabRig:
    """Just enough of the Game interface for grubs, weapons, projectiles
    and rigid bodies to run inside the sandbox."""

    def __init__(self, world):
        self.world = world
        self.rng = random.Random(7)
        self.gravity_scale = 1.0
        self.wind = 0.0
        self.particles = Particles()
        self.projectiles = []
        self.entities = []
        self.bodies = []
        self.grubs = []
        self.tracers = []
        self.toasts = []
        self.floaters = []
        self.ghosts = []
        self.fx = []
        self.focus = (0, 0)
        self.active_grub = None
        self.tick = 0           # bodies poll game.tick for their rest checks

    # --- Game API used by weapons/grubs ---
    def all_grubs(self):
        return iter(self.grubs)

    def add_projectile(self, p):
        self.projectiles.append(p)

    def add_entity(self, e):
        self.entities.append(e)

    def add_tracer(self, x0, y0, x1, y1, ttl, color):
        self.tracers.append([x0, y0, x1, y1, ttl, color])

    def toast(self, x, y, text, ttl=70):
        self.toasts.append([float(x), float(y), text, ttl])

    def fx_event(self, *a):
        pass

    def spawn_trail(self, p):
        if p.trail == "smoke":
            self.particles.spawn(p.x, p.y, 0, -0.1, M.SMOKE, 2, 30)

    def npy_rng(self, shape):
        return self.world.rng.random(shape)

    def magic_touch(self, grub):
        pass

    def shock_check(self, x, y, radius, dmg):
        for g in self.grubs:
            if g.alive and math.hypot(g.x - x, g.y - y) < radius:
                g.hurt(dmg)
                g.shock_t = 20

    def flip_gravity(self, ticks):
        self.world.gravity_dir = -self.world.gravity_dir

    def on_grub_death(self, grub):
        self.ghosts.append([grub.x, grub.y - 2, 150])

    def apply_explosion(self, x, y, r, power, fire=False, silentish=False):
        self.world.explode(x, y, r, power, make_fire=fire, silent=silentish)
        for g in self.grubs:
            d = math.hypot(g.x - x, g.y - y)
            if g.alive and d < r * 2.2:
                f = max(0.0, 1 - d / (r * 2.2))
                g.hurt(power * f * 0.8, self)
                nd = max(d, 2.0)
                g.knockback((g.x - x) / nd * f * power * 0.045,
                            (g.y - y) / nd * f * power * 0.045 - f * 0.5)
        for b in self.bodies:
            d = math.hypot(b.x - x, b.y - y)
            if d < r * 2.2:
                f = max(0.0, 1 - d / (r * 2.2))
                nd = max(d, 2.0)
                b.impulse((b.x - x) / nd * f * power * 0.05,
                          (b.y - y) / nd * f * power * 0.05 - f * 0.4,
                          dmg=power * f * 0.5)

    def step(self, inp):
        for _ in self.step_slices(inp):
            pass

    def step_slices(self, inp):
        """Tick as a generator (see World.step_slices): the sandbox spends
        a per-frame time budget on slices, so one heavy tick spreads over
        several rendered frames instead of freezing one."""
        self.tick += 1
        self.bodies = [b for b in self.bodies if b.update(self)]
        yield from self.world.step_slices()
        self.particles.step(self.world)
        self.projectiles = [p for p in self.projectiles if p.update(self)]
        self.entities = [e for e in self.entities if e.update(self)]
        self.grubs = [g for g in self.grubs if g.alive or g.hp > -1]
        for g in self.grubs:
            g.update(self, inp if g is self.active_grub else None,
                     g is self.active_grub)
        for tr in self.tracers:
            tr[4] -= 1
        self.tracers = [t for t in self.tracers if t[4] > 0]
        for gh in self.ghosts:
            gh[1] -= 0.22; gh[2] -= 1
        self.ghosts = [g2 for g2 in self.ghosts if g2[2] > 0]
        for ev in self.world.events:
            if ev["type"] in ("debris", "splash"):
                dx, dy = ev["x"] - ev["ox"], ev["y"] - ev["oy"]
                d = math.hypot(dx, dy) or 1
                sp = ev["power"] * 0.03
                self.particles.spawn(ev["x"], ev["y"], dx / d * sp,
                                     dy / d * sp - 0.8, ev["mat"],
                                     KIND_MAT, 240)
        self.world.events.clear()

MAPS_DIR = os.path.join(os.getcwd(), "maps")

# palette grouped the way the panel draws it: solids, powders, fluids, fire
PALETTE_GROUPS = [
    ("SOLID", [M.STONE, M.DIRT, M.METAL, M.WOOD, M.ICE,
               M.GLASS, M.GRASS, M.CRYSTAL]),
    ("POWDER", [M.SAND, M.GRAVEL, M.SNOW, M.ASH, M.EXPOWDER]),
    ("LIQUID", [M.WATER, M.OIL, M.ACID, M.LAVA, M.SLUDGE,
                M.SLIME, M.MAGIC, M.NITRO, M.NAPALM]),
    ("GAS+", [M.GAS, M.TOXGAS, M.SMOKE, M.STEAM, M.FIRE]),
]
PALETTE_ITEMS = [m for _, row in PALETTE_GROUPS for m in row]


def list_custom_maps():
    out = []
    if os.path.isdir(MAPS_DIR):
        for f in sorted(os.listdir(MAPS_DIR)):
            if f.endswith(".npz"):
                out.append("map:" + os.path.join(MAPS_DIR, f))
    return out


def save_map(world, name):
    os.makedirs(MAPS_DIR, exist_ok=True)
    path = os.path.join(MAPS_DIR, f"{name}.npz")
    np.savez_compressed(path, mat=world.mat, shade=world.shade)
    return path


def load_map_into(world, path):
    data = np.load(path)
    world.mat[:] = data["mat"]
    world.shade[:] = data["shade"]
    world._wake_box = [0, world.h, 0, world.w]


PANEL_W = 112          # right-hand lab kit panel
SPAWN_TOOLS = [        # (label, tool id, hint)
    ("GRUB", "grub", "Drop a test worm. A/D walk, SPACE jump. (G)"),
    ("BOOM", "boom", "Explosion at your click. Stays armed. (E)"),
    ("CRATE", "crate", "Wooden box prop. Floats and burns. (K)"),
    ("PLANK", "plank", "Light wooden plank. Floats, burns."),
    ("BLOCK", "block", "Heavy stone block. Sinks, shrugs off hits."),
    ("BEAM", "beam", "Metal beam. Very heavy, fireproof."),
]

# what-is-this line per material, shown at the panel bottom on hover
MAT_HINTS = {
    M.STONE: "Solid rock. Blastable.",
    M.DIRT: "Soft solid. Digs and blasts easily.",
    M.METAL: "Hard. Conducts electricity.",
    M.WOOD: "Solid fuel. Burns long.",
    M.ICE: "Slippery. Melts near heat.",
    M.GLASS: "Brittle. Shatters.",
    M.GRASS: "Catches fire fast.",
    M.CRYSTAL: "Glows in the dark. Brittle.",
    M.SAND: "Powder. Flows into flat cones.",
    M.GRAVEL: "Chunky powder. Sluggish.",
    M.SNOW: "Fluffy. Holds steep drifts. Melts.",
    M.ASH: "Clumpy burnt powder.",
    M.EXPOWDER: "EXPLOSIVE. Chain-detonates.",
    M.WATER: "Levels out. Conducts zaps. Douses fire.",
    M.OIL: "Floats on water. Very flammable.",
    M.ACID: "Dissolves terrain. Toxic puffs.",
    M.LAVA: "Melts and ignites. Water turns it to stone.",
    M.SLUDGE: "Toxic goo. Poisons worms.",
    M.SLIME: "Sticky gel. Clings to walls.",
    M.MAGIC: "Chaos liquid. Anything goes.",
    M.NITRO: "Liquid explosive. Sneeze and it booms.",
    M.NAPALM: "Sticky fire gel. Coats, then burns.",
    M.GAS: "Rises. Explodes at a spark.",
    M.TOXGAS: "Poison cloud.",
    M.SMOKE: "Drifts up and fades.",
    M.STEAM: "Hot mist. Cools into water.",
    M.FIRE: "Burns whatever can burn.",
}


class SandboxScreen:
    def __init__(self, app):
        self.app = app
        self.world = World(random.randint(0, 10 ** 9))
        self.world.mat[-30:, :] = M.STONE
        self.particles = Particles()
        self.mat = M.SAND
        self.brush = 5
        self.running = True
        self.rig = LabRig(self.world)
        self.weapon_i = W_BY_KEY["bazooka"]
        self.save_n = len(list_custom_maps())
        self.msg = ""
        self.msg_t = 0
        self.panel = True              # TAB hides the lab kit
        self.tool = "paint"            # paint | grub | crate/... | boom | fire
        self._hover_name = ""
        self._stroke = None            # last painted point for interpolation
        self._pt1 = None               # anchor for two-point tools
        self._grid_bg = None           # cached blueprint-grid backdrop

        class _LabSpec:
            light = 0.85
        self._spec = _LabSpec()
        self._slices = None            # mid-tick generator, frame-budgeted
        self._tick_debt = 0            # whole ticks owed to the slicer

    # ------------------------------------------------------------- actions
    def _flash(self, text, ttl=160):
        self.msg, self.msg_t = text, ttl

    def _spawn_at(self, tool, x, y):
        if tool == "grub":
            if len(self.rig.grubs) < 6:
                g = Grub(x, y, "TESTER", 0, 100)
                self.rig.grubs.append(g)
                self.rig.active_grub = g
        elif tool in ("crate", "plank", "block", "beam"):
            self.rig.bodies.append(RigidBody(x, y, tool))
        elif tool == "boom":
            self.rig.apply_explosion(x, y, 14, 60, fire=True)
            self.app.audio.play("boom", 0.8)
            self.app.renderer.camera.kick(2)
        elif tool == "fire":
            self._fire_test(x, y)

    def _fire_test(self, tx=None, ty=None):
        g = self.rig.active_grub
        ui = self.app.ui
        if g is None or not g.alive:
            self._flash("spawn a GRUB first — it does the shooting")
            return
        tx = ui.mx if tx is None else tx
        ty = ui.my if ty is None else ty
        spec = WEAPONS[self.weapon_i]
        ang = math.atan2(ty - g.y, tx - g.x)
        spec.fire_fn(self.rig, g, ang, 0.75, (int(tx), int(ty)))
        self.app.audio.play("shoot", 0.5)

    def _stroke_to(self, x, y, mat):
        """Paint a continuous stroke: interpolate from the last painted
        point so a fast-moving brush leaves a line, not dotted gaps
        (painting only happens on sim ticks, the mouse moves between)."""
        mode = "replace" if mat != M.EMPTY else "erase"
        if self._stroke is None:
            pts = [(x, y)]
        else:
            x0, y0 = self._stroke
            dist = max(abs(x - x0), abs(y - y0))
            step = max(1.0, self.brush * 0.5)
            n = max(1, int(dist / step))
            pts = [(x0 + (x - x0) * i / n, y0 + (y - y0) * i / n)
                   for i in range(1, n + 1)]
        self._stroke = (x, y)
        for px, py in pts:
            self.world.paint(px, py, self.brush, mat, mode=mode)

    def _do_shape(self, kind, p1, p2):
        mode = "replace" if self.mat != M.EMPTY else "erase"
        if kind == "line":
            self._stroke = None
            self._stroke_to(p1[0], p1[1], self.mat)
            self._stroke_to(p2[0], p2[1], self.mat)
            self._stroke = None
        else:                                    # rect
            x0, x1 = sorted((int(p1[0]), int(p2[0])))
            y0, y1 = sorted((int(p1[1]), int(p2[1])))
            x0 = max(1, x0); y0 = max(1, y0)
            x1 = min(self.world.w - 2, x1); y1 = min(self.world.h - 2, y1)
            sub = self.world.mat[y0:y1 + 1, x0:x1 + 1]
            ok = sub != M.BEDROCK
            sub[ok] = M.EMPTY if mode == "erase" else self.mat
            self.world.shade[y0:y1 + 1, x0:x1 + 1][ok] = \
                self.world.tex[y0:y1 + 1, x0:x1 + 1][ok]
            self.world.life[y0:y1 + 1, x0:x1 + 1][ok] = 0
            self.world.burn[y0:y1 + 1, x0:x1 + 1][ok] = 0
            self.world.rest[y0:y1 + 1, x0:x1 + 1] = 0
            self.world.wake(x0 - 1, y0 - 1, x1 + 1, y1 + 1)

    def _flood(self, x, y):
        """Bucket fill: flood the enclosed air pocket under the cursor."""
        import numpy as np
        w = self.world
        if self.mat == M.EMPTY:
            self._flash("pick a material to fill with")
            return
        open_ = (M.PHASE[w.mat] == M.P_EMPTY) | (M.PHASE[w.mat] == M.P_GAS)
        xi, yi = int(x), int(y)
        if not (0 <= xi < w.w and 0 <= yi < w.h) or not open_[yi, xi]:
            return
        mask = np.zeros_like(open_)
        mask[yi, xi] = True
        for _ in range(600):
            grow = mask.copy()
            grow[1:] |= mask[:-1]
            grow[:-1] |= mask[1:]
            grow[:, 1:] |= mask[:, :-1]
            grow[:, :-1] |= mask[:, 1:]
            grow &= open_
            if (grow == mask).all():
                break
            mask = grow
        w.mat[mask] = self.mat
        w.shade[mask] = w.tex[mask]
        w.life[mask] = 0
        w.burn[mask] = 0
        w.rest[mask] = 0
        ys, xs = np.nonzero(mask)
        w.wake(int(xs.min()) - 1, int(ys.min()) - 1,
               int(xs.max()) + 1, int(ys.max()) + 1)

    def _save(self):
        self.save_n += 1
        save_map(self.world, f"lab_{self.save_n:03d}")
        self._flash(f"saved maps/lab_{self.save_n:03d}.npz — "
                    f"playable from match setup!", 260)

    def _clear(self):
        self._slices = None            # drop any mid-tick work
        self.world.mat[1:-1, 1:-1] = M.EMPTY
        self.world._wake_box = [0, self.world.h, 0, self.world.w]
        self.rig.grubs.clear()
        self.rig.bodies.clear()
        self.rig.projectiles.clear()
        self.rig.entities.clear()
        self.rig.active_grub = None

    def _over_panel(self, ui):
        return self.panel and ui.mx >= GRID_W - PANEL_W

    # -------------------------------------------------------------- update
    def update(self, events):
        ui = self.app.ui
        for e in events:
            if e.type == pygame.KEYDOWN:
                if e.key == pygame.K_ESCAPE:
                    if self._pt1 is not None:
                        self._pt1 = None
                    elif self.tool != "paint":
                        self.tool = "paint"
                    else:
                        from .app import MainMenu
                        self.app.goto(MainMenu(self.app))
                elif e.key == pygame.K_TAB:
                    self.panel = not self.panel
                elif e.key == pygame.K_LEFTBRACKET:
                    self.brush = max(1, self.brush - 2)
                elif e.key == pygame.K_RIGHTBRACKET:
                    self.brush = min(25, self.brush + 2)
                elif e.key == pygame.K_p:
                    self.running = not self.running
                elif e.key == pygame.K_e:
                    self._spawn_at("boom", ui.mx, ui.my)
                elif e.key == pygame.K_g:
                    self._spawn_at("grub", ui.mx, ui.my)
                elif e.key == pygame.K_k:
                    self._spawn_at("crate", ui.mx, ui.my)
                elif e.key == pygame.K_q:
                    self.weapon_i = (self.weapon_i - 1) % len(WEAPONS)
                elif e.key == pygame.K_f:
                    self._fire_test()
                elif e.key == pygame.K_c:
                    self._clear()
                elif e.key == pygame.K_F5 or e.key == pygame.K_o:
                    self._save()
            elif e.type == pygame.MOUSEWHEEL:
                self.brush = max(1, min(25, self.brush + e.y))
            elif e.type == pygame.MOUSEBUTTONDOWN and e.button == 2:
                m = self.world.get(ui.mx, ui.my)      # eyedropper
                if m in PALETTE_ITEMS:
                    self.mat = m
                    self.tool = "paint"
                    self._flash(f"picked {M.NAMES[m]}", 70)
        # world interaction (never through the panel). Painting is tied to
        # sim ticks, not render frames — at 144 fps a frame-rate brush fed
        # the world 2.4x the material and the lag to match
        if not self._over_panel(ui):
            if self.tool in ("line", "rect"):
                if ui.clicked:
                    if self._pt1 is None:
                        self._pt1 = (ui.mx, ui.my)
                    else:
                        self._do_shape(self.tool, self._pt1,
                                       (ui.mx, ui.my))
                        self._pt1 = None
            elif self.tool == "fill":
                if ui.clicked:
                    self._flood(ui.mx, ui.my)
            elif self.tool != "paint":
                if ui.clicked:
                    self._spawn_at(self.tool, ui.mx, ui.my)
                    if self.tool in ("boom", "fire"):
                        pass                     # keep blasting on click
                    else:
                        self.tool = "paint"
            elif pygame.mouse.get_pressed()[0] and \
                    (self.app.sim_steps or not self.running):
                self._stroke_to(ui.mx, ui.my, self.mat)
            if pygame.mouse.get_pressed()[2] and \
                    (self.app.sim_steps or not self.running):
                self._stroke_to(ui.mx, ui.my, M.EMPTY)
        if not (pygame.mouse.get_pressed()[0] or
                pygame.mouse.get_pressed()[2]):
            self._stroke = None
        if self.running:
            keys = pygame.key.get_pressed()
            inp = InputFrame()
            inp.left = keys[pygame.K_a]
            inp.right = keys[pygame.K_d]
            inp.jump = keys[pygame.K_SPACE]
            g = self.rig.active_grub
            if g is not None and g.alive:
                a = math.atan2(ui.my - g.y, ui.mx - g.x)
                inp.aim = round(a * 1000) / 1000
            # frame-budgeted slicing: the sim only gets what the frame has
            # left after the measured draw+present cost (EWMAs from the
            # app loop) plus a slack for events/audio. A heavy tick then
            # spans several frames (brief slow-motion) instead of freezing
            # one — render fluidity always wins over sim realtime.
            self._tick_debt = min(self._tick_debt + self.app.sim_steps, 3)
            cap = int(self.app.settings.get("fps_cap", 144)) or 144
            frame_ms = 1000.0 / min(cap, 240)
            budget = max(1.5, frame_ms - self.app._ms_d
                         - self.app._ms_c - 1.0) / 1000.0
            t0 = time.perf_counter()
            while True:
                if self._slices is None:
                    if not self._tick_debt:
                        break
                    self._tick_debt -= 1
                    self._slices = self.rig.step_slices(inp)
                s0 = time.perf_counter()
                try:
                    next(self._slices)
                except StopIteration:
                    self._slices = None
                now = time.perf_counter()
                # predictive stop: if another slice like the last one
                # would blow the budget, don't start it (the first slice
                # of a frame always runs, so the sim can't starve)
                if now - t0 + (now - s0) * 0.8 > budget:
                    break
        if self.msg_t > 0:
            self.msg_t -= 1

    # ---------------------------------------------------------------- draw
    def draw(self, view):
        app, ui = self.app, self.app.ui
        # blueprint grid so empty space reads as lab, not void — static,
        # so it's drawn once and blitted instead of 49 lines per frame
        if self._grid_bg is None:
            bg = pygame.Surface((GRID_W, GRID_H))
            bg.fill((11, 11, 19))
            for gx in range(0, GRID_W, 24):
                pygame.draw.line(bg, (16, 17, 30), (gx, 0), (gx, GRID_H))
            for gy in range(0, GRID_H, 24):
                pygame.draw.line(bg, (16, 17, 30), (0, gy), (GRID_W, gy))
            self._grid_bg = bg
        view.blit(self._grid_bg, (0, 0))
        r = app.renderer
        r._t += 1
        changed = r.refresh_cells(self.world)
        if changed or not r._light_built:
            r._rebuild_light(self.world, self._spec)
            r._light_built = True
        view.blit(r.cell_surf, (0, 0))
        for g in self.rig.grubs:
            if g.alive:
                r._draw_grub(self.rig, g, False)
        for p in self.rig.projectiles:
            r._draw_projectile(p)
        for (x0, y0, x1, y1, ttl, col) in self.rig.tracers:
            pygame.draw.line(view, col, (x0, y0), (x1, y1), 1)
        for ps in (self.particles, self.rig.particles):
            self._draw_ps(view, ps)
        view.blit(r.liq_surf, (0, 0))
        view.blit(r.gas_surf, (0, 0))
        r._apply_light()
        self._draw_cursor(view, ui)
        if self.panel:
            self._draw_panel(view, ui)
        self._draw_status(view, ui)

    def _draw_cursor(self, view, ui):
        if self._over_panel(ui):
            return
        if self.tool == "paint":
            pygame.draw.circle(view, (255, 255, 255), (ui.mx, ui.my),
                               self.brush, 1)
        elif self.tool in ("line", "rect"):
            if self._pt1 is not None:
                if self.tool == "line":
                    pygame.draw.line(view, ACCENT, self._pt1,
                                     (ui.mx, ui.my), 1)
                else:
                    x0, x1 = sorted((self._pt1[0], ui.mx))
                    y0, y1 = sorted((self._pt1[1], ui.my))
                    pygame.draw.rect(view, ACCENT,
                                     (x0, y0, x1 - x0 + 1, y1 - y0 + 1), 1)
            pygame.draw.line(view, (255, 255, 255), (ui.mx - 2, ui.my),
                             (ui.mx + 2, ui.my))
            pygame.draw.line(view, (255, 255, 255), (ui.mx, ui.my - 2),
                             (ui.mx, ui.my + 2))
        elif self.tool == "fill":
            pygame.draw.rect(view, ACCENT, (ui.mx - 2, ui.my - 1, 5, 4), 1)
            pygame.draw.line(view, ACCENT, (ui.mx, ui.my - 3),
                             (ui.mx + 2, ui.my - 1))
        elif self.tool == "boom":
            pygame.draw.circle(view, (255, 120, 60), (ui.mx, ui.my), 14, 1)
            pygame.draw.line(view, (255, 120, 60), (ui.mx - 3, ui.my),
                             (ui.mx + 3, ui.my))
            pygame.draw.line(view, (255, 120, 60), (ui.mx, ui.my - 3),
                             (ui.mx, ui.my + 3))
        elif self.tool == "fire":
            pygame.draw.circle(view, (255, 80, 80), (ui.mx, ui.my), 3, 1)
            view.set_at((ui.mx, ui.my), (255, 220, 220))
        else:                                   # spawn ghost
            w, h = (5, 5) if self.tool == "grub" else \
                {"crate": (7, 7), "plank": (16, 4), "block": (8, 6),
                 "beam": (14, 3)}.get(self.tool, (7, 7))
            pygame.draw.rect(view, ACCENT,
                             (ui.mx - w // 2, ui.my - h // 2, w, h), 1)

    def _draw_status(self, view, ui):
        # one slim status line at the top, always visible
        tool = self.tool.upper() if self.tool != "paint" \
            else M.NAMES[self.mat].upper()
        bits = f"{tool} · brush {self.brush}"
        if not self.running:
            bits += " · PAUSED (P)"
        if not self.panel:
            bits += " · TAB kit"
        s = ui.font.render(bits, True, FG)
        pygame.draw.rect(view, (10, 9, 14),
                         (2, 2, s.get_width() + 8, 11))
        view.blit(s, (6, 4))
        if self.msg_t > 0:
            ui.label(view, (GRID_W - (PANEL_W if self.panel else 0)) // 2, 18,
                     self.msg, ACCENT2, ui.font_m, center=True)

    def _btn(self, view, ui, rect, label, hint, **kw):
        if ui._hover(pygame.Rect(rect)):
            self._hint = hint
        return ui.button(view, rect, label, font=ui.font, **kw)

    def _draw_panel(self, view, ui):
        x0 = GRID_W - PANEL_W
        ui.panel(view, (x0, 0, PANEL_W, GRID_H), None)
        ui.label(view, x0 + PANEL_W // 2, 4, "LAB KIT", ACCENT, ui.font_m,
                 center=True)
        self._hint = None
        y = 16
        # ---- materials, grouped, with a fixed name line (no flicker) ----
        hover_name = None
        for gname, row in PALETTE_GROUPS:
            ui.label(view, x0 + 8, y, gname, DIM, ui.font)
            y += 7
            for ri in range(0, len(row), 5):
                cx = x0 + 8
                for m in row[ri:ri + 5]:
                    r2 = pygame.Rect(cx, y, 17, 10)
                    pygame.draw.rect(view, PAL_RGB[m], r2)
                    pygame.draw.rect(view, (8, 8, 12), r2, 1)
                    if m == self.mat and self.tool == "paint":
                        pygame.draw.rect(view, (255, 255, 255),
                                         r2.inflate(2, 2), 1)
                    if r2.collidepoint(ui.mx, ui.my):
                        hover_name = M.NAMES[m]
                        self._hint = MAT_HINTS.get(m)
                        pygame.draw.rect(view, ACCENT, r2.inflate(2, 2), 1)
                        if ui.clicked:
                            self.mat = m
                            self.tool = "paint"
                            self.app.audio.play("click", 0.4)
                    cx += 19
                y += 12
            y += 2
        name = hover_name or (M.NAMES[self.mat] if self.mat else "Eraser")
        ui.label(view, x0 + PANEL_W // 2, y, name, FG, ui.font, center=True)
        y += 9
        # ---- tools, Photoshop style ----
        ui.label(view, x0 + 8, y, "TOOLS", DIM, ui.font)
        y += 8
        for i, (lbl, tid, hint) in enumerate((
                ("BRUSH", "paint", "Freehand painting. Hold LMB."),
                ("LINE", "line", "Two clicks: start, end."),
                ("RECT", "rect", "Two clicks: opposite corners."),
                ("FILL", "fill", "Click a cave: floods the pocket."))):
            bx = x0 + 8 + (i % 4) * 25
            if self._btn(view, ui, (bx, y, 23, 11), lbl[:4], hint,
                         accent=(self.tool == tid)):
                self.tool = tid
                self._pt1 = None
        y += 14
        if self._btn(view, ui, (x0 + 8, y, 42, 11), "ERASE",
                     "Paint nothing. RMB erases anywhere.",
                     accent=(self.mat == M.EMPTY and self.tool == "paint")):
            self.mat = M.EMPTY
            self.tool = "paint"
        if self._btn(view, ui, (x0 + 54, y, 13, 11), "-",
                     "Brush size. Or roll the mouse wheel."):
            self.brush = max(1, self.brush - 2)
        ui.label(view, x0 + 71, y + 3, f"{self.brush:2d}", FG, ui.font)
        if self._btn(view, ui, (x0 + 86, y, 13, 11), "+",
                     "Brush size. Or roll the mouse wheel."):
            self.brush = min(25, self.brush + 2)
        y += 15
        # ---- spawn tools (click arms the stamp, click world to drop) ----
        ui.label(view, x0 + 8, y, "DROP INTO THE WORLD", DIM, ui.font)
        y += 8
        for i, (label, tool, hint) in enumerate(SPAWN_TOOLS):
            bx = x0 + 8 + (i % 3) * 33
            by = y + (i // 3) * 14
            if self._btn(view, ui, (bx, by, 31, 11), label, hint,
                         accent=(self.tool == tool)):
                self.tool = tool if self.tool != tool else "paint"
        y += 2 * 14 + 3
        # ---- weapon test rig ----
        ui.label(view, x0 + 8, y, "WEAPON TEST (needs grub)", DIM, ui.font)
        y += 8
        from .icons import weapon_icon
        ic = weapon_icon(WEAPONS[self.weapon_i].key)
        view.blit(ic, (x0 + 8, y))
        if self._btn(view, ui, (x0 + 24, y, 12, 11), "<",
                     "Previous weapon. (Q)"):
            self.weapon_i = (self.weapon_i - 1) % len(WEAPONS)
        if self._btn(view, ui, (x0 + 38, y, 12, 11), ">", "Next weapon."):
            self.weapon_i = (self.weapon_i + 1) % len(WEAPONS)
        if self._btn(view, ui, (x0 + 54, y, 50, 11), "FIRE F",
                     "Armed: the grub shoots at your click.",
                     accent=(self.tool == "fire")):
            self.tool = "fire" if self.tool != "fire" else "paint"
        y += 13
        ui.label(view, x0 + 8, y, WEAPONS[self.weapon_i].name, ACCENT,
                 ui.font)
        y += 11
        # ---- world actions ----
        if self._btn(view, ui, (x0 + 8, y, 46, 11),
                     "RUN P" if not self.running else "PAUSE P",
                     "Freeze / resume the simulation.",
                     accent=not self.running):
            self.running = not self.running
        if self._btn(view, ui, (x0 + 58, y, 46, 11), "CLEAR C",
                     "Wipe the world and all actors."):
            self._clear()
        y += 14
        if self._btn(view, ui, (x0 + 8, y, 46, 11), "SAVE O",
                     "Save as a map. Playable in match setup!",
                     accent=True):
            self._save()
        if self._btn(view, ui, (x0 + 58, y, 46, 11), "MENU",
                     "Back to the main menu. (ESC)"):
            from .app import MainMenu
            self.app.goto(MainMenu(self.app))
        y += 15
        # hint area: explains whatever the mouse is over, else the basics
        if self._hint:
            words = self._hint.split()
            line = ""
            for wd in words:
                if len(line) + len(wd) + 1 > 24 and line:
                    ui.label(view, x0 + 8, y, line, FG, ui.font)
                    y += 8
                    line = wd
                else:
                    line = f"{line} {wd}".strip()
            if line:
                ui.label(view, x0 + 8, y, line, FG, ui.font)
        else:
            for line in ("MMB pick  wheel brush",):
                ui.label(view, x0 + 8, y, line, DIM, ui.font)
                y += 8

    def _draw_ps(self, view, ps):
        for i in ps.live_indices():
            x, y = int(ps.x[i]), int(ps.y[i])
            if 0 <= x < GRID_W and 0 <= y < GRID_H:
                view.set_at((x, y), PAL_RGB[ps.mat[i]])
