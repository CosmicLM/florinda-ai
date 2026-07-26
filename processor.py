"""processor.py — The Brain: turns user input into a structured instruction from the AI."""
import logging
from dataclasses import dataclass
from string import Template
from typing import Literal, Optional

from config import NULL_COMMAND

logger = logging.getLogger(__name__)

DEFAULT_PROMPT_PATH = "./INSTRUCTION.md"
END_OF_COMMAND_TOKEN = "<END>"


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
        self, user_input: str, tier: Literal["light", "deep"] = "light"
    ) -> ParsedInstruction:
        """Query the AI for `user_input`; a failed turn still returns something speakable."""
        try:
            raw_text = self._query_model(user_input, tier)
            return self._parse_response(raw_text)
        except Exception:
            logger.exception("Hypr orchestration failed for input: %r", user_input)
            return ParsedInstruction(speech="An error occurred. Please check the logs.")

    def _query_model(self, user_input: str, tier: Literal["light", "deep"]) -> str:
        model = self._ai_model if tier == "deep" else self._ai_model_light
        system_prompt = self._load_system_prompt()
        response = self._client.models.generate_content(
            model=model,
            contents=user_input,
            config={"system_instruction": system_prompt},
        )
        return getattr(response, "text", "") or ""

    def _load_system_prompt(self) -> str:
        with open(self._prompt_path, "r") as prompt_file:
            template = Template(prompt_file.read())
        return template.safe_substitute(EOC=END_OF_COMMAND_TOKEN, SYS_INFO="")

    def _parse_response(self, raw_text: str) -> ParsedInstruction:
        """Delegate field-splitting to a helper so this stays a pure dispatcher."""
        if END_OF_COMMAND_TOKEN not in raw_text:
            return ParsedInstruction(speech=raw_text.strip())
        command, speech, recursive, info = self._split_instruction_fields(raw_text)
        return ParsedInstruction(
            speech=speech,
            command=command or NULL_COMMAND,
            recursive=recursive == "Y",
            info=info,
        )

    @staticmethod
    def _split_instruction_fields(raw_text: str) -> tuple[str, str, str, str]:
        parts = raw_text.split(END_OF_COMMAND_TOKEN, 3)
        command = parts[0].replace("COMMAND:", "").strip()
        speech = parts[1].replace("SPEECH:", "").strip()
        recursive = parts[2].replace("RECURSIVE:", "").strip()
        info = parts[3].replace("INFO:", "").strip()
        return command, speech, recursive, info
