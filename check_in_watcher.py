"""check_in_watcher.py — periodically glances at whatever's been on screen
and, if there's something concrete worth a casual remark, checks in briefly
("how's that going", "still working on X?"). Distinct from quantum_watcher.py
(domain-specific, keyword-triggered on quantum/Qiskit content specifically)
— this is a general, purely time-gated check-in that fires occasionally
regardless of subject matter, using whatever's already in the shared
screen-OCR cache (sys_info_cache.py) rather than hooking into the OCR
pipeline itself.

WHY time-gated instead of keyword-triggered: the user asked for something
that checks in "every now and then" — not "whenever X word appears," which
is what quantum_watcher.py already does for its own narrower domain. This
fires no more than once per `cooldown_s`, independent of screen content.

WHY the local model can still say nothing: same caution as quantum_watcher.py
— asked to comment on a screen with nothing substantive on it (a blank
desktop, a login prompt, unreadable OCR noise), a small local model will
happily invent something plausible-sounding rather than admit there's
nothing worth saying. Same NO_COMMENT sentinel and same defensive parsing
(dict/JSON leakage, overlong replies) as quantum_watcher.py, since both
failure modes were verified live against the same local model there and
there's no reason to assume this prompt is immune.
"""
import json
import logging
import re
import time
from pathlib import Path
from typing import Callable, Optional

from local_brain import LocalBrain, LocalBrainError

logger = logging.getLogger(__name__)

NO_COMMENT = "NO_COMMENT"

_HISTORY_PATH = Path.home() / ".local/share/flora-ai/check_in_history.json"
_MAX_HISTORY = 5
_MAX_COMMENT_CHARS = 220
_MIN_SCREEN_TEXT_CHARS = 40  # near-empty OCR isn't worth asking a model about at all

_NO_COMMENT_RE = re.compile(r"\bno_comment\b", re.IGNORECASE)
_RAW_DICT_RE = re.compile(r"\{[^{}\n]{0,120}\}")

_SYSTEM_PROMPT = """You are Florinda, a voice assistant glancing at the user's screen every so often just to \
check in — like a coworker walking by, not a supervisor watching over their shoulder. This is NOT triggered \
by anything specific happening; it just fires occasionally, so most of the time there's nothing worth saying.

Only speak up if the CURRENT screen text below shows something concrete and specific enough to casually \
reference — a named project, file, error, or task actually visible in the text. If the text is vague, \
generic, unreadable OCR noise, or just a browser/desktop with nothing identifiable, you have nothing to say.

Never state the obvious or describe what a tool IS. Never invent a project name, error, or detail that is \
not literally present in the text below. Never guess or comment on the user's mood, confidence, feelings, \
or how something is "going" — you cannot see or know any of that, only reference a concrete, nameable thing \
actually visible in the text (a file, function, project, error message, task). Keep it SHORT, casual, and \
low-key — one brief sentence, phrased like a passing remark, not an update or a status report.
{history_context}
If it is NOT worth commenting, reply with EXACTLY: {no_comment}
If — and only if — there's something genuinely worth a casual remark, reply with ONLY that one short \
sentence. No preamble, no quotes, no explanation, and don't repeat a check-in you've already made recently \
(see above)."""


class CheckInWatcher:
    """Occasionally comments on whatever's on screen, purely on a time cooldown."""

    def __init__(
        self,
        brain: LocalBrain,
        get_screen_text: Callable[[], str],
        cooldown_s: float,
        on_comment: Callable[[str], bool],
        history_path: Path = _HISTORY_PATH,
    ) -> None:
        self._brain = brain
        self._get_screen_text = get_screen_text
        self._cooldown_s = cooldown_s
        self._on_comment = on_comment
        self._history_path = history_path
        self._last_attempt_at = 0.0

    def poll_once(self) -> None:
        if time.monotonic() - self._last_attempt_at < self._cooldown_s:
            return
        screen_text = self._get_screen_text()
        if len(screen_text) < _MIN_SCREEN_TEXT_CHARS:
            return
        # WHY truncate once, up front, and reuse `shown_text` for both the
        # prompt AND the fabrication check: verified live — checking a
        # generated number against the FULL screen_text (not just the slice
        # the model actually saw) let a coincidental match elsewhere in a
        # large OCR blob wrongly "verify" a number the model never had
        # access to in the first place, defeating the point of the guard.
        shown_text = screen_text[:2000]
        self._last_attempt_at = time.monotonic()
        try:
            reply = self._brain.generate(
                f"Current screen text (may be noisy OCR):\n{shown_text}",
                system=_SYSTEM_PROMPT.format(history_context=self._format_history(), no_comment=NO_COMMENT),
            )
        except LocalBrainError:
            logger.exception("local brain call failed during check-in")
            return
        comment = _extract_spoken_comment(reply)
        if comment and _has_fabricated_number(comment, shown_text):
            logger.warning("check-in comment invented a number not present on screen, dropping: %r", comment)
            return
        if comment and self._on_comment(comment):
            self._remember_comment(comment)

    def _format_history(self) -> str:
        history = self._read_history()
        if not history:
            return ""
        lines = "\n".join(f'- "{entry["comment"]}"' for entry in history)
        return f"\nYour own recent check-ins (don't just repeat these):\n{lines}\n"

    def _read_history(self) -> list:
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


def _extract_spoken_comment(reply: str) -> Optional[str]:
    """Mirrors quantum_watcher.py's _extract_spoken_comment exactly — same
    local model, same observed failure modes (leaked reasoning around the
    sentinel, raw dict/JSON leakage, overlong replies)."""
    if not reply or not reply.strip():
        return None
    if _NO_COMMENT_RE.search(reply):
        return None
    text = reply.strip().splitlines()[0].strip()
    if _RAW_DICT_RE.search(text):
        return None
    if len(text) <= _MAX_COMMENT_CHARS:
        return text
    truncated = text[:_MAX_COMMENT_CHARS]
    boundary = max(truncated.rfind(". "), truncated.rfind("! "), truncated.rfind("? "))
    return truncated[: boundary + 1] if boundary > 40 else truncated.rsplit(" ", 1)[0]


_NUMBER_RE = re.compile(r"\d[\d,.]*\s?%?")


def _has_fabricated_number(comment: str, screen_text: str) -> bool:
    """WHY this exists: observed live — asked to check in on a general
    screen (not the quantum-specific evidence quantum_watcher.py gates on),
    phi4-mini invented a specific, plausible-sounding statistic ("23%
    autonomous based on current tasks") that appears nowhere in the actual
    OCR'd text. A number is either genuinely read off the screen or it's
    made up — there's no in-between — so any number in the comment that
    doesn't appear verbatim in the source text is treated as fabricated and
    the whole comment is dropped, same "reject, don't repair" philosophy as
    quantum_watcher.py's raw-dict guard."""
    for match in _NUMBER_RE.finditer(comment):
        if match.group().strip() and match.group() not in screen_text:
            return True
    return False


if __name__ == "__main__":
    import tempfile

    class _StubBrain:
        def __init__(self, reply: str) -> None:
            self._reply = reply
            self.last_prompt: Optional[str] = None
            self.last_system: Optional[str] = None
            self.calls = 0

        def generate(self, prompt: str, system: Optional[str] = None) -> str:
            self.calls += 1
            self.last_prompt = prompt
            self.last_system = system
            return self._reply

    with tempfile.TemporaryDirectory() as tmp_dir:
        history_path = Path(tmp_dir) / "history.json"
        calls: list = []

        stub_short = _StubBrain("should never be called")
        watcher = CheckInWatcher(
            stub_short, get_screen_text=lambda: "hi", cooldown_s=0,
            on_comment=lambda c: (calls.append(c), True)[1], history_path=history_path,
        )
        watcher.poll_once()
        assert stub_short.calls == 0, "near-empty screen text must never reach the model"
        print("OK: near-empty screen text short-circuits before any model call")

        stub_no = _StubBrain(NO_COMMENT)
        watcher2 = CheckInWatcher(
            stub_no, get_screen_text=lambda: "a" * 200, cooldown_s=0,
            on_comment=lambda c: (calls.append(c), True)[1], history_path=history_path,
        )
        watcher2.poll_once()
        assert calls == [], "NO_COMMENT must suppress on_comment"
        assert stub_no.calls == 1, "long-enough screen text should reach the model"
        print("OK: NO_COMMENT is respected once the model is actually asked")

        stub_yes = _StubBrain("Still deep in that refactor, huh?")
        watcher3 = CheckInWatcher(
            stub_yes, get_screen_text=lambda: "def refactor_module(): pass\n" * 10, cooldown_s=1000,
            on_comment=lambda c: (calls.append(c), True)[1], history_path=history_path,
        )
        watcher3.poll_once()
        assert calls == ["Still deep in that refactor, huh?"], calls
        print("OK: a real comment fires on_comment")

        watcher3.poll_once()
        assert calls == ["Still deep in that refactor, huh?"], "cooldown should suppress a second attempt entirely"
        assert stub_yes.calls == 1, "cooldown must prevent even calling the model again"
        print("OK: cooldown suppresses a second attempt without even calling the model")

        assert json.loads(history_path.read_text())[-1]["comment"] == "Still deep in that refactor, huh?"
        print("OK: a real comment is persisted to on-disk history")

        stub4 = _StubBrain("Another casual remark.")
        watcher4 = CheckInWatcher(
            stub4, get_screen_text=lambda: "b" * 200, cooldown_s=0,
            on_comment=lambda c: (calls.append(c), True)[1], history_path=history_path,
        )
        watcher4.poll_once()
        assert "deep in that refactor" in stub4.last_system, "past comments should be surfaced in the system prompt"
        print("OK: comment history persists and is surfaced across separate watcher instances (service restarts)")

        assert _extract_spoken_comment("NO_COMMENT") is None
        assert _extract_spoken_comment("Not worth it.\n\nNO_COMMENT") is None
        assert _extract_spoken_comment("Observed: {'x': 1} interesting.") is None
        assert _extract_spoken_comment("A clean, real remark.") == "A clean, real remark."
        print("OK: defensive parsing matches quantum_watcher.py's verified behavior")

        screen = "def foo(): pass\nrunning 3 tests, 2 passed"
        assert not _has_fabricated_number("You've got 3 tests going.", screen)
        assert _has_fabricated_number("23% autonomous based on current tasks.", screen), (
            "a number not present anywhere in the source screen text must be flagged as fabricated"
        )
        print("OK: a fabricated number not present on screen is caught; a real one read off screen is not")

        stub_fabricated = _StubBrain("23% autonomous based on current tasks. Cool!")
        watcher5 = CheckInWatcher(
            stub_fabricated, get_screen_text=lambda: "some real screen text with no numbers on it at all here",
            cooldown_s=0, on_comment=lambda c: (calls.append(c), True)[1], history_path=history_path,
        )
        calls_before = len(calls)
        watcher5.poll_once()
        assert len(calls) == calls_before, "a comment with a fabricated number must never reach on_comment"
        print("OK: poll_once drops a comment containing a fabricated number end-to-end")

        print("CheckInWatcher self-check OK")
