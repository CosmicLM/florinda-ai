"""processor.py — The Brain: turns user input into a structured instruction from the AI."""
import json
import logging
import re
import threading
from dataclasses import dataclass
from string import Template
from typing import Callable, Iterator, Literal, Optional

import httpx
import requests
from google.genai import types
from google.genai.errors import APIError

import anthropic_backend
import claude_cli_backend
import openai_backend
from config import NULL_COMMAND

logger = logging.getLogger(__name__)

DEFAULT_PROMPT_PATH = "./INSTRUCTION.md"
END_OF_COMMAND_TOKEN = "<END>"

_CLAUDE_FIELD_SPLIT_RE = re.compile(r"(?=\bSPEECH:|\bRECURSIVE:|\bINFO:)")


def _ensure_end_tokens(text: str) -> str:
    """WHY this exists: verified live — claude_cli_backend reliably produces
    the COMMAND:/SPEECH:/RECURSIVE:/INFO: field prefixes this wire protocol
    expects, but consistently omits the literal <END> delimiter between them
    that StreamingInstructionParser requires to detect field boundaries —
    this whole protocol was iteratively tuned against Gemini's specific
    formatting habits over this project's history, which a different model
    doesn't automatically share. Rather than hope prompt wording alone fixes
    a different model's formatting quirk (the same "reject/repair
    deterministically, don't just re-prompt and hope" philosophy already
    used elsewhere, e.g. quantum_watcher.py's raw-dict guard), this splices
    <END> in at the field boundaries directly, which are fixed and always
    appear in this exact order."""
    if END_OF_COMMAND_TOKEN in text:
        return text  # already compliant, don't touch it
    segments = _CLAUDE_FIELD_SPLIT_RE.split(text)
    return END_OF_COMMAND_TOKEN.join(segment.strip() for segment in segments) + END_OF_COMMAND_TOKEN

# WHY a deterministic pattern/length check, not a model call, to decide
# light vs deep: matches this project's existing philosophy (see
# quantum_watcher.py's keyword pre-filter) of gating something more
# expensive behind a cheap, explainable check rather than asking a model to
# judge its own question's complexity first — that would add a whole extra
# round-trip to literally every turn just to decide how to handle it,
# defeating the entire point of having a fast light tier.
_DEEP_REASONING_SIGNALS = re.compile(
    r"\b(why|explain|derive|prove|compare|difference between|walk me through|"
    r"step[\s-]by[\s-]step|in detail|analyze|trade-?offs?|pros and cons|"
    r"how does .+ work)\b",
    re.IGNORECASE,
)
_DEEP_REASONING_MIN_LENGTH = 140  # a long, multi-clause question often needs more than a quick reply


def _needs_deep_reasoning(user_input: str) -> bool:
    if _DEEP_REASONING_SIGNALS.search(user_input):
        return True
    return len(user_input) >= _DEEP_REASONING_MIN_LENGTH


def _format_history_for_claude(conversation_history: Optional[list]) -> str:
    """The `claude` CLI's -p mode is a single fresh prompt with no native
    multi-turn history injection (each invocation is its own new session) —
    folding prior turns into the prompt text itself is a deliberate, partial
    substitute, not full parity with Gemini's native conversation_history
    support. Acceptable here since "deep" tier triggers on generally
    self-contained questions (see _needs_deep_reasoning), not the kind of
    quick back-and-forth continuation where losing native history would be
    most noticeable."""
    if not conversation_history:
        return ""
    lines = []
    for turn in conversation_history:
        role = "You" if turn.get("role") == "model" else "User"
        text = "".join(part.get("text", "") for part in turn.get("parts", []))
        if text:
            lines.append(f"{role}: {text}")
    if not lines:
        return ""
    return "Prior conversation so far:\n" + "\n".join(lines) + "\n\n"


_FIELD_PREFIXES = {
    "command": "COMMAND:",
    "speech": "SPEECH:",
    "recursive": "RECURSIVE:",
    "info": "INFO:",
}
_FIELD_ORDER = ("command", "speech", "recursive", "info")
_STATE_DETECTING = "detecting"
_STATE_PLAIN = "plain"
_STILL_DECIDING = object()  # sentinel: not enough characters yet to know if/which field prefix matches


def _strip_decorative_brackets(command: str) -> str:
    """Observed live, ~16 times across a single day and across nearly every
    tool (ls, web_search, web_clip, app_launcher, skill_manager,
    qiskit_runner, hyprland_bridge — never just one): the model sometimes
    wraps its ENTIRE command in a literal leading '[' and trailing ']', e.g.
    "[python3 qiskit_runner.py run <<'EOF' ... EOF ]". This is almost
    certainly INSTRUCTION.md's own "[ ] are for DECORATIVE USES ONLY, do not
    include the square brackets" bracket notation bleeding into the actual
    command text. '[' is bash's `test` alias, so a wrapped command fails
    immediately with "command not found" every single time — telling the
    model this in prose repeatedly (it already says so explicitly) hasn't
    stopped it from recurring, so this strips it in code instead.

    A REAL `test`/`[ ... ]` shell invocation always has a literal SPACE
    right after '[' and right before ']' (POSIX requires it); this
    decorative wrapping never does (it's glued directly to the command, or
    at most separated by a newline before a heredoc's closing bracket) —
    checking for a literal " ", not just any whitespace, is what tells the
    two apart without risking a legitimate `[ -f "$file" ]` command.
    """
    stripped = command.strip()
    if len(stripped) < 2 or stripped[0] != "[" or stripped[-1] != "]":
        return command
    if stripped[1] == " " or stripped[-2] == " ":
        return command  # looks like a real `[ ... ]` test invocation — leave it alone
    logger.warning("COMMAND was wrapped in decorative brackets, stripping: %r", command)
    return stripped[1:-1].strip()


def _looks_like_real_gemini_response(error: Exception) -> bool:
    """Whether the HTTP response behind a google.genai APIError actually
    looks like it came from Gemini. Checked via Content-Type rather than
    parsing the body: a captive portal/intercepting proxy returns a real
    HTTP status but typically an HTML page (a WiFi login redirect, a block
    page, ...), which is otherwise indistinguishable from a genuine error
    without this check.

    WHY both "json" and "event-stream" count as real: verified live — the
    non-streaming endpoint's errors are application/json, but
    generate_content_stream (what's actually used here) returns errors as
    text/event-stream, same as its normal successful responses. Checking
    for "json" alone made a REAL bad-API-key error look fake and get
    silently routed to the offline fallback instead of surfacing — exactly
    the masking this check exists to prevent. Anything that's neither is
    genuinely suspicious (confirmed live: an intercepting proxy returned
    text/html).

    Defaults to True (i.e. "trust it, don't fall back") whenever the
    content-type can't be determined at all — the failure mode of being
    wrong in that direction is surfacing a real error normally, which is
    always safe; the failure mode of being wrong the other way is masking a
    genuine misconfiguration behind a silent fallback, which is not.
    """
    headers = getattr(getattr(error, "response", None), "headers", None)
    if headers is None:
        return True
    content_type = headers.get("content-type", "").lower()
    return "json" in content_type or "event-stream" in content_type


def _match_field_prefix(stripped: str, final: bool):
    """Whether `stripped` begins a KNOWN wire-protocol field, and which one.

    WHY this checks all four prefixes, not just "COMMAND:" (the only one
    the old code recognized): observed live in the activity log — a
    recursive-continuation reply sometimes starts directly with
    "RECURSIVE: Y" or "INFO: <reasoning>", skipping COMMAND:/SPEECH:
    entirely (SESSION.md's own continuation prompt repeats those two words
    heavily, which plausibly primes this). Recognizing only "COMMAND:" as a
    valid structured-mode entry point meant a reply like that fell back to
    plain mode and dumped the LITERAL "RECURSIVE: Y" / "INFO: <the model's
    own internal reasoning about what the user asked and why>" text as if
    it were spoken SPEECH — audibly narrating its own reasoning, which is
    exactly the bug this fixes. Treating any of the four fields as a valid
    start (with earlier, skipped fields simply defaulting to empty) means
    that text is parsed correctly and never reaches on_speech_chunk/SPEECH.
    """
    lowered = stripped.lower()
    still_possible = False
    for name in _FIELD_ORDER:
        prefix = _FIELD_PREFIXES[name].lower()
        if lowered.startswith(prefix):
            return name, stripped[len(prefix) :]
        if not final and prefix.startswith(lowered):
            still_possible = True
    return _STILL_DECIDING if still_possible else None


# A sentence boundary: .!? punctuation immediately followed by whitespace.
# Heuristic, not NLP-grade — see _SentenceSplitter docstring.
_SENTENCE_BOUNDARY_RE = re.compile(r"[.!?]+\s+")


@dataclass(frozen=True)
class ParsedInstruction:
    """One AI turn's structured intent.

    WHY: the wire format is COMMAND:$EOC SPEECH:$EOC RECURSIVE:$EOC INFO:$EOC —
    this dataclass is the single seam between that string protocol and the rest
    of Florinda, so nothing downstream re-parses raw AI text.
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

    def discard(self) -> None:
        """Drop whatever's buffered without emitting it. WHY needed: feed()
        is called incrementally as a field streams in, before its full
        content (and therefore whether it's a leaked wire-protocol fragment,
        see _close_current_field) is known — by the time that's detected,
        partial text may already sit in this buffer waiting for a future
        flush(). This clears it so that flush() (always called once more by
        StreamingInstructionParser.finish()) has nothing left to leak."""
        self._buffer = ""


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
            # WHY check for a field prefix here too: this branch means the
            # model started what looked like a structured reply but never
            # closed even ONE field with <END> — degenerate, but if that
            # leftover still starts with e.g. "RECURSIVE:"/"INFO:", it's
            # still clearly internal wire-protocol text/reasoning, never
            # meant to be heard. Speaking it verbatim (the old, unconditional
            # behavior) is exactly the "why did it narrate its own reasoning"
            # bug. Only genuinely prefix-less text — an ordinary
            # conversational reply that never engaged the structured format
            # at all — should fall back to being read aloud as-is.
            if _match_field_prefix(leftover, final=True) is not None:
                return ParsedInstruction(speech="")
            if leftover:
                self._on_speech_chunk(leftover)
            return ParsedInstruction(speech=leftover)

        self._sentences.flush()
        return ParsedInstruction(
            speech=self._texts["speech"].strip(),
            command=self._sanitize_command(self._texts["command"].strip()),
            recursive=self._texts["recursive"].strip().upper() == "Y",
            info=(self._texts["info"].strip() or None),
        )

    @staticmethod
    def _sanitize_command(command: str) -> str:
        """Observed live: a malformed reply left the literal text
        "SPEECH: <the actual answer>" sitting in the COMMAND field, which
        then got handed to the shell verbatim as if it were a real command.
        A command that itself starts with one of the wire-protocol's own
        field prefixes is never a real shell command — it's a sign the
        model duplicated/misplaced a field — so treat it as no command at
        all instead of executing garbled text.
        """
        if not command:
            return NULL_COMMAND
        command = _strip_decorative_brackets(command)
        lowered = command.lower()
        if any(lowered.startswith(prefix.lower()) for prefix in _FIELD_PREFIXES.values()):
            logger.warning("COMMAND field looked like a mangled wire-protocol field, dropping: %r", command)
            return NULL_COMMAND
        return command

    # --- mode / field state machine ---

    def _resolve_mode(self, final: bool) -> None:
        stripped = self._field_buffer.lstrip()
        match = _match_field_prefix(stripped, final)
        if match is _STILL_DECIDING:
            return  # need more chars to decide; wait for the next feed()
        if match is None:
            self._enter_plain()
            return
        field_name, remainder = match
        self._state = field_name
        self._field_buffer = remainder
        self._prefix_stripped = True

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
        # WHY re-check the field prefix here too, on the SPEECH field
        # specifically: observed live — a recursive-continuation reply can
        # be structurally well-formed (SPEECH: really does open and close
        # with real <END> tokens) while its CONTENT is just the model
        # echoing another field's literal text, e.g. SPEECH: "RECURSIVE: Y".
        # The mode-detection/eoc_count==0 fixes only catch a reply that
        # skips the field structure entirely — this is a different leak,
        # inside an otherwise-valid SPEECH field, so it needs its own guard.
        if self._state == "speech":
            full_speech = self._texts["speech"] + piece
            if _match_field_prefix(full_speech.strip(), final=True) is not None:
                logger.warning(
                    "SPEECH field looked like a leaked wire-protocol fragment, dropping: %r",
                    full_speech,
                )
                self._texts["speech"] = ""
                self._sentences.discard()  # drop any partial text already buffered by earlier feed() calls
            else:
                self._texts["speech"] = full_speech
                self._sentences.feed(piece)
                self._sentences.flush()  # field boundary: no more SPEECH text is coming
        else:
            self._texts[self._state] += piece
        self._eoc_count += 1
        next_idx = _FIELD_ORDER.index(self._state) + 1
        self._state = _FIELD_ORDER[next_idx] if next_idx < len(_FIELD_ORDER) else "info"
        self._field_buffer = remainder
        self._prefix_stripped = False


class PromptProcessor:
    """The Brain: sends prompts to Gemini and hands back a ParsedInstruction.

    WHY the offline fallback: Gemini is a hard dependency on the internet
    being up — with none, Florinda would otherwise go completely silent. If
    `offline_model` is given, a connectivity failure (specifically
    httpx.TransportError — DNS/connection/timeout, NOT a real Gemini API
    error like a bad key or quota, which should surface normally rather than
    be masked) transparently retries the SAME turn against a local Ollama
    model instead, so Florinda degrades to "works locally, less capable"
    instead of "doesn't work at all" when there's no internet.
    """

    def __init__(
        self,
        ai_client,
        ai_model: str,
        ai_model_light: Optional[str] = None,
        prompt_path: str = DEFAULT_PROMPT_PATH,
        offline_model: Optional[str] = None,
        ollama_host: str = "http://localhost:11434",
        use_claude_cli_for_deep: bool = False,
        claude_cli_timeout_s: float = 60.0,
        ai_provider: Literal["gemini", "openai", "anthropic"] = "gemini",
        openai_api_key: Optional[str] = None,
        openai_base_url: str = "https://api.openai.com/v1",
        openai_model: Optional[str] = None,
        openai_model_light: Optional[str] = None,
        anthropic_api_key: Optional[str] = None,
        anthropic_model: str = "claude-opus-5",
        anthropic_model_light: str = "claude-haiku-4-5",
    ) -> None:
        self._client = ai_client
        self._ai_model = ai_model
        self._ai_model_light = ai_model_light or ai_model
        self._prompt_path = prompt_path
        self._offline_model = offline_model
        self._ollama_host = ollama_host.rstrip("/")
        # WHY the deep tier can route through the user's own Claude
        # subscription instead of Gemini: verified live — the `claude` CLI
        # authenticates via subscription (OAuth), not a separate metered API
        # key, and (with tool/skill loading stripped) reliably follows this
        # project's own COMMAND/SPEECH/RECURSIVE/INFO wire protocol just as
        # well as Gemini does. Reserved for "deep" only — see
        # claude_cli_backend.py's own WHY notes for the latency tradeoff
        # that makes it unsuitable for the fast light tier.
        self._use_claude_cli_for_deep = use_claude_cli_for_deep
        self._claude_cli_timeout_s = claude_cli_timeout_s
        # WHY these are alternate PRIMARY providers, not more fallbacks
        # layered onto Gemini: a user without Gemini access (or who simply
        # prefers a different provider) sets FLORA_AI_PROVIDER and this
        # entirely replaces Gemini (+ its Claude-CLI-deep-tier/Ollama-offline
        # fallback chain) for both tiers — see _stream_model's dispatch at
        # the top, before any Gemini-specific logic runs.
        self._ai_provider = ai_provider
        self._openai_api_key = openai_api_key
        self._openai_base_url = openai_base_url
        self._openai_model = openai_model
        self._openai_model_light = openai_model_light or openai_model
        self._anthropic_api_key = anthropic_api_key
        self._anthropic_model = anthropic_model
        self._anthropic_model_light = anthropic_model_light

    def generate_instruction(
        self,
        user_input: str,
        tier: Optional[Literal["light", "deep"]] = None,
        sys_info: str = "",
        recent_actions: str = "",
        qiskit_notes: str = "",
        on_speech_chunk: Optional[Callable[[str], None]] = None,
        conversation_history: Optional[list] = None,
        cancel_event: Optional[threading.Event] = None,
    ) -> ParsedInstruction:
        """Query the AI for `user_input`, streaming SPEECH text out via
        `on_speech_chunk` (called once per complete sentence, as soon as it's
        detected) instead of waiting for the whole reply. A failed turn still
        returns something speakable; callers that don't need incremental
        speech can omit `on_speech_chunk` and just use the return value.

        `conversation_history`, if given, is a list of Gemini-shaped
        `{"role": "user"|"model", "parts": [{"text": ...}]}` entries (see
        conversation_memory.py) prepended to this turn's contents so the model
        sees prior turns. Omitted entirely (default None) for one-shot callers
        like the CLI, which keeps their behavior byte-for-byte unchanged.

        `recent_actions`, if given, is a preformatted block of REAL commands
        already executed moments ago and their REAL output (see
        FloraDaemon._format_recent_actions) — deterministic ground truth about
        what's already been tried, so the model doesn't have to infer that
        from conversational memory alone (observed live: asked to "summarize"
        a page it had just opened, with no such ground truth in front of it,
        the model silently re-ran the same open-url command instead of
        recognizing nothing new had happened).

        `qiskit_notes`, if given, is a short static block of version-pinned
        Qiskit API corrections (see FloraDaemon._format_qiskit_notes) —
        included only on turns that look Qiskit-related, since it's dead
        weight on every other request.

        `cancel_event`, if given and set mid-stream, stops consuming further
        chunks (and therefore emitting further on_speech_chunk calls) as soon
        as the current chunk is done processing — this is what lets a new
        push-to-talk press interrupt a still-generating response instead of
        waiting for it to finish. The returned ParsedInstruction is whatever
        partial text was parsed by that point; callers that pass a
        cancel_event are expected to check it and discard the result rather
        than act on a possibly-truncated command.

        `tier`, if omitted (the normal case — no caller currently passes this
        explicitly), is chosen automatically from `user_input` itself via
        `_needs_deep_reasoning` — an explicit "why"/"explain"/"compare"-style
        question or a long, multi-clause one routes to "deep" (the fuller
        model, with thinking enabled — slower, but reasons more carefully);
        everything else stays on "light" (fast, thinking explicitly
        disabled). Passing tier explicitly overrides the heuristic entirely.
        """
        if tier is None:
            tier = "deep" if _needs_deep_reasoning(user_input) else "light"
        emit = on_speech_chunk or (lambda _sentence: None)
        parser = StreamingInstructionParser(emit)
        try:
            for chunk_text in self._stream_model(
                user_input, tier, sys_info, conversation_history, recent_actions, qiskit_notes, cancel_event
            ):
                if cancel_event is not None and cancel_event.is_set():
                    break
                parser.feed(chunk_text)
            return parser.finish()
        except Exception:
            logger.exception("Florinda orchestration failed for input: %r", user_input)
            return ParsedInstruction(speech="An error occurred. Please check the logs.")

    def _call_gemini_stream(self, model: str, contents, system_prompt: str, thinking_config):
        config = {"system_instruction": system_prompt}
        if thinking_config is not None:
            config["thinking_config"] = thinking_config
        return self._client.models.generate_content_stream(model=model, contents=contents, config=config)

    def _stream_model(
        self,
        user_input: str,
        tier: Literal["light", "deep"],
        sys_info: str = "",
        conversation_history: Optional[list] = None,
        recent_actions: str = "",
        qiskit_notes: str = "",
        cancel_event: Optional[threading.Event] = None,
    ) -> Iterator[str]:
        system_prompt = self._load_system_prompt(sys_info, recent_actions, qiskit_notes)
        if self._ai_provider != "gemini":
            # WHY these entirely REPLACE Gemini (+ its Claude-CLI-deep-tier/
            # Ollama-offline fallback chain) rather than layer on top of it:
            # a user who set FLORA_AI_PROVIDER has no Gemini key configured
            # at all in the common case (see config.py's conditional
            # validator) — falling through to Gemini logic below on any
            # error here would just be a second, differently-broken failure
            # mode, not a real fallback.
            yield from self._stream_alternate_provider(user_input, tier, system_prompt, conversation_history)
            return
        if tier == "deep" and self._use_claude_cli_for_deep:
            try:
                prompt = _format_history_for_claude(conversation_history) + user_input
                reply = claude_cli_backend.generate(
                    prompt, system_prompt, timeout_s=self._claude_cli_timeout_s, cancel_event=cancel_event
                )
                yield _ensure_end_tokens(reply)
                return
            except claude_cli_backend.ClaudeCliCancelled:
                # WHY return (yield nothing) rather than fall through to
                # Gemini: cancel_event fired because a NEW push-to-talk press
                # already came in — starting another slow model call here
                # would keep the old turn alive exactly when the user is
                # trying to barge in on it. generate_instruction's own
                # cancel_event check discards whatever this returns anyway.
                return
            except claude_cli_backend.ClaudeCliError as error:
                # WHY fall through to Gemini deep tier rather than raise:
                # same defensive philosophy as the Gemini->Ollama offline
                # fallback below — a broken/rate-limited subscription
                # backend shouldn't take down the "deep" tier entirely when
                # a working alternative already exists right here.
                logger.warning("Claude CLI backend failed (%s), falling back to Gemini deep tier", error)
        model = self._ai_model if tier == "deep" else self._ai_model_light
        contents = user_input
        if conversation_history:
            contents = [*conversation_history, {"role": "user", "parts": [{"text": user_input}]}]
        # WHY thinking is explicitly set both ways, not left at the model's
        # own default: verified live earlier (screen_control.py's vision
        # lookup) that this exact model family's default/adaptive thinking
        # turned a 2s call into 71s — leaving it unset would make "light"
        # tier's speed unpredictable rather than guaranteed. "deep" gets
        # thinking enabled on purpose — the whole point of routing a harder
        # question there is to trade some latency for better reasoning.
        thinking_config = (
            types.ThinkingConfig(thinking_level=types.ThinkingLevel.HIGH)
            if tier == "deep"
            else types.ThinkingConfig(thinking_budget=0)
        )
        yielded_anything = False
        try:
            for response in self._call_gemini_stream(model, contents, system_prompt, thinking_config):
                text = getattr(response, "text", "") or ""
                if text:
                    yielded_anything = True
                    yield text
        except httpx.TransportError as error:
            # WHY only fall back when NOTHING was yielded yet: a failure
            # partway through a reply means some SPEECH may already be
            # spoken/parsed — restarting fresh on a different model at that
            # point would silently splice two unrelated replies together,
            # which is worse than just surfacing the error.
            if yielded_anything or not self._offline_model:
                raise
            logger.warning(
                "Gemini unreachable (%s) — falling back to local Ollama model %r", error, self._offline_model
            )
            yield from self._stream_ollama(user_input, system_prompt, conversation_history)
        except APIError as error:
            # WHY thinking_config-unsupported is checked FIRST, before the
            # captive-portal/offline-fallback logic below: verified live —
            # gemini-3.5-flash-lite (a valid choice for ai_model_light)
            # rejects thinking_config outright with a plain 400
            # INVALID_ARGUMENT, even thinking_budget=0 explicitly meant to
            # disable it. Not every Gemini model supports this parameter and
            # there's no API to check in advance, so this retries once, same
            # model, without that one parameter — a different, more cheaply
            # recoverable condition than "Gemini is unreachable," which is
            # what the rest of this handler is for.
            if not yielded_anything and thinking_config is not None and getattr(error, "code", None) == 400:
                logger.warning("model %r rejected thinking_config (%s), retrying without it", model, error)
                for response in self._call_gemini_stream(model, contents, system_prompt, None):
                    text = getattr(response, "text", "") or ""
                    if text:
                        yielded_anything = True
                        yield text
                return
            # WHY this is separate from the httpx.TransportError case above:
            # observed live — a captive portal or intercepting proxy (common
            # on public/hotel/train WiFi) can return a real HTTP error
            # response instead of failing the connection outright, which
            # google-genai parses into a genuine ClientError/ServerError —
            # indistinguishable from a real Gemini error UNLESS you check
            # what actually answered. A real Gemini error is always JSON
            # (that's the whole API contract); anything else here means
            # something other than Gemini answered, so it's treated the same
            # as "unreachable." A GENUINE Gemini error (bad key, quota) is
            # always real JSON and must still surface normally, never be
            # masked by silently falling back.
            if yielded_anything or not self._offline_model or _looks_like_real_gemini_response(error):
                raise
            logger.warning(
                "Gemini response wasn't real JSON (likely a captive portal/proxy intercepting "
                "the request, not Gemini) — falling back to local Ollama model %r: %s",
                self._offline_model, error,
            )
            yield from self._stream_ollama(user_input, system_prompt, conversation_history)

    def _stream_alternate_provider(
        self,
        user_input: str,
        tier: Literal["light", "deep"],
        system_prompt: str,
        conversation_history: Optional[list],
    ) -> Iterator[str]:
        """Routes to whichever non-Gemini provider is configured. Buffers the
        full reply before yielding once (rather than yielding per-chunk like
        Gemini does above) and runs it through _ensure_end_tokens — verified
        live that Claude (whether via the CLI or, here, the real Anthropic
        API) reliably omits this wire protocol's <END> delimiter; an
        untested-live third-party model behind an OpenAI-compatible endpoint
        could just as easily share that same formatting gap, and
        _ensure_end_tokens is a no-op if the delimiter's already present, so
        applying it defensively here costs nothing when it isn't needed."""
        if self._ai_provider == "anthropic":
            model = self._anthropic_model if tier == "deep" else self._anthropic_model_light
            reply = "".join(anthropic_backend.stream(
                user_input, system_prompt, model, self._anthropic_api_key,
                conversation_history=conversation_history,
            ))
        elif self._ai_provider == "openai":
            model = self._openai_model if tier == "deep" else self._openai_model_light
            reply = "".join(openai_backend.stream(
                user_input, system_prompt, model, self._openai_api_key, self._openai_base_url,
                conversation_history=conversation_history,
            ))
        else:
            raise ValueError(f"unknown ai_provider: {self._ai_provider!r}")
        yield _ensure_end_tokens(reply)

    def _stream_ollama(
        self, user_input: str, system_prompt: str, conversation_history: Optional[list],
    ) -> Iterator[str]:
        messages = [{"role": "system", "content": system_prompt}]
        for turn in conversation_history or []:
            role = "assistant" if turn.get("role") == "model" else "user"
            text = "".join(part.get("text", "") for part in turn.get("parts", []))
            messages.append({"role": role, "content": text})
        messages.append({"role": "user", "content": user_input})

        response = requests.post(
            f"{self._ollama_host}/api/chat",
            json={"model": self._offline_model, "messages": messages, "stream": True},
            stream=True,
            timeout=60,
        )
        response.raise_for_status()
        for line in response.iter_lines():
            if not line:
                continue
            data = json.loads(line)
            text = data.get("message", {}).get("content", "")
            if text:
                yield text
            if data.get("done"):
                break

    def _load_system_prompt(self, sys_info: str = "", recent_actions: str = "", qiskit_notes: str = "") -> str:
        with open(self._prompt_path, "r") as prompt_file:
            template = Template(prompt_file.read())
        return template.safe_substitute(
            EOC=END_OF_COMMAND_TOKEN, SYS_INFO=sys_info, RECENT_ACTIONS=recent_actions, QISKIT_NOTES=qiskit_notes
        )
