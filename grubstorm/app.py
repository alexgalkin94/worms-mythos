"""Application shell: window, screens, settings, the main loop."""
import json
import math
import os
import random

import pygame

from .constants import (GRID_W, GRID_H, TEAM_COLORS, TEAM_NAME_POOL,
                        TURN_SECONDS)
from .game import Game, InputFrame
from .ai import Bot
from .render import Renderer
from .crt import CRT, CRT_PRESETS, CRT_PARAMS, migrate_crt_settings
from .audio import Audio
from .ui import UI, ACCENT, ACCENT2, FG, DIM
from .mapgen import BIOMES, BIOME_LABELS
from .weapons import WEAPONS
from .icons import weapon_icon
from . import sandbox as sandbox_mod
from . import net as net_mod
from .music import MusicPlayer, BIOME_MOOD

SETTINGS_PATH = os.path.join(os.path.expanduser("~"), ".grubstorm.json")

DEFAULT_SETTINGS = {
    "shake": 1.0, "volume": 0.8, "music": 0.6,
    "reduce_flash": False, "colorblind": False,
    "fullscreen": False, "server": "127.0.0.1:31999", "player_name": "Grub",
    "fps_cap": 144, "show_fps": False, "render_scale": 3,
    "gpu_crt": True,
    **CRT_PRESETS["ARCADE"],
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
    migrate_crt_settings(s)
    # sanitize: a hand-edited or stale settings file must never be able to
    # crash a slider or the CRT pipeline. Coerce types, kill NaN/inf, clamp.
    def _num(key, lo, hi, default):
        try:
            x = float(s.get(key, default))
        except (TypeError, ValueError):
            x = float(default)
        if x != x or x in (float("inf"), float("-inf")):
            x = float(default)
        s[key] = min(hi, max(lo, x))
    for k in CRT_PARAMS:
        _num(k, 0.0, 1.0, DEFAULT_SETTINGS.get(k, 0.0))
    _num("bloom", 0.0, 1.0, DEFAULT_SETTINGS.get("bloom", 0.3))
    _num("shake", 0.0, 2.0, 1.0)
    _num("volume", 0.0, 1.0, 0.8)
    _num("music", 0.0, 1.0, 0.6)
    for k in ("reduce_flash", "colorblind", "fullscreen", "show_fps",
              "gpu_crt"):
        s[k] = bool(s.get(k, DEFAULT_SETTINGS[k]))
    try:
        s["fps_cap"] = int(s.get("fps_cap", 144))
    except (TypeError, ValueError):
        s["fps_cap"] = 144
    if s["fps_cap"] not in FPS_CAPS:
        s["fps_cap"] = 144
    try:
        s["render_scale"] = int(s.get("render_scale", 3))
    except (TypeError, ValueError):
        s["render_scale"] = 3
    if s["render_scale"] not in (2, 3, 4):
        s["render_scale"] = 3
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
        self.mouse_aim = True
        self._fire_block = False
        self._jump = False
        self._backflip = False
        self._last_mouse = pygame.mouse.get_pos()

    # ----------------------------------------------------------- input
    def _mouse_world(self):
        mx, my = pygame.mouse.get_pos()
        win = pygame.display.get_surface().get_size()
        sx = mx * GRID_W / max(1, win[0])
        sy = my * GRID_H / max(1, win[1])
        cam = self.app.renderer.camera
        if cam.zoom <= 1.001:
            return (sx, sy)
        w = GRID_W / cam.zoom
        h = GRID_H / cam.zoom
        x0 = min(max(cam.cx - w / 2, 0), GRID_W - w)
        y0 = min(max(cam.cy - h / 2, 0), GRID_H - h)
        return (x0 + sx / cam.zoom, y0 + sy / cam.zoom)

    def _local_input(self):
        keys = pygame.key.get_pressed()
        mouse = pygame.mouse.get_pressed()
        inp = InputFrame()
        inp.left = keys[pygame.K_LEFT] or keys[pygame.K_a]
        inp.right = keys[pygame.K_RIGHT] or keys[pygame.K_d]
        if not self.mouse_aim:
            inp.aim_up = keys[pygame.K_UP] or keys[pygame.K_w]
            inp.aim_down = keys[pygame.K_DOWN] or keys[pygame.K_s]
        spec = WEAPONS[self.game.weapon]
        if not mouse[0]:
            self._fire_block = False
        # click-weapons fire where you click — except the homing missile,
        # which becomes a charge weapon once its target is locked
        click_style = spec.target == "click" and not (
            spec.key == "homing"
            and getattr(self.game, "_homing_target", None) is not None)
        inp.fire = keys[pygame.K_f] or (
            mouse[0] and not click_style and not self.panel_open
            and not self.paused and not self._fire_block)
        inp.jump = self._jump
        inp.backflip = self._backflip
        self._jump = self._backflip = False     # consumed by this tick
        inp.weapon = self.pending_weapon
        inp.click = self.pending_click
        # mouse aiming: absolute angle from the active grub to the cursor,
        # quantized exactly like the net encoding so lockstep stays bit-true
        g = self.game.active_grub
        if self.mouse_aim and g is not None:
            import math
            gx, gy = self._mouse_world()
            a = math.atan2(gy - g.y, gx - g.x)
            inp.aim = round(a * 1000) / 1000.0
        self.pending_weapon = -1
        self.pending_click = None
        return inp

    def _cycle_weapon(self, d):
        g = self.game
        team = g.current_team()
        n = len(WEAPONS)
        i = g.weapon
        for _ in range(n):
            i = (i - d) % n
            if team.ammo.get(i, 0) != 0:
                self.pending_weapon = i
                return

    def update(self, events):
        app = self.app
        # NOTE: edge inputs (_jump/_backflip/_fire_block/pending_*) are
        # latched here and consumed by sim ticks. At high FPS most frames
        # have NO sim tick — resetting them per frame would eat inputs.
        for e in events:
            if e.type == pygame.KEYDOWN:
                if e.key == pygame.K_ESCAPE:
                    if self.panel_open:
                        self.panel_open = False
                    else:
                        self.paused = not self.paused
                elif e.key in (pygame.K_SPACE, pygame.K_RETURN):
                    self._jump = True
                elif e.key in (pygame.K_BACKSPACE, pygame.K_LSHIFT):
                    self._backflip = True
                elif e.key in (pygame.K_w, pygame.K_s, pygame.K_UP,
                               pygame.K_DOWN):
                    self.mouse_aim = False      # keys take over the aim
                elif e.key == pygame.K_q:
                    self._cycle_weapon(-1)
                elif e.key == pygame.K_e:
                    self._cycle_weapon(1)
                elif e.key in (pygame.K_HOME, pygame.K_n):
                    app.renderer.camera.follow = True
                elif e.key == pygame.K_TAB:
                    self.panel_open = not self.panel_open
            elif e.type == pygame.MOUSEMOTION:
                lx, ly = self._last_mouse
                if abs(e.pos[0] - lx) + abs(e.pos[1] - ly) > 3:
                    self.mouse_aim = True       # ...until the mouse moves
                    self._last_mouse = e.pos
                if e.buttons[1]:                # middle-drag pans the camera
                    cam = app.renderer.camera
                    win = pygame.display.get_surface().get_size()
                    cam.cx -= e.rel[0] * GRID_W / max(1, win[0]) / cam.zoom
                    cam.cy -= e.rel[1] * GRID_H / max(1, win[1]) / cam.zoom
                    cam.follow = False
            elif e.type == pygame.MOUSEWHEEL:
                if self.panel_open:
                    self.panel_scroll = getattr(self, "panel_scroll", 0) - \
                        e.y * 20
                elif not self.paused:           # wheel zooms the battlefield
                    cam = app.renderer.camera
                    cam.zoom = max(1.0, min(2.5, cam.zoom * (1.12 ** e.y)))
            elif e.type == pygame.MOUSEBUTTONUP:
                if e.button == 1:
                    self._fire_block = False
            elif e.type == pygame.MOUSEBUTTONDOWN:
                if e.button == 3:
                    self.panel_open = not self.panel_open
                elif e.button == 1 and self.panel_open:
                    self._fire_block = True     # selection click, not a shot
                elif e.button == 1 and not self.panel_open and not self.paused:
                    spec = WEAPONS[self.game.weapon]
                    if spec.target == "click":
                        gx, gy = self._mouse_world()
                        self.pending_click = (int(gx), int(gy))
        if self.paused:
            return
        # classic Worms edge pan: cursor against the window border
        cam = app.renderer.camera
        if not self.panel_open and pygame.mouse.get_focused():
            mx, my = pygame.mouse.get_pos()
            win = pygame.display.get_surface().get_size()
            pan = 2.6 / cam.zoom
            if mx < 10: cam.cx -= pan; cam.follow = False
            elif mx > win[0] - 10: cam.cx += pan; cam.follow = False
            if my < 10: cam.cy -= pan; cam.follow = False
            elif my > win[1] - 10: cam.cy += pan; cam.follow = False
        if self.game.turn_no != getattr(self, "_seen_turn", -1):
            self._seen_turn = self.game.turn_no
            cam.follow = True                   # new turn recaptures camera
        # dramatic finish: brief slow motion + zoom onto the final blow
        if self.game.phase == Game.PH_OVER and self.over_t < 80:
            cam.zoom = min(2.2, cam.zoom + 0.012)
            if app.ui.t % 3:
                return
        for _ in range(app.sim_steps):
            self._tick_once()
            self._jump = self._backflip = False
            if app.screen is not self:          # match ended mid-frame
                return
        # offline fast-forward: hold SPACE while the dust settles
        if self.session is None and \
                self.game.phase in (Game.PH_RESOLVE, Game.PH_TURNEND) and \
                pygame.key.get_pressed()[pygame.K_SPACE]:
            for _ in range(2):
                self._tick_once()
                if app.screen is not self:
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
            if self.session and g.tick % 600 == 0 and g.tick > 0:
                import hashlib
                h = hashlib.sha256(g.world.mat.tobytes()).hexdigest()[:12]
                self.session.check_state(g.tick, h)
                # late comparison: a peer hash may arrive after our tick
                peer = self.session.peer_hashes.get(g.tick)
                own = self.session.own_hashes.get(g.tick)
                if peer is not None and own is not None and peer != own:
                    self.session.desynced = True
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
        if self.session and self.session.desynced:
            ui.label(view, GRID_W // 2, GRID_H - 62,
                     "DESYNC DETECTED — please restart the match",
                     (255, 90, 80), ui.font_m, center=True)
        if self.paused:
            ui.panel(view, (GRID_W // 2 - 60, 60, 120, 132), "PAUSED")
            y = 85
            if ui.button(view, (GRID_W // 2 - 45, y, 90, 15), "RESUME"):
                self.paused = False
            y += 20
            if ui.button(view, (GRID_W // 2 - 45, y, 90, 15), "OPTIONS"):
                app.goto(OptionsScreen(app, back_fn=lambda: app.goto(self),
                                       bg_game=self.game))
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
        panel = pygame.Rect(GRID_W - 150, 14, 144, GRID_H - 28)
        ui.panel(view, panel, "ARSENAL")
        inner = pygame.Rect(panel.x + 4, panel.y + 22, panel.w - 10,
                            panel.h - 28)
        cats = [("boom", "BOOM"), ("chem", "CHEMISTRY"),
                ("energy", "ENERGY"), ("super", "SUPER"), ("move", "MOVE")]
        mine = not g.teams[g.turn_team].control.startswith("net:") or \
            (self.session and
             g.teams[g.turn_team].control == f"net:{self.session.pid}")
        # flat row list, then a scrolling window over it
        rows = []
        for ckey, clabel in cats:
            rows.append(("cat", clabel))
            for i, w in enumerate(WEAPONS):
                if w.category == ckey and team.ammo.get(i, 0) != 0:
                    rows.append(("w", i))
            rows.append(("gap", None))
        row_h = 12
        content_h = len(rows) * row_h
        max_scroll = max(0, content_h - inner.h)
        self.panel_scroll = max(0, min(getattr(self, "panel_scroll", 0),
                                       max_scroll))
        view.set_clip(inner)
        y = inner.y - self.panel_scroll
        for kind, payload in rows:
            if y > inner.bottom:
                break
            if y + row_h >= inner.y:
                if kind == "cat":
                    ui.label(view, inner.x + 2, y + 2, payload, ACCENT,
                             ui.font)
                elif kind == "w":
                    i = payload
                    w = WEAPONS[i]
                    ammo = team.ammo.get(i, 0)
                    ammo_s = "" if ammo < 0 else f" x{ammo}"
                    sel = i == g.weapon
                    r = pygame.Rect(inner.x, y, inner.w - 4, row_h)
                    hov = r.collidepoint(ui.mx, ui.my)
                    if sel:
                        pygame.draw.rect(view, (52, 38, 18), r)
                    elif hov:
                        pygame.draw.rect(view, (36, 30, 24), r)
                    col = ACCENT if sel else (FG if hov else DIM)
                    view.blit(weapon_icon(w.key), (r.x + 1, y + 1))
                    ui.label(view, r.x + 16, y + 3, w.name + ammo_s, col,
                             ui.font)
                    if hov and ui.clicked and mine:
                        self.pending_weapon = i
                        self.panel_open = False
            y += row_h
        view.set_clip(None)
        # scrollbar
        if max_scroll > 0:
            track = pygame.Rect(panel.right - 5, inner.y, 3, inner.h)
            pygame.draw.rect(view, (30, 26, 20), track)
            knob_h = max(8, inner.h * inner.h // content_h)
            knob_y = track.y + (track.h - knob_h) * self.panel_scroll // max_scroll
            pygame.draw.rect(view, (150, 124, 78),
                             (track.x, knob_y, 3, knob_h))
            hint = ui.font.render("scroll", True, DIM)
            view.blit(hint, (panel.centerx - hint.get_width() // 2,
                             panel.bottom - 9))


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
        y = 66
        hdr = "TEAM            HP  ALIVE  DMG  KILLS  SHOTS  BEST HIT"
        ui.label(view, GRID_W // 2 - 100, y, hdr, DIM, ui.font)
        y += 11
        for t in sorted(g.teams, key=lambda t: -t.total_hp()):
            col = app.renderer.team_color(t.color_idx)
            pygame.draw.rect(view, col, (GRID_W // 2 - 108, y + 1, 5, 5))
            row = (f"{t.name[:14]:14s} {int(t.total_hp()):4d}  "
                   f"{len(t.alive_grubs()):3d}  {int(t.damage_dealt):4d}  "
                   f"{t.kills:3d}   {t.shots:3d}    {int(t.max_hit):3d}")
            ui.label(view, GRID_W // 2 - 100, y, row, FG, ui.font)
            y += 11
        # fallen heroes
        dead = [gr.name for t in g.teams for gr in t.grubs if not gr.alive]
        if dead:
            y += 6
            ui.label(view, GRID_W // 2, y, "IN MEMORIAM", DIM, ui.font,
                     center=True)
            y += 10
            ui.label(view, GRID_W // 2, y, ", ".join(dead[:8]), ACCENT2,
                     ui.font, center=True)
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
    """Modern tabbed options. The CRT tab pairs every tube parameter with
    a live preview pane — a worm sprinting around with bright sparks — so
    smear, phosphor trails and halation can actually be judged while you
    drag the slider. All sliders are continuous 0–100."""

    TABS = ["CRT", "VIDEO", "AUDIO", "GAMEPLAY"]

    CRT_SLIDERS = [
        ("crt_smear", "Beam smear"), ("crt_scanline", "Scanlines"),
        ("crt_mask", "Slot mask"), ("crt_fringe", "Color fringe"),
        ("crt_halation", "Halation"), ("crt_persist", "Phosphor trail"),
        ("crt_flicker", "Flicker"), ("crt_vignette", "Vignette"),
        ("crt_curve", "Curvature"), ("bloom", "Bloom"),
    ]

    # one-line "what does this actually look like" per dial
    HINTS = {
        "crt_smear": "Sideways blur that melts hard pixels together.",
        "crt_scanline": "Dark horizontal lines between picture rows.",
        "crt_mask": "Fine phosphor dot grid over the whole image.",
        "crt_fringe": "Red/blue ghost edges on hard contrasts.",
        "crt_halation": "Bright spots glow softly into the glass.",
        "crt_persist": "Moving lights leave fading trails (ghosting).",
        "crt_flicker": "Gentle brightness shimmer, like mains hum.",
        "crt_vignette": "Darkens the corners of the screen.",
        "crt_curve": "Bends the image like curved tube glass.",
        "bloom": "Bright pixels bleed light outward.",
        "preset": "Pre-mixed looks - pick one, then fine-tune below.",
        "shake": "Camera kick on explosions and impacts.",
        "reduce_flash": "Kills flashes, flicker and phosphor trails.",
        "colorblind": "Alternative team colors, easier to tell apart.",
        "fullscreen": "Borderless fullscreen on / windowed off.",
        "show_fps": "Tiny frame counter in the corner.",
        "fps_cap": "Render speed limit. Sim always runs at 60.",
        "render_scale": "Window size when not fullscreen.",
        "volume": "Loudness of effects: booms, splats, clicks.",
        "music": "Loudness of the soundtrack.",
    }

    def __init__(self, app, back_fn=None, bg_game=None):
        super().__init__(app)
        self.back_fn = back_fn or (lambda: app.goto(MainMenu(app)))
        self.bg_game = bg_game
        self.tab = "CRT"
        self.preset_open = False
        self._pt = 0                       # preview animation clock

    def update(self, events):
        if self.bg_game is None:
            self.app.step_demo()

    def _preset_name(self):
        s = self.app.settings
        for name, vals in CRT_PRESETS.items():
            if all(abs(float(s.get(k, 0)) - v) < 0.03 for k, v in vals.items()):
                return name
        return None

    def _hover_hint(self, key, rect):
        if self.app.ui._hover(pygame.Rect(rect)):
            self._hint = self.HINTS.get(key)

    # continuous 0–100 slider over any numeric setting
    def _pct(self, view, rect, label, key, lo=0.0, hi=1.0, locked=False):
        self._hover_hint(key, rect)
        ui, s = self.app.ui, self.app.settings
        try:
            val = float(s.get(key, lo))
        except (TypeError, ValueError):
            val = lo
        val = min(hi, max(lo, val))
        pct = (val - lo) / (hi - lo) * 100.0
        nv = ui.slider(view, rect, f"{label}: {pct:.0f}", val, lo, hi)
        if not locked:                    # an open dropdown overlays these
            s[key] = round(min(hi, max(lo, nv)), 3)

    def draw(self, view):
        app, ui = self.app, self.app.ui
        s = app.settings
        game = self.bg_game or app.demo.game
        app.renderer.render_game(game, hud=False)
        ui.panel(view, (6, 4, GRID_W - 12, GRID_H - 8), "OPTIONS")
        # ---------------------------------------------------- tab strip
        tx = 16
        for name in self.TABS:
            wbtn = len(name) * 4 + 14
            if ui.button(view, (tx, 20, wbtn, 13), name,
                         accent=(name == self.tab), font=ui.font):
                self.tab = name
                self.preset_open = False
                save_settings(s)
            tx += wbtn + 5
        y0 = 44
        self._hint = None
        if self.tab == "CRT":
            self._draw_crt_tab(view, y0)
        elif self.tab == "VIDEO":
            self._draw_video_tab(view, y0)
        elif self.tab == "AUDIO":
            self._draw_audio_tab(view, y0)
        else:
            self._draw_gameplay_tab(view, y0)
        # what-does-this-do line for whatever the mouse is over
        ui.label(view, 16, GRID_H - 34,
                 self._hint or "Hover a control to see what it changes.",
                 FG if self._hint else DIM, ui.font)
        if ui.button(view, (GRID_W // 2 - 40, GRID_H - 22, 80, 14), "BACK"):
            save_settings(s)
            self.back_fn()

    # ------------------------------------------------------------- tabs
    def _draw_crt_tab(self, view, y0):
        app, ui = self.app, self.app.ui
        s = app.settings
        lx = 16
        # preset dropdown (list drawn last so it overlays the sliders)
        ui.label(view, lx, y0, "PRESET", DIM, ui.font)
        active = self._preset_name() or "CUSTOM"
        dd_rect = pygame.Rect(lx, y0 + 9, 104, 13)
        self._hover_hint("preset", dd_rect)
        if ui.button(view, dd_rect, f"{active}  {'^' if self.preset_open else 'v'}",
                     accent=True, font=ui.font):
            self.preset_open = not self.preset_open
        ui.label(view, lx + 116, y0 + 12,
                 "every dial below, pre-mixed", DIM, ui.font)
        sy = y0 + 32
        for i, (key, label) in enumerate(self.CRT_SLIDERS):
            col_x = lx + (i % 2) * 112
            row_y = sy + (i // 2) * 26
            self._pct(view, (col_x, row_y, 100, 18), label, key,
                      locked=self.preset_open)
        # live preview pane
        self._draw_preview(view, pygame.Rect(252, y0 + 6, 208, 152))
        ui.label(view, 252 + 104, y0 + 162, "LIVE PREVIEW", DIM, ui.font,
                 center=True)
        if self.preset_open:
            oy = dd_rect.bottom + 1
            inside = ui._hover(dd_rect)
            for name in CRT_PRESETS:
                r = pygame.Rect(dd_rect.x, oy, dd_rect.w, 12)
                inside = inside or ui._hover(r)
                if ui.button(view, r, name, accent=(name == active),
                             font=ui.font):
                    s.update(CRT_PRESETS[name])
                    self.preset_open = False
                oy += 13
            if ui.clicked and not inside:
                self.preset_open = False

    def _draw_video_tab(self, view, y0):
        app, ui = self.app, self.app.ui
        s = app.settings
        lx, rx = 16, 132
        self._hover_hint("fullscreen", (lx, y0, 104, 13))
        self._hover_hint("show_fps", (rx, y0, 104, 13))
        fs = ui.toggle(view, (lx, y0, 104, 13), "Fullscreen", s["fullscreen"])
        if fs != s["fullscreen"]:
            s["fullscreen"] = fs
            app.apply_window()
        s["show_fps"] = ui.toggle(view, (rx, y0, 104, 13), "Show FPS",
                                  s["show_fps"])
        y = y0 + 26
        self._hover_hint("fps_cap", (lx, y, 104, 22))
        self._hover_hint("render_scale", (rx, y, 104, 22))
        cap = int(s.get("fps_cap", 144))
        ui.selector(view, (lx, y, 104, 22), "FPS CAP",
                    "Uncapped" if cap == 0 else f"{cap} fps",
                    lambda: self._cycle_cap(-1), lambda: self._cycle_cap(1))
        scale = int(s.get("render_scale", 3))
        ui.selector(view, (rx, y, 104, 22), "WINDOW",
                    f"{GRID_W * scale}x{GRID_H * scale}",
                    lambda: self._cycle_scale(-1), lambda: self._cycle_scale(1))
        ui.label(view, lx, y + 34,
                 "The CRT tab has its own page of tube dials.", DIM, ui.font)

    def _draw_audio_tab(self, view, y0):
        view_ = view
        self._pct(view_, (16, y0 + 6, 130, 18), "SFX volume", "volume")
        self._pct(view_, (16, y0 + 36, 130, 18), "Music volume", "music")

    def _draw_gameplay_tab(self, view, y0):
        ui, s = self.app.ui, self.app.settings
        self._pct(view, (16, y0 + 6, 130, 18), "Screen shake", "shake",
                  0.0, 2.0)
        self._hover_hint("reduce_flash", (16, y0 + 34, 130, 13))
        self._hover_hint("colorblind", (16, y0 + 52, 130, 13))
        s["reduce_flash"] = ui.toggle(view, (16, y0 + 34, 130, 13),
                                      "Reduce flashing", s["reduce_flash"])
        s["colorblind"] = ui.toggle(view, (16, y0 + 52, 130, 13),
                                    "Colorblind colors", s["colorblind"])

    # ---------------------------------------------------------- preview
    def _draw_preview(self, view, rect):
        """A bright worm sprinting in a dark box: motion for judging the
        phosphor trail and smear, sparks for halation, text and a checker
        patch for mask/scanline sharpness."""
        self._pt += 1
        t = self._pt
        pygame.draw.rect(view, (10, 9, 12), rect)
        pygame.draw.rect(view, (94, 80, 56), rect, 1)
        # checker patch (top-right): pixel sharpness / mask structure
        for cy in range(6):
            for cx in range(10):
                if (cx + cy) % 2:
                    view.fill((188, 178, 150),
                              (rect.right - 46 + cx * 4, rect.y + 6 + cy * 4,
                               4, 4))
        # bright bar sweeping back and forth: THE ghosting test
        f = math.sin(t * 0.045)
        bx = rect.x + 8 + int((f * 0.5 + 0.5) * (rect.w - 20))
        pygame.draw.rect(view, (235, 238, 248), (bx, rect.y + 8, 3, 40))
        # readability line
        s = self.app.ui.font.render("THE QUICK GRUB JUMPS OVER IT", True,
                                    (224, 210, 178))
        view.blit(s, (rect.x + 8, rect.y + 56))
        # sparks on an orbit: halation / bloom
        for k in range(3):
            a = t * 0.09 + k * 2.1
            sx = rect.centerx + math.cos(a) * 34
            sy = rect.y + 92 + math.sin(a) * 10
            pygame.draw.circle(view, (255, 240, 180), (sx, sy), 1)
        # the sprinting worm
        span = rect.w - 56
        ph = math.sin(t * 0.022)
        wx = rect.x + 28 + (ph * 0.5 + 0.5) * span
        face = 1 if math.cos(t * 0.022) > 0 else -1
        hop = abs(math.sin(t * 0.13)) * 7
        wy = rect.bottom - 18 - hop
        segs = []
        for i in range(5):
            sx = wx - face * i * 2.4
            sy = wy + (4 - i) * 0.5 + math.sin(t * 0.3 + i * 0.9) * 0.9
            segs.append((sx, sy, 3.0 - i * 0.35))
        for sx, sy, r in segs:
            pygame.draw.circle(view, (16, 12, 22), (sx, sy), r + 0.8)
        for sx, sy, r in segs:
            pygame.draw.circle(view, (208, 84, 70), (sx, sy), r)
        hx, hy = segs[0][0], segs[0][1] - 2
        pygame.draw.rect(view, (250, 250, 252), (hx - 2, hy - 2, 2, 3))
        pygame.draw.rect(view, (250, 250, 252), (hx + 1, hy - 2, 2, 3))

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
    ("MOVE", "A/D walk. SPACE jump (coyote + buffered). SHIFT backflip."),
    ("AIM", "Aim with the mouse. W/S or arrows fine-tune by keyboard."),
    ("FIGHT", "Hold LEFT MOUSE (or F) to charge, release to fire."),
    ("", "Q/E cycle weapons. TAB or right-click: full arsenal."),
    ("", "Click-weapons (airstrike, teleport...) fire where you click."),
    ("WORLD", "Everything is simulated. Water flows, oil burns, acid eats"),
    ("", "terrain, gas explodes, lava melts, ice freezes, electricity"),
    ("", "travels through water and metal. Use the world as a weapon."),
    ("COMBOS", "Spill oil then spark it. Freeze water to build bridges."),
    ("", "Flood tunnels. Drop lava into bunkers. Open gas pockets near"),
    ("", "campers. Black holes eat everything. Be creative. Be cruel."),
    ("CAMERA", "Wheel zooms. Cursor at the edge or middle-drag pans."),
    ("", "HOME or N snaps back to the action."),
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
                if m.get("proto") != net_mod.PROTOCOL:
                    self.error = "version mismatch - update your game!"
                    continue
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
        self.sess.send({"t": "start", "settings": settings,
                        "proto": net_mod.PROTOCOL})


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
        self.music = MusicPlayer(self.settings)
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
        size = (GRID_W * scale, GRID_H * scale)
        if getattr(self, "gpu_win", None) is not None:
            self.gpu_win.destroy()
        self.gpu_win = None
        # GPU compositing: the CRT chain (upscale + MUL/ADD overlay blends,
        # by far the most expensive render stage) runs as texture draws on
        # the graphics card. Needs a window WITHOUT an attached software
        # surface, so it owns window creation; anything failing here falls
        # back to the classic set_mode + CPU compositing path.
        if self.settings.get("gpu_crt", True):
            try:
                from pygame._sdl2.video import Window, Renderer
                if pygame.display.get_surface() is not None:
                    pygame.display.quit()
                    pygame.display.init()
                win = Window("GRUBSTORM — every pixel is alive", size=size,
                             resizable=True,
                             fullscreen_desktop=bool(
                                 self.settings.get("fullscreen")))
                ren = Renderer(win, accelerated=1, vsync=False)
                self.gpu_win = win
                # placeholder so size-derived code keeps working
                self.screen_surf = pygame.Surface(size)
                self.crt = CRT(self.settings, scale)
                self.crt.attach_gpu(ren, win)
                return
            except Exception:
                if self.gpu_win is not None:
                    self.gpu_win.destroy()
                    self.gpu_win = None
                pygame.display.quit()
                pygame.display.init()
        flags = pygame.RESIZABLE
        if self.settings.get("fullscreen"):
            flags = pygame.FULLSCREEN | pygame.SCALED
        self.screen_surf = pygame.display.set_mode(size, flags)
        pygame.display.set_caption("GRUBSTORM — every pixel is alive")
        self.crt = CRT(self.settings, scale)

    def goto(self, screen):
        self.screen = screen

    def quit(self):
        self.running = False

    def _update_ambience(self, mood):
        if mood == getattr(self, "_amb_mood", None):
            if self._amb_ch is not None:
                self._amb_ch.set_volume(
                    float(self.settings.get("volume", 0.8)) * 0.4)
            return
        snd = self.audio.ambience(mood)
        if getattr(self, "_amb_ch", None) is not None:
            self._amb_ch.fadeout(1200)
        self._amb_mood = mood
        self._amb_ch = snd.play(loops=-1, fade_ms=1500) if snd else None

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
            if self.gpu_win is not None:
                self.ui.win_size = self.gpu_win.size
            self.ui.begin(events)
            self.screen.update(events)
            # mood-aware soundtrack: menus get the theme, arenas their tone
            if isinstance(self.screen, GameScreen):
                biome = self.screen.settings.get("biome", "island")
                mood = BIOME_MOOD.get(biome, "warm")
            elif isinstance(self.screen, sandbox_mod.SandboxScreen):
                mood = "deep"
            else:
                mood = "menu"
            self.music.want(mood)
            self.music.update()
            self._update_ambience(mood)
            view = self.renderer.view
            self.screen.draw(view)
            if self.settings.get("show_fps"):
                fps = self._fps_font.render(f"{self.clock.get_fps():.0f}",
                                            True, (120, 255, 120))
                view.blit(fps, (GRID_W - fps.get_width() - 2, GRID_H - 12))
            # GPU mode presents through its renderer; only the CPU path
            # has a display surface to flip
            if not self.crt.present(view, self.screen_surf):
                pygame.display.flip()
        save_settings(self.settings)
        pygame.quit()


def main():
    App().run()
