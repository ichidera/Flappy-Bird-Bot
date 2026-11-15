# Flappy Bird Bot

![Flappy Bird bot demo](Assets/demo.gif)

A Python bot that plays [flappybird.io](https://flappybird.io/) on its own. It plans every flap with a simulation that matches the game's own physics step for step.

## How it works

The game exposes its live simulation as `window.__game`, a 120 Hz fixed-step engine. The bot injects a planner into the page that:

- **Hooks `game.step()`** and plans right before every physics step, using the same order and integer (fixed-point) constants as the engine: flap, fall cap, gravity, position, pipe collision, pipe movement. Planning once per rendered frame left a 60 Hz control gap, which caused deaths.
- **Simulates ahead.** For each candidate first-flap frame it rolls the whole screen forward with the same reactive policy, throws out plans that hit a pipe or the floor, then waits until the valid takeoff window closes to its midpoint before flapping.
- **Prefers clean passes.** A strict centre-corridor pass has priority, and a collision-only pass is the fallback.
- **Never wastes flaps.** Flaps are only spent at or past the apex, never while the bird is still rising.
- **Restarts automatically** 1.1 s after a game over.

Engine constants the planner relies on (simVersion 4): gravity 5.0 u/s², flap velocity 1.4 u/s, fall cap 2.2 u/s, bird radius 0.068 u, pipe speed 0.6 u/s, pipe spacing 1.0 u, pipe width 0.26 u, gap 0.47 u (the first five pipes are wider), floor at -0.975 u.

> **Note:** unlike a bot that presses keys, this one drives the game through `window.__game` (it calls `flap()` and `restart()` and wraps `step()`). That makes it precise, but it also means it can break whenever the site changes its internals.

## Setup

Requires Python 3.8+ and Chromium via Playwright (developed on Python 3.12, Playwright 1.65, Chromium 1248).

```bash
pip install -r requirements.txt
playwright install chromium
```

## Usage

```bash
python flappy_bot.py                              # play one game
python flappy_bot.py --headless --games 5         # five games, no window
python flappy_bot.py --headless --games 0 --verbose   # until Ctrl+C, log every flap
python flappy_bot.py --headless --games 0 --seconds 600   # stop after 10 minutes
```

| Flag | Description |
| --- | --- |
| `--url URL` | Game URL (defaults to `https://flappybird.io/`) |
| `--headless` | Run the browser without a window |
| `--browser PATH` | Use a specific Chromium/Chrome executable |
| `--games N` | Number of games to play (`0` = until Ctrl+C, default `1`) |
| `--seconds N` | Stop after N seconds in total (`0` = no limit) |
| `--verbose` | Print every flap and planning decision |

## Results

| Version | Test | Result |
| --- | --- | --- |
| v1 (per-frame planner) | 10 min | 7 deaths, best score 95 |
| v2 (120 Hz step hook) | 10 min, repeated | 357 points, 0 deaths |
| v2 | 30 min endurance | 1,000+ points, 0 deaths observed |
| v2 | 5 min stress test (gap centres alternating 0.8 / -0.2 every pipe) | 178 points, 0 deaths |

## Contributing

Issues and pull requests are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT © 2026 ichidera. See [LICENSE](LICENSE).

## Disclaimer

A hobby project for learning about game simulation and browser automation. It is not affiliated with flappybird.io or the creators of Flappy Bird. Please don't use it to post scores to public leaderboards or to disrupt other players.
