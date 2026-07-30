# Setup Guide

A step-by-step walkthrough to get Florinda running from scratch. See
`SYSTEM_REQUIREMENTS.md` first for the full list of what's being installed
here and why — this guide is the "how," that one's the "what."

**Fastest path**: `./install.sh` does everything below interactively —
detects your distro (Arch/Debian/Fedora) and desktop, installs system
packages (confirming before any `sudo` step), sets up the venv, prompts for
an AI provider and voice model, writes `.env`, brings up the SearXNG
container, and installs the systemd service. What follows is what it's
actually doing, for anyone who wants to run the steps by hand, understand
them, or debug a step that didn't go as expected.

Commands below use `pacman` (Arch/Manjaro). Swap in your distro's package
manager (`apt`, `dnf`, ...) and package names as needed — most of these are
correctly named the same or very similarly on Debian/Ubuntu/Fedora.

## 1. Install system dependencies

```bash
sudo pacman -S python kitty alsa-utils tesseract tesseract-data-eng gtk3 \
    docker docker-compose texlive-core texlive-latexextra lm_sensors grim

# piper-tts isn't in the official repos — install from the AUR:
yay -S piper-tts
# (or build it yourself: https://github.com/rhasspy/piper)

# Enable Docker (needed for the self-hosted SearXNG search container):
sudo systemctl enable --now docker
```

**Ubuntu/Debian note**: `docker-compose-plugin` isn't in the default apt
repos (`E: Unable to locate package docker-compose-plugin`) — it only ships
from Docker's own apt repo, not your distro's. Add that repo first, then
install Docker's own packages instead of `docker.io`:

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
  https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update

sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
sudo systemctl enable --now docker
```

(Swap `ubuntu` for `debian` in the two URLs above if you're on Debian
proper.) `./install.sh`/`install.py` does this automatically — confirming
before adding the repo — so this is only needed if you're installing by
hand.

If your desktop isn't a wlroots compositor (not Hyprland/Sway/river) — e.g.
GNOME or KDE — skip `grim` and instead make sure `xdg-desktop-portal` plus
your desktop's own backend package (`xdg-desktop-portal-gnome`,
`-kde`, ...), `gstreamer`, and `gst-plugin-pipewire` are installed. Florinda
detects which path is available automatically; see
`SYSTEM_REQUIREMENTS.md`'s screen-watching section for details.

A voice model is also required — download one `.onnx` (+ its `.onnx.json`)
from [Piper's voice list](https://github.com/rhasspy/piper/blob/master/VOICES.md)
and note its path; you'll point `DEFAULT_VOICE_MODEL` at it in step 3.

## 2. Clone and install Python dependencies

```bash
git clone https://github.com/yourusername/florinda-ai.git
cd florinda-ai
python3 -m venv venv
venv/bin/python3 -m pip install -r requirements.txt
```

## 3. Configure `.env`

Create `.env` in the project root. Pick **one** AI provider section below —
you don't need all of them:

```bash
# --- Required regardless of provider ---
DEFAULT_VOICE_MODEL="/path/to/your/voice-model.onnx"

# --- Provider: Gemini (default, FLORA_AI_PROVIDER can be omitted) ---
FLORA_API_KEY="your-gemini-api-key"

# --- Provider: any OpenAI-compatible endpoint (OpenAI, Azure, Mistral,
#     Groq, OpenRouter, self-hosted vLLM/llama.cpp/Ollama's OpenAI shim...) ---
# FLORA_AI_PROVIDER=openai
# FLORA_OPENAI_API_KEY="sk-..."
# FLORA_OPENAI_BASE_URL="https://api.openai.com/v1"   # point this at whichever provider you're using
# FLORA_OPENAI_MODEL="gpt-4o"                          # required, no default guessed
# FLORA_OPENAI_MODEL_LIGHT="gpt-4o-mini"               # optional, falls back to FLORA_OPENAI_MODEL

# --- Provider: native Anthropic API (separate from the Claude CLI/
#     subscription route below — use this if you have direct API credits) ---
# FLORA_AI_PROVIDER=anthropic
# FLORA_ANTHROPIC_API_KEY="sk-ant-..."
# FLORA_ANTHROPIC_MODEL="claude-opus-5"          # default shown, override if you want
# FLORA_ANTHROPIC_MODEL_LIGHT="claude-haiku-4-5" # default shown

# --- Optional: route the "deep" reasoning tier through a Claude Code
#     subscription instead of the primary provider above (default: on).
#     Needs the `claude` CLI installed and logged in separately —
#     see https://code.claude.com. No API key needed for this route. ---
# FLORA_USE_CLAUDE_CLI_FOR_DEEP=true
```

Everything else in `config.py` has a working default — only touch the rest
of the `FLORA_*` variables listed there if you actually want non-default
behavior (screen-watch interval, watcher cooldowns, etc.).

Validate it parses before moving on:

```bash
venv/bin/python3 -c "from config import ConfigVault; ConfigVault(); print('config OK')"
```

A `ConfigurationError` here means exactly what its message says — e.g.
"FLORA_OPENAI_API_KEY and FLORA_OPENAI_MODEL are both required when
FLORA_AI_PROVIDER=openai" if you picked that provider but left one unset.

## 4. Bring up the SearXNG search container

```bash
docker compose up -d
docker compose ps          # should show flora-ai-searxng as "healthy" within ~20s
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8092/   # expect 200
```

## 5. Install the systemd service

```bash
mkdir -p ~/.config/systemd/user
cat > ~/.config/systemd/user/flora-daemon.service <<EOF
[Unit]
Description=Florinda always-on service (screen watch + push-to-talk)
After=default.target
StartLimitBurst=5
StartLimitIntervalSec=60

[Service]
WorkingDirectory=$(pwd)
ExecStart=$(pwd)/venv/bin/python3 $(pwd)/flora_service.py
Restart=on-failure
RestartSec=3s

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now flora-daemon.service
systemctl --user status flora-daemon.service   # should show "active (running)"
```

On Hyprland specifically, add an `ExecStartPre` line that waits for
`WAYLAND_DISPLAY` to appear in the systemd user environment before starting
— see the comment in this project's own unit file for why (a fresh-boot race
otherwise leaves the daemon with no display to render popups/dialogs on).
Other compositors/DEs generally handle this ordering via
`graphical-session.target` already; adjust if you hit the same race.

## 6. Wire up the push-to-talk keybind

`install.py` does this automatically for **Hyprland** (classic `.conf`
config only — see below), **Sway**, and **GNOME**, detecting your desktop
and confirming before it edits anything. What follows is what it's actually
doing, for KDE (not auto-configured — see the note at the end) or if you'd
rather do it by hand.

**Hyprland (classic `.conf` config)** — a real keybind, held down to talk
(`bind`/`bindr` fire on press/release respectively):

```
bind = SUPER SHIFT, SPACE, exec, python3 -S PROJECT_DIR/scripts/flora_ptt_hook.py PRESS
bindr = SUPER SHIFT, SPACE, exec, python3 -S PROJECT_DIR/scripts/flora_ptt_hook.py RELEASE
```

Put this in its own file (e.g. `~/.config/hypr/florinda-ptt.conf`) and add
`source = ~/.config/hypr/florinda-ptt.conf` to your main `hyprland.conf`,
then `hyprctl reload`.

**Hyprland (Lua-based config, e.g. ML4W dotfiles)** — this project's own
machine actually uses this style (`hyprland.lua` as the real entry point,
not the classic `.conf`), which is why the installer won't auto-edit it —
too much variation between Lua-based setups to safely assume a structure.
This binds bare `Super` alone (no second key needed), using a raw keyboard
hook since Hyprland's own bind system can't target a modifier key by
itself:

```lua
local FLORA_PTT_HOOK = "python3 -S PROJECT_DIR/scripts/flora_ptt_hook.py"
local SUPER_L_KEYCODE = 133  -- evdev KEY_LEFTMETA(125) + 8

hl.on("input.keyboard.key", function(keycode, _timestamp, state)
    if keycode ~= SUPER_L_KEYCODE then return end
    if state == 1 then os.execute(FLORA_PTT_HOOK .. " PRESS &")
    elseif state == 0 then os.execute(FLORA_PTT_HOOK .. " RELEASE &") end
end)
```

Replace `PROJECT_DIR` with your actual clone path, and adjust the keycode if
you want a different key (find yours via `wev`/`libinput debug-events`).
Reload with `hyprctl reload`.

**Sway** — same idea via `bindsym`/`bindsym --release`, using Sway's own
`$mod` variable so it matches whatever modifier your config already
defines:

```
bindsym $mod+shift+space exec python3 -S PROJECT_DIR/scripts/flora_ptt_hook.py PRESS
bindsym --release $mod+shift+space exec python3 -S PROJECT_DIR/scripts/flora_ptt_hook.py RELEASE
```

Put this in its own file and `include` it from `~/.config/sway/config`,
then `swaymsg reload`.

**GNOME** — GNOME's custom-keybinding system only fires a command on key
*press*, with no separate release action, so true hold-to-talk isn't
possible through it at all. Use `scripts/flora_ptt_toggle_hook.py` instead
(press once to start recording, press again to stop) as a Custom Shortcut
bound via `gsettings`:

```bash
gsettings set org.gnome.settings-daemon.plugins.media-keys custom-keybindings \
  "['/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/florinda-ptt/']"
gsettings set org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/florinda-ptt/ \
  name 'Florinda push-to-talk'
gsettings set org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/florinda-ptt/ \
  command 'python3 -S PROJECT_DIR/scripts/flora_ptt_toggle_hook.py'
gsettings set org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/florinda-ptt/ \
  binding '<Super><Shift>space'
```

**KDE** — not auto-configured (KDE's custom-command shortcut format has
changed across KDE4/Plasma5/Plasma6 in ways this installer can't verify).
Same toggle script as GNOME, wired up via System Settings > Shortcuts >
Custom Shortcuts, bound to `python3 -S PROJECT_DIR/scripts/flora_ptt_toggle_hook.py`.

Window/workspace *control* (`hyprland_bridge.py` — "switch to workspace 2",
"close this window", etc.) is Hyprland-specific for now; nothing else in
this guide requires Hyprland specifically. If you're on another WM, this one
feature just won't be available — everything else (voice, search, LaTeX/
Qiskit/RDKit rendering, memory, skills) works the same regardless.

Popped-up figure windows (Qiskit/LaTeX/RDKit) look better floated instead of
tiled — optional Hyprland window rules:

```lua
hl.window_rule({ name = "flora-ai-figure-float", match = { class = "^(flora-figure)$" }, float = true })
hl.window_rule({ name = "flora-ai-figure-size",  match = { class = "^(flora-figure)$" }, size = "720 560" })
hl.window_rule({ name = "flora-ai-figure-center",match = { class = "^(flora-figure)$" }, center = true })
```

## 7. (Optional) Waybar status widget

Add a `custom/flora` module to your waybar config pointing at
`scripts/waybar_flora_status.py` — see this repo's own
`~/.config/waybar/modules.json`-style setup for the exact module/CSS shape,
or skip this entirely if you don't use waybar; it's cosmetic, not required
for Florinda to function.

## 8. Verify end-to-end

```bash
# Service healthy and logging:
journalctl --user -u flora-daemon.service -n 30 --no-pager

# Push-to-talk: hold your bound key, speak, release — check the hook fired:
tail -f ~/.local/share/flora-ai/ptt-hook.log

# A real AI round-trip (bypasses voice, exercises your configured provider):
venv/bin/python3 -c "
from config import ConfigVault
c = ConfigVault().settings
print('provider:', c.ai_provider)
"

# A rendering tool, to confirm figure popups work:
echo '\[ E = mc^2 \]' | venv/bin/python3 latex_runner.py run
```

If push-to-talk doesn't trigger anything, check `~/.local/share/flora-ai/
ptt-hook.log` first (confirms the keybind fired at all) before suspecting the
service itself.
