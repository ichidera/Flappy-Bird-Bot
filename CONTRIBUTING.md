# Contributing to Flappy Bird Bot

Thanks for taking an interest in the project! Bug reports, ideas and pull requests are all welcome.

## Getting started

1. Fork the repo, then clone it (use your own username in the URL to clone your fork):
   ```bash
   git clone https://github.com/ichidera/Flappy-Bird-Bot.git
   cd Flappy-Bird-Bot
   ```
2. Install the dependencies (Python 3.8+, developed on 3.12):
   ```bash
   pip install -r requirements.txt
   playwright install chromium
   ```
3. Run the bot to make sure everything works:
   ```bash
   python flappy_bot.py --headless --seconds 60
   ```

## Reporting bugs

Open an issue and include:

- what you expected vs. what happened (e.g. "died on the 14th pipe after a large gap change")
- your OS, Python, Playwright and Chromium versions
- the command you ran and any error output
- the `[DEATH]` lines from a `--verbose` run, which show the score and state at the time

**If the bot stops working entirely**, the site has probably changed its internals. The bot depends on `window.__game` and its `step()`, `flap()` and `restart()` methods. Run `python recon_flappy.py` and paste the output in your issue. It dumps the page's globals and the shape of `__game`, which is what's needed to fix it.

## Suggesting ideas

Open an issue before starting anything big. Good areas to explore:

- a fallback that reads state from the canvas draw calls if `window.__game` disappears
- tuning the safety margin and centre tolerance
- adapting the planner to other Flappy Bird clones
- a learning-based version (e.g. neuroevolution) for comparison with the physics planner

## Pull requests

1. Create a branch from `main` (`git checkout -b fix-restart-timing`).
2. Keep each pull request focused on one change.
3. Test with at least one timed run (`--seconds 600`) and include the result in the PR description: score, deaths and anything unusual in the log.
4. Add an entry to `CHANGELOG.md` under an "Unreleased" heading.
5. Update `README.md` if you changed flags, behaviour or setup.

The lint workflow runs on every push and pull request. You can check locally with:
```bash
python -m py_compile flappy_bot.py recon_flappy.py
```

### Guidelines

- **Keep the planner in step with the engine.** It matches the game's integer constants and step order exactly. If you change a constant or the order of operations, say where it comes from in the game's source or in a recon dump.
- Don't widen the planner's safety margin or loosen collision checks just to get a higher score without evidence from several long runs.
- Keep planning cheap, since it runs before every 120 Hz physics step.
- Keep the code readable: clear names, and a comment for any game constant you rely on.

## Scope

This is a hobby project for learning about game simulation and browser automation. Please don't contribute changes meant to post scores to public leaderboards, fake or edit scores, or disrupt other players. Pull requests like that won't be merged.

## License

By contributing, you agree that your contributions will be licensed under the [MIT License](LICENSE).