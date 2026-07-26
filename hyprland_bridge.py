"""hyprland_bridge.py — typed read access to the running Hyprland compositor via hyprctl.

WHY read-only for now: this Hyprland build (0.56.0, confirmed running on this
machine) replaced the classic string dispatch grammar (`hyprctl dispatch
workspace 2`) with an undocumented Lua-based one (`hl.dispatch(hl.dsp...)`).
Live probing showed inconsistent, hard-to-predict results for write/mutation
calls (workspace switches happening under argument shapes that also produced
"error" output, and vice versa) — not something to ship confidently without
real docs. The AI can still issue mutating hyprctl commands directly through
SystemTerminal today; this bridge only covers the stable, JSON-parseable query
surface, so callers get typed data instead of scraping subprocess text.
"""
import json
import subprocess
from dataclasses import dataclass


class HyprctlError(Exception):
    """Raised when a hyprctl query fails or returns unparseable output."""


@dataclass(frozen=True)
class ActiveWindow:
    address: str
    title: str
    workspace_id: int


class HyprlandBridge:
    """Read-only typed access to Hyprland's live compositor state."""

    def active_window(self) -> ActiveWindow:
        data = self._query_json("activewindow")
        return ActiveWindow(
            address=data.get("address", ""),
            title=data.get("title", ""),
            workspace_id=data.get("workspace", {}).get("id", -1),
        )

    def workspaces(self) -> list[dict]:
        return self._query_json("workspaces")

    def monitors(self) -> list[dict]:
        return self._query_json("monitors")

    def _query_json(self, command: str):
        completed = subprocess.run(
            ["hyprctl", "-j", command], capture_output=True, text=True
        )
        if completed.returncode != 0:
            raise HyprctlError(f"hyprctl {command} failed: {completed.stderr.strip()}")
        try:
            return json.loads(completed.stdout)
        except json.JSONDecodeError as error:
            raise HyprctlError(f"hyprctl {command} returned unparseable JSON") from error


if __name__ == "__main__":
    bridge = HyprlandBridge()
    window = bridge.active_window()
    print(f"Active window: {window.title!r} on workspace {window.workspace_id}")
    print(f"Workspace count: {len(bridge.workspaces())}")
    print(f"Monitor count: {len(bridge.monitors())}")
