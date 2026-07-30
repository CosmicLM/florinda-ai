"""quantum_watcher.py — watches OCR'd screen text for quantum/Qiskit-related
keywords and, when something relevant appears, asks a local model whether
it's actually worth a spoken comment.

WHY a cheap local keyword pre-filter before ever calling a model: most screen
ticks have nothing quantum-related on them at all — a short keyword list
match costs nothing, so the (still-cheap, but non-zero) local model call only
ever runs when there's a real candidate, matching the existing screen_watcher
philosophy of not wasting compute on screens with nothing worth reacting to.

WHY the model can still say "no comment" after a keyword match: the keyword
list is a blunt trigger (a word like "quantum" could appear in something
completely uninteresting) — the actual judgment call of whether this is worth
interrupting the user's focus is left to the model, not the keyword match
itself.

WHY this got "more seasoned" (observed live: it once saw "Qiskit" and
remarked on what Qiskit IS — a textbook definition the user obviously
already knows, not an interesting observation): three things were missing
that a real, seasoned collaborator would have — (1) knowledge of the user's
own project, Anecho, to connect what's on screen to actual ongoing work
instead of speaking in the abstract; (2) more than the single triggering
screen snapshot, so a comment reflects the arc of what's being worked on,
not an isolated keyword hit; (3) memory of what it already said, so it
doesn't repeat the same observation and can build on a earlier one. All
three are threaded into the system prompt and _ask_for_comment below.
"""
import json
import logging
import re
import time
from collections import deque
from pathlib import Path
from typing import Callable, Optional

from backends.local_brain import LocalBrain, LocalBrainError

logger = logging.getLogger(__name__)

NO_COMMENT = "NO_COMMENT"

_ANECHO_PATH = Path.home() / "Projects/anecho"
_HISTORY_PATH = Path.home() / ".local/share/flora-ai/quantum_watch_history.json"
_MAX_HISTORY = 5
_SCREEN_CONTEXT_WINDOW = 3  # rolling snapshots kept, built on EVERY tick — not just keyword matches
_MAX_COMMENT_CHARS = 220  # a "one short sentence" the local model doesn't always self-enforce

# WHY a second, stricter deterministic filter before the model is ever asked
# to judge "is there real evidence": verified live — asked to only comment
# given concrete evidence, phi4-mini would sometimes still comply with the
# LETTER of that instruction by fabricating a placeholder, e.g. inventing
# `{"000": X}` counts that appear nowhere on screen, rather than correctly
# recognizing there was nothing to react to. A 3.8B local model can't be
# trusted to reliably self-gate on "is this real" — so that gate is made
# deterministic instead: the model is never even asked to comment unless one
# of these patterns is ALREADY present, verbatim, in the current screen
# text. This can't eliminate every possible hallucination once the model IS
# invoked, but it eliminates the entire class of "commented on a plain
# screen with nothing on it at all" fabrications outright.
_EVIDENCE_PATTERNS = [
    re.compile(r"""\{\s*['"]?\w+['"]?\s*:\s*\d+"""),  # a printed counts/results dict
    re.compile(r"\bFake[A-Z]\w*"),  # a named noisy hardware backend, e.g. FakeKyoto
    re.compile(r"\bNoiseModel\b"),
    re.compile(r"\bdepolarizing_error\b"),
    re.compile(r"\bT1\b|\bT2\b"),
    re.compile(r"\bTODO\b|\bFIXME\b"),
    re.compile(r"\bTraceback\b"),
]

# WHY no literal quotable example sentences here (an earlier draft had them):
# verified live against the real local model (phi4-mini) — with concrete
# examples like `"Those 01/10 counts are..."` in the prompt, it would
# literally parrot that exact sentence, numbers and all, on screens that had
# nothing to do with it. Worse than a generic comment: a fabricated specific
# one. Describing the evidence bar in the abstract instead (see below) tested
# far more reliably across repeated live trials.
_NO_COMMENT_RE = re.compile(r"\bno_comment\b", re.IGNORECASE)

# WHY reject-and-drop rather than try to repair: verified live that despite
# an explicit "never read out a dict/JSON structure verbatim" instruction,
# phi4-mini still falls back to a templated `{'00': X}`-style fragment (a
# placeholder OR the real numbers, either way raw Python dict syntax) more
# often than not for this kind of prompt. Reading literal braces/quotes
# aloud via TTS would be worse than saying nothing, and regex-repairing
# arbitrary broken punctuation risks producing new, differently-mangled
# grammar. Silently falling back to no-comment (same as any other case the
# model declines) is the safer choice — this class of thing happens rarely
# enough across a whole coding session that an occasional dropped comment
# costs nothing.
_RAW_DICT_RE = re.compile(r"\{[^{}\n]{0,120}\}")

_COMMENT_SYSTEM_PROMPT = """You are Florinda, a voice assistant glancing at the user's screen in the \
background while they work on quantum computing (Qiskit, Python, physics research). You're a seasoned \
collaborator who knows their work well by now, not a newcomer reacting to a word appearing on screen.

{anecho_context}

Only speak up if the CURRENT screen text below contains actual concrete evidence worth discussing — \
printed measurement counts that diverge from an ideal/expected distribution, an explicit noise model or \
error-rate parameter, a named noisy hardware backend (like FakeKyoto), or a visible bug/TODO. If none of \
those appear literally in the text below, you have nothing to say.

Never state a generic definition of a tool the user already knows (e.g. "Qiskit is a quantum computing \
framework" — the user already knows). Never invent noise, errors, or specific numbers that are not \
literally present in the text below. When it's a genuine, concrete fit, connect what you see to a \
specific piece of Anecho (name the piece and why) rather than a vague "you should use this in Anecho."

Describe what you see in natural spoken language — never read out a Python dict/JSON structure verbatim \
(no curly braces, quotes, or colons) and never use a placeholder like "X" for a number you don't have; \
describe a discrepancy qualitatively (e.g. "the counts leaned heavily toward two outcomes instead of an \
even split") rather than trying to recite exact digits.
{history_context}
If it is NOT worth commenting, reply with EXACTLY: {no_comment}
If — and only if — you have something genuinely worth saying, reply with ONLY the one-sentence spoken \
remark itself, referring only to specifics literally present in the text below. No preamble, no quotes, \
no explanation of your reasoning, and don't repeat a comment you've already made recently (see above)."""


class QuantumWatcher:
    """Detects quantum-related keywords in screen text and decides whether to comment."""

    def __init__(
        self,
        brain: LocalBrain,
        keywords: list[str],
        cooldown_s: float,
        on_comment: Callable[[str], None],
        anecho_path: Path = _ANECHO_PATH,
        history_path: Path = _HISTORY_PATH,
    ) -> None:
        self._brain = brain
        self._keywords = [k.lower() for k in keywords]
        self._cooldown_s = cooldown_s
        self._on_comment = on_comment
        self._last_comment_at = 0.0
        self._anecho_context = _load_anecho_context(anecho_path)
        self._history_path = history_path
        self._recent_screens: deque[str] = deque(maxlen=_SCREEN_CONTEXT_WINDOW)

    def observe(self, screen_text: str) -> None:
        """Call once per fresh screen-watch tick with the newly OCR'd text."""
        if not screen_text:
            return
        self._recent_screens.append(screen_text[:500])
        matched = self._match_keywords(screen_text)
        if not matched:
            return
        if not _has_concrete_evidence(screen_text):
            return
        if time.monotonic() - self._last_comment_at < self._cooldown_s:
            return
        try:
            reply = self._ask_for_comment(matched, screen_text)
        except LocalBrainError:
            logger.exception("local brain call failed during quantum-watch")
            return
        comment = _extract_spoken_comment(reply)
        if comment:
            self._last_comment_at = time.monotonic()
            self._remember_comment(comment)
            self._on_comment(comment)

    def _match_keywords(self, text: str) -> list[str]:
        lowered = text.lower()
        return [keyword for keyword in self._keywords if keyword in lowered]

    def _ask_for_comment(self, matched: list[str], screen_text: str) -> str:
        prior_screens = list(self._recent_screens)[:-1]  # everything before the triggering tick
        prompt_parts = [f"Matched keyword(s): {', '.join(matched)}\n"]
        if prior_screens:
            joined = "\n---\n".join(prior_screens)[-1500:]
            prompt_parts.append(f"What was on screen just before this (older to newer):\n{joined}\n")
        prompt_parts.append(f"Current OCR'd screen text (may be noisy):\n{screen_text[:2000]}")
        system = _COMMENT_SYSTEM_PROMPT.format(
            anecho_context=self._anecho_context,
            history_context=self._format_history(),
            no_comment=NO_COMMENT,
        )
        return self._brain.generate("\n".join(prompt_parts), system=system)

    def _format_history(self) -> str:
        history = self._read_history()
        if not history:
            return ""
        lines = "\n".join(f'- "{entry["comment"]}"' for entry in history)
        return f"\nYour own recent quantum-watch comments (build on these, don't just repeat them):\n{lines}\n"

    def _read_history(self) -> list[dict]:
        try:
            return json.loads(self._history_path.read_text())
        except (OSError, json.JSONDecodeError):
            return []

    def _remember_comment(self, comment: str) -> None:
        history = self._read_history()
        history.append({"comment": comment, "at": time.time()})
        history = history[-_MAX_HISTORY:]
        self._history_path.parent.mkdir(parents=True, exist_ok=True)
        self._history_path.write_text(json.dumps(history))


def _load_anecho_context(anecho_path: Path) -> str:
    """Grounds Anecho suggestions in the REAL project instead of letting the
    model invent plausible-sounding features. Reads the project's own README
    (and, if present, its most recently touched experiment file, for
    concrete "what they're actually building right now" detail beyond the
    README's abstract roadmap) once per process — so it stays current as
    Anecho evolves without re-reading it on every single screen tick."""
    fallback = (
        "The user is building Anecho, their own quantum error-mitigation project — its README could "
        "not be read here, so don't invent specifics about it; only comment on what's literally on screen."
    )
    try:
        readme_text = (anecho_path / "README.md").read_text().strip()
    except OSError:
        return fallback
    parts = [f"The user is building Anecho, their own project:\n{readme_text[:1200]}"]
    try:
        newest = max(
            (p for p in (anecho_path / "experiments").glob("*.py") if p.is_file()),
            key=lambda p: p.stat().st_mtime,
            default=None,
        )
        if newest is not None:
            parts.append(f"Their most recently touched Anecho experiment ({newest.name}):\n{newest.read_text()[:1000]}")
    except OSError:
        pass
    return "\n\n".join(parts)


def _has_concrete_evidence(screen_text: str) -> bool:
    """Whether the CURRENT screen text already contains something real
    worth reacting to — see _EVIDENCE_PATTERNS above for why this check is
    deterministic rather than left to the model's own judgment."""
    return any(pattern.search(screen_text) for pattern in _EVIDENCE_PATTERNS)


def _extract_spoken_comment(reply: str) -> Optional[str]:
    """Defends against two real, observed local-model failure modes that
    prompt wording alone couldn't fully eliminate (phi4-mini is only 3.8B
    params): (1) leaking its reasoning before or after the NO_COMMENT
    sentinel instead of replying with just the sentinel, and (2) ignoring
    the "one short sentence" instruction and rambling for a paragraph.
    Mirrors this project's existing pattern of pairing a prompt fix with a
    defensive code-level guard (see processor.py's _sanitize_command)."""
    if not reply or not reply.strip():
        return None
    if _NO_COMMENT_RE.search(reply):
        return None  # decided not to comment, however that got phrased
    text = reply.strip().splitlines()[0].strip()
    if _RAW_DICT_RE.search(text):
        return None  # fell back to unspeakable raw dict/JSON syntax — drop it, don't try to repair it
    if len(text) <= _MAX_COMMENT_CHARS:
        return text
    truncated = text[:_MAX_COMMENT_CHARS]
    boundary = max(truncated.rfind(". "), truncated.rfind("! "), truncated.rfind("? "))
    return truncated[: boundary + 1] if boundary > 40 else truncated.rsplit(" ", 1)[0]


if __name__ == "__main__":
    import tempfile

    class _StubBrain:
        def __init__(self, reply: str) -> None:
            self._reply = reply
            self.last_prompt: Optional[str] = None
            self.last_system: Optional[str] = None

        def generate(self, prompt: str, system: Optional[str] = None) -> str:
            self.last_prompt = prompt
            self.last_system = system
            return self._reply

    with tempfile.TemporaryDirectory() as tmp_dir:
        missing_anecho = Path(tmp_dir) / "no_such_anecho"
        history_path = Path(tmp_dir) / "history.json"
        calls: list[str] = []

        watcher = QuantumWatcher(
            _StubBrain("this should never be spoken"), ["quantum", "qubit"], cooldown_s=0.2,
            on_comment=calls.append, anecho_path=missing_anecho, history_path=history_path,
        )
        assert "could not be read" in watcher._anecho_context, watcher._anecho_context
        print("OK: a missing Anecho project falls back instead of letting the model invent specifics")

        watcher.observe("just some regular text about lunch")
        assert calls == [], "should not comment when no keyword matched"

        watcher.observe("today I studied a qubit in superposition")
        assert calls == [], "keyword matched but no concrete evidence present — must not comment"
        assert watcher._brain.last_prompt is None, (
            "the evidence gate must short-circuit BEFORE ever asking the model — otherwise a weak "
            "local model can be talked into fabricating evidence that was never really there"
        )
        print("OK: a keyword match alone, with no concrete evidence on screen, never reaches the model")

        stub1 = _StubBrain(NO_COMMENT)
        watcher1 = QuantumWatcher(
            stub1, ["qubit"], cooldown_s=0.2, on_comment=calls.append,
            anecho_path=missing_anecho, history_path=history_path,
        )
        watcher1.observe('a qubit result: {"00": 512, "11": 488}')
        assert calls == [], "stub brain said NO_COMMENT, should not have called on_comment"
        assert stub1.last_prompt is not None, "concrete evidence should have reached the model this time"
        print("OK: NO_COMMENT is respected once the model is actually asked")

        stub2 = _StubBrain("That's a neat circuit!")
        watcher2 = QuantumWatcher(
            stub2, ["quantum"], cooldown_s=100, on_comment=calls.append,
            anecho_path=missing_anecho, history_path=history_path,
        )
        watcher2.observe("reading about qiskit transpile passes")  # no keyword match — just builds rolling context
        watcher2.observe('building a quantum circuit: {"00": 1801, "11": 1699}')
        assert calls == ["That's a neat circuit!"], calls
        assert "transpile passes" in stub2.last_prompt, "the prior (non-matching) screen tick should still be passed as context"
        print("OK: a real comment fires on_comment, carrying the prior screen tick as context")

        watcher2.observe('still looking at this quantum circuit: {"00": 1801, "11": 1699}')
        assert calls == ["That's a neat circuit!"], "cooldown should have suppressed a second comment"
        print("OK: cooldown suppresses a second comment")

        assert json.loads(history_path.read_text())[-1]["comment"] == "That's a neat circuit!"
        print("OK: a real comment is persisted to on-disk history")

        stub3 = _StubBrain("Building on that circuit, try folding it for ZNE.")
        watcher3 = QuantumWatcher(
            stub3, ["quantum"], cooldown_s=0, on_comment=calls.append,
            anecho_path=missing_anecho, history_path=history_path,
        )
        watcher3.observe('another quantum circuit: {"00": 1801, "11": 1699}')
        assert "neat circuit!" in stub3.last_system, "past comments should be surfaced in the system prompt"
        print("OK: comment history persists and is surfaced across separate watcher instances (service restarts)")

        assert _has_concrete_evidence('counts: {"00": 512, "11": 488}')
        assert _has_concrete_evidence("running against FakeKyoto")
        assert _has_concrete_evidence("noise_model = NoiseModel()")
        assert not _has_concrete_evidence("circuit = QuantumCircuit(3)\ncircuit.h(0)\nprint(counts)")
        print("OK: the evidence pre-filter recognizes real signal and rejects plain code with none")

        assert _extract_spoken_comment(
            "Observed counts: {'00': X} This indicates additional noise."
        ) is None, "raw dict/JSON syntax must be dropped, not spoken aloud or repaired"
        assert _extract_spoken_comment(
            "The counts leaned heavily toward two outcomes instead of an even split."
        ) == "The counts leaned heavily toward two outcomes instead of an even split."
        print("OK: unspeakable raw dict syntax is dropped; a clean qualitative comment passes through")

        assert _extract_spoken_comment("NO_COMMENT") is None
        assert _extract_spoken_comment("no_comment") is None
        assert _extract_spoken_comment("NO_COMMENT.") is None, "trailing punctuation must still count as no-comment"
        assert _extract_spoken_comment(
            "Not worth it here.\n\nNO_COMMENT"
        ) is None, "leaked reasoning followed by the sentinel must still be treated as no-comment"
        print("OK: NO_COMMENT is recognized however it's phrased or wrapped")

        long_reply = "This is a real comment. " + ("padding word " * 30)
        extracted = _extract_spoken_comment(long_reply)
        assert extracted is not None and len(extracted) <= _MAX_COMMENT_CHARS, extracted
        assert extracted.startswith("This is a real comment"), extracted
        print("OK: an overlong reply is trimmed down to a single short sentence")

        print("QuantumWatcher self-check OK")
