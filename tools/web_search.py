"""web_search.py — searches the web via flora-ai's own SearXNG container
(see docker-compose.yml), so Florinda can actually look things up instead of
only answering from its training data or the local knowledge base.

WHY SearXNG instead of Gemini's own "google_search" grounding tool: tested
live — every grounded-search request on this API key hits a 429
RESOURCE_EXHAUSTED immediately (plain, non-search calls work fine), which
points at a free-tier restriction with no code-side fix. SearXNG is free,
self-hosted, and has no such quota.

WHY flora-ai runs its own container instead of reusing another project's:
this originally pointed at a SearXNG instance belonging to a separate
project (Odysseus) — meaning web search would silently break if that other
project were ever torn down. docker-compose.yml now defines flora-ai's own
instance (distinct container/port/volume) so this feature's uptime doesn't
depend on anything outside this repo.

WHY the network-change self-healing below: observed live — Docker bakes a
container's /etc/resolv.conf in ONCE, at creation time, and never updates
it afterward. Roam to a new WiFi network (or toggle a VPN) and the
container is silently left with a stale, unreachable nameserver — every
search then returns zero results with no obvious error (the real symptom
was buried in `docker logs` as "Temporary failure in name resolution").
Rather than requiring a human to notice and manually
`docker compose up -d --force-recreate searxng`, every search checks
whether the host's resolver has changed since the container last picked
one up, and recreates it automatically if so — see `_ensure_container_dns_fresh`.
"""
import json
import logging
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import requests

DEFAULT_HOST = "http://127.0.0.1:8092"

_SEARXNG_CONTAINER = "flora-ai-searxng"
_COMPOSE_FILE = Path(__file__).resolve().parent.parent / "docker-compose.yml"
_NETWORK_STATE_PATH = Path.home() / ".local/share/flora-ai/websearch_network_state.json"
_RECREATE_COOLDOWN_S = 30.0  # don't hammer docker on every search if recreation keeps failing
_RECREATE_TIMEOUT_S = 30.0
_HEALTHY_WAIT_S = 20.0  # give the recreated container a chance to pass its healthcheck

logger = logging.getLogger(__name__)


class WebSearchError(Exception):
    """Raised when the search backend can't be reached or returns an error."""


def _current_network_fingerprint() -> str:
    """The host's own resolver config — the exact input Docker bakes into a
    container's /etc/resolv.conf at creation time. Comparing THIS (rather
    than, say, the WiFi SSID or gateway IP) tracks the precise thing that
    goes stale, so it also naturally covers VPN toggling, not just WiFi
    roaming."""
    try:
        lines = Path("/etc/resolv.conf").read_text().splitlines()
    except OSError:
        return "unknown"
    nameservers = sorted(line.split()[1] for line in lines if line.startswith("nameserver"))
    return ",".join(nameservers) or "no-nameservers"


def _read_network_state() -> dict:
    try:
        return json.loads(_NETWORK_STATE_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _write_network_state(state: dict) -> None:
    _NETWORK_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _NETWORK_STATE_PATH.write_text(json.dumps(state))


def _wait_until_healthy(timeout_s: float = _HEALTHY_WAIT_S) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            status = subprocess.run(
                ["docker", "inspect", "--format", "{{.State.Health.Status}}", _SEARXNG_CONTAINER],
                capture_output=True, text=True, timeout=3,
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return
        if status == "healthy":
            return
        time.sleep(1)


def _ensure_container_dns_fresh() -> None:
    """Recreates the SearXNG container if the host's resolver has changed
    since it was last (re)created — see the module docstring for why. A
    cheap fingerprint comparison keeps this a no-op on the common case
    (network unchanged); a cooldown keeps a persistently broken docker from
    adding latency/log noise to every single search."""
    state = _read_network_state()
    current = _current_network_fingerprint()
    if current == state.get("fingerprint"):
        return
    if time.time() - state.get("last_attempt_ts", 0.0) < _RECREATE_COOLDOWN_S:
        return
    logger.info("resolver changed (now %s) — recreating %s to refresh its DNS", current, _SEARXNG_CONTAINER)
    state["last_attempt_ts"] = time.time()
    _write_network_state(state)
    try:
        subprocess.run(
            ["docker", "compose", "-f", str(_COMPOSE_FILE), "up", "-d", "--force-recreate", "searxng"],
            capture_output=True, text=True, timeout=_RECREATE_TIMEOUT_S, check=True,
        )
    except (OSError, subprocess.SubprocessError) as error:
        logger.warning("failed to recreate %s after a network change: %s", _SEARXNG_CONTAINER, error)
        return
    _wait_until_healthy()
    state["fingerprint"] = current
    _write_network_state(state)
    logger.info("%s recreated for the new network", _SEARXNG_CONTAINER)


def search(query: str, max_results: int = 5, host: str = DEFAULT_HOST) -> list[dict]:
    if urlparse(host).hostname in ("127.0.0.1", "localhost"):
        _ensure_container_dns_fresh()
    try:
        response = requests.get(
            f"{host}/search", params={"q": query, "format": "json"}, timeout=15
        )
        response.raise_for_status()
    except requests.RequestException as error:
        raise WebSearchError(
            f"web search failed (is the flora-ai-searxng container running?): {error}"
        ) from error
    results = response.json().get("results", [])[:max_results]
    return [
        {"title": r.get("title", ""), "url": r.get("url", ""), "snippet": r.get("content", "")}
        for r in results
    ]


def _main() -> None:
    import argparse

    # WHY this path fix: run standalone (as every AI COMMAND invocation of
    # this file is), sys.path[0] is this file's own directory (tools/), not
    # the repo root — config.py lives one level up and stayed there when
    # this file moved into tools/.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from config import ConfigVault

    parser = argparse.ArgumentParser(description="Search the web via SearXNG")
    subparsers = parser.add_subparsers(dest="action", required=True)
    search_parser = subparsers.add_parser("search")
    search_parser.add_argument("query", nargs="+")

    args = parser.parse_args()
    host = ConfigVault().settings.web_search_host
    try:
        results = search(" ".join(args.query), host=host)
    except WebSearchError as error:
        print(f"Error: {error}", file=sys.stderr)
        sys.exit(1)

    if not results:
        print("No results found.")
    for result in results:
        print(f"# {result['title']}\n{result['url']}\n{result['snippet']}\n")


if __name__ == "__main__":
    _main()
