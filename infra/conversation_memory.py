"""conversation_memory.py — persists recent voice-turn history so Florinda remembers
what was just discussed, across separate push-to-talk invocations.

WHY this was missing until now: StateManifest already wrote the last turn's
speech/command/info to disk after every request, but nothing ever read it back
into a future prompt — every voice turn was effectively the first message of a
brand new conversation. This closes that gap.

WHY scoped to the always-on service, not the CLI: a CLI invocation
(`python3 flora_daemon.py "..."`) is a deliberately one-shot, scripted call —
folding conversation history into that would change behavior nobody asked to
change. FloraDaemon's `memory` parameter defaults to None, which preserves that
exact stateless behavior; only flora_service.py wires in a real instance.

WHY only `instruction.speech` is stored, not the raw COMMAND/SPEECH/RECURSIVE/
INFO wire-protocol text: the point is conversational continuity ("what did we
just talk about"), not replaying Florinda's own internal formatting back at itself.
"""
import json
import time
from pathlib import Path
from typing import Any


class ConversationMemory:
    """Bounded, disk-persisted turn history, shaped for Gemini's multi-turn `contents`."""

    def __init__(self, memory_path: Path, max_turns: int, reset_after_s: float) -> None:
        self._memory_path = memory_path
        self._max_turns = max_turns
        self._reset_after_s = reset_after_s

    def add_user_turn(self, text: str) -> None:
        self._append("user", text)

    def add_assistant_turn(self, text: str) -> None:
        self._append("model", text)

    def get_history_contents(self) -> list[dict[str, Any]]:
        """Returns prior turns as Gemini-shaped contents entries, oldest first.
        Returns [] if the conversation has gone stale (see module docstring)."""
        turns = self._load()
        if not turns:
            return []
        if time.time() - turns[-1]["timestamp"] > self._reset_after_s:
            return []
        return [{"role": turn["role"], "parts": [{"text": turn["content"]}]} for turn in turns]

    def clear(self) -> None:
        self._save([])

    def pop_stale_turns(self) -> list[dict[str, Any]]:
        """Same staleness check as get_history_contents(), but RETURNS and
        CLEARS the discarded turns instead of silently dropping them — lets
        a caller archive a conversation into longer-term storage before it's
        lost, rather than it just vanishing once reset_after_s elapses.
        Returns [] (and leaves storage untouched) if there's no history at
        all, or if the conversation is still fresh — there's nothing stale
        to pop in either case."""
        turns = self._load()
        if not turns:
            return []
        if time.time() - turns[-1]["timestamp"] <= self._reset_after_s:
            return []
        self._save([])
        return turns

    def _append(self, role: str, text: str) -> None:
        if not text or not text.strip():
            return
        turns = self._load()
        turns.append({"role": role, "content": text, "timestamp": time.time()})
        self._save(turns[-self._max_turns :])

    def _load(self) -> list[dict[str, Any]]:
        if not self._memory_path.exists():
            return []
        try:
            with open(self._memory_path, "r") as memory_file:
                return json.load(memory_file)
        except (OSError, json.JSONDecodeError):
            return []

    def _save(self, turns: list[dict[str, Any]]) -> None:
        self._memory_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._memory_path, "w") as memory_file:
            json.dump(turns, memory_file, indent=2)


if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory() as tmp_dir:
        path = Path(tmp_dir) / "conversation.json"

        memory = ConversationMemory(path, max_turns=4, reset_after_s=1800)
        assert memory.get_history_contents() == []

        memory.add_user_turn("My favorite color is blue.")
        memory.add_assistant_turn("Got it, blue is your favorite color.")
        contents = memory.get_history_contents()
        assert contents == [
            {"role": "user", "parts": [{"text": "My favorite color is blue."}]},
            {"role": "model", "parts": [{"text": "Got it, blue is your favorite color."}]},
        ], contents
        print("OK: round-trip works:", contents)

        # bounding: max_turns=4, add 3 more turns (6 total) -> only last 4 kept
        memory.add_user_turn("What's 2+2?")
        memory.add_assistant_turn("4.")
        memory.add_user_turn("And 3+3?")
        memory.add_assistant_turn("6.")
        contents = memory.get_history_contents()
        assert len(contents) == 4, contents
        assert contents[0]["parts"][0]["text"] == "What's 2+2?", contents
        print("OK: bounded to max_turns:", [c["parts"][0]["text"] for c in contents])

        # staleness reset
        stale_memory = ConversationMemory(path, max_turns=4, reset_after_s=0.01)
        time.sleep(0.05)
        assert stale_memory.get_history_contents() == []
        print("OK: stale conversation resets to empty")

        # pop_stale_turns: returns+clears what get_history_contents would
        # have silently discarded, using a fresh path so it's independent
        # of the assertions above
        pop_path = Path(tmp_dir) / "pop_conversation.json"
        pop_memory = ConversationMemory(pop_path, max_turns=4, reset_after_s=0.01)
        assert pop_memory.pop_stale_turns() == [], "nothing to pop with no history yet"
        pop_memory.add_user_turn("What's the capital of France?")
        pop_memory.add_assistant_turn("Paris.")
        assert pop_memory.pop_stale_turns() == [], "fresh conversation has nothing stale to pop"
        time.sleep(0.05)
        popped = pop_memory.pop_stale_turns()
        assert [t["content"] for t in popped] == ["What's the capital of France?", "Paris."], popped
        assert pop_memory.get_history_contents() == [], "storage should be cleared after popping"
        assert pop_memory.pop_stale_turns() == [], "already-cleared storage has nothing left to pop"
        print("OK: pop_stale_turns returns+clears exactly what would've been silently lost")

        memory.clear()
        assert memory.get_history_contents() == []
        print("OK: clear() works")

        print("ConversationMemory self-check OK")
