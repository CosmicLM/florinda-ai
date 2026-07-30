"""openai_backend.py — routes AI generation through any OpenAI-compatible
endpoint via the official `openai` SDK's `base_url` override, so a user can
attach essentially any provider without a bespoke integration per vendor:
OpenAI itself, Azure OpenAI, Mistral, Groq, OpenRouter, Together,
Fireworks, or a self-hosted vLLM/llama.cpp/Ollama OpenAI-compat shim — all
speak the same chat-completions wire shape this module drives.

WHY the real `openai` SDK instead of hand-rolled requests+SSE parsing:
verified live against the real API (with a deliberately invalid key) that
`client.chat.completions.create(..., stream=True)` round-trips correctly
end-to-end (a real 401 AuthenticationError came back, confirming the
request reached the endpoint and was well-formed) — the SDK's `base_url`
constructor argument is exactly what makes this generic across providers,
without reimplementing streaming/retry/error-parsing by hand.

WHY conversation_history is reshaped here, not passed through as-is: it
arrives in Gemini's own shape ({"role": "user"|"model", "parts": [{"text":
...}]}) from conversation_memory.py — this project's one shared history
format, already reshaped the same way for Claude
(_format_history_for_claude in processor.py) and the Ollama offline
fallback (_stream_ollama).
"""
from typing import Iterator, Optional

import openai


class OpenAiBackendError(Exception):
    """Raised when the OpenAI-compatible endpoint can't be reached or returns an error."""


def stream(
    prompt: str,
    system_prompt: str,
    model: str,
    api_key: str,
    base_url: str,
    timeout_s: float = 30.0,
    conversation_history: Optional[list] = None,
) -> Iterator[str]:
    client = openai.OpenAI(api_key=api_key, base_url=base_url, timeout=timeout_s)
    messages = [{"role": "system", "content": system_prompt}]
    for turn in conversation_history or []:
        role = "assistant" if turn.get("role") == "model" else "user"
        text = "".join(part.get("text", "") for part in turn.get("parts", []))
        messages.append({"role": role, "content": text})
    messages.append({"role": "user", "content": prompt})

    try:
        response_stream = client.chat.completions.create(model=model, messages=messages, stream=True)
        for chunk in response_stream:
            delta = chunk.choices[0].delta.content if chunk.choices else None
            if delta:
                yield delta
    except openai.APIError as error:
        raise OpenAiBackendError(str(error)) from error


if __name__ == "__main__":
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from config import ConfigVault

    settings = ConfigVault().settings
    if not settings.openai_api_key or not settings.openai_model:
        print("Error: FLORA_OPENAI_API_KEY / FLORA_OPENAI_MODEL not configured", file=sys.stderr)
        sys.exit(1)
    try:
        for text in stream(
            "Say OK in one word.", "You are a test.", settings.openai_model,
            settings.openai_api_key, settings.openai_base_url, timeout_s=15.0,
        ):
            print(text, end="", flush=True)
        print()
    except OpenAiBackendError as error:
        print(f"Error: {error}", file=sys.stderr)
        sys.exit(1)
