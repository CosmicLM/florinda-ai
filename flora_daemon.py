"""flora_daemon.py — The Receptionist: composition root and CLI entry point for Florinda."""
import logging
import re
import socket
import sys
import threading
import time
from pathlib import Path
from string import Template
from typing import Callable, Optional

from colorama import init
from google import genai
from termcolor import colored

from infra.activity_log import ActivityLog
from config import ConfigurationError, ConfigVault, NULL_COMMAND
from infra.conversation_memory import ConversationMemory
from executor import SystemTerminal
from processor import ParsedInstruction, PromptProcessor
from state_manifest import StateManifest
from voice.voice import AudioEngine

logger = logging.getLogger(__name__)

SESSION_PROMPT_PATH = "./SESSION.md"

# WHY this replaced the old curated allowlist (per-tool auto-safe sets,
# shell-chaining rejection, etc.): the user explicitly asked for any command
# that doesn't need sudo to run without confirmation, full stop — not just a
# growing list of specifically-verified-safe actions. Under that rule the old
# fine-grained classifier became dead weight (every command it used to
# reject for "not on the list" is now allowed anyway), so it was removed
# rather than left around unused. `pkexec` is unaffected by this — it never
# needed Florinda's own confirm gate to begin with, since its own mandatory
# polkit password dialog is the real, OS-enforced gate; it just happens to
# also not match `sudo` below, so it needs no special case anymore either.
# Bare `sudo` remains "gated" only in the sense that _confirm_fn still runs
# for it — in practice it fails immediately with no TTY regardless (see
# INSTRUCTION.md's Elevated Commands section), so this is a defensive/
# symbolic line, not something a real workflow depends on.
_SUDO_TOKEN_RE = re.compile(r"\bsudo\b")

# WHY these exist: observed live — asked to "summarize" a Canvas quiz page
# right after already opening it, the model had no ground truth about what
# it had just tried and silently re-ran the identical open-url command,
# describing a vague restatement as if it were new information. Telling the
# model "don't repeat yourself" in prose alone is easy to ignore under
# uncertainty; these two constants back that instruction with real,
# code-enforced state instead of relying on the model to reconstruct it.
_RECENT_ACTIONS_WINDOW_S = 300.0  # how long a command stays visible to the model as "recently done"
_RECENT_ACTIONS_MAX = 3  # how many recent commands to keep/show
_COMMAND_REPEAT_BLOCK_S = 90.0  # hard refuse to re-run the literal same command within this window

# WHY this exists: observed live — the model's training-data knowledge of
# the Qiskit API doesn't match this machine's actually-installed, pinned
# environment (qiskit 2.4.1 / qiskit-aer 0.17.2). Two confirmed real
# failures in one evening: `from qiskit.primitives import Sampler`
# (ImportError — that class lives in qiskit_aer.primitives on this
# version) and `.c_if(...)` on a gate (AttributeError — removed; replaced
# by the `with qc.if_test(...):` context-manager form). Rather than only
# hoping the model remembers this correctly next time (or reaches for
# qiskit_docs.py's on-demand lookup unprompted, which it may not think to
# do), this is injected automatically as $QISKIT_NOTES — but ONLY on turns
# that look Qiskit-related, so it isn't dead weight on every other request.
_QISKIT_KEYWORD_RE = re.compile(r"\b(qiskit|quantum circuit|qubit|quantum gate)\b", re.IGNORECASE)
_QISKIT_NOTES = """Verified against the REAL installed environment (qiskit 2.4.1, qiskit-aer 0.17.2) — these are known-recurring mistakes, not general Qiskit knowledge:
- `from qiskit.primitives import Sampler` (or Estimator) FAILS on this install — those names don't exist there anymore. For Aer-backed sampling use `from qiskit_aer.primitives import Sampler, SamplerV2`.
- For the common case of "run this circuit and get counts", skip primitives entirely — the simplest working pattern is: `from qiskit_aer import AerSimulator` then `AerSimulator().run(circuit, shots=1024).result().get_counts()`.
- `some_gate_call(...).c_if(...)` FAILS on this install (AttributeError — removed from InstructionSet). For classically-conditioned gates, use the context-manager form instead: `with qc.if_test((creg_or_bit, value)): qc.x(1)` — the conditioned gate(s) go inside the `with` block.
- Both `Aer.get_backend('qasm_simulator')` and `AerSimulator()` work fine on this install — no need to guess between them.
- Not sure about something else? Run `python3 /home/manjaro/Projects/flora-ai/tools/qiskit_docs.py search <keyword>` or `... lookup <dotted.path>` first — it reads the real installed package, not a guess."""


def _is_auto_safe_command(command: str) -> bool:
    return not _SUDO_TOKEN_RE.search(command)


class FloraDaemon:
    """The Receptionist: wires Florinda's collaborators together and runs one request."""

    def __init__(
        self,
        processor: PromptProcessor,
        terminal: SystemTerminal,
        audio: AudioEngine,
        state: StateManifest,
        confirm_fn: Optional[Callable[[str], bool]] = None,
        memory: Optional[ConversationMemory] = None,
        activity_log: Optional[ActivityLog] = None,
        on_thinking: Optional[Callable[[], None]] = None,
        on_talking: Optional[Callable[[], None]] = None,
    ) -> None:
        self._processor = processor
        self._terminal = terminal
        self._audio = audio
        self._state = state
        self._always_execute = False
        self._confirm_fn = confirm_fn or self._interactive_confirm
        self._current_sys_info = ""
        # WHY these default to no-ops, not None-checked at each call site:
        # the CLI has no status widget to update, so it just never gets
        # these — same "optional for the service, absent for the CLI"
        # pattern as memory/activity_log below, but as callbacks instead of
        # objects since FloraDaemon has no business knowing StatusBroadcaster
        # exists, only that "something might want to know when I start
        # thinking/talking."
        self._on_thinking = on_thinking or (lambda: None)
        self._on_talking = on_talking or (lambda: None)
        self._spoken_this_leg = False
        # WHY default None: a one-shot CLI invocation is a deliberately fresh
        # call, not a continuation of a voice conversation. Only the
        # always-on service's composition root (flora_service.py) passes a
        # real ConversationMemory — the CLI stays stateless exactly as before.
        self._memory = memory
        # Same default-None reasoning as memory above — only the always-on
        # service feeds the click-to-view Waybar transcript; the CLI's own
        # stdout already IS its transcript.
        self._activity_log = activity_log
        # WHY tracked here (not in ConversationMemory): this is about
        # concrete tool actions and their real results, not conversational
        # turns — see _format_recent_actions and _maybe_execute.
        self._recent_commands: list[tuple[str, str, float]] = []  # (command, output, monotonic_time)
        # Set by interrupt() the instant a new push-to-talk press comes in
        # (from a DIFFERENT thread than the one running run_daemon — see
        # ptt_ipc.py's on_press callback), so a still-generating/still-
        # speaking turn can notice and bail out fast instead of finishing.
        self._interrupt_event = threading.Event()

    def interrupt(self) -> None:
        """Barge-in: stop whatever Florinda is currently saying or generating,
        right now, so the user can start talking again without waiting for
        her to finish. Safe to call even when nothing is in flight — the
        next run_daemon() call always clears this event before it does
        anything else."""
        self._interrupt_event.set()
        self._audio.interrupt()

    def run_daemon(self, user_input: str, sys_info: str = "") -> None:
        """Handle a single user request end-to-end, including recursive follow-ups."""
        if not user_input.strip():
            return
        self._interrupt_event.clear()
        self._current_sys_info = sys_info
        if self._activity_log:
            self._activity_log.write("YOU", user_input)
        history = self._memory.get_history_contents() if self._memory else None
        instruction = self._generate(user_input, sys_info, conversation_history=history)
        if self._interrupt_event.is_set():
            # The user started talking again mid-response — the AI's reply
            # here may be truncated mid-field; abandon it entirely rather
            # than execute a partial/garbled command or remember it.
            return
        final_instruction = self._handle_instruction(instruction)
        self._persist_state(instruction)
        self._remember_turn(user_input, final_instruction)

    def _generate(
        self, user_input: str, sys_info: str, conversation_history: Optional[list] = None
    ) -> ParsedInstruction:
        """Run one AI turn, speaking each SPEECH sentence over TTS the moment
        streaming produces it, then logging the complete text once generation
        is done.

        WHY print() happens AFTER, not before/interleaved: audio is the
        latency-sensitive channel and starts speaking as soon as the first
        sentence completes, well before the reply finishes generating; the
        terminal/log line has no such stakes, and printing once with the
        complete text keeps logs scannable instead of fragmenting one turn
        into many partial print lines that add noise, not value.

        WHY on_thinking() fires here unconditionally: this is called once
        per leg of a reply — the very first call, and again after every
        recursive continuation/command execution. Marking "thinking" at the
        start of EVERY leg (not just the first) is what stops the status
        widget from reading "talking" all the way through a slow command or
        a multi-step recursive chain when no audio is actually playing
        during that time — observed live, a Qiskit run (which can take up
        to 2 minutes) left the widget stuck on "talking" for its entire
        duration despite no speech happening.
        """
        self._transition_to_thinking()
        self._spoken_this_leg = False
        instruction = self._processor.generate_instruction(
            user_input,
            sys_info=sys_info,
            recent_actions=self._format_recent_actions(),
            qiskit_notes=self._format_qiskit_notes(user_input),
            on_speech_chunk=self._speak_chunk,
            conversation_history=conversation_history,
            cancel_event=self._interrupt_event,
        )
        print(instruction.speech)
        if self._activity_log:
            self._activity_log.write("FLORINDA", instruction.speech)
        return instruction

    def _remember_turn(self, user_input: str, final_instruction: ParsedInstruction) -> None:
        """WHY only the final instruction of a recursive chain is recorded as
        the assistant turn: intermediate recursive prompts are built from
        SESSION_PROMPT_PATH scaffolding, not real conversation — what matters
        for "what did we just talk about" is what the user asked and what
        Florinda ultimately said back."""
        if self._memory is None:
            return
        self._memory.add_user_turn(user_input)
        self._memory.add_assistant_turn(final_instruction.speech)

    def _speak_chunk(self, sentence: str) -> None:
        """Wraps AudioEngine directly so a TTS-pipeline failure is logged
        accurately here, instead of being swallowed and mislabeled by
        PromptProcessor's broad except Exception as an orchestration failure.

        WHY the interrupt check here too, not just in generate_instruction's
        streaming loop: a sentence that was already mid-flight when interrupt()
        fired would otherwise still get spoken into the freshly-respawned
        pipeline — this closes that last small race window.

        WHY on_talking() fires here, on the FIRST real sentence, rather than
        at the top of _generate alongside on_thinking(): COMMAND/RECURSIVE/
        INFO stream in silently before any SPEECH text exists — flipping to
        "talking" only once there's an actual sentence to speak is what
        makes the status genuinely track audio, not just "a turn started".
        """
        if self._interrupt_event.is_set():
            return
        if not self._spoken_this_leg:
            self._spoken_this_leg = True
            self._on_talking()
        try:
            self._audio.stream_vocal_synthesis(sentence)
        except Exception:
            logger.exception("stream_vocal_synthesis failed for a speech chunk")

    def _handle_instruction(self, instruction: ParsedInstruction) -> ParsedInstruction:
        output = self._maybe_execute(instruction.command)
        if instruction.recursive:
            return self._continue_session(instruction, output)
        return instruction

    def _maybe_execute(self, command: str) -> str | None:
        if command == NULL_COMMAND:
            return None
        blocked = self._check_repeat_block(command)
        if blocked is not None:
            return blocked
        output = self._confirm_and_run(command)
        self._recent_commands.append((command, output, time.monotonic()))
        self._recent_commands = self._recent_commands[-_RECENT_ACTIONS_MAX:]
        return output

    def _check_repeat_block(self, command: str) -> Optional[str]:
        """Hard, code-enforced backstop against re-running the literal same
        command seconds after it already ran — deliberately separate from
        _format_recent_actions below, which only gives the model a chance to
        avoid this on its own. This guarantees it regardless of whether the
        model actually reads/heeds that context."""
        now = time.monotonic()
        for prior_command, _output, ran_at in reversed(self._recent_commands):
            if now - ran_at >= _COMMAND_REPEAT_BLOCK_S:
                break
            if prior_command == command:
                logger.info("Blocking immediate repeat of command: %r", command)
                return (
                    f"[SYSTEM: this exact command already ran less than {_COMMAND_REPEAT_BLOCK_S:.0f}s ago "
                    "— see RECENT_ACTIONS. It was NOT run again. Nothing about the situation has changed, "
                    "so re-running it would not produce anything new — tell the user what you actually "
                    "know instead.]"
                )
        return None

    def _format_recent_actions(self) -> str:
        """Deterministic ground truth about what was ACTUALLY just run and
        what it ACTUALLY returned, injected into every turn's prompt as
        $RECENT_ACTIONS (see INSTRUCTION.md's Recent Actions section).

        WHY this exists: observed live — asked to "summarize" a Canvas quiz
        page right after already opening it, the model had no real page
        content (Canvas is login-walled; web_clip.py returns near-empty
        output for it) and nothing telling it what it had just tried, so it
        silently re-ran the same open-url command and described a vague
        restatement as if it were new information. Handing it the literal
        command + literal output it just got — computed here, not reasoned
        about by the model — is something it can act on directly instead of
        re-deriving "have I already done this?" from scratch every turn.
        """
        now = time.monotonic()
        fresh = [
            (command, output, ran_at)
            for command, output, ran_at in self._recent_commands
            if now - ran_at < _RECENT_ACTIONS_WINDOW_S
        ]
        self._recent_commands = fresh
        if not fresh:
            return ""
        lines = []
        for command, output, ran_at in fresh:
            age = int(now - ran_at)
            preview = " ".join((output or "").split())[:200]
            lines.append(f"- {age}s ago: ran `{command}` -> {preview!r}")
        return "\n".join(lines)

    @staticmethod
    def _format_qiskit_notes(user_input: str) -> str:
        """Returns _QISKIT_NOTES if `user_input` looks Qiskit-related,
        otherwise "" — see the WHY comment on _QISKIT_NOTES above. Gated on
        the raw user_input text (which, on a recursive-continuation turn, is
        SESSION.md's own prompt embedding the prior COMMAND/INFO — those
        already contain the word "qiskit" whenever the chain started from a
        Qiskit request, so this naturally stays active across the chain
        without extra plumbing)."""
        return _QISKIT_NOTES if _QISKIT_KEYWORD_RE.search(user_input) else ""

    def _transition_to_thinking(self) -> None:
        """WHY wait_until_done() first: on_thinking() flips the status widget
        away from "talking" — but stream_vocal_synthesis is fire-and-forget,
        so text already handed to the pipeline may still be audibly playing
        even after generation/command-dispatch has moved on. Observed live:
        the widget went to "thinking"/idle while Florinda was still speaking.
        Waiting for the estimated playback to catch up first means the
        status transition actually lines up with when speech really stops.
        """
        self._audio.wait_until_done()
        self._on_thinking()

    def _confirm_and_run(self, command: str) -> str:
        if self._always_execute or _is_auto_safe_command(command) or self._confirm_fn(command):
            self._transition_to_thinking()  # running a command is never "talking", however long it takes
            print(colored(f"Executing:\n{command}", "dark_grey"))
            if self._activity_log:
                self._activity_log.write("COMMAND", command)
            output = self._terminal.run_command(command)
            if self._activity_log:
                self._activity_log.write("OUTPUT", output[:1000])
            return output
        return "User Decided Not To Execute Command"

    def _interactive_confirm(self, command: str) -> bool:
        """Default confirm strategy for the CLI: block on a TTY prompt.

        WHY a separate method: the always-on service (no attached terminal)
        injects a different, non-blocking confirm_fn instead — this one stays
        the default so the CLI's behavior is unchanged.
        """
        answer = input(
            f"The AI Asks Permission To Run Command:\n{colored(command, 'red')}\nProceed? [All/Y/n] "
        )
        if answer == "All":
            self._always_execute = True
        return answer == "Y" or self._always_execute

    def enable_always_execute(self) -> None:
        """Voice mode's equivalent of the CLI's "All" answer — stops asking
        for confirmation on every command for the rest of this process's
        life. Same underlying flag the CLI's "All" answer sets, just reached
        via a different trigger since voice mode has no input() prompt."""
        self._always_execute = True

    def _continue_session(self, instruction: ParsedInstruction, output: str | None) -> ParsedInstruction:
        session_prompt = self._build_session_prompt(instruction, output)
        follow_up = self._generate(session_prompt, self._current_sys_info)
        if self._interrupt_event.is_set():
            # Same reasoning as run_daemon's own top-level check — this one
            # was missing here, which is exactly why it mattered: observed
            # live, a new PTT press correctly set the interrupt flag, but a
            # RECURSIVE chain already in flight kept generating through two
            # more full turns (each a real Gemini round-trip) before the
            # voice-worker thread ever got back to processing the new press
            # — 3.6+ seconds of the widget looking stuck on idle while the
            # user was already holding the button for the next thing.
            return follow_up
        return self._handle_instruction(follow_up)

    def _build_session_prompt(self, instruction: ParsedInstruction, output: str | None) -> str:
        with open(SESSION_PROMPT_PATH, "r") as session_file:
            template = Template(session_file.read())
        return template.safe_substitute(
            INFO=instruction.info,
            COMMAND=instruction.command,
            OUTPUT=output or "No Command Sent",
        )

    def _persist_state(self, instruction: ParsedInstruction) -> None:
        """Save the last turn's outcome so it survives past this CLI invocation.

        WHY: this is a light touch point, not a rewrite of the AI's context
        handling — it just proves state round-trips to disk. Feeding this back
        into future prompts (the spec's "implicit caching" story) is separate,
        larger work left for later.
        """
        state = self._state.load()
        session_count = state.get("session_count", 0) + 1
        self._state.save({
            "session_count": session_count,
            "last_speech": instruction.speech,
            "last_command": instruction.command,
            "last_info": instruction.info,
        })


def _service_is_running(socket_path: Path) -> bool:
    """WHY: the CLI and the always-on service each spawn their own independent
    AudioEngine/piper-tts pipeline. Running both at once has no coordination
    over who "owns" the speakers, so their spoken output can land at the same
    time and audibly overlap — this is what a real running-both-at-once
    incident sounded like, not a TTS/voice-model bug. Checked by trying to
    connect to the service's own PTT socket, not just checking the file
    exists (a crashed service can leave a stale socket file behind).
    """
    if not socket_path.exists():
        return False
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.2)
            sock.connect(str(socket_path))
        return True
    except OSError:
        return False


def main() -> None:
    init()
    try:
        vault = ConfigVault()
    except ConfigurationError as error:
        print(f"CRITICAL: {error}")
        sys.exit(1)

    if _service_is_running(vault.settings.ptt_socket_path):
        print(colored(
            "WARNING: flora-daemon.service appears to be running. Running this CLI now "
            "may cause overlapping audio output (two independent voice pipelines speaking "
            "at once). Stop it first with 'systemctl --user stop flora-daemon.service' if "
            "you don't want that, or continue if you understand the risk.",
            "yellow",
        ))

    # WHY only built when ai_provider is actually "gemini": verified live —
    # genai.Client(api_key=None) raises ValueError immediately at
    # construction, not lazily on first use. A user running purely on
    # FLORA_AI_PROVIDER=openai/anthropic (the whole point of attaching a
    # different provider's key — see config.py's conditional validator) has
    # no Gemini key configured, so building this unconditionally would
    # crash startup before PromptProcessor ever got a chance to route
    # around Gemini entirely.
    client = None
    if vault.settings.ai_provider == "gemini":
        client = genai.Client(
            api_key=vault.settings.api_key,
            # WHY retry_options attempts=1: google-genai defaults to 5 retries
            # with exponential backoff (up to 60s delay each) — observed live,
            # that turned a 10s timeout into a 52s+ real-world wait before the
            # offline fallback ever got a chance to run. A genuinely dead
            # network won't be fixed by retrying, so failing fast (once) here
            # is what actually lets the timeout above mean what it says.
            http_options={
                "timeout": int(vault.settings.gemini_timeout_s * 1000),
                "retry_options": {"attempts": 1},
            },
        )
    processor = PromptProcessor(
        client, vault.settings.ai_model, vault.settings.ai_model_light,
        offline_model=vault.settings.offline_fallback_model if vault.settings.offline_fallback_enabled else None,
        ollama_host=vault.settings.ollama_host,
        use_claude_cli_for_deep=vault.settings.use_claude_cli_for_deep,
        claude_cli_timeout_s=vault.settings.claude_cli_timeout_s,
        ai_provider=vault.settings.ai_provider,
        openai_api_key=vault.settings.openai_api_key,
        openai_base_url=vault.settings.openai_base_url,
        openai_model=vault.settings.openai_model,
        openai_model_light=vault.settings.openai_model_light,
        anthropic_api_key=vault.settings.anthropic_api_key,
        anthropic_model=vault.settings.anthropic_model,
        anthropic_model_light=vault.settings.anthropic_model_light,
    )
    terminal = SystemTerminal()
    audio = AudioEngine(vault.settings.voice_model, vault.settings.debug)
    state = StateManifest(vault.settings.state_path)

    daemon = FloraDaemon(processor, terminal, audio, state)
    daemon.run_daemon(" ".join(sys.argv[1:]))


if __name__ == "__main__":
    main()
