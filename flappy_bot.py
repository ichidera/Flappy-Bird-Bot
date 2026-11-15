#!/usr/bin/env python3
# Flappy Bird bot - v2 (120 Hz step-hook planner + safe-window flap timing)
# Stack:  Python 3.12 / Playwright 1.65.2 / Chromium 1248 (fallback from Playwright's expected 1255)
# Engine: flappybird.io (React/SpacetimeDB build). The page exposes window.__game, a 120 Hz
#         fixed-step simulation with simVersion 4:
#           gravity 5.0 u/s^2, flap velocity 1.4 u/s, fall cap 2.2 u/s, bird radius 0.068 u,
#           pipe speed 0.6 u/s, pipe spacing 1.0 u, pipe width 0.26 u, gap 0.47 u
#           (the first five pipes are wider: 0.62, 0.59, 0.56, 0.53, 0.50 u), floor -0.975 u.
#         The bot reads birdY/birdVy/pipes straight from that object; the canvas hook is only
#         the fallback probe used during recon. v2 wraps game.step() and plans immediately
#         before every physics step (same flap/gravity/move/collision order as the engine),
#         instead of once per rendered frame; that removes the 60 Hz control gap.
#         Flaps are only spent at or past the apex, never while the bird is still rising.
# Setup:  pip install playwright && playwright install chromium
# Run:    python flappy_bot.py --headless --games 5
#         python flappy_bot.py --headless --games 0 --verbose
# Flags:  --headless, --games N (0 = until Ctrl+C), --verbose, --seconds N, --url, --browser PATH

import argparse
import os
import re
import signal
import threading
import time

from playwright.sync_api import sync_playwright

GAME_URL = "https://flappybird.io/"
STOP = threading.Event()
LAST_FINAL = {}

BOT_JS = """
(function () {
  if (window.__flappyBot) { console.log('[BOT] already injected'); return; }
  window.__flappyBot = true;
  const VERBOSE = !!window.__flappyVerbose;

  let stopped = false;
  const log = m => {   // buffered in the page so Python can poll it (console events are unreliable here)
    try { const b = (window.__botLogs = window.__botLogs || []); b.push(m); if (b.length > 1200) b.shift(); } catch (e) {}
    console.log(m);
  };
  window.__flappyBotStop = function () { stopped = true; window.__flappyBot = false; };

  // ---- engine constants (read out of the game bundle; fixed point is 2^20) ----------------
  const SN          = 1 << 20;
  const FIX         = x => Math.round(x * SN);
  const GRAV        = Math.trunc(FIX(5) / 120);          // velocity loss per 120 Hz step
  const SPEED       = Math.trunc(FIX(0.6) / 120);        // pipe x movement per step
  const RADIUS      = FIX(0.068);
  const FLOOR       = FIX(-0.975);
  const PIPE_HALF   = Math.trunc(26 * FIX(0.01) / 2);    // 0.13 u, same expression as the engine
  const SAFETY      = FIX(0.015);                         // extra radius used by the planner only
  const RAD_PLAN    = RADIUS + SAFETY;
  const BAND        = FIX(0.08);                          // altitude-hold hysteresis
  const CENTER_TOL  = FIX(0.12);                          // max |y-gapY| while crossing a pipe (strict pass)
  const MAX_K       = 120;                                // first-flap delays tried (1 s at 120 Hz)
  const MAX_SIM     = 900;                                // 7.5 s rollout cap
  const LOOKAHEAD_X = FIX(0.70);                          // engage the safe-window planner this close

  let lastState = '', deathAnnounced = false, restartAt = 0;
  let games = 0, flaps = 0, lastLog = 0, lastTs = 0, fps = 60, steps = 0;
  let curPipe = null, safeW = 0, lastScore = 0, lastWarn = 0, lastPipeLogged = null;
  let trace = [], hookInstalled = false, planErrors = 0;

  const physics = g => {
    const v = g.simVersion | 0;
    return {
      v: v,
      flap: v < 3 ? FIX(1.55) : FIX(1.4),
      fall: v < 3 ? 0 : (v < 4 ? FIX(1.5) : FIX(2.2))
    };
  };

  function collide(y, p, margin) {
    // Same circle-vs-rectangle test as the engine's rE(), with an inflated radius for planning.
    const l = p.x - PIPE_HALF, u = p.x + PIPE_HALF;
    const f = -Math.max(l, Math.min(0, u));
    if (Math.abs(f) >= margin) return false;
    const bottom = p.gapY - p.halfGap;
    const by = Math.max(FIX(-10), Math.min(y, bottom));
    const dyb = y - by;
    if (f * f + dyb * dyb < margin * margin) return 'bottom';
    const top = p.gapY + p.halfGap;
    const ty = Math.max(top, Math.min(y, FIX(10)));
    const dyt = y - ty;
    return f * f + dyt * dyt < margin * margin ? 'top' : null;
  }

  const pipeAhead = ps => {
    for (let i = 0; i < ps.length; i++) {
      if (ps[i].x + PIPE_HALF + RAD_PLAN >= 0) return ps[i];
    }
    return null;
  };
  const targetOf = ps => { const p = pipeAhead(ps); return p ? p.gapY : FIX(0.2); };

  // Roll the exact 120 Hz engine forward. true = this first-flap frame survives every pipe on screen.
  // After frame k the policy flaps whenever it is below the target band and falling, which is the same
  // controller the bot runs live. strict also requires it to stay inside the middle of the gap.
  function rollout(k, y0, vy0, src, P, strict) {
    let y = y0, vy = vy0;
    const ps = src.map(p => ({ x: p.x, gapY: p.gapY, halfGap: p.halfGap }));
    for (let f = 0; f < MAX_SIM; f++) {
      const tgt = targetOf(ps);
      const flap = (f === k) || (f > k && vy <= 0 && y < tgt - BAND);
      if (flap) vy = P.flap;
      if (P.fall && vy < -P.fall) vy = -P.fall;
      vy -= GRAV;
      y += Math.trunc(vy / 120);
      if (y - RAD_PLAN < FLOOR) return false;         // floor
      let allPast = true;
      for (let i = 0; i < ps.length; i++) {
        const p = ps[i];
        if (collide(y, p, RAD_PLAN)) return false;    // pipe hit
        if (strict && p.x - PIPE_HALF < RAD_PLAN && p.x + PIPE_HALF > -RAD_PLAN) {
          const tol = Math.min(p.halfGap - RAD_PLAN, CENTER_TOL);
          if (Math.abs(y - p.gapY) > tol) return false;   // not centered enough while crossing
        }
        if (p.x + PIPE_HALF + RAD_PLAN >= 0) allPast = false;
      }
      if (allPast) return true;
      for (let i = 0; i < ps.length; i++) ps[i].x -= SPEED;
    }
    return true;
  }

  // R = number of valid first-flap frames starting at "now"; first = first valid frame if now isn't one.
  function windowInfo(y, vy, ps, P, strict) {
    let R = 0;
    while (R <= MAX_K && rollout(R, y, vy, ps, P, strict)) R++;
    let first = -1;
    if (R === 0) {
      for (let k = 1; k <= MAX_K; k++) {
        if (rollout(k, y, vy, ps, P, strict)) { first = k; break; }
      }
    }
    return { R: R, first: first };
  }

  const fmt = v => (v / SN).toFixed(3);
  // The engine only commits bestScore on gameover; show the live run's best too.
  const bestOf = g => Math.max(g.bestScore || 0, g.score || 0);

  function resetRun() {
    curPipe = null; safeW = 0; lastScore = 0; lastPipeLogged = null; lastWarn = 0;
    trace = [];
  }

  // Runs once per rendered frame. It only handles state changes, restarts and slow status logs.
  // All steering happens in planStep(), which the step hook calls at the engine's native 120 Hz.
  function tick(ts) {
    if (stopped) return;
    requestAnimationFrame(tick);
    const g = window.__game;
    if (!g) return;
    if (!hookInstalled) installStepHook(g);

    const now = performance.now();
    if (lastTs) {
      const d = Math.min(50, Math.max(4, now - lastTs));
      fps = fps * 0.9 + (1000 / d) * 0.1;
    }
    lastTs = now;

    const st = g.currentState || g.state;
    if (st !== lastState) {
      log('[BOT] state=' + st);
      lastState = st;
    }

    if (st === 'gameover') {
      if (!deathAnnounced) {
        deathAnnounced = true;
        games++;
        log('[DEATH] score=' + g.score + ' best=' + bestOf(g) + ' game=' + games +
            ' flaps=' + flaps + ' steps=' + steps + ' cause=' + (g.deathCause || '?') +
            ' simVersion=' + g.simVersion);
        if (VERBOSE && trace.length) {
          const tail = trace.slice(-24);
          log('[TRACE] last ' + tail.length + ' physics steps (step,y,vy,x,gap,R,safeW,why):');
          for (let i = 0; i < tail.length; i++) log('[TRACE] ' + tail[i].join(','));
        }
        restartAt = now + 1100;
      }
      if (restartAt && now >= restartAt) {
        restartAt = 0;
        deathAnnounced = false;
        resetRun();
        g.restart();
        g.flap();                    // restart() lands in getready; flap starts the next run
      }
      return;
    }

    if (st === 'getready') {
      deathAnnounced = false;
      resetRun();
      g.flap();                      // begin the run immediately
      return;
    }
    if (st !== 'play') return;

    if (now - lastLog > 3000) {
      lastLog = now;
      const y = g.birdY, vy = g.birdVy, ps = g.pipes || [];
      const p = pipeAhead(ps);
      log('[BOT] score=' + g.score + ' best=' + bestOf(g) + ' speed=0.60 u/s flaps=' + flaps +
          ' steps=' + steps + ' y=' + fmt(y) + ' vy=' + fmt(vy) + ' pipes=' + ps.length +
          (p ? ' next x=' + fmt(p.x) + ' gap=' + fmt(p.gapY) + ' half=' + fmt(p.halfGap) +
               ' window=' + safeW : ' next=-') +
          ' fps~' + fps.toFixed(0));
    }
  }

  // Full steering decision, executed just before each engine physics step. This is the same instant
  // the rollout models: flap first, then gravity, then position, collision and pipe advance.
  function planStep(g) {
    if (stopped || g.currentState !== 'play') return;
    steps++;

    const P = physics(g);
    const y = g.birdY, vy = g.birdVy;
    const ps = g.pipes || [];
    const tgt = pipeAhead(ps);

    if (tgt && tgt !== lastPipeLogged) {
      lastPipeLogged = tgt;
      log('[PIPE] n=' + (g.score + 1) + ' x=' + fmt(tgt.x) + ' gap=' + fmt(tgt.gapY) +
                   ' half=' + fmt(tgt.halfGap) + ' speed=0.60 u/s');
    }
    if (g.score !== lastScore) {
      lastScore = g.score;
      log('[SCORE] ' + lastScore + ' best=' + bestOf(g));
    }

    let action = 'hold', why = '', dbgR = -1;
    if (!tgt) {
      // No pipe on screen: just hold a little above the middle of the world.
      if (vy <= 0 && y < FIX(0.05)) { action = 'flap'; why = 'hold'; }
    } else if (tgt.x > LOOKAHEAD_X) {
      // The next gap is still far away; fly toward its centre with simple hysteresis.
      if (vy <= 0 && y < tgt.gapY - BAND) { action = 'flap'; why = 'gap-hold'; }
    } else {
      let w = windowInfo(y, vy, ps, P, true);
      let relaxed = false;
      if (w.R === 0 && w.first < 0) {
        w = windowInfo(y, vy, ps, P, false);   // strict centering found nothing; accept any collision-free plan
        relaxed = w.R > 0 || w.first >= 0;
      }
      dbgR = w.R;
      if (curPipe !== tgt) { curPipe = tgt; safeW = 0; }
      if (w.R > 0) {
        if (w.R > safeW) safeW = w.R;
        if (vy <= 0 && w.R <= Math.ceil(safeW / 2)) {
          // Only spend a flap when we are at or past the apex. Flapping during the rise only
          // raises the bird into the top pipe and used to cause one flap on every physics step.
          action = 'flap';
          why = 'window ' + w.R + '/' + safeW + (relaxed ? ' relaxed' : '');
        }
      } else if (w.first > 0) {
        why = 'waiting ' + w.first;            // flapping now would leave the centre corridor
      } else {
        // No surviving first-flap frame at all. Recover if we are low; otherwise fall into the gap.
        if (vy <= 0 && y < tgt.gapY - BAND) { action = 'flap'; why = 'emergency'; }
        const now = performance.now();
        if (now - lastWarn > 700) {
          lastWarn = now;
          log('[WARN] no safe window y=' + fmt(y) + ' vy=' + fmt(vy) + ' gap=' + fmt(tgt.gapY) +
                      ' x=' + fmt(tgt.x));
        }
      }
    }

    if (action === 'flap') {
      g.flap();
      flaps++;
      safeW = 0;
      if (tgt) curPipe = tgt;
      if (VERBOSE) {
        log('[FLAP] #' + flaps + ' score=' + g.score + ' y=' + fmt(y) + ' vy=' + fmt(vy) +
            ' gap=' + (tgt ? fmt(tgt.gapY) : '-') + ' x=' + (tgt ? fmt(tgt.x) : '-') + ' | ' + why);
      }
    }

    if (VERBOSE) {
      trace.push([g.playStep | 0, fmt(y), fmt(vy), tgt ? fmt(tgt.x) : '-',
                  tgt ? fmt(tgt.gapY) : '-', dbgR, safeW, why || action]);
      if (trace.length > 240) trace.shift();
    }
  }

  // The page's loop calls game.step() once per fixed 120 Hz tick. Wrapping it lets the planner act
  // on every physics step instead of once per rendered frame, which removes the 60 Hz control gap.
  function installStepHook(g) {
    if (hookInstalled || !g || typeof g.step !== 'function') return;
    const origStep = g.step;
    g.step = function (dt) {
      if (window.__game === g && g.currentState === 'play') {
        try {
          planStep(g);
        } catch (e) {
          if (planErrors++ < 3) log('[BOT] planner error: ' + (e && e.message ? e.message : e));
        }
      }
      return origStep.call(g, dt);
    };
    hookInstalled = true;
    log('[BOT] step hook installed -> planning every 120 Hz physics step');
  }

  function waitForGame() {
    if (stopped) return;
    if (window.__game) {
      const g = window.__game;
      installStepHook(g);
      const P = physics(g);
      log('[BOT] ready simVersion=' + g.simVersion + ' step=120Hz gravity=' + (GRAV / SN * 120).toFixed(2) +
          ' u/s^2 flap=' + (P.flap / SN).toFixed(2) + ' u/s fallCap=' + (P.fall / SN).toFixed(2) +
          ' u/s radius=' + (RADIUS / SN).toFixed(3) + ' pipeHalf=' + (PIPE_HALF / SN).toFixed(3) + ' u');
      requestAnimationFrame(tick);
    } else {
      setTimeout(waitForGame, 300);
    }
  }

  log('[BOT] injected');
  waitForGame();
})();

"""


# ----------------------------------------------------------------------------------------------
# driver
# ----------------------------------------------------------------------------------------------

def chromium_path():
    """Playwright's bundled Chromium, or the newest one already in the cache, or system Chromium."""
    env = os.environ.get("FLAPPY_CHROMIUM")
    if env and os.path.exists(env):
        return env
    root = os.path.expanduser("~/.cache/ms-playwright")
    if os.path.isdir(root):
        for name in sorted(os.listdir(root), reverse=True):
            if name.startswith("chromium-"):
                p = os.path.join(root, name, "chrome-linux64", "chrome")
                if os.path.exists(p):
                    return p
    for name in ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable"):
        for d in os.environ.get("PATH", "").split(os.pathsep):
            p = os.path.join(d, name)
            if os.path.exists(p):
                return p
    return None


def open_game(browser, args):
    print(f"[*] opening {args.url}")
    page = browser.new_page(viewport={"width": 1000, "height": 700})
    page.on("pageerror", lambda e: print(f"[page error] {e}"))
    page.goto(args.url, wait_until="domcontentloaded", timeout=60000)
    print("[*] waiting for window.__game ...")
    end = time.time() + 30
    while time.time() < end:
        try:
            if page.evaluate("!!window.__game"):
                return page
        except Exception:
            pass
        page.wait_for_timeout(200)
    raise SystemExit("[!] window.__game never appeared. The site may have changed; re-run recon_flappy.py.")


def play(page, args):
    LAST_FINAL.clear()
    print("[*] injecting bot ...")
    page.evaluate(f"window.__flappyVerbose = {str(args.verbose).lower()}")
    page.evaluate(BOT_JS)
    print("[*] bot running. Ctrl+C to stop.\n")

    scores = []
    deaths = 0
    started = time.time()
    old = signal.signal(signal.SIGINT, lambda *_: STOP.set())
    try:
        while not STOP.is_set():
            try:
                lines = page.evaluate("window.__botLogs ? window.__botLogs.splice(0) : []") or []
            except Exception as e:
                print(f"\n[*] tab or browser closed ({type(e).__name__})")
                break
            for line in lines:
                print(f"{time.strftime('%H:%M:%S')} {line}", flush=True)
                if line.startswith("[DEATH]"):
                    deaths += 1
                    m = re.search(r"score=(\d+)", line)
                    if m:
                        scores.append(int(m.group(1)))
            if args.games and deaths >= args.games:
                break
            if args.seconds and time.time() - started >= args.seconds:
                print(f"\n[*] time limit ({args.seconds:.0f}s) reached")
                break
            page.wait_for_timeout(200)      # NOT time.sleep: keeps Playwright's event loop alive
    except KeyboardInterrupt:
        print("\n[*] stopping")
    finally:
        signal.signal(signal.SIGINT, old)
        try:
            page.evaluate("window.__flappyBotStop && window.__flappyBotStop()")
        except Exception:
            pass
        try:
            final = page.evaluate(
                "() => { const g = window.__game; return g ? "
                "{state: g.currentState, score: g.score, "
                "best: Math.max(g.bestScore || 0, g.score || 0)} : null; }")
            LAST_FINAL.clear()
            if final:
                LAST_FINAL.update(final)
        except Exception:
            pass
    return scores


def run(args):
    exe = args.browser or chromium_path()
    with sync_playwright() as p:
        kw = {"headless": args.headless}
        if exe:
            kw["executable_path"] = exe
        if args.headless:
            kw["args"] = ["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage", "--mute-audio"]
        print(f"[*] chromium: {exe or 'Playwright default'}")
        browser = p.chromium.launch(**kw)
        try:
            page = open_game(browser, args)
            scores = play(page, args)
            if scores:
                print(f"\n[*] games played: {len(scores)} | scores: {scores} | best: {max(scores)} | "
                      f"avg: {sum(scores) / len(scores):.1f}")
            else:
                print("\n[*] no deaths")
            if LAST_FINAL:
                alive = LAST_FINAL.get("state") == "play"
                print(f"[*] final: state={LAST_FINAL.get('state')} score={LAST_FINAL.get('score')} "
                      f"best={LAST_FINAL.get('best')}" + (" (still alive)" if alive else ""))
        finally:
            try:
                browser.close()
            except Exception:
                pass


def main():
    ap = argparse.ArgumentParser(description="flappybird.io bot")
    ap.add_argument("--url", default=GAME_URL)
    ap.add_argument("--headless", action="store_true", help="run Chromium without a window")
    ap.add_argument("--games", type=int, default=1, help="games to play (0 = until Ctrl+C)")
    ap.add_argument("--seconds", type=float, default=0, help="stop after this many seconds (0 = no limit)")
    ap.add_argument("--verbose", action="store_true", help="print every flap and planning decision")
    ap.add_argument("--browser", help="path to a Chromium/Chrome executable (default: Playwright's)")
    run(ap.parse_args())


if __name__ == "__main__":
    main()
