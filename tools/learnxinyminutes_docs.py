"""learnxinyminutes_docs.py — pulls real syntax reference straight from the
learnxinyminutes-docs repo (github.com/adambard/learnxinyminutes-docs) for
whatever language the user names, so Florinda can hand over a genuine
boilerplate/syntax pattern to learn from instead of inventing one from
training data — same "verify against the real source" principle as
qiskit_docs.py, applied to "how does language X spell a function/loop/class"
instead of "what does this installed Python package actually contain."

WHY this is a teaching aid, not a solve-it-for-you tool: if the user wants to
know how to write a function in a language (e.g. to then go write their own
bread-baking function), the right answer is learnxinyminutes' own generic
example of a function in that language — never a made-up bread-baking
implementation. This module only ever returns what the real repo page
actually says; it has no path that invents or completes the user's own task
for them.

WHY chunk+score instead of a real per-language parser: learnxinyminutes-docs
covers 200+ languages with no shared section-header convention and wildly
different comment syntax (#, //, --, ;, %, REM, ...) — a hand-written parser
per language doesn't scale and would silently rot as pages are edited
upstream. Every page's fenced code IS reliably written as short comment+example
groups separated by a blank line (verified live across python.md, rust.md,
javascript.md, go.md, c.md, haskell.md), so splitting on blank lines recovers
roughly one teaching point per chunk without needing to know anything
language-specific. Matches are then ranked by how much the keyword shows up
in comment lines specifically (weighted higher than a keyword that only
happens to appear inside example code).

WHY cached locally: this hits GitHub's raw content host and (for the file
list) its unauthenticated REST API, which is rate-limited per IP — repeat
lookups of the same language/keyword within a work session shouldn't cost a
new request every time, and this content changes rarely enough that a
multi-day cache is safe.
"""
import json
import re
import subprocess
import sys
import time
from difflib import get_close_matches
from pathlib import Path

import requests

RAW_BASE = "https://raw.githubusercontent.com/adambard/learnxinyminutes-docs/master"
REPO_URL = "https://github.com/adambard/learnxinyminutes-docs/blob/master"
TREE_URL = "https://api.github.com/repos/adambard/learnxinyminutes-docs/git/trees/master?recursive=1"
CACHE_DIR = Path.home() / ".local/share/flora-ai/learnxinyminutes-cache"
TREE_CACHE_PATH = CACHE_DIR / "_tree.json"
CACHE_TTL_S = 7 * 24 * 3600
REQUEST_TIMEOUT_S = 15
_HEADERS = {"User-Agent": "flora-ai-learnxinyminutes-docs"}

# WHY excluded explicitly rather than filtered by some naming rule: these are
# the only two root-level .md files in the repo that are NOT a language page
# (verified live against the real file tree) — everything else at root level
# genuinely is one.
_NON_LANGUAGE_PAGES = {"readme", "contributing"}

# WHY a manual alias table instead of relying only on fuzzy-matching real
# filenames: the repo's own slugs are sometimes not what anyone would
# actually type ("cpp" for "c++.md", "csharp" for "c#"-style naming) — fuzzy
# matching alone can't bridge those, they need an exact mapping.
_ALIASES = {
    "js": "javascript", "node": "javascript", "nodejs": "javascript",
    "ts": "typescript",
    "py": "python", "python2": "pythonlegacy",
    "golang": "go",
    "cpp": "c++", "cplusplus": "c++",
    "cs": "csharp", "c#": "csharp",
    "rb": "ruby",
    "rs": "rust",
    "kt": "kotlin",
    "sh": "bash", "shell": "bash",
    "objc": "objective-c",
    "ps": "powershell", "ps1": "powershell",
    "vb": "visualbasic",
}

_COMMENT_MARKERS = ("#", "//", "--", ";", "%", "*", "'", '"""', "REM ", "<!--")
_MAX_CHUNK_LINES = 25


class LearnXInYMinutesError(Exception):
    """Raised when a language can't be resolved, or its page can't be fetched."""


def _load_language_slugs() -> list[str]:
    """Returns every real, root-level language page's slug (filename minus
    .md) from the repo's actual file tree — root level only, since
    subdirectories (e.g. fr/python.md) are translations of the same page."""
    if TREE_CACHE_PATH.exists() and time.time() - TREE_CACHE_PATH.stat().st_mtime < CACHE_TTL_S:
        return json.loads(TREE_CACHE_PATH.read_text())

    try:
        response = requests.get(TREE_URL, headers=_HEADERS, timeout=REQUEST_TIMEOUT_S)
        response.raise_for_status()
    except requests.RequestException as error:
        if TREE_CACHE_PATH.exists():  # a stale list beats no list at all
            return json.loads(TREE_CACHE_PATH.read_text())
        raise LearnXInYMinutesError(
            f"failed to fetch learnxinyminutes-docs' file list: {error}"
        ) from error

    slugs = sorted(
        path[:-3] for entry in response.json().get("tree", [])
        if (path := entry["path"]).endswith(".md") and "/" not in path
        and path[:-3].lower() not in _NON_LANGUAGE_PAGES
    )
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    TREE_CACHE_PATH.write_text(json.dumps(slugs))
    return slugs


def resolve_language(name: str) -> str:
    """Resolves a user-given language name (e.g. "js", "JavaScript", "c++")
    to its real slug in the repo. Raises with close-match suggestions rather
    than silently guessing a wrong page if nothing matches."""
    slugs = _load_language_slugs()
    normalized = re.sub(r"\s+", "", name.strip().lower())
    normalized = _ALIASES.get(normalized, normalized)
    if normalized in slugs:
        return normalized

    suggestions = get_close_matches(normalized, slugs, n=5, cutoff=0.5)
    if not suggestions:
        suggestions = [slug for slug in slugs if normalized in slug][:5]
    hint = f" Did you mean: {', '.join(suggestions)}?" if suggestions else ""
    raise LearnXInYMinutesError(f"no learnxinyminutes page found for {name!r}.{hint}")


def _page_cache_path(slug: str) -> Path:
    return CACHE_DIR / f"{slug}.md"


def fetch_page(slug: str) -> str:
    """Returns the real, raw markdown for slug's page, cached locally for
    CACHE_TTL_S so repeated lookups don't refetch it every time."""
    cache_path = _page_cache_path(slug)
    if cache_path.exists() and time.time() - cache_path.stat().st_mtime < CACHE_TTL_S:
        return cache_path.read_text()

    try:
        response = requests.get(f"{RAW_BASE}/{slug}.md", headers=_HEADERS, timeout=REQUEST_TIMEOUT_S)
        response.raise_for_status()
    except requests.RequestException as error:
        if cache_path.exists():  # a stale copy beats no copy at all
            return cache_path.read_text()
        raise LearnXInYMinutesError(f"failed to fetch {slug}.md: {error}") from error

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(response.text)
    return response.text


def _code_blocks(markdown: str) -> list[str]:
    """Returns each fenced ``` code block's body, in order. A line only ever
    toggles the fence if IT ITSELF starts with ``` — a doc-comment that
    happens to contain ``` mid-line (e.g. Rust's `/// \\`\\`\\``) never does,
    since that line starts with `///`, not backticks."""
    blocks: list[str] = []
    current: list[str] = []
    in_fence = False
    for line in markdown.splitlines():
        if line.startswith("```"):
            if in_fence:
                blocks.append("\n".join(current))
                current = []
            in_fence = not in_fence
            continue
        if in_fence:
            current.append(line)
    return blocks


def _chunks(markdown: str) -> list[str]:
    """Splits every fenced code block on blank lines into topic-sized
    pieces — see module docstring for why this, not a real parser."""
    pieces: list[str] = []
    for block in _code_blocks(markdown):
        current: list[str] = []
        for line in block.splitlines():
            if line.strip() == "":
                if current:
                    pieces.append("\n".join(current))
                    current = []
            else:
                current.append(line)
        if current:
            pieces.append("\n".join(current))
    return pieces


def _is_comment_line(line: str) -> bool:
    return line.strip().startswith(_COMMENT_MARKERS)


def _score(chunk: str, keyword: str) -> int:
    """Counts DISTINCT matching lines, capped at 2 per category, rather than
    raw occurrence count — otherwise a long prose chunk that just happens to
    repeat the keyword many times (e.g. a paragraph explaining decorators
    that says "function" five times) would always outrank a short, on-topic
    example that says it once (e.g. "# Use def to create new functions").

    A match only counts if it's not glued to a preceding identifier
    character (`(?<!\\w)`) — otherwise searching "function" would count
    `log_function`/`my_function` as hits just because the substring happens
    to appear inside an unrelated variable name."""
    pattern = re.compile(r"(?<!\w)" + re.escape(keyword.lower()))
    comment_hits = code_hits = 0
    for line in chunk.splitlines():
        if not pattern.search(line.lower()):
            continue
        if _is_comment_line(line):
            comment_hits += 1
        else:
            code_hits += 1
    return min(comment_hits, 2) * 3 + min(code_hits, 2)


def _ranked_matches(slug: str, keyword: str) -> list[str]:
    """Every chunk containing `keyword` at least once, best match first.
    Deterministic for a given (slug, keyword) pair, so `read_match` can
    reproduce the same ranking to fetch one full chunk by number without
    needing to remember a prior `search` call's output. Ties break by
    original position in the page — these docs are written in roughly
    simplest-first order, so the earlier of two equally-scored matches is
    usually the more fundamental/canonical one."""
    markdown = fetch_page(slug)
    scored = [(chunk, _score(chunk, keyword)) for chunk in _chunks(markdown)]
    scored = [(index, chunk, score) for index, (chunk, score) in enumerate(scored) if score > 0]
    scored.sort(key=lambda item: (-item[2], item[0]))
    return [chunk for _, chunk, _ in scored]


def search(slug: str, keyword: str, max_results: int = 8) -> list[tuple[int, str]]:
    """Returns [(match_number, preview), ...], best match first. `preview` is
    the chunk's first comment line (or its first line, if it has none)."""
    results = []
    for index, chunk in enumerate(_ranked_matches(slug, keyword)[:max_results], start=1):
        lines = chunk.splitlines()
        preview = next((line.strip() for line in lines if _is_comment_line(line)), lines[0].strip())
        results.append((index, preview))
    return results


def read_match(slug: str, keyword: str, match_number: int) -> str:
    """Returns the full text of the Nth-ranked chunk for (slug, keyword) —
    N as reported by `search`'s match_number for that same pair."""
    matches = _ranked_matches(slug, keyword)
    if not (1 <= match_number <= len(matches)):
        raise LearnXInYMinutesError(
            f"no match #{match_number} for {keyword!r} in {slug} "
            f"({len(matches)} match(es) found — run search first)"
        )
    lines = matches[match_number - 1].splitlines()
    if len(lines) > _MAX_CHUNK_LINES:
        omitted = len(lines) - _MAX_CHUNK_LINES
        lines = lines[:_MAX_CHUNK_LINES] + [f"... ({omitted} more line(s) truncated)"]
    return "\n".join(lines)


def open_page(slug: str) -> str:
    """Opens the real page on GitHub in the user's browser (nicely rendered,
    the whole cheatsheet at once) and returns its URL."""
    url = f"{REPO_URL}/{slug}.md"
    subprocess.Popen(["xdg-open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return url


def list_languages(filter_text: str | None = None) -> list[str]:
    slugs = _load_language_slugs()
    if filter_text:
        filter_text = filter_text.strip().lower()
        slugs = [slug for slug in slugs if filter_text in slug]
    return slugs


def _main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Real syntax reference from learnxinyminutes-docs, for any language it covers"
    )
    subparsers = parser.add_subparsers(dest="action", required=True)

    languages_parser = subparsers.add_parser("languages")
    languages_parser.add_argument("filter", nargs="?", default=None)

    search_parser = subparsers.add_parser("search")
    search_parser.add_argument("language")
    search_parser.add_argument("keyword", nargs="+")

    read_parser = subparsers.add_parser("read")
    read_parser.add_argument("language")
    read_parser.add_argument("match_number", type=int)
    read_parser.add_argument("keyword", nargs="+")

    open_parser = subparsers.add_parser("open")
    open_parser.add_argument("language")

    args = parser.parse_args()

    try:
        if args.action == "languages":
            slugs = list_languages(args.filter)
            print("\n".join(slugs) if slugs else "No matching languages found.")
            return

        if args.action == "search":
            slug = resolve_language(args.language)
            keyword = " ".join(args.keyword)
            results = search(slug, keyword)
            if not results:
                print(f"No matches for {keyword!r} in {slug}.")
                return
            print(f"# {slug} — matches for {keyword!r}")
            for number, preview in results:
                print(f"{number}. {preview}")
            return

        if args.action == "read":
            slug = resolve_language(args.language)
            keyword = " ".join(args.keyword)
            print(read_match(slug, keyword, args.match_number))
            return

        if args.action == "open":
            slug = resolve_language(args.language)
            url = open_page(slug)
            print(f"Opened {url}")
            return
    except LearnXInYMinutesError as error:
        print(f"Error: {error}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    _main()
