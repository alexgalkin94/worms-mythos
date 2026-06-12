# GRUBSTORM test & benchmark suite

Everything here is **stdlib-only** (no pytest) and runs **headless**: each
script forces `SDL_VIDEODRIVER=dummy` / `SDL_AUDIODRIVER=dummy` before
pygame is imported, so it works on any CI box or VM with no display/audio.

```sh
python tests/run_all.py            # the whole suite (tests 1-5)
python tests/run_all.py --fast     # same, but skip the long battery test
python tests/<file>.py             # any single test standalone
```

Every check prints `[GREEN]`/`[FAIL]`; a file exits non-zero if any of its
checks failed, and `run_all.py` exits non-zero if any file failed.

## Tests

| file | what it guards | ~runtime* |
|---|---|---|
| `test_determinism.py` | Lockstep determinism. (A) two same-seed Worlds under an identical scripted 10-material chaos paint run 1500 ticks with full state hashes (`mat/shade/life/burn/rest/head/temp` — the planes `World.to_bytes` snapshots) compared every 25 ticks. (B) two identical bot matches through `Game`+`Bot` for 1800 ticks, compared with the same `sha256(world.mat)` hash multiplayer uses for desync detection, extended with grub state. | ~100 s |
| `test_snapshot.py` | Snapshot round-trips. (A) busy `World` → `to_bytes` → `from_bytes` into a *fresh* World (plus the extra sim state `Game.serialize/restore` carries: tick, np RNG state, wake/cool boxes, levelling window, phase/density mirrors), then 600 ticks of lockstep continuation. (B) the real reconnect path: bot match to a quiescent `PH_START`, `Game.serialize` → JSON → `Game.restore` into a fresh Game with fresh bots, 600 ticks lockstep. | ~60 s |
| `test_battery.py` | The long one. (a) A full bot match (sudden death armed) must complete without exceptions; reports ticks and ms/tick. (b) Every biome in `mapgen.BIOMES` must generate + pre-settle within a generous per-biome budget (45 s; worst observed ~10 s); reports per-biome startup seconds. | ~75 s |
| `test_render_exact.py` | Golden-free pixel determinism. Boots a real `App` headless (settings redirected to a temp file via the `A.SETTINGS_PATH` module global — your `~/.grubstorm.json` is never touched), renders the same seeded scene twice through `Renderer.refresh_cells` + a real `SandboxScreen.draw`, asserts identical pixels; asserts the CPU CRT `present` chain is deterministic with `crt_flicker=0`/`crt_persist=0` (the only two time/randomness-coupled dials); checks the surface-format invariants the renderer relies on. **No stored golden images** — they'd break on every intentional visual change; only run-to-run determinism is asserted. | ~25 s |
| `test_sim_behavior.py` | Behaviours past bugs regressed on: (a) communicating vessels — a sealed stone U-tube filled high on one side equalizes to within 1 row inside 3000 ticks with the water cell count conserved *exactly*; (b) a lopsided pour into an open basin ends fully flat and the world goes to sleep (50 consecutive zero-activity ticks, wake box + cooldown expired); (c) a sleeping world stays byte-identical for 100 ticks. | ~25 s |

\* on a slow CI VM; budgets inside the tests are several times larger than
the observed runtimes to absorb ±25 % machine noise. **Timing budgets only
exist in `test_battery.py`** — everything else is timing-independent, so
the suite cannot flake on a slow machine.

## Benchmarks (not part of `run_all.py`)

```sh
python bench/bench_sim.py [--quick]      # per-tick sim cost, 4 scenarios
python bench/bench_frame.py [--quick]    # end-to-end frame cost at 144 Hz
```

* `bench_sim.py` — fixed-seed scenario benchmarks with median/p95/max tick
  ms over 600 ticks (200 with `--quick`): lava brush, water brush,
  10-material chaos, and the **lava-spam sweep** (two 25-radius lava discs
  painted *every* tick marching across the map — the historical worst-case
  complaint scenario).
* `bench_frame.py` — drives the real `App` + `SandboxScreen` per-frame
  pipeline (`ui.begin → update → draw → crt.present`) at a forced 144 Hz
  accumulator cadence under lava spam, reporting p50/p90/p99 frame ms and
  the update/draw/present split for tick frames vs render-only frames.

Benchmark numbers are machine-dependent and are **reported, not asserted**.

## Notes for test authors

* Import `tests/_harness.py` first: it sets the dummy SDL drivers before
  pygame loads, fixes `sys.path`, and provides `Runner`, `world_hash`,
  `mat_hash`, `game_hash`.
* Determinism rules: fixed seeds everywhere, no wall-clock dependence.
  The CRT pins `crt_flicker`/`crt_persist` to 0 when pixel-exactness is
  asserted (flicker draws `random.randint`, persistence decays on
  `time.monotonic`). `Renderer._t` and `UI.t` are animation clocks — reset
  them between passes that must match.
* `World.from_bytes` restores the seven cell planes only. To continue a
  restored world bit-identically you must also carry tick, the np RNG
  state, `_wake_box`/`_wake_cool`/`_cool_box`, `level_until`/`level_box`,
  `water_level`, `gravity_dir`, and refresh the `phase`/`dens` mirrors —
  exactly what `Game.serialize`/`restore` does (`test_snapshot.py` is the
  executable documentation of that contract).
* Direct `world.mat[...]` writes don't wake the sim; set
  `world._wake_box = [0, h, 0, w]` afterwards (mapgen does the same).
