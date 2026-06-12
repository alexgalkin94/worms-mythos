"""Run the whole GRUBSTORM test suite sequentially and aggregate results.

    python tests/run_all.py            # full suite
    python tests/run_all.py --fast     # skip the long battery test

Each test is a standalone script (stdlib-only, headless); this driver
streams their output, collects exit codes, prints a GREEN/FAIL summary
and exits non-zero if anything failed.
"""
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))

TESTS = [
    "test_determinism.py",
    "test_snapshot.py",
    "test_battery.py",
    "test_render_exact.py",
    "test_sim_behavior.py",
]

_TTY = sys.stdout.isatty()
GREEN = "\033[32mGREEN\033[0m" if _TTY else "GREEN"
FAIL = "\033[31mFAIL\033[0m" if _TTY else "FAIL"


def main():
    tests = list(TESTS)
    if "--fast" in sys.argv[1:]:
        tests.remove("test_battery.py")
    env = dict(os.environ)
    env.setdefault("SDL_VIDEODRIVER", "dummy")
    env.setdefault("SDL_AUDIODRIVER", "dummy")
    results = []
    t_all = time.perf_counter()
    for name in tests:
        print(f"\n######## {name} ########", flush=True)
        t0 = time.perf_counter()
        proc = subprocess.run([sys.executable, "-u",
                               os.path.join(HERE, name)], env=env)
        results.append((name, proc.returncode, time.perf_counter() - t0))
    print("\n================ SUMMARY ================")
    failed = 0
    for name, code, dt in results:
        tag = GREEN if code == 0 else FAIL
        print(f"[{tag}] {name:24s} {dt:7.1f}s")
        failed += code != 0
    total = time.perf_counter() - t_all
    if failed:
        print(f"{failed}/{len(results)} test files FAILED ({total:.1f}s)")
        sys.exit(1)
    print(f"all {len(results)} test files GREEN ({total:.1f}s)")
    sys.exit(0)


if __name__ == "__main__":
    main()
