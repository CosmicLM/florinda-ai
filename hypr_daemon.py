"""hypr_daemon.py — The Receptionist: composition root and CLI entry point for Hypr."""
import sys
from string import Template

from colorama import init
from google import genai
from termcolor import colored

from config import ConfigurationError, ConfigVault, NULL_COMMAND
from executor import SystemTerminal
from processor import ParsedInstruction, PromptProcessor
from state_manifest import StateManifest
from voice import AudioEngine

SESSION_PROMPT_PATH = "./SESSION.md"


class HyprDaemon:
    """The Receptionist: wires Hypr's collaborators together and runs one request."""

    def __init__(
        self,
        processor: PromptProcessor,
        terminal: SystemTerminal,
        audio: AudioEngine,
        state: StateManifest,
    ) -> None:
        self._processor = processor
        self._terminal = terminal
        self._audio = audio
        self._state = state
        self._always_execute = False

    def run_daemon(self, user_input: str) -> None:
        """Handle a single user request end-to-end, including recursive follow-ups."""
        if not user_input.strip():
            return
        instruction = self._processor.generate_instruction(user_input)
        self._handle_instruction(instruction)
        self._persist_state(instruction)

    def _handle_instruction(self, instruction: ParsedInstruction) -> None:
        self._speak(instruction.speech)
        output = self._maybe_execute(instruction.command)
        if instruction.recursive:
            self._continue_session(instruction, output)

    def _maybe_execute(self, command: str) -> str | None:
        if command == NULL_COMMAND:
            return None
        return self._confirm_and_run(command)

    def _confirm_and_run(self, command: str) -> str:
        if self._always_execute or self._request_permission(command):
            print(colored(f"Executing:\n{command}", "dark_grey"))
            return self._terminal.run_command(command)
        return "User Decided Not To Execute Command"

    def _request_permission(self, command: str) -> bool:
        answer = input(
            f"The AI Asks Permission To Run Command:\n{colored(command, 'red')}\nProceed? [All/Y/n] "
        )
        if answer == "All":
            self._always_execute = True
        return answer == "Y" or self._always_execute

    def _continue_session(self, instruction: ParsedInstruction, output: str | None) -> None:
        session_prompt = self._build_session_prompt(instruction, output)
        follow_up = self._processor.generate_instruction(session_prompt)
        self._handle_instruction(follow_up)

    def _build_session_prompt(self, instruction: ParsedInstruction, output: str | None) -> str:
        with open(SESSION_PROMPT_PATH, "r") as session_file:
            template = Template(session_file.read())
        return template.safe_substitute(
            INFO=instruction.info,
            COMMAND=instruction.command,
            OUTPUT=output or "No Command Sent",
        )

    def _speak(self, speech: str) -> None:
        print(speech)
        self._audio.stream_vocal_synthesis(speech)

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


def main() -> None:
    init()
    try:
        vault = ConfigVault()
    except ConfigurationError as error:
        print(f"CRITICAL: {error}")
        sys.exit(1)

    client = genai.Client(api_key=vault.settings.api_key)
    processor = PromptProcessor(client, vault.settings.ai_model, vault.settings.ai_model_light)
    terminal = SystemTerminal()
    audio = AudioEngine(vault.settings.voice_model, vault.settings.debug)
    state = StateManifest(vault.settings.state_path)

    daemon = HyprDaemon(processor, terminal, audio, state)
    daemon.run_daemon(" ".join(sys.argv[1:]))


if __name__ == "__main__":
    main()
