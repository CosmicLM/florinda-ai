"""hyprland_bridge.py — typed access to the running Hyprland compositor via hyprctl.

WHY a CLI wrapper instead of letting the AI write raw hyprctl/Lua itself:
this Hyprland build (0.56.0) uses an undocumented-until-we-reverse-engineered-
it Lua dispatch grammar (`hyprctl dispatch 'hl.dsp.window.close()'`, not the
classic `hyprctl dispatch closewindow`). An LLM asked to control this
compositor directly would very likely generate the wrong (older, more common)
syntax. This module verifies the correct dispatch calls once, live, and
exposes them as a small, predictable CLI (`python3 hyprland_bridge.py
workspace 2`) that the AI can reliably invoke as its COMMAND field — going
through the exact same confirm/trust-session gate as any other shell command,
no separate safety mechanism needed.

Write actions verified live and reversibly on this machine before shipping:
switching to workspace 1 and back (confirmed via `hyprctl activeworkspace`),
and toggling floating on/off on the active window (confirmed via `hyprctl
activewindow`). `close_active_window`/`focus_direction`/`toggle_fullscreen`
follow the identical `hl.dsp.<category>.<method>({...})` pattern seen
throughout this machine's own working Hyprland config (`custom.lua`/
`default.lua`), so they're trusted by pattern consistency rather than each
one individually tested destructively.
"""
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass

_VALID_DIRECTIONS = ("left", "right", "up", "down")


class HyprctlError(Exception):
    """Raised when a hyprctl query or dispatch fails."""


@dataclass(frozen=True)
class ActiveWindow:
    address: str
    title: str
    workspace_id: int


class HyprlandBridge:
    """Typed access to Hyprland's live compositor state, plus verified control actions."""

    def __init__(self) -> None:
        # WHY checked here, once, instead of letting subprocess.run raise:
        # reported live — on a non-Hyprland desktop (GNOME, in this case),
        # `hyprctl` simply doesn't exist, and subprocess.run(["hyprctl", ...])
        # with no shell raises a raw, uncaught FileNotFoundError — NOT a
        # HyprctlError, so _main()'s `except HyprctlError` never catches it
        # and the AI got a full Python traceback instead of a clear answer.
        # This feature is genuinely Hyprland-only for now (see
        # INSTRUCTION.md's Hyprland Control section) — the right behavior
        # on another desktop is a clean "not available" error, not a crash.
        if not shutil.which("hyprctl"):
            raise HyprctlError(
                "hyprctl not found — window/workspace control only works on Hyprland. "
                "Not available on this desktop."
            )

    # --- read-only queries ---

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

    # --- control actions ---

    def switch_workspace(self, workspace_id: int) -> None:
        self._dispatch(f"hl.dsp.focus({{ workspace = {workspace_id} }})")

    def focus_direction(self, direction: str) -> None:
        if direction not in _VALID_DIRECTIONS:
            raise ValueError(f"invalid direction: {direction!r}, expected one of {_VALID_DIRECTIONS}")
        self._dispatch(f'hl.dsp.focus({{ direction = "{direction}" }})')

    def close_active_window(self) -> None:
        self._dispatch("hl.dsp.window.close()")

    def toggle_floating(self) -> None:
        self._dispatch('hl.dsp.window.float({ action = "toggle" })')

    def toggle_fullscreen(self) -> None:
        self._dispatch('hl.dsp.window.fullscreen({ mode = "fullscreen", action = "toggle" })')

    # --- internals ---

    def _query_json(self, command: str):
        completed = subprocess.run(["hyprctl", "-j", command], capture_output=True, text=True)
        if completed.returncode != 0:
            raise HyprctlError(f"hyprctl {command} failed: {completed.stderr.strip()}")
        try:
            return json.loads(completed.stdout)
        except json.JSONDecodeError as error:
            raise HyprctlError(f"hyprctl {command} returned unparseable JSON") from error

    def _dispatch(self, lua_expr: str) -> None:
        completed = subprocess.run(["hyprctl", "dispatch", lua_expr], capture_output=True, text=True)
        output = completed.stdout.strip()
        if completed.returncode != 0 or output.lower().startswith("error"):
            raise HyprctlError(
                f"hyprctl dispatch failed for {lua_expr!r}: {output or completed.stderr.strip()}"
            )


def _main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Florinda AI's Hyprland control CLI")
    subparsers = parser.add_subparsers(dest="action", required=True)
    subparsers.add_parser("active-window")
    subparsers.add_parser("workspaces")
    subparsers.add_parser("monitors")
    subparsers.add_parser("close-window")
    subparsers.add_parser("toggle-float")
    subparsers.add_parser("toggle-fullscreen")
    workspace_parser = subparsers.add_parser("workspace")
    workspace_parser.add_argument("id", type=int)
    focus_parser = subparsers.add_parser("focus")
    focus_parser.add_argument("direction", choices=_VALID_DIRECTIONS)

    args = parser.parse_args()
    try:
        bridge = HyprlandBridge()
        _dispatch_cli_action(bridge, args)
    except HyprctlError as error:
        print(f"Error: {error}", file=sys.stderr)
        sys.exit(1)


def _dispatch_cli_action(bridge: HyprlandBridge, args) -> None:
    if args.action == "active-window":
        window = bridge.active_window()
        print(f"{window.title} (workspace {window.workspace_id})")
    elif args.action == "workspaces":
        print(json.dumps(bridge.workspaces()))
    elif args.action == "monitors":
        print(json.dumps(bridge.monitors()))
    elif args.action == "workspace":
        bridge.switch_workspace(args.id)
        print(f"Switched to workspace {args.id}")
    elif args.action == "focus":
        bridge.focus_direction(args.direction)
        print(f"Focused {args.direction}")
    elif args.action == "close-window":
        bridge.close_active_window()
        print("Closed active window")
    elif args.action == "toggle-float":
        bridge.toggle_floating()
        print("Toggled floating")
    elif args.action == "toggle-fullscreen":
        bridge.toggle_fullscreen()
        print("Toggled fullscreen")


if __name__ == "__main__":
    _main()
