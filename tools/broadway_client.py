"""Headless Chromium client for a GTK Broadway display (development only).

Broadway only draws frames while a client is connected, which
tools/screenshots.py needs. If an output directory is given, the client also
saves a browser-side screenshot ``<name>-page.png`` for every
``<name>.request`` file that appears there.

Usage: python tools/broadway_client.py [url] [seconds] [out_dir]
Requires ``pip install playwright`` and a Chromium binary.
"""

import shutil
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8085/"
SECONDS = float(sys.argv[2]) if len(sys.argv) > 2 else 120
OUT = Path(sys.argv[3]) if len(sys.argv) > 3 else None

with sync_playwright() as p:
    browser = p.chromium.launch(executable_path=shutil.which("chromium"))
    page = browser.new_page(viewport={"width": 1240, "height": 800})
    page.goto(URL)
    deadline = time.time() + SECONDS
    while time.time() < deadline:
        if OUT is not None:
            for request in sorted(OUT.glob("*.request")):
                time.sleep(0.5)
                page.screenshot(path=str(OUT / f"{request.stem}-page.png"))
                request.unlink()
        # Keep the page busy so Chromium does not throttle Broadway's frames.
        page.mouse.move(1235, 795)
        page.wait_for_timeout(100)
    browser.close()
