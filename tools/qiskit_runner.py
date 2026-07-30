"""qiskit_runner.py — runs real Qiskit code using the actual Qiskit
environment at ~/Projects/quantum-projects/venv (qiskit 2.4.1 + qiskit-aer),
so Florinda can verify a circuit actually works instead of only reasoning about
it from training data or notes.

WHY a dedicated venv instead of installing qiskit into flora-ai's own venv:
this project already has a real, working Qiskit environment (qiskit-aer,
matplotlib, pylatexenc already there) — reusing it avoids duplicating a
large dependency tree and stays consistent with the user's actual toolchain,
rather than flora-ai carrying its own separate, potentially-drifting copy.

WHY MPLBACKEND=Agg: a circuit diagram or histogram plot often ends with
plt.show(), which would hang waiting for a display in this headless
execution context. Agg renders to memory/file without needing one;
savefig(...) still works normally if the code wants to save a figure.

WHY figures are auto-saved without the AI needing to write any save code:
verified live — circuit.draw(output="mpl") and plot_histogram(...) both
register their Figure with pyplot's global figure manager (plt.get_fignums())
even under Agg, with no plt.show()/savefig() call needed in the user's own
script. An epilogue appended after the user's code just walks whatever
figures ended up open and saves each one — the AI writes plain, ordinary
Qiskit code exactly as it would in a notebook, nothing extra to remember.

WHY figures pop up immediately via kitty, not via the user's default image
handler: this system has no dedicated image viewer installed — PNGs default
to opening as a Chromium tab, and killing that process to auto-dismiss it
would kill the user's entire browser session, not just one tab. kitty (a
terminal already used elsewhere in this project) can render an image inline
via `+kitten icat` in a fresh window this module fully owns. This also means
the AI doesn't need a separate follow-up turn to open anything — the image
is already on screen by the time run() returns. See figure_popup.py (shared
with latex_runner.py) for why no figure auto-closes on a timer anymore.
"""
import os
import subprocess
import shutil
import sys
import tempfile
import time
import uuid
from pathlib import Path

from figure_popup import popup_image

QUANTUM_VENV_PYTHON = Path.home() / "Projects/quantum-projects/venv/bin/python3"
FIGURES_DIR = Path.home() / ".local/share/flora-ai/qiskit-figures"
# WHY a separate, never-pruned location: FIGURES_DIR is auto-cleaned (see
# _MAX_RETAINED_RUNS below) since its figures are only ever meant for the
# one-time popup display — a user who wants to actually KEEP a specific
# result needs somewhere that isn't subject to that cleanup. Mirrors
# web_clip.py's ~/Documents/Research/hypr-clippings convention.
SAVED_FIGURES_DIR = Path.home() / "Documents/Research/qiskit-figures"
FIGURE_MARKER = "__FLORA_FIGURE__:"
TIMEOUT_S = 120
# WHY this exists: observed live -- nothing ever cleaned up old runs here;
# every single call left a new run_id directory behind forever (confirmed:
# 21 directories / 28 PNGs accumulated from ordinary use over ~2 days, with
# no cap). Each run's figures are only ever needed for their one-time popup
# display and are never referenced again afterward, so unbounded retention
# serves no purpose -- just keep enough recent runs to be useful for
# debugging a just-happened issue.
_MAX_RETAINED_RUNS = 20

_SAVE_FIGURES_EPILOGUE = """
try:
    import matplotlib.pyplot as _hypr_plt
    for _hypr_num in _hypr_plt.get_fignums():
        _hypr_path = {output_dir!r} + "/figure_" + str(_hypr_num) + ".png"
        _hypr_plt.figure(_hypr_num).savefig(_hypr_path, bbox_inches="tight")
        print({marker!r} + _hypr_path)
except Exception:
    pass
"""

# WHY this exists: observed live, repeatedly — the model reflexively wraps
# draw()/plot_histogram() calls in display(...), since that's the standard
# idiom in every Jupyter-notebook-style Qiskit tutorial it's ever seen. This
# is a plain script, not a notebook, so bare `display` is undefined and
# crashes the whole run. Rather than relying on the model to remember not to
# do this (it hasn't, twice), just make display() work here: a Figure is
# already tracked by pyplot's figure manager the moment draw()/plot_histogram
# create it, regardless of whether anything is later done with the return
# value — so display() only needs to not crash. For anything that ISN'T a
# figure (e.g. display(counts)), fall back to print() so that call still
# surfaces something instead of silently doing nothing.
_DISPLAY_SHIM_PREAMBLE = """
def display(*_hypr_args, **_hypr_kwargs):
    for _hypr_arg in _hypr_args:
        if not hasattr(_hypr_arg, "savefig"):
            print(_hypr_arg)
"""


class QiskitRunnerError(Exception):
    """Raised when the quantum-projects venv can't be found or a run fails to complete."""


def save_figure(source_path: str, name: str | None = None) -> Path:
    """Copies a figure PNG out of FIGURES_DIR (auto-pruned, ephemeral) into
    SAVED_FIGURES_DIR (permanent, never touched by _prune_old_figures)."""
    src = Path(source_path)
    if not src.exists():
        raise QiskitRunnerError(f"no such figure: {source_path}")
    SAVED_FIGURES_DIR.mkdir(parents=True, exist_ok=True)
    dest_name = name if name else f"qiskit-figure-{int(time.time())}"
    if not dest_name.endswith(".png"):
        dest_name += ".png"
    dest = SAVED_FIGURES_DIR / dest_name
    shutil.copy2(src, dest)
    return dest


def _prune_old_figures() -> None:
    """Deletes all but the _MAX_RETAINED_RUNS most recent run directories
    under FIGURES_DIR. Directory names are `{unix_timestamp}-{uuid}`, so a
    plain lexicographic sort is also a chronological one."""
    if not FIGURES_DIR.exists():
        return
    run_dirs = sorted(d for d in FIGURES_DIR.iterdir() if d.is_dir())
    for stale_dir in run_dirs[:-_MAX_RETAINED_RUNS]:
        shutil.rmtree(stale_dir, ignore_errors=True)


def run(source: str) -> tuple[str, list[str]]:
    """Returns (text_output, [saved_figure_paths]). Any matplotlib figure
    left open by `source` (a circuit diagram, a histogram, ...) is saved as
    a PNG automatically — no savefig() needed in the Qiskit code itself."""
    if not QUANTUM_VENV_PYTHON.exists():
        raise QiskitRunnerError(f"quantum-projects venv not found at {QUANTUM_VENV_PYTHON}")

    _prune_old_figures()
    run_id = f"{int(time.time())}-{uuid.uuid4().hex[:8]}"
    output_dir = FIGURES_DIR / run_id
    output_dir.mkdir(parents=True, exist_ok=True)
    epilogue = _SAVE_FIGURES_EPILOGUE.format(output_dir=str(output_dir), marker=FIGURE_MARKER)
    combined_source = f"{_DISPLAY_SHIM_PREAMBLE}\n\n{source}\n\n{epilogue}"

    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as tmp_file:
        tmp_file.write(combined_source)
        tmp_path = Path(tmp_file.name)

    try:
        result = subprocess.run(
            [str(QUANTUM_VENV_PYTHON), str(tmp_path)],
            capture_output=True, text=True, timeout=TIMEOUT_S,
            env={**os.environ, "MPLBACKEND": "Agg"},
        )
    except subprocess.TimeoutExpired:
        raise QiskitRunnerError(f"circuit run exceeded {TIMEOUT_S}s, aborted")
    finally:
        tmp_path.unlink(missing_ok=True)

    lines = result.stdout.splitlines()
    figures = [line[len(FIGURE_MARKER):] for line in lines if line.startswith(FIGURE_MARKER)]
    text_lines = [line for line in lines if not line.startswith(FIGURE_MARKER)]
    output = "\n".join(text_lines)
    if result.returncode != 0:
        output += f"\n[exited {result.returncode}]\n{result.stderr}"

    for index, figure_path in enumerate(figures):
        title = "Circuit" if index == 0 else "Results"
        popup_image(figure_path, title)

    return output.strip(), figures


def _main() -> None:
    action = sys.argv[1] if len(sys.argv) >= 2 else None

    if action == "run":
        source = sys.stdin.read()
        try:
            output, figures = run(source)
        except QiskitRunnerError as error:
            print(f"Error: {error}", file=sys.stderr)
            sys.exit(1)
        if output:
            print(output)
        if figures:
            print(f"\n{len(figures)} figure(s) already popped up on screen (no further action needed).")
            for figure_path in figures:
                print(f"figure: {figure_path}")
        return

    if action == "save":
        if len(sys.argv) < 3:
            print("usage: qiskit_runner.py save <figure_path> [name]", file=sys.stderr)
            sys.exit(1)
        name = sys.argv[3] if len(sys.argv) > 3 else None
        try:
            dest = save_figure(sys.argv[2], name)
        except QiskitRunnerError as error:
            print(f"Error: {error}", file=sys.stderr)
            sys.exit(1)
        print(f"Saved permanently to {dest} (won't be auto-deleted)")
        return

    print("usage: qiskit_runner.py run  (reads Python source from stdin)", file=sys.stderr)
    print("       qiskit_runner.py save <figure_path> [name]", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    _main()
