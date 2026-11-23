"""Tool registry and operation builders.

Rules every operation follows:
- Commands are argument lists and never pass through a shell.
- The input file is never modified. Anything that writes works on a copy
  inside a fresh run folder.
"""
from __future__ import annotations

import glob
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

HOME = Path.home() / "Documents" / "Palimpsest"


class OpError(Exception):
    """A problem the user can fix, shown in the log."""


@dataclass(frozen=True)
class Tool:
    bin: str
    pkg: str
    source: str
    use: str
    gui: bool = False


TOOLS = [
    Tool("exiftool", "perl-image-exiftool", "repo", "read and write metadata"),
    Tool("binwalk", "binwalk", "repo", "find and extract embedded files"),
    Tool("foremost", "foremost", "repo", "carve files by signature"),
    Tool("mat2", "mat2", "repo", "scrub metadata"),
    Tool("sox", "sox", "repo", "render spectrograms"),
    Tool("age", "age", "repo", "encrypt payloads"),
    Tool("qpdf", "qpdf", "repo", "inspect PDF structure"),
    Tool("pngcheck", "pngcheck", "aur", "PNG chunks and trailing data"),
    Tool("steghide", "steghide", "aur", "hide data in JPG, BMP, WAV"),
    Tool("stegseek", "stegseek", "aur", "wordlist attack on steghide"),
    Tool("outguess", "outguess", "aur", "hide data in JPG"),
    Tool("zsteg", "zsteg", "aur", "PNG and BMP bit-plane scan"),
    Tool("minimodem", "minimodem", "aur", "data as audio tones"),
    Tool("qrencode", "qrencode", "repo", "make QR codes"),
    Tool("zbarimg", "zbar", "repo", "read QR codes and barcodes"),
    Tool("xxd", "vim", "repo", "hex dump"),
    Tool("strings", "binutils", "repo", "readable text in files"),
    Tool("okteta", "okteta", "repo", "hex editor", gui=True),
    Tool("sonic-visualiser", "sonic-visualiser", "repo", "audio analysis", gui=True),
    Tool("audacity", "audacity", "repo", "audio editor", gui=True),
    Tool("qsstv", "qsstv", "aur", "SSTV images in audio", gui=True),
]
TOOL_BY_BIN = {t.bin: t for t in TOOLS}


def cyberchef_page() -> str | None:
    for pattern in ("/usr/share/webapps/cyberchef/*.html",
                    "/usr/share/webapps/CyberChef/*.html",
                    "/usr/share/cyberchef/*.html"):
        hits = sorted(glob.glob(pattern))
        if hits:
            return hits[0]
    return None


def tilde(text: str) -> str:
    """Shorten the home folder for display."""
    return text.replace(str(Path.home()), "~")


def have(binary: str) -> bool:
    return shutil.which(binary) is not None


@dataclass
class Cmd:
    argv: list[str] | None = None
    stdin: bytes | None = None
    builtin: Callable[[], str] | None = None
    secret: tuple[str, ...] = ()
    result: str | None = None
    verify: Callable[[], bool] | None = None

    def display(self) -> str:
        if not self.argv:
            return "(built in)"
        return tilde(" ".join("••••" if a in self.secret and a else a for a in self.argv))


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    default: str = ""
    secret: bool = False
    required: bool = False
    path: bool = False
    placeholder: str = ""


@dataclass(frozen=True)
class Op:
    id: str
    group: str
    title: str
    about: str
    tool: str | None
    build: Callable[[Path | None, dict, Path], Cmd]
    fields: tuple[Field, ...] = field(default_factory=tuple)
    needs_target: bool = True


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "untitled"


def case_dir(case: str) -> Path:
    path = HOME / "cases" / slug(case)
    path.mkdir(parents=True, exist_ok=True)
    return path


def run_dir(case: str, op_id: str) -> Path:
    base = case_dir(case) / "runs"
    stamp = time.strftime("%Y%m%d-%H%M%S")
    for n in range(1000):
        path = base / f"{stamp}-{op_id}" if not n else base / f"{stamp}-{op_id}-{n}"
        try:
            path.mkdir(parents=True)
            return path
        except FileExistsError:
            continue
    raise OpError("could not create a run folder")


def _copy(target: Path, out: Path) -> Path:
    dest = out / target.name
    shutil.copy2(target, dest)
    dest.chmod(dest.stat().st_mode | 0o200)
    return dest


def _existing(value: str, label: str) -> Path:
    path = Path(value.strip().strip("'\"")).expanduser()
    if not path.is_file():
        raise OpError(f"{label} not found: {path}")
    return path


def _coord(value: str, pos: str, neg: str) -> tuple[str, str]:
    try:
        number = float(value)
    except ValueError as exc:
        raise OpError(f"bad coordinate: {value}") from exc
    return str(abs(number)), pos if number >= 0 else neg


def _stamp(t: Path, v: dict, out: Path) -> Cmd:
    copy = _copy(t, out)
    argv = ["exiftool", "-overwrite_original"]
    for key, tag in (("artist", "Artist"), ("date", "DateTimeOriginal"),
                     ("comment", "Comment"), ("desc", "ImageDescription")):
        if v.get(key):
            argv.append(f"-{tag}={v[key]}")
    if v.get("lat"):
        val, ref = _coord(v["lat"], "N", "S")
        argv += [f"-GPSLatitude={val}", f"-GPSLatitudeRef={ref}"]
    if v.get("lon"):
        val, ref = _coord(v["lon"], "E", "W")
        argv += [f"-GPSLongitude={val}", f"-GPSLongitudeRef={ref}"]
    if len(argv) == 2:
        raise OpError("fill in at least one field")
    return Cmd(argv + ["--", str(copy)], result=str(copy))


def _scrub(t: Path, _: dict, out: Path) -> Cmd:
    copy = _copy(t, out)
    return Cmd(["mat2", "--inplace", str(copy)], result=str(copy))


def _polyglot(t: Path, v: dict, out: Path) -> Cmd:
    payload = _existing(v["zip"], "zip")
    dest = out / f"{t.stem}-polyglot{t.suffix}"

    def work() -> str:
        with dest.open("wb") as fh:
            for src in (t, payload):
                with src.open("rb") as part:
                    shutil.copyfileobj(part, fh)
        return f"joined {t.name} + {payload.name}"
    return Cmd(builtin=work, result=str(dest))


def _steg_embed(t: Path, v: dict, out: Path) -> Cmd:
    payload = _existing(v["payload"], "payload")
    dest = out / f"{t.stem}-stego{t.suffix}"
    passphrase = v.get("pass", "")

    def verify() -> bool:
        # The Arch steghide build can abort on exit after writing a good file,
        # so success is decided by reading the payload back.
        check = out / ".verify"
        try:
            done = subprocess.run(
                ["steghide", "extract", "-sf", str(dest), "-xf", str(check),
                 "-p", passphrase, "-f"],
                stdin=subprocess.DEVNULL, capture_output=True, timeout=120)
            return (done.returncode == 0 and check.is_file()
                    and check.read_bytes() == payload.read_bytes())
        except (OSError, subprocess.SubprocessError):
            return False
        finally:
            check.unlink(missing_ok=True)

    return Cmd(["steghide", "embed", "-cf", str(t), "-ef", str(payload),
                "-sf", str(dest), "-p", passphrase],
               secret=(passphrase,), result=str(dest), verify=verify)


def _outguess_embed(t: Path, v: dict, out: Path) -> Cmd:
    payload = _existing(v["payload"], "payload")
    dest = out / f"{t.stem}-stego.jpg"
    key = ["-k", v["pass"]] if v.get("pass") else []
    return Cmd(["outguess", *key, "-d", str(payload), str(t), str(dest)],
               secret=(v.get("pass", ""),), result=str(dest))


def _outguess_read(t: Path, v: dict, out: Path) -> Cmd:
    dest = out / "payload.bin"
    key = ["-k", v["pass"]] if v.get("pass") else []
    return Cmd(["outguess", *key, "-r", str(t), str(dest)],
               secret=(v.get("pass", ""),), result=str(dest))


def _seek(t: Path, v: dict, out: Path) -> Cmd:
    words = _existing(v["wordlist"], "wordlist")
    dest = out / "payload.bin"
    return Cmd(["stegseek", str(t), str(words), str(dest)], result=str(dest))


def _simple(*parts: str) -> Callable[[Path | None, dict, Path], Cmd]:
    def build(t: Path | None, _: dict, __: Path) -> Cmd:
        return Cmd([str(t) if p == "{f}" else p for p in parts])
    return build


PASS = Field("pass", "passphrase", secret=True)

OPS: list[Op] = [
    Op("file", "inspect", "identify", "What the file really is, by content.",
       "file", _simple("file", "-k", "--", "{f}")),
    Op("exif", "inspect", "metadata", "Every metadata tag, unknown tags included.",
       "exiftool", _simple("exiftool", "-a", "-u", "-G1", "--", "{f}")),
    Op("strings", "inspect", "strings", "Readable text anywhere in the bytes.",
       "strings",
       lambda t, v, _: Cmd(["strings", "-n", v.get("min") or "6", "--", str(t)]),
       (Field("min", "minimum length", "6"),)),
    Op("signatures", "inspect", "signatures", "File signatures hidden at any offset.",
       "binwalk", _simple("binwalk", "{f}")),
    Op("hex", "inspect", "hex head", "First 512 bytes.",
       "xxd", _simple("xxd", "-l", "512", "{f}")),
    Op("pngcheck", "inspect", "png chunks", "Chunk list and trailing data.",
       "pngcheck", _simple("pngcheck", "-v", "{f}")),
    Op("zsteg", "inspect", "bit planes", "Scans PNG or BMP bit planes. Can be slow.",
       "zsteg", _simple("zsteg", "-a", "{f}")),
    Op("qr", "inspect", "read codes", "Reads QR codes and barcodes in an image.",
       "zbarimg", _simple("zbarimg", "--", "{f}")),
    Op("spectro", "inspect", "spectrogram", "Renders audio to a PNG in the run folder.",
       "sox", lambda t, _, o: Cmd(["sox", str(t), "-n", "spectrogram", "-m", "-o",
                                   str(o / "spectrogram.png")],
                                  result=str(o / "spectrogram.png"))),
    Op("pdf", "inspect", "pdf structure", "Checks a PDF and lists its objects.",
       "qpdf", _simple("qpdf", "--json=latest", "--json-key=qpdf", "{f}")),

    Op("bwx", "extract", "binwalk extract", "Pulls embedded files into the run folder.",
       "binwalk", lambda t, _, o: Cmd(["binwalk", "-e", "-C", str(o / "extracted"), str(t)],
                                      result=str(o / "extracted"))),
    Op("carve", "extract", "carve files", "Recovers files by header and footer.",
       "foremost", lambda t, _, o: Cmd(["foremost", "-i", str(t), "-o", str(o / "carved")],
                                       result=str(o / "carved"))),
    Op("stegx", "extract", "steghide read", "Extracts a payload with a known passphrase.",
       "steghide",
       lambda t, v, o: Cmd(["steghide", "extract", "-sf", str(t), "-xf",
                            str(o / "payload.bin"), "-p", v.get("pass", ""), "-f"],
                           secret=(v.get("pass", ""),), result=str(o / "payload.bin")),
       (PASS,)),
    Op("seek", "extract", "steghide crack", "Tries a wordlist against a steghide file.",
       "stegseek", _seek, (Field("wordlist", "wordlist path", required=True, path=True),)),
    Op("ogx", "extract", "outguess read", "Extracts an outguess payload from a JPG.",
       "outguess", _outguess_read, (PASS,)),
    Op("mmrx", "extract", "modem decode", "Decodes minimodem tones from a WAV.",
       "minimodem",
       lambda t, v, _: Cmd(["minimodem", "--rx", "-q", "-f", str(t), v.get("baud") or "1200"]),
       (Field("baud", "baud rate", "1200"),)),

    Op("stamp", "craft", "forge metadata", "Writes fiction into a copy of the file.",
       "exiftool", _stamp,
       (Field("artist", "artist"),
        Field("date", "date", placeholder="YYYY:MM:DD HH:MM:SS"),
        Field("comment", "comment"),
        Field("desc", "description"),
        Field("lat", "latitude", placeholder="decimal, like 44.9778"),
        Field("lon", "longitude", placeholder="decimal, like -93.265"))),
    Op("scrub", "craft", "scrub metadata", "Removes your traces from a copy.",
       "mat2", _scrub),
    Op("stege", "craft", "steghide embed", "Hides a payload in a JPG, BMP, or WAV.",
       "steghide", _steg_embed,
       (Field("payload", "payload file", required=True, path=True), PASS)),
    Op("oge", "craft", "outguess embed", "Hides a payload in a JPG.",
       "outguess", _outguess_embed,
       (Field("payload", "payload file", required=True, path=True), PASS)),
    Op("poly", "craft", "zip polyglot", "An image that also opens as a zip.",
       None, _polyglot, (Field("zip", "zip file", required=True, path=True),)),
    Op("qrmake", "craft", "make qr", "Renders text to a QR PNG.", "qrencode",
       lambda _, v, o: Cmd(["qrencode", "-m", "2", "-s", "8", "-o", str(o / "clue.png"),
                            "--", v["text"]], result=str(o / "clue.png")),
       (Field("text", "text or url", required=True),), False),
    Op("mmtx", "craft", "modem encode", "Turns text into data tones in a WAV.",
       "minimodem",
       lambda _, v, o: Cmd(["minimodem", "--tx", "-f", str(o / "signal.wav"),
                            v.get("baud") or "1200"],
                           stdin=(v["text"] + "\n").encode(),
                           result=str(o / "signal.wav")),
       (Field("text", "message", required=True), Field("baud", "baud rate", "1200")),
       False),
]


def ops_in(group: str) -> list[Op]:
    return [op for op in OPS if op.group == group]
