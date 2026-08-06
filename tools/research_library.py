"""research_library.py — a writable, searchable store for specific pieces of
information (a paper finding, a verified circuit result, a formula, a fact
worth remembering) that Florinda accumulates across research sessions.

WHY this is separate from knowledge_base.py: that module is a deliberately
READ-ONLY window onto the user's own curated Obsidian vault (see its own
docstring) — Florinda must never write into it. This is the opposite: a
store Florinda DOES own and write into, for things it finds or verifies
mid-session rather than notes the user wrote by hand.

WHY this is separate from web_clip.py: web_clip saves an entire fetched
page verbatim. This saves a short, distilled entry — a specific fact,
snippet, or result, with a title and optional tags — regardless of where it
came from (a paper, a Qiskit run, a conversation), searched the same way as
knowledge_base.py rather than archived as one big document.

WHY plain markdown files under their own folder, not a database: every
other persistent thing this project writes (web clippings, saved figures)
is a plain file under ~/Documents/Research/ — readable/greppable/portable
without any of Florinda's own code, and consistent with that convention.

WHY a same-title collision gets a dated variant instead of overwriting:
two real, distinct facts can end up with the same natural title (e.g. two
different "VQE convergence" observations weeks apart) — silently
overwriting one would lose it with no warning.
"""
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

LIBRARY_DIR = Path.home() / "Documents/Research/florinda-library"
_SNIPPET_CONTEXT_CHARS = 160
_TAGS_RE = re.compile(r"^Tags:\s*(.*)$", re.MULTILINE)
_SAVED_RE = re.compile(r"^Saved:\s*(.*)$", re.MULTILINE)


class ResearchLibraryError(Exception):
    """Raised when a requested entry doesn't exist."""


def save(
    title: str, body: str, tags: Optional[list[str]] = None, library_dir: Path = LIBRARY_DIR
) -> Path:
    library_dir.mkdir(parents=True, exist_ok=True)
    slug = _slugify(title)
    path = library_dir / f"{slug}.md"
    suffix = 2
    while path.exists():
        path = library_dir / f"{slug}-{suffix}.md"
        suffix += 1
    tags_csv = ", ".join(tags) if tags else ""
    saved_at = datetime.now().isoformat(timespec="seconds")
    content = f"# {title}\n\nTags: {tags_csv}\nSaved: {saved_at}\n\n---\n\n{body.strip()}\n"
    path.write_text(content)
    return path


def search(
    query: str, tag: Optional[str] = None, max_results: int = 8, library_dir: Path = LIBRARY_DIR
) -> list[tuple[str, str, str]]:
    """Returns [(title, tags_csv, snippet), ...] for entries whose title or
    body contains every word in `query` (case-insensitive), optionally
    restricted to entries carrying `tag`."""
    terms = [t.lower() for t in query.split() if t.strip()]
    matches = []
    for path in _iter_entries(library_dir):
        text = path.read_text(errors="ignore")
        entry_tags = _extract_tags(text)
        if tag and tag.lower() not in [t.lower() for t in entry_tags]:
            continue
        lowered = text.lower()
        if terms and not all(term in lowered for term in terms):
            continue
        matches.append((_extract_title(text, path), ", ".join(entry_tags), _snippet(text, terms)))
        if len(matches) >= max_results:
            break
    return matches


def read_entry(title: str, library_dir: Path = LIBRARY_DIR) -> str:
    for path in _iter_entries(library_dir):
        text = path.read_text(errors="ignore")
        if _extract_title(text, path).lower() == title.lower():
            return text
    raise ResearchLibraryError(f"no entry titled {title!r}")


def read_body(title: str, library_dir: Path = LIBRARY_DIR) -> str:
    """Same as read_entry, but strips the `# title` / `Tags:` / `Saved:`
    header block and returns just the saved content — for callers (e.g.
    flora_daemon.py's preference pull-in) that want the substance to feed
    into a prompt, not save()'s own bookkeeping metadata repeated back."""
    return read_entry(title, library_dir).split("---\n\n", 1)[-1].strip()


def list_entries(tag: Optional[str] = None, library_dir: Path = LIBRARY_DIR) -> list[tuple[str, str, str]]:
    """Returns [(title, tags_csv, saved_at), ...] newest first."""
    entries = []
    for path in _iter_entries(library_dir):
        text = path.read_text(errors="ignore")
        entry_tags = _extract_tags(text)
        if tag and tag.lower() not in [t.lower() for t in entry_tags]:
            continue
        saved_match = _SAVED_RE.search(text)
        entries.append((
            _extract_title(text, path),
            ", ".join(entry_tags),
            saved_match.group(1) if saved_match else "",
            path.stat().st_mtime,
        ))
    entries.sort(key=lambda e: e[3], reverse=True)
    return [(t, tg, s) for t, tg, s, _ in entries]


def _iter_entries(library_dir: Path = LIBRARY_DIR):
    if not library_dir.exists():
        return
    yield from sorted(library_dir.glob("*.md"))


def _extract_title(text: str, path: Path) -> str:
    first_line = text.splitlines()[0] if text else ""
    return first_line.lstrip("#").strip() or path.stem


def _extract_tags(text: str) -> list[str]:
    match = _TAGS_RE.search(text)
    if not match or not match.group(1).strip():
        return []
    return [t.strip() for t in match.group(1).split(",") if t.strip()]


def _snippet(text: str, terms: list[str]) -> str:
    lowered = text.lower()
    idx = next((lowered.find(term) for term in terms if lowered.find(term) != -1), -1)
    if idx == -1:
        idx = 0
    half = _SNIPPET_CONTEXT_CHARS // 2
    start, end = max(0, idx - half), min(len(text), idx + half)
    return text[start:end].strip().replace("\n", " ")


def _slugify(text: str) -> str:
    slug = re.sub(r"[^\w\s-]", "", text).strip().lower()
    slug = re.sub(r"[\s_-]+", "-", slug)
    return slug[:80] or "entry"


def _main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Store and search Florinda's own research library")
    subparsers = parser.add_subparsers(dest="action", required=True)

    save_parser = subparsers.add_parser("save")
    save_parser.add_argument("title", nargs="+")
    save_parser.add_argument("--tags", default="", help="comma-separated tags")

    search_parser = subparsers.add_parser("search")
    search_parser.add_argument("query", nargs="*")
    search_parser.add_argument("--tag", default=None)

    read_parser = subparsers.add_parser("read")
    read_parser.add_argument("title", nargs="+")

    list_parser = subparsers.add_parser("list")
    list_parser.add_argument("--tag", default=None)

    args = parser.parse_args()

    if args.action == "save":
        title = " ".join(args.title)
        body = sys.stdin.read()
        if not body.strip():
            print("Error: no content on stdin to save", file=sys.stderr)
            sys.exit(1)
        tags = [t.strip() for t in args.tags.split(",") if t.strip()]
        path = save(title, body, tags)
        print(f"Saved to {path}")
    elif args.action == "search":
        results = search(" ".join(args.query), tag=args.tag)
        if not results:
            print("No matching entries found.")
        for title, tags_csv, snippet in results:
            tag_suffix = f" [{tags_csv}]" if tags_csv else ""
            print(f"# {title}{tag_suffix}\n...{snippet}...\n")
    elif args.action == "read":
        title = " ".join(args.title)
        try:
            print(read_entry(title))
        except ResearchLibraryError as error:
            print(f"Error: {error}", file=sys.stderr)
            sys.exit(1)
    elif args.action == "list":
        entries = list_entries(tag=args.tag)
        if not entries:
            print("No entries yet.")
        for title, tags_csv, saved_at in entries:
            tag_suffix = f" [{tags_csv}]" if tags_csv else ""
            print(f"{title}{tag_suffix} — saved {saved_at}")


if __name__ == "__main__":
    _main()
