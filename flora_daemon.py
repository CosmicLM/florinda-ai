"""hypr_daemon.py — The Receptionist: composition root and CLI entry point for Hypr."""
import logging
import socket
import sys
from pathlib import Path
from string import Template
from typing import Callable, Optional

from colorama import init
from google import genai
from termcolor import colored

from config import ConfigurationError, ConfigVault, NULL_COMMAND
from executor import SystemTerminal
from processor import ParsedInstruction, PromptProcessor
from state_manifest import StateManifest
from voice import AudioEngine

logger = logging.getLogger(__name__)

SESSION_PROMPT_PATH = "./SESSION.md"


class HyprDaemon:
    """The Receptionist: wires Hypr's collaborators together and runs one request."""

    def __init__(
        self,
        processor: PromptProcessor,
        terminal: SystemTerminal,
        audio: AudioEngine,
        state: StateManifest,
        confirm_fn: Optional[Callable[[str], bool]] = None,
    ) -> None:
        self._processor = processor
        self._terminal = terminal
        self._audio = audio
        self._state = state
        self._always_execute = False
        self._confirm_fn = confirm_fn or self._interactive_confirm
        self._current_sys_info = ""

    def run_daemon(self, user_input: str, sys_info: str = "") -> None:
        """Handle a single user request end-to-end, including recursive follow-ups."""
        if not user_input.strip():
            return
        self._current_sys_info = sys_info
        instruction = self._generate(user_input, sys_info)
        self._handle_instruction(instruction)
        self._persist_state(instruction)

    def _generate(self, user_input: str, sys_info: str) -> ParsedInstruction:
        """Run one AI turn, speaking each SPEECH sentence over TTS the moment
        streaming produces it, then logging the complete text once generation
        is done.

        WHY print() happens AFTER, not before/interleaved: audio is the
        latency-sensitive channel and starts speaking as soon as the first
        sentence completes, well before the reply finishes generating; the
        terminal/log line has no such stakes, and printing once with the
        complete text keeps logs scannable instead of fragmenting one turn
        into many partial print lines that add noise, not value.
        """
        instruction = self._processor.generate_instruction(
            user_input, sys_info=sys_info, on_speech_chunk=self._speak_chunk
        )
        print(instruction.speech)
        return instruction

    def _speak_chunk(self, sentence: str) -> None:
        """Wraps AudioEngine directly so a TTS-pipeline failure is logged
        accurately here, instead of being swallowed and mislabeled by
        PromptProcessor's broad except Exception as an orchestration failure."""
        try:
            self._audio.stream_vocal_synthesis(sentence)
        except Exception:
            logger.exception("stream_vocal_synthesis failed for a speech chunk")

    def _handle_instruction(self, instruction: ParsedInstruction) -> None:
        output = self._maybe_execute(instruction.command)
        if instruction.recursive:
            self._continue_session(instruction, output)

    def _maybe_execute(self, command: str) -> str | None:
        if command == NULL_COMMAND:
            return None
        return self._confirm_and_run(command)

    def _confirm_and_run(self, command: str) -> str:
        if self._always_execute or self._confirm_fn(command):
            print(colored(f"Executing:\n{command}", "dark_grey"))
            return self._terminal.run_command(command)
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

    def _continue_session(self, instruction: ParsedInstruction, output: str | None) -> None:
        session_prompt = self._build_session_prompt(instruction, output)
        follow_up = self._generate(session_prompt, self._current_sys_info)
        self._handle_instruction(follow_up)

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
            "WARNING: hypr-daemon.service appears to be running. Running this CLI now "
            "may cause overlapping audio output (two independent voice pipelines speaking "
            "at once). Stop it first with 'systemctl --user stop hypr-daemon.service' if "
            "you don't want that, or continue if you understand the risk.",
            "yellow",
        ))

    client = genai.Client(api_key=vault.settings.api_key)
    processor = PromptProcessor(client, vault.settings.ai_model, vault.settings.ai_model_light)
    terminal = SystemTerminal()
    audio = AudioEngine(vault.settings.voice_model, vault.settings.debug)
    state = StateManifest(vault.settings.state_path)

    daemon = HyprDaemon(processor, terminal, audio, state)
    daemon.run_daemon(" ".join(sys.argv[1:]))


if __name__ == "__main__":
    main()
