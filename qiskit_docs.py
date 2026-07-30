"""qiskit_docs.py — on-demand Qiskit API reference, read straight from the
REAL installed packages (not training data, which observed live does not
match this pinned environment: qiskit 2.4.1 / qiskit-aer 0.17.2). Use this to
check a specific symbol before guessing at it; a wrong guess otherwise only
surfaces as a Python traceback after actually running the circuit.

WHY this exists alongside INSTRUCTION.md's Qiskit Notes section (a short,
static list of the handful of gotchas that have actually recurred — e.g.
c_if, the primitives import path): that list is deliberately small and
can't cover every symbol in Qiskit. This is the fallback for anything else
— search by keyword, or look up one specific dotted path.

WHY this shells out to the quantum-projects venv (same reasoning as
qiskit_runner.py) rather than importing qiskit directly: qiskit isn't
installed in flora-ai's own venv, only in ~/Projects/quantum-projects/venv —
this script runs in flora-ai's normal environment (same invocation
convention as every other tool here) and delegates the actual introspection
to a subprocess using that venv's interpreter.
"""
import subprocess
import sys
from pathlib import Path

QUANTUM_VENV_PYTHON = Path.home() / "Projects/quantum-projects/venv/bin/python3"
TIMEOUT_S = 20

# Modules worth searching for `search` — deliberately not the entire
# installed package tree (slow, and pulls in many internal/rarely-used
# symbols); these are the ones actually touched by qiskit_runner.py requests.
_SEARCH_MODULES = (
    "qiskit.circuit",
    "qiskit.circuit.library",
    "qiskit.quantum_info",
    "qiskit.visualization",
    "qiskit.transpiler",
    "qiskit.primitives",
    "qiskit_aer",
    "qiskit_aer.primitives",
)

_INTROSPECT_SCRIPT = r"""
import importlib, inspect, sys

def first_doc_line(obj):
    doc = inspect.getdoc(obj) or ""
    return doc.strip().splitlines()[0] if doc.strip() else ""

def do_search(keyword):
    keyword = keyword.lower()
    matches = []
    for module_name in SEARCH_MODULES:
        try:
            module = importlib.import_module(module_name)
        except ImportError:
            continue
        for name, obj in inspect.getmembers(module):
            if name.startswith("_"):
                continue
            if not (inspect.isclass(obj) or inspect.isfunction(obj)):
                continue
            summary = first_doc_line(obj)
            if keyword in name.lower() or keyword in summary.lower():
                matches.append((module_name + "." + name, summary))
    if not matches:
        print("No matches for %r in %s." % (keyword, ", ".join(SEARCH_MODULES)))
        return
    for dotted_path, summary in matches[:25]:
        print(dotted_path + " -- " + summary if summary else dotted_path)

def do_lookup(dotted_path):
    parts = dotted_path.split(".")
    module = None
    consumed = 0
    for split_at in range(len(parts), 0, -1):
        candidate = ".".join(parts[:split_at])
        try:
            module = importlib.import_module(candidate)
            consumed = split_at
            break
        except ImportError:
            continue
    if module is None:
        print("Error: no importable module prefix found in %r" % dotted_path, file=sys.stderr)
        sys.exit(1)

    obj = module
    resolved = parts[:consumed]
    try:
        for attr in parts[consumed:]:
            obj = getattr(obj, attr)
            resolved.append(attr)
    except AttributeError as error:
        print("Error: %s (resolved so far: %s)" % (error, ".".join(resolved)), file=sys.stderr)
        sys.exit(1)

    print("# " + ".".join(resolved))
    try:
        print("signature: " + str(inspect.signature(obj)))
    except (TypeError, ValueError):
        pass
    doc = inspect.getdoc(obj)
    if doc:
        doc_lines = doc.splitlines()
        print("\n".join(doc_lines[:40]))
        if len(doc_lines) > 40:
            print("... (%d more lines truncated)" % (len(doc_lines) - 40))
    else:
        print("(no docstring)")

SEARCH_MODULES = MODULES_PLACEHOLDER
if MODE_PLACEHOLDER == "search":
    do_search(ARG_PLACEHOLDER)
else:
    do_lookup(ARG_PLACEHOLDER)
"""


def _run_introspection(mode: str, arg: str) -> subprocess.CompletedProcess:
    if not QUANTUM_VENV_PYTHON.exists():
        raise FileNotFoundError(f"quantum-projects venv not found at {QUANTUM_VENV_PYTHON}")
    script = (
        _INTROSPECT_SCRIPT
        .replace("MODULES_PLACEHOLDER", repr(_SEARCH_MODULES))
        .replace("MODE_PLACEHOLDER", repr(mode))
        .replace("ARG_PLACEHOLDER", repr(arg))
    )
    return subprocess.run(
        [str(QUANTUM_VENV_PYTHON), "-c", script],
        capture_output=True, text=True, timeout=TIMEOUT_S,
    )


def _main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Look up real, installed Qiskit API reference")
    subparsers = parser.add_subparsers(dest="action", required=True)

    search_parser = subparsers.add_parser("search")
    search_parser.add_argument("keyword")

    lookup_parser = subparsers.add_parser("lookup")
    lookup_parser.add_argument("dotted_path")

    args = parser.parse_args()
    mode = args.action
    arg = args.keyword if mode == "search" else args.dotted_path

    try:
        result = _run_introspection(mode, arg)
    except (FileNotFoundError, subprocess.TimeoutExpired) as error:
        print(f"Error: {error}", file=sys.stderr)
        sys.exit(1)

    if result.stdout:
        print(result.stdout, end="")
    if result.returncode != 0:
        print(result.stderr, file=sys.stderr, end="")
        sys.exit(1)


if __name__ == "__main__":
    _main()
