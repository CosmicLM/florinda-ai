"""local_brain.py — a local, GPU-offloaded Ollama model for background jobs that
shouldn't cost a cloud API call or wait on the network.

WHY a separate module from processor.py's Gemini-backed PromptProcessor: this
isn't part of the interactive voice conversation and doesn't speak the
COMMAND/SPEECH/RECURSIVE/INFO wire protocol — it's a plain prompt-in,
text-out call used by background jobs like quantum_watcher.py, deliberately
kept far cheaper/simpler than a full AI turn.
"""
import base64
import logging

import requests

logger = logging.getLogger(__name__)


class LocalBrainError(Exception):
    """Raised when the local Ollama model can't be reached or fails to respond."""


class LocalBrain:
    """Thin client for a local Ollama model's /api/generate endpoint."""

    def __init__(self, model: str, host: str = "http://localhost:11434", timeout_s: float = 20.0) -> None:
        self._model = model
        self._host = host.rstrip("/")
        self._timeout_s = timeout_s

    def generate(
        self,
        prompt: str,
        system: str | None = None,
        images: list[bytes] | None = None,
        think: bool | None = None,
    ) -> str:
        payload = {"model": self._model, "prompt": prompt, "stream": False}
        if system:
            payload["system"] = system
        if images:
            # WHY base64 here, not left to the caller: Ollama's /api/generate
            # "images" field wants base64 strings, not raw bytes — this is
            # Ollama's own wire format, not something callers should each
            # have to know/repeat (added for screen_control.py's local,
            # vision-based click lookup).
            payload["images"] = [base64.b64encode(image).decode() for image in images]
        if think is not None:
            # WHY a passthrough, not always-on: only hybrid-reasoning models
            # (e.g. gemma4:e2b) understand "think" at all — verified live,
            # think=False cut a real coordinate-finding call from 73s to
            # 5.7s with no accuracy loss, the same tradeoff Gemini's
            # thinking_budget=0 gave screen_control.py earlier.
            payload["think"] = think
        try:
            response = requests.post(f"{self._host}/api/generate", json=payload, timeout=self._timeout_s)
            response.raise_for_status()
        except requests.RequestException as error:
            raise LocalBrainError(f"local model call failed: {error}") from error
        return response.json().get("response", "").strip()


if __name__ == "__main__":
    import sys

    model = sys.argv[1] if len(sys.argv) > 1 else "phi4-mini"
    brain = LocalBrain(model)
    try:
        reply = brain.generate("Say hello in exactly three words.")
    except LocalBrainError as error:
        print(f"LocalBrain self-check FAILED: {error}", file=sys.stderr)
        sys.exit(1)
    assert reply, "expected a non-empty reply"
    print(f"LocalBrain self-check OK — {model} replied: {reply!r}")
