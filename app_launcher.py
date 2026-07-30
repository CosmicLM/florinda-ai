"""app_launcher.py — launches installed desktop applications by name.

WHY a wrapper instead of letting the AI guess launch commands itself: app
binaries/launch commands vary (Electron apps needing specific flags, Flatpak/
Snap wrapping, apps needing particular env vars) and each installed app's
.desktop file already encodes the correct way to start it. Resolving by
desktop entry (via `gtk-launch`) is more robust than guessing a binary name —
same reasoning as hyprland_bridge.py's CLI wrapper for Hyprland control:
verified once here, exposed as a small predictable CLI the AI can reliably
invoke as its COMMAND field.
"""
import glob
import subprocess
import sys
from pathlib import Path
from typing import Optional

_DESKTOP_DIRS = (
    "/usr/share/applications",
    str(Path.home() / ".local/share/applications"),
)


class AppLauncherError(Exception):
    """Raised when no matching application can be found or launch fails."""


class AppLauncher:
    """Resolves a friendly app name to an installed .desktop entry and launches it."""

    def open_app(self, name: str) -> str:
        """Launch the application best matching `name`. Returns the desktop id used.

        WHY fire-and-forget instead of waiting for gtk-launch to exit: some
        desktop entries make gtk-launch block for as long as the app itself
        runs (observed live — xfce4-file-manager never returned), not just
        until it's started. _resolve_desktop_id already confirmed a real
        installed .desktop entry exists before we get here, so launching and
        moving on is the same "fire and forget" treatment this codebase
        already gives every other launch-a-subprocess side effect (the
        listening cue, TTS playback).
        """
        desktop_id = self._resolve_desktop_id(name)
        subprocess.Popen(
            ["gtk-launch", desktop_id], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        return desktop_id

    def open_resource(self, resource: str) -> None:
        """Opens a URL or file path (a paper, a PDF, a webpage, a document)
        with the user's default handler via xdg-open — same fire-and-forget
        reasoning as open_app: which handler xdg-open resolves to, and
        whether it blocks, isn't something to wait on here."""
        subprocess.Popen(
            ["xdg-open", resource], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )

    def list_apps(self) -> list[tuple[str, str]]:
        """Returns [(desktop_id, display_name), ...] for all installed apps."""
        apps = []
        for directory in _DESKTOP_DIRS:
            for path in glob.glob(f"{directory}/*.desktop"):
                display_name = self._read_desktop_name(path)
                if display_name:
                    apps.append((Path(path).stem, display_name))
        return apps

    def _resolve_desktop_id(self, name: str) -> str:
        candidate = name if name.endswith(".desktop") else f"{name}.desktop"
        for directory in _DESKTOP_DIRS:
            if Path(directory, candidate).exists():
                return Path(candidate).stem

        needle = name.strip().lower()
        best_match: Optional[str] = None
        for directory in _DESKTOP_DIRS:
            for path in glob.glob(f"{directory}/*.desktop"):
                desktop_id = Path(path).stem
                display_name = (self._read_desktop_name(path) or "").lower()
                if needle == desktop_id.lower() or needle == display_name:
                    return desktop_id
                if best_match is None and (needle in desktop_id.lower() or needle in display_name):
                    best_match = desktop_id
        if best_match:
            return best_match
        raise AppLauncherError(f"no installed application matches {name!r}")

    @staticmethod
    def _read_desktop_name(path: str) -> Optional[str]:
        try:
            with open(path, "r", errors="ignore") as desktop_file:
                for line in desktop_file:
                    if line.startswith("Name="):
                        return line.split("=", 1)[1].strip()
        except OSError:
            return None
        return None


def _main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Florinda AI's application launcher CLI")
    subparsers = parser.add_subparsers(dest="action", required=True)
    open_parser = subparsers.add_parser("open")
    open_parser.add_argument("name", nargs="+", help="app name, e.g. code, firefox, 'visual studio code'")
    open_url_parser = subparsers.add_parser("open-url")
    open_url_parser.add_argument("resource", help="a URL or file path, e.g. a paper's link or a local PDF")
    subparsers.add_parser("list")

    args = parser.parse_args()
    launcher = AppLauncher()
    try:
        if args.action == "open":
            desktop_id = launcher.open_app(" ".join(args.name))
            print(f"Launched {desktop_id}")
        elif args.action == "open-url":
            launcher.open_resource(args.resource)
            print(f"Opened {args.resource}")
        elif args.action == "list":
            for desktop_id, display_name in sorted(launcher.list_apps(), key=lambda item: item[1].lower()):
                print(f"{desktop_id}: {display_name}")
    except AppLauncherError as error:
        print(f"Error: {error}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    _main()
