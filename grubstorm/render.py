"""Rendering: compose the cell grid, entities, lighting and glow into a
grid-resolution frame. crt.py upscales it into warm phosphor glass."""
import math
import random

import numpy as np
import pygame

from . import materials as M
from .constants import (GRID_W, GRID_H, TEAM_COLORS, TEAM_COLORS_CB,
                        GRUB_RADIUS)
from .game import Game
from .particles import KIND_MAT, KIND_SPARK, KIND_FX
from .pixelfont import PixelFont

_KEY = (255, 0, 255)

PAL_FLAT = M.PALETTE.reshape(M.N_MATS * 4, 3).copy()
EMIT_FLAT = M.EMISSION.copy()


class Camera:
    def __init__(self):
        self.shake = 0.0
        self.ox = self.oy = 0.0
        self.flash = 0.0

    def kick(self, mag):
        self.shake = min(8.0, self.shake + mag * 2.2)

    def update(self):
        self.shake *= 0.88
        self.flash *= 0.85
        if self.shake > 0.2:
            self.ox = random.uniform(-self.shake, self.shake)
            self.oy = random.uniform(-self.shake, self.shake)
        else:
            self.ox = self.oy = 0.0


class Renderer:
    def __init__(self, settings):
        self.settings = settings
        self.view = pygame.Surface((GRID_W, GRID_H))
        self.cell_surf = pygame.Surface((GRID_W, GRID_H))
        self.cell_surf.set_colorkey(_KEY)
        self.cell_surf.fill(_KEY)
        self.gas_surf = pygame.Surface((GRID_W, GRID_H))
        self.gas_surf.set_colorkey(_KEY)
        self.gas_surf.set_alpha(150)
        self.gas_surf.fill(_KEY)
        self.em_surf = pygame.Surface((GRID_W, GRID_H))
        self._glow = pygame.Surface((GRID_W, GRID_H))
        self._dark = None
        self._light_built = False
        self._world_ref = None
        self.font = PixelFont(1)
        self.font_big = PixelFont(2)
        self.font_huge = PixelFont(3)
        self.camera = Camera()
        self._sky_cache = None
        self._sky_key = None
        self._decor = []
        self._t = 0
        self._gas_rect = None
        self._gas_layer = None

    # ----------------------------------------------------------------- sky
    def _sky(self, spec):
        key = (spec.sky_top, spec.sky_bottom)
        if self._sky_key != key:
            top = np.array(spec.sky_top, np.float32)
            bot = np.array(spec.sky_bottom, np.float32)
            g = np.linspace(0, 1, GRID_H, dtype=np.float32)[:, None]
            grad = top[None, :] * (1 - g) + bot[None, :] * g
            # depth falloff: the lower you look, the darker the backdrop —
            # caves opened under the surface read as dark, not as sunset
            depth = 1.0 - 0.62 * g ** 1.6
            grad = (grad * depth).astype(np.uint8)
            arr = np.repeat(grad[:, None, :], GRID_W, axis=1)
            surf = pygame.Surface((GRID_W, GRID_H))
            pygame.surfarray.blit_array(surf, arr.swapaxes(0, 1))
            self._sky_cache = surf
            self._sky_key = key
            self._decor = [(random.uniform(0, GRID_W),
                            random.uniform(0, GRID_H * 0.8),
                            random.uniform(0.2, 1.0)) for _ in range(60)]
        return self._sky_cache

    def _draw_decor(self, spec):
        v = self.view
        t = self._t
        kind = spec.decor
        for i, (x, y, z) in enumerate(self._decor):
            if kind == "stars":
                if (i + t // 30) % 7:
                    v.set_at((int(x), int(y * 0.7)), (200, 200, 220))
            elif kind == "embers":
                yy = (y - t * 0.2 * z) % GRID_H
                v.set_at((int((x + math.sin(t * 0.01 + i) * 8) % GRID_W),
                          int(yy)), (255, int(120 * z), 20))
            elif kind == "snow":
                yy = (y + t * 0.3 * z) % GRID_H
                v.set_at((int((x + math.sin(t * 0.02 + i) * 10) % GRID_W),
                          int(yy)), (230, 235, 250))
            elif kind == "clouds":
                if i % 6 == 0:
                    xx = (x + t * 0.05 * z) % (GRID_W + 60) - 30
                    pygame.draw.ellipse(v, (96, 88, 102),
                                        (xx, y * 0.35, 38 * z, 9 * z))
                    pygame.draw.ellipse(v, (118, 108, 122),
                                        (xx + 8 * z, y * 0.35 - 3 * z,
                                         22 * z, 8 * z))
            elif kind == "drips":
                yy = (y + t * 0.8 * z) % GRID_H
                if i % 3 == 0:
                    v.set_at((int(x), int(yy)), (90, 140, 80))
            elif kind == "bubbles":
                yy = (y - t * 0.25 * z) % GRID_H
                v.set_at((int(x), int(yy)), (255, 190, 220))
            elif kind == "spores":
                yy = (y - t * 0.1 * z) % GRID_H
                if (i + t // 20) % 5:
                    v.set_at((int(x), int(yy)), (120, 200, 160))
            elif kind == "dust":
                xx = (x + t * 0.3 * z) % GRID_W
                if i % 2:
                    v.set_at((int(xx), int(y)), (180, 150, 110))
            elif kind == "sparks":
                if (i * 13 + t // 8) % 11 == 0:
                    v.set_at((int(x), int(y)), (255, 230, 120))

    # --------------------------------------------------------------- cells
    # The composed cell/gas/emission surfaces persist between frames; only
    # the world's dirty rectangle is recomposed, and only on sim ticks.
    # Render-only frames (high-FPS mode) just re-blit cached surfaces.
    def refresh_cells(self, world):
        if self._world_ref is not world:        # screen switched worlds
            self._world_ref = world
            self._light_built = False
            world.render_dirty = [0, world.h, 0, world.w]
        box = world.render_dirty
        if box is None:
            return False
        world.render_dirty = None
        y0 = max(0, box[0] - 1); y1 = min(world.h, box[1] + 1)
        x0 = max(0, box[2] - 1); x1 = min(world.w, box[3] + 1)
        if y0 >= y1 or x0 >= x1:
            return False
        mat = world.mat[y0:y1, x0:x1]
        idxT = np.ascontiguousarray(
            ((mat.astype(np.uint16) << 2) | world.shade[y0:y1, x0:x1]).T)
        rgbT = PAL_FLAT[idxT]                            # (w, h, 3)
        # burning cells flicker orange
        burning = world.burn[y0:y1, x0:x1].T > 0
        if burning.any():
            n = int(burning.sum())
            fl = (np.arange(n) + self._t) % 3
            rgbT[burning] = np.array([(255, 140, 40), (255, 100, 30),
                                      (240, 180, 60)], np.uint8)[fl]
        matT = (idxT >> 2).astype(np.uint8)
        phT = M.PHASE[matT]
        self._edge_shade(world, rgbT, y0, y1, x0, x1)
        gasT = phT == M.P_GAS
        gas_rgb = rgbT.copy()
        gas_rgb[~gasT] = _KEY
        rgbT[phT <= M.P_GAS] = _KEY                      # key out empty + gas
        rect = (x0, y0, x1 - x0, y1 - y0)
        pygame.surfarray.blit_array(self.cell_surf.subsurface(rect), rgbT)
        pygame.surfarray.blit_array(self.gas_surf.subsurface(rect), gas_rgb)
        pygame.surfarray.blit_array(self.em_surf.subsurface(rect),
                                    EMIT_FLAT[matT])
        return True

    def _edge_shade(self, world, rgbT, y0, y1, x0, x1):
        """Noita-style material edges: dark outline where terrain meets air,
        a lit highlight on top surfaces, and a bright skin on liquids."""
        h, w = world.h, world.w
        ey0, ey1 = max(0, y0 - 1), min(h, y1 + 1)
        ex0, ex1 = max(0, x0 - 1), min(w, x1 + 1)
        ph = M.PHASE[world.mat[ey0:ey1, ex0:ex1]]
        solid = ph >= M.P_POWDER
        open_ = ph <= M.P_GAS
        liquid = ph == M.P_LIQUID
        above = np.zeros_like(open_)
        above[1:] = open_[:-1]
        above[0] = ey0 == 0                       # the sky counts as open
        below = np.zeros_like(open_)
        below[:-1] = open_[1:]
        sides = np.zeros_like(open_)
        sides[:, 1:] |= open_[:, :-1]
        sides[:, :-1] |= open_[:, 1:]
        top_lit = solid & above
        outline = solid & (below | sides) & ~top_lit
        skin = liquid & above
        # crop the context window back to the write rect, transposed
        oy, ox = y0 - ey0, x0 - ex0
        sl = (slice(oy, oy + (y1 - y0)), slice(ox, ox + (x1 - x0)))
        top_litT = top_lit[sl].T
        outlineT = outline[sl].T
        skinT = skin[sl].T
        v = rgbT[top_litT].astype(np.uint16)
        rgbT[top_litT] = np.minimum(v + (v >> 2) + 16, 255).astype(np.uint8)
        v = rgbT[outlineT].astype(np.uint16)
        rgbT[outlineT] = ((v * 130) >> 8).astype(np.uint8)
        v = rgbT[skinT].astype(np.uint16)
        rgbT[skinT] = np.minimum(v + (v >> 2) + 10, 255).astype(np.uint8)

    def _rebuild_light(self, world, spec, projectiles=()):
        """Blurred glow from the emission surface + cave darkness."""
        sw, sh = GRID_W // 6, GRID_H // 6
        small = pygame.transform.smoothscale(self.em_surf, (sw, sh))
        small = pygame.transform.smoothscale(small, (sw // 2, sh // 2))
        self._glow = pygame.transform.smoothscale(small, (GRID_W, GRID_H))
        bloom = float(self.settings.get("bloom", 0.7))
        if bloom < 0.99:
            self._glow.set_alpha(int(255 * bloom))
        amb = spec.light
        if amb < 0.999:
            dark = pygame.Surface((GRID_W, GRID_H))
            base = int(80 + amb * 175)
            dark.fill((base, base, base))
            light_up = pygame.transform.smoothscale(small, (GRID_W, GRID_H))
            for _ in range(2):
                dark.blit(light_up, (0, 0), special_flags=pygame.BLEND_RGB_ADD)
            for p in projectiles:
                pygame.draw.circle(dark, (70, 60, 50), (int(p.x), int(p.y)), 16)
            self._dark = dark
        else:
            self._dark = None

    def _apply_light(self):
        if self._dark is not None:
            self.view.blit(self._dark, (0, 0),
                           special_flags=pygame.BLEND_RGB_MULT)
        if float(self.settings.get("bloom", 0.7)) > 0.05:
            for _ in range(2):
                self.view.blit(self._glow, (0, 0),
                               special_flags=pygame.BLEND_RGB_ADD)

    # ------------------------------------------------------------ entities
    def team_color(self, idx):
        table = TEAM_COLORS_CB if self.settings.get("colorblind") else TEAM_COLORS
        return table[idx % len(table)][0]

    def _draw_grub(self, game, g, active):
        v = self.view
        x, y = g.x, g.y
        col = self.team_color(g.team)
        f = g.facing
        wig = math.sin(g.anim) * 0.6 if abs(g.vx) > 0.01 else \
            math.sin(self._t * 0.05 + g.x) * 0.25      # idle breathing
        # skin tints for status
        skin = (226, 162, 144)
        belly = (246, 200, 178)
        if g.poisoned:
            skin, belly = (160, 198, 124), (196, 226, 160)
        if g.shock_t > 0 and (self._t // 2) % 2:
            skin, belly = (255, 255, 170), (255, 255, 210)
        if g.burn_t > 0 and (self._t // 3) % 2:
            skin, belly = (255, 150, 80), (255, 190, 120)
        outline = (44, 22, 30)
        # an actual worm: segmented body curling up from the tail to a
        # raised head, leaning into the facing direction
        tilt = -math.sin(g.aim) * 0.6 if active else 0.0
        segs = [
            (x - f * 2.7, y + 2.2, 1.4),                  # tail
            (x - f * 1.7, y + 1.8 + wig * 0.4, 1.7),
            (x - f * 0.6, y + 0.9 - wig * 0.4, 1.9),
            (x + f * 0.3, y - 0.5 + wig * 0.3, 1.9),
        ]
        hx = x + f * 0.9
        hy = y - 2.4 + tilt
        for px, py, r in segs:                            # dark rim first
            pygame.draw.circle(v, outline, (px, py), r + 0.8)
        pygame.draw.circle(v, outline, (hx, hy), 3.0)
        for px, py, r in segs:
            pygame.draw.circle(v, skin, (px, py), r)
        for px, py, r in segs[1:]:                        # belly highlight
            pygame.draw.circle(v, belly, (px + f * 0.5, py + 0.5), r - 0.9)
        pygame.draw.circle(v, skin, (hx, hy), 2.2)
        pygame.draw.circle(v, belly, (hx + f * 0.6, hy + 0.7), 1.0)
        # team bandana: slim headband under the eyes, knot trailing behind
        pygame.draw.rect(v, col, (hx - 2.4, hy - 0.4, 5, 1.6))
        kx = hx - f * 2.8
        pygame.draw.line(v, col, (kx, hy - 0.4), (kx - f, hy + 1.2), 1)
        # two big googly worm eyes above the band, pupils follow the aim
        pdy = 2 if g.aim > 0.35 else 0 if g.aim < -0.35 else 1
        pdx = 1 if f > 0 else 0
        for k in (-1, 1):
            ex = hx + f * 0.6 + k * 1.5 - 1
            pygame.draw.rect(v, (250, 250, 252), (ex, hy - 3.8, 2, 3))
            v.set_at((int(ex + pdx), int(hy - 3.8 + pdy)), (16, 12, 22))
        if g.chute and not g.on_ground:
            pygame.draw.arc(v, col, (x - 7, y - 14, 14, 12), 0, math.pi, 2)
            pygame.draw.line(v, (200, 200, 200), (x - 6, y - 8), (x, y - 2))
            pygame.draw.line(v, (200, 200, 200), (x + 6, y - 8), (x, y - 2))
        if g.jetpack:
            pygame.draw.rect(v, (120, 120, 140), (x - g.facing * 5, y - 2, 3, 5))
        if g.roping:
            pygame.draw.line(v, (220, 200, 140), (x, y),
                             (int(g.rope_ax), int(g.rope_ay)), 1)
        # hp pill
        if g.alive:
            hpw = max(1, int(10 * g.hp / g.max_hp))
            hpc = (90, 220, 90) if g.hp > 50 else \
                  (240, 200, 60) if g.hp > 25 else (240, 80, 60)
            pygame.draw.rect(v, (20, 20, 30), (x - 6, y - 11, 12, 3))
            pygame.draw.rect(v, hpc, (x - 5, y - 10, hpw, 1))
            pygame.draw.rect(v, col, (x - 6, y - 11, 12, 3), 1)
        if active:
            # name tag + aim
            name = self.font.render(g.name, True, (255, 255, 255))
            v.blit(name, (x - name.get_width() // 2, y - 20))
            ang = g.aim if g.facing == 1 else math.pi - g.aim
            cx = x + math.cos(ang) * 14
            cy = y + math.sin(ang) * 14
            pygame.draw.circle(v, (255, 80, 80), (int(cx), int(cy)), 2, 1)
            pygame.draw.circle(v, (255, 220, 220), (int(cx), int(cy)), 0)
            # marker arrow
            if (self._t // 20) % 2:
                pygame.draw.polygon(v, col, [(x, y - 16), (x - 3, y - 20),
                                             (x + 3, y - 20)])

    def _draw_projectile(self, p):
        v = self.view
        x, y = int(p.x), int(p.y)
        if p.glyph == "rocket":
            ang = math.atan2(p.vy, p.vx)
            tx = x - math.cos(ang) * 3
            ty = y - math.sin(ang) * 3
            pygame.draw.line(v, p.color, (tx, ty), (x, y), 2)
            pygame.draw.circle(v, (255, 240, 200), (x, y), 1)
        elif p.glyph == "mine":
            pygame.draw.circle(v, (60, 60, 70), (x, y), 2)
            if (self._t // 15) % 2 and p.age > p.arm_delay:
                v.set_at((x, y - 2), (255, 60, 60))
        elif p.glyph == "tnt":
            pygame.draw.rect(v, (200, 50, 50), (x - 2, y - 3, 4, 6))
            if (self._t // 6) % 2:
                v.set_at((x, y - 4), (255, 230, 120))
        elif p.glyph == "melon":
            pygame.draw.circle(v, (90, 180, 70), (x, y), 3)
            pygame.draw.circle(v, (50, 120, 50), (x, y), 3, 1)
        elif p.glyph == "hole":
            pygame.draw.circle(v, (30, 10, 50), (x, y), 3)
            pygame.draw.circle(v, (180, 100, 255), (x, y), 3, 1)
        else:
            pygame.draw.circle(v, p.color, (x, y), 2)
            pygame.draw.circle(v, (255, 255, 255), (x, y), 2, 1)

    def _draw_particles(self, game):
        v = self.view
        ps = game.particles
        for i in ps.live_indices():
            x, y = int(ps.x[i]), int(ps.y[i])
            if not (0 <= x < GRID_W and 0 <= y < GRID_H):
                continue
            k = ps.kind[i]
            if k == KIND_SPARK:
                pygame.draw.line(v, (255, 255, 160),
                                 (x, y), (x - int(ps.vx[i]), y - int(ps.vy[i])))
            else:
                m = int(ps.mat[i])
                v.set_at((x, y), tuple(int(c) for c in M.PALETTE[m][1]))

    def _draw_entities(self, game):
        v = self.view
        from .weapons import BlackHole, Stream, WEAPONS, CAST_RANGE
        # casting-range ring for the selected click weapon
        g = game.active_grub
        if g is not None and g.alive and game.phase == Game.PH_ACTIVE:
            rng_r = CAST_RANGE.get(WEAPONS[game.weapon].key)
            if rng_r:
                pygame.draw.circle(v, (120, 104, 70), (int(g.x), int(g.y)),
                                   rng_r, 1)
        for (x, y, text, ttl) in game.toasts:
            s = self.font.render(text, True, (255, 120, 100))
            s.set_alpha(min(255, ttl * 6))
            v.blit(s, (int(x) - s.get_width() // 2, int(y) - 4))
        for (x0, y0, x1, y1, ttl, col) in game.tracers:
            pygame.draw.line(v, col, (x0, y0), (x1, y1), 1)
        # homing lock-on marker
        tgt = getattr(game, "_homing_target", None)
        if tgt is not None and (self._t // 10) % 2:
            tx, ty = tgt
            pygame.draw.circle(v, (255, 90, 90), (int(tx), int(ty)), 4, 1)
            pygame.draw.line(v, (255, 90, 90), (tx - 6, ty), (tx - 3, ty))
            pygame.draw.line(v, (255, 90, 90), (tx + 3, ty), (tx + 6, ty))
        for e in game.entities:
            if isinstance(e, BlackHole):
                pygame.draw.circle(v, (10, 5, 20), (int(e.x), int(e.y)), 5)
                a = self._t * 0.3
                for k in range(3):
                    aa = a + k * 2.1
                    r = 7 + 3 * math.sin(a * 0.7 + k)
                    pygame.draw.circle(v, (170, 90, 255),
                                       (int(e.x + math.cos(aa) * r),
                                        int(e.y + math.sin(aa) * r)), 1)
                pygame.draw.circle(v, (220, 180, 255), (int(e.x), int(e.y)), 6, 1)
        for c in game.crates:
            x, y = int(c.x), int(c.y)
            if not c.landed:
                pygame.draw.arc(v, (240, 240, 240), (x - 6, y - 12, 12, 10),
                                0, math.pi, 1)
                pygame.draw.line(v, (180, 180, 180), (x - 5, y - 7), (x, y - 2))
                pygame.draw.line(v, (180, 180, 180), (x + 5, y - 7), (x, y - 2))
            col = {"health": (90, 220, 120), "weapon": (230, 190, 80),
                   "trap": (230, 190, 80)}[c.kind]
            pygame.draw.rect(v, (60, 45, 30), (x - 3, y - 3, 7, 7))
            pygame.draw.rect(v, col, (x - 3, y - 3, 7, 7), 1)
            sign = "+" if c.kind == "health" else "?"
            s = self.font.render(sign, True, col)
            v.blit(s, (x - s.get_width() // 2 + 1, y - 4))

    def _draw_headstones(self, game):
        v = self.view
        for (x, y, team) in game.headstones:
            x, y = int(x), int(y)
            pygame.draw.rect(v, (140, 140, 150), (x - 2, y - 4, 5, 6))
            pygame.draw.rect(v, (100, 100, 110), (x - 3, y + 1, 7, 2))
            pygame.draw.line(v, (60, 60, 70), (x, y - 3), (x, y))
            pygame.draw.line(v, (60, 60, 70), (x - 1, y - 2), (x + 1, y - 2))

    # ----------------------------------------------------------------- HUD
    def _draw_hud(self, game: Game):
        v = self.view
        # wind bar
        cx = GRID_W // 2
        pygame.draw.rect(v, (16, 16, 26), (cx - 36, 4, 72, 7))
        pygame.draw.rect(v, (90, 90, 120), (cx - 36, 4, 72, 7), 1)
        wpix = int(game.wind * 33)
        if wpix >= 0:
            pygame.draw.rect(v, (120, 200, 255), (cx + 1, 6, wpix, 3))
        else:
            pygame.draw.rect(v, (255, 160, 90), (cx + 1 + wpix, 6, -wpix, 3))
        wl = self.font.render("WIND", True, (140, 140, 170))
        v.blit(wl, (cx - wl.get_width() // 2, 12))
        # timer
        secs = max(0, game.turn_timer // 60)
        timer = self.font_big.render(str(secs), True,
                                     (255, 90, 80) if secs <= 5 else (240, 240, 250))
        pygame.draw.circle(v, (16, 16, 26), (16, 12), 10)
        pygame.draw.circle(v, (90, 90, 120), (16, 12), 10, 1)
        v.blit(timer, (16 - timer.get_width() // 2, 12 - timer.get_height() // 2))
        # team health bars
        yy = 4
        total_max = max(1, max(sum(g.max_hp for g in t.grubs) for t in game.teams))
        for i, t in enumerate(game.teams):
            col = self.team_color(t.color_idx)
            hp = t.total_hp()
            wbar = int(50 * hp / total_max)
            x0 = GRID_W - 58
            pygame.draw.rect(v, (16, 16, 26), (x0, yy, 52, 5))
            pygame.draw.rect(v, col, (x0 + 1, yy + 1, max(0, wbar), 3))
            if i == game.turn_team and (self._t // 20) % 2:
                pygame.draw.rect(v, (255, 255, 255), (x0 - 1, yy - 1, 54, 7), 1)
            yy += 7
        # weapon + charge
        from .weapons import WEAPONS
        spec = WEAPONS[game.weapon]
        ammo = game.current_team().ammo.get(game.weapon, 0)
        wtxt = self.font.render(
            spec.name if ammo < 0 else f"{spec.name} [{ammo}]",
            True, (224, 210, 178))
        pygame.draw.rect(v, (16, 16, 26),
                         (4, GRID_H - 16, wtxt.get_width() + 8, 12))
        v.blit(wtxt, (8, GRID_H - 14))
        # homing needs a target first — say so
        if spec.key == "homing" and \
                getattr(game, "_homing_target", None) is None and \
                game.phase == Game.PH_ACTIVE:
            hint = self.font.render("click a target, then fire", True,
                                    (160, 200, 220))
            v.blit(hint, (8, GRID_H - 26))
        if game.charging:
            p = game.charge
            pygame.draw.rect(v, (20, 20, 30), (4, GRID_H - 26, 84, 8))
            pygame.draw.rect(v, (255, int(220 - p * 160), 60),
                             (5, GRID_H - 25, int(82 * p), 6))
        # banner
        if game.phase in (Game.PH_START,) and game.banner:
            b = self.font_big.render(game.banner, True, (255, 255, 255))
            bg = pygame.Surface((b.get_width() + 16, b.get_height() + 6))
            bg.fill((16, 16, 30))
            bg.set_alpha(200)
            v.blit(bg, (GRID_W // 2 - bg.get_width() // 2, 30))
            v.blit(b, (GRID_W // 2 - b.get_width() // 2, 33))
        if game.sudden_death and (self._t // 30) % 2:
            sd = self.font.render("SUDDEN DEATH", True, (255, 80, 70))
            v.blit(sd, (GRID_W // 2 - sd.get_width() // 2, 22))

    # ---------------------------------------------------------------- main
    def render_game(self, game: Game, hud=True):
        self._t += 1
        self.camera.update()
        spec = game.spec
        v = self.view
        changed = self.refresh_cells(game.world)
        if changed or game.projectiles or not self._light_built:
            self._rebuild_light(game.world, spec, game.projectiles)
            self._light_built = True
        v.blit(self._sky(spec), (0, 0))
        self._draw_decor(spec)
        v.blit(self.cell_surf, (0, 0))
        v.blit(self.gas_surf, (0, 0))
        self._draw_headstones(game)
        self._draw_entities(game)
        for p in game.projectiles:
            self._draw_projectile(p)
        self._draw_particles(game)
        for g in game.all_grubs():
            if g.alive:
                self._draw_grub(game, g, g is game.active_grub and
                                game.phase in (Game.PH_ACTIVE, Game.PH_RETREAT))
        self._apply_light()
        if self.camera.flash > 0.03 and not self.settings.get("reduce_flash"):
            f = pygame.Surface((GRID_W, GRID_H))
            f.fill((255, 240, 220))
            f.set_alpha(int(90 * self.camera.flash))
            v.blit(f, (0, 0))
        if hud:
            self._draw_hud(game)
        return v

    def consume_fx(self, game, audio):
        """Translate game fx events into shake/flash/sound."""
        shake_scale = float(self.settings.get("shake", 1.0))
        for (kind, x, y, mag) in game.fx:
            if kind == "boom":
                self.camera.kick(mag * shake_scale)
                self.camera.flash = min(1.0, self.camera.flash + mag * 0.3)
                audio.play("boom" if mag > 1.5 else "boom_s", min(1.0, 0.4 + mag * 0.2))
            elif kind == "thud":
                self.camera.kick(0.4 * shake_scale)
                audio.play("thud", 0.5)
            elif kind in ("splat",):
                audio.play("splat", 0.5)
            elif kind == "zap":
                audio.play("zap", 0.5)
            elif kind == "lightning":
                self.camera.kick(2 * shake_scale)
                self.camera.flash = 1.0
                audio.play("zap", 1.0)
            elif kind == "death":
                audio.play("death", 0.7)
            elif kind in ("pickup", "heal"):
                audio.play("pickup", 0.6)
            elif kind == "teleport" or kind == "magic":
                audio.play("warp", 0.5)
            elif kind == "fanfare":
                audio.play("fanfare", 0.8)
            elif kind == "alarm":
                audio.play("alarm", 0.8)
            elif kind.startswith("fire_"):
                audio.play("shoot", 0.5)
            elif kind == "shot":
                audio.play("shoot", 0.7)
            elif kind == "tic":
                audio.play("tic", 0.25)
            elif kind == "torch":
                if self._t % 8 == 0:
                    audio.play("sizzle", 0.2)
            elif kind == "vortex":
                audio.play("warp", 0.9)
            elif kind == "chime":
                audio.play("pickup", 0.5)
            elif kind in ("swing", "clank"):
                audio.play("thud", 0.6)
            elif kind == "bubble":
                audio.play("bubble", 0.3)
        game.fx.clear()
