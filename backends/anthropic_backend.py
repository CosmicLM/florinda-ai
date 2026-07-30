"""anthropic_backend.py — routes AI generation through the real Anthropic
Messages API using a user-supplied API key, for people with direct
Anthropic API billing rather than a claude.ai/Claude Code subscription.
See claude_cli_backend.py for the subscription-based route (routes through
the `claude` CLI instead, no separate API key needed) — the two are
independent, alternative ways to reach Claude, not layered on each other.

WHY the real `anthropic` SDK's streaming context manager: verified live
against the real API (with a deliberately invalid key) that
`client.messages.stream(...)` round-trips correctly end-to-end (a real 401
AuthenticationError came back). `.text_stream` yields incremental text
deltas — the same one-chunk-at-a-time shape every other backend in this
codebase already returns.

WHY the refusal check: a refused request returns a normal HTTP 200 with
`stop_reason: "refusal"`, not an exception — silently yielding nothing
would look like an empty reply with no explanation, so it's raised as a
real error instead.
"""
from typing import Iterator, Optional

import anthropic

_DEFAULT_MAX_TOKENS = 4096


class AnthropicBackendError(Exception):
    """Raised when the Anthropic API can't be reached, errors, or refuses the request."""


def stream(
    prompt: str,
    system_prompt: str,
    model: str,
    api_key: str,
    timeout_s: float = 60.0,
    max_tokens: int = _DEFAULT_MAX_TOKENS,
    conversation_history: Optional[list] = None,
) -> Iterator[str]:
    client = anthropic.Anthropic(api_key=api_key, timeout=timeout_s)
    messages = []
    for turn in conversation_history or []:
        role = "assistant" if turn.get("role") == "model" else "user"
        text = "".join(part.get("text", "") for part in turn.get("parts", []))
        messages.append({"role": role, "content": text})
    messages.append({"role": "user", "content": prompt})

    try:
        with client.messages.stream(
            model=model, max_tokens=max_tokens, system=system_prompt, messages=messages
        ) as message_stream:
            yield from message_stream.text_stream
            final_message = message_stream.get_final_message()
        if final_message.stop_reason == "refusal":
            raise AnthropicBackendError("Claude declined to respond to this request")
    except anthropic.APIError as error:
        raise AnthropicBackendError(str(error)) from error


if __name__ == "__main__":
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from config import ConfigVault

    settings = ConfigVault().settings
    if not settings.anthropic_api_key:
        print("Error: FLORA_ANTHROPIC_API_KEY not configured", file=sys.stderr)
        sys.exit(1)
    try:
        for text in stream(
            "Say OK in one word.", "You are a test.", settings.anthropic_model,
            settings.anthropic_api_key, timeout_s=15.0,
        ):
            print(text, end="", flush=True)
        print()
    except AnthropicBackendError as error:
        print(f"Error: {error}", file=sys.stderr)
        sys.exit(1)
