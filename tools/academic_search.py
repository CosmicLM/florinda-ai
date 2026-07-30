"""academic_search.py — searches real academic literature (arXiv, Crossref,
Google Scholar, OpenAlex, PubMed, Semantic Scholar) via flora-ai's own SearXNG
container, instead of the general web.

WHY reuse web_search.py's SearXNG container rather than a new API/key: SearXNG
already ships these exact engines under its "scientific publications" category
(verified live via /config) — arxiv, crossref, google scholar, openalex,
pubmed, semantic scholar. Standing up a separate API (e.g. Semantic Scholar's
own REST API) would mean a second set of credentials/rate limits and a second
DNS-health story to maintain, for coverage this container already has.

WHY "scientific publications" and not the broader "science" category: science
also pulls in pdbe (protein structures) and openaire datasets/wikispecies —
noise for "find me a paper," not papers themselves. "scientific publications"
is exactly the six engines above, verified live via /config.

WHY authors are truncated in the formatted display: a real result (Google's
"quantum error correction below threshold" paper) came back with a 250+ name
author list — printing all of them would drown out the title/venue/url in
anything meant to be read or spoken back to the user.
"""
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

import requests

from web_search import DEFAULT_HOST, WebSearchError, _ensure_container_dns_fresh

_CATEGORY = "scientific publications"
_MAX_AUTHORS_SHOWN = 3
# WHY: Crossref returns abstracts as real JATS XML (e.g. "<jats:p>...</jats:p>"),
# not plain text like the other five engines — verified live on a real result.
_JATS_TAG_RE = re.compile(r"</?jats:[a-z]+[^>]*>")


def search_papers(query: str, max_results: int = 5, host: str = DEFAULT_HOST) -> list[dict]:
    if urlparse(host).hostname in ("127.0.0.1", "localhost"):
        _ensure_container_dns_fresh()
    try:
        response = requests.get(
            f"{host}/search",
            params={"q": query, "format": "json", "categories": _CATEGORY},
            timeout=15,
        )
        response.raise_for_status()
    except requests.RequestException as error:
        raise WebSearchError(
            f"academic search failed (is the flora-ai-searxng container running?): {error}"
        ) from error
    results = response.json().get("results", [])[:max_results]
    return [_extract(r) for r in results]


def _extract(result: dict) -> dict:
    year = (result.get("publishedDate") or "")[:4]
    return {
        "title": result.get("title", ""),
        "authors": result.get("authors") or [],
        "year": year,
        "venue": result.get("journal") or result.get("publisher") or "",
        "doi": result.get("doi") or "",
        "url": result.get("pdf_url") or result.get("url") or "",
        "abstract": _JATS_TAG_RE.sub("", result.get("content") or "").strip(),
        "source": result.get("engine", ""),
    }


def _format_authors(authors: list[str]) -> str:
    if not authors:
        return ""
    if len(authors) <= _MAX_AUTHORS_SHOWN:
        return ", ".join(authors)
    return ", ".join(authors[:_MAX_AUTHORS_SHOWN]) + " et al."


def _main() -> None:
    import argparse

    # WHY this path fix: run standalone (as every AI COMMAND invocation of
    # this file is), sys.path[0] is this file's own directory (tools/), not
    # the repo root — config.py lives one level up and stayed there when
    # this file moved into tools/.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from config import ConfigVault

    parser = argparse.ArgumentParser(description="Search academic papers via SearXNG")
    subparsers = parser.add_subparsers(dest="action", required=True)
    search_parser = subparsers.add_parser("search")
    search_parser.add_argument("query", nargs="+")

    args = parser.parse_args()
    host = ConfigVault().settings.web_search_host
    try:
        results = search_papers(" ".join(args.query), host=host)
    except WebSearchError as error:
        print(f"Error: {error}", file=sys.stderr)
        sys.exit(1)

    if not results:
        print("No papers found.")
    for r in results:
        byline = _format_authors(r["authors"])
        year = f" ({r['year']})" if r["year"] else ""
        venue = f" — {r['venue']}" if r["venue"] else ""
        print(f"# {r['title']}{year}")
        if byline:
            print(byline)
        if venue.strip(" —"):
            print(venue.strip(" — "))
        print(r["url"])
        if r["doi"]:
            print(f"doi: {r['doi']}")
        if r["abstract"]:
            print(r["abstract"][:400])
        print()


if __name__ == "__main__":
    _main()
