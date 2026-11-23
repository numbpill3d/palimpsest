"""Pure-Python decoders and encoders for the DECODE view."""
from __future__ import annotations

import base64
import binascii
import re
import urllib.parse
from typing import Callable

MORSE = {
    "A": ".-", "B": "-...", "C": "-.-.", "D": "-..", "E": ".", "F": "..-.",
    "G": "--.", "H": "....", "I": "..", "J": ".---", "K": "-.-", "L": ".-..",
    "M": "--", "N": "-.", "O": "---", "P": ".--.", "Q": "--.-", "R": ".-.",
    "S": "...", "T": "-", "U": "..-", "V": "...-", "W": ".--", "X": "-..-",
    "Y": "-.--", "Z": "--..", "0": "-----", "1": ".----", "2": "..---",
    "3": "...--", "4": "....-", "5": ".....", "6": "-....", "7": "--...",
    "8": "---..", "9": "----.", ".": ".-.-.-", ",": "--..--", "?": "..--..",
    "/": "-..-.", "-": "-....-", "(": "-.--.", ")": "-.--.-", "'": ".----.",
    "!": "-.-.--", ":": "---...", "=": "-...-", "+": ".-.-.", "@": ".--.-.",
}
UNMORSE = {v: k for k, v in MORSE.items()}


def _text(data: bytes) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return "(binary) " + data.hex(" ")


def _compact(s: str) -> str:
    return re.sub(r"\s+", "", s)


def _b64(s: str, _: str) -> str:
    c = _compact(s).replace("-", "+").replace("_", "/")
    return _text(base64.b64decode(c + "=" * (-len(c) % 4), validate=True))


def _b32(s: str, _: str) -> str:
    c = _compact(s).upper()
    return _text(base64.b32decode(c + "=" * (-len(c) % 8)))


def _hex(s: str, _: str) -> str:
    return _text(bytes.fromhex(re.sub(r"0x|[\s:,]", "", s)))


def _bin(s: str, _: str) -> str:
    bits = re.sub(r"[^01]", "", s)
    if not bits or len(bits) % 8:
        raise ValueError("bit count is not a multiple of 8")
    return _text(bytes(int(bits[i:i + 8], 2) for i in range(0, len(bits), 8)))


def _dec(s: str, _: str) -> str:
    return _text(bytes(int(n) for n in re.findall(r"\d+", s)))


def _shift(s: str, n: int) -> str:
    out = []
    for ch in s:
        if "a" <= ch <= "z":
            out.append(chr((ord(ch) - 97 + n) % 26 + 97))
        elif "A" <= ch <= "Z":
            out.append(chr((ord(ch) - 65 + n) % 26 + 65))
        else:
            out.append(ch)
    return "".join(out)


def _rot(s: str, key: str) -> str:
    return _shift(s, int(key or 13))


def _rot_all(s: str, _: str) -> str:
    return "\n".join(f"{n:>2}  {_shift(s, n)}" for n in range(1, 26))


def _atbash(s: str, _: str) -> str:
    out = []
    for ch in s:
        if "a" <= ch <= "z":
            out.append(chr(219 - ord(ch)))
        elif "A" <= ch <= "Z":
            out.append(chr(155 - ord(ch)))
        else:
            out.append(ch)
    return "".join(out)


def _vig(s: str, key: str, sign: int) -> str:
    k = [ord(c) - 97 for c in key.lower() if c.isascii() and c.isalpha()]
    if not k:
        raise ValueError("needs a letter key")
    out, i = [], 0
    for ch in s:
        if ch.isascii() and ch.isalpha():
            out.append(_shift(ch, sign * k[i % len(k)]))
            i += 1
        else:
            out.append(ch)
    return "".join(out)


def _xor(s: str, key: str) -> str:
    if not key:
        raise ValueError("needs a key")
    k = key.encode()
    try:
        raw = bytes.fromhex(re.sub(r"\s", "", s))
    except ValueError:
        raw = s.encode()
    return _text(bytes(b ^ k[i % len(k)] for i, b in enumerate(raw)))


def _xor_enc(s: str, key: str) -> str:
    if not key:
        raise ValueError("needs a key")
    k = key.encode()
    return bytes(b ^ k[i % len(k)] for i, b in enumerate(s.encode())).hex()


def _morse_d(s: str, _: str) -> str:
    norm = s.replace("_", "-").replace("·", ".").replace("—", "-")
    words = re.split(r"\s*/\s*|\s{3,}|\n+", norm.strip())
    return " ".join(
        "".join(UNMORSE.get(sym, "?") for sym in w.split()) for w in words
    )


def _morse_e(s: str, _: str) -> str:
    return " / ".join(
        " ".join(MORSE[c] for c in w.upper() if c in MORSE) for w in s.split()
    )


def _a1z26_d(s: str, _: str) -> str:
    return "".join(
        chr(int(n) + 64) if n.isdigit() and 1 <= int(n) <= 26 else " "
        for n in re.findall(r"\d+|/", s)
    )


def _a1z26_e(s: str, _: str) -> str:
    return " ".join(
        str(ord(c) - 64) if "A" <= c <= "Z" else "/" for c in s.upper()
        if ("A" <= c <= "Z") or c == " "
    )


Transform = Callable[[str, str], str]

TRANSFORMS: dict[str, tuple[Transform, bool]] = {
    "base64 decode": (_b64, False),
    "base64 encode": (lambda s, _: base64.b64encode(s.encode()).decode(), False),
    "base32 decode": (_b32, False),
    "base32 encode": (lambda s, _: base64.b32encode(s.encode()).decode(), False),
    "base85 decode": (lambda s, _: _text(base64.b85decode(_compact(s))), False),
    "hex decode": (_hex, False),
    "hex encode": (lambda s, _: s.encode().hex(" "), False),
    "binary decode": (_bin, False),
    "binary encode": (lambda s, _: " ".join(f"{b:08b}" for b in s.encode()), False),
    "decimal decode": (_dec, False),
    "morse decode": (_morse_d, False),
    "morse encode": (_morse_e, False),
    "a1z26 decode": (_a1z26_d, False),
    "a1z26 encode": (_a1z26_e, False),
    "rot-n (key = n)": (_rot, True),
    "rot, all 25": (_rot_all, False),
    "rot47": (lambda s, _: "".join(
        chr(33 + (ord(c) - 33 + 47) % 94) if 33 <= ord(c) <= 126 else c for c in s
    ), False),
    "atbash": (_atbash, False),
    "vigenere decode": (lambda s, k: _vig(s, k, -1), True),
    "vigenere encode": (lambda s, k: _vig(s, k, 1), True),
    "xor decode": (_xor, True),
    "xor encode (hex)": (_xor_enc, True),
    "url decode": (lambda s, _: urllib.parse.unquote(s), False),
    "url encode": (lambda s, _: urllib.parse.quote(s), False),
    "reverse": (lambda s, _: s[::-1], False),
}

SCAN = ["base64 decode", "base32 decode", "base85 decode", "hex decode",
        "binary decode", "decimal decode", "morse decode", "a1z26 decode",
        "url decode"]
# These always produce output, so they only appear when nothing else matched.
FALLBACK = ["rot47", "atbash", "reverse"]


# Loose decoders only join a scan when the whole input has their shape.
SHAPES = {
    "morse decode": re.compile(r"[.\-_·—/\s]+"),
    "a1z26 decode": re.compile(r"(?:[1-9]|1\d|2[0-6])(?:[\s,/-]+(?:[1-9]|1\d|2[0-6]))*"),
    "decimal decode": re.compile(r"\d{1,3}(?:[\s,]+\d{1,3})+"),
    "url decode": re.compile(r".*%[0-9a-fA-F]{2}.*", re.S),
}


def _plausible(s: str) -> bool:
    if not s.strip() or s.startswith("(binary)") or "?" * 3 in s:
        return False
    good = sum(c.isprintable() or c in "\n\t" for c in s)
    return good / len(s) > 0.95


def apply(name: str, text: str, key: str = "") -> str:
    if not text:
        return ""
    if name == "scan everything":
        return scan(text)
    fn, _ = TRANSFORMS[name]
    try:
        return fn(text, key)
    except (ValueError, binascii.Error, OverflowError) as exc:
        return f"// cannot apply: {exc}"


def scan(text: str) -> str:
    blocks = []
    for name in SCAN:
        shape = SHAPES.get(name)
        if shape and not shape.fullmatch(text.strip()):
            continue
        try:
            result = TRANSFORMS[name][0](text, "")
        except (ValueError, binascii.Error, OverflowError):
            continue
        if result != text and _plausible(result):
            blocks.append(f"── {name}\n{result}")
    if not blocks:
        for name in FALLBACK:
            blocks.append(f"── {name}\n{TRANSFORMS[name][0](text, '')}")
    return "\n\n".join(blocks)


def needs_key(name: str) -> bool:
    return name in TRANSFORMS and TRANSFORMS[name][1]
