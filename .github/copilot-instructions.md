---
description: "Florinda is a Linux/Hyprland-integrated voice research assistant with pluggable AI backends (Gemini, OpenAI-compatible, Anthropic, Claude CLI, local Ollama). Architecture: voice/text input -> PromptProcessor (streamed COMMAND/SPEECH/RECURSIVE/INFO protocol) -> SystemTerminal (shell) -> AudioEngine (Piper TTS). Handle API keys, the field-streaming parser, confirm-before-execute gating, and recursive session continuation carefully."
---

# Florinda Workspace Instructions

Florinda ("flora" internally — see the naming note in README.md) is a
voice-activated AI research assistant integrated with Hyprland (Linux window
manager), built for hands-free quantum computing research. It processes
voice/text input through a pluggable AI backend, executes commands via a
confirm-gated shell wrapper, and speaks results back over local TTS.

## Two entry points

| File | Runs as | Purpose |
|------|---------|---------|
| `flora_daemon.py` | one-shot CLI (`python3 flora_daemon.py "<query>"`) | Composition root for a single stateless request |
| `flora_service.py` | `flora-daemon.service` (systemd, always-on) | Composition root for push-to-talk, wake-word, screen watching, and all background watchers |

Both build the same collaborators (`PromptProcessor`, `SystemTerminal`,
`AudioEngine`, `ConfigVault`) and hand them to `FloraDaemon` — the service
just adds continuously-running input sources and watchers on top.

## Architecture Overview

```
Input (voice via PTT/wake-word, or CLI text)
    ↓
FloraDaemon.run_daemon()              (flora_daemon.py — "The Receptionist")
    ↓
PromptProcessor.generate_instruction() (processor.py — "The Brain")
    ↓  routes to the configured backend, streams COMMAND:/SPEECH:/RECURSIVE:/INFO: fields
Backend: Gemini (native) | backends/openai_backend.py | backends/anthropic_backend.py
         | backends/claude_cli_backend.py ("deep" tier) | backends/local_brain.py (offline fallback)
    ↓
SystemTerminal.run_command()          (executor.py — "The Hand")
    ↓  if instruction.recursive: build SESSION.md prompt, generate again
AudioEngine.stream_vocal_synthesis()  (voice/voice.py — "The Voice", Piper TTS)
```

A single user request can span multiple recursive "legs": the model can set
`RECURSIVE: true` to keep working (e.g. run a search, read the result, then
answer) — `FloraDaemon._continue_session` re-prompts using `SESSION.md`
until the model returns a non-recursive final answer.

## Core Modules

| File | Purpose |
|------|---------|
| `config.py` | `FloraSettings` (pydantic) + `ConfigVault` — loads `.env`, validates only the credentials the selected `ai_provider` actually needs |
| `flora_daemon.py` | `FloraDaemon` — orchestrates one request end-to-end: confirm-gating, repeat-command blocking, recursive continuation, interrupt/barge-in |
| `flora_service.py` | Always-on composition root: wires push-to-talk, wake-word, screen watching, and every `watchers/*` module into `FloraDaemon` |
| `processor.py` | `PromptProcessor` — builds the prompt from `INSTRUCTION.md`, calls the selected backend, streams and parses the COMMAND/SPEECH/RECURSIVE/INFO protocol |
| `executor.py` | `SystemTerminal` — runs a shell command with the venv's PATH prepended, rejects empty/`null` commands |
| `backends/` | Alternate/deep-tier AI providers — `anthropic_backend.py`, `openai_backend.py`, `claude_cli_backend.py` (subscription `claude` CLI), `local_brain.py` (Ollama, offline fallback + background jobs) |
| `tools/` | Capabilities the model invokes as literal `python3 tools/<name>.py ...` shell commands — web/academic search, app launching, file search, Hyprland control, LaTeX/Qiskit/RDKit runners, skill management, etc. |
| `watchers/` | Background monitors used only by `flora_service.py` — screen OCR, quantum-keyword triggers, task log watching, morning briefing, self fact-checking, thermal/system health |
| `voice/` | Push-to-talk IPC, mic recording, faster-whisper STT, wake-word listening, Piper TTS streaming |
| `infra/` | Cross-cutting always-on-service state — conversation memory, activity log, Waybar status broadcasting, cached screen-OCR text |

## Critical Patterns & Conventions

### 1. Configuration Management
- All settings load through `config.py`'s `FloraSettings`/`ConfigVault` — never read `os.environ` directly elsewhere.
- `FLORA_AI_PROVIDER` selects `gemini` (default), `openai`, or `anthropic`; only that provider's credential is required (see `_require_selected_provider_credentials`).
- `config.py` is a pure library: it never prints or calls `sys.exit()` — only the entry points (`flora_daemon.py`/`flora_service.py`) do that, via `ConfigurationError`.

### 2. Response Format (AI → Execution)
The model is instructed (`INSTRUCTION.md`) to stream fields in this fixed order, each terminated by a literal `<END>` token:
```
COMMAND:<...><END>SPEECH:<...><END>RECURSIVE:<true|false><END>INFO:<...><END>
```
- Parsed incrementally by `processor.py`'s streaming parser so `SPEECH:` can start playing over TTS before `INFO:` has even arrived.
- `claude_cli_backend.py` doesn't reliably emit `<END>`; `_ensure_end_tokens()` splices it in deterministically rather than re-prompting and hoping.
- `COMMAND: null` (the `NULL_COMMAND` sentinel) means "no command to run."

### 3. Command Execution Safety
- `SystemTerminal.run_command()` (executor.py) rejects empty/`null` commands, then runs everything else via `subprocess.run(shell=True, ...)`.
- `FloraDaemon._confirm_and_run` gates on a `confirm_fn`: any command containing `sudo` always asks first; everything else auto-executes (a deliberate design choice — see the `_SUDO_TOKEN_RE` comment in `flora_daemon.py`, not an oversight to "fix").
- `_check_repeat_block` hard-blocks re-running the literal same command within 90s, independent of whether the model heeds the recent-actions context it's given.
- **Never bypass these checkpoints** when extending execution logic.

### 4. Voice Synthesis (Piper TTS)
- `voice/voice.py`'s `AudioEngine` streams sentences to Piper as they complete, not after the full reply finishes generating.
- Subprocess pipe to `aplay`; **`shell=True` requires sanitized input** — only trusted, already-parsed text reaches this path.

### 5. Error Handling
- Backend/API errors: caught and reported as speech, never a raw traceback to the user.
- Missing/invalid config: fails fast with `ConfigurationError` at startup, no partial operation.
- Command execution: stderr is returned as the command's output for visibility, not swallowed.

## Development Workflow

### Adding a New Tool (`tools/*.py`)
1. Write it as a standalone script invoked via `python3 tools/<name>.py <args>` — the model calls it exactly like any other shell command.
2. Document it in `INSTRUCTION.md` so the model knows it exists and how to call it.
3. Keep real, working mechanics — no stubbed-out "TODO: implement the actual logic" for the part the tool exists to do.

### Adding a New Backend (`backends/*.py`)
1. Match the existing backends' call shape so `processor.py` can route to it uniformly.
2. Add its settings/credentials to `config.py`'s `FloraSettings` with a conditional-required validator, not an unconditionally-required field.
3. Wire the new `ai_provider` value into `flora_daemon.py`'s/`flora_service.py`'s client construction.

### Adding a New Watcher (`watchers/*.py`)
1. Watchers only run under `flora_service.py`, never the one-shot CLI — they need the always-on process.
2. Gate expensive checks behind a cheap deterministic pre-filter (see `quantum_watcher.py`'s keyword regex) rather than calling the model just to decide whether to call the model.
3. Give it its own `*_enabled`/`*_cooldown_s` settings in `config.py` so it can be tuned or disabled independently.

## Common Pitfalls

| Pitfall | Avoid | Instead |
|---------|-------|---------|
| Hardcoding API keys | `API_KEY = "sk-..."` | Route through `config.py`/`.env` |
| Hardcoding this dev machine's paths | A literal `/home/<user>/...` in a tool invocation example or comment | Use `$PROJECT_DIR` (INSTRUCTION.md) / `Path(__file__).resolve().parent` (Python) |
| Trusting the field protocol is well-formed | Assuming `<END>` always appears | Route through the streaming parser; see `_ensure_end_tokens` for a real formatting-quirk fix |
| Shell injection | Passing unsanitized text into a `shell=True` call | Keep to the existing tool-invocation pattern; quote/escape anything user-derived |
| Skipping the repeat/confirm gates | Calling `subprocess.run` directly for a new feature | Go through `SystemTerminal.run_command()` so the existing safety checkpoints apply |
| Blocking the recursive/interrupt state machine | Adding a new long-running step with no `interrupt_event` check | Check `self._interrupt_event.is_set()` at natural break points, like existing code does |

## Useful Context

- **Branch**: `florinda-dev` (default: `main`)
- **Python minimum**: 3.8+ (developed against 3.14)
- **Pluggable AI providers**: Gemini (native `google-genai`), any OpenAI-compatible endpoint, native Anthropic API, or the user's own `claude` CLI subscription for the "deep" reasoning tier
- **Hyprland**: push-to-talk keybind and window/workspace control (`tools/hyprland_bridge.py`) are Hyprland-specific; screen watching falls back to the XDG desktop portal on other desktops (see README.md)
