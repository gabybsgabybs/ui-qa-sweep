---
name: ui-qa-sweep
description: >-
  Sweeps a page across phone, tablet and desktop viewports, light and dark
  mode, slow 3G with a throttled CPU, and offline - then reports layout,
  tap-target, overlap, contrast and load-jump bugs with evidence and
  screenshots. Use when asked to QA or check a site/page on mobile or across
  devices, when a UI bug needs reproducing at phone sizes, or before shipping
  a page change.
metadata:
  category: qa
---

# UI QA sweep

A repeatable pass that finds the class of bugs a desktop browser hides: sideways
scrolling at 320px, content stuck under a fixed header, taps that miss, text
clipped at one width, dark mode where the contrast falls apart, and pages that
jump while loading on a slow connection.

Built from a real case (gabrielabelo.com, Nov 2026) where the desktop looked
fine and the phone was unusable: on-load page jump, scroll fighting on taps, the
chip rail hiding behind the mobile header.

## Run it

```bash
python3 scripts/qa_sweep.py https://example.com --out /tmp/qa
python3 scripts/qa_sweep.py ./index.html --out /tmp/qa        # local file works
python3 scripts/qa_sweep.py https://example.com --out /tmp/qa --quick   # phones only, ~1 min
python3 scripts/qa_sweep.py https://example.com --out /tmp/qa --viewports "iPhone14-390,pixel7-412" --schemes dark
```

Read `<out>/report.md` (ranked findings, each with the viewport it appears in and
the offending element), `<out>/report.json` (same, machine-readable) and
`<out>/shots/*.png` (one screenshot per configuration). Exit code is 1 when
something high-severity was found, 0 when clean, 2 when the page never loaded.

Requires Playwright (`pip install playwright`) and a Chrome/Chromium binary; the
script finds `/usr/bin/google-chrome` or falls back to Playwright's own Chromium.

## Matrix it covers

| Dimension | Values |
|---|---|
| Phone | 320x568 (oldest iPhone SE), 360x640 (budget Android), 390x844 (iPhone 14), 412x915 (Pixel 7), 430x932 (Pro Max) |
| Tablet / desktop | 768x1024 (iPad mini), 1440x900 |
| Colour scheme | light and dark (`prefers-color-scheme`), every viewport |
| Network | fast, 400kbps + 350ms latency with 4x CPU throttle (slow 3G), offline |

`--quick` keeps the three phones x light plus one slow-3G and one offline run.
The full matrix takes 2-3 minutes; `--quick` about one.

For the site this was built from (gabrielabelo.com), sweep the live URL or the
local clone at `~/repos/personal-site` before pushing a change, and again on the
deployed URL after Pages updates.

## What it checks

1. **Horizontal overflow** - the page scrolls sideways, with the elements whose
   right edge crosses the viewport (the classic fixed-width card at 320px).
2. **Scroll locked** - content taller than the screen with `overflow: hidden` on
   html/body. Intro animations legitimately lock scroll for a moment, so the
   script waits up to 6s for the lock to release and only reports one that never
   does.
3. **Tap targets** - block-level links, buttons and inputs under 44x44 (WCAG
   2.5.5), touch viewports only. Nav and language links are the usual offenders.
4. **Content under a fixed/sticky top bar** - the element top is inside the bar
   rectangle at scroll 0.
5. **In-page anchors landing under the header** - clicks a `#section` link and
   measures where the target lands (the chip-rail bug).
6. **Text clipped without an ellipsis** - box narrower than its text with
   `overflow: hidden`. Collapsed-by-animation boxes (width < 4px) are ignored.
7. **Broken images** - `complete` with `naturalWidth === 0`.
8. **Contrast (WCAG AA)** - computed text and background colours, 4.5:1 normal /
   3:1 large. Solid backgrounds only; gradient and image backgrounds are skipped.
9. **Stuck overlays** - a fixed layer covering >60% of the screen that has text
   or interactive content (a cookie wall or modal that never dismisses).
10. **Load behaviour** - scroll position sampled for 3s (jump detection), CLS via
    `layout-shift`, first contentful paint, JS errors, and failed requests/HTTP
    4xx-5xx.
11. **Slow 3G interactivity** - can the page still scroll while assets are still
    arriving.

## How to work a report

1. Start with the HIGH findings that appear in the most configurations - those
   are the ones that hit real phones.
2. Open the screenshot for the viewport named in the finding before changing
   code; the element path in the finding tells you what to inspect.
3. Fix, then re-run only that configuration
   (`--viewports "iPhone14-390" --schemes light`) while iterating, and the full
   matrix once before saying it is done.
4. Report the finding and the fix with the numbers (320px overflow of 44px, tap
   target 24x17) - "mobile is fixed" is not a finding.

## Limits - say these out loud

- It is **headless Chrome**, not real Safari. iOS Safari's `100vh`, rubber-band
  scroll, input zoom and `position: sticky` quirks only show on a device.
- Thumb reach, gesture feel and "does it feel right" need a human on a phone.
- Auth-gated or personalized pages and anything behind a hard paywall cannot be
  swept without a session.
- A passing report means no automated finding, not that the design is right.
- Offline only matters if the page is meant to be a PWA; a plain marketing site
  failing offline is normal and the finding is LOW.
- Contrast is computed from `getComputedStyle`, so text over images or gradients
  is skipped rather than guessed.
