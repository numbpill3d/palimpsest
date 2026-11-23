"""Palimpsest TUI."""
from __future__ import annotations

import asyncio
import json
import math
import os
import random
import re
import shlex
import shutil
import signal
import subprocess
import time
from pathlib import Path
from typing import Iterable
from urllib.parse import unquote, urlparse

from rich.text import Text
from textual import events, on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.theme import Theme
from textual.widget import Widget
from textual.widgets import (ContentSwitcher, DataTable, DirectoryTree, Footer,
                             Input, Label, OptionList, RichLog, Static, Tab,
                             Tabs, TextArea)
from textual.widgets.option_list import Option

from . import art, ciphers
from .ops import (HOME, OPS, TOOL_BY_BIN, TOOLS, Cmd, Op, OpError, case_dir,
                  cyberchef_page, have, ops_in, run_dir, slug, tilde)

CONTROL = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)?|.)"
                     r"|[\x00-\x08\x0b-\x1f\x7f-\x9f]")
KEY_VALUE = re.compile(r"^(.{1,60}?)(\s*:\s)(.*)$")
TRANSCRIPT_LIMIT = 8 * 1024 * 1024
CONFIG = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "palimpsest" / "config.json"
MONO = Theme(
    name="palimpsest", primary="#e8e8e8", secondary="#9a9a9a", accent="#ffffff",
    foreground="#cfcfcf", background="#0a0a0a", surface="#101010", panel="#181818",
    success="#e8e8e8", warning="#bdbdbd", error="#ffffff", dark=True,
    variables={"input-selection-background": "#6a6a6a", "footer-key-foreground": "#e4e4e4",
               "block-cursor-background": "#e8e8e8", "block-cursor-foreground": "#0a0a0a"},
)
VIEWS = ["inspect", "extract", "craft", "decode", "tools", "notes"]
HELP = """\
F1-F6  or  1-6     switch section
F7                 open a target, or browse for a path field
F8                 switch or create a case
F9                 open the latest output folder
F10                turn animation on or off
Ctrl+R             run the selected operation
Enter              run, or move into the operation's fields
Esc                stop a running tool
Ctrl+Y             copy decoded output
Ctrl+Q             quit

Drop or paste a file anywhere to make it the target."""


def clean(text: str) -> str:
    """Remove terminal control sequences from untrusted file content."""
    return CONTROL.sub("", text)


def parse_path(raw: str) -> Path | None:
    lines = [line for line in raw.strip().splitlines() if line.strip()]
    if not lines:
        return None
    raw = lines[0].strip()
    if raw.startswith("file://"):
        raw = unquote(urlparse(raw).path)
    else:
        try:
            parts = shlex.split(raw)
            raw = parts[0] if len(parts) == 1 else raw
        except ValueError:
            pass
    return Path(raw).expanduser()


def load_config() -> dict:
    try:
        data = json.loads(CONFIG.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_config(data: dict) -> None:
    try:
        CONFIG.parent.mkdir(parents=True, exist_ok=True)
        CONFIG.write_text(json.dumps(data))
    except OSError:
        pass


def alive(widget: Widget) -> bool:
    """Animate only when the window is focused and the widget can be seen."""
    app = widget.app
    if not app.motion or not app.app_focus or widget.screen is not app.screen:
        return False
    return all(node.display for node in widget.ancestors_with_self if isinstance(node, Widget))


class BootScreen(Screen):
    DURATION = 2.5

    def compose(self) -> ComposeResult:
        yield Static("", id="boot")

    def on_mount(self) -> None:
        self.started = time.monotonic()
        self.done = False
        self.timer = self.set_interval(1 / 15, self.tick)

    def tick(self) -> None:
        p = (time.monotonic() - self.started) / self.DURATION
        if p >= 1.16:
            self.finish()
            return
        size = self.size
        if size.width and size.height:
            self.query_one("#boot", Static).update(art.boot_frame(size.width, size.height, min(1.0, p)))

    def finish(self) -> None:
        if self.done:
            return
        self.done = True
        self.timer.stop()
        if self.app.screen is self:
            self.app.switch_screen(MainScreen())

    def on_key(self, event: events.Key) -> None:
        event.stop()
        self.finish()

    def on_click(self) -> None:
        self.finish()


class Ticker(Static):
    """The older text surfacing under the header."""

    def on_mount(self) -> None:
        self.set_interval(0.3, self.step)

    def step(self) -> None:
        if alive(self) and self.size.width:
            self.update(art.ghost_line(self.size.width, time.monotonic()), layout=False)


class Sigil(Static):
    """An eye that blinks, glances around, and scans while a tool runs."""

    def on_mount(self) -> None:
        now = time.monotonic()
        self.next_blink = now + random.uniform(3, 7)
        self.next_glance = now + random.uniform(2, 5)
        self.gaze = 0
        self.want = 0
        self.frame = ""
        self.show_frame(art.eye())
        self.set_interval(0.11, self.step)

    def show_frame(self, frame: str) -> None:
        if frame != self.frame:
            self.frame = frame
            self.update(frame, layout=False)

    def step(self) -> None:
        if not alive(self):
            return
        now = time.monotonic()
        running = self.app.proc_active
        if running:
            self.want = round(3 * math.sin(now * 3.2))
        elif now >= self.next_glance:
            self.want = random.choice((-3, -2, -1, 0, 0, 0, 1, 2, 3))
            self.next_glance = now + random.uniform(2.5, 7)
        if self.gaze != self.want:
            self.gaze += 1 if self.want > self.gaze else -1
        delta = now - self.next_blink
        lid = 1 if running else 0
        if 0 <= delta < 0.11 or 0.22 <= delta < 0.33:
            lid = 1
        elif 0.11 <= delta < 0.22:
            lid = 2
        elif delta >= 0.33:
            self.next_blink = now + random.uniform(4, 11)
        self.show_frame(art.eye(lid, self.gaze))


class Emblem(Static):
    """Idle line art for an empty workbench."""

    def __init__(self, group: str, **kwargs) -> None:
        super().__init__("", **kwargs)
        self.group = group

    def on_mount(self) -> None:
        self.render_art(0.0)
        self.set_interval(1 / 6, self.step)

    def render_art(self, t: float) -> None:
        body = art.shimmer(art.EMBLEMS[self.group], t)
        body.append("\n\n" + art.CAPTIONS[self.group].center(26).replace(" ", "\u00a0"), "#7a7a7a")
        body.append("\n" + "ctrl+r to run".center(26).replace(" ", "\u00a0"), "#4a4a4a")
        self.update(body, layout=False)

    def step(self) -> None:
        if alive(self):
            self.render_art(time.monotonic())


class ScrambleLabel(Static):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__("", *args, markup=False, **kwargs)
        self.target_text = ""
        self.anim = None
        self.step_n = 0

    def show(self, text: str) -> None:
        self.target_text = text
        self.step_n = 0
        if self.anim:
            self.anim.stop()
            self.anim = None
        if not self.app.motion:
            self.update(text)
            return
        self.anim = self.set_interval(1 / 24, self._tick)

    def _tick(self) -> None:
        self.step_n += 1
        progress = self.step_n / 7
        if progress >= 1:
            self.update(self.target_text, layout=False)
            self.anim.stop()
            self.anim = None
        else:
            self.update(art.scramble(self.target_text, progress, random), layout=False)


class Bench(Horizontal):
    def __init__(self, group: str, **kwargs) -> None:
        super().__init__(classes="view", **kwargs)
        self.group = group
        self.ops = ops_in(group)
        self.current: Op | None = None
        self.memory: dict[str, dict[str, str]] = {}

    def compose(self) -> ComposeResult:
        yield OptionList(*[Option(self._label(op), id=op.id) for op in self.ops], classes="oplist")
        with Vertical(classes="work"):
            yield ScrambleLabel(classes="op-title")
            yield Static("", classes="op-about", markup=False)
            yield VerticalScroll(classes="fields")
            yield Static("", classes="scan", markup=False)
            with Horizontal(classes="actions"):
                yield Static("", classes="hint", markup=False)
            yield Emblem(self.group, classes="idle")
            yield RichLog(wrap=True, markup=False, highlight=False, max_lines=5000)

    def _label(self, op: Op) -> Text:
        ok = op.tool is None or have(op.tool)
        return Text(("  " if ok else "○ ") + op.title, style="" if ok else "#5c5c5c")

    def on_mount(self) -> None:
        self.query_one(OptionList).highlighted = 0

    def refresh_labels(self) -> None:
        option_list = self.query_one(OptionList)
        for op in self.ops:
            option_list.replace_option_prompt(op.id, self._label(op))
        if self.current:
            self._hint(self.current)

    def _hint(self, op: Op) -> None:
        hint = self.query_one(".hint", Static)
        if op.tool and not have(op.tool):
            tool = TOOL_BY_BIN.get(op.tool)
            hint.update(f"not installed · yay -S {tool.pkg if tool else op.tool}")
        elif any(f.path for f in op.fields):
            hint.update("ctrl+r run  ·  f7 browse in a path field  ·  ? help")
        else:
            hint.update("ctrl+r run  ·  esc stop  ·  f9 output  ·  ? help")

    def remember(self) -> None:
        if self.current:
            self.memory[self.current.id] = self.values()

    @on(OptionList.OptionHighlighted)
    async def highlighted(self, event: OptionList.OptionHighlighted) -> None:
        event.stop()
        op = next(o for o in self.ops if o.id == event.option.id)
        if op is self.current:
            return
        self.remember()
        self.current = op
        self.query_one(ScrambleLabel).show(op.title.upper())
        self.query_one(".op-about", Static).update(op.about)
        self.query_one(".scan", Static).update("")
        self._hint(op)
        saved = self.memory.get(op.id, {})
        rows = [
            Horizontal(Label(f.label, classes="field-label", markup=False),
                       Input(value=saved.get(f.key, f.default), password=f.secret, name=f.key,
                             placeholder=f.placeholder),
                       classes="field-row")
            for f in op.fields
        ]
        box = self.query_one(".fields", VerticalScroll)
        box.display = bool(rows)
        with self.app.batch_update():
            await box.remove_children()
            if rows:
                await box.mount_all(rows)

    @on(OptionList.OptionSelected)
    def selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        inputs = self.query(".fields Input")
        if inputs:
            inputs.first().focus()
        else:
            self.app.action_run()

    @on(Input.Submitted)
    def submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self.app.action_run()

    def values(self) -> dict[str, str]:
        return {i.name: i.value for i in self.query(".fields Input")}

    def status(self, text: str) -> None:
        self.query_one(".scan", Static).update(text, layout=False)

    def show_log(self) -> None:
        self.query_one(Emblem).display = False
        self.log_view.add_class("shown")

    def flash(self) -> None:
        log = self.log_view
        log.add_class("flash")
        self.set_timer(0.35, lambda: log.remove_class("flash"))

    @property
    def log_view(self) -> RichLog:
        return self.query_one(RichLog)


class DecodeView(Horizontal):
    NAMES = ["scan everything", *ciphers.TRANSFORMS]

    def compose(self) -> ComposeResult:
        yield OptionList(*[Option(n, id=f"t{i}") for i, n in enumerate(self.NAMES)],
                         classes="oplist", id="decode-list")
        with Vertical(classes="work"):
            yield Static("input", classes="pane-label", markup=False)
            yield TextArea(id="decode-in", soft_wrap=True)
            with Vertical(id="decode-key-box"):
                yield Static("key", classes="pane-label", markup=False)
                yield Input(id="decode-key", placeholder="cipher key")
            yield Static("output  ·  ctrl+y copies", classes="pane-label", markup=False)
            yield TextArea(id="decode-out", read_only=True, soft_wrap=True)

    def on_mount(self) -> None:
        self.mode = self.NAMES[0]
        self.reveal_timer = None
        self.query_one(OptionList).highlighted = 0

    def result(self) -> str:
        text = self.query_one("#decode-in", TextArea).text
        key = self.query_one("#decode-key", Input).value
        return ciphers.apply(self.mode, text, key)

    def recompute(self, reveal: bool = False) -> None:
        if self.reveal_timer:
            self.reveal_timer.stop()
            self.reveal_timer = None
        output = self.query_one("#decode-out", TextArea)
        final = self.result()
        if not reveal or not self.app.motion or not final or len(final) > 400:
            output.text = final
            return
        step = [0]

        def tick() -> None:
            step[0] += 1
            if step[0] >= 7:
                self.reveal_timer.stop()
                self.reveal_timer = None
                output.text = final
            else:
                output.text = "\n".join(
                    art.scramble(line, step[0] / 7, random).rstrip()
                    for line in final.split("\n"))
        self.reveal_timer = self.set_interval(1 / 30, tick)

    @on(OptionList.OptionHighlighted, "#decode-list")
    def picked(self, event: OptionList.OptionHighlighted) -> None:
        event.stop()
        self.mode = self.NAMES[event.option_index]
        self.query_one("#decode-key-box").set_class(ciphers.needs_key(self.mode), "shown")
        self.recompute(reveal=True)

    @on(TextArea.Changed, "#decode-in")
    @on(Input.Changed, "#decode-key")
    def changed(self) -> None:
        self.recompute()

    def copy_output(self) -> None:
        final = self.result()
        if final:
            self.app.copy_to_clipboard(final)
            self.app.notify("output copied", timeout=2)


class ToolsView(Vertical):
    def compose(self) -> ComposeResult:
        yield DataTable(cursor_type="row", zebra_stripes=False)
        yield Static("", id="tools-note", markup=False)

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_columns("", "tool", "source", "does")
        self.reload()

    def reload(self) -> None:
        table = self.query_one(DataTable)
        row = table.cursor_row
        table.clear()
        self.rows = []
        missing = []
        for t in TOOLS:
            ok = have(t.bin)
            if not ok:
                missing.append(t.pkg)
            table.add_row("●" if ok else "○", t.bin + (" ↗" if t.gui else ""), t.source, t.use)
            self.rows.append(t.bin)
        page = cyberchef_page()
        table.add_row("●" if page else "○", "cyberchef ↗", "aur", "chained decoding in your browser")
        self.rows.append("cyberchef")
        if not page:
            missing.append("cyberchef-web")
        table.move_cursor(row=min(row, len(self.rows) - 1))
        note = "enter opens ↗ tools with the current target"
        if missing:
            note += f"\n{len(missing)} missing · yay -S {' '.join(missing)}"
        self.query_one("#tools-note", Static).update(note)

    @on(DataTable.RowSelected)
    def launch(self, event: DataTable.RowSelected) -> None:
        name = self.rows[event.cursor_row]
        app: Palimpsest = self.app  # type: ignore[assignment]
        if name == "cyberchef":
            page = cyberchef_page()
            if page:
                app.spawn(["xdg-open", page])
                app.notify("opening cyberchef", timeout=2)
            else:
                app.notify("cyberchef is not installed", severity="warning")
            return
        tool = TOOL_BY_BIN[name]
        if not tool.gui:
            app.notify(f"{name} runs from the workbench sections", timeout=2)
            return
        if not have(tool.bin):
            app.notify(f"{name} is not installed · yay -S {tool.pkg}", severity="warning")
            return
        argv = [tool.bin]
        if app.target and name != "qsstv":
            argv.append(str(app.target))
        app.spawn(argv)
        app.notify(f"opening {name}", timeout=2)


class NotesView(Vertical):
    def compose(self) -> ComposeResult:
        yield Static("", id="notes-path", classes="pane-label", markup=False)
        yield TextArea(id="notes", soft_wrap=True)

    def on_mount(self) -> None:
        self.save_timer = None
        self.load()

    @property
    def path(self) -> Path:
        return case_dir(self.app.case) / "notes.md"

    def load(self) -> None:
        self.loading = True
        text = self.path.read_text(errors="replace") if self.path.exists() else ""
        self.query_one("#notes", TextArea).text = text
        self.query_one("#notes-path", Static).update(f"autosaves to {tilde(str(self.path))}")
        self.call_after_refresh(self._loaded)

    def _loaded(self) -> None:
        self.loading = False

    @on(TextArea.Changed, "#notes")
    def changed(self) -> None:
        if self.loading:
            return
        if self.save_timer:
            self.save_timer.stop()
        self.save_timer = self.set_timer(0.6, self.save)

    def save(self) -> None:
        if self.save_timer:
            self.save_timer.stop()
            self.save_timer = None
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(self.query_one("#notes", TextArea).text)
            tmp.replace(self.path)


class QuietTree(DirectoryTree):
    def filter_paths(self, paths: Iterable[Path]) -> Iterable[Path]:
        return [p for p in paths if not p.name.startswith(".")]


class TargetScreen(ModalScreen[Path | None]):
    BINDINGS = [Binding("escape", "dismiss(None)", "close")]

    def __init__(self, title: str = "OPEN TARGET") -> None:
        super().__init__()
        self.heading = title

    def compose(self) -> ComposeResult:
        target = self.app.target
        start = target.parent if target and target.parent.is_dir() else Path.home()
        with Vertical(classes="modal"):
            yield Static(self.heading, classes="modal-title", markup=False)
            yield Input(placeholder="type, paste, or drop a path", id="target-path")
            yield QuietTree(str(start))
            yield Static("enter picks  ·  tab moves to the tree  ·  esc closes",
                         classes="modal-hint", markup=False)

    @on(Input.Submitted)
    def typed(self, event: Input.Submitted) -> None:
        path = parse_path(event.value)
        if path and path.is_file():
            self.dismiss(path)
        elif path and path.is_dir():
            self.query_one(QuietTree).path = path
        else:
            self.notify("no file at that path", severity="warning")

    @on(DirectoryTree.FileSelected)
    def picked(self, event: DirectoryTree.FileSelected) -> None:
        self.dismiss(Path(event.path))


class CaseScreen(ModalScreen[str | None]):
    BINDINGS = [Binding("escape", "dismiss(None)", "close")]

    def compose(self) -> ComposeResult:
        cases = sorted(p.name for p in (HOME / "cases").glob("*") if p.is_dir())
        with Vertical(classes="modal"):
            yield Static("CASE FILE", classes="modal-title", markup=False)
            yield Input(value=self.app.case, placeholder="new or existing case", id="case-name")
            yield Static("existing: " + (", ".join(cases) or "none"), classes="modal-hint", markup=False)

    @on(Input.Submitted)
    def named(self, event: Input.Submitted) -> None:
        if event.value.strip():
            self.dismiss(slug(event.value))


class HelpScreen(ModalScreen[None]):
    BINDINGS = [Binding("escape", "dismiss(None)", "close"),
                Binding("question_mark", "dismiss(None)", "close")]

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal"):
            yield Static("KEYS", classes="modal-title", markup=False)
            yield Static(HELP, id="help-body", markup=False)

    def on_click(self) -> None:
        self.dismiss(None)


class MainScreen(Screen):
    def compose(self) -> ComposeResult:
        with Horizontal(id="top"):
            yield Static(art.LOGO, id="logo", markup=False)
            with Vertical(id="meta-box"):
                yield Static("", id="meta", markup=False)
                yield Ticker("", id="ticker")
            yield Sigil("", id="sigil", markup=False)
        yield Tabs(*[Tab(f"{i + 1} {v.upper()}", id=v) for i, v in enumerate(VIEWS)])
        with ContentSwitcher(initial="view-inspect"):
            yield Bench("inspect", id="view-inspect")
            yield Bench("extract", id="view-extract")
            yield Bench("craft", id="view-craft")
            yield DecodeView(id="view-decode", classes="view")
            yield ToolsView(id="view-tools", classes="view")
            yield NotesView(id="view-notes", classes="view")
        yield Footer()

    def on_mount(self) -> None:
        self.app.update_meta()
        self.apply_layout(self.size.width, self.size.height)
        self.query_one("#view-inspect OptionList").focus()

    def on_resize(self, event: events.Resize) -> None:
        self.apply_layout(event.size.width, event.size.height)

    def apply_layout(self, width: int, height: int) -> None:
        if not width or not height:
            return
        compact = height < 34 or width < 100
        self.set_class(compact, "compact")
        self.set_class(width < 100, "narrow")
        self.set_class(height < 30, "short")
        self.query_one("#logo", Static).update(art.LOGO_SMALL if compact else art.LOGO)

    def on_paste(self, event: events.Paste) -> None:
        if isinstance(self.focused, (Input, TextArea)):
            return
        path = parse_path(event.text)
        if path and path.is_file():
            event.stop()
            self.app.set_target(path)

    @on(Tabs.TabActivated)
    def tab(self, event: Tabs.TabActivated) -> None:
        name = event.tab.id
        switcher = self.query_one(ContentSwitcher)
        changed = switcher.current != f"view-{name}"
        switcher.current = f"view-{name}"
        view = self.query_one(f"#view-{name}")
        if isinstance(view, ToolsView):
            view.reload()
            view.query_one(DataTable).focus()
        elif isinstance(view, Bench):
            view.refresh_labels()
            view.query_one(OptionList).focus()
            if changed and view.current:
                view.query_one(ScrambleLabel).show(view.current.title.upper())
        elif isinstance(view, DecodeView):
            view.query_one("#decode-in").focus()
        elif isinstance(view, NotesView):
            view.query_one("#notes").focus()


class Palimpsest(App):
    CSS_PATH = "app.tcss"
    TITLE = "PALIMPSEST"
    ENABLE_COMMAND_PALETTE = False
    BINDINGS = [
        Binding("ctrl+q", "quit", "quit", priority=True),
        Binding("f7", "target", "target", priority=True),
        Binding("f8", "case", "case", priority=True),
        Binding("f9", "open_output", "output", priority=True),
        Binding("f10", "motion", "motion", priority=True),
        Binding("ctrl+r", "run", "run", priority=True),
        Binding("ctrl+y", "copy_output", "copy", show=False, priority=True),
        Binding("escape", "stop", "stop"),
        Binding("question_mark", "help", "help"),
        *[Binding(f"f{i + 1}", f"view('{v}')", v, show=False, priority=True)
          for i, v in enumerate(VIEWS)],
        *[Binding(str(i + 1), f"view('{v}')", v, show=False) for i, v in enumerate(VIEWS)],
    ]

    def __init__(self, target: Path | None = None) -> None:
        super().__init__()
        self.target = target
        self.case = "field-notes"
        self.proc: asyncio.subprocess.Process | None = None
        self.proc_active = False
        self.last_output: Path | None = None
        self.cancelled = False
        self.config = load_config()
        self.motion = (self.config.get("motion", True)
                       and os.environ.get("TEXTUAL_ANIMATIONS", "") != "none")

    def on_mount(self) -> None:
        self.register_theme(MONO)
        self.theme = "palimpsest"
        case_dir(self.case)
        self.push_screen(BootScreen() if self.motion else MainScreen())

    @property
    def main(self) -> MainScreen | None:
        return self.screen if isinstance(self.screen, MainScreen) else None

    def update_meta(self) -> None:
        main = self.main
        if not main:
            return
        target = "no target · f7, or drop a file here"
        if self.target:
            try:
                size = self.target.stat().st_size
                target = f"{self.target.name}  ({size:,} bytes)"
            except OSError:
                target = f"{self.target.name}  (missing)"
        main.query_one("#meta", Static).update(f"case    {self.case}\ntarget  {target}")

    def set_target(self, path: Path) -> None:
        self.target = path
        self.update_meta()
        self.notify(f"target · {path.name}", timeout=2)

    def action_view(self, name: str) -> None:
        if self.main:
            self.main.query_one(Tabs).active = name

    def action_help(self) -> None:
        if self.main:
            self.push_screen(HelpScreen())

    def action_motion(self) -> None:
        self.motion = not self.motion
        self.config["motion"] = self.motion
        save_config(self.config)
        self.notify("animation on" if self.motion else "animation off", timeout=2)

    def action_copy_output(self) -> None:
        if self.main:
            view = self.main.query_one(ContentSwitcher).visible_content
            if isinstance(view, DecodeView):
                view.copy_output()

    def action_target(self) -> None:
        if not self.main:
            return
        focused = self.focused
        if isinstance(focused, Input) and focused.name:
            bench = self.active_bench()
            field = None
            if bench and bench.current:
                field = next((f for f in bench.current.fields if f.key == focused.name), None)
            if field and field.path:
                def fill(path: Path | None) -> None:
                    if path:
                        focused.value = str(path)
                self.push_screen(TargetScreen(field.label.upper()), fill)
                return
        self.push_screen(TargetScreen(), lambda path: self.set_target(path) if path else None)

    def action_case(self) -> None:
        if self.main:
            self.push_screen(CaseScreen(), self._set_case)

    def _set_case(self, name: str | None) -> None:
        if name and self.main:
            notes = self.main.query_one(NotesView)
            notes.save()
            self.case = name
            case_dir(name)
            notes.load()
            self.update_meta()
            self.notify(f"case · {name}", timeout=2)

    def action_open_output(self) -> None:
        if self.last_output and self.last_output.exists():
            self.spawn(["xdg-open", str(self.last_output)])
        else:
            self.notify("nothing has run yet", timeout=2)

    def spawn(self, argv: list[str]) -> None:
        try:
            subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
        except OSError as exc:
            self.notify(f"could not open: {exc}", severity="error")

    def active_bench(self) -> Bench | None:
        if not self.main:
            return None
        view = self.main.query_one(ContentSwitcher).visible_content
        return view if isinstance(view, Bench) else None

    def action_run(self) -> None:
        bench = self.active_bench()
        if not bench or not bench.current:
            return
        if self.proc_active:
            self.notify("a tool is already running, esc stops it", timeout=2)
            return
        self.proc_active = True
        self.run_worker(self.execute(bench, bench.current, bench.values()), exit_on_error=False)

    def action_stop(self) -> None:
        if self.proc and self.proc.returncode is None:
            self.cancelled = True
            try:
                os.killpg(self.proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                return
            self.set_timer(2, self._force_kill)

    def _force_kill(self) -> None:
        if self.proc and self.proc.returncode is None:
            try:
                os.killpg(self.proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    def format_line(self, op: Op, line: str) -> Text:
        line = tilde(line[:2000])
        if op.id == "exif":
            match = KEY_VALUE.match(line)
            if match:
                out = Text(match.group(1) + match.group(2), style="#747474")
                out.append(match.group(3), style="#f0f0f0")
                return out
        return Text(line)

    async def execute(self, bench: Bench, op: Op, values: dict[str, str]) -> None:
        log = bench.log_view
        pulse = None
        out: Path | None = None
        started = time.monotonic()
        try:
            if op.tool and not have(op.tool):
                pkg = TOOL_BY_BIN[op.tool].pkg if op.tool in TOOL_BY_BIN else op.tool
                raise OpError(f"{op.tool} is not installed · yay -S {pkg}")
            if op.needs_target:
                if not self.target:
                    raise OpError("no target · press f7 or drop a file")
                if not self.target.is_file():
                    raise OpError(f"target is gone: {self.target}")
            for f in op.fields:
                if f.required and not values.get(f.key, "").strip():
                    raise OpError(f"{f.label} is required")
            out = run_dir(self.case, op.id)
            cmd = op.build(self.target, values, out)
            self.last_output = out
            bench.show_log()
            log.clear()
            log.write(Text(f"$ {cmd.display()}", style="bold #f0f0f0"))
            pulse = self._pulse(bench)
            if cmd.builtin:
                message = await asyncio.to_thread(cmd.builtin)
                log.write(Text(message))
                code = 0
                (out / "output.log").write_text(message + "\n")
            else:
                code = await self._stream(cmd, op, log, out / "output.log")
                if cmd.verify and not self.cancelled:
                    if await asyncio.to_thread(cmd.verify):
                        if code != 0:
                            log.write(Text(f"// tool exited {code}, payload verified intact",
                                           style="#9a9a9a"))
                        code = 0
                    else:
                        log.write(Text("// payload could not be read back", style="bold #ffffff"))
                        code = code or 1
            if pulse:
                pulse.stop()
                pulse = None
            elapsed = time.monotonic() - started
            if self.cancelled:
                log.write(Text("── stopped", style="#9a9a9a"))
                bench.status("■ stopped")
            elif code == 0:
                where = tilde(cmd.result or str(out))
                log.write(Text(f"── done · {where}", style="#9a9a9a"))
                bench.status(f"✓ complete · {elapsed:0.1f}s")
                bench.flash()
                if cmd.result:
                    self.notify(f"{Path(cmd.result).name} · f9 opens it", title="written", timeout=3)
            else:
                log.write(Text(f"── exit {code}", style="bold #ffffff"))
                bench.status(f"✕ exit {code} · {elapsed:0.1f}s")
                bench.flash()
        except OpError as exc:
            bench.show_log()
            log.write(Text(f"// {exc}", style="bold #ffffff"))
            bench.status("✕ blocked")
            if out:
                shutil.rmtree(out, ignore_errors=True)
        except OSError as exc:
            bench.show_log()
            log.write(Text(f"// {exc}", style="bold #ffffff"))
            bench.status("✕ error")
        finally:
            if pulse:
                pulse.stop()
            self.proc = None
            self.proc_active = False
            self.cancelled = False

    def _pulse(self, bench: Bench):
        start = time.monotonic()

        def step() -> None:
            elapsed = time.monotonic() - start
            bar = art.wave(elapsed) if self.motion else "running"
            bench.status(f"{bar}  {elapsed:0.1f}s  ·  esc stops")
        step()
        return self.set_interval(1 / 10, step)

    async def _stream(self, cmd: Cmd, op: Op, log: RichLog, transcript: Path) -> int:
        self.proc = await asyncio.create_subprocess_exec(
            *cmd.argv,
            stdin=asyncio.subprocess.PIPE if cmd.stdin is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            cwd=transcript.parent, start_new_session=True,
            env={**os.environ, "TERM": "dumb", "NO_COLOR": "1"},
        )
        if cmd.stdin is not None:
            self.proc.stdin.write(cmd.stdin)
            await self.proc.stdin.drain()
            self.proc.stdin.close()
        written = 0
        pending = ""
        with transcript.open("w", errors="replace") as fh:
            while chunk := await self.proc.stdout.read(32768):
                pending += chunk.decode("utf-8", "replace")
                *lines, pending = pending.split("\n")
                for line in lines:
                    line = clean(line)
                    if written < TRANSCRIPT_LIMIT:
                        fh.write(line + "\n")
                        written += len(line) + 1
                    log.write(self.format_line(op, line))
            if pending:
                line = clean(pending)
                fh.write(line + "\n")
                log.write(self.format_line(op, line))
        return await self.proc.wait()

    async def action_quit(self) -> None:
        if self.proc and self.proc.returncode is None:
            self.action_stop()
        main = next((s for s in self.screen_stack if isinstance(s, MainScreen)), None)
        if main:
            main.query_one(NotesView).save()
        self.exit()


def main() -> None:
    import sys
    target = parse_path(sys.argv[1]) if len(sys.argv) > 1 else None
    if target and not target.is_file():
        target = None
    Palimpsest(target).run()


if __name__ == "__main__":
    main()
