"""Application shell: window, screens, settings, the main loop."""
import json
import math
import os
import random
import sys

import pygame

from .constants import (GRID_W, GRID_H, VIEW_W, VIEW_H, TEAM_COLORS,
                        TEAM_COLORS_CB, TEAM_NAME_POOL, TURN_SECONDS)
from .game import Game, InputFrame
from .ai import Bot, PERSONA_LABELS
from .render import Renderer
from .crt import CRT
from .audio import Audio
from .ui import UI, ACCENT, ACCENT2, FG, DIM, BG, BG2
from .mapgen import BIOMES, BIOME_LABELS
from .weapons import WEAPONS
from . import sandbox as sandbox_mod
from . import net as net_mod

SETTINGS_PATH = os.path.join(os.path.expanduser("~"), ".grubstorm.json")

DEFAULT_SETTINGS = {
    "crt": 0.8, "bloom": 0.7, "shake": 1.0, "volume": 0.8,
    "reduce_flash": False, "colorblind": False, "aberration": True,
    "fullscreen": False, "server": "127.0.0.1:31999", "player_name": "Grub",
    "fps_cap": 144, "show_fps": False, "render_scale": 3, "curvature": False,
}

FPS_CAPS = [60, 120, 144, 240, 0]          # 0 = uncapped

CONTROL_CYCLE = ["local", "bot:dumb", "bot:normal", "bot:tactical",
                 "bot:evil"]
CONTROL_LABELS = {"local": "Human", "bot:dumb": "Bot: Dummy",
                  "bot:normal": "Bot: Joe", "bot:tactical": "Bot: Tactician",
                  "bot:evil": "Bot: Evil Genius"}


def load_settings():
    s = dict(DEFAULT_SETTINGS)
    try:
        with open(SETTINGS_PATH) as f:
            s.update(json.load(f))
    except Exception:
        pass
    return s


def save_settings(s):
    try:
        with open(SETTINGS_PATH, "w") as f:
            json.dump(s, f, indent=2)
    except Exception:
        pass


# ============================================================== screens ====
class Screen:
    def __init__(self, app):
        self.app = app

    def update(self, events):
        pass

    def draw(self, view):
        pass


class MenuDemo:
    """Bot match running behind the menus."""
    def __init__(self):
        self.new_match()

    def new_match(self):
        biome = random.choice(BIOMES)
        settings = {
            "seed": random.randint(0, 10 ** 9), "biome": biome,
            "turn_seconds": 10,
            "teams": [
                {"name": "Demo A", "color_idx": random.randint(0, 3),
                 "n_grubs": 2, "control": "bot:dumb"},
                {"name": "Demo B", "color_idx": random.randint(4, 7),
                 "n_grubs": 2, "control": "bot:normal"},
            ],
        }
        self.game = Game(settings)
        self.bots = {0: Bot("dumb"), 1: Bot("normal")}
        self.over_t = 0

    def step(self):
        g = self.game
        if g.phase == Game.PH_OVER:
            self.over_t += 1
            if self.over_t > 240:
                self.new_match()
                return
        inp = self.bots[g.turn_team].act(g)
        g.step(inp)
        g.fx.clear()


class MainMenu(Screen):
    def __init__(self, app):
        super().__init__(app)
        self.demo = app.demo

    def update(self, events):
        self.app.step_demo()

    def draw(self, view):
        app = self.app
        ui = app.ui
        app.renderer.render_game(self.demo.game, hud=False)
        dim = pygame.Surface((GRID_W, GRID_H), pygame.SRCALPHA)
        dim.fill((8, 8, 18, 120))
        view.blit(dim, (0, 0))
        wob = math.sin(ui.t * 0.05) * 2
        ui.title(view, GRID_W // 2, 22 + wob, "GRUBSTORM")
        ui.label(view, GRID_W // 2, 58, "every pixel is alive",
                 ACCENT2, ui.font_m, center=True)
        bw, bh, x = 110, 16, GRID_W // 2 - 55
        y = 84
        items = [
            ("LOCAL PARTY", lambda: app.goto(LocalSetup(app))),
            ("ONLINE", lambda: app.goto(OnlineMenu(app))),
            ("SANDBOX LAB", lambda: app.goto(sandbox_mod.SandboxScreen(app))),
            ("OPTIONS", lambda: app.goto(OptionsScreen(app))),
            ("HOW TO PLAY", lambda: app.goto(HelpScreen(app))),
            ("QUIT", app.quit),
        ]
        for label, fn in items:
            if ui.button(view, (x, y, bw, bh), label,
                         accent=(label == "LOCAL PARTY")):
                fn()
            y += bh + 6
        ui.label(view, 4, GRID_H - 12, "v0.1 — a Worms × Noita fever dream",
                 DIM, ui.font)


class LocalSetup(Screen):
    def __init__(self, app):
        super().__init__(app)
        names = list(TEAM_NAME_POOL)
        random.shuffle(names)
        self.teams = [
            {"name": names[0], "color_idx": 0, "n_grubs": 4, "control": "local"},
            {"name": names[1], "color_idx": 1, "n_grubs": 4, "control": "bot:normal"},
        ]
        self.pool = names
        self.biome_i = 0
        self.biomes = list(BIOMES) + sandbox_mod.list_custom_maps()
        self.mods = {"low_gravity": False, "one_shot": False,
                     "random_weapons": False, "crate_madness": False,
                     "all_super": False, "hurricane": False}
        self.turn_seconds = TURN_SECONDS
        self.sd_minutes = 8

    def update(self, events):
        self.app.step_demo()

    def biome_label(self):
        b = self.biomes[self.biome_i]
        if b.startswith("map:"):
            return os.path.basename(b[4:])[:-4]
        return BIOME_LABELS.get(b, (b, ""))[0]

    def draw(self, view):
        app, ui = self.app, self.app.ui
        app.renderer.render_game(app.demo.game, hud=False)
        ui.panel(view, (8, 6, GRID_W - 16, GRID_H - 12), "MATCH SETUP")
        y = 28
        # team rows
        for i, t in enumerate(self.teams):
            col = app.renderer.team_color(t["color_idx"])
            pygame.draw.rect(view, col, (16, y + 2, 8, 8))
            ui.label(view, 28, y + 1, t["name"], FG, ui.font_m)
            if ui.button(view, (120, y, 60, 12),
                         CONTROL_LABELS[t["control"]], font=ui.font):
                idx = CONTROL_CYCLE.index(t["control"])
                t["control"] = CONTROL_CYCLE[(idx + 1) % len(CONTROL_CYCLE)]
            if ui.button(view, (184, y, 30, 12), f"x{t['n_grubs']}",
                         font=ui.font):
                t["n_grubs"] = t["n_grubs"] % 6 + 1
            if ui.button(view, (218, y, 26, 12), "col", font=ui.font):
                t["color_idx"] = (t["color_idx"] + 1) % len(TEAM_COLORS)
            if len(self.teams) > 2 and ui.button(view, (248, y, 14, 12), "-",
                                                 font=ui.font):
                self.teams.pop(i)
                break
            y += 15
        if len(self.teams) < 8 and ui.button(view, (16, y, 90, 12),
                                             "+ ADD TEAM", font=ui.font):
            self.teams.append({
                "name": self.pool[len(self.teams) % len(self.pool)],
                "color_idx": len(self.teams) % len(TEAM_COLORS),
                "n_grubs": 4, "control": "bot:normal"})
        # biome selector
        bx = 280
        ui.selector(view, (bx, 24, 180, 22), "ARENA", self.biome_label(),
                    lambda: self._cycle_biome(-1), lambda: self._cycle_biome(1))
        b = self.biomes[self.biome_i]
        desc = BIOME_LABELS.get(b, ("", "custom sandbox map"))[1]
        ui.label(view, bx, 48, desc, DIM, ui.font)
        # mutators
        ui.label(view, bx, 62, "MUTATORS", ACCENT, ui.font)
        my = 72
        labels = {"low_gravity": "Low gravity", "one_shot": "One-shot kills",
                  "random_weapons": "Random weapons",
                  "crate_madness": "Crate madness",
                  "all_super": "All super weapons",
                  "hurricane": "Hurricane winds"}
        for k, lab in labels.items():
            self.mods[k] = ui.toggle(view, (bx, my, 130, 13), lab, self.mods[k])
            my += 15
        self.turn_seconds = int(ui.slider(view, (bx, my + 6, 130, 18),
                                          f"Turn time: {self.turn_seconds}s",
                                          self.turn_seconds, 10, 90))
        my += 28
        self.sd_minutes = int(ui.slider(view, (bx, my + 6, 130, 18),
                                        f"Sudden death: {self.sd_minutes} min",
                                        self.sd_minutes, 2, 20))
        if ui.button(view, (GRID_W // 2 - 70, GRID_H - 28, 80, 18),
                     "START!", accent=True, font=ui.font_b):
            self.start()
        if ui.button(view, (GRID_W // 2 + 20, GRID_H - 28, 50, 18), "BACK"):
            app.goto(MainMenu(app))

    def _cycle_biome(self, d):
        self.biome_i = (self.biome_i + d) % len(self.biomes)

    def start(self):
        mods = dict(self.mods)
        hurricane = mods.pop("hurricane")
        settings = {
            "seed": random.randint(0, 10 ** 9),
            "biome": self.biomes[self.biome_i],
            "teams": [dict(t) for t in self.teams],
            "turn_seconds": self.turn_seconds,
            "sudden_death_at": self.sd_minutes * 60,
            "wind_scale": 2.2 if hurricane else 1.0,
            **mods,
        }
        self.app.goto(GameScreen(self.app, settings))


class GameScreen(Screen):
    def __init__(self, app, settings, session=None, game=None):
        super().__init__(app)
        self.settings = settings
        self.game = game if game is not None else Game(settings)
        self.net_lost = False
        self.bots = {}
        for i, t in enumerate(self.game.teams):
            if t.control.startswith("bot:"):
                self.bots[i] = Bot(t.control.split(":", 1)[1])
        self.session = session              # net session or None
        self.paused = False
        self.panel_open = False
        self.over_t = 0
        self.pending_weapon = -1
        self.pending_click = None
        self.stalled = False

    # ----------------------------------------------------------- input
    def _local_input(self):
        keys = pygame.key.get_pressed()
        inp = InputFrame()
        inp.left = keys[pygame.K_LEFT] or keys[pygame.K_a]
        inp.right = keys[pygame.K_RIGHT] or keys[pygame.K_d]
        inp.aim_up = keys[pygame.K_UP] or keys[pygame.K_w]
        inp.aim_down = keys[pygame.K_DOWN] or keys[pygame.K_s]
        inp.fire = keys[pygame.K_SPACE]
        inp.jump = self._jump
        inp.backflip = self._backflip
        inp.weapon = self.pending_weapon
        inp.click = self.pending_click
        self.pending_weapon = -1
        self.pending_click = None
        return inp

    def update(self, events):
        app = self.app
        self._jump = self._backflip = False
        for e in events:
            if e.type == pygame.KEYDOWN:
                if e.key == pygame.K_ESCAPE:
                    if self.panel_open:
                        self.panel_open = False
                    else:
                        self.paused = not self.paused
                elif e.key == pygame.K_RETURN:
                    self._jump = True
                elif e.key == pygame.K_BACKSPACE:
                    self._backflip = True
                elif e.key == pygame.K_TAB:
                    self.panel_open = not self.panel_open
            elif e.type == pygame.MOUSEBUTTONDOWN:
                if e.button == 3:
                    self.panel_open = not self.panel_open
                elif e.button == 1 and not self.panel_open and not self.paused:
                    mx, my = pygame.mouse.get_pos()
                    win = pygame.display.get_surface().get_size()
                    gx = mx * GRID_W // max(1, win[0])
                    gy = my * GRID_H // max(1, win[1])
                    self.pending_click = (gx, gy)
        if self.paused:
            return
        for _ in range(app.sim_steps):
            self._tick_once()
            self._jump = self._backflip = False
            if app.screen is not self:          # match ended mid-frame
                return

    def _tick_once(self):
        app = self.app
        g = self.game
        if g.phase == Game.PH_OVER:
            self.over_t += 1
            g.step(None)
            app.renderer.consume_fx(g, app.audio)
            if self.over_t > 200:
                app.goto(ResultsScreen(app, self.settings, g, self.session))
            return
        # who controls this tick?
        ctrl = g.teams[g.turn_team].control
        inp = None
        self.stalled = False
        if ctrl.startswith("bot:"):
            inp = self.bots[g.turn_team].act(g)
        elif ctrl == "local":
            inp = self._local_input()
        elif ctrl.startswith("net:"):
            pid = int(ctrl.split(":")[1])
            if self.session and pid == self.session.pid:
                inp = self._local_input()
                self.session.send_input(g.tick + 1, inp)
            else:
                inp = self.session.get_input(g.tick + 1) if self.session else InputFrame()
                if inp is None:
                    # host keeps the match alive for vanished players
                    if self.session and self.session.is_host() and \
                            pid not in self.session.present_pids():
                        inp = InputFrame()
                        self.session.send_input(g.tick + 1, inp)
                    else:
                        self.stalled = True
        if self.session:
            self.session.pump(self)
        if not self.stalled:
            g.step(inp)
        app.renderer.consume_fx(g, app.audio)

    # ------------------------------------------------------------ draw
    def draw(self, view):
        app, ui = self.app, self.app.ui
        g = self.game
        app.renderer.render_game(g, hud=True)
        if self.panel_open:
            self._draw_weapon_panel(view)
        if self.stalled and ui.t % 60 < 40:
            ui.label(view, GRID_W // 2, GRID_H - 40,
                     "waiting for player...", ACCENT2, ui.font_m, center=True)
        if self.net_lost:
            ui.label(view, GRID_W // 2, GRID_H - 52,
                     "connection lost — ESC to leave", (255, 100, 90),
                     ui.font_m, center=True)
        if self.paused:
            ui.panel(view, (GRID_W // 2 - 60, 70, 120, 110), "PAUSED")
            y = 95
            if ui.button(view, (GRID_W // 2 - 45, y, 90, 15), "RESUME"):
                self.paused = False
            y += 20
            if self.session is None and \
                    ui.button(view, (GRID_W // 2 - 45, y, 90, 15), "SKIP TURN"):
                g.turn_timer = 0
                self.paused = False
            y += 20
            if ui.button(view, (GRID_W // 2 - 45, y, 90, 15), "QUIT MATCH"):
                if self.session:
                    self.session.close()
                app.goto(MainMenu(app))
        elif g.phase == Game.PH_OVER:
            who = g.teams[g.winner].name if g.winner is not None else "NOBODY"
            ui.title(view, GRID_W // 2, 60, f"{who} WINS!")

    def _draw_weapon_panel(self, view):
        app, ui = self.app, self.app.ui
        g = self.game
        team = g.current_team()
        ui.panel(view, (GRID_W - 150, 18, 144, GRID_H - 40), "ARSENAL")
        cats = [("boom", "BOOM"), ("chem", "CHEMISTRY"),
                ("energy", "ENERGY"), ("super", "SUPER"), ("move", "MOVE")]
        y = 38
        mine = not g.teams[g.turn_team].control.startswith("net:") or \
            (self.session and
             g.teams[g.turn_team].control == f"net:{self.session.pid}")
        for ckey, clabel in cats:
            ui.label(view, GRID_W - 144, y, clabel, ACCENT, ui.font)
            y += 9
            for i, w in enumerate(WEAPONS):
                if w.category != ckey:
                    continue
                ammo = team.ammo.get(i, 0)
                if ammo == 0:
                    continue
                ammo_s = "" if ammo < 0 else f" x{ammo}"
                sel = i == g.weapon
                r = pygame.Rect(GRID_W - 144, y, 136, 9)
                hov = r.collidepoint(ui.mx, ui.my)
                if sel:
                    pygame.draw.rect(view, (60, 45, 25), r)
                elif hov:
                    pygame.draw.rect(view, (40, 40, 60), r)
                col = ACCENT if sel else (FG if hov else DIM)
                ui.label(view, r.x + 2, y + 1, w.name + ammo_s, col, ui.font)
                if hov and ui.clicked and mine:
                    self.pending_weapon = i
                    self.panel_open = False
                y += 9
            y += 4


class ResultsScreen(Screen):
    def __init__(self, app, settings, game, session=None):
        super().__init__(app)
        self.settings = settings
        self.game = game
        self.session = session

    def update(self, events):
        for _ in range(self.app.sim_steps):
            self.game.step(None)
            self.game.fx.clear()

    def draw(self, view):
        app, ui = self.app, self.app.ui
        g = self.game
        app.renderer.render_game(g, hud=False)
        ui.panel(view, (GRID_W // 2 - 110, 24, 220, GRID_H - 60),
                 "MATCH RESULTS")
        who = g.teams[g.winner].name if g.winner is not None else "NOBODY"
        ui.label(view, GRID_W // 2, 46, f"{who} WINS!", ACCENT,
                 ui.font_b, center=True)
        y = 70
        for t in sorted(g.teams, key=lambda t: -t.total_hp()):
            col = app.renderer.team_color(t.color_idx)
            pygame.draw.rect(view, col, (GRID_W // 2 - 96, y + 2, 6, 6))
            alive = len(t.alive_grubs())
            ui.label(view, GRID_W // 2 - 86, y,
                     f"{t.name}  hp:{int(t.total_hp())}  alive:{alive}"
                     f"  dmg:{int(t.damage_dealt)}  kills:{t.kills}",
                     FG, ui.font)
            y += 14
        if self.session is None:
            if ui.button(view, (GRID_W // 2 - 80, GRID_H - 52, 75, 16),
                         "REMATCH", accent=True):
                s = dict(self.settings)
                s["seed"] = random.randint(0, 10 ** 9)
                app.goto(GameScreen(app, s))
        if ui.button(view, (GRID_W // 2 + 5, GRID_H - 52, 75, 16), "MENU"):
            if self.session:
                self.session.close()
            app.goto(MainMenu(app))


class OptionsScreen(Screen):
    def update(self, events):
        self.app.step_demo()

    def draw(self, view):
        app, ui = self.app, self.app.ui
        s = app.settings
        app.renderer.render_game(app.demo.game, hud=False)
        ui.panel(view, (GRID_W // 2 - 110, 10, 220, GRID_H - 20), "OPTIONS")
        x, w = GRID_W // 2 - 90, 180
        y = 34
        s["crt"] = ui.slider(view, (x, y, w, 18),
                             f"CRT intensity: {int(s['crt'] * 100)}%",
                             s["crt"])
        y += 26
        s["bloom"] = ui.slider(view, (x, y, w, 18),
                               f"Bloom: {int(s['bloom'] * 100)}%", s["bloom"])
        y += 26
        s["shake"] = ui.slider(view, (x, y, w, 18),
                               f"Screen shake: {int(s['shake'] * 100)}%",
                               s["shake"], 0, 2)
        y += 26
        s["volume"] = ui.slider(view, (x, y, w, 18),
                                f"Volume: {int(s['volume'] * 100)}%",
                                s["volume"])
        y += 26
        s["reduce_flash"] = ui.toggle(view, (x, y, w, 14),
                                      "Reduce flashing", s["reduce_flash"])
        y += 18
        s["colorblind"] = ui.toggle(view, (x, y, w, 14),
                                    "Colorblind team colors", s["colorblind"])
        y += 18
        s["aberration"] = ui.toggle(view, (x, y, w // 2 - 4, 14),
                                    "Color fringe", s["aberration"])
        s["curvature"] = ui.toggle(view, (x + w // 2 + 4, y, w // 2 - 4, 14),
                                   "Curvature", s["curvature"])
        y += 18
        fs = ui.toggle(view, (x, y, w, 14), "Fullscreen", s["fullscreen"])
        if fs != s["fullscreen"]:
            s["fullscreen"] = fs
            app.apply_window()
        y += 18
        s["show_fps"] = ui.toggle(view, (x, y, w // 2 - 4, 14), "Show FPS",
                                  s["show_fps"])
        cap = int(s.get("fps_cap", 144))
        cap_label = "Uncapped" if cap == 0 else f"{cap} fps"
        ui.selector(view, (x + w // 2 + 4, y - 9, w // 2 - 4, 22), "FPS CAP",
                    cap_label, lambda: self._cycle_cap(-1),
                    lambda: self._cycle_cap(1))
        y += 18
        scale = int(s.get("render_scale", 3))
        ui.selector(view, (x, y - 4, w // 2 - 4, 22), "WINDOW SIZE",
                    f"{GRID_W * scale}x{GRID_H * scale}",
                    lambda: self._cycle_scale(-1), lambda: self._cycle_scale(1))
        y += 24
        if ui.button(view, (GRID_W // 2 - 40, GRID_H - 28, 80, 16), "BACK"):
            save_settings(s)
            app.goto(MainMenu(app))

    def _cycle_cap(self, d):
        s = self.app.settings
        cur = int(s.get("fps_cap", 144))
        i = FPS_CAPS.index(cur) if cur in FPS_CAPS else 2
        s["fps_cap"] = FPS_CAPS[(i + d) % len(FPS_CAPS)]

    def _cycle_scale(self, d):
        s = self.app.settings
        scales = [2, 3, 4]
        cur = int(s.get("render_scale", 3))
        i = scales.index(cur) if cur in scales else 1
        s["render_scale"] = scales[(i + d) % len(scales)]
        self.app.apply_window()


HELP_TEXT = [
    ("MOVE", "Arrows / WASD walk & aim. ENTER jump, BACKSPACE backflip."),
    ("FIGHT", "SPACE: hold to charge, release to fire. TAB or right-click"),
    ("", "opens the arsenal. Click-weapons (airstrike, teleport...) fire"),
    ("", "where you left-click."),
    ("WORLD", "Everything is simulated. Water flows, oil burns, acid eats"),
    ("", "terrain, gas explodes, lava melts, ice freezes, electricity"),
    ("", "travels through water and metal. Use the world as a weapon."),
    ("COMBOS", "Spill oil then spark it. Freeze water to build bridges."),
    ("", "Flood tunnels. Drop lava into bunkers. Open gas pockets near"),
    ("", "campers. Black holes eat everything. Be creative. Be cruel."),
    ("WIN", "Last team standing wins. After sudden death the world floods."),
    ("CRATES", "Falling crates hold weapons or health. Or a trap. Gamble!"),
]


class HelpScreen(Screen):
    def update(self, events):
        self.app.step_demo()

    def draw(self, view):
        app, ui = self.app, self.app.ui
        app.renderer.render_game(app.demo.game, hud=False)
        ui.panel(view, (20, 8, GRID_W - 40, GRID_H - 16), "HOW TO PLAY")
        y = 32
        for head, line in HELP_TEXT:
            if head:
                ui.label(view, 32, y, head, ACCENT, ui.font)
            ui.label(view, 70, y, line, FG, ui.font)
            y += 11
        if ui.button(view, (GRID_W // 2 - 40, GRID_H - 26, 80, 16), "BACK"):
            app.goto(MainMenu(app))


# ---------------------------------------------------------------- online ---
class OnlineMenu(Screen):
    def __init__(self, app):
        super().__init__(app)
        self.code = ""
        self.active_field = None
        self.error = ""

    def update(self, events):
        self.app.step_demo()

    def draw(self, view):
        app, ui = self.app, self.app.ui
        s = app.settings
        app.renderer.render_game(app.demo.game, hud=False)
        ui.panel(view, (GRID_W // 2 - 100, 20, 200, GRID_H - 50),
                 "PLAY ONLINE")
        x, w = GRID_W // 2 - 80, 160
        y = 44
        s["player_name"], a1 = ui.textinput(
            view, (x, y, w, 24), "YOUR NAME", s["player_name"],
            self.active_field == "name", 12)
        if a1:
            self.active_field = "name"
        y += 32
        s["server"], a2 = ui.textinput(
            view, (x, y, w, 24), "SERVER (host:port)", s["server"],
            self.active_field == "server", 24)
        if a2:
            self.active_field = "server"
        y += 36
        if ui.button(view, (x, y, w, 16), "CREATE PRIVATE LOBBY", accent=True):
            self._connect(create=True)
        y += 26
        self.code, a3 = ui.textinput(view, (x, y, 70, 24), "ROOM CODE",
                                     self.code,
                                     self.active_field == "code", 4, upper=True)
        if a3:
            self.active_field = "code"
        if ui.button(view, (x + 80, y + 9, 80, 15), "JOIN"):
            self._connect(create=False)
        y += 34
        if self.error:
            ui.label(view, GRID_W // 2, y, self.error, (255, 100, 90),
                     ui.font, center=True)
        if ui.button(view, (GRID_W // 2 - 40, GRID_H - 26, 80, 16), "BACK"):
            app.goto(MainMenu(app))

    def _connect(self, create):
        app = self.app
        save_settings(app.settings)
        try:
            host, _, port = app.settings["server"].partition(":")
            sess = net_mod.Session(host, int(port or 31999),
                                   app.settings["player_name"] or "Grub")
            if create:
                sess.create_room()
            else:
                if len(self.code) != 4:
                    self.error = "enter a 4-letter room code"
                    return
                sess.join_room(self.code)
            if sess.started:
                app.goto(SyncScreen(app, sess))     # rejoin a running match
            else:
                app.goto(LobbyScreen(app, sess))
        except Exception as e:
            self.error = f"can't reach server: {e}"[:48]


class SyncScreen(Screen):
    """Rejoining a running match: ask the host for a snapshot and wait."""
    def __init__(self, app, sess):
        super().__init__(app)
        self.sess = sess
        self.t = 0
        sess.request_snapshot()

    def update(self, events):
        self.app.step_demo()
        self.t += 1
        self.sess.poll()
        snap = self.sess.pending_snapshot
        if snap is not None:
            self.sess.pending_snapshot = None
            settings = snap["settings"]
            game = Game(settings)
            game.restore(snap["snap"])
            for t, ctrl in zip(game.teams, snap["controls"]):
                t.control = ctrl
            self.sess.drop_old_inputs(game.tick + 1)
            self.app.goto(GameScreen(self.app, settings, self.sess, game))
        elif self.t > 60 * 30:
            self.sess.close()
            self.app.goto(OnlineMenu(self.app))

    def draw(self, view):
        app, ui = self.app, self.app.ui
        app.renderer.render_game(app.demo.game, hud=False)
        ui.panel(view, (GRID_W // 2 - 90, 90, 180, 60), "REJOINING")
        dots = "." * (self.t // 30 % 4)
        ui.label(view, GRID_W // 2, 120, f"syncing match state{dots}",
                 ACCENT2, ui.font_m, center=True)
        if ui.button(view, (GRID_W // 2 - 30, 128, 60, 14), "CANCEL"):
            self.sess.close()
            app.goto(OnlineMenu(app))


class LobbyScreen(Screen):
    def __init__(self, app, sess):
        super().__init__(app)
        self.sess = sess
        self.biome_i = 0
        self.bots = 0
        self.error = ""

    def update(self, events):
        self.app.step_demo()
        msgs = self.sess.poll()
        for m in msgs:
            if m["t"] == "start":
                self.app.goto(GameScreen(self.app, m["settings"], self.sess))
                return
            if m["t"] == "error":
                self.error = m.get("msg", "error")
            if m["t"] == "closed":
                self.app.goto(OnlineMenu(self.app))
                return

    def draw(self, view):
        app, ui = self.app, self.app.ui
        app.renderer.render_game(app.demo.game, hud=False)
        ui.panel(view, (GRID_W // 2 - 110, 14, 220, GRID_H - 28),
                 "PRIVATE LOBBY")
        ui.label(view, GRID_W // 2, 36, "ROOM CODE", DIM, ui.font, center=True)
        ui.title(view, GRID_W // 2, 42, self.sess.code or "....")
        ui.label(view, GRID_W // 2, 80, "tell your friends. they join with it.",
                 DIM, ui.font, center=True)
        y = 95
        for p in self.sess.roster:
            tag = " (host)" if p["pid"] == self.sess.host_pid else ""
            me = " <- you" if p["pid"] == self.sess.pid else ""
            col = app.renderer.team_color(p["pid"] % 8)
            pygame.draw.rect(view, col, (GRID_W // 2 - 90, y + 2, 6, 6))
            ui.label(view, GRID_W // 2 - 80, y, p["name"] + tag + me, FG,
                     ui.font_m)
            y += 13
        if self.sess.is_host():
            bl = list(BIOMES)
            ui.selector(view, (GRID_W // 2 - 90, y + 4, 110, 20), "ARENA",
                        BIOME_LABELS[bl[self.biome_i]][0],
                        lambda: self._cyc(-1), lambda: self._cyc(1))
            ui.selector(view, (GRID_W // 2 + 30, y + 4, 60, 20), "BOTS",
                        str(self.bots),
                        lambda: self._cb(-1), lambda: self._cb(1))
            if len(self.sess.roster) >= 1 and \
                    ui.button(view, (GRID_W // 2 - 45, GRID_H - 46, 90, 16),
                              "START MATCH", accent=True):
                self._start()
        else:
            ui.label(view, GRID_W // 2, y + 8, "waiting for host to start...",
                     ACCENT2, ui.font, center=True)
        if self.error:
            ui.label(view, GRID_W // 2, GRID_H - 58, self.error,
                     (255, 100, 90), ui.font, center=True)
        if ui.button(view, (GRID_W // 2 - 40, GRID_H - 26, 80, 14), "LEAVE"):
            self.sess.close()
            app.goto(OnlineMenu(app))

    def _cyc(self, d):
        self.biome_i = (self.biome_i + d) % len(BIOMES)

    def _cb(self, d):
        self.bots = max(0, min(6, self.bots + d))

    def _start(self):
        teams = []
        for i, p in enumerate(self.sess.roster):
            teams.append({"name": p["name"], "color_idx": i % 8,
                          "n_grubs": 3, "control": f"net:{p['pid']}"})
        for b in range(self.bots):
            teams.append({"name": f"Bot squad {b + 1}",
                          "color_idx": (len(teams)) % 8, "n_grubs": 3,
                          "control": "bot:normal"})
        settings = {
            "seed": random.randint(0, 10 ** 9),
            "biome": list(BIOMES)[self.biome_i],
            "teams": teams,
        }
        self.sess.send({"t": "start", "settings": settings})


# ================================================================== app ====
class App:
    """Fixed 60 Hz simulation, render as fast as the cap allows. Screens
    run their sims `app.sim_steps` times per rendered frame."""
    def __init__(self):
        pygame.init()
        pygame.display.set_caption("GRUBSTORM — every pixel is alive")
        self.settings = load_settings()
        self.screen_surf = None
        self.crt = None
        self.apply_window()
        self.audio = Audio(self.settings)
        self.ui = UI(self.audio)
        self.renderer = Renderer(self.settings)
        self.demo = MenuDemo()
        self.screen: Screen = MainMenu(self)
        self.running = True
        self.clock = pygame.time.Clock()
        self.sim_steps = 1
        self._acc = 0.0
        from .pixelfont import PixelFont
        self._fps_font = PixelFont(1)

    def apply_window(self):
        scale = int(self.settings.get("render_scale", 3))
        flags = pygame.RESIZABLE
        size = (GRID_W * scale, GRID_H * scale)
        if self.settings.get("fullscreen"):
            flags = pygame.FULLSCREEN | pygame.SCALED
        self.screen_surf = pygame.display.set_mode(size, flags)
        self.crt = CRT(self.settings, scale)

    def goto(self, screen):
        self.screen = screen

    def quit(self):
        self.running = False

    def step_demo(self):
        for _ in range(self.sim_steps):
            self.demo.step()

    def run(self):
        while self.running:
            cap = int(self.settings.get("fps_cap", 144))
            dt = self.clock.tick(cap) / 1000.0
            # fixed-timestep accumulator: sim always runs at 60 Hz
            self._acc += dt
            self.sim_steps = 0
            while self._acc >= 1 / 60 and self.sim_steps < 2:
                self._acc -= 1 / 60
                self.sim_steps += 1
            if self._acc > 0.1:         # spiraled — drop the backlog rather
                self._acc = 0.0         # than stutter (brief slow-mo instead)
            events = pygame.event.get()
            for e in events:
                if e.type == pygame.QUIT:
                    self.running = False
            self.ui.begin(events)
            self.screen.update(events)
            view = self.renderer.view
            self.screen.draw(view)
            if self.settings.get("show_fps"):
                fps = self._fps_font.render(f"{self.clock.get_fps():.0f}",
                                            True, (120, 255, 120))
                view.blit(fps, (GRID_W - fps.get_width() - 2, GRID_H - 12))
            self.crt.present(view, self.screen_surf)
            pygame.display.flip()
        save_settings(self.settings)
        pygame.quit()


def main():
    App().run()
