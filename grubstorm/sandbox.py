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
from .ui import ACCENT, ACCENT2, FG, DIM, BG2

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
        self.save_n = len(list_custom_maps())
        self.msg = ""
        self.msg_t = 0

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
                    self.world.explode(ui.mx, ui.my, 14, 60, make_fire=True)
                    self.app.audio.play("boom", 0.8)
                    self.app.renderer.camera.kick(2)
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
            self.world.step()
            self.particles.step(self.world)
            for ev in self.world.events:
                if ev["type"] in ("debris", "splash"):
                    dx, dy = ev["x"] - ev["ox"], ev["y"] - ev["oy"]
                    d = math.hypot(dx, dy) or 1
                    sp = ev["power"] * 0.03
                    self.particles.spawn(ev["x"], ev["y"], dx / d * sp,
                                         dy / d * sp - 0.8, ev["mat"],
                                         KIND_MAT, 240)
            self.world.events.clear()
        if self.msg_t > 0:
            self.msg_t -= 1

    def draw(self, view):
        app, ui = self.app, self.app.ui
        view.fill((12, 12, 22))
        # cells (reuse renderer compose path on a bare world)
        r = app.renderer
        r._t += 1
        r._compose_cells(self.world)
        view.blit(r.cell_surf, (0, 0))
        if r._gas_rect is not None:
            view.blit(r._gas_layer, r._gas_rect)
        # particles
        ps = self.particles
        for i in ps.live_indices():
            x, y = int(ps.x[i]), int(ps.y[i])
            if 0 <= x < GRID_W and 0 <= y < GRID_H:
                view.set_at((x, y), tuple(int(c) for c in M.PALETTE[int(ps.mat[i])][1]))
        # emission glow
        class _FakeSpec:
            light = 0.85
        class _FakeGame:
            projectiles = []
        r._light_pass(self.world, _FakeSpec, _FakeGame)
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
                 "LMB paint  RMB erase  [ ] brush  E boom  P pause  C clear"
                 "  O save map  ESC menu",
                 DIM, ui.font)
        if self.msg_t > 0:
            ui.label(view, GRID_W // 2, 8, self.msg, ACCENT2, ui.font_m,
                     center=True)
        if not self.running:
            ui.label(view, GRID_W // 2, 20, "SIM PAUSED", ACCENT, ui.font_m,
                     center=True)
