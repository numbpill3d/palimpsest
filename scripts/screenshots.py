"""Regenerates README screenshots from a fictional case.

Run with: .venv/bin/python scripts/screenshots.py [output-dir]
Uses a throwaway home folder so no real paths appear.
"""
import asyncio
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else ROOT / "assets" / "screenshots"
WORK = Path(tempfile.mkdtemp(prefix="palimpsest-shots-"))
os.environ["HOME"] = str(WORK)
os.environ["XDG_CONFIG_HOME"] = str(WORK / ".config")
sys.path.insert(0, str(ROOT))

import palimpsest.app as A  # noqa: E402
import palimpsest.ops as O  # noqa: E402

O.HOME = A.HOME = WORK / "Documents" / "Palimpsest"


def shot(app, name):
    svg = WORK / f"{name}.svg"
    app.save_screenshot(str(svg))
    # Chromium renders Textual's SVG with correct cell alignment.
    markup = svg.read_text().replace("Fira Code", "DejaVu Sans Mono")
    markup = re.sub(r"@font-face\s*{.*?}", "", markup, flags=re.S)
    width, height = map(float, re.search(r'viewBox="0 0 ([\d.]+) ([\d.]+)"', markup).groups())
    page = WORK / f"{name}.html"
    page.write_text("<!doctype html><style>html,body{margin:0;background:#0a0a0a}"
                    f"svg{{display:block;width:{width}px;height:{height}px}}</style>{markup}")
    subprocess.run(["chromium", "--headless=new", "--disable-gpu", "--hide-scrollbars",
                    "--force-device-scale-factor=1.5", f"--user-data-dir={WORK / 'chrome'}",
                    f"--window-size={int(width)},{int(height)}",
                    f"--screenshot={OUT / f'{name}.png'}", page.as_uri()],
                   check=True, capture_output=True, timeout=60)


async def wait(p, app):
    for _ in range(300):
        await p.pause(0.03)
        if not app.proc_active:
            break
    await p.pause(0.5)


async def pick(p, bench, op):
    bench.query_one(A.OptionList).highlighted = [o.id for o in bench.ops].index(op)
    await p.pause(0.6)


async def main():
    OUT.mkdir(parents=True, exist_ok=True)
    evidence = WORK / "evidence"
    evidence.mkdir()
    photo = evidence / "third-floor.jpg"
    subprocess.run(["magick", "-size", "640x480", "plasma:gray20-gray70", "-colorspace", "Gray",
                    "-quality", "90", str(photo)], check=True)
    subprocess.run(["mat2", "--inplace", str(photo)], check=True)
    subprocess.run(["exiftool", "-q", "-overwrite_original", "-Artist=K. Vance",
                    "-DateTimeOriginal=1999:03:14 03:33:00",
                    "-Comment=she never left the third floor", "-GPSLatitude=44.9778",
                    "-GPSLatitudeRef=N", "-GPSLongitude=93.2650", "-GPSLongitudeRef=W",
                    str(photo)], check=True)

    app = A.Palimpsest(photo)
    async with app.run_test(size=(124, 38)) as p:
        await p.pause(2.45)
        shot(app, "boot")
        await p.press("x")
        await p.pause(0.7)
        shot(app, "idle")
        bench = app.screen.query_one("#view-inspect")
        await pick(p, bench, "exif")
        await p.press("ctrl+r")
        await wait(p, app)
        await p.pause(3.2)
        shot(app, "inspect")
        await p.press("f3")
        await p.pause(0.3)
        bench = app.screen.query_one("#view-craft")
        await pick(p, bench, "stamp")
        values = {"artist": "K. Vance", "date": "1999:03:14 03:33:00",
                  "comment": "she never left the third floor",
                  "desc": "stairwell, east wing", "lat": "44.9778", "lon": "-93.265"}
        for field in bench.query(".fields Input"):
            field.value = values[field.name]
        await p.press("ctrl+r")
        await wait(p, app)
        await p.pause(3.2)
        shot(app, "craft")
        await p.press("f4")
        await p.pause(0.3)
        app.screen.query_one("#decode-in").text = "dGhlIHN0YWlyd2VsbCBpcyB0aGUga2V5"
        await p.pause(0.5)
        shot(app, "decode")
        await p.press("f5")
        await p.pause(0.5)
        shot(app, "tools")
        await p.press("question_mark")
        await p.pause(0.3)
        shot(app, "help")


try:
    asyncio.run(main())
finally:
    shutil.rmtree(WORK, ignore_errors=True)
