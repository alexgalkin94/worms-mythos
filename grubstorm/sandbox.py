"""Sandbox lab: paint materials, trigger chaos, save experiments as maps."""
import os
import math
import random

import numpy as np
import pygame

from . import materials as M
from .constants import GRID_W, GRID_H
from .world import World
from .particles import Particles, KIND_MAT
from .grub import Grub
from .bodies import RigidBody
from .game import InputFrame
from .weapons import WEAPONS, W_BY_KEY
from .ui import ACCENT, ACCENT2, FG, DIM, BG2


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
        self.bodies = [b for b in self.bodies if b.update(self)]
        self.world.step()
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

PALETTE_ITEMS = [
    M.EMPTY, M.STONE, M.DIRT, M.SAND, M.METAL, M.WOOD, M.ICE, M.GLASS,
    M.GRASS, M.CRYSTAL, M.GRAVEL, M.SNOW, M.EXPOWDER, M.WATER, M.OIL,
    M.ACID, M.LAVA, M.SLUDGE, M.SLIME, M.MAGIC, M.NITRO, M.NAPALM,
    M.GAS, M.TOXGAS, M.FIRE,
]


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

        class _LabSpec:
            light = 0.85
        self._spec = _LabSpec()

    def update(self, events):
        for e in events:
            if e.type == pygame.KEYDOWN:
                if e.key == pygame.K_ESCAPE:
                    from .app import MainMenu
                    self.app.goto(MainMenu(self.app))
                elif e.key == pygame.K_LEFTBRACKET:
                    self.brush = max(1, self.brush - 2)
                elif e.key == pygame.K_RIGHTBRACKET:
                    self.brush = min(25, self.brush + 2)
                elif e.key == pygame.K_p:
                    self.running = not self.running
                elif e.key == pygame.K_e:
                    ui = self.app.ui
                    self.rig.apply_explosion(ui.mx, ui.my, 14, 60, fire=True)
                    self.app.audio.play("boom", 0.8)
                    self.app.renderer.camera.kick(2)
                elif e.key == pygame.K_g:
                    ui = self.app.ui
                    if len(self.rig.grubs) < 4:
                        g = Grub(ui.mx, ui.my, "TESTER", 0, 100)
                        self.rig.grubs.append(g)
                        self.rig.active_grub = g
                elif e.key == pygame.K_k:
                    ui = self.app.ui
                    self.rig.bodies.append(RigidBody(ui.mx, ui.my, "crate"))
                elif e.key == pygame.K_q:
                    self.weapon_i = (self.weapon_i - 1) % len(WEAPONS)
                elif e.key == pygame.K_f:
                    self._fire_test()
                elif e.key == pygame.K_c:
                    self.world.mat[1:-1, 1:-1] = M.EMPTY
                    self.world._wake_box = [0, self.world.h, 0, self.world.w]
                elif e.key == pygame.K_F5 or e.key == pygame.K_o:
                    self.save_n += 1
                    save_map(self.world, f"lab_{self.save_n:03d}")
                    self.msg = f"saved maps/lab_{self.save_n:03d}.npz " \
                               f"(playable from match setup!)"
                    self.msg_t = 240
        ui = self.app.ui
        if ui.my < GRID_H - 26:                  # don't paint through the bar
            if pygame.mouse.get_pressed()[0] and not ui.clicked or \
                    (pygame.mouse.get_pressed()[0] and ui.my < GRID_H - 26):
                self.world.paint(ui.mx, ui.my, self.brush, self.mat,
                                 mode="replace" if self.mat != M.EMPTY else "erase")
            elif pygame.mouse.get_pressed()[2]:
                self.world.paint(ui.mx, ui.my, self.brush, M.EMPTY, mode="erase")
        if self.running:
            keys = pygame.key.get_pressed()
            inp = InputFrame()
            inp.left = keys[pygame.K_a]
            inp.right = keys[pygame.K_d]
            inp.jump = keys[pygame.K_SPACE]
            ui = self.app.ui
            g = self.rig.active_grub
            if g is not None and g.alive:
                a = math.atan2(ui.my - g.y, ui.mx - g.x)
                inp.aim = round(a * 1000) / 1000
            for _ in range(self.app.sim_steps):
                self.rig.step(inp)
        if self.msg_t > 0:
            self.msg_t -= 1

    def _fire_test(self):
        g = self.rig.active_grub
        if g is None or not g.alive:
            return
        ui = self.app.ui
        spec = WEAPONS[self.weapon_i]
        ang = math.atan2(ui.my - g.y, ui.mx - g.x)
        spec.fire_fn(self.rig, g, ang, 0.75, (int(ui.mx), int(ui.my)))
        self.app.audio.play("shoot", 0.5)

    def draw(self, view):
        app, ui = self.app, self.app.ui
        view.fill((12, 12, 22))
        # cells (reuse renderer compose path on a bare world)
        r = app.renderer
        r._t += 1
        changed = r.refresh_cells(self.world)
        if changed or not r._light_built:
            r._rebuild_light(self.world, self._spec)
            r._light_built = True
        view.blit(r.cell_surf, (0, 0))
        # rig actors live between solids and translucent liquids
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
        # brush cursor
        pygame.draw.circle(view, (255, 255, 255), (ui.mx, ui.my),
                           self.brush, 1)
        # palette bar
        bar = pygame.Rect(0, GRID_H - 26, GRID_W, 26)
        pygame.draw.rect(view, (14, 14, 26), bar)
        pygame.draw.line(view, (70, 70, 110), (0, bar.y), (GRID_W, bar.y))
        x = 4
        for m in PALETTE_ITEMS:
            r2 = pygame.Rect(x, bar.y + 3, 17, 12)
            col = tuple(int(c) for c in M.PALETTE[m][1]) if m else (30, 30, 40)
            pygame.draw.rect(view, col, r2)
            if m == self.mat:
                pygame.draw.rect(view, (255, 255, 255), r2.inflate(2, 2), 1)
            if r2.collidepoint(ui.mx, ui.my):
                pygame.draw.rect(view, ACCENT, r2, 1)
                ui.label(view, x, bar.y - 9, M.NAMES[m], FG, ui.font)
                if ui.clicked:
                    self.mat = m
            x += 19
        ui.label(view, 4, bar.y + 17,
                 "LMB paint RMB erase [ ] brush E boom G grub K crate "
                 "Q weapon F fire A/D/SPACE move O save ESC menu",
                 DIM, ui.font)
        ui.label(view, GRID_W - 130, bar.y - 9,
                 f"weapon: {WEAPONS[self.weapon_i].name}", ACCENT, ui.font)
        if self.msg_t > 0:
            ui.label(view, GRID_W // 2, 8, self.msg, ACCENT2, ui.font_m,
                     center=True)
        if not self.running:
            ui.label(view, GRID_W // 2, 20, "SIM PAUSED", ACCENT, ui.font_m,
                     center=True)

    def _draw_ps(self, view, ps):
        for i in ps.live_indices():
            x, y = int(ps.x[i]), int(ps.y[i])
            if 0 <= x < GRID_W and 0 <= y < GRID_H:
                view.set_at((x, y),
                            tuple(int(c) for c in M.PALETTE[int(ps.mat[i])][1]))
