"""accuracy_verifier.py — independently fact-checks Florinda's research-backed
answers against freshly retrieved sources and reports back a percentage.

WHY independent, not self-graded: asking the SAME model that answered a
question whether its own answer was accurate is not a real check — it's
just another sample from the same source of error. This runs the judgment
through a DIFFERENT backend (Claude via the real Anthropic API — Florinda's
default answering path is Gemini, see processor.py) against sources fetched
by a FRESH search of the ORIGINAL question, never Florinda's own search
results, which she already saw and could already be paraphrasing badly or
building on selectively. Known gap: if the user has set FLORA_AI_PROVIDER=
anthropic, the judge and the answerer are the same provider — a distinct
second opinion still, but a weaker independence claim than the Gemini-default
case. Not worth a second judge-provider selection just for that edge case.

WHY it only fires on turns that actually used Web/Academic Search: nothing
here can independently verify a purely conversational or command-execution
reply — there are no sources to check it against. Attempting to verify
those anyway would produce a fabricated-looking percentage with nothing
real behind it.

WHY the caller hands over THIS TURN's real (command, output) pairs instead
of this module re-deriving them: the actual web_search.py/academic_search.py
call usually happens on an EARLIER leg of a recursive turn (search first,
RECURSIVE: Y, then a follow-up leg speaks from the results) — the FINAL
ParsedInstruction alone (what flora_daemon.py's _remember_turn sees) rarely
still carries that command. flora_daemon.py already tracks every command run
during a turn in _recent_commands; it just needs to hand this module the
slice belonging to the current turn.

WHY a background thread, not blocking the turn: a real search plus a real
Claude call both take real seconds, and the caller runs on the critical path
of a live voice interaction — reporting the result later (the same on_report
pattern TaskWatcher and QuantumWatcher already use) is consistent with how
every other "found out something after the fact" report already works.
"""
import json
import logging
import re
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Optional

# WHY tools/ is added to sys.path here: academic_search.py's own module-level
# import is `from web_search import ...` (verified live by reading its
# source), not `from tools.web_search import ...` — it assumes tools/ itself
# is on sys.path, which is only true when it's run standalone as
# `python3 tools/academic_search.py` (every AI COMMAND invocation of it).
# Importing it as a package submodule from outside tools/ (as this module
# does) needs that same assumption satisfied by hand first.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from tools.web_search import search as web_search, WebSearchError  # noqa: E402
from tools.academic_search import search_papers  # noqa: E402
from backends import anthropic_backend  # noqa: E402

logger = logging.getLogger(__name__)

_RESEARCH_COMMAND_RE = re.compile(r"tools/(web_search|academic_search)\.py")
_DEFAULT_LOG_PATH = Path.home() / ".local/share/flora-ai/accuracy_log.jsonl"
_REPORT_RETRY_ATTEMPTS = 3
_REPORT_RETRY_DELAY_S = 20
_SOURCE_SNIPPET_MAX_CHARS = 300

_JUDGE_SYSTEM_PROMPT = """You are an independent fact-checking judge. You will be given a question, an \
answer someone else already gave, and a set of sources for that same question retrieved just now, \
independently of that answer.

Score how well the answer's claims are actually supported by the sources — not whether you personally \
believe the claims are true from your own training. If the sources simply don't cover something the \
answer said, that specific claim counts as unsupported, not automatically wrong.

Respond with ONLY a single JSON object and nothing else, no markdown fencing, no commentary:
{"accuracy_pct": <integer 0-100>, "verdict": "<one short sentence>", "unsupported_claims": ["<claim>", ...]}
"""

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


class AccuracyVerifier:
    """Fact-checks a research-backed turn against a fresh, independent search."""

    def __init__(
        self,
        on_report: Callable[[str], bool],
        anthropic_api_key: Optional[str],
        anthropic_model: str,
        web_search_host: str,
        log_path: Path = _DEFAULT_LOG_PATH,
    ) -> None:
        self._on_report = on_report
        self._api_key = anthropic_api_key
        self._model = anthropic_model
        self._web_search_host = web_search_host
        self._log_path = log_path

    def check_async(self, user_input: str, answer: str, commands: list[tuple[str, str]]) -> None:
        """Fire-and-forget: `commands` is the (command, output) pairs run during
        THIS turn only (see module docstring) — not the whole recent-actions
        window, which can span multiple unrelated turns."""
        if not self._api_key:
            return  # not configured — main() already logs this once at startup
        command = _find_research_command(commands)
        if command is None:
            return
        threading.Thread(
            target=self._verify, args=(user_input, answer, command), daemon=True
        ).start()

    def _verify(self, user_input: str, answer: str, command: str) -> None:
        try:
            sources = self._fetch_sources(user_input, command)
        except WebSearchError:
            logger.exception("accuracy check: independent search failed")
            return
        if not sources:
            logger.info("accuracy check: independent search returned nothing, skipping")
            return
        try:
            reply = "".join(
                anthropic_backend.stream(
                    _build_judge_prompt(user_input, answer, sources),
                    _JUDGE_SYSTEM_PROMPT,
                    self._model,
                    self._api_key,
                )
            )
        except anthropic_backend.AnthropicBackendError:
            logger.exception("accuracy check: judge call failed")
            return
        verdict = _parse_verdict(reply)
        if verdict is None:
            return
        self._log(user_input, answer, sources, verdict)
        self._deliver(_compose_report(verdict))

    def _fetch_sources(self, user_input: str, command: str) -> list[dict]:
        if "academic_search.py" in command:
            papers = search_papers(user_input, host=self._web_search_host)
            return [
                {"title": p["title"], "url": p["url"], "snippet": p["abstract"]}
                for p in papers
            ]
        return web_search(user_input, host=self._web_search_host)

    def _deliver(self, report: str) -> None:
        for _ in range(_REPORT_RETRY_ATTEMPTS):
            if self._on_report(report):
                return
            time.sleep(_REPORT_RETRY_DELAY_S)
        logger.info("accuracy report never delivered (user never went idle): %r", report)

    def _log(self, user_input: str, answer: str, sources: list[dict], verdict: dict) -> None:
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "timestamp": time.time(),
            "question": user_input,
            "answer": answer,
            "sources": [s["url"] for s in sources if s.get("url")],
            **verdict,
        }
        with open(self._log_path, "a") as log_file:
            log_file.write(json.dumps(entry) + "\n")


def _find_research_command(commands: list[tuple[str, str]]) -> Optional[str]:
    for command, _output in commands:
        if _RESEARCH_COMMAND_RE.search(command):
            return command
    return None


def _build_judge_prompt(user_input: str, answer: str, sources: list[dict]) -> str:
    lines = []
    for i, source in enumerate(sources, start=1):
        snippet = (source.get("snippet") or "")[:_SOURCE_SNIPPET_MAX_CHARS]
        lines.append(f"{i}. {source.get('title', '')} — {source.get('url', '')}\n   {snippet}")
    sources_block = "\n".join(lines)
    return (
        f"QUESTION: {user_input}\n\n"
        f"ANSWER GIVEN: {answer}\n\n"
        f"SOURCES (freshly retrieved just now, independent of the answer above):\n{sources_block}"
    )


def _parse_verdict(reply: str) -> Optional[dict]:
    match = _JSON_RE.search(reply)
    if match is None:
        logger.warning("accuracy check: judge reply had no JSON: %r", reply[:200])
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        logger.warning("accuracy check: judge reply had malformed JSON: %r", reply[:200])
        return None
    pct = data.get("accuracy_pct")
    if not isinstance(pct, (int, float)) or not (0 <= pct <= 100):
        logger.warning("accuracy check: judge reply had no valid accuracy_pct: %r", data)
        return None
    return {
        "accuracy_pct": int(pct),
        "verdict": data.get("verdict", ""),
        "unsupported_claims": data.get("unsupported_claims", []),
    }


def _compose_report(verdict: dict) -> str:
    report = f"Independent check on my last research answer: {verdict['accuracy_pct']}% supported by sources."
    if verdict["verdict"]:
        report += f" {verdict['verdict']}"
    return report


if __name__ == "__main__":
    assert _find_research_command([("ls -la", "...")]) is None
    assert _find_research_command(
        [("python3 tools/web_search.py search foo", "...")]
    ) == "python3 tools/web_search.py search foo"
    assert _find_research_command(
        [("ls", "..."), ("python3 tools/academic_search.py search bar", "...")]
    ) is not None
    print("OK: _find_research_command only matches real research commands")

    clean = '{"accuracy_pct": 82, "verdict": "Mostly right.", "unsupported_claims": ["a stray claim"]}'
    assert _parse_verdict(clean) == {
        "accuracy_pct": 82, "verdict": "Mostly right.", "unsupported_claims": ["a stray claim"]
    }
    print("OK: clean JSON verdict parses")

    wrapped = 'Sure, here you go:\n```json\n{"accuracy_pct": 55, "verdict": "Partial.", "unsupported_claims": []}\n```'
    parsed = _parse_verdict(wrapped)
    assert parsed is not None and parsed["accuracy_pct"] == 55
    print("OK: JSON wrapped in prose/markdown still parses")

    assert _parse_verdict("not json at all") is None
    print("OK: non-JSON reply is rejected, not guessed at")

    assert _parse_verdict('{"accuracy_pct": 150, "verdict": "x", "unsupported_claims": []}') is None
    print("OK: an out-of-range accuracy_pct is rejected, not clamped/guessed")

    report = _compose_report({"accuracy_pct": 70, "verdict": "Mostly grounded."})
    assert report == "Independent check on my last research answer: 70% supported by sources. Mostly grounded."
    print("OK: report composes cleanly")

    print("AccuracyVerifier self-check OK")
