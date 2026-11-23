"""Palimpsest TUI."""
from __future__ import annotations

import asyncio
import os
import random
import re
import shlex
import shutil
import signal
import subprocess
import time
from pathlib import Path
from urllib.parse import unquote, urlparse

from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen, Screen
from textual.theme import Theme
from textual.widgets import (ContentSwitcher, DataTable, DirectoryTree, Footer,
                             Input, Label, OptionList, RichLog, Static, Tab,
                             Tabs, TextArea)
from textual.widgets.option_list import Option

from . import art, ciphers
from .ops import (HOME, OPS, TOOL_BY_BIN, TOOLS, Cmd, Op, OpError, case_dir,
                  cyberchef_page, have, ops_in, run_dir, slug)

CONTROL = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)?|.)"
                     r"|[\x00-\x08\x0b-\x1f\x7f-\x9f]")
TRANSCRIPT_LIMIT = 8 * 1024 * 1024
MONO = Theme(
    name="palimpsest", primary="#e8e8e8", secondary="#9a9a9a", accent="#ffffff",
    foreground="#cfcfcf", background="#0a0a0a", surface="#101010", panel="#181818",
    success="#e8e8e8", warning="#bdbdbd", error="#ffffff", dark=True,
    variables={"input-selection-background": "#6a6a6a", "footer-key-foreground": "#e4e4e4",
               "block-cursor-background": "#e8e8e8", "block-cursor-foreground": "#0a0a0a"},
)
VIEWS = ["inspect", "extract", "craft", "decode", "tools", "notes"]


def clean(text: str) -> str:
    """Remove terminal control sequences from untrusted file content."""
    return CONTROL.sub("", text)


def parse_path(raw: str) -> Path | None:
    raw = raw.strip()
    if not raw:
        return None
    if raw.startswith("file://"):
        raw = unquote(urlparse(raw).path)
    else:
        try:
            parts = shlex.split(raw)
            raw = parts[0] if len(parts) == 1 else raw
        except ValueError:
            pass
    return Path(raw).expanduser()


class BootScreen(Screen):
    FRAMES = 22

    def compose(self) -> ComposeResult:
        yield Static("", id="boot", markup=False)

    def on_mount(self) -> None:
        self.frame = 0
        self.rng = random.Random()
        self.timer = self.set_interval(1 / 12, self.tick)

    def tick(self) -> None:
        self.frame += 1
        progress = min(1.0, self.frame / self.FRAMES)
        self.query_one("#boot", Static).update(art.reveal(art.BOOT, progress ** 1.5, self.rng))
        if self.frame >= self.FRAMES + 9:
            self.finish()

    def finish(self) -> None:
        self.timer.stop()
        if self.app.screen is self:
            self.app.switch_screen(MainScreen())

    def on_key(self) -> None:
        self.finish()

    def on_click(self) -> None:
        self.finish()


class Ticker(Static):
    """A slow drift of dust under the header."""

    def on_mount(self) -> None:
        self.offset_x = 0
        self.timer = self.set_interval(0.45, self.step)

    def step(self) -> None:
        if not self.app.app_focus:
            return
        self.offset_x = (self.offset_x + 1) % len(art.DRIFT)
        width = max(self.size.width, 1)
        line = (art.DRIFT * (width // len(art.DRIFT) + 2))[self.offset_x:self.offset_x + width]
        self.update(line)


class Sigil(Static):
    """An eye that blinks now and then, and narrows while a tool runs."""

    def on_mount(self) -> None:
        self.next_blink = time.monotonic() + random.uniform(4, 9)
        self.state = None
        self.set_interval(0.14, self.step)

    def step(self) -> None:
        if not self.app.app_focus:
            return
        now = time.monotonic()
        delta = now - self.next_blink
        if getattr(self.app, "proc_active", False):
            frame = art.EYE_HALF if int(now * 2) % 2 else art.EYE_OPEN
        elif 0 <= delta < 0.14 or 0.28 <= delta < 0.42:
            frame = art.EYE_HALF
        elif 0.14 <= delta < 0.28:
            frame = art.EYE_SHUT
        else:
            frame = art.EYE_OPEN
            if delta >= 0.42:
                self.next_blink = now + random.uniform(5, 13)
        if frame is not self.state:
            self.state = frame
            self.update("\n".join(frame.split("\n")[:4]))


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
        self.anim = self.set_interval(1 / 20, self._tick)

    def _tick(self) -> None:
        self.step_n += 1
        progress = self.step_n / 9
        if progress >= 1:
            self.update(self.target_text)
            self.anim.stop()
            self.anim = None
        else:
            self.update(art.scramble(self.target_text, progress, random))


class Bench(Horizontal):
    def __init__(self, group: str, **kwargs) -> None:
        super().__init__(classes="view", **kwargs)
        self.group = group
        self.ops = ops_in(group)
        self.current: Op | None = None

    def compose(self) -> ComposeResult:
        yield OptionList(*[Option(self._label(op), id=op.id) for op in self.ops], classes="oplist")
        with Vertical(classes="work"):
            yield ScrambleLabel(classes="op-title")
            yield Static("", classes="op-about", markup=False)
            yield Vertical(classes="fields")
            yield Static("", classes="scan", markup=False)
            with Horizontal(classes="actions"):
                yield Static("", classes="hint", markup=False)
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
            pkg = tool.pkg if tool else op.tool
            hint.update(f"not installed · yay -S {pkg}")
        else:
            hint.update("ctrl+r run  ·  esc stop  ·  f9 open output")

    @on(OptionList.OptionHighlighted)
    async def highlighted(self, event: OptionList.OptionHighlighted) -> None:
        event.stop()
        op = next(o for o in self.ops if o.id == event.option.id)
        if op is self.current:
            return
        self.current = op
        self.query_one(ScrambleLabel).show(op.title.upper())
        self.query_one(".op-about", Static).update(op.about)
        self._hint(op)
        box = self.query_one(".fields", Vertical)
        await box.remove_children()
        for f in op.fields:
            row = Horizontal(Label(f.label, classes="field-label", markup=False),
                             Input(value=f.default, password=f.secret, name=f.key),
                             classes="field-row")
            await box.mount(row)

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
        self.query_one(".scan", Static).update(text)

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
            yield Static("key", classes="pane-label", markup=False)
            yield Input(id="decode-key", placeholder="only for keyed ciphers")
            yield Static("output", classes="pane-label", markup=False)
            yield TextArea(id="decode-out", read_only=True, soft_wrap=True)

    def on_mount(self) -> None:
        self.mode = self.NAMES[0]
        self.query_one(OptionList).highlighted = 0

    def recompute(self) -> None:
        text = self.query_one("#decode-in", TextArea).text
        key = self.query_one("#decode-key", Input).value
        self.query_one("#decode-out", TextArea).text = ciphers.apply(self.mode, text, key)

    @on(OptionList.OptionHighlighted, "#decode-list")
    def picked(self, event: OptionList.OptionHighlighted) -> None:
        event.stop()
        self.mode = self.NAMES[event.option_index]
        self.recompute()

    @on(TextArea.Changed, "#decode-in")
    @on(Input.Changed, "#decode-key")
    def changed(self) -> None:
        self.recompute()


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
        note = "enter opens ↗ tools with the current target"
        if missing:
            note += f"\n{len(missing)} missing · run install-tools.sh from the app folder"
        self.query_one("#tools-note", Static).update(note)

    @on(DataTable.RowSelected)
    def launch(self, event: DataTable.RowSelected) -> None:
        name = self.rows[event.cursor_row]
        app: Palimpsest = self.app  # type: ignore[assignment]
        if name == "cyberchef":
            page = cyberchef_page()
            if page:
                app.spawn(["xdg-open", page])
            return
        tool = TOOL_BY_BIN[name]
        if not tool.gui or not have(tool.bin):
            return
        argv = [tool.bin]
        if app.target and name != "qsstv":
            argv.append(str(app.target))
        app.spawn(argv)


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
        self.query_one("#notes-path", Static).update(f"autosaves to {self.path}")
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


class TargetScreen(ModalScreen[Path | None]):
    BINDINGS = [Binding("escape", "dismiss(None)", "close")]

    def compose(self) -> ComposeResult:
        start = self.app.target.parent if self.app.target else Path.home()
        with Vertical(classes="modal"):
            yield Static("OPEN TARGET", classes="modal-title", markup=False)
            yield Input(placeholder="type, paste, or drop a path", id="target-path")
            yield DirectoryTree(str(start))
            yield Static("enter picks  ·  esc closes", classes="modal-hint", markup=False)

    @on(Input.Submitted)
    def typed(self, event: Input.Submitted) -> None:
        path = parse_path(event.value)
        if path and path.is_file():
            self.dismiss(path)
        elif path and path.is_dir():
            self.query_one(DirectoryTree).path = path
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
        self.dismiss(slug(event.value))


class MainScreen(Screen):
    def compose(self) -> ComposeResult:
        with Horizontal(id="top"):
            yield Static(art.LOGO, id="logo", markup=False)
            with Vertical(id="meta-box"):
                yield Static("", id="meta", markup=False)
                yield Ticker("", id="ticker", markup=False)
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
        self.query_one("#view-inspect OptionList").focus()

    @on(Tabs.TabActivated)
    def tab(self, event: Tabs.TabActivated) -> None:
        name = event.tab.id
        self.query_one(ContentSwitcher).current = f"view-{name}"
        view = self.query_one(f"#view-{name}")
        if isinstance(view, ToolsView):
            view.reload()
            view.query_one(DataTable).focus()
        elif isinstance(view, Bench):
            view.refresh_labels()
            view.query_one(OptionList).focus()
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
        Binding("ctrl+r", "run", "run", priority=True),
        Binding("escape", "stop", "stop"),
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

    def on_mount(self) -> None:
        self.register_theme(MONO)
        self.theme = "palimpsest"
        case_dir(self.case)
        self.push_screen(BootScreen())

    @property
    def main(self) -> MainScreen | None:
        return self.screen if isinstance(self.screen, MainScreen) else None

    def update_meta(self) -> None:
        main = self.main
        if not main:
            return
        target = "no target · press f7"
        if self.target:
            try:
                size = self.target.stat().st_size
                target = f"{self.target.name}  ({size:,} bytes)"
            except OSError:
                target = f"{self.target.name}  (missing)"
        main.query_one("#meta", Static).update(f"case    {self.case}\ntarget  {target}")

    def action_view(self, name: str) -> None:
        if self.main:
            self.main.query_one(Tabs).active = name

    def action_target(self) -> None:
        if self.main:
            self.push_screen(TargetScreen(), self._set_target)

    def _set_target(self, path: Path | None) -> None:
        if path:
            self.target = path
            self.update_meta()

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

    def action_open_output(self) -> None:
        if self.last_output:
            self.spawn(["xdg-open", str(self.last_output)])
        else:
            self.notify("nothing has run yet")

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
            self.notify("a tool is already running, esc stops it")
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

    async def execute(self, bench: Bench, op: Op, values: dict[str, str]) -> None:
        log = bench.log_view
        pulse = None
        out: Path | None = None
        try:
            if op.tool and not have(op.tool):
                pkg = TOOL_BY_BIN[op.tool].pkg if op.tool in TOOL_BY_BIN else op.tool
                raise OpError(f"{op.tool} is not installed · yay -S {pkg}")
            if op.needs_target:
                if not self.target:
                    raise OpError("no target · press f7")
                if not self.target.is_file():
                    raise OpError(f"target is gone: {self.target}")
            for f in op.fields:
                if f.required and not values.get(f.key, "").strip():
                    raise OpError(f"{f.label} is required")
            out = run_dir(self.case, op.id)
            self.last_output = out
            cmd = op.build(self.target, values, out)
            log.clear()
            log.write(Text(f"$ {cmd.display()}", style="bold #f0f0f0"))
            pulse = self._pulse(bench)
            if cmd.builtin:
                message = await asyncio.to_thread(cmd.builtin)
                log.write(Text(message))
                code = 0
                (out / "output.log").write_text(message + "\n")
            else:
                code = await self._stream(cmd, log, out / "output.log")
                if cmd.verify and not self.cancelled:
                    if await asyncio.to_thread(cmd.verify):
                        if code != 0:
                            log.write(Text(f"// tool exited {code}, payload verified intact",
                                           style="#9a9a9a"))
                        code = 0
                    else:
                        log.write(Text("// payload could not be read back", style="bold #ffffff"))
                        code = code or 1
            if self.cancelled:
                log.write(Text("── stopped", style="#9a9a9a"))
                bench.status("stopped")
            elif code == 0:
                where = cmd.result or str(out)
                log.write(Text(f"── done · {where}", style="#9a9a9a"))
                bench.status("✓ complete")
            else:
                log.write(Text(f"── exit {code}", style="bold #ffffff"))
                bench.status(f"✕ exit {code}")
        except OpError as exc:
            log.write(Text(f"// {exc}", style="bold #ffffff"))
            bench.status("✕ blocked")
            if out:
                shutil.rmtree(out, ignore_errors=True)
        except OSError as exc:
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
            n = int((time.monotonic() - start) * 8)
            width = 14
            pos = abs((n % (2 * width)) - width)
            bar = "".join("█" if i == pos else "░" if abs(i - pos) < 2 else "·" for i in range(width + 1))
            bench.status(f"{bar}  {time.monotonic() - start:0.1f}s")
        step()
        return self.set_interval(1 / 8, step)

    async def _stream(self, cmd: Cmd, log: RichLog, transcript: Path) -> int:
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
                    log.write(Text(line[:2000]))
            if pending:
                line = clean(pending)
                fh.write(line + "\n")
                log.write(Text(line[:2000]))
        return await self.proc.wait()

    async def action_quit(self) -> None:
        if self.proc and self.proc.returncode is None:
            self.action_stop()
        if self.main:
            self.main.query_one(NotesView).save()
        self.exit()


def main() -> None:
    import sys
    target = parse_path(sys.argv[1]) if len(sys.argv) > 1 else None
    if target and not target.is_file():
        target = None
    Palimpsest(target).run()


if __name__ == "__main__":
    main()
