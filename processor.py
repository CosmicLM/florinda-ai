"""processor.py — The Brain: turns user input into a structured instruction from the AI."""
import logging
import re
from dataclasses import dataclass
from string import Template
from typing import Callable, Iterator, Literal, Optional

from config import NULL_COMMAND

logger = logging.getLogger(__name__)

DEFAULT_PROMPT_PATH = "./INSTRUCTION.md"
END_OF_COMMAND_TOKEN = "<END>"

_FIELD_PREFIXES = {
    "command": "COMMAND:",
    "speech": "SPEECH:",
    "recursive": "RECURSIVE:",
    "info": "INFO:",
}
_FIELD_ORDER = ("command", "speech", "recursive", "info")
_STATE_DETECTING = "detecting"
_STATE_PLAIN = "plain"

# A sentence boundary: .!? punctuation immediately followed by whitespace.
# Heuristic, not NLP-grade — see _SentenceSplitter docstring.
_SENTENCE_BOUNDARY_RE = re.compile(r"[.!?]+\s+")


@dataclass(frozen=True)
class ParsedInstruction:
    """One AI turn's structured intent.

    WHY: the wire format is COMMAND:$EOC SPEECH:$EOC RECURSIVE:$EOC INFO:$EOC —
    this dataclass is the single seam between that string protocol and the rest
    of Hypr, so nothing downstream re-parses raw AI text.
    """
    speech: str
    command: str = NULL_COMMAND
    recursive: bool = False
    info: Optional[str] = None


class _SentenceSplitter:
    """Buffers streamed text and emits complete sentences as soon as they appear.

    Heuristic only, not NLP-grade: a boundary is punctuation in .!? immediately
    followed by whitespace, found in text ALREADY received — never assumed
    just because a chunk happened to end there, since more of the same
    sentence may still be arriving (chunk boundaries don't align with
    anything semantic). Known false positives on things like "3.5" or "Dr."
    are an accepted, low-stakes limitation: this is a latency optimization,
    not a correctness-critical sentence-boundary detector.
    """

    def __init__(self, on_sentence: Callable[[str], None]) -> None:
        self._on_sentence = on_sentence
        self._buffer = ""

    def feed(self, text: str) -> None:
        if not text:
            return
        self._buffer += text
        cursor = 0
        for match in _SENTENCE_BOUNDARY_RE.finditer(self._buffer):
            sentence = self._buffer[cursor : match.end()].strip()
            if sentence:
                self._on_sentence(sentence)
            cursor = match.end()
        self._buffer = self._buffer[cursor:]

    def flush(self) -> None:
        """Emit whatever's left over, even without trailing punctuation —
        called at true stream-end and at every field boundary, since no more
        SPEECH text will ever follow past either point."""
        remainder = self._buffer.strip()
        self._buffer = ""
        if remainder:
            self._on_sentence(remainder)


class StreamingInstructionParser:
    """Incrementally parses a growing Gemini reply into a ParsedInstruction.

    Feed it every chunk as it arrives via feed(); it calls the on_speech_chunk
    callback with each complete sentence of SPEECH text the moment that
    sentence is detected (plain-mode: the whole reply is SPEECH; structured-
    mode: only the text between the 1st and 2nd <END>). Call finish() exactly
    once after the stream ends to get the final ParsedInstruction.

    State machine:
        detecting -> plain                          (no "COMMAND:" prefix found)
        detecting -> command                        ("COMMAND:" prefix found)
        command   -> speech       (on 1st <END>)
        speech    -> recursive    (on 2nd <END>)
        recursive -> info         (on 3rd <END>)
        info      -> (terminal; just accumulates until finish())

    Mode detection deliberately differs from the old rule of checking whether
    <END> appears ANYWHERE in the complete text — impossible to know early in
    a stream without reading the whole thing, which would defeat streaming.
    Instead this keys off whether the reply STARTS with "COMMAND:", decidable
    within the first few characters. The two heuristics agree on every well-
    formed reply; they can only diverge on an already-malformed one — see the
    eoc_count == 0 fallback below, which mirrors the old parser's behavior in
    that same rare case.

    Robustness beyond the old parser: if the stream ends in the
    command/speech/recursive state (fewer than 3 <END> tokens ever appeared —
    a real IndexError seen in production with a model that follows the format
    less consistently), finish() degrades the missing fields to their
    defaults (N / empty) instead of crashing.
    """

    def __init__(self, on_speech_chunk: Callable[[str], None]) -> None:
        self._on_speech_chunk = on_speech_chunk
        self._sentences = _SentenceSplitter(on_speech_chunk)
        self._state = _STATE_DETECTING
        self._raw_buffer = ""  # every chunk, verbatim — for the eoc_count==0 fallback
        self._field_buffer = ""  # unconsumed text belonging to the CURRENT state
        self._prefix_stripped = False
        self._eoc_count = 0
        self._texts = {"command": "", "speech": "", "recursive": "", "info": ""}

    def feed(self, chunk: str) -> None:
        if not chunk:
            return
        self._raw_buffer += chunk
        self._field_buffer += chunk
        if self._state == _STATE_DETECTING:
            self._resolve_mode(final=False)
            if self._state not in (_STATE_DETECTING, _STATE_PLAIN):
                self._advance_fields(final=False)
        elif self._state == _STATE_PLAIN:
            leftover, self._field_buffer = self._field_buffer, ""
            self._texts["speech"] += leftover
            self._sentences.feed(leftover)
        else:
            self._advance_fields(final=False)

    def finish(self) -> ParsedInstruction:
        if self._state == _STATE_DETECTING:
            self._resolve_mode(final=True)
        if self._state not in (_STATE_DETECTING, _STATE_PLAIN):
            self._advance_fields(final=True)

        if self._state == _STATE_PLAIN:
            self._sentences.flush()
            return ParsedInstruction(speech=self._texts["speech"].strip())

        if self._eoc_count == 0:
            leftover = self._raw_buffer.strip()
            if leftover:
                self._on_speech_chunk(leftover)
            return ParsedInstruction(speech=leftover)

        self._sentences.flush()
        return ParsedInstruction(
            speech=self._texts["speech"].strip(),
            command=self._texts["command"].strip() or NULL_COMMAND,
            recursive=self._texts["recursive"].strip().upper() == "Y",
            info=(self._texts["info"].strip() or None),
        )

    # --- mode / field state machine ---

    def _resolve_mode(self, final: bool) -> None:
        stripped = self._field_buffer.lstrip()
        prefix = _FIELD_PREFIXES["command"]
        if len(stripped) >= len(prefix) or final:
            if stripped[: len(prefix)].lower() == prefix.lower():
                self._state = "command"
                self._field_buffer = stripped[len(prefix) :]
                self._prefix_stripped = True
            else:
                self._enter_plain()
            return
        if not prefix.lower().startswith(stripped.lower()):
            self._enter_plain()  # already can't match "COMMAND:" — bail out early

    def _enter_plain(self) -> None:
        self._state = _STATE_PLAIN
        leftover, self._field_buffer = self._field_buffer, ""
        self._texts["speech"] += leftover
        self._sentences.feed(leftover)

    def _advance_fields(self, final: bool) -> None:
        """Consume as many complete fields as the buffer allows, in a loop —
        a single feed() (or the final finish()) call can contain more than
        one field boundary at once for short/late-arriving replies."""
        while self._state in _FIELD_ORDER:
            if not self._prefix_stripped and not self._strip_field_prefix(final):
                return  # need more chars to decide; wait for the next feed()
            if self._state == "info":
                self._texts["info"] += self._field_buffer
                self._field_buffer = ""
                return  # terminal field: no closing token to look for
            idx = self._field_buffer.find(END_OF_COMMAND_TOKEN)
            if idx == -1:
                self._consume_partial(final)
                return
            self._close_current_field(idx)

    def _strip_field_prefix(self, final: bool) -> bool:
        prefix = _FIELD_PREFIXES[self._state]
        stripped = self._field_buffer.lstrip()
        if len(stripped) < len(prefix) and not final:
            return False
        if stripped[: len(prefix)].lower() == prefix.lower():
            self._field_buffer = stripped[len(prefix) :]
        # else: model omitted/garbled the expected prefix — proceed without
        # stripping rather than waiting forever for one that may never come.
        self._prefix_stripped = True
        return True

    def _consume_partial(self, final: bool) -> None:
        """No full <END> in the field buffer yet. Release whatever's safely
        known NOT to be a partial <END> straddling the next chunk (withhold
        the last len(TOKEN)-1 characters), so a split token never leaks into
        spoken output."""
        margin = 0 if final else len(END_OF_COMMAND_TOKEN) - 1
        safe_len = max(0, len(self._field_buffer) - margin)
        emit, self._field_buffer = self._field_buffer[:safe_len], self._field_buffer[safe_len:]
        self._texts[self._state] += emit
        if self._state == "speech":
            self._sentences.feed(emit)

    def _close_current_field(self, idx: int) -> None:
        piece = self._field_buffer[:idx]
        remainder = self._field_buffer[idx + len(END_OF_COMMAND_TOKEN) :]
        self._texts[self._state] += piece
        if self._state == "speech":
            self._sentences.feed(piece)
            self._sentences.flush()  # field boundary: no more SPEECH text is coming
        self._eoc_count += 1
        next_idx = _FIELD_ORDER.index(self._state) + 1
        self._state = _FIELD_ORDER[next_idx] if next_idx < len(_FIELD_ORDER) else "info"
        self._field_buffer = remainder
        self._prefix_stripped = False


class PromptProcessor:
    """The Brain: sends prompts to Gemini and hands back a ParsedInstruction."""

    def __init__(
        self,
        ai_client,
        ai_model: str,
        ai_model_light: Optional[str] = None,
        prompt_path: str = DEFAULT_PROMPT_PATH,
    ) -> None:
        self._client = ai_client
        self._ai_model = ai_model
        self._ai_model_light = ai_model_light or ai_model
        self._prompt_path = prompt_path

    def generate_instruction(
        self,
        user_input: str,
        tier: Literal["light", "deep"] = "light",
        sys_info: str = "",
        on_speech_chunk: Optional[Callable[[str], None]] = None,
    ) -> ParsedInstruction:
        """Query the AI for `user_input`, streaming SPEECH text out via
        `on_speech_chunk` (called once per complete sentence, as soon as it's
        detected) instead of waiting for the whole reply. A failed turn still
        returns something speakable; callers that don't need incremental
        speech can omit `on_speech_chunk` and just use the return value.
        """
        emit = on_speech_chunk or (lambda _sentence: None)
        parser = StreamingInstructionParser(emit)
        try:
            for chunk_text in self._stream_model(user_input, tier, sys_info):
                parser.feed(chunk_text)
            return parser.finish()
        except Exception:
            logger.exception("Hypr orchestration failed for input: %r", user_input)
            return ParsedInstruction(speech="An error occurred. Please check the logs.")

    def _stream_model(
        self, user_input: str, tier: Literal["light", "deep"], sys_info: str = ""
    ) -> Iterator[str]:
        model = self._ai_model if tier == "deep" else self._ai_model_light
        system_prompt = self._load_system_prompt(sys_info)
        for response in self._client.models.generate_content_stream(
            model=model,
            contents=user_input,
            config={"system_instruction": system_prompt},
        ):
            text = getattr(response, "text", "") or ""
            if text:
                yield text

    def _load_system_prompt(self, sys_info: str = "") -> str:
        with open(self._prompt_path, "r") as prompt_file:
            template = Template(prompt_file.read())
        return template.safe_substitute(EOC=END_OF_COMMAND_TOKEN, SYS_INFO=sys_info)
