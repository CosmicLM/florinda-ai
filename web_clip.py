"""web_clip.py — fetches a URL and saves its content as a markdown file, so
Florinda can actually do "save this page as a note" instead of hallucinating it.

WHY this exists: observed live — asked to convert a page to markdown, Florinda
claimed success across three separate turns ("I am fetching...", "I have
successfully converted...") without ever actually fetching or writing
anything; the conversation history then kept reinforcing its own fabricated
claim. There was simply no tool that could do this — now there is one.

WHY a separate clippings folder, not the quantum-knowledge-base vault: that
vault is explicitly read-only (a deliberate, earlier decision) — this writes
fetched pages into their own folder instead, so the vault's guarantee stays
intact. Nothing in this module ever touches the vault.
"""
import re
import sys
from pathlib import Path
from typing import Optional

import html2text
import requests

CLIPPINGS_DIR = Path.home() / "Documents/Research/flora-clippings"


class WebClipError(Exception):
    """Raised when a page can't be fetched or saved."""


def clip(url: str, title: Optional[str] = None) -> Path:
    try:
        response = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
        response.raise_for_status()
    except requests.RequestException as error:
        raise WebClipError(f"failed to fetch {url}: {error}") from error

    converter = html2text.HTML2Text()
    converter.ignore_images = False
    converter.body_width = 0  # don't hard-wrap lines mid-sentence
    markdown = converter.handle(response.text)

    heading = title or url
    slug = _slugify(heading)
    CLIPPINGS_DIR.mkdir(parents=True, exist_ok=True)
    path = CLIPPINGS_DIR / f"{slug}.md"
    path.write_text(f"# {heading}\n\nSource: {url}\n\n---\n\n{markdown}")
    return path


def _slugify(text: str) -> str:
    slug = re.sub(r"[^\w\s-]", "", text).strip().lower()
    slug = re.sub(r"[\s_-]+", "-", slug)
    return slug[:80] or "clip"


def _main() -> None:
    if len(sys.argv) < 3 or sys.argv[1] != "clip":
        print("usage: web_clip.py clip <url> [title words...]", file=sys.stderr)
        sys.exit(1)
    url = sys.argv[2]
    title = " ".join(sys.argv[3:]) or None
    try:
        path = clip(url, title)
    except WebClipError as error:
        print(f"Error: {error}", file=sys.stderr)
        sys.exit(1)
    print(f"Saved to {path}")


if __name__ == "__main__":
    _main()
