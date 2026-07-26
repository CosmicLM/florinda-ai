#!/usr/bin/env python3
"""hypr_ptt_hook.py — the ONLY thing Hyprland's Super_L bind actually executes.

WHY stdlib-only, no project imports: this must return near-instantly so it
never blocks Hyprland's key-event dispatch. All real work (recording,
transcription, calling the AI) happens in the already-running hypr_service.py
process; this script just signals it over a Unix socket and exits.
"""
import socket
import sys
import time
from pathlib import Path

SOCKET_PATH = "/home/manjaro/.local/share/hypr-ai/ptt.sock"
HOOK_LOG_PATH = Path.home() / ".local/share/hypr-ai/ptt-hook.log"


def _log(line: str) -> None:
    """Separate from the main service log — proves whether Hyprland's bind
    fired at all, independent of whether the socket send succeeded."""
    try:
        with open(HOOK_LOG_PATH, "a") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {line}\n")
    except OSError:
        pass


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in ("PRESS", "RELEASE"):
        _log(f"invalid args: {sys.argv[1:]!r}")
        return 1
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.3)
            sock.connect(SOCKET_PATH)
            sock.sendall((sys.argv[1] + "\n").encode())
        _log(f"sent {sys.argv[1]} OK")
    except OSError as error:
        _log(f"failed to send {sys.argv[1]}: {error}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
