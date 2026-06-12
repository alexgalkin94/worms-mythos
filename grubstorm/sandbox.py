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
SPAWN_TOOLS = [        # (label, tool id, hotkey hint)
    ("GRUB", "grub", "G"), ("BOOM", "boom", "E"),
    ("CRATE", "crate", "K"), ("PLANK", "plank", ""),
    ("BLOCK", "block", ""), ("BEAM", "beam", ""),
]


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

        class _LabSpec:
            light = 0.85
        self._spec = _LabSpec()

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

    def _save(self):
        self.save_n += 1
        save_map(self.world, f"lab_{self.save_n:03d}")
        self._flash(f"saved maps/lab_{self.save_n:03d}.npz — "
                    f"playable from match setup!", 260)

    def _clear(self):
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
                    if self.tool != "paint":
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
        # world interaction (never through the panel)
        if not self._over_panel(ui):
            if self.tool != "paint":
                if ui.clicked:
                    self._spawn_at(self.tool, ui.mx, ui.my)
                    if self.tool in ("boom", "fire"):
                        pass                     # keep blasting on click
                    else:
                        self.tool = "paint"
            elif pygame.mouse.get_pressed()[0]:
                self.world.paint(ui.mx, ui.my, self.brush, self.mat,
                                 mode="replace" if self.mat != M.EMPTY
                                 else "erase")
            if pygame.mouse.get_pressed()[2]:
                self.world.paint(ui.mx, ui.my, self.brush, M.EMPTY,
                                 mode="erase")
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
            for _ in range(self.app.sim_steps):
                self.rig.step(inp)
        if self.msg_t > 0:
            self.msg_t -= 1

    # ---------------------------------------------------------------- draw
    def draw(self, view):
        app, ui = self.app, self.app.ui
        view.fill((11, 11, 19))
        # blueprint grid so empty space reads as lab, not void
        for gx in range(0, GRID_W, 24):
            pygame.draw.line(view, (16, 17, 30), (gx, 0), (gx, GRID_H))
        for gy in range(0, GRID_H, 24):
            pygame.draw.line(view, (16, 17, 30), (0, gy), (GRID_W, gy))
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

    def _draw_panel(self, view, ui):
        x0 = GRID_W - PANEL_W
        ui.panel(view, (x0, 0, PANEL_W, GRID_H), None)
        ui.label(view, x0 + PANEL_W // 2, 4, "LAB KIT", ACCENT, ui.font_m,
                 center=True)
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
                    col = tuple(int(c) for c in M.PALETTE[m][1])
                    pygame.draw.rect(view, col, r2)
                    pygame.draw.rect(view, (8, 8, 12), r2, 1)
                    if m == self.mat and self.tool == "paint":
                        pygame.draw.rect(view, (255, 255, 255),
                                         r2.inflate(2, 2), 1)
                    if r2.collidepoint(ui.mx, ui.my):
                        hover_name = M.NAMES[m]
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
        # eraser + brush row
        if ui.button(view, (x0 + 8, y, 42, 11), "ERASE",
                     accent=(self.mat == M.EMPTY and self.tool == "paint"),
                     font=ui.font):
            self.mat = M.EMPTY
            self.tool = "paint"
        if ui.button(view, (x0 + 54, y, 13, 11), "-", font=ui.font):
            self.brush = max(1, self.brush - 2)
        ui.label(view, x0 + 71, y + 3, f"{self.brush:2d}", FG, ui.font)
        if ui.button(view, (x0 + 86, y, 13, 11), "+", font=ui.font):
            self.brush = min(25, self.brush + 2)
        y += 15
        # ---- spawn tools (click arms the stamp, click world to drop) ----
        ui.label(view, x0 + 8, y, "DROP INTO THE WORLD", DIM, ui.font)
        y += 8
        for i, (label, tool, key) in enumerate(SPAWN_TOOLS):
            bx = x0 + 8 + (i % 3) * 33
            by = y + (i // 3) * 14
            if ui.button(view, (bx, by, 31, 11), label,
                         accent=(self.tool == tool), font=ui.font):
                self.tool = tool if self.tool != tool else "paint"
        y += 2 * 14 + 3
        # ---- weapon test rig ----
        ui.label(view, x0 + 8, y, "WEAPON TEST (needs grub)", DIM, ui.font)
        y += 8
        from .icons import weapon_icon
        ic = weapon_icon(WEAPONS[self.weapon_i].key)
        view.blit(ic, (x0 + 8, y))
        if ui.button(view, (x0 + 24, y, 12, 11), "<", font=ui.font):
            self.weapon_i = (self.weapon_i - 1) % len(WEAPONS)
        if ui.button(view, (x0 + 38, y, 12, 11), ">", font=ui.font):
            self.weapon_i = (self.weapon_i + 1) % len(WEAPONS)
        if ui.button(view, (x0 + 54, y, 50, 11), "FIRE F",
                     accent=(self.tool == "fire"), font=ui.font):
            self.tool = "fire" if self.tool != "fire" else "paint"
        y += 13
        ui.label(view, x0 + 8, y, WEAPONS[self.weapon_i].name, ACCENT,
                 ui.font)
        y += 11
        # ---- world actions ----
        if ui.button(view, (x0 + 8, y, 46, 11),
                     "RUN P" if not self.running else "PAUSE P",
                     accent=not self.running, font=ui.font):
            self.running = not self.running
        if ui.button(view, (x0 + 58, y, 46, 11), "CLEAR C", font=ui.font):
            self._clear()
        y += 14
        if ui.button(view, (x0 + 8, y, 46, 11), "SAVE O", accent=True,
                     font=ui.font):
            self._save()
        if ui.button(view, (x0 + 58, y, 46, 11), "MENU", font=ui.font):
            from .app import MainMenu
            self.app.goto(MainMenu(self.app))
        y += 15
        for line in ("LMB paint  RMB erase  MMB pick",
                     "wheel brush  TAB hide panel"):
            ui.label(view, x0 + 8, y, line, DIM, ui.font)
            y += 8

    def _draw_ps(self, view, ps):
        for i in ps.live_indices():
            x, y = int(ps.x[i]), int(ps.y[i])
            if 0 <= x < GRID_W and 0 <= y < GRID_H:
                view.set_at((x, y),
                            tuple(int(c) for c in M.PALETTE[int(ps.mat[i])][1]))
