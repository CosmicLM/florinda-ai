"""knowledge_base.py — read-only search over the user's quantum-knowledge-base
Obsidian vault, so Florinda can ground answers/comments in the user's own notes
instead of guessing.

WHY read-only, strictly: this vault is the user's own carefully organized
personal research notes (physics, Qiskit, course notes). Florinda must never
create, edit, or delete anything in it — this module has no write path at
all, by construction, not just by convention.
"""
import subprocess
import sys
from pathlib import Path
from typing import Optional

_MARKDOWN_GLOB = "**/*.md"
_SNIPPET_CONTEXT_CHARS = 160


class KnowledgeBase:
    """Read-only substring search/lookup over a vault of markdown notes."""

    def __init__(self, vault_path: Path) -> None:
        self._vault_path = vault_path

    def search(self, query: str, max_results: int = 5) -> list[tuple[str, str]]:
        """Returns [(note_title, snippet), ...] for notes whose title or
        content contains every word in `query` (case-insensitive)."""
        terms = [t.lower() for t in query.split() if t.strip()]
        if not terms:
            return []
        matches = []
        for path in self._iter_notes():
            try:
                text = path.read_text(errors="ignore")
            except OSError:
                continue
            lowered = text.lower()
            if not all(term in lowered or term in path.stem.lower() for term in terms):
                continue
            matches.append((path.stem, self._snippet(text, terms)))
            if len(matches) >= max_results:
                break
        return matches

    def read_note(self, title: str) -> Optional[str]:
        """Returns the full content of the note matching `title` exactly (by
        filename, case-insensitive), or None if no such note exists."""
        for path in self._iter_notes():
            if path.stem.lower() == title.lower():
                try:
                    return path.read_text(errors="ignore")
                except OSError:
                    return None
        return None

    def open_note(self, title: str) -> None:
        """Opens `title` directly in the Obsidian app via obsidian-cli —
        nicer than a generic xdg-open for something that already lives in an
        Obsidian vault: obsidian-cli resolves the note by vault-relative
        name and opens it with Obsidian's own rendering (links, formatting,
        embedded images intact), rather than dumping raw markdown."""
        subprocess.Popen(
            ["obsidian-cli", "open", title, "-v", self._vault_path.name],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

    def _iter_notes(self):
        if not self._vault_path.exists():
            return
        for path in self._vault_path.glob(_MARKDOWN_GLOB):
            if ".obsidian" in path.parts:
                continue
            yield path

    @staticmethod
    def _snippet(text: str, terms: list[str]) -> str:
        lowered = text.lower()
        idx = next((lowered.find(term) for term in terms if lowered.find(term) != -1), -1)
        if idx == -1:
            idx = 0
        half = _SNIPPET_CONTEXT_CHARS // 2
        start, end = max(0, idx - half), min(len(text), idx + half)
        return text[start:end].strip().replace("\n", " ")


def _main() -> None:
    import argparse

    from config import ConfigVault

    parser = argparse.ArgumentParser(description="Read-only search over the quantum-knowledge-base vault")
    subparsers = parser.add_subparsers(dest="action", required=True)

    search_parser = subparsers.add_parser("search")
    search_parser.add_argument("query", nargs="+")

    read_parser = subparsers.add_parser("read")
    read_parser.add_argument("title", nargs="+")

    open_parser = subparsers.add_parser("open")
    open_parser.add_argument("title", nargs="+")

    args = parser.parse_args()
    vault_path = ConfigVault().settings.knowledge_base_path
    kb = KnowledgeBase(vault_path)

    if args.action == "search":
        results = kb.search(" ".join(args.query))
        if not results:
            print("No matching notes found.")
        for title, snippet in results:
            print(f"# {title}\n...{snippet}...\n")
    elif args.action == "read":
        content = kb.read_note(" ".join(args.title))
        if content is None:
            print("No note with that title found.", file=sys.stderr)
            sys.exit(1)
        print(content)
    elif args.action == "open":
        title = " ".join(args.title)
        kb.open_note(title)
        print(f"Opened {title!r} in Obsidian.")


if __name__ == "__main__":
    _main()
