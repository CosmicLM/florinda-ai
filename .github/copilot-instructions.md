---
description: "Hypr-AI is a Hyprland-integrated voice assistant. Architecture: voice → PromptProcessor (Gemini API) → SystemTerminal (shell) → HyprCore (piper-tts). Handle API keys, command parsing (COMMAND:|SPEECH:), error safety, and subprocess integration carefully."
---

# Hypr-AI Workspace Instructions

Hypr-AI is a voice-activated AI research assistant that integrates with Hyprland (Linux window manager). It processes voice/text input through Google Gemini API and executes commands via a safe shell wrapper.

## Architecture Overview

```
User Input (voice/text)
    ↓
PromptProcessor (Gemini API query)
    ↓
Response Parsing (COMMAND:|SPEECH: format)
    ↓
SystemTerminal (safe shell execution)
    ↓
HyprCore.speak() (piper-tts audio output)
```

## Core Modules

| File | Purpose | Key Responsibility |
|------|---------|-------------------|
| `config.py` | Environment & constants | API keys, model selection, `NULL_COMMAND` constant |
| `hypr_daemon.py` | Entry point | Orchestrates processor, core, user input flow |
| `processor.py` | PromptProcessor class | Gemini API calls, response parsing, command/speech extraction |
| `voice.py` | HyprCore class | Text-to-speech via piper-tts, subprocess pipe to aplay |
| `executor.py` | SystemTerminal class | Safe command execution with null-check and error handling |

## Critical Patterns & Conventions

### 1. Configuration Management
- **Environment variables** required: `HYPR_API_KEY`, `DEFAULT_VOICE_MODEL`
- **Load via** `dotenv` in config.py
- **Constants** imported from config across all modules to maintain consistency
- **API Model**: "gemini-3.1-flash-lite" (see processor.py)

### 2. Response Format (Gemini → Execution)
The AI model is instructed to format responses as: `COMMAND: <bash> | SPEECH: <text>`
- **Parsing rule**: Split on first `|` → extract command and speech
- **Null handling**: If no `|` found, treat entire response as speech-only (no execution)
- **Safety**: Commands matching `NULL_COMMAND` ("null") are rejected

```python
# Correct parsing pattern:
if "|" in raw_text:
    cmd_part = raw_text.split("|")[0].replace("COMMAND:", "").strip()
    speech_part = raw_text.split("|")[1].replace("SPEECH:", "").strip()
```

### 3. Command Execution Safety
The `SystemTerminal.run_command()` method includes **two safety checkpoints**:
1. Reject empty commands or `"null"` placeholder
2. Return stdout on success (exit code 0), stderr on failure

**Never bypass these checks** when extending execution logic.

### 4. Voice Synthesis (piper-tts)
- Command: `piper-tts --model {voice_model} --output_raw | aplay -r 22050 -f S16_LE -t raw`
- Execution: via `subprocess.Popen` with shell=True
- **Warning**: shell=True requires sanitized input; only use trusted model/text values

### 5. Error Handling
- API errors: Fallback gracefully (return raw response if parsing fails)
- Missing config: Exit immediately in config.py (no partial operation)
- Command execution: Return stderr for visibility

## Development Workflow

### Adding a New Feature
1. **Define config constants** in config.py if needed (e.g., new model name, timeout)
2. **Update PromptProcessor** if AI behavior changes
3. **Update response parsing** if format changes (keep COMMAND:|SPEECH: structure)
4. **Test SystemTerminal** with edge cases: empty, "null", dangerous commands
5. **Test HyprCore.speak()** with special characters and long text

### Modifying Processor.process()
- Keep system instruction clear: "COMMAND: bash | SPEECH: [text]"
- Test response parsing with malformed input (missing |, extra |, etc.)
- Always validate API response exists before accessing `.text`

### Adding Commands
- **Always** add null-check in SystemTerminal
- Log or return errors (stderr) for debugging
- Test with dangerous patterns: `rm -rf`, pipes, command substitution

## Common Pitfalls

| Pitfall | Avoid | Instead |
|---------|-------|---------|
| Hardcoding API keys | `API_KEY = "sk-..."` | Use `.env` and config.py |
| Trusting response format | Assuming `response.text` exists | Always check response object type |
| Shell injection | Passing untrusted text to piper-tts | Quote/escape or use list-based subprocess |
| Ignoring parse failures | Assuming `|` is always present | Check and fallback to speech-only |
| Skipping null-check | Running `NULL_COMMAND` | Enforce in SystemTerminal.run_command() |

## Testing Recommendations

- **Unit**: Test response parsing with various formats (no |, multiple |, etc.)
- **Integration**: Mock Gemini API, verify HyprCore speaks output
- **Safety**: Test SystemTerminal rejects empty/null with ValueError
- **Edge cases**: Special characters in AI response, very long text for TTS, API timeouts

## Useful Context

- **Branch**: clean-code (default: main)
- **Python minimum**: 3.8+
- **External dependencies**: google-genai, python-dotenv, system tools (piper-tts, aplay)
- **Hyprland**: Required for full integration (config.toml at ~/.config/hypr-ai/)
