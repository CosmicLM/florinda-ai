"""watch_toggle.py — lets the user pause/resume Florinda's passive screen-watching
(OCR + quantum-keyword detection + sys-info updates) at runtime, without
restarting the always-on service.

WHY a flag file instead of a socket or signal: flora_service.py's screen-watch
loop already polls on a short, fixed interval (screen_watch_interval_s,
default 4s) — checking one small JSON file each tick is simpler than adding a
new socket server for something that doesn't need to react any faster than
the loop already does. The AI's COMMAND always runs as a fresh subprocess
(see executor.py) with no direct handle to the already-running service, so
some out-of-process signal is required either way; a file is the lightest
one that fits the existing polling design.

WHY an auto-expiry instead of "paused" meaning paused forever: this exists so
the user can do something sensitive (enter a password, look at banking info,
a private conversation) without it being OCR'd — a genuinely temporary need.
Nothing else in this codebase actively reminds the user screen-watching is
off, so a forgotten toggle would otherwise silently disable it (including
quantum-keyword detection) indefinitely. Auto-resuming after _MAX_PAUSE_S
means the worst case is "watching came back on its own after 30 minutes,"
never "I turned it off once in 2026 and never noticed it stayed off."
"""
import json
import sys
import time
from pathlib import Path

WATCH_STATE_PATH = Path.home() / ".local/share/flora-ai/watch_paused.json"
_MAX_PAUSE_S = 1800.0  # 30 minutes — matches conversation_memory's staleness-reset scale


def is_paused() -> bool:
    if not WATCH_STATE_PATH.exists():
        return False
    try:
        data = json.loads(WATCH_STATE_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return False
    if not data.get("paused", False):
        return False
    return (time.time() - data.get("since", 0)) < _MAX_PAUSE_S


def set_paused(paused: bool) -> None:
    WATCH_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    WATCH_STATE_PATH.write_text(json.dumps({"paused": paused, "since": time.time()}))


def _main() -> None:
    action = sys.argv[1] if len(sys.argv) >= 2 else None
    if action == "off":
        set_paused(True)
        print(f"Screen watching paused (auto-resumes in {int(_MAX_PAUSE_S / 60)} minutes if not turned back on sooner).")
    elif action == "on":
        set_paused(False)
        print("Screen watching resumed.")
    elif action == "status":
        print("paused" if is_paused() else "active")
    else:
        print("usage: watch_toggle.py off|on|status", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    _main()
