# Changelog

All notable changes to this project are documented here. The format is loosely based on [Keep a Changelog](https://keepachangelog.com/).

## [2.0.0]

### Added
- 120 Hz step-hook planner: wraps `game.step()` and plans immediately before every physics step, using the engine's own integer constants and step order (flap, fall cap, gravity, position, pipe collision, pipe movement).
- Safe-window flap timing: for each candidate first-flap frame the planner simulates the full screen, rejects plans that hit a pipe or the floor, and waits until the valid takeoff window closes to its midpoint before flapping.
- Strict centre-corridor pass with a collision-only fallback.
- `--seconds` wall-clock limit and `--verbose` decision logging.

### Changed
- Flaps are only spent at or past the apex, never while the bird is still rising.

### Results
- 357 points in 10 minutes with zero deaths (repeated), 1,000+ points over 30-minute runs with zero deaths observed, and 178 points in a 5-minute worst-case stress test with zero deaths.

## [1.0.0] - Initial version

- Planner ran once per rendered frame (60 Hz), leaving a control gap relative to the 120 Hz simulation.
- 7 deaths in 10 minutes, best score 95.
- Added `recon_flappy.py`, a one-shot probe that scans the page for game state, dumps `window.__game` and captures one second of `drawImage` / `fillRect` calls.
