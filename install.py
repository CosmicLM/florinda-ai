#!/usr/bin/env python3
"""install.py — interactive, plug-and-play installer for Florinda.

WHY Python instead of one big bash script: every step past "which package
manager exists" needs real logic — mapping ~15 dependencies to three
different distros' package names, prompting for and validating an AI
provider choice, generating a correctly-quoted .env, writing a systemd unit
file. install.sh (a thin bash bootstrap) only handles the one thing bash is
actually needed for — bootstrapping python3 itself if it's somehow missing —
then hands off to this script for everything else.

WHY every privileged step is confirmed, not silently run: same "never
fabricate, always show what's happening before it happens" principle this
whole project already follows for the AI's own COMMAND execution — an
installer running unattended `sudo` commands is exactly the kind of thing
that should never surprise the person running it.

WHY --answers-file exists: this script needs to be genuinely testable
without a human sitting at a real terminal (and without ever touching a
real .env/systemd unit by accident during that testing) — it accepts
pre-supplied answers so its own logic can be exercised end-to-end, same
motivation as this project's existing self-check pattern in every other
module's `if __name__ == "__main__"` block, just adapted for something that
needs actual interactive input in normal use.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parent
VENV_DIR = REPO_ROOT / "venv"
ENV_PATH = REPO_ROOT / ".env"
SYSTEMD_UNIT_DIR = Path.home() / ".config/systemd/user"
SYSTEMD_UNIT_PATH = SYSTEMD_UNIT_DIR / "flora-daemon.service"


class InstallError(Exception):
    """Raised when a step fails in a way the installer can't recover from."""


# --- distro / package-manager detection ---

_PACKAGE_MANAGERS = {
    "pacman": "pacman",
    "apt": "apt-get",
    "dnf": "dnf",
}

# WHY a table instead of one list: each distro names the same real
# dependency differently (verified live only for pacman on this machine —
# apt/dnf names are standard, well-established conventions for these exact
# packages, not verified live since this machine can't run them; flagged
# inline where a name is a best-effort mapping rather than a live-tested one).
_SYSTEM_PACKAGES = {
    "pacman": [
        "kitty", "alsa-utils", "tesseract", "tesseract-data-eng", "gtk3",
        "docker", "docker-compose", "texlive-core", "texlive-latexextra",
        "lm_sensors", "grim",
        # fd/ripgrep back file_search.py's whole-filesystem find/grep — were
        # missing from this list entirely until a live report showed
        # file_search.py crashing with a raw FileNotFoundError traceback on
        # a fresh install that (correctly) didn't already have them. Arch's
        # fd package installs the binary as plain `fd` (no name collision).
        "fd", "ripgrep",
        # Build deps for dbus-python/PyGObject (pip has no prebuilt wheel for
        # either — both compile from source on every install). base-devel
        # brings gcc/pkgconf; dbus's Arch package already ships its own dev
        # headers (unlike Debian/Fedora, which split them out separately).
        # cairo is PyGObject's own build dependency (pulls in pycairo, which
        # needs cairo's headers to compile) — same "no prebuilt wheel" story.
        "base-devel", "gobject-introspection", "cairo",
    ],
    "apt": [
        "kitty", "alsa-utils", "tesseract-ocr", "tesseract-ocr-eng",
        "libgtk-3-bin", "docker-ce", "docker-ce-cli", "containerd.io",
        "docker-compose-plugin", "texlive", "texlive-latex-extra",
        "lm-sensors", "grim", "python3-venv",
        # Same build-from-source deps as above, Debian/Ubuntu package names.
        # WHY libgirepository-2.0-dev, not the older libgirepository1.0-dev:
        # verified live — PyGObject>=3.56 requires girepository-2.0 at build
        # time unconditionally (no fallback to the 1.0 API), and Debian/
        # Ubuntu only started shipping this package with gobject-introspection
        # 1.80 — confirmed present on Ubuntu 24.04 LTS ("noble") onward and
        # Debian 13 ("trixie") onward, confirmed ABSENT on Ubuntu 22.04 and
        # Debian 12 ("bookworm") — those older releases can't build
        # PyGObject>=3.56 from source at all.
        "build-essential", "pkg-config", "python3-dev", "libdbus-1-dev",
        "libglib2.0-dev", "libgirepository-2.0-dev", "libcairo2-dev",
        # WHY fd-find, not fd: Debian/Ubuntu's own `fd` package name is
        # already taken by an unrelated, preexisting tool ("fastdate") —
        # fd-find installs its binary as `fdfind` instead. file_search.py
        # resolves this itself at runtime (tries `fd`, falls back to
        # `fdfind`), so no symlink/alias is needed here.
        "fd-find", "ripgrep",
    ],
    "dnf": [
        "kitty", "alsa-utils", "tesseract", "tesseract-langpack-eng", "gtk3",
        "docker", "docker-compose-plugin", "texlive-scheme-medium",
        "lm_sensors", "grim",
        # Same build-from-source deps as above, Fedora package names.
        "gcc", "pkgconf-pkg-config", "python3-devel", "dbus-devel",
        "glib2-devel", "gobject-introspection-devel", "cairo-devel",
        # Same fd naming quirk as Debian/Ubuntu — verified against Fedora's
        # own package page: Fedora's fd-find ALSO installs as `fdfind`, not
        # `fd` (the name collision isn't Debian-specific).
        "fd-find", "ripgrep",
    ],
}

_INSTALL_CMD = {
    "pacman": ["sudo", "pacman", "-S", "--needed", "--noconfirm"],
    "apt": ["sudo", "apt-get", "install", "-y"],
    "dnf": ["sudo", "dnf", "install", "-y"],
}


def detect_package_manager() -> str:
    for name, binary in _PACKAGE_MANAGERS.items():
        if shutil.which(binary):
            return name
    raise InstallError(
        "No supported package manager found (pacman/apt-get/dnf). "
        "See SYSTEM_REQUIREMENTS.md and install dependencies manually."
    )


def detect_desktop() -> str:
    """Best-effort label for what's actually running, used only to decide
    whether to offer the Hyprland-specific integration step — never gates
    anything else, since every other feature works regardless of desktop."""
    if os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"):
        return "Hyprland"
    current = os.environ.get("XDG_CURRENT_DESKTOP", "")
    if current:
        return current
    return "unknown"


def is_wayland_session() -> bool:
    return os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"


class Prompter:
    """Wraps input() so answers can come from a real terminal OR a
    pre-supplied JSON file (see --answers-file), without duplicating the
    prompt logic for each. Keys are stable strings the answers file uses."""

    def __init__(self, answers: Optional[dict] = None) -> None:
        self._answers = answers or {}

    def confirm(self, key: str, prompt: str, default: bool = True) -> bool:
        if key in self._answers:
            return bool(self._answers[key])
        suffix = "[Y/n]" if default else "[y/N]"
        reply = input(f"{prompt} {suffix} ").strip().lower()
        if not reply:
            return default
        return reply in ("y", "yes")

    def choose(self, key: str, prompt: str, options: list[str], default_index: int = 0) -> int:
        if key in self._answers:
            return int(self._answers[key])
        print(prompt)
        for i, option in enumerate(options, start=1):
            marker = " (default)" if i - 1 == default_index else ""
            print(f"  {i}) {option}{marker}")
        reply = input(f"Choose [1-{len(options)}]: ").strip()
        if not reply:
            return default_index
        try:
            index = int(reply) - 1
            if 0 <= index < len(options):
                return index
        except ValueError:
            pass
        print("Invalid choice, using default.")
        return default_index

    def text(self, key: str, prompt: str, default: str = "", secret: bool = False) -> str:
        if key in self._answers:
            return str(self._answers[key])
        suffix = f" [{default}]" if default else ""
        reply = input(f"{prompt}{suffix}: ").strip()
        return reply or default


def _run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    print(f"$ {' '.join(cmd)}")
    return subprocess.run(cmd, check=True, **kwargs)


def _add_docker_apt_repo(prompter: Prompter) -> None:
    """WHY this exists: verified live-reported behavior — Debian/Ubuntu's own
    default repos don't carry `docker-compose-plugin` (apt fails with
    "Unable to locate package"), because that package only ships from
    Docker's own apt repo, not the distro's. `docker.io` (the distro's own
    Docker build) and `docker-ce` (Docker's own build) can't be installed
    side by side, so once we need the plugin from Docker's repo we get
    Docker's own engine packages from there too, instead of mixing sources."""
    keyrings_dir = Path("/etc/apt/keyrings")
    keyring_path = keyrings_dir / "docker.asc"
    if keyring_path.exists():
        return
    if not prompter.confirm(
        "add_docker_apt_repo",
        "docker-compose-plugin isn't in the default apt repos — add Docker's "
        "official apt repo now (needed for Docker + the SearXNG search container)?",
    ):
        print("Skipped — docker-ce/docker-compose-plugin install will likely fail below.")
        return
    os_release = {}
    try:
        for line in Path("/etc/os-release").read_text().splitlines():
            if "=" in line:
                key, _, value = line.partition("=")
                os_release[key] = value.strip('"')
    except OSError:
        pass
    distro_id = os_release.get("ID", "ubuntu")  # "ubuntu" or "debian"
    codename = os_release.get("VERSION_CODENAME", "")
    if not codename:
        raise InstallError(
            "Couldn't determine your Debian/Ubuntu release codename from "
            "/etc/os-release — add Docker's apt repo manually: "
            "https://docs.docker.com/engine/install/"
        )
    _run(["sudo", "apt-get", "update"])
    _run(["sudo", "apt-get", "install", "-y", "ca-certificates", "curl"])
    _run(["sudo", "install", "-m", "0755", "-d", str(keyrings_dir)])
    _run([
        "sudo", "curl", "-fsSL",
        f"https://download.docker.com/linux/{distro_id}/gpg",
        "-o", str(keyring_path),
    ])
    _run(["sudo", "chmod", "a+r", str(keyring_path)])
    arch = subprocess.run(
        ["dpkg", "--print-architecture"], capture_output=True, text=True, check=True
    ).stdout.strip()
    repo_line = (
        f"deb [arch={arch} signed-by={keyring_path}] "
        f"https://download.docker.com/linux/{distro_id} {codename} stable\n"
    )
    Path("/tmp/docker.list").write_text(repo_line)
    _run(["sudo", "cp", "/tmp/docker.list", "/etc/apt/sources.list.d/docker.list"])
    _run(["sudo", "apt-get", "update"])


def install_system_packages(pm: str, prompter: Prompter) -> None:
    packages = _SYSTEM_PACKAGES[pm]
    print(f"\nDetected package manager: {pm}")
    print("System packages needed:", ", ".join(packages))
    print("(piper-tts is installed separately via pip — see SYSTEM_REQUIREMENTS.md for why)")
    if not prompter.confirm("install_system_packages", "Install these now with sudo?"):
        print("Skipped — make sure these are installed manually before continuing.")
        return
    if pm == "apt":
        _add_docker_apt_repo(prompter)
    _run(_INSTALL_CMD[pm] + packages)


def setup_venv(prompter: Prompter) -> None:
    if VENV_DIR.exists():
        print(f"\nvenv already exists at {VENV_DIR}, reusing it.")
    else:
        print(f"\nCreating venv at {VENV_DIR}...")
        _run([sys.executable, "-m", "venv", str(VENV_DIR)])
    pip = str(VENV_DIR / "bin" / "pip")
    _run([pip, "install", "--upgrade", "pip"])
    _run([pip, "install", "-r", str(REPO_ROOT / "requirements.txt")])
    # WHY pip, not the system package manager: the `piper-tts` PyPI package
    # is one cross-distro install path instead of AUR-only (Arch) with no
    # equivalent on Debian/Fedora's official repos.
    #
    # WHY the symlink: verified live — the PyPI package's own console script
    # is named `piper` (not `piper-tts`), while voice.py hard-codes the
    # command `piper-tts` (matching this project's original AUR-based
    # install, whose package happens to install a `piper-tts` symlink to the
    # same underlying `piper` binary). Without this, a fresh pip-based
    # install would produce a `piper` binary voice.py never actually calls,
    # and audio output would silently do nothing.
    _run([pip, "install", "piper-tts"])
    piper_bin = VENV_DIR / "bin" / "piper"
    piper_tts_alias = VENV_DIR / "bin" / "piper-tts"
    if piper_bin.exists() and not piper_tts_alias.exists():
        piper_tts_alias.symlink_to("piper")


_DEFAULT_OLLAMA_MODEL = "phi4-mini"  # matches config.py's own FloraSettings.local_model default


def setup_ollama(pm: str, prompter: Prompter) -> None:
    """Backs local_brain.py (background watchers) and the optional offline
    fallback. Without this, those features fail at runtime with nothing
    obviously pointing back at "Ollama isn't installed" as the cause."""
    print("\n--- Ollama (local background-job model) ---")
    if not shutil.which("ollama"):
        if not prompter.confirm("install_ollama", "Install Ollama (needed for background watchers)?"):
            print("Skipped — background watchers and the offline fallback won't work.")
            return
        if pm == "pacman":
            # WHY the package manager here specifically: verified live —
            # `ollama` is in Arch's official `extra` repo, not AUR.
            _run(_INSTALL_CMD[pm] + ["ollama"])
        else:
            # WHY curl-pipe here instead of a distro package: Ollama isn't
            # in Debian/Ubuntu's or Fedora's official repos — this is
            # Ollama's own documented cross-distro install method, the same
            # one their own install instructions point everyone to.
            print("Running Ollama's official install script (ollama.com/install.sh)...")
            _run(["sh", "-c", "curl -fsSL https://ollama.com/install.sh | sh"])

    if shutil.which("systemctl"):
        status = subprocess.run(["systemctl", "is-active", "ollama"], capture_output=True, text=True)
        if status.stdout.strip() != "active":
            if prompter.confirm("enable_ollama", "Enable and start the ollama service now?"):
                _run(["sudo", "systemctl", "enable", "--now", "ollama"])

    if prompter.confirm("pull_ollama_model", f"Pull the default model ({_DEFAULT_OLLAMA_MODEL}, needed by local_brain.py)?"):
        _run(["ollama", "pull", _DEFAULT_OLLAMA_MODEL])


_PROVIDER_OPTIONS = ["Gemini", "OpenAI-compatible endpoint", "Anthropic API"]


def prompt_ai_provider(prompter: Prompter) -> dict:
    print("\n--- AI provider ---")
    choice = prompter.choose("ai_provider", "Which AI provider do you want as primary?", _PROVIDER_OPTIONS, 0)
    env: dict[str, str] = {}
    if choice == 0:
        env["FLORA_API_KEY"] = prompter.text("gemini_api_key", "Gemini API key", secret=True)
    elif choice == 1:
        env["FLORA_AI_PROVIDER"] = "openai"
        env["FLORA_OPENAI_API_KEY"] = prompter.text("openai_api_key", "API key", secret=True)
        env["FLORA_OPENAI_BASE_URL"] = prompter.text(
            "openai_base_url", "Base URL", default="https://api.openai.com/v1"
        )
        env["FLORA_OPENAI_MODEL"] = prompter.text("openai_model", "Model name (e.g. gpt-4o)")
    else:
        env["FLORA_AI_PROVIDER"] = "anthropic"
        env["FLORA_ANTHROPIC_API_KEY"] = prompter.text("anthropic_api_key", "Anthropic API key", secret=True)

    print("\n--- Claude Code CLI (optional, separate from the above) ---")
    if prompter.confirm(
        "use_claude_cli",
        "Also route the 'deep' reasoning tier through a Claude Code subscription "
        "(needs the `claude` CLI installed and logged in separately)?",
        default=shutil.which("claude") is not None,
    ):
        env["FLORA_USE_CLAUDE_CLI_FOR_DEEP"] = "true"
    else:
        env["FLORA_USE_CLAUDE_CLI_FOR_DEEP"] = "false"
    return env


_DEFAULT_VOICE_NAME = "en_US-lessac-medium"
_DEFAULT_VOICE_BASE_URL = (
    "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/lessac/medium"
)
_VOICES_DIR = Path.home() / ".local/share/flora-ai/voices"


def _download_default_voice() -> str:
    """Downloads en_US-lessac-medium (verified live: the real Hugging Face
    URL pattern piper-voices actually uses, confirmed via a real HEAD
    request before this was written) so a fresh install has a working voice
    without requiring the user to go find one manually first."""
    _VOICES_DIR.mkdir(parents=True, exist_ok=True)
    model_path = _VOICES_DIR / f"{_DEFAULT_VOICE_NAME}.onnx"
    config_path = _VOICES_DIR / f"{_DEFAULT_VOICE_NAME}.onnx.json"
    if model_path.exists() and config_path.exists():
        print(f"Default voice already downloaded at {model_path}.")
        return str(model_path)
    print(f"Downloading default voice ({_DEFAULT_VOICE_NAME}, ~60MB)...")
    for filename, dest in ((f"{_DEFAULT_VOICE_NAME}.onnx", model_path), (f"{_DEFAULT_VOICE_NAME}.onnx.json", config_path)):
        _run(["curl", "-fL", "-o", str(dest), f"{_DEFAULT_VOICE_BASE_URL}/{filename}?download=true"])
    return str(model_path)


def prompt_voice_model(prompter: Prompter) -> str:
    print("\n--- Voice model ---")
    print("Enter a path to a Piper voice .onnx you already have, or leave blank")
    print(f"to auto-download the default ({_DEFAULT_VOICE_NAME}) — see")
    print("  https://github.com/rhasspy/piper/blob/master/VOICES.md for other options.")
    path = prompter.text("voice_model_path", "Path to an existing .onnx voice model (blank = auto-download default)")
    if path and Path(path).exists():
        return path
    if path:
        print(f"{path!r} doesn't exist — falling back to the default download.")
    return _download_default_voice()


_DEFAULT_QISKIT_VENV = Path.home() / "Projects/quantum-projects/venv"


def setup_qiskit_venv(prompter: Prompter) -> Optional[str]:
    """Optional — Qiskit circuit verification (qiskit_runner.py/qiskit_docs.py)
    is deliberately NOT part of flora-ai's own venv (a large, opt-in
    dependency tree most installs don't need, see that file's own WHY note).
    Returns a FLORA_QISKIT_VENV_PYTHON value to add to .env, or None if the
    default location is used (already qiskit_runner.py's own fallback, so
    nothing needs to go in .env for that case) or the step was skipped."""
    print("\n--- Qiskit circuit verification (optional) ---")
    if not prompter.confirm(
        "setup_qiskit_venv",
        "Set up a separate Qiskit environment (qiskit + qiskit-aer + matplotlib) "
        "so Florinda can actually run circuits instead of just reasoning about "
        "them? Optional, and a sizeable download.",
        default=False,
    ):
        print(
            "Skipped — Qiskit requests will fail with a clear error until you set "
            "one up later (see SETUP.md's Qiskit section)."
        )
        return None
    venv_path = Path(
        prompter.text(
            "qiskit_venv_path", "Where should this venv live?", default=str(_DEFAULT_QISKIT_VENV)
        )
    ).expanduser()
    if venv_path.exists():
        print(f"{venv_path} already exists, reusing it.")
    else:
        venv_path.parent.mkdir(parents=True, exist_ok=True)
        _run([sys.executable, "-m", "venv", str(venv_path)])
    pip = str(venv_path / "bin" / "pip")
    _run([pip, "install", "--upgrade", "pip"])
    _run([pip, "install", "qiskit", "qiskit-aer", "matplotlib"])
    print(f"Qiskit environment ready at {venv_path}.")
    return None if venv_path == _DEFAULT_QISKIT_VENV else str(venv_path / "bin" / "python3")


def write_env(env_vars: dict, prompter: Prompter) -> None:
    if ENV_PATH.exists():
        if not prompter.confirm("overwrite_env", f"{ENV_PATH} already exists. Overwrite?", default=False):
            print("Kept existing .env unchanged.")
            return
    lines = [f'{key}="{value}"' for key, value in env_vars.items() if value]
    ENV_PATH.write_text("\n".join(lines) + "\n")
    os.chmod(ENV_PATH, 0o600)
    print(f"Wrote {ENV_PATH} (mode 600).")


def _docker_daemon_active() -> bool:
    result = subprocess.run(["systemctl", "is-active", "docker"], capture_output=True, text=True)
    return result.stdout.strip() == "active"


def _user_in_docker_group() -> bool:
    result = subprocess.run(["groups"], capture_output=True, text=True)
    return "docker" in result.stdout.split()


def setup_docker(prompter: Prompter) -> bool:
    """Returns True if a re-login is needed before `docker` works without sudo."""
    print("\n--- SearXNG search container ---")
    if not shutil.which("docker"):
        print("docker not found on PATH — skipping. Web/academic search won't work until it's set up.")
        return False

    if not _docker_daemon_active():
        if prompter.confirm("enable_docker_daemon", "docker service isn't running. Enable and start it now?"):
            _run(["sudo", "systemctl", "enable", "--now", "docker"])
        else:
            print("Skipped — docker compose will fail until the docker service is running.")
            return False

    needs_relogin = False
    if not _user_in_docker_group():
        if prompter.confirm(
            "add_docker_group",
            f"Add {os.environ.get('USER', 'your user')} to the docker group (avoids needing sudo for docker)?",
        ):
            _run(["sudo", "usermod", "-aG", "docker", os.environ.get("USER", "")])
            needs_relogin = True
            print("Added to the docker group — this takes effect on your NEXT login, not this session.")

    if not prompter.confirm("setup_docker", "Bring up the SearXNG container (docker compose up -d)?"):
        return needs_relogin
    # WHY sudo here specifically if group membership was JUST granted: a
    # newly-added group membership doesn't apply to the current shell/session
    # until re-login — running this one command via sudo lets setup finish
    # today anyway, without forcing an immediate logout mid-install.
    compose_cmd = ["sudo", "docker", "compose", "up", "-d"] if needs_relogin else ["docker", "compose", "up", "-d"]
    _run(compose_cmd, cwd=REPO_ROOT)
    return needs_relogin


_UNIT_TEMPLATE = """[Unit]
Description=Florinda always-on service (screen watch + push-to-talk)
After=default.target
StartLimitBurst=5
StartLimitIntervalSec=60

[Service]
WorkingDirectory={repo_root}
{exec_start_pre}ExecStart={repo_root}/venv/bin/python3 {repo_root}/flora_service.py
Restart=on-failure
RestartSec=3s

[Install]
WantedBy=default.target
"""

# WHY conditional on a real Wayland session: verified this project's own
# real unit file needs this exact wait-loop — on a fresh boot the daemon can
# otherwise win the race and start before WAYLAND_DISPLAY is imported into
# the systemd user environment, leaving screenshots/GUI popups with nowhere
# to render, no visible error. But the thing being waited for
# (WAYLAND_DISPLAY) only ever appears on a Wayland session — unconditionally
# adding this on an X11 desktop (GNOME/KDE can run either) would just make
# the service wait the full 30s and then fail to start at all, every time.
_EXEC_START_PRE = (
    'ExecStartPre=/bin/sh -c \'for i in $(seq 1 30); do systemctl --user show-environment | '
    'grep -q "^WAYLAND_DISPLAY=" && exit 0; sleep 1; done; '
    'echo "WAYLAND_DISPLAY never appeared in the systemd user environment" >&2; exit 1\'\n'
)


def setup_systemd_service(prompter: Prompter) -> None:
    print("\n--- systemd service ---")
    if not prompter.confirm("setup_systemd", "Install and enable the flora-daemon.service systemd unit?"):
        return
    exec_start_pre = _EXEC_START_PRE if is_wayland_session() else ""
    SYSTEMD_UNIT_DIR.mkdir(parents=True, exist_ok=True)
    SYSTEMD_UNIT_PATH.write_text(_UNIT_TEMPLATE.format(repo_root=REPO_ROOT, exec_start_pre=exec_start_pre))
    _run(["systemctl", "--user", "daemon-reload"])
    _run(["systemctl", "--user", "enable", "--now", "flora-daemon.service"])


_HYPRLAND_LUA_SNIPPET = """
-- Add to your Hyprland config (e.g. custom.lua):
local FLORA_PTT_HOOK = "python3 -S {repo_root}/scripts/flora_ptt_hook.py"
local SUPER_L_KEYCODE = 133  -- evdev KEY_LEFTMETA(125) + 8

hl.on("input.keyboard.key", function(keycode, _timestamp, state)
    if keycode ~= SUPER_L_KEYCODE then return end
    if state == 1 then os.execute(FLORA_PTT_HOOK .. " PRESS &")
    elseif state == 0 then os.execute(FLORA_PTT_HOOK .. " RELEASE &") end
end)

-- Optional: float/size/center the figure popups (Qiskit/LaTeX/RDKit)
hl.window_rule({{ name = "flora-ai-figure-float", match = {{ class = "^(flora-figure)$" }}, float = true }})
hl.window_rule({{ name = "flora-ai-figure-size",  match = {{ class = "^(flora-figure)$" }}, size = "720 560" }})
hl.window_rule({{ name = "flora-ai-figure-center",match = {{ class = "^(flora-figure)$" }}, center = true }})
"""

_DEFAULT_KEY_COMBO = "Super+Shift+Space"  # WM-agnostic form the user types/accepts; translated per-WM below

# WHY a WM-agnostic combo instead of asking for each WM's own syntax:
# Hyprland ("MODS, KEY"), Sway ("$mod+shift+space"), and GNOME
# ("<Super><Shift>space") all spell the same physical key combo differently
# — asking the user to already know whichever one applies to their WM would
# just be a worse version of the same prompt. One canonical name per
# modifier (Super/Shift/Ctrl/Alt) means the user only ever answers once,
# regardless of which of the three auto-configurable WMs they're on.
_MOD_ALIASES = {
    "super": "SUPER", "win": "SUPER", "windows": "SUPER", "meta": "SUPER", "cmd": "SUPER",
    "shift": "SHIFT",
    "ctrl": "CTRL", "control": "CTRL",
    "alt": "ALT",
}


def _parse_key_combo(combo: str) -> tuple[list[str], str]:
    """'Super+Shift+Space' -> (['SUPER', 'SHIFT'], 'SPACE'). Raises
    InstallError on anything that isn't at least one recognized modifier
    plus a key — never silently accepts something we can't actually
    translate into a real bind."""
    parts = [p.strip() for p in combo.replace("-", "+").split("+") if p.strip()]
    if len(parts) < 2:
        raise InstallError(
            f"Invalid key combo {combo!r} — expected at least one modifier and a "
            "key, e.g. 'Super+Shift+Space'"
        )
    *mod_parts, key = parts
    mods: list[str] = []
    for mod in mod_parts:
        canonical = _MOD_ALIASES.get(mod.lower())
        if canonical is None:
            raise InstallError(
                f"Unknown modifier {mod!r} in {combo!r} — supported: Super, Shift, Ctrl, Alt"
            )
        if canonical not in mods:
            mods.append(canonical)
    if not key:
        raise InstallError(f"Invalid key combo {combo!r} — missing a key after the modifiers")
    return mods, key.upper()


def _combo_label(mods: list[str], key: str) -> str:
    return "+".join(mod.capitalize() for mod in mods) + "+" + key.capitalize()


def _combo_to_hyprland(mods: list[str], key: str) -> str:
    return f"{' '.join(mods)}, {key}"


def _combo_to_sway(mods: list[str], key: str) -> str:
    # WHY $mod for SUPER specifically, not the literal keysym: Sway's own
    # config already defines `$mod` (almost always Mod4/Super) — reusing it
    # matches however the user's own config defines their modifier, rather
    # than assuming Super specifically. Other modifiers don't have this
    # indirection in a stock Sway config, so they're used literally.
    sway_names = {"SUPER": "$mod", "SHIFT": "shift", "CTRL": "ctrl", "ALT": "alt"}
    return "+".join(sway_names[mod] for mod in mods) + "+" + key.lower()


def _combo_to_gnome(mods: list[str], key: str) -> str:
    return "".join(f"<{mod.capitalize()}>" for mod in mods) + key.lower()


def _prompt_key_combo(prompter: Prompter) -> tuple[list[str], str, str]:
    """Returns (mods, key, label). Falls back to the default combo (with a
    printed warning, not a crash) if the user's input can't be parsed —
    getting the keybind wrong shouldn't take down the rest of the install."""
    raw = prompter.text("ptt_key_combo", "Push-to-talk key combo", default=_DEFAULT_KEY_COMBO)
    try:
        mods, key = _parse_key_combo(raw)
    except InstallError as error:
        print(f"{error} — falling back to the default ({_DEFAULT_KEY_COMBO}).")
        mods, key = _parse_key_combo(_DEFAULT_KEY_COMBO)
    return mods, key, _combo_label(mods, key)


_HYPRLAND_PTT_CONF_NAME = "florinda-ptt.conf"
_HYPRLAND_PTT_CONF_TEMPLATE = """# Florinda push-to-talk (added by install.py) — hold {label} to talk.
# Remove this file and the `source` line pointing at it in hyprland.conf to undo.
bind = {mods_key}, exec, python3 -S {repo_root}/scripts/flora_ptt_hook.py PRESS
bindr = {mods_key}, exec, python3 -S {repo_root}/scripts/flora_ptt_hook.py RELEASE
"""

_SWAY_PTT_CONF_NAME = "florinda-ptt.conf"
_SWAY_PTT_CONF_TEMPLATE = """# Florinda push-to-talk (added by install.py) — hold {label} to talk.
# Remove this file and the `include` line pointing at it in your sway config to undo.
bindsym {sway_combo} exec python3 -S {repo_root}/scripts/flora_ptt_hook.py PRESS
bindsym --release {sway_combo} exec python3 -S {repo_root}/scripts/flora_ptt_hook.py RELEASE
"""


def _backup_and_append(config_path: Path, marker: str, addition: str) -> bool:
    """Appends `addition` to `config_path` unless `marker` is already present
    (idempotent — safe to re-run). Backs up the original first (matching
    this project's own established .bak-pre-<name> convention for editing
    external configs). Returns True if it actually changed anything."""
    existing = config_path.read_text() if config_path.exists() else ""
    if marker in existing:
        return False
    if config_path.exists():
        backup = config_path.with_name(config_path.name + ".bak-pre-florinda")
        shutil.copy2(config_path, backup)
        print(f"Backed up {config_path} to {backup}.")
    with open(config_path, "a") as f:
        f.write(addition)
    return True


def _hyprland_uses_classic_conf() -> bool:
    """True for a typical/default Hyprland install (classic bind=/source=
    syntax) — false for a Lua-based config (e.g. ML4W dotfiles, which use
    hyprland.lua as the real entry point). Verified live on this exact
    machine: its hyprland.conf is just an "autogenerated = 1" stub while the
    real config lives in hyprland.lua — a fresh/default Hyprland install
    doesn't have that split, only a heavily customized Lua-based setup does.
    Only the classic form is safe to auto-edit here: `source =` is a
    stable, well-documented single-purpose include mechanism, whereas a
    Lua-based setup could have any module structure at all."""
    if (Path.home() / ".config/hypr/hyprland.lua").exists():
        return False
    conf_path = Path.home() / ".config/hypr/hyprland.conf"
    if not conf_path.exists():
        return False
    return len(conf_path.read_text().strip().splitlines()) > 3


def _confirm_hyprland_bind(mods_key: str) -> None:
    """Real confirmation, not an assumption: asks the running compositor
    itself (via `hyprctl binds -j`, not just re-reading the file we wrote)
    whether it actually registered both the PRESS and RELEASE binds —
    catches a bind Hyprland silently rejected (bad syntax, key name typo)
    that a successful `hyprctl reload` exit code alone wouldn't surface."""
    if not shutil.which("hyprctl"):
        print("hyprctl not found — can't confirm the bind registered; verify manually with `hyprctl binds`.")
        return
    result = subprocess.run(["hyprctl", "-j", "binds"], capture_output=True, text=True)
    if result.returncode != 0:
        print(f"Couldn't query hyprctl binds to confirm: {result.stderr.strip()}")
        return
    try:
        binds = json.loads(result.stdout)
    except json.JSONDecodeError:
        print("Couldn't parse `hyprctl binds -j` output to confirm — verify manually.")
        return
    found_press = any("flora_ptt_hook.py" in b.get("arg", "") and "PRESS" in b.get("arg", "") for b in binds)
    found_release = any("flora_ptt_hook.py" in b.get("arg", "") and "RELEASE" in b.get("arg", "") for b in binds)
    if found_press and found_release:
        print(f"Confirmed: Hyprland has both binds registered for {mods_key}.")
    else:
        missing = ", ".join(name for name, ok in (("PRESS", found_press), ("RELEASE", found_release)) if not ok)
        print(f"Warning: hyprctl binds doesn't show the {missing} bind — check hyprland.conf for a typo.")


def _setup_hyprland_keybind(prompter: Prompter) -> None:
    if not _hyprland_uses_classic_conf():
        print(
            "This looks like a Lua-based Hyprland config (e.g. ML4W dotfiles) — "
            "too much variation between setups to safely auto-edit. Add this yourself:"
        )
        print(_HYPRLAND_LUA_SNIPPET.format(repo_root=REPO_ROOT))
        return
    mods, key, label = _prompt_key_combo(prompter)
    if not prompter.confirm("setup_hyprland_keybind", f"Auto-configure the push-to-talk keybind (hold {label})?"):
        return
    mods_key = _combo_to_hyprland(mods, key)
    hypr_dir = Path.home() / ".config/hypr"
    ptt_conf = hypr_dir / _HYPRLAND_PTT_CONF_NAME
    ptt_conf.write_text(_HYPRLAND_PTT_CONF_TEMPLATE.format(label=label, mods_key=mods_key, repo_root=REPO_ROOT))
    main_conf = hypr_dir / "hyprland.conf"
    source_line = f"\n# Florinda push-to-talk\nsource = ~/.config/hypr/{_HYPRLAND_PTT_CONF_NAME}\n"
    if _backup_and_append(main_conf, _HYPRLAND_PTT_CONF_NAME, source_line):
        print(f"Wrote {ptt_conf} and sourced it from {main_conf}.")
    else:
        print(f"Wrote {ptt_conf} (already sourced from {main_conf}).")
    if shutil.which("hyprctl"):
        result = subprocess.run(["hyprctl", "reload"], capture_output=True, text=True)
        print("hyprctl reload: " + ("OK" if result.returncode == 0 else result.stderr.strip()))
        if result.returncode == 0:
            _confirm_hyprland_bind(mods_key)


def _setup_sway_keybind(prompter: Prompter) -> None:
    mods, key, label = _prompt_key_combo(prompter)
    if not prompter.confirm("setup_sway_keybind", f"Auto-configure the push-to-talk keybind (hold {label})?"):
        return
    sway_dir = Path.home() / ".config/sway"
    if not sway_dir.exists():
        print(f"{sway_dir} not found — skipping. See SETUP.md to wire this up manually.")
        return
    ptt_conf = sway_dir / _SWAY_PTT_CONF_NAME
    sway_combo = _combo_to_sway(mods, key)
    ptt_conf.write_text(_SWAY_PTT_CONF_TEMPLATE.format(label=label, sway_combo=sway_combo, repo_root=REPO_ROOT))
    main_conf = sway_dir / "config"
    include_line = f"\n# Florinda push-to-talk\ninclude {_SWAY_PTT_CONF_NAME}\n"
    if _backup_and_append(main_conf, _SWAY_PTT_CONF_NAME, include_line):
        print(f"Wrote {ptt_conf} and included it from {main_conf}.")
    else:
        print(f"Wrote {ptt_conf} (already included from {main_conf}).")
    if shutil.which("swaymsg"):
        result = subprocess.run(["swaymsg", "reload"], capture_output=True, text=True)
        # WHY a successful reload IS the confirmation here, unlike Hyprland:
        # Sway's IPC protocol has no "list active binds" query to check
        # against (verified against Sway's documented message types — only
        # Hyprland exposes `binds -j`), so a config Sway actually accepted
        # without error is the strongest signal available that our exact
        # bindsym lines parsed correctly, short of physically pressing the key.
        if result.returncode == 0:
            print(f"Confirmed: sway accepted the reloaded config — {sway_combo} is active.")
        else:
            print(f"swaymsg reload reported an error — the bind may not be active: {result.stderr.strip()}")


def _gnome_schema_available(schema: str) -> bool:
    result = subprocess.run(["gsettings", "list-schemas"], capture_output=True, text=True)
    return schema in result.stdout.split()


def _setup_gnome_keybind(prompter: Prompter) -> None:
    mods, key, label = _prompt_key_combo(prompter)
    if not prompter.confirm(
        "setup_gnome_keybind",
        f"Auto-configure a push-to-talk keybind (press {label} to start, press again to stop)?",
    ):
        return
    # WHY toggle, not hold: GNOME's custom-keybinding schema only fires a
    # command on key PRESS — there's no separate "on release" action to bind
    # a custom shortcut to, so true hold-to-talk isn't available through
    # GNOME's standard shortcut system at all. This uses
    # flora_ptt_toggle_hook.py (PRESS on the first press, RELEASE on the
    # second) instead of flora_ptt_hook.py.
    base = "org.gnome.settings-daemon.plugins.media-keys"
    # WHY checked explicitly rather than letting `gsettings set` fail:
    # verified live — `gsettings` itself is on PATH even on a non-GNOME
    # machine (it's a generic glib CLI tool, present here as a transitive
    # dependency on a Hyprland system with zero GNOME session installed),
    # but the schema it needs only exists with gnome-settings-daemon
    # actually installed. Without this check, a `desktop` mis-detection or
    # a partial GNOME install would raise CalledProcessError and abort the
    # ENTIRE installer, not just skip this one optional step.
    if not _gnome_schema_available(base):
        print(
            f"gsettings schema {base!r} isn't available (gnome-settings-daemon not installed?) — "
            "skipping. See SETUP.md to wire up a keybind manually."
        )
        return
    binding = _combo_to_gnome(mods, key)
    command = f"python3 -S {REPO_ROOT}/scripts/flora_ptt_toggle_hook.py"
    path = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/florinda-ptt/"
    existing = subprocess.run(["gsettings", "get", base, "custom-keybindings"], capture_output=True, text=True)
    current_paths = existing.stdout.strip()
    if path not in current_paths:
        if current_paths in ("@as []", ""):
            new_paths = f"['{path}']"
        else:
            new_paths = current_paths.rstrip("]") + f", '{path}']"
        _run(["gsettings", "set", base, "custom-keybindings", new_paths])
    keybinding_schema = f"{base}.custom-keybinding:{path}"
    _run(["gsettings", "set", keybinding_schema, "name", "Florinda push-to-talk"])
    _run(["gsettings", "set", keybinding_schema, "command", command])
    _run(["gsettings", "set", keybinding_schema, "binding", binding])
    _confirm_gnome_keybind(base, path, keybinding_schema, command, binding)


def _confirm_gnome_keybind(base: str, path: str, keybinding_schema: str, command: str, binding: str) -> None:
    """Real confirmation: reads every value back from gsettings rather than
    assuming the `gsettings set` calls above landed — catches a schema
    silently rejecting a value (e.g. a binding string GNOME doesn't
    recognize) that a zero exit code from `set` wouldn't necessarily
    surface on its own."""
    paths = subprocess.run(["gsettings", "get", base, "custom-keybindings"], capture_output=True, text=True).stdout.strip()
    got_name = subprocess.run(["gsettings", "get", keybinding_schema, "name"], capture_output=True, text=True).stdout.strip().strip("'")
    got_command = subprocess.run(["gsettings", "get", keybinding_schema, "command"], capture_output=True, text=True).stdout.strip().strip("'")
    got_binding = subprocess.run(["gsettings", "get", keybinding_schema, "binding"], capture_output=True, text=True).stdout.strip().strip("'")
    problems = []
    if path not in paths:
        problems.append(f"custom-keybindings list doesn't include {path!r}")
    if got_command != command:
        problems.append(f"command read back as {got_command!r}, expected {command!r}")
    if got_binding != binding:
        problems.append(f"binding read back as {got_binding!r}, expected {binding!r}")
    if problems:
        print("Warning: GNOME keybinding may not have registered correctly:")
        for problem in problems:
            print(f"  - {problem}")
    else:
        print(f"Confirmed: GNOME keybinding {got_name!r} registered — {got_binding} runs {got_command}.")


_GNOME_EXTENSION_UUID = "florinda-status@florinda-ai"
_GNOME_EXTENSION_SRC_DIR = REPO_ROOT / "gnome-extension" / _GNOME_EXTENSION_UUID


def _gnome_extension_enabled(uuid: str) -> bool:
    result = subprocess.run(["gnome-extensions", "list", "--enabled"], capture_output=True, text=True)
    return uuid in result.stdout.split()


def _install_gnome_status_extension(prompter: Prompter) -> None:
    """The GNOME equivalent of the project's Waybar custom/flora module —
    GNOME has no Waybar, so a top-bar state indicator has to come from a
    GNOME Shell extension instead. See gnome-extension/florinda-status@
    florinda-ai/extension.js for the actual indicator/menu code."""
    if not prompter.confirm(
        "install_gnome_extension",
        "Install the Florinda Status GNOME Shell extension (top-bar state "
        "indicator + a menu to stop/start Florinda and view the activity log)?",
    ):
        return
    if not _GNOME_EXTENSION_SRC_DIR.exists():
        print(f"{_GNOME_EXTENSION_SRC_DIR} not found in this checkout — skipping.")
        return
    dest_dir = Path.home() / ".local/share/gnome-shell/extensions" / _GNOME_EXTENSION_UUID
    dest_dir.parent.mkdir(parents=True, exist_ok=True)
    if dest_dir.exists():
        shutil.rmtree(dest_dir)
    shutil.copytree(_GNOME_EXTENSION_SRC_DIR, dest_dir)
    print(f"Copied extension to {dest_dir}.")
    if not shutil.which("gnome-extensions"):
        print(
            "`gnome-extensions` CLI not found — log out and back in, then enable "
            f"\"Florinda Status\" via the Extensions app (or run `gnome-extensions "
            f"enable {_GNOME_EXTENSION_UUID}` if that command becomes available)."
        )
        return
    result = subprocess.run(["gnome-extensions", "enable", _GNOME_EXTENSION_UUID], capture_output=True, text=True)
    if result.returncode != 0:
        print(
            f"`gnome-extensions enable` reported: {result.stderr.strip()} — you may "
            f"need to log out and back in first, then run `gnome-extensions enable "
            f"{_GNOME_EXTENSION_UUID}` yourself."
        )
        return
    # WHY read back `gnome-extensions list --enabled` instead of trusting
    # `enable`'s exit code alone: a brand-new extension GNOME Shell hasn't
    # picked up yet (common right after copying its files in, especially on
    # Wayland) can return success from `enable` while not actually showing
    # up until a session restart — the same "confirm what actually
    # happened, don't assume" approach as the Hyprland/GNOME keybind checks.
    if _gnome_extension_enabled(_GNOME_EXTENSION_UUID):
        print("Confirmed: Florinda Status is enabled — it should be visible in the top bar now.")
    else:
        print(
            "Enabled, but couldn't confirm it's active yet (gnome-extensions list "
            "--enabled doesn't show it) — this can happen right after installing a "
            "brand-new extension. Log out and back in if it doesn't appear."
        )


def setup_keybind(desktop: str, prompter: Prompter) -> None:
    print("\n--- Push-to-talk keybind ---")
    desktop_lower = desktop.lower()
    if desktop == "Hyprland":
        _setup_hyprland_keybind(prompter)
    elif "sway" in desktop_lower:
        _setup_sway_keybind(prompter)
    elif "gnome" in desktop_lower:
        _setup_gnome_keybind(prompter)
        _install_gnome_status_extension(prompter)
    else:
        # WHY KDE isn't auto-configured: KDE's custom-command shortcuts live
        # in khotkeysrc, whose format has changed across KDE4/Plasma5/
        # Plasma6 and isn't something this installer can verify against a
        # real KDE session — a silently-wrong auto-write here would be
        # worse than asking the user to do it once by hand. Same reasoning
        # extends to anything else not explicitly handled above.
        print(
            f"Desktop {desktop!r} isn't one of the auto-configurable ones (Hyprland/Sway/GNOME) — "
            "see SETUP.md to wire up a keybind manually. On KDE: System Settings > Shortcuts > "
            f"Custom Shortcuts, bound to: python3 -S {REPO_ROOT}/scripts/flora_ptt_toggle_hook.py"
        )


def run_verification() -> None:
    print("\n--- Verification ---")
    python3 = str(VENV_DIR / "bin" / "python3")
    try:
        subprocess.run([python3, "-c", "from config import ConfigVault; ConfigVault(); print('config OK')"],
                        cwd=REPO_ROOT, check=True)
    except subprocess.CalledProcessError:
        print("config.py failed to load — check .env for missing/invalid values above.")
        return
    if shutil.which("systemctl"):
        status = subprocess.run(
            ["systemctl", "--user", "is-active", "flora-daemon.service"],
            capture_output=True, text=True,
        )
        print(f"flora-daemon.service: {status.stdout.strip() or 'not installed'}")
    print("\nDone. See SETUP.md's Verification section for the full end-to-end checklist.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Interactive installer for Florinda")
    parser.add_argument(
        "--answers-file", help="JSON file of pre-supplied answers, for non-interactive/testing use"
    )
    args = parser.parse_args()

    answers = {}
    if args.answers_file:
        answers = json.loads(Path(args.answers_file).read_text())
    prompter = Prompter(answers)

    print("=== Florinda installer ===\n")
    pm = detect_package_manager()
    desktop = detect_desktop()
    print(f"Package manager: {pm} | Desktop: {desktop}")

    try:
        install_system_packages(pm, prompter)
        setup_venv(prompter)
        setup_ollama(pm, prompter)
        env_vars = prompt_ai_provider(prompter)
        voice_model = prompt_voice_model(prompter)
        if voice_model:
            env_vars["DEFAULT_VOICE_MODEL"] = voice_model
        qiskit_venv_python = setup_qiskit_venv(prompter)
        if qiskit_venv_python:
            env_vars["FLORA_QISKIT_VENV_PYTHON"] = qiskit_venv_python
        write_env(env_vars, prompter)
        needs_relogin = setup_docker(prompter)
        setup_systemd_service(prompter)
        setup_keybind(desktop, prompter)
        run_verification()
        if needs_relogin:
            print(
                "\nNote: you were added to the docker group during this install — "
                "log out and back in before running docker commands without sudo."
            )
    except InstallError as error:
        print(f"\nError: {error}", file=sys.stderr)
        sys.exit(1)
    except subprocess.CalledProcessError as error:
        print(f"\nA command failed: {error}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
