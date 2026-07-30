#!/usr/bin/env python3
"""flora_kill_switch.py — right-click handler for Waybar's custom/flora module.

Toggles flora-daemon.service on or off: stops it if running, starts it if
not. Meant to be wired up as that module's `on-click-right` command, giving
a one-click way to fully silence Florinda (mic, screen-watching, all
background watchers) without leaving a terminal open — the module's
existing `on-click` already opens the read-only activity log, so this adds
the other half: actually controlling whether it's running at all.

WHY stdlib-only, no project imports: same reasoning as flora_ptt_hook.py —
this runs as a Waybar click handler, so it should stay a fast,
dependency-light process rather than importing the project's venv-only
deps just to shell out to systemctl.

WHY explicitly nudge Waybar instead of waiting on it: `systemctl --user
stop` returns once systemd has sent SIGTERM, but flora_service.py's own
StatusBroadcaster.clear() (which removes status.json and nudges Waybar)
only runs once the process actually finishes its shutdown handling — a
real gap of up to a second or two. Nudging here too means the widget
flips to "Offline" the moment the click handler returns, not whenever the
process happens to finish exiting. On start, the daemon's own
`status.set_idle()` call will nudge Waybar again once it's actually up
(see flora_service.py) — this nudge just avoids sitting on the stale
"Offline" reading in the meantime.
"""
import os
import subprocess
import sys

SERVICE_NAME = "flora-daemon.service"
WAYBAR_SIGNAL_NUM = int(os.environ.get("FLORA_WAYBAR_SIGNAL_NUM", "8"))


def _is_active() -> bool:
    result = subprocess.run(
        ["systemctl", "--user", "is-active", "--quiet", SERVICE_NAME]
    )
    return result.returncode == 0


def _nudge_waybar() -> None:
    try:
        subprocess.run(
            ["pkill", f"-SIGRTMIN+{WAYBAR_SIGNAL_NUM}", "waybar"],
            capture_output=True,
            timeout=1,
        )
    except Exception:
        pass


def main() -> int:
    if _is_active():
        subprocess.run(["systemctl", "--user", "stop", SERVICE_NAME])
    else:
        subprocess.run(["systemctl", "--user", "start", SERVICE_NAME])
    _nudge_waybar()
    return 0


if __name__ == "__main__":
    sys.exit(main())
