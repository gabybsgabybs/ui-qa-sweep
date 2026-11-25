# ui-qa-sweep

A repeatable UI QA sweep for web pages: it runs a page through five phone sizes, a tablet and a desktop, in light and dark mode, on a fast connection, on slow 3G with a throttled CPU, offline and with reduced motion requested — then reports the bugs a desktop browser hides, with a screenshot and the offending element for each one.

It came out of a real launch: a personal site that looked perfect on a laptop and was unusable on a phone — it opened halfway down the page, the header covered the section nav, and taps fought the scroll.

## What it finds

- Horizontal overflow (the fixed-width card at 320px)
- Content stuck under a fixed or sticky top bar
- In-page anchor links that land under the header
- Tap targets under 44x44 (WCAG 2.5.5)
- Text clipped without an ellipsis at narrow widths
- Contrast below WCAG AA, computed for the active colour scheme
- Form fields under 16px text, which make iOS zoom the page on focus
- Links and buttons with no accessible name
- Keyboard stops with no visible focus indicator
- Animations that keep running with `prefers-reduced-motion: reduce`
- Broken images, stuck overlays and cookie walls
- Page jumps while loading, CLS, JS errors, failed requests
- Page weight and request count
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

- `report.md` — findings ranked by severity, each with the viewport, the element path, the measurement and a crop of the offending element
- `report.json` — the same, machine-readable, with a fingerprint per finding
- `shots/*.png` — one full-page screenshot per configuration
- `shots/elem/*.png` — a crop of the element behind each finding, so the report shows the bug

Flags that matter:

| Flag | What it does |
|---|---|
| `--quick` | three phones in light mode plus one slow-3G and one offline run (~1 min) |
| `--viewports "iPhone14-390,desktop-1440"` | run part of the matrix while iterating |
| `--schemes light` | one colour scheme |
| `--fail-on med` | gate on medium findings too (default `high`, `none` disables the gate) |
| `--baseline qa/baseline.json` | compare against an earlier run and only fail on new or changed findings |
| `--save-baseline` | write `qa/baseline.json` for later comparison |
| `--no-focus` | skip the keyboard pass |
| `--no-motion` | skip the reduced-motion run |
| `--no-shots` | skip screenshots (faster) |

## Use it as a gate, not a report

The first sweep of an existing site finds everything that was already there, which makes a plain "any high finding fails" gate useless. Save a baseline once, then compare:

```bash
python3 scripts/qa_sweep.py https://example.com --out qa --save-baseline     # once
python3 scripts/qa_sweep.py https://example.com --out qa \
  --baseline qa/baseline.json --fail-on high                                # on every change
```

New findings are marked `NEW` at the top of the report, pre-existing ones are listed under them, and findings that disappeared since the baseline are listed as fixed. The exit code is 0 when nothing new crosses the bar, 1 when something does, 2 when the page never loaded.

`.github/workflows/ui-qa.yml` in this repo does exactly that on GitHub: `workflow_dispatch` with a URL input, plus a weekly run. It installs Playwright, sweeps, writes the report into the run summary, uploads the screenshots as an artifact and fails only on a regression. The same file drops into a site repository as-is — point the `url` input at that site's deployed address and commit a baseline for its host under `baseline/`.

## Matrix

| Dimension | Values |
|---|---|
| Phones | 320x568, 360x640, 390x844, 412x915, 430x932 |
| Tablet / desktop | 768x1024, 1440x900 |
| Colour scheme | light and dark on every viewport |
| Network | fast, 400 kbps + 350 ms latency with 4x CPU throttle, offline |
| Preferences | `prefers-reduced-motion: reduce`, keyboard-only tab order |

Full matrix runs in about three minutes; `--quick` in about one.

## How to work a report

1. Start with the `NEW` findings that appear in the most configurations — those are the ones hitting real phones.
2. Open the crop for the finding before touching code; it shows the element and the space around it.
3. Fix, then re-run only that configuration (`--viewports "iPhone14-390" --schemes light`) while iterating, and the full matrix once before saying it is done.
4. Re-save the baseline only after a human has seen the site on a phone.

## Limits, said out loud

Headless Chrome is not real Safari: iOS `100vh`, rubber-band scroll, input zoom timing and sticky quirks only show on a device. Thumb reach and gesture feel need a human. Contrast is computed from `getComputedStyle`, so text over gradients or images is skipped rather than guessed. A clean report means no automated finding, not that the design is right.

## Licence

MIT — see `LICENSE`.
