"""Golden-free pixel determinism for the render pipeline.

No stored golden images — they would break on every intentional visual
change. Instead we assert run-to-run determinism: rendering the same
seeded world state twice (through Renderer.refresh_cells + the real
SandboxScreen.draw composition, inside a real App) must produce identical
pixels, and the CPU CRT present chain must be deterministic once the two
time/randomness-coupled dials (crt_flicker, crt_persist) are pinned to 0.
Surface format invariants the renderer relies on are checked too.

The App is booted headless with A.SETTINGS_PATH redirected to a temp file
so the user's real ~/.grubstorm.json is never read or written.
"""
import sys
import os
import json
import random
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _harness as H

import grubstorm.app as A

# settings file: CPU compositing, deterministic CRT, silent audio
_fd, _settings_path = tempfile.mkstemp(prefix="grubstorm_test_",
                                       suffix=".json")
with os.fdopen(_fd, "w") as f:
    json.dump({
        "gpu_crt": False,          # force the CPU present path
        "fullscreen": False, "show_fps": False,
        "crt_flicker": 0.0,        # random.randint per present
        "crt_persist": 0.0,        # time.monotonic-decayed trails
        "crt_curve": 0.0,
        "volume": 0.0, "music": 0.0,
        "fps_cap": 144, "render_scale": 3,
    }, f)
A.SETTINGS_PATH = _settings_path

r = H.Runner("test_render_exact")

import pygame
from grubstorm import sandbox as sandbox_mod
from grubstorm import materials as M
from grubstorm.constants import GRID_W, GRID_H

t0 = time.perf_counter()
random.seed(2024)                  # App() boots a randomly-seeded MenuDemo
app = A.App()
r.info(f"App() booted headless in {time.perf_counter() - t0:.1f}s")

r.check("App: CPU compositing path active (gpu_win is None)",
        app.gpu_win is None)
r.check("App: settings came from the redirected temp file",
        app.settings.get("crt_flicker") == 0.0
        and app.settings.get("crt_persist") == 0.0
        and app.settings.get("gpu_crt") is False)

# -------------------------------------------------- surface format checks
view = app.renderer.view
r.check("Renderer: view is grid-resolution 32-bit",
        view.get_size() == (GRID_W, GRID_H) and view.get_bitsize() == 32,
        f"{view.get_size()} @{view.get_bitsize()}bpp")
layers = [app.renderer.cell_surf, app.renderer.gas_surf,
          app.renderer.liq_surf, app.renderer.em_surf]
r.check("Renderer: cell/gas/liq/em layers share size + pixel format",
        all(s.get_size() == (GRID_W, GRID_H) for s in layers)
        and len({s.get_shifts() for s in layers}) == 1
        and len({s.get_bitsize() for s in layers}) == 1,
        f"shifts {layers[0].get_shifts()}")
r.check("CRT: scale-3 window surface",
        app.screen_surf.get_size() == (GRID_W * 3, GRID_H * 3)
        and app.crt.scale == 3, f"{app.screen_surf.get_size()}")

# --------------------------------------- two-pass sandbox draw determinism
SCENE_MATS = [M.WATER, M.SAND, M.LAVA, M.OIL, M.ACID,
              M.GAS, M.NITRO, M.EXPOWDER, M.SNOW, M.SLIME]


def render_pass():
    """Fresh SandboxScreen, scripted scene, fixed tick count, one draw.
    Everything seeded; renderer/ui animation clocks reset so flicker
    phases (burning-cell shimmer, blink timers) line up between passes."""
    random.seed(777)               # SandboxScreen seeds its World from this
    sb = sandbox_mod.SandboxScreen(app)
    w = sb.world
    rng = random.Random(3)
    for i in range(60):
        w.paint(rng.randint(40, 340), rng.randint(20, 200),
                rng.randint(3, 8), SCENE_MATS[i % len(SCENE_MATS)],
                mode="fill")
    w.paint(120, 230, 10, M.FIRE, mode="fill")   # exercise burn flicker
    for _ in range(120):
        w.step()
        w.events.clear()
    app.renderer._t = 0
    app.ui.t = 0
    app.ui.begin([])
    sb.draw(app.renderer.view)
    return pygame.image.tobytes(app.renderer.view, "RGB")


t0 = time.perf_counter()
pix1 = render_pass()
pix2 = render_pass()
r.check("Sandbox draw: two fresh seeded passes are pixel-identical",
        pix1 == pix2, f"{len(pix1)} bytes, {time.perf_counter() - t0:.1f}s")
r.check("Sandbox draw: frame is not degenerate (more than 32 colors)",
        len({pix1[i:i + 3] for i in range(0, len(pix1), 3 * 97)}) > 32)

# ----------------------------------- CRT CPU present chain determinism
# present() mutates the view in place (fringe/bloom), so each run gets its
# own copy of the same source frame.
src = app.renderer.view.copy()


def present_pass():
    v = src.copy()
    used_cpu = app.crt.present(v, app.screen_surf) is False
    return used_cpu, pygame.image.tobytes(app.screen_surf, "RGB")


cpu1, out1 = present_pass()
cpu2, out2 = present_pass()
r.check("CRT present: ran on the CPU path both times", cpu1 and cpu2)
r.check("CRT present: identical output with flicker=0, persist=0",
        out1 == out2, f"{len(out1)} bytes at {app.screen_surf.get_size()}")

# format invariant: present must not change the window surface format
r.check("CRT present: window surface format unchanged",
        app.screen_surf.get_bitsize() == 32
        and app.screen_surf.get_size() == (GRID_W * 3, GRID_H * 3))

pygame.quit()
os.unlink(_settings_path)
r.finish()
