"""latex_runner.py — renders real LaTeX to a PNG using the system's actual
TeX Live install (verified live: pdflatex, and the `standalone` document
class + `varwidth` package, are all present), so Florinda can show the user a
genuinely typeset equation/derivation instead of only speaking it — the
same "verify, don't guess" principle as qiskit_runner.py, applied to
typesetting instead of circuit correctness. A bad LaTeX source fails with a
real compiler error (surfaced from the .log), not a silently wrong image.

WHY `standalone` instead of a full article-style document: the AI is meant
to hand this a snippet (an equation, a derivation, a matrix) — `standalone`
with `border`/`varwidth` crops the output to exactly the content's bounding
box, so the resulting PNG has no page margins around a few lines of math,
matching how a circuit diagram or histogram plot pops up. If the AI's source
already declares its own `\\documentclass`, it's used as-is instead (a full
document is trusted to already produce a real page), which costs nothing
extra to support and keeps this from being needlessly restrictive.

WHY pdftoppm over convert/dvipng: pdflatex's own PDF output goes straight to
pdftoppm (poppler-based, already installed) at a high enough DPI to look
sharp popped up on screen — no intermediate DVI stage, and no dependence on
ImageMagick's PDF policy (which is disabled by default on many distros and
would otherwise fail unpredictably).

WHY figures never expire and how popups work: shared with qiskit_runner.py
— see figure_popup.py's own WHY note.
"""
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

from figure_popup import popup_image

FIGURES_DIR = Path.home() / ".local/share/flora-ai/latex-figures"
SAVED_FIGURES_DIR = Path.home() / "Documents/Research/latex-figures"
_MAX_RETAINED_RUNS = 20
_COMPILE_TIMEOUT_S = 30
_RENDER_DPI = 300

_DOCUMENTCLASS_RE = re.compile(r"\\documentclass")

_STANDALONE_TEMPLATE = r"""\documentclass[preview, border=12pt, varwidth]{standalone}
\usepackage{amsmath,amssymb,amsfonts}
\begin{document}
%s
\end{document}
"""


class LatexRunnerError(Exception):
    """Raised when pdflatex/pdftoppm can't be found or a render fails to complete."""


def save_figure(source_path: str, name: str | None = None) -> Path:
    """Copies a rendered PNG out of FIGURES_DIR (auto-pruned, ephemeral) into
    SAVED_FIGURES_DIR (permanent, never touched by _prune_old_figures)."""
    src = Path(source_path)
    if not src.exists():
        raise LatexRunnerError(f"no such figure: {source_path}")
    SAVED_FIGURES_DIR.mkdir(parents=True, exist_ok=True)
    dest_name = name if name else f"latex-figure-{int(time.time())}"
    if not dest_name.endswith(".png"):
        dest_name += ".png"
    dest = SAVED_FIGURES_DIR / dest_name
    shutil.copy2(src, dest)
    return dest


def _prune_old_figures() -> None:
    """Same convention as qiskit_runner.py's _prune_old_figures — directory
    names are `{unix_timestamp}-{uuid}`, so a lexicographic sort is also a
    chronological one."""
    if not FIGURES_DIR.exists():
        return
    run_dirs = sorted(d for d in FIGURES_DIR.iterdir() if d.is_dir())
    for stale_dir in run_dirs[:-_MAX_RETAINED_RUNS]:
        shutil.rmtree(stale_dir, ignore_errors=True)


def render(source: str) -> Path:
    """Compiles `source` (a LaTeX snippet, or a full document if it declares
    its own \\documentclass) to a cropped PNG and pops it up immediately.
    Returns the PNG's path."""
    if shutil.which("pdflatex") is None:
        raise LatexRunnerError("pdflatex not found — TeX Live doesn't seem to be installed")

    _prune_old_figures()
    run_id = f"{int(time.time())}-{uuid.uuid4().hex[:8]}"
    output_dir = FIGURES_DIR / run_id
    output_dir.mkdir(parents=True, exist_ok=True)

    document = source if _DOCUMENTCLASS_RE.search(source) else _STANDALONE_TEMPLATE % source
    tex_path = output_dir / "doc.tex"
    tex_path.write_text(document)

    try:
        result = subprocess.run(
            ["pdflatex", "-interaction=nonstopmode", "-halt-on-error",
             "-output-directory", str(output_dir), str(tex_path)],
            capture_output=True, text=True, timeout=_COMPILE_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        raise LatexRunnerError(f"LaTeX compile exceeded {_COMPILE_TIMEOUT_S}s, aborted")

    pdf_path = output_dir / "doc.pdf"
    if result.returncode != 0 or not pdf_path.exists():
        raise LatexRunnerError(f"LaTeX failed to compile:\n{_tail_log(output_dir, result)}")

    png_prefix = output_dir / "figure"
    convert = subprocess.run(
        ["pdftoppm", "-r", str(_RENDER_DPI), "-png", "-singlefile", str(pdf_path), str(png_prefix)],
        capture_output=True, text=True, timeout=_COMPILE_TIMEOUT_S,
    )
    png_path = png_prefix.with_suffix(".png")
    if convert.returncode != 0 or not png_path.exists():
        raise LatexRunnerError(f"PDF-to-PNG conversion failed: {convert.stderr.strip()}")

    popup_image(str(png_path), "LaTeX")
    return png_path


def _tail_log(output_dir: Path, result: subprocess.CompletedProcess) -> str:
    """pdflatex's real error is in the .log file, not stdout — same
    "surface the real error, don't paraphrase" principle as qiskit_runner.py
    letting a Python traceback through as-is.

    WHY anchored on the first `!` line, not a plain tail: TeX's actual error
    (`! Undefined control sequence.` etc.) can appear well before a long tail
    of font-loading/memory-usage noise that follows it — a plain last-N-lines
    tail risks cutting the real error off entirely on a longer document."""
    log_path = output_dir / "doc.log"
    if not log_path.exists():
        return (result.stdout + result.stderr)[-2000:]
    lines = log_path.read_text(errors="replace").splitlines()
    for index, line in enumerate(lines):
        if line.startswith("!"):
            return "\n".join(lines[index:index + 20])
    return "\n".join(lines[-40:])


def _main() -> None:
    action = sys.argv[1] if len(sys.argv) >= 2 else None

    if action == "run":
        source = sys.stdin.read()
        if not source.strip():
            print("Error: no LaTeX source given on stdin", file=sys.stderr)
            sys.exit(1)
        try:
            png_path = render(source)
        except LatexRunnerError as error:
            print(f"Error: {error}", file=sys.stderr)
            sys.exit(1)
        print("Image already popped up on screen (no further action needed).")
        print(f"figure: {png_path}")
        return

    if action == "save":
        if len(sys.argv) < 3:
            print("usage: latex_runner.py save <figure_path> [name]", file=sys.stderr)
            sys.exit(1)
        name = sys.argv[3] if len(sys.argv) > 3 else None
        try:
            dest = save_figure(sys.argv[2], name)
        except LatexRunnerError as error:
            print(f"Error: {error}", file=sys.stderr)
            sys.exit(1)
        print(f"Saved permanently to {dest} (won't be auto-deleted)")
        return

    print("usage: latex_runner.py run  (reads LaTeX source from stdin)", file=sys.stderr)
    print("       latex_runner.py save <figure_path> [name]", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    _main()
