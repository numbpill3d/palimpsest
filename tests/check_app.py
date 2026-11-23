"""End-to-end checks. Run with: .venv/bin/python tests/check_app.py

Uses a throwaway home folder, and skips operations whose tool is missing.
"""
import asyncio
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

WORK = Path(tempfile.mkdtemp(prefix="palimpsest-check-"))
os.environ["HOME"] = str(WORK)
os.environ["XDG_CONFIG_HOME"] = str(WORK / ".config")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import palimpsest.app as A  # noqa: E402
import palimpsest.ops as O  # noqa: E402
from palimpsest import ciphers as C  # noqa: E402
from textual import events  # noqa: E402

O.HOME = A.HOME = WORK / "wb"
SECRET = "HIDDEN-TRUTH"


def sh(*argv):
    subprocess.run(argv, check=True, capture_output=True)


def fixtures():
    d = WORK / "evidence"
    d.mkdir()
    jpg, png, wav = d / "cover it's here.jpg", d / "c.png", d / "a.wav"
    sh("magick", "-size", "640x480", "plasma:", "-quality", "92", str(jpg))
    sh("magick", "-size", "128x128", "plasma:", str(png))
    if O.have("sox"):
        sh("sox", "-n", str(wav), "synth", "1", "sine", "440")
    qr = d / "qr.png"
    if O.have("qrencode"):
        sh("qrencode", "-o", str(qr), SECRET)
    pdf = d / "x.pdf"
    sh("magick", "-size", "100x100", "xc:white", str(pdf))
    payload = d / "payload.txt"
    payload.write_text(SECRET + "\n")
    words = d / "words.txt"
    words.write_text("nope\nhunter2\nzzz\n")
    archive = d / "p.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("secret.txt", SECRET)
    return dict(jpg=jpg, png=png, wav=wav, qr=qr, pdf=pdf, payload=payload, words=words, zip=archive)


async def settle(pilot, app):
    for _ in range(600):
        await pilot.pause(0.03)
        if not app.proc_active:
            break
    await pilot.pause(0.05)


async def run_op(pilot, app, group, op_id, target, values=None):
    tool = next(o for o in O.OPS if o.id == op_id).tool
    if tool and not O.have(tool):
        print(f"  skip {op_id} ({tool} missing)")
        return None
    app.target = target
    await pilot.press(f"f{A.VIEWS.index(group) + 1}")
    await pilot.pause(0.08)
    bench = app.screen.query_one(f"#view-{group}")
    bench.query_one(A.OptionList).highlighted = [o.id for o in bench.ops].index(op_id)
    await pilot.pause(0.08)
    for field in bench.query(".fields Input"):
        if values and field.name in values:
            field.value = values[field.name]
    await pilot.press("ctrl+r")
    await settle(pilot, app)
    status = str(bench.query_one(".scan").render())
    assert "complete" in status, f"{op_id}: {status}"
    print(f"  ok   {op_id}")
    return app.last_output


def payload_in(run, name="payload.bin"):
    return SECRET.encode() in (run / name).read_bytes()


async def workflow(f):
    originals = {k: v.read_bytes() for k, v in f.items() if v.exists()}
    app = A.Palimpsest(f["jpg"])
    async with app.run_test(size=(132, 40)) as p:
        await p.pause(0.2)
        await p.press("x")
        await p.pause(0.4)
        assert isinstance(app.screen, A.MainScreen)
        for i, view in enumerate(A.VIEWS):
            await p.press(f"f{i + 1}")
            await p.pause(0.05)
            assert app.screen.query_one(A.ContentSwitcher).current == f"view-{view}"

        for op, key in (("file", "jpg"), ("exif", "jpg"), ("strings", "jpg"), ("signatures", "jpg"),
                        ("hex", "jpg"), ("pngcheck", "png"), ("zsteg", "png"), ("qr", "qr"),
                        ("spectro", "wav"), ("pdf", "pdf")):
            run = await run_op(p, app, "inspect", op, f[key])
            if run and op == "qr":
                assert SECRET in (run / "output.log").read_text()

        run = await run_op(p, app, "craft", "stege", f["jpg"], {"payload": str(f["payload"]), "pass": "hunter2"})
        if run:
            stego = next(run.glob("*-stego.jpg"))
            r = await run_op(p, app, "extract", "stegx", stego, {"pass": "hunter2"})
            assert payload_in(r), "steghide"
            r = await run_op(p, app, "extract", "seek", stego, {"wordlist": str(f["words"])})
            if r:
                assert payload_in(r), "stegseek"
        run = await run_op(p, app, "craft", "oge", f["jpg"], {"payload": str(f["payload"]), "pass": "k3y"})
        if run:
            r = await run_op(p, app, "extract", "ogx", next(run.glob("*-stego.jpg")), {"pass": "k3y"})
            assert payload_in(r), "outguess"
        run = await run_op(p, app, "craft", "mmtx", None, {"text": SECRET})
        if run:
            r = await run_op(p, app, "extract", "mmrx", run / "signal.wav")
            assert SECRET in (r / "output.log").read_text(), "minimodem"
        run = await run_op(p, app, "craft", "poly", f["jpg"], {"zip": str(f["zip"])})
        poly = next(run.glob("*-polyglot.jpg"))
        assert zipfile.ZipFile(poly).read("secret.txt").decode() == SECRET
        await run_op(p, app, "extract", "bwx", poly)
        await run_op(p, app, "extract", "carve", poly)
        await run_op(p, app, "craft", "scrub", f["jpg"])
        await run_op(p, app, "craft", "qrmake", None, {"text": SECRET})
        run = await run_op(p, app, "craft", "stamp", f["jpg"], {"artist": "K. Vance", "lat": "44.97", "lon": "-93.26"})
        if run:
            tags = subprocess.run(["exiftool", "-Artist", "-GPSLongitudeRef", str(run / f["jpg"].name)],
                                  capture_output=True, text=True).stdout
            assert "K. Vance" in tags and "West" in tags

        # Field values survive moving away and back, within the session.
        bench = app.screen.query_one("#view-craft")
        ids = [o.id for o in bench.ops]
        await p.press("f3")
        ol = bench.query_one(A.OptionList)
        ol.highlighted = ids.index("qrmake")
        await p.pause(0.1)
        bench.query_one(".fields Input").value = "remember me"
        ol.highlighted = ids.index("scrub")
        await p.pause(0.1)
        ol.highlighted = ids.index("qrmake")
        await p.pause(0.1)
        assert bench.query_one(".fields Input").value == "remember me"

        # Key field appears only for keyed ciphers.
        await p.press("f4")
        await p.pause(0.1)
        view = app.screen.query_one("#view-decode")
        box = view.query_one("#decode-key-box")
        assert not box.has_class("shown")
        names = view.NAMES
        view.query_one(A.OptionList).highlighted = names.index("vigenere decode")
        await p.pause(0.1)
        assert box.has_class("shown")
        view.query_one(A.OptionList).highlighted = 0
        await p.pause(0.5)
        view.query_one("#decode-in").text = "aGVsbG8="
        await p.pause(0.2)
        assert "hello" in view.query_one("#decode-out").text

        # A dropped file becomes the target.
        await p.press("f5")
        await p.pause(0.1)
        app.screen.post_message(events.Paste(f"file://{f['png']}\n"))
        await p.pause(0.1)
        assert app.target == f["png"]

        await p.press("question_mark")
        await p.pause(0.1)
        assert isinstance(app.screen, A.HelpScreen)
        await p.press("escape")
        await p.pause(0.1)

        # Animation toggle stops frames and persists.
        await p.press("f10")
        await p.pause(0.1)
        assert app.motion is False and A.load_config()["motion"] is False
        await p.press("f10")
    for k, data in originals.items():
        assert f[k].read_bytes() == data, f"{k} changed"
    print("  ok   originals unchanged")


async def layouts():
    for size in ((132, 40), (100, 30), (80, 24)):
        app = A.Palimpsest()
        async with app.run_test(size=size) as p:
            await p.pause(0.1)
            await p.press("x")
            await p.pause(0.4)
            await p.press("f3")
            await p.pause(0.2)
            bench = app.screen.query_one("#view-craft")
            inputs = list(bench.query(".fields Input"))
            assert len(inputs) == 6
            last = inputs[-1]
            last.focus()
            await p.pause(0.2)
            box = bench.query_one(".fields").region
            assert box.contains_region(last.region), f"last field clipped at {size}"
            assert bench.query_one(".idle").region.height >= 3, f"no idle room at {size}"
        print(f"  ok   layout {size[0]}x{size[1]}")


def main():
    assert C.apply("base64 decode", "aGVsbG8=") == "hello"
    assert C.scan("dGhlIHN0YWlyd2VsbCBpcyB0aGUga2V5").count("──") == 1
    try:
        asyncio.run(workflow(fixtures()))
        asyncio.run(layouts())
    finally:
        shutil.rmtree(WORK, ignore_errors=True)
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
