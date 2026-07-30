"""claude_cli_backend.py — routes Florinda's "deep" reasoning tier through the
user's own Claude subscription via the `claude` CLI (Claude Code) itself,
instead of a separate, pay-per-token Anthropic API key.

WHY the CLI instead of the Messages API: a claude.ai Pro/Max subscription and
Anthropic API access are billed and authenticated completely separately — a
subscription grants no API credits. The `claude` CLI, however, already
authenticates via the user's existing subscription login (OAuth) and can be
invoked non-interactively, genuinely running against the subscription's
usage window rather than metered API billing — verified live: no
ANTHROPIC_API_KEY is set anywhere in this environment, and a real
invocation's rate_limit_event showed `rateLimitType: "five_hour"`, a
subscription-style rolling usage window, not per-request billing.

WHY --tools "" --disable-slash-commands: without them, the CLI runs its full
agentic harness — verified live, a plain question caused it to spontaneously
invoke its own Skill tool (loading quantum-personality, unprompted) and take
12+ seconds across multiple round-trips. These flags strip that down to a
single clean text completion — the same shape every other backend in this
codebase already returns — without needing --bare (which forces
API-key-only auth and would defeat the entire point of using this backend).

WHY non-streaming (--output-format json), not stream-json: the deep tier
already accepts slower, more deliberate answers as a tradeoff for better
reasoning (see processor.py's tier routing) — a single subprocess.run with a
real timeout is far simpler and safer than hand-building a streaming-reader
with its own timeout-enforcement logic, and matches local_brain.py's own
non-streaming LocalBrain.generate() exactly.

WHY this is reserved for "deep" tier only, not a general Gemini replacement:
verified live, even in this stripped-down mode, ttft was ~1.3s and a
genuinely detailed answer took ~9s total — much slower than Gemini's light
tier (~0.8s). Acceptable for the harder questions "deep" tier already exists
for, not for everyday quick replies.

WHY `cancel_event` uses a background reader thread + poll loop, not a plain
subprocess.run(timeout=...): reported live — a new push-to-talk press (which
sets FloraDaemon's interrupt event) did nothing while a deep-tier reply was
being generated through this backend; barge-in only took effect once the
whole CLI call finished on its own. subprocess.run() blocks until the
process exits with no way to check anything mid-call. This polls
cancel_event (and the timeout) every _POLL_INTERVAL_S instead, killing the
CLI's entire process group the moment either fires — matching how
voice.py's AudioEngine.interrupt() already kills its own pipeline group, and
why start_new_session=True is needed here too (the `claude` CLI may spawn
its own children; plain terminate() wouldn't reach them). The actual
stdout/stderr read happens on a separate thread via communicate() so a
large reply can't deadlock the poll loop against a full OS pipe buffer.
"""
import json
import logging
import os
import signal
import subprocess
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)

_POLL_INTERVAL_S = 0.15


class ClaudeCliError(Exception):
    """Raised when the `claude` CLI can't run, times out, or returns an error."""


class ClaudeCliCancelled(ClaudeCliError):
    """Raised when `cancel_event` fired mid-call — the caller asked to stop,
    this is not a backend failure and should not trigger a Gemini fallback."""


def _kill_process_group(process: subprocess.Popen) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def generate(
    prompt: str,
    system_prompt: str,
    model: Optional[str] = None,
    timeout_s: float = 60.0,
    cancel_event: Optional[threading.Event] = None,
) -> str:
    command = [
        "claude", "-p", prompt,
        "--system-prompt", system_prompt,
        "--tools", "",
        "--disable-slash-commands",
        "--output-format", "json",
    ]
    if model:
        command += ["--model", model]
    try:
        process = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            start_new_session=True,  # own process group, so a cancel/timeout can kill it and any children
        )
    except FileNotFoundError as error:
        raise ClaudeCliError(f"claude CLI not found: {error}") from error

    output: dict = {}

    def _read() -> None:
        output["stdout"], output["stderr"] = process.communicate()

    reader = threading.Thread(target=_read, daemon=True)
    reader.start()

    deadline = time.monotonic() + timeout_s
    while reader.is_alive():
        if cancel_event is not None and cancel_event.is_set():
            _kill_process_group(process)
            reader.join(timeout=2)
            raise ClaudeCliCancelled("claude CLI call cancelled (interrupted by new push-to-talk press)")
        if time.monotonic() >= deadline:
            _kill_process_group(process)
            reader.join(timeout=2)
            raise ClaudeCliError(f"claude CLI exceeded {timeout_s}s")
        reader.join(timeout=_POLL_INTERVAL_S)

    if process.returncode != 0:
        raise ClaudeCliError(f"claude CLI exited {process.returncode}: {output.get('stderr', '').strip()}")
    stdout = output.get("stdout", "")
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError as error:
        raise ClaudeCliError(f"couldn't parse claude CLI output: {stdout[:500]!r}") from error
    if data.get("is_error"):
        raise ClaudeCliError(data.get("result", "unknown error"))
    return data.get("result", "")


if __name__ == "__main__":
    import sys

    try:
        reply = generate("Say hello in exactly three words.", system_prompt="You are a terse assistant.")
    except ClaudeCliError as error:
        print(f"ClaudeCliBackend self-check FAILED: {error}", file=sys.stderr)
        sys.exit(1)
    assert reply.strip(), "expected a non-empty reply"
    print(f"ClaudeCliBackend self-check OK — replied: {reply!r}")
