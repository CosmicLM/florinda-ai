"""file_search.py — searches anywhere on the filesystem the current user can
read, not limited to any one folder (see knowledge_base.py for the narrower,
Obsidian-vault-specific search/read/open tool over quantum-knowledge-base
specifically — that one stays scoped on purpose since it also has an `open`
action tied to one real vault; this one is plain read-only search with no
notion of "the" vault). Two modes: find files by name, or search inside
files by content.

WHY fd/ripgrep instead of find/grep -r: both already installed on this
system, and both are dramatically faster and better-behaved for a
whole-filesystem walk than the coreutils equivalents — they skip
permission-denied directories silently instead of spamming stderr, and
support real exclude-glob flags natively instead of find's clunkier -prune.

WHY /proc, /sys, /dev, /run are excluded by default, and this is the only
restriction: these aren't real files — they're kernel-exposed virtual
interfaces (live process info, device nodes, hardware state). Recursively
walking them produces enormous irrelevant output and, in /proc's case, can
hang on entries that block on read. This is a practical necessity to make a
whole-system search actually finish, not a content restriction on what the
user can find — every real file anywhere the user's own account can read,
including hidden files and anything a .gitignore would otherwise hide, is in
scope. Root defaults to `/` — the whole system — not just $HOME.
"""
import argparse
import shutil
import subprocess
import sys
from pathlib import Path

_EXCLUDED_DIRS = ("/proc", "/sys", "/dev", "/run")
_FIND_DEFAULT_ROOT = "/"
# WHY content search defaults to $HOME, not `/` like find_by_name does:
# verified live — finding files BY NAME across the whole `/` took ~4s (fd
# only has to stat filenames), but searching file CONTENT genuinely has to
# read file bytes, and a real run scoped to just $HOME (not even the whole
# filesystem) took 130s on this machine's real, heavily-used disk. Starting
# content search at `/` by default would be dramatically slower for content
# that's overwhelmingly not there anyway (binaries, system libraries) —
# --root can still override this to `/` explicitly any time it's genuinely
# needed, this is a practical default, not a restriction on scope.
_GREP_DEFAULT_ROOT = str(Path.home())
_MAX_RESULTS = 200
_FIND_TIMEOUT_S = 60
_GREP_TIMEOUT_S = 240


class FileSearchError(Exception):
    """Raised when fd/ripgrep can't run or the search exceeds its time budget."""


def _resolve_fd_binary() -> str:
    """Debian/Ubuntu's `fd-find` package installs its binary as `fdfind`,
    not `fd` — an unrelated, preexisting Debian package called `fd`
    ("fastdate") already owns that name there. Arch and Fedora's `fd`
    packages don't have this collision and install it as plain `fd`.
    Resolved at call time so this works regardless of which the running
    system actually has, rather than assuming one naming convention."""
    return "fd" if shutil.which("fd") else "fdfind"


def find_by_name(pattern: str, root: str = _FIND_DEFAULT_ROOT) -> str:
    fd_bin = _resolve_fd_binary()
    if not shutil.which(fd_bin):
        raise FileSearchError(
            "`fd` not found on PATH — install the `fd` package (Arch/Fedora) "
            "or `fd-find` (Debian/Ubuntu, which installs it as `fdfind` "
            "instead — see SYSTEM_REQUIREMENTS.md)"
        )
    command = [
        fd_bin, "--hidden", "--no-ignore", "--absolute-path",
        *[f"--exclude={path}" for path in _EXCLUDED_DIRS],
        pattern, root,
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=_FIND_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        raise FileSearchError(f"search for {pattern!r} under {root!r} exceeded {_FIND_TIMEOUT_S}s — try a narrower --root")
    return _format_results([line for line in result.stdout.splitlines() if line])


def search_content(query: str, root: str = _GREP_DEFAULT_ROOT) -> str:
    if not shutil.which("rg"):
        raise FileSearchError(
            "`rg` (ripgrep) not found on PATH — install the `ripgrep` package; see SYSTEM_REQUIREMENTS.md"
        )
    command = [
        "rg", "--hidden", "--no-ignore", "-l",
        *[f"--glob=!{path}/**" for path in _EXCLUDED_DIRS],
        "--", query, root,
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=_GREP_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        raise FileSearchError(f"search for {query!r} under {root!r} exceeded {_GREP_TIMEOUT_S}s — try a narrower --root")
    return _format_results([line for line in result.stdout.splitlines() if line])


def _format_results(lines: list[str]) -> str:
    if not lines:
        return "No matches found."
    truncated = len(lines) > _MAX_RESULTS
    shown = lines[:_MAX_RESULTS]
    output = "\n".join(shown)
    if truncated:
        output += f"\n... ({len(lines) - _MAX_RESULTS} more not shown — narrow the query or pass --root to see more)"
    return output


def _main() -> None:
    parser = argparse.ArgumentParser(description="Search the whole filesystem by filename or content")
    subparsers = parser.add_subparsers(dest="action", required=True)

    find_parser = subparsers.add_parser("find")
    find_parser.add_argument("pattern")
    find_parser.add_argument("--root", default=_FIND_DEFAULT_ROOT)

    grep_parser = subparsers.add_parser("grep")
    grep_parser.add_argument("query")
    grep_parser.add_argument("--root", default=_GREP_DEFAULT_ROOT)

    args = parser.parse_args()
    try:
        if args.action == "find":
            print(find_by_name(args.pattern, args.root))
        elif args.action == "grep":
            print(search_content(args.query, args.root))
    except FileSearchError as error:
        print(f"Error: {error}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    _main()
