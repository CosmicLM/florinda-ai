#!/usr/bin/env python3
"""flora_ptt_toggle_hook.py — a single-keypress toggle wrapper around
flora_ptt_hook.py's PRESS/RELEASE protocol, for desktops whose global
shortcut system only supports "run this command when the shortcut is
pressed" with no separate release-triggered action.

WHY this exists: verified against how GNOME (org.gnome.settings-daemon.
plugins.media-keys custom-keybindings) and KDE (kglobalshortcuts) actually
expose custom shortcuts — both only fire a command on key press, with no
built-in equivalent to Hyprland's `bindr`/Sway's `bindsym --release` for a
separate release action. True hold-to-talk (record while the key is
physically held) isn't achievable through either desktop's standard
shortcut system at all. This makes the same PRESS/RELEASE protocol work
as a toggle instead: press once to start recording (sends PRESS), press
again to stop and transcribe (sends RELEASE) — same socket protocol
ptt_ipc.py already expects, just driven by two presses instead of a
press-and-hold.

WHY a state file instead of in-process state: each keypress invokes this
script as a brand-new process (the WM/DE spawns it fresh every time) —
nothing survives between invocations except the filesystem.
"""
import socket
import sys
import time
from pathlib import Path

SOCKET_PATH = str(Path.home() / ".local/share/flora-ai/ptt.sock")
HOOK_LOG_PATH = Path.home() / ".local/share/flora-ai/ptt-hook.log"
STATE_PATH = Path.home() / ".local/share/flora-ai/ptt_toggle_state"


def _log(line: str) -> None:
    try:
        with open(HOOK_LOG_PATH, "a") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {line}\n")
    except OSError:
        pass


def _send(message: str) -> None:
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.3)
            sock.connect(SOCKET_PATH)
            sock.sendall((message + "\n").encode())
        _log(f"sent {message} OK (toggle)")
    except OSError as error:
        _log(f"failed to send {message}: {error}")


def main() -> int:
    currently_recording = STATE_PATH.exists()
    if currently_recording:
        STATE_PATH.unlink(missing_ok=True)
        _send("RELEASE")
    else:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATE_PATH.touch()
        _send("PRESS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
