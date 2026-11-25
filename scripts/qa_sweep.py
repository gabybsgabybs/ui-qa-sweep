#!/usr/bin/env python3
"""qa_sweep.py - multi-device UI sweep for a live URL or local HTML file.

Runs a page through phone / tablet / desktop viewports, light and dark mode,
slow 3G + throttled CPU and offline, then reports layout, tap-target,
overlap, load-jump and contrast problems with evidence.

Usage:
    python3 qa_sweep.py https://example.com --out /tmp/qa
    python3 qa_sweep.py ./index.html --out /tmp/qa --quick
    python3 qa_sweep.py https://example.com --viewports "iPhone 14,Pixel 7" --schemes dark

Outputs: <out>/report.md, <out>/report.json, <out>/shots/*.png
Exit code: 0 = no high-severity findings, 1 = high-severity findings,
           2 = the page could not be loaded at all.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright, Error as PWError, TimeoutError as PWTimeout
except ImportError:  # pragma: no cover
    sys.exit("playwright is missing: pip install playwright")

CHROME_CANDIDATES = [
    "/usr/bin/google-chrome",
    "/usr/bin/google-chrome-stable",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
]

VIEWPORTS = {
    # name: (width, height, mobile)
    "small-320": (320, 568, True),        # iPhone SE 1st gen / oldest phones
    "android-360": (360, 640, True),      # Galaxy A / common budget Android
    "iPhone14-390": (390, 844, True),     # the default phone target
    "pixel7-412": (412, 915, True),       # common large Android
    "iPhoneMax-430": (430, 932, True),    # iPhone 14 Pro Max
    "tablet-768": (768, 1024, True),      # iPad mini portrait
    "desktop-1440": (1440, 900, False),
}

MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)

# Measures layout health. Returns a dict; never throws for page-side oddities.
CHECK_JS = r"""
(vwIn) => {
  const vw = vwIn || window.innerWidth, vh = window.innerHeight;
  const doc = document.scrollingElement || document.documentElement;
  const out = {viewport: {w: vw, h: vh}, issues: []};

  const path = (el) => {
    if (!el || el.nodeType !== 1) return '';
    const parts = [];
    let node = el;
    while (node && node.nodeType === 1 && parts.length < 5) {
      let s = node.tagName.toLowerCase();
      if (node.id) { s += '#' + node.id; parts.unshift(s); break; }
      const cls = (typeof node.className === 'string' ? node.className : '')
        .trim().split(/\s+/).filter(Boolean).slice(0, 2);
      if (cls.length) s += '.' + cls.join('.');
      const p = node.parentElement;
      if (p) {
        const same = Array.from(p.children).filter(c => c.tagName === node.tagName);
        if (same.length > 1) s += ':nth-of-type(' + (same.indexOf(node) + 1) + ')';
      }
      parts.unshift(s);
      node = p;
    }
    return parts.join(' > ');
  };
  const visible = (el) => {
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden' || parseFloat(cs.opacity) === 0) return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  const add = (sev, check, msg, extra) => out.issues.push(Object.assign({sev, check, msg}, extra || {}));

  // 1. horizontal overflow (page scrolls sideways)
  if (doc.scrollWidth > vw + 1) {
    const offenders = [];
    for (const el of document.querySelectorAll('body *')) {
      const r = el.getBoundingClientRect();
      if (r.width === 0 || r.height === 0) continue;
      const cs = getComputedStyle(el);
      if (cs.position === 'fixed' || !visible(el)) continue;
      if (r.right > vw + 1 || r.left < -1) {
        offenders.push({sel: path(el), left: Math.round(r.left), right: Math.round(r.right), w: Math.round(r.width)});
        if (offenders.length >= 6) break;
      }
    }
    add('high', 'overflow', 'page scrolls horizontally (' + doc.scrollWidth + 'px wide in a ' + vw + 'px viewport)',
        {data: {scrollWidth: doc.scrollWidth, offenders}});
  }

  // 2. page cannot scroll at all although content is taller than the screen
  const sh = Math.max(doc.scrollHeight, document.body ? document.body.scrollHeight : 0);
  const locked = ['hidden', 'clip'].includes(getComputedStyle(document.documentElement).overflowY)
              || ['hidden', 'clip'].includes(getComputedStyle(document.body).overflowY);
  if (sh > vh + 80 && locked) {
    add('high', 'scroll-lock', 'content is taller than the screen but scrolling is disabled (overflow hidden on html/body)',
        {data: {docHeight: sh, viewportHeight: vh}});
  }

  // 3. tap targets below 44x44 (WCAG 2.5.5) - block-level only, inline text links exempt
  const targets = [];
  const touch = vw <= 1024 && matchMedia('(pointer: coarse)').matches;
  const interactive = document.querySelectorAll(
    'a[href], button, input:not([type=hidden]), select, textarea, [role=button], [onclick]');
  for (const el of interactive) {
    if (!visible(el)) continue;
    const r = el.getBoundingClientRect();
    const cs = getComputedStyle(el);
    if (cs.display === 'inline' && el.tagName === 'A') continue;
    if (r.bottom < 0 || r.top > vh) continue;
    if (r.width < 44 || r.height < 44) {
      targets.push({sel: path(el), w: Math.round(r.width), h: Math.round(r.height),
                    text: (el.innerText || el.value || el.getAttribute('aria-label') || '').trim().slice(0, 40)});
    }
  }
  if (touch && targets.length) {
    const tiny = targets.filter(t => t.w < 32 || t.h < 32).length;
    add(tiny ? 'high' : 'med', 'tap-targets',
        targets.length + ' tap target(s) under 44x44 in the viewport' + (tiny ? ', ' + tiny + ' under 32px' : ''),
        {data: {count: targets.length, examples: targets.slice(0, 6)}});
  }

  // 4. fixed / sticky top bars that swallow content
  const bars = [];
  for (const el of document.querySelectorAll('body *')) {
    const cs = getComputedStyle(el);
    if (cs.position !== 'fixed' && cs.position !== 'sticky') continue;
    const r = el.getBoundingClientRect();
    if (r.top <= 4 && r.height >= 24 && r.width >= vw * 0.5 && r.height < vh * 0.5) {
      bars.push({el, height: r.height, sel: path(el)});
    }
  }
  if (bars.length) {
    const tallest = bars.reduce((a, b) => (b.height > a.height ? b : a));
    const barRects = bars.map(b => b.el.getBoundingClientRect());
    const hidden = [];
    for (const el of document.querySelectorAll('h1, h2, h3, h4, p, li, img, ul, section, form')) {
      if (!visible(el)) continue;
      const r = el.getBoundingClientRect();
      if (r.top >= r.bottom) continue;
      if (r.top > vh || r.bottom < 0) continue;
      if (r.height < 8) continue;
      let inside = false;
      for (const b of bars) if (b.el === el || b.el.contains(el)) inside = true;
      if (inside) continue;
      for (const br of barRects) {
        if (r.top < br.bottom - 2 && r.bottom > br.top + 2
            && r.left < br.right && r.right > br.left) {
          hidden.push({sel: path(el), top: Math.round(r.top), barHeight: Math.round(br.height)});
          break;
        }
      }
      if (hidden.length >= 5) break;
    }
    if (hidden.length) {
      add('high', 'under-header', 'content sits under a fixed/sticky top bar (top bar ' + Math.round(tallest.height) + 'px tall)',
          {data: {bar: tallest.sel, barHeight: Math.round(tallest.height), examples: hidden}});
    }
  }

  // 5. text clipped without an ellipsis
  const clipped = [];
  for (const el of document.querySelectorAll('h1,h2,h3,h4,p,li,span,a,button,td,th,label')) {
    if (!visible(el)) continue;
    const cs = getComputedStyle(el);
    if (!['hidden', 'clip'].includes(cs.overflowX) && !['hidden', 'clip'].includes(cs.overflow)) continue;
    if (cs.textOverflow === 'ellipsis') continue;
    if (el.children.length > 0 && (el.innerText || '').length > 200) continue;
    if (el.clientWidth < 4 || el.clientHeight < 4) continue;  // collapsed by animation
    if (el.scrollWidth > el.clientWidth + 8 && (el.innerText || '').trim().length > 0) {
      clipped.push({sel: path(el), scrollWidth: el.scrollWidth, clientWidth: el.clientWidth,
                    text: (el.innerText || '').trim().slice(0, 50)});
    }
    if (clipped.length >= 5) break;
  }
  if (clipped.length) {
    add('med', 'text-clipped', clipped.length + ' element(s) clipping text without an ellipsis',
        {data: {examples: clipped}});
  }

  // 6. broken images
  const broken = [];
  for (const img of document.images) {
    if (img.complete && img.naturalWidth === 0 && img.getAttribute('src')) {
      broken.push({sel: path(img), src: (img.currentSrc || img.src || '').slice(0, 120)});
    }
    if (broken.length >= 5) break;
  }
  if (broken.length) add('high', 'broken-image', broken.length + ' image(s) failed to load', {data: {examples: broken}});

  // 7. text contrast (WCAG AA) for solid-background text
  const parseColor = (c) => {
    const m = c && c.match(/rgba?\(([\d.]+),\s*([\d.]+),\s*([\d.]+)(?:,\s*([\d.]+))?\)/);
    if (!m) return null;
    return {r: +m[1], g: +m[2], b: +m[3], a: m[4] === undefined ? 1 : +m[4]};
  };
  const lum = (c) => {
    const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
    return 0.2126 * f(c.r) + 0.7152 * f(c.g) + 0.0722 * f(c.b);
  };
  const ratio = (a, b) => {
    const L1 = lum(a), L2 = lum(b), hi = Math.max(L1, L2), lo = Math.min(L1, L2);
    return (hi + 0.05) / (lo + 0.05);
  };
  const bgOf = (el) => {
    let node = el, found = null;
    while (node && node.nodeType === 1) {
      const cs = getComputedStyle(node);
      if (cs.backgroundImage && cs.backgroundImage !== 'none') return null;
      const c = parseColor(cs.backgroundColor);
      if (c && c.a >= 0.85) return c;
      if (c && c.a > 0.1) found = c;
      node = node.parentElement;
    }
    return found || {r: 255, g: 255, b: 255, a: 1};
  };
  const low = [];
  const textEls = document.querySelectorAll('h1,h2,h3,h4,h5,p,li,a,button,span,label,td,blockquote');
  let scanned = 0;
  for (const el of textEls) {
    if (scanned > 400) break;
    if (!visible(el)) continue;
    if (el.children.length > 0) continue;
    const txt = (el.innerText || '').trim();
    if (txt.length < 2) continue;
    const cs = getComputedStyle(el);
    const fg = parseColor(cs.color);
    if (!fg || fg.a < 0.5) continue;
    const bg = bgOf(el);
    if (!bg) { scanned++; continue; }
    const size = parseFloat(cs.fontSize) || 16;
    const bold = (parseInt(cs.fontWeight, 10) || 400) >= 700;
    const large = size >= 24 || (size >= 18.66 && bold);
    const need = large ? 3 : 4.5;
    const r = ratio(fg, bg);
    scanned++;
    if (r < need) {
      low.push({sel: path(el), ratio: Math.round(r * 100) / 100, need, size: Math.round(size),
                text: txt.slice(0, 40), color: cs.color, bg: 'rgb(' + bg.r + ',' + bg.g + ',' + bg.b + ')'});
    }
    if (low.length >= 6) break;
  }
  if (low.length) {
    add('med', 'contrast', low.length + ' text element(s) below WCAG AA contrast',
        {data: {examples: low}});
  }

  // 8. fixed elements eating the screen (stuck overlay / cookie wall / modal)
  const bigFixed = [];
  for (const el of document.querySelectorAll('body *')) {
    const cs = getComputedStyle(el);
    if (cs.position !== 'fixed') continue;
    const r = el.getBoundingClientRect();
    const interactive = el.querySelector('a[href], button, input, select, textarea, [role=button]');
    const texty = (el.innerText || '').trim().length > 40;
    if (r.width * r.height > vw * vh * 0.6 && cs.pointerEvents !== 'none' && (interactive || texty)) {
      bigFixed.push({sel: path(el), w: Math.round(r.width), h: Math.round(r.height)});
    }
    if (bigFixed.length >= 3) break;
  }
  if (bigFixed.length) {
    add('med', 'fixed-overlay', 'a fixed element covers more than 60% of the screen', {data: {examples: bigFixed}});
  }

  // informational: fixed bottom bars
  out.bottomBars = [];
  for (const el of document.querySelectorAll('body *')) {
    const cs = getComputedStyle(el);
    if (cs.position !== 'fixed') continue;
    const r = el.getBoundingClientRect();
    if (r.bottom >= vh - 4 && r.height >= 32 && r.width >= vw * 0.5 && r.height < vh * 0.5) {
      out.bottomBars.push({sel: path(el), height: Math.round(r.height)});
      if (out.bottomBars.length >= 3) break;
    }
  }
  out.docHeight = sh;
  return out;
}
"""


ANCHOR_JS = r"""
() => {
  const w = document.documentElement.clientWidth;
  const bars = [...document.querySelectorAll('body *')].filter(el => {
    const cs = getComputedStyle(el);
    if (cs.position !== 'fixed' && cs.position !== 'sticky') return false;
    const r = el.getBoundingClientRect();
    return r.top <= 4 && r.height >= 24 && r.width >= w * 0.5 && r.height < window.innerHeight * 0.5;
  });
  const barH = bars.length ? Math.round(Math.max(...bars.map(b => b.getBoundingClientRect().height))) : 0;
  const links = [...new Set([...document.querySelectorAll('a[href^="#"]')]
    .map(a => a.getAttribute('href')).filter(h => h && h !== '#' && h.length > 1))];
  return {barH, links: links.slice(0, 3)};
}
"""

# Measures how the page behaves while it loads: scroll jumps and layout shift.
LOAD_JS = r"""
() => {
  const paint = performance.getEntriesByType('paint');
  const fcp = paint.find(p => p.name === 'first-contentful-paint');
  const nav = performance.getEntriesByType('navigation')[0] || {};
  return {
    scrollY: window.scrollY,
    fcp: fcp ? Math.round(fcp.startTime) : null,
    domContentLoaded: nav.domContentLoadedEventEnd ? Math.round(nav.domContentLoadedEventEnd) : null,
    load: nav.loadEventEnd ? Math.round(nav.loadEventEnd) : null,
    lcp: (window.__lcp || null),
    cls: (window.__cls || 0),
    longTasks: window.__longTasks || 0,
    docHeight: (document.scrollingElement || document.documentElement).scrollHeight,
  };
}
"""

INIT_SCRIPT = r"""
window.__cls = 0;
window.__longTasks = 0;
window.__lcp = null;
try {
  new PerformanceObserver((list) => {
    for (const e of list.getEntries()) {
      if (!e.hadRecentInput) window.__cls += e.value;
    }
  }).observe({type: 'layout-shift', buffered: true});
} catch (e) {}
try {
  new PerformanceObserver((list) => {
    const es = list.getEntries();
    window.__lcp = Math.round(es[es.length - 1].startTime);
  }).observe({type: 'largest-contentful-paint', buffered: true});
} catch (e) {}
try {
  new PerformanceObserver((list) => { window.__longTasks += list.getEntries().length; })
    .observe({type: 'longtask', buffered: true});
} catch (e) {}
"""

SEV_ORDER = {"high": 0, "med": 1, "low": 2}
SEV_LABEL = {"high": "HIGH", "med": "MED", "low": "LOW"}


def find_chrome() -> str | None:
    for p in CHROME_CANDIDATES:
        if os.path.exists(p):
            return p
    return None


def normalise_url(target: str) -> str:
    if target.startswith(("http://", "https://", "file://")):
        return target
    p = Path(target).expanduser().resolve()
    if p.exists():
        return p.as_uri()
    return "https://" + target


def run_once(browser, url, vp_name, scheme, net, shots_dir, mobile=True):
    """One page load in one configuration. Returns a run record."""
    w, h = VIEWPORTS[vp_name][0], VIEWPORTS[vp_name][1]
    record = {
        "viewport": vp_name, "width": w, "height": h, "scheme": scheme, "net": net,
        "issues": [], "console": [], "failed_requests": [], "metrics": {}, "shot": None,
    }
    ctx_args = dict(
        viewport={"width": w, "height": h},
        device_scale_factor=3 if mobile else 1,
        is_mobile=mobile,
        has_touch=mobile,
        color_scheme=scheme,
    )
    if mobile:
        ctx_args["user_agent"] = MOBILE_UA
    context = browser.new_context(**ctx_args)
    page = context.new_page()
    page.add_init_script(INIT_SCRIPT)

    page.on("console", lambda m: record["console"].append({"type": m.type, "text": m.text[:300]})
            if m.type in ("error", "warning") else None)
    page.on("pageerror", lambda e: record["console"].append({"type": "pageerror", "text": str(e)[:300]}))
    page.on("requestfailed", lambda r: record["failed_requests"].append(
        {"url": r.url[:160], "reason": (r.failure or "failed")}))
    page.on("response", lambda r: record["failed_requests"].append(
        {"url": r.url[:160], "reason": "HTTP %d" % r.status})
        if r.status >= 400 and "favicon" not in r.url else None)

    cdp = context.new_cdp_session(page)
    cdp.send("Network.enable")
    if net == "slow3g":
        cdp.send("Network.emulateNetworkConditions", {
            "offline": False, "latency": 350,
            "downloadThroughput": int(400 * 1024 / 8),
            "uploadThroughput": int(400 * 1024 / 8),
        })
        cdp.send("Emulation.setCPUThrottlingRate", {"rate": 4})
    elif net == "offline":
        context.set_offline(True)

    start = time.time()
    try:
        page.goto(url, wait_until="load", timeout=45000)
    except PWTimeout:
        record["issues"].append({"sev": "high", "check": "timeout",
                                 "msg": "page did not finish loading within 45s"})
    except PWError as e:
        if net == "offline":
            record["issues"].append({"sev": "low", "check": "offline",
                                     "msg": "no offline fallback - the page fails to open with the connection down (only matters if it should be a PWA)",
                                     "data": {"error": str(e)[:200]}})
        else:
            record["issues"].append({"sev": "high", "check": "load-failed",
                                     "msg": "page could not be loaded", "data": {"error": str(e)[:200]}})
        record["load_ms"] = int((time.time() - start) * 1000)
        context.close()
        return record

    record["load_ms"] = int((time.time() - start) * 1000)

    # scroll jump sampling: on-load repositioning is a classic mobile bug
    samples = []
    for delay in (0.05, 0.25, 0.8, 2.0):
        time.sleep(delay if not samples else delay - 0.05)
        try:
            samples.append({"t": round(time.time() - start, 2), "y": page.evaluate("window.scrollY")})
        except PWError:
            break
    ys = [s["y"] for s in samples]
    jump = (max(ys) - min(ys)) if ys else 0
    record["scroll_samples"] = samples
    if jump > 60:
        record["issues"].append({"sev": "high", "check": "scroll-jump",
                                 "msg": "page position jumps by %dpx while loading" % jump,
                                 "data": {"samples": samples}})

    try:
        record["metrics"] = page.evaluate(LOAD_JS)
    except PWError:
        pass

    cls = record["metrics"].get("cls") or 0
    if cls > 0.1:
        record["issues"].append({"sev": "high" if cls > 0.25 else "med", "check": "layout-shift",
                                 "msg": "layout shifts during load (CLS %.3f)" % cls,
                                 "data": {"cls": round(cls, 3)}})

    fcp = record["metrics"].get("fcp")
    if net == "slow3g" and fcp and fcp > 3000:
        record["issues"].append({"sev": "med", "check": "slow-first-paint",
                                 "msg": "first content paint takes %.1fs at 400kbps (throttled 3G)" % (fcp / 1000),
                                 "data": {"fcp_ms": fcp}})
    if net == "fast" and record["load_ms"] > 5000:
        record["issues"].append({"sev": "med", "check": "slow-load",
                                 "msg": "page load took %.1fs" % (record["load_ms"] / 1000)})

    # interactive check under throttling: can the page still scroll/tap?
    if net == "slow3g":
        try:
            scrollable = page.evaluate("(document.scrollingElement||document.documentElement).scrollHeight > window.innerHeight * 1.2")
            if not scrollable:
                raise PWError("page is shorter than the viewport")
            page.mouse.wheel(0, 400)
            time.sleep(0.4)
            moved = page.evaluate("window.scrollY")
            if moved < 50:
                record["issues"].append({"sev": "med", "check": "slow-scroll",
                                         "msg": "scrolling is unresponsive while the page is still loading",
                                         "data": {"scrollY": moved}})
            page.evaluate("window.scrollTo(0,0)")
        except PWError:
            pass

    if net != "offline":
        # intro animations and preloaders legitimately lock scroll for a moment - wait them out
        lock_js = ("['hidden','clip'].includes(getComputedStyle(document.body).overflowY)"
                   " || ['hidden','clip'].includes(getComputedStyle(document.documentElement).overflowY)")
        try:
            if page.evaluate(lock_js):
                t0 = time.time()
                for _ in range(12):
                    time.sleep(0.5)
                    if not page.evaluate(lock_js):
                        record["scroll_unlocked_after"] = round(time.time() - t0 + 0.5, 1)
                        break
                else:
                    record["still_locked"] = True
        except PWError:
            pass
        try:
            page.evaluate("window.scrollTo(0,0)")
            time.sleep(0.2)
            result = page.evaluate(CHECK_JS, w)
            record["issues"].extend(result.get("issues", []))
            record["bottomBars"] = result.get("bottomBars", [])
            anchor = page.evaluate(ANCHOR_JS)
            if anchor.get("barH"):
                bad = []
                for href in anchor.get("links", []):
                    page.evaluate("(h) => { const t = document.querySelector(h); if (t) t.scrollIntoView({block: 'start'}); }", href)
                    time.sleep(0.6)
                    top = page.evaluate("(h) => { const t = document.querySelector(h); return t ? Math.round(t.getBoundingClientRect().top) : null; }", href)
                    if top is not None and top < anchor["barH"] - 2:
                        bad.append({"href": href, "top": top, "barHeight": anchor["barH"]})
                page.evaluate("window.scrollTo(0,0)")
                if bad:
                    record["issues"].append({"sev": "high", "check": "anchor-under-header",
                                             "msg": "%d in-page link(s) land their target under the fixed header" % len(bad),
                                             "data": {"examples": bad}})
        except PWError as e:
            record["issues"].append({"sev": "low", "check": "checks-failed",
                                     "msg": "some in-page checks could not run", "data": {"error": str(e)[:160]}})

    errs = [c for c in record["console"]
            if c["type"] == "pageerror" or (c["type"] == "error" and "Failed to load resource" not in c["text"])]
    bad_status = [c for c in record["console"] if "Failed to load resource" in c["text"]]
    if bad_status:
        record["failed_requests"].extend({"url": "resource (see browser console)", "reason": "HTTP error"}
                                         for _ in bad_status[:5])
    if errs:
        record["issues"].append({"sev": "high", "check": "js-error",
                                 "msg": "%d JavaScript error(s) during load" % len(errs),
                                 "data": {"examples": errs[:5]}})
    fails = [r for r in record["failed_requests"] if "favicon" not in r["url"]]
    if len(fails) >= 2:
        record["issues"].append({"sev": "med", "check": "failed-requests",
                                 "msg": "%d network request(s) failed" % len(fails),
                                 "data": {"examples": fails[:5]}})

    shot_name = "%s_%s_%s.png" % (vp_name, scheme, net)
    try:
        height = page.evaluate("(document.scrollingElement||document.documentElement).scrollHeight")
        clip_h = min(max(height, h), 7000)
        page.evaluate("window.scrollTo(0,0)")
        page.screenshot(path=str(shots_dir / shot_name),
                        clip={"x": 0, "y": 0, "width": w, "height": clip_h})
        record["shot"] = "shots/" + shot_name
    except PWError:
        pass

    context.close()
    return record


def dedupe(records):
    """Collapse the same finding across viewports into one entry with a count."""
    grouped = {}
    for r in records:
        for i in r["issues"]:
            key = i["check"]
            g = grouped.setdefault(key, {"check": i["check"], "sev": i["sev"], "msg": i["msg"],
                                         "data": i.get("data"), "seen": []})
            g["seen"].append("%s/%s/%s" % (r["viewport"], r["scheme"], r["net"]))
            if SEV_ORDER[i["sev"]] < SEV_ORDER[g["sev"]]:
                g["sev"] = i["sev"]
            if len(str(i.get("data") or "")) > len(str(g.get("data") or "")):
                g["msg"] = i["msg"]
                g["data"] = i.get("data")
    out = list(grouped.values())
    out.sort(key=lambda g: (SEV_ORDER[g["sev"]], -len(g["seen"])))
    return out


def write_report(out_dir: Path, url: str, records, findings, args):
    lines = []
    highs = [f for f in findings if f["sev"] == "high"]
    lines.append("# UI QA sweep - %s" % url)
    lines.append("")
    lines.append("Run: %s | %d configurations | %d finding(s), %d high severity"
                 % (time.strftime("%Y-%m-%d %H:%M"), len(records), len(findings), len(highs)))
    lines.append("")
    lines.append("## Findings")
    lines.append("")
    if not findings:
        lines.append("No layout or interaction problems found across the matrix. Check the manual list at the end.")
    for f in findings:
        lines.append("### [%s] %s" % (SEV_LABEL[f["sev"]], f["msg"]))
        lines.append("")
        lines.append("- seen in: %s" % ", ".join(f["seen"][:8]) + (" (+%d more)" % (len(f["seen"]) - 8)
                                                                  if len(f["seen"]) > 8 else ""))
        d = f.get("data") or {}
        if f["check"] == "overflow":
            lines.append("- page width: %spx" % d.get("scrollWidth"))
            for o in d.get("offenders", [])[:4]:
                lines.append("  - `%s` right edge at %spx (width %spx)" % (o["sel"], o["right"], o["w"]))
        elif f["check"] == "tap-targets":
            for e in d.get("examples", [])[:5]:
                lines.append("  - `%s` %sx%s %s" % (e["sel"], e["w"], e["h"], ("- " + e["text"]) if e["text"] else ""))
        elif f["check"] in ("under-header", "anchor-under-header"):
            for e in d.get("examples", [])[:5]:
                lines.append("  - `%s` top %spx, bar %spx" % (e.get("sel") or e.get("href", ""),
                                                              e.get("top", 0), e.get("barHeight", 0)))
        elif f["check"] == "contrast":
            for e in d.get("examples", [])[:5]:
                lines.append("  - `%s` %.2f:1 (needs %.1f) %s \"%s\"" % (e["sel"], e["ratio"], e["need"],
                                                                         e["color"], e["text"]))
        elif f["check"] == "text-clipped":
            for e in d.get("examples", [])[:4]:
                lines.append("  - `%s` text %spx wider than its %spx box - \"%s\"" % (e["sel"], e["scrollWidth"] - e["clientWidth"], e["clientWidth"], e["text"]))
        elif f["check"] in ("overflow", "broken-image", "failed-requests", "js-error", "fixed-overlay"):
            for e in d.get("examples", [])[:4]:
                if isinstance(e, dict):
                    lines.append("  - `%s`" % (e.get("sel") or e.get("url") or e.get("text", "")))
        elif f["check"] == "scroll-jump":
            lines.append("- scroll positions: %s" % ", ".join("y=%s@%ss" % (s["y"], s["t"]) for s in d.get("samples", [])))
        elif f["check"] == "layout-shift":
            lines.append("- CLS: %.3f" % d.get("cls", 0))
        lines.append("")
    lines.append("## Per-configuration metrics")
    lines.append("")
    lines.append("| viewport | scheme | net | load ms | FCP ms | CLS | screenshot |")
    lines.append("|---|---|---|---|---|---|---|")
    for r in records:
        m = r.get("metrics", {})
        lines.append("| %s | %s | %s | %s | %s | %s | %s |" % (
            r["viewport"], r["scheme"], r["net"], r.get("load_ms", "-"),
            m.get("fcp", "-"), round(m.get("cls", 0), 3),
            ("[%s](%s)" % (r["shot"].split("/")[-1], r["shot"])) if r.get("shot") else "-"))
    lines.append("")
    lines.append("## Still needs a human")
    lines.append("")
    lines.append("- Real iOS Safari (this sweeps headless Chrome): Safari-specific layout, `100vh`, and input focus behaviour.")
    lines.append('- Thumb reach and whether it "feels right" in the hand on a real phone.')
    lines.append("- Auth-gated or personalized pages and anything behind a hard paywall.")
    lines.append("- Visual taste: whether a layout that passes the checks is actually the design you wanted.")
    lines.append("")
    (out_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")
    (out_dir / "report.json").write_text(json.dumps({"url": url, "records": records,
                                                     "findings": findings}, indent=2), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description="Multi-device UI sweep")
    ap.add_argument("target", help="URL or local HTML file")
    ap.add_argument("--out", default="/tmp/ui-qa", help="output directory")
    ap.add_argument("--viewports", default="", help="comma list, e.g. 'iPhone14-390,pixel7-412' or 'all'")
    ap.add_argument("--schemes", default="light,dark", help="light,dark")
    ap.add_argument("--quick", action="store_true", help="phones x light only, plus one slow-3G run")
    ap.add_argument("--no-shots", action="store_true", help="skip screenshots")
    args = ap.parse_args()

    url = normalise_url(args.target)
    out_dir = Path(args.out).expanduser()
    shots_dir = out_dir / "shots"
    shots_dir.mkdir(parents=True, exist_ok=True)

    if args.viewports and args.viewports != "all":
        names = [v.strip() for v in args.viewports.split(",") if v.strip()]
        unknown = [n for n in names if n not in VIEWPORTS]
        if unknown:
            sys.exit("unknown viewport(s): %s. Known: %s" % (", ".join(unknown), ", ".join(VIEWPORTS)))
    elif args.quick:
        names = ["iPhone14-390", "small-320", "pixel7-412"]
    else:
        names = list(VIEWPORTS)
    schemes = [s.strip() for s in args.schemes.split(",") if s.strip()]

    chrome = find_chrome()
    records = []
    with sync_playwright() as p:
        launch = {"headless": True, "args": ["--no-sandbox", "--allow-file-access-from-files"]}
        if chrome:
            launch["executable_path"] = chrome
        browser = p.chromium.launch(**launch)
        try:
            for name in names:
                mobile = VIEWPORTS[name][2]
                for scheme in schemes:
                    print("  %s / %s / fast" % (name, scheme), flush=True)
                    records.append(run_once(browser, url, name, scheme, "fast", shots_dir, mobile))
            # network-resilience runs on the two most common phones
            for name in [n for n in ("iPhone14-390", "pixel7-412") if n in names][:2]:
                print("  %s / light / slow3g" % name, flush=True)
                records.append(run_once(browser, url, name, "light", "slow3g", shots_dir, True))
            if "iPhone14-390" in names:
                print("  iPhone14-390 / light / offline", flush=True)
                records.append(run_once(browser, url, "iPhone14-390", "light", "offline", shots_dir, True))
        finally:
            browser.close()

    findings = dedupe(records)
    write_report(out_dir, url, records, findings, args)

    if records and all(any(i["check"] in ("load-failed", "timeout") for i in r["issues"])
                       for r in records):
        print("PAGE DID NOT LOAD: %s" % url)
        return 2

    print("")
    print("UI sweep: %d configurations, %d finding(s) - %d high" %
          (len(records), len(findings), len([f for f in findings if f["sev"] == "high"])))
    for f in findings[:10]:
        print("  [%s] %s (%s)" % (SEV_LABEL[f["sev"]], f["msg"], ", ".join(f["seen"][:3])))
    print("report: %s" % (out_dir / "report.md"))
    return 1 if any(f["sev"] == "high" for f in findings) else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception:  # pragma: no cover
        traceback.print_exc()
        sys.exit(2)
