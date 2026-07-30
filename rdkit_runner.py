"""rdkit_runner.py — renders a real 2D molecular structure diagram from a
SMILES string using RDKit's own drawing engine, so Florinda can show the
user an actual structure instead of only describing a molecule in words —
same "verify, don't guess" principle as qiskit_runner.py/latex_runner.py,
applied to chemistry instead of circuits/typesetting.

WHY RDKit's own Draw.MolToImage instead of py3Dmol: py3Dmol wraps 3Dmol.js,
a WebGL-based JS viewer meant for interactive display inside a Jupyter
notebook or browser — there is no in-process way to pull a static PNG out
of it without actually running a JS/WebGL engine somewhere (e.g. driving a
real or headless browser to render its generated HTML and screenshotting
the canvas), which is a much heavier, more fragile dependency than this
project's existing render-to-PNG tools need. RDKit draws directly to a
raster image in-process (Cairo-backed), the same "one function call, one
real PNG" shape as matplotlib's `draw(output="mpl")` in qiskit_runner.py —
verified live with a real SMILES string (aspirin) producing a correct,
readable structure diagram.

WHY a bad SMILES surfaces RDKit's own parse error, not a generic message:
`Chem.MolFromSmiles` returns None on failure without raising or printing to
Python's own stderr — its parse error is written straight to the process's
OS-level stderr file descriptor (verified live: contextlib.redirect_stderr
does NOT catch it, an fd-level os.dup2 redirect does). Capturing that real
text is the same "surface the real error, don't paraphrase" principle as
latex_runner.py's _tail_log reading pdflatex's actual .log output.

WHY figures never expire and how popups work: shared with
qiskit_runner.py/latex_runner.py — see figure_popup.py's own WHY note.
"""
import os
import shutil
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Optional

from rdkit import Chem
from rdkit.Chem import Draw

from figure_popup import popup_image

FIGURES_DIR = Path.home() / ".local/share/flora-ai/rdkit-figures"
SAVED_FIGURES_DIR = Path.home() / "Documents/Research/rdkit-figures"
_MAX_RETAINED_RUNS = 20
_IMAGE_SIZE = (500, 500)


class RdkitRunnerError(Exception):
    """Raised when a SMILES string fails to parse or fails to render."""


def save_figure(source_path: str, name: str | None = None) -> Path:
    """Copies a rendered PNG out of FIGURES_DIR (auto-pruned, ephemeral) into
    SAVED_FIGURES_DIR (permanent, never touched by _prune_old_figures)."""
    src = Path(source_path)
    if not src.exists():
        raise RdkitRunnerError(f"no such figure: {source_path}")
    SAVED_FIGURES_DIR.mkdir(parents=True, exist_ok=True)
    dest_name = name if name else f"rdkit-figure-{int(time.time())}"
    if not dest_name.endswith(".png"):
        dest_name += ".png"
    dest = SAVED_FIGURES_DIR / dest_name
    shutil.copy2(src, dest)
    return dest


def _prune_old_figures() -> None:
    """Same convention as qiskit_runner.py/latex_runner.py's
    _prune_old_figures — directory names are `{unix_timestamp}-{uuid}`, so a
    lexicographic sort is also a chronological one."""
    if not FIGURES_DIR.exists():
        return
    run_dirs = sorted(d for d in FIGURES_DIR.iterdir() if d.is_dir())
    for stale_dir in run_dirs[:-_MAX_RETAINED_RUNS]:
        shutil.rmtree(stale_dir, ignore_errors=True)


def render(source: str) -> Path:
    """Parses `source` as a SMILES string and pops up its 2D structure
    diagram immediately. Returns the PNG's path."""
    smiles = source.strip()
    if not smiles:
        raise RdkitRunnerError("no SMILES string given")

    mol, parse_error = _parse_smiles(smiles)
    if mol is None:
        detail = f":\n{parse_error}" if parse_error else ""
        raise RdkitRunnerError(f"could not parse SMILES {smiles!r}{detail}")

    _prune_old_figures()
    run_id = f"{int(time.time())}-{uuid.uuid4().hex[:8]}"
    output_dir = FIGURES_DIR / run_id
    output_dir.mkdir(parents=True, exist_ok=True)
    png_path = output_dir / "figure.png"

    try:
        image = Draw.MolToImage(mol, size=_IMAGE_SIZE)
    except Exception as error:
        raise RdkitRunnerError(f"RDKit failed to render {smiles!r}: {error}") from error
    image.save(png_path)

    popup_image(str(png_path), "Molecule")
    return png_path


def _parse_smiles(smiles: str) -> tuple[Optional["Chem.Mol"], str]:
    """Returns (mol, stderr_text). mol is None on failure — RDKit's real
    parse error goes to the process's OS-level stderr fd, not an exception
    or Python's own sys.stderr, so it's captured via an fd-level redirect
    (verified live: contextlib.redirect_stderr alone does not catch it)."""
    with tempfile.TemporaryFile(mode="w+") as tmp:
        saved_fd = os.dup(2)
        os.dup2(tmp.fileno(), 2)
        try:
            mol = Chem.MolFromSmiles(smiles)
        finally:
            os.dup2(saved_fd, 2)
            os.close(saved_fd)
        tmp.seek(0)
        return mol, tmp.read().strip()


def _main() -> None:
    action = sys.argv[1] if len(sys.argv) >= 2 else None

    if action == "run":
        source = sys.stdin.read()
        try:
            png_path = render(source)
        except RdkitRunnerError as error:
            print(f"Error: {error}", file=sys.stderr)
            sys.exit(1)
        print("Image already popped up on screen (no further action needed).")
        print(f"figure: {png_path}")
        return

    if action == "save":
        if len(sys.argv) < 3:
            print("usage: rdkit_runner.py save <figure_path> [name]", file=sys.stderr)
            sys.exit(1)
        name = sys.argv[3] if len(sys.argv) > 3 else None
        try:
            dest = save_figure(sys.argv[2], name)
        except RdkitRunnerError as error:
            print(f"Error: {error}", file=sys.stderr)
            sys.exit(1)
        print(f"Saved permanently to {dest} (won't be auto-deleted)")
        return

    print("usage: rdkit_runner.py run  (reads a SMILES string from stdin)", file=sys.stderr)
    print("       rdkit_runner.py save <figure_path> [name]", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    _main()
