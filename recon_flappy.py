#!/usr/bin/env python3
# flappybird.io recon probe
# Python 3.12 / Playwright 1.65 / Chromium 1248
#
# Opens https://flappybird.io/, injects a probe before any page script runs, starts a run,
# captures 1.0 s of CanvasRenderingContext2D.drawImage / fillRect calls, dumps non-standard
# globals and scans window for objects that look like bird state or pipe arrays.

import json
import os
import time

from playwright.sync_api import sync_playwright

URL = "https://flappybird.io/"


def chromium_path():
    exe = os.environ.get("FLAPPY_CHROMIUM")
    if exe:
        return exe
    root = os.path.expanduser("~/.cache/ms-playwright")
    if os.path.isdir(root):
        for name in sorted(os.listdir(root), reverse=True):
            if name.startswith("chromium-"):
                p = os.path.join(root, name, "chrome-linux64", "chrome")
                if os.path.exists(p):
                    return p
    return None


PROBE_JS = r"""
(() => {
  if (window.__fbProbe) return;
  const P = window.__fbProbe = {
    active: false, started: 0, stopped: 0,
    draws: new Map(), fills: new Map(), frames: 0
  };
  const proto = CanvasRenderingContext2D.prototype;
  const origDraw = proto.drawImage;
  const origFill = proto.fillRect;
  const yes = c => c && c.tagName === 'CANVAS' && c.width >= 300 && c.height >= 300;

  function mtx(m) {
    return [m.a, m.b, m.c, m.d, m.e, m.f].map(v => Math.round(v * 1000) / 1000);
  }
  function bump(map, key, sample) {
    const e = map.get(key);
    if (e) e.n++;
    else map.set(key, { n: 1, sample });
  }

  proto.drawImage = function () {
    if (P.active && yes(this.canvas)) {
      const a = arguments;
      const m = mtx(this.getTransform());
      const key = Array.prototype.join.call(a, ',') + '|' + m.join(',');
      bump(P.draws, key, {
        args: Array.prototype.slice.call(a).map(v => typeof v === 'number' ? Math.round(v * 1000) / 1000 : '[img]'),
        ctm: m,
        canvas: [this.canvas.width, this.canvas.height]
      });
    }
    return origDraw.apply(this, arguments);
  };
  proto.fillRect = function (x, y, w, h) {
    if (P.active && yes(this.canvas)) {
      const m = mtx(this.getTransform());
      if (String(this.fillStyle).toLowerCase() === '#4ec0ca') P.frames++;
      const key = [x, y, w, h, this.fillStyle].join('|') + '|' + m.join(',');
      bump(P.fills, key, {
        rect: [x, y, w, h].map(v => Math.round(v * 1000) / 1000),
        fill: String(this.fillStyle),
        ctm: m,
        canvas: [this.canvas.width, this.canvas.height]
      });
    }
    return origFill.apply(this, arguments);
  };

  // Count completed frames: the renderer clears the frame with this exact fill first.
  const origClear = proto.fillRect;
  P.start = () => {
    P.draws.clear(); P.fills.clear(); P.frames = 0;
    P.started = performance.now(); P.active = true;
  };
  P.stop = () => {
    P.active = false; P.stopped = performance.now();
    return P.report();
  };
  P.report = () => ({
    ms: Math.round((P.stopped || performance.now()) - P.started),
    frames: P.frames,
    canvases: [...document.querySelectorAll('canvas')].map(c => ({
      w: c.width, h: c.height, cw: c.clientWidth, ch: c.clientHeight, id: c.id || null
    })),
    draws: [...P.draws.values()],
    fills: [...P.fills.values()]
  });

  // ---- window scan ---------------------------------------------------------
  const STATE_RE = /^(bird|birdy|birdvy|birdrot|velocity|vy|gravity|pipes?|gapy|halfgap|score|state|speed)$/i;
  function scanWindow() {
    const seen = new Set();
    const hits = [];
    function walk(o, path, depth) {
      if (!o || typeof o !== 'object' || depth > 2 || seen.has(o) || o === document) return;
      if (o instanceof Node || o instanceof Window || o instanceof Event) return;
      seen.add(o);
      let keys;
      try { keys = Object.keys(o); } catch (e) { return; }
      if (!keys.length || keys.length > 200) return;
      const interesting = keys.filter(k => STATE_RE.test(k));
      const arr = Array.isArray(o) ? o : null;
      const pipeish = arr && arr.length && arr.slice(0, 3).every(v =>
        v && typeof v === 'object' && ('x' in v) && ('gapY' in v || 'y' in v));
      if (interesting.length >= 2 || pipeish) {
        const sample = {};
        for (const k of interesting.slice(0, 12)) {
          const v = o[k];
          sample[k] = (typeof v === 'number' || typeof v === 'string' || typeof v === 'boolean') ? v :
                      (Array.isArray(v) ? `[array ${v.length}]` : typeof v);
        }
        hits.push({ path, keys: keys.slice(0, 24), interesting, pipeish: !!pipeish,
                    length: arr ? arr.length : undefined, sample });
      }
      for (const k of keys.slice(0, 60)) {
        let v;
        try { v = o[k]; } catch (e) { continue; }
        if (v && typeof v === 'object' && !(v instanceof Node) && k !== 'window' && k !== 'self' &&
            k !== 'parent' && k !== 'top' && k !== 'document' && k !== 'location') {
          walk(v, path + '.' + k, depth + 1);
        }
      }
    }
    for (const k of Object.keys(window)) {
      let v;
      try { v = window[k]; } catch (e) { continue; }
      if (v && typeof v === 'object') walk(v, 'window.' + k, 0);
    }
    return hits;
  }

  P.scan = () => {
    const game = window.__game || null;
    const globals = Object.getOwnPropertyNames(window).map(k => {
      let v; try { v = window[k]; } catch (e) { return [k, '?', false]; }
      const fn = typeof v === 'function';
      return [k, typeof v, fn && /\{\s*\[native code\]\s*\}/.test(Function.prototype.toString.call(v))];
    });
    return {
      globals,
      hits: scanWindow(),
      game: game ? {
        ctor: game.constructor && game.constructor.name,
        keys: Object.keys(game),
        state: game.state, score: game.score, simVersion: game.simVersion,
        playStep: game.playStep, birdY: game.birdY, birdVy: game.birdVy,
        pipes: (game.pipes || []).slice(0, 5),
        snapshot: (() => { try { return game.snapshot; } catch (e) { return String(e); } })()
      } : null
    };
  };
})();
"""


def pilot(page, seconds, target=0.12):
    """Crude altitude hold so the recon run survives long enough to show a live pipe."""
    end = time.time() + seconds
    while time.time() < end:
        try:
            st = page.evaluate("() => ({s: __game.state, y: __game.snapshot.birdY, vy: __game.birdVy})")
        except Exception:
            st = None
        if st:
            if st["s"] == "gameover":
                page.evaluate("__game.restart(); __game.flap()")
            elif st["s"] == "getready":
                page.evaluate("__game.flap()")
            elif st["y"] < target and st["vy"] < 0:
                page.evaluate("__game.flap()")
        page.wait_for_timeout(35)


def main():
    exe = chromium_path()
    print(f"[*] chromium: {exe}")
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            executable_path=exe,
            args=["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage", "--mute-audio"],
        )
        baseline = browser.new_page()
        baseline.goto("about:blank")
        base_names = set(baseline.evaluate("Object.getOwnPropertyNames(window)"))
        baseline.close()

        page = browser.new_page(viewport={"width": 1280, "height": 800})
        page.add_init_script(PROBE_JS)
        page.on("pageerror", lambda e: print(f"[page error] {e}"))
        print(f"[*] opening {URL}")
        page.goto(URL, wait_until="domcontentloaded", timeout=60000)

        print("[*] waiting for window.__game ...")
        t0 = time.time()
        while time.time() - t0 < 30:
            try:
                ok = page.evaluate("!!(window.__game && window.__game.state)")
            except Exception:
                ok = False
            if ok:
                break
            page.wait_for_timeout(200)
        else:
            print("[!] __game never appeared")
            print(page.evaluate("Object.getOwnPropertyNames(window).filter(k => /game|bird|pipe/i.test(k))"))
            browser.close()
            return

        print("[*] game found; flying a simple altitude hold for 2 s before capture")
        pilot(page, 2.0)
        page.evaluate("window.__fbProbe.start()")
        pilot(page, 1.0)
        rep = page.evaluate("window.__fbProbe.stop()")
        scan = page.evaluate("window.__fbProbe.scan()")

        # baseline window properties -> only app/third-party additions.
        # Browser APIs are lazily exposed, so also drop native constructors and event handlers.
        extra = []
        for name, kind, native in scan["globals"]:
            if name in base_names or native or name.startswith("on") or name[:1].isupper():
                continue
            extra.append(name)
        extra = sorted(extra)

        print("\n================ WINDOW ================")
        print(f"own properties: {len(scan['globals'])} total, "
              f"{sum(1 for n, _, _ in scan['globals'] if n not in base_names)} not present on about:blank")
        print("page/third-party globals (non-native, non-constructor, non-event-handler):")
        for k in extra:
            print(f"  {k}")

        print("\n================ CANDIDATE STATE OBJECTS ================")
        if not scan["hits"]:
            print("  none")
        for h in scan["hits"]:
            print(f"  path={h['path']}  interesting={h['interesting']}  pipeish={h['pipeish']}"
                  + (f"  len={h['length']}" if h.get("length") is not None else ""))
            print(f"    keys={h['keys']}")
            print(f"    sample={json.dumps(h['sample'], default=str)}")

        print("\n================ window.__game ================")
        g = scan.get("game")
        if not g:
            print("  absent")
        else:
            print(f"  constructor={g['ctor']}")
            print(f"  keys={g['keys']}")
            print(f"  state={g['state']} score={g['score']} simVersion={g['simVersion']} playStep={g['playStep']}")
            print(f"  birdY={g['birdY']} birdVy={g['birdVy']}")
            print(f"  pipes={json.dumps(g['pipes'], default=str)}")
            print(f"  snapshot={json.dumps(g['snapshot'], default=str)}")

        print("\n================ CANVASES ================")
        for c in rep["canvases"]:
            print(f"  {c}")

        print(f"\n================ DRAW CAPTURE ({rep['ms']} ms) ================")
        print(f"unique drawImage signatures: {len(rep['draws'])}")
        rows = []
        for d in rep["draws"]:
            a = d["sample"]["args"]
            rows.append((tuple(a[1:5]), d))
        rows.sort(key=lambda x: (x[0], -x[1]["n"]))
        for _, d in rows:
            a = d["sample"]["args"]
            if len(a) >= 9:
                print(f"  n={d['n']:4d} src=({a[1]},{a[2]},{a[3]},{a[4]}) "
                      f"dst=({a[5]},{a[6]},{a[7]},{a[8]}) ctm={d['sample']['ctm']} "
                      f"canvas={d['sample']['canvas']}")
            else:
                print(f"  n={d['n']:4d} args={a} ctm={d['sample']['ctm']} canvas={d['sample']['canvas']}")

        print(f"\n================ fillRect CAPTURE ================")
        print(f"unique fillRect signatures: {len(rep['fills'])}")
        for f in sorted(rep["fills"], key=lambda x: -x["n"]):
            s = f["sample"]
            print(f"  n={f['n']:4d} rect={s['rect']} fill={s['fill']} ctm={s['ctm']} canvas={s['canvas']}")

        browser.close()


if __name__ == "__main__":
    main()
