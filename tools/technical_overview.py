"""technical_overview.py — turns a paper's or website's content (whatever's
on screen or pasted into the conversation) into a permanent Markdown
technical writeup where every equation is REAL, compiled LaTeX — not a text
description of one — and every plot is a REAL executed matplotlib figure of
that equation over a sensible range, not a hallucinated description of what
the curve "would" look like. Same "verify, don't guess" principle as
latex_runner.py/qiskit_runner.py, applied to a whole document instead of one
snippet, and to any subject (physics, chemistry, atmospheric science, ...),
not just Qiskit/quantum.

WHY the AI supplies a structured spec (JSON) rather than this tool calling
its own LLM to extract equations: the assistant has ALREADY read the
on-screen/pasted content by the time this runs — a second, separate model
call inside this tool would just be a blinder, less-informed rewrite of work
already done in-conversation. This tool's job is only to render/verify/
assemble what the AI has already analyzed and authored, the same division
of labor as latex_runner.py (AI writes the LaTeX, tool really compiles it)
and qiskit_runner.py (AI writes the circuit, tool really runs it).

WHY equations get composited with their plot into ONE image (see _compose):
matplotlib's mathtext can render only a LIMITED LaTeX subset in a title or
annotation — real amsmath constructs (nested \\frac, \\sigma with
sub/superscripts, ...) aren't guaranteed to render correctly there. Using
the SAME real pdflatex compile as every other equation in this document (via
latex_runner.compile_source, not matplotlib's own approximation) means an
equation looks identical whether or not it has a plot attached, and never
silently mis-renders a symbol mathtext doesn't support.

WHY a fresh subprocess per plot, not exec() in-process: the AI's plotting
code is untrusted-ish generated code (same trust level as qiskit_runner.py's
circuit code) — an isolated subprocess with a timeout keeps one bad/hanging
plot from taking down the whole document build, and MPLBACKEND=Agg avoids
the same plt.show()-would-hang problem qiskit_runner.py already solved.

WHY a failed equation/plot doesn't abort the whole build: a technical
overview is typically many equations across several sections — one bad
LaTeX macro or one buggy plot script shouldn't throw away every other
section that compiled and ran fine. Each failure is left as a visible note
inline in the document AND printed to stderr, so the assistant can see
exactly what to fix in a follow-up call instead of the whole thing silently
vanishing or hard-failing.

WHY it auto-opens in Obsidian rather than just printing a path: the whole
point of a technical overview is the user actually reading it (rendered
headings, embedded equation+plot images), not a file path they'd have to go
open themselves — same reasoning as qiskit_runner.py/latex_runner.py
popping figures up automatically with no separate action needed. Uses
obsidian-cli the same way knowledge_base.py's open_note does (see
_open_in_obsidian) — best-effort, since a missing obsidian-cli install
should never fail the actual document build, which has already fully
succeeded by that point.

WHY every plotted equation ALSO gets a saved, standalone .py script (see
_save_plot_script), not just the one-off temp file _run_plot executes and
discards: the static PNG in the document is only ONE set of numbers — the
user asked to be able to actually try other values themselves (a different
albedo, a different temperature range, ...) in their own editor (Spyder),
not just stare at the one curve this build happened to produce. The saved
script is the AI's plot code verbatim plus a trailing plt.show(), so it
runs standalone with no dependency on this tool or its Agg/headless
subprocess plumbing — Spyder (or any plain `python3 script.py`) opens a
real interactive window the user can actually edit numbers in and rerun.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Optional

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from latex_runner import LatexRunnerError, compile_source

OVERVIEWS_DIR = Path.home() / "Documents/Research/technical-overviews"
_PLOT_TIMEOUT_S = 30
_COMPOSE_PADDING = 24
_FIGURE_MARKER = "__FLORA_PLOT__:"

_SAVE_FIGURE_EPILOGUE = """
try:
    import matplotlib.pyplot as _hypr_plt
    _hypr_nums = _hypr_plt.get_fignums()
    if _hypr_nums:
        _hypr_path = {output_path!r}
        _hypr_plt.figure(_hypr_nums[0]).savefig(_hypr_path, bbox_inches="tight", dpi=150)
        print({marker!r} + _hypr_path)
except Exception:
    pass
"""


class TechnicalOverviewError(Exception):
    """Raised when the spec itself is malformed (missing subject/sections) —
    NOT raised for a single equation/plot failing to render/run, which is
    handled per-item instead so the rest of the document still gets built."""


def _run_plot(code: str, work_dir: Path) -> tuple[Optional[Path], Optional[str]]:
    """Returns (png_path, error). png_path is None if the code ran cleanly
    but produced no figure (not every equation is meaningfully plottable) —
    that is NOT an error. error is set only on an actual crash/timeout."""
    output_path = work_dir / f"plot-{uuid.uuid4().hex[:8]}.png"
    epilogue = _SAVE_FIGURE_EPILOGUE.format(output_path=str(output_path), marker=_FIGURE_MARKER)
    combined = f"{code}\n\n{epilogue}"
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as tmp_file:
        tmp_file.write(combined)
        tmp_path = Path(tmp_file.name)
    try:
        result = subprocess.run(
            [sys.executable, str(tmp_path)],
            capture_output=True, text=True, timeout=_PLOT_TIMEOUT_S,
            env={**os.environ, "MPLBACKEND": "Agg"},
        )
    except subprocess.TimeoutExpired:
        return None, f"plot code exceeded {_PLOT_TIMEOUT_S}s, aborted"
    finally:
        tmp_path.unlink(missing_ok=True)
    if _FIGURE_MARKER in result.stdout:
        return (output_path if output_path.exists() else None), None
    if result.returncode != 0:
        return None, result.stderr.strip()[-1500:]
    return None, None  # ran fine, just never opened a figure — not an error


def _compose(equation_png: Path, plot_png: Optional[Path], dest: Path) -> None:
    """Stacks the real compiled equation image above its real executed plot
    (when one exists) into a single PNG, centered on a shared white canvas —
    so the equation is never shown separated from the curve it produced."""
    images = [Image.open(equation_png)]
    if plot_png is not None:
        images.append(Image.open(plot_png))
    width = max(img.width for img in images) + _COMPOSE_PADDING * 2
    height = sum(img.height for img in images) + _COMPOSE_PADDING * (len(images) + 1)
    canvas = Image.new("RGB", (width, height), "white")
    y = _COMPOSE_PADDING
    for img in images:
        x = (width - img.width) // 2
        canvas.paste(img.convert("RGB"), (x, y))
        y += img.height + _COMPOSE_PADDING
    canvas.save(dest)


def build(spec: dict, overviews_dir: Path = OVERVIEWS_DIR) -> tuple[Path, list[str]]:
    """Renders every equation for real and runs every plot for real, then
    assembles the result into a permanent Markdown file with its own images
    subfolder. Returns (markdown_path, [per-item error strings]) — the
    document is still written even if some items failed."""
    subject = (spec.get("subject") or "").strip()
    sections = spec.get("sections") or []
    if not subject:
        raise TechnicalOverviewError("spec is missing a non-empty 'subject'")
    if not sections:
        raise TechnicalOverviewError("spec has no 'sections' to render")

    doc_dir = _fresh_dir(overviews_dir, _slugify(subject))
    images_dir = doc_dir / "images"
    scripts_dir = doc_dir / "scripts"
    images_dir.mkdir(parents=True, exist_ok=True)
    scripts_dir.mkdir(parents=True, exist_ok=True)

    lines = [f"# {subject}\n"]
    source_note = (spec.get("source_note") or "").strip()
    if source_note:
        lines.append(f"*{source_note}*\n")

    errors: list[str] = []
    image_index = 0
    for section in sections:
        heading = (section.get("heading") or "").strip()
        if heading:
            lines.append(f"\n## {heading}\n")
        body = (section.get("body") or "").strip()
        if body:
            lines.append(f"{body}\n")
        for equation in section.get("equations") or []:
            image_index += 1
            rendered, error = _render_equation(equation, image_index, images_dir, scripts_dir)
            lines.extend(rendered)
            if error:
                errors.append(f"[{equation.get('name', f'Equation {image_index}')}] {error}")

    doc_path = doc_dir / "overview.md"
    doc_path.write_text("\n".join(lines).strip() + "\n")

    obsidian_error = _open_in_obsidian(doc_path, overviews_dir.parent)
    if obsidian_error:
        errors.append(f"[Obsidian] {obsidian_error}")

    return doc_path, errors


def _render_equation(
    equation: dict, index: int, images_dir: Path, scripts_dir: Path
) -> tuple[list[str], Optional[str]]:
    name = (equation.get("name") or f"Equation {index}").strip()
    latex = (equation.get("latex") or "").strip()
    explanation = (equation.get("explanation") or "").strip()
    if not latex:
        return [f"\n### {name}\n", "*(no LaTeX given for this equation)*\n"], "missing 'latex'"

    try:
        equation_png = compile_source(f"\\[ {latex} \\]")
    except LatexRunnerError as error:
        short = _first_line(str(error))
        out = [f"\n### {name}\n", f"```\n{latex}\n```\n", f"*(this equation could not be rendered: {short})*\n"]
        return out, f"LaTeX compile failed: {short}"

    plot = equation.get("plot") or {}
    plot_code = (plot.get("code") or "").strip()
    plot_png, plot_error, script_path = (None, None, None)
    if plot_code:
        plot_png, plot_error = _run_plot(plot_code, images_dir)
        script_path = _save_plot_script(name, latex, plot_code, index, scripts_dir)

    dest = images_dir / f"eq-{index}.png"
    _compose(equation_png, plot_png, dest)

    out = [f"\n### {name}\n", f"![{name}](images/{dest.name})\n", f"```\n{latex}\n```\n"]
    if explanation:
        out.append(f"{explanation}\n")
    caption = (plot.get("caption") or "").strip()
    if plot_png is not None and caption:
        out.append(f"*{caption}*\n")
    if plot_error:
        out.append(f"*(plot could not be generated: {plot_error})*\n")
    if script_path is not None:
        rel = script_path.relative_to(scripts_dir.parent)
        out.append(f"*Run this yourself: `{rel}` — edit any value and rerun (e.g. F5 in Spyder) to try other outcomes.*\n")
    return out, (f"plot failed: {plot_error}" if plot_error else None)


def _save_plot_script(name: str, latex: str, code: str, index: int, scripts_dir: Path) -> Path:
    """Saves the AI's plot code verbatim, plus a trailing plt.show(), as a
    real standalone .py file the user can open and rerun themselves (Spyder,
    Jupyter, or a plain interpreter) with no dependency on this tool or its
    headless Agg subprocess plumbing — so trying a different number is a
    one-line edit + rerun, not something that requires coming back to
    Florinda at all.

    WHY a `#`-comment header, not a triple-quoted docstring: verified
    live — raw LaTeX embedded in a real Python string literal gets silently
    mangled by Python's OWN escape-sequence parsing (`\\alpha` starts with
    `\\a`, a real Python escape for the BEL control character — the header
    would have quietly become a bell char followed by `lpha`, not the LaTeX
    the equation actually says). A `#` comment is never escape-processed by
    the tokenizer at all, so the LaTeX shown here is guaranteed byte-for-byte
    identical to the equation used to compile the image above it."""
    header_lines = [f"# {name}", "#", *(f"# {line}" for line in latex.splitlines()),
                     "#", "# Generated by technical_overview.py — edit any value below and rerun to explore other outcomes."]
    script = "\n".join(header_lines) + "\n\n" + code.strip() + "\n\nplt.show()\n"
    path = scripts_dir / f"eq-{index}.py"
    path.write_text(script)
    return path


def _open_in_obsidian(doc_path: Path, vault_root: Path) -> Optional[str]:
    """Opens the freshly built document directly in Obsidian via
    obsidian-cli, so the user sees the real rendered document (embedded
    images, headings) immediately instead of a bare file path — same
    vault-relative-name convention as knowledge_base.py's open_note
    (obsidian-cli resolves a vault by its REGISTERED NAME, which for
    OVERVIEWS_DIR's default location is "Research", the name under which
    `~/Documents/Research` is already registered as a vault covering every
    other thing this project saves there).

    WHY best-effort, not fatal: the document itself is already fully built
    and saved by the time this runs — a missing obsidian-cli install or an
    unregistered vault must not be allowed to look like the BUILD failed,
    only that this one extra convenience step couldn't happen. Returns an
    error string on failure (folded into build()'s existing errors list),
    None on success."""
    try:
        note_name = str(doc_path.relative_to(vault_root).with_suffix(""))
    except ValueError:
        return f"{doc_path} is not under the Obsidian vault root {vault_root}, skipped"
    try:
        subprocess.Popen(
            ["obsidian-cli", "open", note_name, "-v", vault_root.name],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        return "obsidian-cli is not installed, could not auto-open"
    return None


def _fresh_dir(overviews_dir: Path, slug: str) -> Path:
    doc_dir = overviews_dir / slug
    suffix = 2
    while doc_dir.exists():
        doc_dir = overviews_dir / f"{slug}-{suffix}"
        suffix += 1
    return doc_dir


def _first_line(error_text: str) -> str:
    """LatexRunnerError's message is _tail_log's full ~20-line dump (real
    TeX error line plus memory-usage noise) — fine for stderr, unreadable
    inline in a generated document. Pull out just the real `! ...` error
    line (TeX's own error marker) if one is present, else fall back to the
    first non-empty line."""
    for line in error_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("!"):
            return stripped
    return next((line.strip() for line in error_text.splitlines() if line.strip()), error_text[:200])


def _slugify(text: str) -> str:
    slug = re.sub(r"[^\w\s-]", "", text).strip().lower()
    slug = re.sub(r"[\s_-]+", "-", slug)
    return slug[:80] or "overview"


def _main() -> None:
    action = sys.argv[1] if len(sys.argv) >= 2 else None

    if action == "build":
        raw = sys.stdin.read()
        if not raw.strip():
            print("Error: no JSON spec given on stdin", file=sys.stderr)
            sys.exit(1)
        try:
            spec = json.loads(raw)
        except json.JSONDecodeError as error:
            print(f"Error: invalid JSON spec: {error}", file=sys.stderr)
            sys.exit(1)
        try:
            doc_path, errors = build(spec)
        except TechnicalOverviewError as error:
            print(f"Error: {error}", file=sys.stderr)
            sys.exit(1)
        print(f"Saved technical overview to {doc_path}")
        if errors:
            print(f"\n{len(errors)} item(s) had problems (document still saved with the rest intact):", file=sys.stderr)
            for error in errors:
                print(f"  - {error}", file=sys.stderr)
        return

    print("usage: technical_overview.py build  (reads a JSON spec from stdin)", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    _main()
