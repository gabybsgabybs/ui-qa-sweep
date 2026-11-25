# ui-qa-sweep

A repeatable UI QA sweep for web pages: it runs a page through five phone sizes, a tablet and a desktop, in light and dark mode, on a fast connection, on slow 3G with a throttled CPU, and offline — then reports the bugs a desktop browser hides, with a screenshot and the offending element for each one.

It came out of a real launch: a personal site that looked perfect on a laptop and was unusable on a phone — it opened halfway down the page, the header covered the section nav, and taps fought the scroll.

## What it finds

- Horizontal overflow (the fixed-width card at 320px)
- Content stuck under a fixed or sticky top bar
- In-page anchor links that land under the header
- Tap targets under 44x44 (WCAG 2.5.5)
- Text clipped without an ellipsis at narrow widths
- Contrast below WCAG AA, computed for the active colour scheme
- Broken images
- Stuck overlays and cookie walls
- Page jumps while loading, CLS, JS errors, failed requests
- Whether scrolling still works while assets arrive on slow 3G
- Offline behaviour (only worth acting on if the page is meant to work offline)

## Install

Any agent that reads `SKILL.md` skill folders can use this one as-is: drop `SKILL.md` and `scripts/` into its skills directory.

```bash
git clone https://github.com/gabybsgabybs/ui-qa-sweep.git
cp -r ui-qa-sweep ~/.opencode/skills/ui-qa-sweep    # or wherever your skills live
```

Requirements: Python 3.9+, `pip install playwright`, and a Chrome or Chromium binary (the script looks for `/usr/bin/google-chrome` and falls back to Playwright's own Chromium).

## Run it

```bash
python3 scripts/qa_sweep.py https://example.com --out /tmp/qa
python3 scripts/qa_sweep.py ./index.html --out /tmp/qa          # local file works too
python3 scripts/qa_sweep.py https://example.com --out /tmp/qa --quick   # phones only, ~1 min
python3 scripts/qa_sweep.py https://example.com --out /tmp/qa \
  --viewports "iPhone14-390,pixel7-412" --schemes dark
```

Output lands in the `--out` folder:

- `report.md` — findings ranked by severity, each with the viewport, the element path and the measurement
- `report.json` — the same, machine-readable
- `shots/*.png` — one screenshot per configuration

Exit code is `1` when something high-severity is found, `0` when clean, `2` when the page never loaded, so it drops into CI as a gate.

## Matrix

| Dimension | Values |
|---|---|
| Phones | 320x568, 360x640, 390x844, 412x915, 430x932 |
| Tablet / desktop | 768x1024, 1440x900 |
| Colour scheme | light and dark on every viewport |
| Network | fast, 400 kbps + 350 ms latency with 4x CPU throttle, offline |

Full matrix runs in about three minutes; `--quick` in about one.

## Limits, said out loud

Headless Chrome is not real Safari: iOS `100vh`, rubber-band scroll, input zoom and sticky quirks only show on a device. Thumb reach and gesture feel need a human. A clean report means no automated finding, not that the design is right.

## Licence

MIT — see `LICENSE`.
