#!/usr/bin/env python3
"""waybar_hypr_status.py — reads Hypr's status file and prints Waybar custom-module JSON.

WHY stdlib-only, no project imports: same reasoning as hypr_ptt_hook.py — this
runs as Waybar's exec script on a fast interval, so it should stay a fast,
dependency-light process rather than importing the project's venv-only deps.
"""
import json
import sys
import time
from pathlib import Path

STATUS_PATH = Path.home() / ".local/share/hypr-ai/status.json"
STALE_AFTER_S = 60

_SOLID_DOT = "●"
_HOLLOW_DOT = "○"

_STATE_LABELS = {
    # (word, tooltip) — WHY a dot instead of an emoji: the dot inherits its
    # color from the SAME per-state CSS class already applied to the text
    # (#custom-hypr.listening/.thinking/.talking in style.css), so it's
    # automatically colored correctly with zero extra styling — an emoji
    # carries its own fixed color/rendering that can't do that. The word is
    # still what actually disambiguates the state; the dot is a lighter
    # visual marker, and for "talking" specifically it alternates solid/
    # hollow (see StatusBroadcaster.set_talking's blink thread) so an active
    # reply visibly reads as "live" rather than a static icon.
    "idle": ("Idle", "Hypr: idle"),
    "listening": ("Listening", "Hypr: listening..."),
    "thinking": ("Thinking", "Hypr: thinking..."),
    "talking": ("Talking", "Hypr: talking..."),
    "watching": ("Watching", "Hypr: watching the screen..."),
}
_OFFLINE_LABEL = ("Offline", "Hypr: offline")


def main() -> None:
    if not STATUS_PATH.exists():
        _emit(f"{_SOLID_DOT} {_OFFLINE_LABEL[0]}", _OFFLINE_LABEL[1], "offline")
        return
    try:
        with open(STATUS_PATH) as status_file:
            data = json.load(status_file)
        state = data.get("state", "idle")
        updated_at = data.get("updated_at", 0)
        phase = data.get("phase", 0)
    except (OSError, json.JSONDecodeError):
        _emit(f"{_SOLID_DOT} {_OFFLINE_LABEL[0]}", _OFFLINE_LABEL[1], "offline")
        return

    # WHY self-heal to idle on stale non-idle state: a crash mid-"thinking"/
    # "talking" would otherwise leave the widget stuck showing that forever.
    if state != "idle" and (time.time() - updated_at) > STALE_AFTER_S:
        state = "idle"

    dot = _HOLLOW_DOT if (state == "talking" and phase == 1) else _SOLID_DOT
    label, tooltip = _STATE_LABELS.get(state, _STATE_LABELS["idle"])
    _emit(f"{dot} {label}", tooltip, state)


def _emit(text: str, tooltip: str, css_class: str) -> None:
    print(json.dumps({"text": text, "tooltip": tooltip, "class": css_class}))


if __name__ == "__main__":
    main()
    sys.exit(0)
