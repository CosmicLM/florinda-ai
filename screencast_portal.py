"""screencast_portal.py — WM-agnostic screen capture via the
org.freedesktop.portal.ScreenCast + PipeWire, for desktops where `grim`
doesn't exist (GNOME, KDE, X11 session managers — anything that isn't a
wlroots compositor).

WHY the ScreenCast portal and not the simpler-looking Screenshot portal:
verified live — `org.freedesktop.portal.Screenshot` is built for a single
interactive, user-confirmed capture. On this very machine (three portal
backends installed: gnome, gtk, hyprland) a non-interactive Screenshot call
got silently routed to xdg-desktop-portal-gtk and hung forever ("Unhandled
parent window type" in its logs) — exactly the kind of per-desktop portal
misconfiguration this module exists to route around, not repeat.
ScreenCast is the API actually meant for a program that pulls frames
repeatedly (screen recorders/sharing use it) — CreateSession/SelectSources/
Start all completed instantly and without any dialog when tested live on
this Hyprland setup, and Start's `persist_mode` + restore_token lets a
GNOME/KDE user approve the one-time picker dialog once, not on every frame.

WHY the session is created once and reused, not per-frame: repeating
CreateSession/SelectSources/Start for every observe() call would be slow
and, on backends that actually show a picker dialog (unlike this machine),
would reprompt the user constantly. The PipeWire remote fd + node id stay
valid for the life of the session, so each frame grab afterward is just a
fresh, fast one-shot `gst-launch-1.0` subprocess against the already-open
remote — no new D-Bus negotiation needed.

WHY a subprocess (gst-launch-1.0) instead of GStreamer's Python bindings for
the actual frame grab: verified live this exact pipeline
(`pipewiresrc fd=<fd> path=<node> num-buffers=1 ! videoconvert ! pngenc !
filesink`) produces a real, correctly-sized, non-degenerate PNG in well
under a second. A subprocess call matches this project's existing pattern
for every other capture/render tool (grim, pdflatex, piper) — no need to
manage a GStreamer pipeline's lifecycle/threading from Python for a single
buffer.
"""
import json
import logging
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

import dbus
import dbus.mainloop.glib
from gi.repository import GLib

logger = logging.getLogger(__name__)

_RESTORE_TOKEN_PATH = Path.home() / ".local/share/flora-ai/screencast_restore_token.json"
_BUS_NAME = "org.freedesktop.portal.Desktop"
_OBJECT_PATH = "/org/freedesktop/portal/desktop"
_REQUEST_IFACE = "org.freedesktop.portal.Request"
_CREATE_SESSION_TIMEOUT_S = 10
_SELECT_SOURCES_TIMEOUT_S = 10
# WHY much longer than the others: on a desktop whose portal backend
# actually implements interactive source selection (unlike this machine),
# the user needs real time to notice and act on the picker dialog.
_START_TIMEOUT_S = 60
_FRAME_TIMEOUT_S = 10


class PortalCaptureError(Exception):
    """Raised when the ScreenCast portal session or a frame grab fails."""


class PortalScreenCapture:
    """Lazily establishes one ScreenCast portal session and reuses it for
    repeated single-frame captures — see module docstring for why."""

    def __init__(self) -> None:
        self._bus: Optional[dbus.SessionBus] = None
        self._loop: Optional[GLib.MainLoop] = None
        self._session_handle: Optional[str] = None
        self._node_id: Optional[int] = None
        self._remote_fd: Optional[int] = None
        self._token_counter = 0

    def capture_frame(self, output_path: Path) -> None:
        if self._remote_fd is None:
            self._establish_session()
        self._grab_one_frame(output_path)

    def close(self) -> None:
        if self._remote_fd is not None:
            try:
                os.close(self._remote_fd)
            except OSError:
                pass
            self._remote_fd = None
        self._session_handle = None
        self._node_id = None

    # --- session setup (once per process lifetime) ---

    def _establish_session(self) -> None:
        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        self._bus = dbus.SessionBus()
        self._loop = GLib.MainLoop()
        portal = self._bus.get_object(_BUS_NAME, _OBJECT_PATH)
        screencast = dbus.Interface(portal, "org.freedesktop.portal.ScreenCast")

        create = self._call_and_wait(
            screencast.CreateSession,
            [],
            {"session_handle_token": self._new_token()},
            timeout_s=_CREATE_SESSION_TIMEOUT_S,
        )
        if create["code"] != 0 or "session_handle" not in create["results"]:
            raise PortalCaptureError(f"CreateSession failed: {create}")
        self._session_handle = create["results"]["session_handle"]

        select_args = {
            "handle_token": self._new_token(),
            "types": dbus.UInt32(1),  # MONITOR
            "cursor_mode": dbus.UInt32(2),  # embedded in the frame
            "persist_mode": dbus.UInt32(2),  # persist until explicitly revoked
        }
        # The ("susv") struct shape and the whole round-trip (a token saved
        # from a prior Start(), fed back into this SelectSources call, then
        # accepted without a repeat prompt) is verified live on this
        # machine. Still kept defensive/best-effort below: other portal
        # backends (GNOME/KDE) may reject a token from a different backend
        # or a different struct convention, and that should fail open to a
        # fresh consent prompt, not crash the whole session.
        restore_token = _read_restore_token()
        if restore_token:
            try:
                select_args["restore_data"] = dbus.Struct(
                    (dbus.String("u1"), dbus.UInt32(1), dbus.String(restore_token)), signature="susv"
                )
            except Exception:
                logger.warning("could not build restore_data from saved token, falling back to fresh consent", exc_info=True)
        try:
            select = self._call_and_wait(
                screencast.SelectSources,
                [self._session_handle],
                select_args,
                timeout_s=_SELECT_SOURCES_TIMEOUT_S,
            )
        except dbus.exceptions.DBusException:
            if "restore_data" not in select_args:
                raise
            logger.warning("SelectSources rejected the saved restore_data, retrying with a fresh prompt", exc_info=True)
            del select_args["restore_data"]
            select = self._call_and_wait(
                screencast.SelectSources,
                [self._session_handle],
                select_args,
                timeout_s=_SELECT_SOURCES_TIMEOUT_S,
            )
        if select["code"] != 0:
            raise PortalCaptureError(f"SelectSources failed (code {select['code']})")

        start = self._call_and_wait(
            screencast.Start,
            [self._session_handle, ""],
            {"handle_token": self._new_token()},
            timeout_s=_START_TIMEOUT_S,
        )
        if start["code"] != 0 or "streams" not in start["results"]:
            raise PortalCaptureError(
                f"Start failed (code {start['code']}) — the user may have declined the picker dialog"
            )
        streams = start["results"]["streams"]
        self._node_id = int(streams[0][0])
        new_restore_token = start["results"].get("restore_token")
        if new_restore_token:
            _write_restore_token(str(new_restore_token))

        fd_obj = screencast.OpenPipeWireRemote(self._session_handle, {})
        raw_fd = fd_obj.take()
        os.set_inheritable(raw_fd, True)
        self._remote_fd = raw_fd
        logger.info("ScreenCast portal session established (node %s)", self._node_id)

    def _new_token(self) -> str:
        self._token_counter += 1
        return f"florascreen{self._token_counter}"

    def _call_and_wait(self, method, posargs: list, kwargs: dict, timeout_s: int) -> dict:
        request_path = method(*posargs, kwargs)
        result = {"code": None, "results": {}}

        def handler(response, results):
            result["code"] = int(response)
            result["results"] = dict(results)
            self._loop.quit()

        receiver = self._bus.add_signal_receiver(
            handler, signal_name="Response", dbus_interface=_REQUEST_IFACE, path=request_path
        )
        timer_id = GLib.timeout_add_seconds(timeout_s, self._loop.quit)
        self._loop.run()
        GLib.source_remove(timer_id)
        receiver.remove()
        if result["code"] is None:
            raise PortalCaptureError(f"portal request at {request_path} timed out after {timeout_s}s")
        return result

    # --- per-frame capture ---

    def _grab_one_frame(self, output_path: Path) -> None:
        cmd = [
            "gst-launch-1.0", "-q",
            "pipewiresrc", f"fd={self._remote_fd}", f"path={self._node_id}", "num-buffers=1",
            "!", "videoconvert", "!", "pngenc", "!", "filesink", f"location={output_path}",
        ]
        try:
            completed = subprocess.run(
                cmd, pass_fds=(self._remote_fd,), capture_output=True, text=True, timeout=_FRAME_TIMEOUT_S
            )
        except subprocess.TimeoutExpired as error:
            raise PortalCaptureError(f"frame grab timed out: {error}") from error
        if completed.returncode != 0:
            raise PortalCaptureError(f"gst-launch-1.0 frame grab failed: {completed.stderr.strip()}")


def _read_restore_token() -> Optional[str]:
    try:
        return json.loads(_RESTORE_TOKEN_PATH.read_text()).get("restore_token")
    except (OSError, json.JSONDecodeError):
        return None


def _write_restore_token(token: str) -> None:
    _RESTORE_TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    _RESTORE_TOKEN_PATH.write_text(json.dumps({"restore_token": token}))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    capture = PortalScreenCapture()
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp_file:
        out_path = Path(tmp_file.name)
    capture.capture_frame(out_path)
    size = out_path.stat().st_size
    print(f"First capture OK — {out_path} ({size} bytes)")
    assert size > 1000, "suspiciously small output for a real screen capture"
    capture.capture_frame(out_path)
    print(f"Second capture (session reused, no new D-Bus negotiation) OK — {out_path.stat().st_size} bytes")
    capture.close()
    out_path.unlink()
    print("PortalScreenCapture self-check OK")
