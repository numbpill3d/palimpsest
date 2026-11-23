"""ASCII art and procedural frames. Everything here is grayscale."""
from __future__ import annotations

import math
import random

from rich.text import Text

LOGO = """\
┌─┐┌─┐┬  ┬┌┬┐┌─┐┌─┐┌─┐┌─┐┌┬┐
├─┘├─┤│  ││││├─┘└─┐├┤ └─┐ │
┴  ┴ ┴┴─┘┴┴ ┴┴  └─┘└─┘└─┘ ┴ """
LOGO_SMALL = "P A L I M P S E S T"
TAGLINE = "what was written beneath"

NOISE = "░▒▓·:;/\\|+*#%"
WAVE = "▁▂▃▄▅▆▇█"
PAGE = (
    "the page was scraped and the page was written again and the first hand "
    "stayed under the second like a bruise under skin. read it slantwise. "
    "hold it to the lamp. every erasure keeps the shape of what it erased. "
    "someone was here before you and left the door of the sentence open. "
)

EMBLEMS = {
    "inspect": r"""
        .-''''-.
      .'  .--.  '.
     /   ( () )   \
     \    '--'    /
      '.        .'\
        '-....-'\  \
                 \  \
                  \__\
""",
    "extract": r"""
   +--------------------+
   |  +--------------+  |
   |  |  +--------+  |  |
   |  |  |  +--+  |  |  |
   |  |  |  |##|  |  |  |
   |  |  |  +--+  |  |  |
   |  |  +--------+  |  |
   |  +--------------+  |
   +--------------------+
""",
    "craft": r"""
                 ,/
               ,'/
             ,' /
           ,'  /
         ,'   /
       ,' _.-'
      /_-'
     //
    ~ ~~~ ~~ ~~~~ ~~ ~~~
""",
}

CAPTIONS = {
    "inspect": "look closer",
    "extract": "take out what was put in",
    "craft": "leave something behind",
}

_SHADES = ("#4a4a4a", "#6a6a6a", "#8c8c8c", "#bcbcbc", "#f2f2f2")


def eye(lid: int = 0, gaze: int = 0) -> str:
    """A 17x5 eye. lid is 0 open, 1 narrowed, 2 shut. gaze runs -3 to 3."""
    top = "   .-~~~~~~~-.   "
    bottom = "   '-.......-'   "
    if lid >= 2:
        return "\n".join(("", "", "(~~~~~~~~~~~~~~~)", "", ""))
    centre = 8 + max(-3, min(3, gaze))

    def place(row: str, part: str) -> str:
        cells = list(row)
        cells[centre - 1:centre + 2] = part
        return "".join(cells)

    if lid == 1:
        middle = place("(---------------)", "(●)")
        return "\n".join(("", top, middle, bottom, ""))
    return "\n".join((
        top,
        place(" .'           '. ", ".-."),
        place("(               )", "(●)"),
        place(" '.           .' ", "'-'"),
        bottom,
    ))


def _cell(x: int, y: int) -> float:
    return (((x * 73856093) ^ (y * 19349663)) & 0xFFFF) / 0xFFFF


def _emit(out: Text, cells: list[tuple[str, str]]) -> None:
    run: list[str] = []
    style = None
    for ch, st in cells:
        if st != style and run:
            out.append("".join(run), style or "")
            run = []
        style = st
        run.append(ch)
    if run:
        out.append("".join(run), style or "")


def boot_frame(width: int, height: int, p: float) -> Text:
    """Manuscript appears, is scraped away, and the title surfaces beneath."""
    block = (eye(0, 0) + "\n\n" + LOGO).split("\n")
    block = [line.ljust(29) for line in block]
    eye_pad = (29 - 17) // 2
    block[:5] = [" " * eye_pad + line[:17].ljust(17) + " " * eye_pad for line in block[:5]]
    shown = int(len(TAGLINE) * max(0.0, min(1.0, (p - 0.78) / 0.16)))
    block += ["", TAGLINE.center(29)[:2 + shown + 1]]
    block = [line[:29].ljust(29) for line in block]
    left = max(0, (width - 29) // 2)
    top = max(0, (height - len(block)) // 2)

    fade = min(1.0, p / 0.16)
    scrape = (p - 0.16) / 0.36 * (height + 3)
    art_p = max(0.0, (p - 0.42) / 0.40)
    out = Text(no_wrap=True, end="")
    n = len(PAGE)
    for y in range(height):
        cells: list[tuple[str, str]] = []
        for x in range(width):
            noise = _cell(x, y)
            ch, st = " ", "#0a0a0a"
            distance = y - scrape
            if distance > 0 and noise < fade:
                ch, st = PAGE[(x + y * 37) % n], "#292929"
            elif -2.2 < distance <= 0 and noise < 0.45:
                ch, st = NOISE[int(noise * 100) % len(NOISE)], "#7a7a7a"
            elif distance <= -2.2 and noise < 0.018:
                ch, st = PAGE[(x + y * 37) % n], "#1c1c1c"
            if top <= y < top + len(block) and left <= x < left + 29:
                real = block[y - top][x - left]
                if real != " ":
                    if noise < art_p:
                        ch, st = real, "#f2f2f2"
                    elif noise < art_p + 0.22 and art_p > 0:
                        ch, st = NOISE[int(noise * 251) % len(NOISE)], "#6a6a6a"
                elif art_p > 0:
                    ch, st = " ", "#0a0a0a"
            cells.append((ch, st))
        _emit(out, cells)
        if y < height - 1:
            out.append("\n")
    return out


def shimmer(art: str, t: float) -> Text:
    """A soft diagonal band of light moving across line art."""
    out = Text(no_wrap=True, end="")
    lines = art.strip("\n").split("\n")
    period = 46
    head = (t * 9) % period
    width = max(len(line) for line in lines)
    for y, line in enumerate(lines):
        cells = []
        for x, ch in enumerate(line.ljust(width)):
            d = (head - (x + y * 2)) % period
            shade = 4 if d < 1.5 else 3 if d < 3.5 else 2 if d < 7 else 1
            # Non-breaking spaces keep the drawing's indentation when rendered.
            cells.append(("\u00a0" if ch == " " else ch, _SHADES[shade]))
        _emit(out, cells)
        if y < len(lines) - 1:
            out.append("\n")
    return out


def ghost_line(width: int, t: float) -> Text:
    """A line of the older text, surfacing and fading in slow waves."""
    out = Text(no_wrap=True, end="")
    n = len(PAGE)
    cells = []
    for x in range(width):
        level = math.sin(x * 0.11 - t * 0.7) + 0.6 * math.sin(x * 0.037 + t * 0.31)
        ch = PAGE[(x + 11) % n]
        if level > 1.05:
            cells.append((ch, "#6a6a6a"))
        elif level > 0.6:
            cells.append((ch, "#3c3c3c"))
        elif level > 0.25:
            cells.append(("·" if ch != " " else " ", "#262626"))
        else:
            cells.append((" ", "#0a0a0a"))
    _emit(out, cells)
    return out


def wave(t: float, width: int = 18) -> str:
    return "".join(
        WAVE[int((math.sin(i * 0.62 - t * 6.5) + 1) / 2 * (len(WAVE) - 1))]
        for i in range(width)
    )


def scramble(text: str, progress: float, rng: random.Random) -> str:
    """Reveal text left to right with a noisy leading edge."""
    edge = int(len(text) * progress)
    tail = "".join(
        " " if c == " " else rng.choice(NOISE) for c in text[edge:edge + 5]
    )
    return (text[:edge] + tail)[: len(text)].ljust(len(text))
