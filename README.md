# Florinda AI ✶

A Jarvis-like research assistant deeply integrated into your Linux laptop and Hyprland environment. Made for quantum computing research

> **Naming note:** the assistant's name is **Florinda** — that's what it calls itself, what the GitHub repo/README use, and what any human-facing text says. Internally, the codebase uses the short technical token **flora** for anything invisible to an end user: file names (`flora_daemon.py`), environment variables (`FLORA_API_KEY`), the systemd service (`flora-daemon.service`), the Docker container (`flora-ai-searxng`), and the local project/data directories (`flora-ai`). If you're grepping the code and only find "flora", that's expected — it's the same project, just the plumbing side of the name rather than the public one.

## Overview


Florinda is a voice-activated, AI-powered research assistant designed specifically for Hyprland users. Get instant answers, perform research, and manage tasks without leaving your workflow.

## Features

- **Voice Activation** - Hands-free interaction with natural language commands
- **Hyprland Integration** - Seamlessly integrated with your window manager
- **Research Assistant** - Quick information retrieval and web searches
- **Context Aware** - Understands your active window and workspace
- **Lightweight** - Optimized for Linux systems

## Installation

```bash
git clone https://github.com/yourusername/florinda-ai.git
cd florinda-ai
./install.sh
```

An interactive installer — detects your distro (Arch/Debian/Fedora) and
desktop, installs system dependencies (confirming before anything that
needs `sudo`), sets up the venv, walks you through picking an AI provider
and voice model, writes `.env`, brings up the search container, and
installs the systemd service. See **[SETUP.md](SETUP.md)** for what it does
step by step (or to do it by hand instead), and
**[SYSTEM_REQUIREMENTS.md](SYSTEM_REQUIREMENTS.md)** for exactly what gets
installed and why.

**Already installed?** `./update.sh` pulls the latest changes and safely
re-runs the installer, so any newly-required system packages get picked up
too — see SETUP.md's "Updating an existing install" section.

## Quick Start

1. Configure your Hyprland keybind
2. Press your activation key
3. Speak your query or type your request
4. Florinda processes and displays results

## Configuration

Edit `.env` in the project root to customize:
- AI provider + API keys (see SETUP.md — Gemini, OpenAI-compatible, or Anthropic)
- Voice model
- Dozens of other settings (screen-watch interval, watcher cooldowns, timeouts, ...) — see `config.py` for the full list, all with working defaults

Keybindings (push-to-talk, etc.) are configured in your window manager's own
config, not here — see SETUP.md's Hyprland example.

<details>
<summary>Prompts, Behavior And How To Change</summary>

The AI Is called in a Recursive function. that means that:
- the first prompt is the User-Prompt
- all the other prompts are Sessions
the ai can call itself again in a Recursive creating a Session

To Change The Prompts Of The AI You Can:
- INSTRUCTION.md -> this is the system prompt of the AI, uses:
  - $EOC -> End-Of-Command - will use it as a seperator for your commands
  - $SYS_INFO -> System-Information - will replace it will all the basic info of the system so no unneceserry recursions will be used
- SESSION.md -> at each Recursion of the AI it will run this prompt as the "user prompt"
  - $INFO -> The Information From The Prev Session
  - $COMMAND -> The Command Excecuted In The Prev Session
  - $OUTPUT -> The Output Of That Command

</details>

## Requirements

- Linux, Wayland or X11
- Python 3.8+
- Internet connection
- Screen-watching (passive OCR context-awareness) uses `grim` if present
  (any wlroots compositor: Hyprland, Sway, river — zero extra setup), or
  falls back automatically to the `org.freedesktop.portal.ScreenCast`
  D-Bus portal on other desktops (GNOME, KDE, X11 session managers), which
  needs `gstreamer`, `gst-plugin-pipewire`, and a running
  `xdg-desktop-portal` backend for your desktop — standard on any of these
  out of the box, nothing Hyprland-specific required.
- Window/workspace control (`hyprland_bridge.py`) still requires Hyprland
  specifically for now — a portability pass for that piece hasn't happened
  yet.

  ## Known Issues

  - We are currently working on implementing Florinda on Ubuntu/ Debian work spaces, specifically for GNOME.
  #### What Works (and what doesn't):
  - [x] Qiskit Visualization and Quantum Circuit Generation
  - [x] Text-to-speech
  - [x] Notification Status bar
  - [x] Searching the Web
  - [x] Skill Creation
  - [ ] Screen Watching (Soon)      

## License

MIT
