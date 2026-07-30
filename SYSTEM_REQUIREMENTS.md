# System Requirements

This lists everything Florinda needs installed **on the OS itself** — binaries,
services, and system libraries — separate from `requirements.txt` (Python
packages installed into the project's own venv). Compiled by auditing every
`subprocess`/external-binary call actually made across the codebase, not from
memory — if a tool below is missing, the specific feature that needs it fails
with a real, readable error naming exactly what's missing (this project's own
"never fabricate completion" principle applies to its own error messages too).

## Core — needed for the always-on voice service to run at all

| What | Package (Arch/Manjaro) | Used by |
|---|---|---|
| Python 3.8+ | `python` | everything (tested on 3.14) |
| A terminal emulator: **kitty** | `kitty` | figure popups (Qiskit/LaTeX/RDKit), background task windows, the activity-log viewer |
| **piper-tts** + a voice model (`.onnx`) | AUR: `piper-tts`, or a manual build | text-to-speech (`voice.py`) |
| ALSA utilities: `aplay`, `arecord` | `alsa-utils` | audio playback + mic recording (`voice.py`, `mic_recorder.py`) |
| **tesseract-ocr** | `tesseract`, `tesseract-data-eng` (or your language) | screen-watching OCR, via the `pytesseract` Python binding — the binding alone does nothing without this |
| **gtk-launch** | `gtk3` (or `libgtk-3-bin` on Debian/Ubuntu) | resolving an installed app's `.desktop` entry (`app_launcher.py`) |
| Docker + Docker Compose | `docker`, `docker-compose` (Debian/Ubuntu: `docker-ce`/`docker-compose-plugin` from Docker's own apt repo — see note below) | the self-hosted SearXNG container backing Web Search / Academic Paper Search |
| systemd (user session) | (present on virtually any modern distro) | `flora-daemon.service`, the always-on background service |
| **One configured AI provider** | — | see "AI provider" below — at least one is required |

**Ubuntu/Debian note**: `docker-compose-plugin` isn't in the default apt
repos (`E: Unable to locate package docker-compose-plugin`) — it's only
published in Docker's own apt repo, not your distro's. `./install.sh` /
`install.py` adds that repo automatically (confirming first); see `SETUP.md`
step 1 for the manual commands if you're not using the installer.

## AI provider — pick exactly one as primary (`FLORA_AI_PROVIDER`)

No system package is needed for any of these — just an account/subscription
and (for three of the four) an API key:

| Provider | What you need | Notes |
|---|---|---|
| Gemini (default) | A Gemini API key | `FLORA_API_KEY` |
| Claude via subscription | The `claude` CLI (Claude Code) installed and logged in | Used automatically for the "deep" tier if `FLORA_USE_CLAUDE_CLI_FOR_DEEP=true` (default) — no separate API key, billed against your Claude subscription, not per-token |
| Claude via API | An Anthropic API key | `FLORA_ANTHROPIC_API_KEY` — separate billing from a Claude subscription; use this instead of/alongside the CLI route if you have direct API credits |
| Any OpenAI-compatible provider | An API key + that provider's base URL | `FLORA_OPENAI_API_KEY` / `FLORA_OPENAI_BASE_URL` / `FLORA_OPENAI_MODEL` — covers OpenAI itself, Azure OpenAI, Mistral, Groq, OpenRouter, Together, or a self-hosted vLLM/llama.cpp/Ollama OpenAI-compat endpoint |

The Claude CLI route and the "attach any key" routes (Anthropic API, OpenAI-
compatible) are independent — you don't need all of them, just whichever
matches what you actually have access to.

## Screen-watching — one of these two, chosen automatically

Florinda tries `grim` first; if it's not on `PATH`, it falls back to the
ScreenCast portal automatically (`screen_observer.py` / `screencast_portal.py`)
— you don't need to configure which one, just make sure one of these two
paths is actually satisfied:

| Path | Needs | Works on |
|---|---|---|
| `grim` | `grim` package | Any wlroots compositor: Hyprland, Sway, river |
| ScreenCast portal | `xdg-desktop-portal` + a backend matching your desktop (`xdg-desktop-portal-gnome`, `-kde`, `-hyprland`, `-gtk`, ...), `gstreamer`, `gst-plugin-pipewire`, PipeWire itself | GNOME, KDE, and anything else running a portal — this is the WM-agnostic path |

(`dbus-python` and `PyGObject`, needed by the portal path, are already in
`requirements.txt` — they need system `libdbus-1` and GObject-introspection
dev headers to build, which any desktop running `xdg-desktop-portal` already
has, since the portal is itself a D-Bus/GLib service.)

## Hyprland-specific (window/workspace control only)

Only `hyprland_bridge.py` — controlling windows/workspaces directly (switch
workspace, focus, close, float, fullscreen) — actually requires Hyprland
specifically right now. Nothing else in this list does.

| What | Needed for |
|---|---|
| Hyprland + `hyprctl` (bundled with it) | `hyprland_bridge.py` |
| **waybar** (optional) | The status widget showing idle/listening/thinking/talking — works on any wlroots compositor, not GNOME/KDE; skip it if you don't want a status widget |
| **ydotool** (optional) | Only used by some autonomously-created skills (`skills/` — gitignored, generated at runtime), not by any tracked source file |

A portability pass for `hyprland_bridge.py` itself (supporting Sway natively,
etc.) hasn't happened yet — see the README's Requirements section.

## Feature-specific

| What | Package | Needed for |
|---|---|---|
| TeX Live: `pdflatex`, `pdftoppm`, plus the `standalone`, `varwidth`, `preview` LaTeX packages | `texlive-core`, `texlive-latexextra` (or your distro's equivalent bundle) | LaTeX rendering (`latex_runner.py`) |
| A **separate** Qiskit virtualenv at `~/Projects/quantum-projects/venv` | your own `pip install qiskit qiskit-aer matplotlib` in that venv | Qiskit circuit verification (`qiskit_runner.py`) — deliberately NOT bundled into Florinda's own venv, see that file's own WHY note |
| **Ollama**, running locally (`localhost:11434` by default) | `ollama` + at least one pulled model | Background-job local model (`local_brain.py`, backing the passive watchers) and the optional offline fallback when the primary AI provider is unreachable |
| `lm_sensors` (`sensors` command) | `lm_sensors` | `thermal_monitor.py` — currently a standalone utility, not yet wired into the running service |
| `nvidia-smi` (optional, NVIDIA GPUs only) | proprietary NVIDIA driver package | Same file, optional GPU thermal reads |

## Python packages

Everything else (the AI SDKs, OpenCV/Tesseract bindings, RDKit, etc.) is in
`requirements.txt` and installed into the project's own venv — see
`SETUP.md` for the exact commands.
