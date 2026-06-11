# GRUBSTORM

**Worms World Party, but the entire world is physically alive.**

A turn-based artillery party game fused with a Noita-style falling-sand
simulation. Every pixel of terrain is a simulated material: sand falls, water
flows, oil ignites, acid dissolves bunkers, gas pockets deflagrate, lava
quenches into stone, electricity hunts through water and metal — and your
rocket is just the first domino.

![volcano](docs/volcano.png)

| | |
|---|---|
| ![menu](docs/menu.png) | ![island](docs/island.png) |
| ![cavern](docs/cavern.png) | every pixel is simulated |

## Quick start

```bash
pip install -r requirements.txt     # pygame-ce + numpy
python -m grubstorm
```

Local hot-seat: **Local Party → add teams (humans or bots) → pick an arena →
START**. Pass the keyboard around.

### Play online with friends

Someone runs the relay (any cheap VPS or a LAN machine, pure stdlib, no deps):

```bash
python server/relay.py            # listens on :31999
```

Everyone else: **Online → set the server address → Create Private Lobby** →
share the 4-letter room code. Friends join with the code, the host hits
start. 2–8 players plus bot teams.

The simulation runs in deterministic lockstep: only the active player's
inputs travel over the wire, every client computes the identical world.
If someone disconnects the host keeps the match alive for them; rejoining
with the same name hands them their team back via a full state snapshot.
(All players should run the same Python/numpy versions.)

## Controls

| Key | Action |
|---|---|
| ← → / A D | walk (tap away from facing to turn first) |
| ↑ ↓ / W S | aim (also rope length while swinging) |
| SPACE | hold to charge, release to fire |
| ENTER | jump · BACKSPACE backflip · jump again to release the rope |
| TAB / right-click | weapon arsenal |
| left-click | target for click-weapons (airstrike, teleport, lightning…) |
| ESC | pause |

## The world

29 simulated materials with density, viscosity, flammability, corrosion,
conductivity, hardness, melting/freezing and light emission. Reactions are
the gameplay:

- water + lava → steam + obsidian
- fire spreads through wood, grass, oil, napalm and gas
- acid eats terrain (slowly chews metal) and exhales toxic puffs
- explosive powder and nitro chain-react — one spark, whole vein
- freeze ray turns lakes into walkable bridges; heat melts them back
- electricity conducts through water and metal and *hurts*
- toxic sludge poisons, magic goo does... something, every time
- black holes eat terrain, liquids, grubs, projectiles and your friendships

**35 weapons & tools** across classic boom (bazooka, grenade, cluster,
shotgun, mine, dynamite, airstrike, homing, Holy Melon), chemistry (acid /
oil / sludge / slime flasks, lava bomb, gas canister, powder bomb, steam
bomb, crystal bomb, napalm strike), energy (water cannon, freeze ray, spark
gun, lightning rod, transmuter, liquefier, black hole, gravity flip) and
movement (ninja rope, jetpack, parachute, teleport, girder, blowtorch,
drill).

**11 arenas**, procedurally generated per match: Grubtide Isle, Mt. Kaboom,
The Drips (acid sewer), Frostbite Flats, Dune & Doom, Gloomhollow (dark
crystal cave), Scrapheap, Powderkeg Mine, Lab 13, Goopland, Lunar Lounge
(low gravity). Plus any map you build in the sandbox.

**Match rules:** turn timer, wind, fall damage, drowning, poison, crates
(weapon / health / booby-trapped), retreat time, sudden death floods the map
with the biome's signature fluid. Mutators: low gravity, one-shot kills,
random weapons, crate madness, all-super-weapons.

**Bots** in four flavors — Chaotic Dummy, Regular Joe, Tactician, Evil
Genius — with real ballistic solving and personality-driven bad decisions.

**Sandbox Lab:** paint any material, set things on fire, trigger explosions,
and save experiments as playable maps (`O` key → appears in match setup).

## Presentation

Everything renders into a 480×270 cell grid and goes through a CRT pipeline:
emissive materials (lava, fire, acid, crystals, magic) light up the dark,
bright pixels bloom through a low-res bright-pass, then slot mask, scanlines,
chromatic fringe, smooth vignette, glass shine and a gentle flicker. All of
it on sliders in Options, including a reduced-flashing mode and
colorblind-friendly team palettes.

## Repository layout

```
grubstorm/
  world.py      vectorized falling-sand sim (numpy, active-region culled)
  materials.py  the material property tables — add a material here
  mapgen.py     procedural biomes
  game.py       turn engine, damage, crates, sudden death, snapshots
  weapons.py    the arsenal (projectiles, streams, black holes)
  grub.py       characters: walking, roping, jetpacking, drowning
  ai.py         deterministic bots
  render.py     cell compositor, lighting, HUD
  crt.py        the glass
  net.py        lockstep client
  ui.py/app.py  menus, screens, the cabinet
  sandbox.py    the lab
server/relay.py room-code relay server (stdlib only)
```

Determinism contract: anything that affects game state must draw randomness
from `game.rng` / `world.rng` and never from wall-clock time. Render and
audio may do whatever they like.
