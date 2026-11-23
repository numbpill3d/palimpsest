"""ASCII art and small frame helpers."""
from __future__ import annotations

import random

LOGO = """\
┌─┐┌─┐┬  ┬┌┬┐┌─┐┌─┐┌─┐┌─┐┌┬┐
├─┘├─┤│  ││││├─┘└─┐├┤ └─┐ │
┴  ┴ ┴┴─┘┴┴ ┴┴  └─┘└─┘└─┘ ┴ """

EYE_OPEN = """\
    .-~~~~~-.
  .'  .---.  '.
 (   ( (●) )   )
  '.  '---'  .'
    '-.....-'"""

EYE_HALF = """\

    .-~~~~~-.
 (---( (●) )---)
    '-.....-'
"""

EYE_SHUT = """\


 (~~~~~~~~~~~~~)


"""

BOOT = EYE_OPEN + "\n\n" + LOGO + "\n\n    what was written beneath"

NOISE = "░▒▓·:;/\\|+*#%"
DRIFT = " .  ·   ˙  .    ·  :   .   ˙ "


def reveal(art: str, progress: float, rng: random.Random) -> str:
    """Resolve art out of noise. progress runs from 0 to 1."""
    out = []
    for ch in art:
        if ch in " \n" or rng.random() < progress:
            out.append(ch)
        elif rng.random() < 0.35:
            out.append(rng.choice(NOISE))
        else:
            out.append(" ")
    return "".join(out)


def scramble(text: str, progress: float, rng: random.Random) -> str:
    """Reveal text left to right with a noisy leading edge."""
    edge = int(len(text) * progress)
    tail = "".join(
        " " if c == " " else rng.choice(NOISE) for c in text[edge:edge + 5]
    )
    return (text[:edge] + tail)[: len(text)].ljust(len(text))
