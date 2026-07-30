# Florinda

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

## Quick Start

1. Configure your Hyprland keybind
2. Press your activation key
3. Speak your query or type your request
4. Florinda processes and displays results

## Configuration

Edit `~/.config/flora-ai/config.toml` to customize:
- API keys
- Voice settings
- Keybindings
- Response behavior

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

- Linux with Hyprland
- Python 3.8+
- Internet connection

## License

MIT
