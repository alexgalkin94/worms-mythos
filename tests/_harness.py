"""Shared scaffolding for the GRUBSTORM test suite.

Import this FIRST in every test: it forces the dummy SDL drivers (so
everything runs headless on CI), puts the repo root on sys.path, and
provides a tiny stdlib-only check/summary runner with GREEN/FAIL output
and a non-zero exit code on any failure. No pytest required.
"""
import hashlib
import os
import sys
import time

# headless before pygame is ever imported, anywhere
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

_TTY = sys.stdout.isatty()
GREEN = "\033[32mGREEN\033[0m" if _TTY else "GREEN"
FAIL = "\033[31mFAIL\033[0m" if _TTY else "FAIL"


class Runner:
    """Collects named checks; finish() prints a summary and exits."""

    def __init__(self, name):
        self.name = name
        self.results = []          # (label, ok, detail)
        self.t0 = time.perf_counter()
        print(f"=== {name} ===")

    def check(self, label, ok, detail=""):
        ok = bool(ok)
        tag = GREEN if ok else FAIL
        msg = f"[{tag}] {label}"
        if detail:
            msg += f"  ({detail})"
        print(msg, flush=True)
        self.results.append((label, ok, detail))
        return ok

    def info(self, msg):
        print(f"       {msg}", flush=True)

    def finish(self):
        dt = time.perf_counter() - self.t0
        bad = [r for r in self.results if not r[1]]
        n = len(self.results)
        if bad:
            print(f"--- {self.name}: {FAIL} "
                  f"({n - len(bad)}/{n} checks passed, {dt:.1f}s) ---")
            sys.exit(1)
        print(f"--- {self.name}: {GREEN} ({n}/{n} checks passed, "
              f"{dt:.1f}s) ---")
        sys.exit(0)


def init_pygame():
    """Headless pygame init (audio synth may fail silently — that's fine)."""
    import pygame
    pygame.init()
    return pygame


# --------------------------------------------------------------- hashing ---
def world_hash(w):
    """Full deterministic-state hash of a World: every plane that
    World.to_bytes() snapshots (mat/shade/life/burn/rest/head/temp)."""
    return hashlib.sha256(b"".join([
        w.mat.tobytes(), w.shade.tobytes(), w.life.tobytes(),
        w.burn.tobytes(), w.rest.tobytes(), w.head.tobytes(),
        w.temp.tobytes(),
    ])).hexdigest()


def mat_hash(w):
    """The exact state-hash notion lockstep multiplayer uses
    (see GameScreen._tick_once in app.py / Session.check_state in net.py)."""
    return hashlib.sha256(w.mat.tobytes()).hexdigest()[:12]


def game_hash(g):
    """mat_hash plus the per-grub kinematic/health state, so divergence in
    entity simulation is caught even when the grid still agrees."""
    grubs = repr([(gr.x, gr.y, gr.vx, gr.vy, gr.hp, gr.alive, gr.facing)
                  for tm in g.teams for gr in tm.grubs]).encode()
    return (mat_hash(g.world)
            + hashlib.sha256(grubs).hexdigest()[:12]
            + f":{g.tick}:{g.phase}:{g.turn_team}")


def first_divergence(seq_a, seq_b):
    """Index of the first differing element, or None if identical."""
    for i, (a, b) in enumerate(zip(seq_a, seq_b)):
        if a != b:
            return i
    if len(seq_a) != len(seq_b):
        return min(len(seq_a), len(seq_b))
    return None
