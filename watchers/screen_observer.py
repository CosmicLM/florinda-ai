"""screen_observer.py — captures the screen and OCRs it only when content actually changed.

WHY delta-filtering: the spec's cost strategy is to avoid wasting tokens/CPU on
OCR-ing screens that haven't meaningfully changed. A frame is captured,
OpenCV diffs it against the previous capture (mean absolute pixel difference),
and Tesseract only runs when the diff crosses a threshold.

WHY grim first, portal as fallback: `grim` works with zero configuration on
any wlroots compositor (Hyprland, Sway, river) and is what this machine
actually uses. It doesn't exist on GNOME/KDE/X11 sessions, though — for
those, screencast_portal.py's WM-agnostic ScreenCast-portal capture is used
instead, picked automatically the first time `grim` isn't found (see
_capture_frame). This keeps the fast, zero-config path as the default
everywhere it works, without hardcoding a Hyprland/wlroots dependency into
what "capturing the screen" means.

Region selection (`slurp`) is intentionally not wired in here: `slurp` blocks
on interactive mouse input, which has no meaning for an unattended/background
observer loop. Callers that want a specific region should pass `geometry`
(the same "X,Y WxH" string `slurp` would normally produce) captured once,
up front, interactively. Geometry selection has no equivalent in the portal
fallback (ScreenCast returns whatever monitor the user/compositor picked),
so `geometry` is simply unused on that path.
"""
import logging
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
import pytesseract

logger = logging.getLogger(__name__)


class CaptureError(Exception):
    """Raised when neither `grim` nor the ScreenCast portal fallback can produce a screenshot."""


@dataclass(frozen=True)
class ObservationResult:
    changed: bool
    text: Optional[str]  # OCR text, only populated when changed is True


class ScreenObserver:
    """Captures the screen and OCRs it only on meaningful frame-to-frame change."""

    def __init__(self, change_threshold: float = 8.0, geometry: Optional[str] = None) -> None:
        self._change_threshold = change_threshold
        self._geometry = geometry
        self._previous_frame: Optional[np.ndarray] = None
        self._use_portal = shutil.which("grim") is None
        self._portal_capture = None
        if self._use_portal:
            logger.info("grim not found — falling back to the ScreenCast portal for screen capture")

    def observe(self) -> ObservationResult:
        frame = self._capture_frame()
        changed = self._has_changed(frame)
        self._previous_frame = frame
        if not changed:
            return ObservationResult(changed=False, text=None)
        return ObservationResult(changed=True, text=pytesseract.image_to_string(frame))

    def _capture_frame(self) -> np.ndarray:
        with tempfile.NamedTemporaryFile(suffix=".png") as tmp_file:
            output_path = Path(tmp_file.name)
            if self._use_portal:
                self._run_portal_capture(output_path)
            else:
                self._run_grim(output_path)
            frame = cv2.imread(str(output_path))
        if frame is None:
            raise CaptureError("screen capture produced an unreadable image")
        return frame

    def _run_portal_capture(self, output_path: Path) -> None:
        # WHY imported here, not at module scope: dbus/gi are only needed on
        # the fallback path (GNOME/KDE/X11) — machines with grim shouldn't
        # need those extra dependencies importable just to run this module.
        from tools import screencast_portal

        if self._portal_capture is None:
            self._portal_capture = screencast_portal.PortalScreenCapture()
        try:
            self._portal_capture.capture_frame(output_path)
        except screencast_portal.PortalCaptureError as error:
            raise CaptureError(f"ScreenCast portal capture failed: {error}") from error

    def _run_grim(self, output_path: Path) -> None:
        command = ["grim"]
        if self._geometry:
            command += ["-g", self._geometry]
        command.append(str(output_path))
        completed = subprocess.run(command, capture_output=True, text=True)
        if completed.returncode != 0:
            raise CaptureError(f"grim failed: {completed.stderr.strip()}")

    def _has_changed(self, frame: np.ndarray) -> bool:
        if self._previous_frame is None or self._previous_frame.shape != frame.shape:
            return True
        diff = cv2.absdiff(self._previous_frame, frame)
        return float(diff.mean()) >= self._change_threshold


if __name__ == "__main__":
    observer = ScreenObserver()
    first = observer.observe()
    print(f"First capture — changed: {first.changed} (expected True, no prior frame)")
    second = observer.observe()
    print(f"Second capture — changed: {second.changed} (expected False, static screen)")
    if first.text:
        print(f"OCR sample (first 120 chars): {first.text[:120]!r}")
