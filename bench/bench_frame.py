"""End-to-end frame benchmark through the real App + SandboxScreen path.

    python bench/bench_frame.py            # 1440 frames (~10 s at 144 Hz)
    python bench/bench_frame.py --quick    # 360 frames

Drives the exact per-frame pipeline App.run executes — ui.begin ->
screen.update -> screen.draw -> crt.present — at a forced 144 Hz
accumulator cadence (fixed dt = 1/144, sim still 60 Hz), entirely
headless. The scenario is the lava-spam sweep (two 25-radius lava discs
per sim tick marching across the map), which exercises the sandbox's
frame-budgeted tick slicing.

Reports p50/p90/p99 frame ms plus the update/draw/present split for
tick frames (>=1 sim step scheduled) vs render-only frames.

Settings are redirected to a temp file (CPU CRT compositing, ARCADE
preset visuals) so the user's real ~/.grubstorm.json is untouched.
"""
import json
import os
import random
import statistics
import sys
import tempfile
import time

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import grubstorm.app as A

QUICK = "--quick" in sys.argv[1:]
N_FRAMES = 360 if QUICK else 1440
HZ = 144.0
DT = 1.0 / HZ


def pct(vals, p):
    s = sorted(vals)
    return s[min(len(s) - 1, int(p / 100.0 * len(s)))]


def main():
    fd, settings_path = tempfile.mkstemp(prefix="grubstorm_bench_",
                                         suffix=".json")
    with os.fdopen(fd, "w") as f:
        json.dump({"gpu_crt": False, "fullscreen": False, "show_fps": False,
                   "volume": 0.0, "music": 0.0,
                   "fps_cap": 144, "render_scale": 3}, f)
    A.SETTINGS_PATH = settings_path

    import pygame
    from grubstorm import sandbox as sandbox_mod
    from grubstorm import materials as M

    t0 = time.perf_counter()
    random.seed(99)
    app = A.App()
    print(f"App() boot: {time.perf_counter() - t0:.1f}s "
          f"(CPU compositing: {app.gpu_win is None})")

    random.seed(31337)
    sb = sandbox_mod.SandboxScreen(app)
    app.goto(sb)
    world = sb.world

    frame_ms, upd_ms, drw_ms, prs_ms, is_tick = [], [], [], [], []
    acc = 0.0
    sweep_x = 40.0
    bench_t0 = time.perf_counter()
    for _ in range(N_FRAMES):
        # the App.run fixed-timestep accumulator, at a forced 144 Hz cadence
        acc += DT
        steps = 0
        while acc >= 1 / 60 and steps < 2:
            acc -= 1 / 60
            steps += 1
        app.sim_steps = steps
        if steps:                  # lava-spam sweep, fed on sim cadence
            sweep_x += 3.0
            if sweep_x > 440:
                sweep_x = 40.0
            world.paint(sweep_x, 60, 25, M.LAVA, mode="fill")
            world.paint((sweep_x + 200) % 400 + 40, 110, 25, M.LAVA,
                        mode="fill")
        t0 = time.perf_counter()
        app.ui.begin([])
        sb.update([])
        t1 = time.perf_counter()
        sb.draw(app.renderer.view)
        t2 = time.perf_counter()
        if not app.crt.present(app.renderer.view, app.screen_surf):
            pygame.display.flip()
        t3 = time.perf_counter()
        frame_ms.append((t3 - t0) * 1000.0)
        upd_ms.append((t1 - t0) * 1000.0)
        drw_ms.append((t2 - t1) * 1000.0)
        prs_ms.append((t3 - t2) * 1000.0)
        is_tick.append(steps > 0)
    bench_total = time.perf_counter() - bench_t0

    mode = "QUICK" if QUICK else "FULL"
    print(f"\nbench_frame [{mode}]  {N_FRAMES} frames at forced {HZ:.0f} Hz "
          f"cadence, lava-spam sweep, CPU CRT present")
    print(f"world ticks completed: {world.tick} "
          f"(scheduled {sum(is_tick)} tick frames; debt-capped slicing)")
    print(f"wall time: {bench_total:.1f}s -> "
          f"{N_FRAMES / bench_total:.0f} fps effective\n")
    print(f"frame ms: p50={pct(frame_ms, 50):.2f}  "
          f"p90={pct(frame_ms, 90):.2f}  p99={pct(frame_ms, 99):.2f}  "
          f"max={max(frame_ms):.2f}")
    hdr = (f"{'frame kind':<14s}{'frames':>7s}{'update':>9s}{'draw':>9s}"
           f"{'present':>9s}{'total':>9s}   (median ms)")
    print(hdr)
    print("-" * len(hdr))
    for label, want in (("tick", True), ("render-only", False)):
        idx = [i for i in range(N_FRAMES) if is_tick[i] == want]
        if not idx:
            continue
        u = statistics.median(upd_ms[i] for i in idx)
        d = statistics.median(drw_ms[i] for i in idx)
        p = statistics.median(prs_ms[i] for i in idx)
        t = statistics.median(frame_ms[i] for i in idx)
        print(f"{label:<14s}{len(idx):>7d}{u:>9.2f}{d:>9.2f}{p:>9.2f}"
              f"{t:>9.2f}")
    pygame.quit()
    os.unlink(settings_path)
    print("done.")


if __name__ == "__main__":
    main()
