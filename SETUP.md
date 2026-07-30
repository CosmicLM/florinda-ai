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
    docker docker-compose texlive-core texlive-latexextra lm_sensors grim \
    base-devel gobject-introspection cairo fd ripgrep

# piper-tts isn't in the official repos — install from the AUR:
yay -S piper-tts
# (or build it yourself: https://github.com/rhasspy/piper)

# Enable Docker (needed for the self-hosted SearXNG search container):
sudo systemctl enable --now docker
```

`fd`/`ripgrep` back `file_search.py`'s whole-filesystem find/grep. On
Debian/Ubuntu/Fedora the package is `fd-find`, not `fd` (an unrelated
package already owns that name there), and it installs its binary as
`fdfind` — `file_search.py` resolves this itself at runtime, so no
symlink/alias setup is needed either way:

```bash
sudo apt install fd-find ripgrep      # Debian/Ubuntu
sudo dnf install fd-find ripgrep      # Fedora
```

`base-devel`/`gobject-introspection`/`cairo` (Debian/Ubuntu:
`build-essential`, `pkg-config`, `python3-dev`, `libdbus-1-dev`,
`libglib2.0-dev`, `libgirepository-2.0-dev`, `libcairo2-dev`; Fedora: `gcc`,
`pkgconf-pkg-config`, `python3-devel`, `dbus-devel`, `glib2-devel`,
`gobject-introspection-devel`, `cairo-devel`) are needed even if you're on
Hyprland/Sway and never touch the ScreenCast fallback — `pip install -r
requirements.txt` builds `dbus-python` and `PyGObject` (which pulls in
`pycairo`) from source on every install (none of the three ships a
prebuilt wheel), and without a C compiler + these dev headers that step
fails with a Meson "Unknown compiler(s)" or "Dependency ... not found"
error.

**Ubuntu/Debian note**: `libgirepository-2.0-dev` only exists starting with
gobject-introspection 1.80 — confirmed present on **Ubuntu 24.04 LTS
("noble") onward** and **Debian 13 ("trixie") onward**, confirmed absent on
Ubuntu 22.04 and Debian 12 ("bookworm"). `PyGObject>=3.56` requires
`girepository-2.0` unconditionally, with no fallback to the older
`girepository-1.0` that `libgirepository1.0-dev` provides, so this won't
build from source on Ubuntu 22.04 or Debian 12 or older as things stand.
Not automated by this installer since it hasn't come up yet — you'd need
an older, pinned `PyGObject` version on those releases instead.

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

**Debian/Ubuntu note**: `python3 -m venv` fails with "ensurepip is not
available" unless `python3-venv` is installed separately — Debian/Ubuntu
splits it out of the base `python3` package (Arch and Fedora don't need
this extra step). `./install.sh`/`install.py` installs it automatically;
if you're doing this by hand: `sudo apt install python3-venv` (or the
version-specific `python3.13-venv` etc. the error message names) first.

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
and confirming before it edits anything. It also asks which key combo you
want (default `Super+Shift+Space`, typed as e.g. `Ctrl+Alt+F` — one
modifier-and-key combo you answer once, translated into whichever of the
three WMs' own bind syntax applies) rather than assuming everyone wants the
same default. After writing the config, it confirms the bind actually took
— for Hyprland by asking the running compositor itself (`hyprctl binds -j`)
whether both the press and release binds are registered, for GNOME by
reading every value back from `gsettings` and comparing it to what was
just set, and for Sway by checking that `swaymsg reload` accepted the
config without error (Sway's IPC has no "list binds" query to check
against, unlike Hyprland's). A failed confirmation prints a warning
instead of claiming success. What follows is what it's actually doing, for
KDE (not auto-configured — see the note at the end) or if you'd rather do
it by hand.

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

## 7. (Optional) Status widget (Waybar or GNOME Shell)

### Waybar

Add a `custom/flora` module to your waybar config:

```jsonc
"custom/flora": {
    "format": "{}",
    "escape": false,
    "return-type": "json",
    "exec": "python3 PROJECT_DIR/scripts/waybar_flora_status.py",
    "interval": 10,
    "signal": 8,
    "tooltip": true,
    "on-click": "kitty --class flora-activity --title 'Florinda Activity (read-only)' -e sh -c 'tail -n 200 -f ~/.local/share/flora-ai/activity.log'",
    "on-click-right": "python3 PROJECT_DIR/scripts/flora_kill_switch.py"
}
```

`on-click-right` is a kill switch: it stops `flora-daemon.service` if
running (mic, screen-watching, every background watcher all go silent —
the widget flips to "Offline"), or starts it back up if it's currently
stopped. Same toggle-on-repeated-click pattern as the GNOME/KDE push-to-
talk hook, just bound to a right-click instead of a keybind. Skip this
entire module if you don't use waybar; it's cosmetic/convenience, not
required for Florinda to function — `systemctl --user stop/start
flora-daemon.service` does the same thing from a terminal.

### GNOME Shell

GNOME has no Waybar, so the equivalent is a small GNOME Shell extension —
`gnome-extension/florinda-status@florinda-ai/` in this repo — showing the
same states (Idle/Listening/Thinking/Talking/Watching/Offline) as a top-bar
label, with a menu to stop/start `flora-daemon.service` and open the
activity log. `install.py` installs and enables this automatically when it
detects a GNOME desktop (confirming first, same as every other step); to do
it by hand:

```bash
mkdir -p ~/.local/share/gnome-shell/extensions
cp -r gnome-extension/florinda-status@florinda-ai \
    ~/.local/share/gnome-shell/extensions/florinda-status@florinda-ai
gnome-extensions enable florinda-status@florinda-ai
```

If `gnome-extensions enable` doesn't make it appear right away (can happen
right after copying in a brand-new extension, especially on Wayland), log
out and back in — it'll already be enabled once the session restarts.
Requires GNOME Shell 45+ (the ESM extension API); skip this entirely if
you don't use GNOME, same as skipping the Waybar module on other desktops.

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

## (Optional) Qiskit circuit verification

`qiskit_runner.py`/`qiskit_docs.py` let Florinda actually run Qiskit code
you give it (or describe its own suggestions to you) instead of just
reasoning about whether a circuit would work. This is entirely optional —
skip it and everything else still works — and deliberately NOT part of
flora-ai's own venv, since qiskit/qiskit-aer/matplotlib are a large
dependency tree most installs don't need. Set up a separate venv:

```bash
python3 -m venv ~/Projects/quantum-projects/venv
~/Projects/quantum-projects/venv/bin/pip install qiskit qiskit-aer matplotlib
```

That default path is what both scripts look for out of the box. If you
already have a Qiskit environment somewhere else, point at it instead:

```bash
# in .env
FLORA_QISKIT_VENV_PYTHON="/path/to/your/venv/bin/python3"
```

Without either, a Qiskit request just fails with a clear error naming
exactly what's missing — not a crash, and nothing else is affected.

## Updating an existing install

```bash
cd ~/florinda-ai   # wherever you originally cloned it
git pull
./install.sh
```

Re-running `./install.sh` on top of an existing install is safe — every
step either confirms before touching anything or is naturally idempotent:
system packages just get skipped if already installed, the venv is reused
(not recreated) and `pip install -r requirements.txt` re-run on top of it,
`.env` is left alone unless you explicitly agree to overwrite it, and
keybind/GNOME-extension files are overwritten with whatever the current
checkout has rather than duplicated. This is the right move any time a fix
lands that touches system dependencies (as several recently have — see the
git log) — a plain `git pull` alone wouldn't install a newly-required
system package.

If you know a change was Python-only (no new system packages, no changes
to `install.py` itself), it's faster to skip the full installer:

```bash
git pull
venv/bin/pip install -r requirements.txt   # only needed if requirements.txt changed
systemctl --user restart flora-daemon.service
journalctl --user -u flora-daemon.service -n 20 --no-pager   # confirm a clean restart
```

When in doubt, `./install.sh` is always safe to re-run — it just costs a
few extra confirmation prompts for steps that turn out to already be done.
