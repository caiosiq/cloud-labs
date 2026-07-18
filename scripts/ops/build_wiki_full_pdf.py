#!/usr/bin/env python3
"""Screenshot the live Wiki (Learn chapters + mock camera Backends) into one PDF.

Requires coordinator on http://127.0.0.1:8000.

    python scripts/ops/build_wiki_full_pdf.py

Output (repo only, not Desktop):

    dist/CloudLabs_Wiki_Full.pdf
    dist/wiki_pdf_shots/*.png
"""
from __future__ import annotations

import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DIST = ROOT / "dist"
SHOTS = DIST / "wiki_pdf_shots"
PDF_PATH = DIST / "CloudLabs_Wiki_Full.pdf"
BASE = "http://127.0.0.1:8000"
MANIFEST = ROOT / "frontend" / "wiki" / "guides" / "manifest.json"
CAMERA_TAG = "tag_22"
VIEWPORT = {"width": 1440, "height": 900}


def ensure_coordinator() -> None:
    try:
        with urllib.request.urlopen(f"{BASE}/wiki", timeout=5) as r:
            if r.status >= 400:
                raise RuntimeError(f"/wiki HTTP {r.status}")
    except (urllib.error.URLError, TimeoutError, RuntimeError) as exc:
        raise SystemExit(
            f"Coordinator not reachable at {BASE}/wiki ({exc}).\n"
            "Start it with: python backend/main.py"
        ) from exc


def ensure_playwright():
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
    except ImportError:
        print("Installing playwright…")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "playwright", "-q"])
    # Chromium binary
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            browser.close()
    except Exception:
        print("Installing Chromium for Playwright…")
        subprocess.check_call([sys.executable, "-m", "playwright", "install", "chromium"])
    from playwright.sync_api import sync_playwright

    return sync_playwright


def learn_chapters() -> list[dict]:
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    for sec in data.get("sections") or []:
        if sec.get("id") == "learn":
            return list(sec.get("chapters") or [])
    raise SystemExit("No learn section in manifest.json")


def wait_learn_ready(page) -> None:
    page.wait_for_function(
        """() => {
            const t = document.body?.innerText || '';
            return !t.includes('Loading Wiki') && !!document.querySelector('.guide-article, #detail .guide-article, main#detail');
        }""",
        timeout=30000,
    )
    # Prefer article content
    page.wait_for_timeout(400)


def capture_learn(page, chapters: list[dict]) -> list[tuple[str, Path]]:
    out: list[tuple[str, Path]] = []
    for i, ch in enumerate(chapters, start=1):
        cid = ch["id"]
        title = ch.get("title") or cid
        url = f"{BASE}/wiki#learn/{cid}"
        print(f"Learn {i}/{len(chapters)}: {cid}")
        page.goto(url, wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(300)
        # Force hash navigation if first load ignored hash
        page.evaluate(f"() => {{ location.hash = 'learn/{cid}'; }}")
        wait_learn_ready(page)
        # Click chapter in sidebar for certainty
        btn = page.locator(f'[data-chapter="{cid}"]')
        if btn.count():
            btn.first.click()
            wait_learn_ready(page)
        path = SHOTS / f"learn-{i:02d}-{cid}.png"
        page.screenshot(path=str(path), full_page=True)
        out.append((title, path))
    return out


def capture_backends_camera(page) -> tuple[str, Path]:
    print("Backends: mock.default + camera", CAMERA_TAG)
    page.goto(f"{BASE}/wiki#backends", wait_until="networkidle", timeout=60000)
    page.wait_for_timeout(500)
    page.evaluate("() => { location.hash = 'backends'; }")
    # Gallery card
    page.wait_for_selector('[data-backend-id="mock.default"]', timeout=30000)
    page.locator('[data-backend-id="mock.default"]').first.click()
    page.wait_for_timeout(600)
    # Prefer components tab
    tab = page.locator('[data-tab="components"]')
    if tab.count():
        tab.first.click()
        page.wait_for_timeout(400)
    # Component list inside backends hub / catalog host
    page.wait_for_selector(f'button[data-tag="{CAMERA_TAG}"]', timeout=30000)
    page.locator(f'button[data-tag="{CAMERA_TAG}"]').first.click()
    page.wait_for_timeout(800)
    # Wait for camera detail (measurables / ImageViewer / camera_image text)
    page.wait_for_function(
        """() => {
            const t = document.body?.innerText || '';
            return t.includes('camera_image') || t.includes('OPTICAL_CAMERA') || t.includes('Measurables');
        }""",
        timeout=30000,
    )
    path = SHOTS / "backends-mock-camera.png"
    hub = page.locator("#backends-hub")
    if hub.count() and hub.first.is_visible():
        hub.first.screenshot(path=str(path))
    else:
        page.screenshot(path=str(path), full_page=False)
    return (f"Backends - mock.default / {CAMERA_TAG}", path)


def assemble_pdf(pages: list[tuple[str, Path]]) -> Path:
    from fpdf import FPDF
    from PIL import Image

    pdf = FPDF(orientation="L", format="Letter", unit="mm")
    pdf.set_auto_page_break(auto=False)
    page_w, page_h = 279.4, 215.9  # Letter landscape mm
    margin = 6.0
    usable_w = page_w - 2 * margin
    usable_h = page_h - 2 * margin - 8  # footer

    for title, img_path in pages:
        pdf.add_page()
        with Image.open(img_path) as im:
            iw, ih = im.size
        aspect = iw / ih
        box_aspect = usable_w / usable_h
        if aspect > box_aspect:
            w = usable_w
            h = w / aspect
        else:
            h = usable_h
            w = h * aspect
        x = margin + (usable_w - w) / 2
        y = margin + (usable_h - h) / 2
        pdf.image(str(img_path), x=x, y=y, w=w, h=h)
        pdf.set_xy(margin, page_h - 10)
        pdf.set_font("Helvetica", "I", 8)
        pdf.set_text_color(100, 116, 139)
        safe_title = title.encode("latin-1", "replace").decode("latin-1")
        pdf.cell(usable_w, 5, f"{safe_title}  |  Cloud Labs Wiki", align="L")

    DIST.mkdir(parents=True, exist_ok=True)
    pdf.output(str(PDF_PATH))
    return PDF_PATH


def main() -> int:
    ensure_coordinator()
    sync_playwright = ensure_playwright()
    chapters = learn_chapters()

    if SHOTS.exists():
        for old in SHOTS.glob("*.png"):
            old.unlink()
    else:
        SHOTS.mkdir(parents=True)

    pages: list[tuple[str, Path]] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport=VIEWPORT,
            device_scale_factor=1.25,
        )
        page = context.new_page()
        pages.extend(capture_learn(page, chapters))
        pages.append(capture_backends_camera(page))
        browser.close()

    out = assemble_pdf(pages)
    print(f"PDF: {out}")
    print(f"pages: {len(pages)}")
    print(f"bytes: {out.stat().st_size}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
