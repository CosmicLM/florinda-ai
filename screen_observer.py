"""screen_observer.py — captures the screen and OCRs it only when content actually changed.

WHY delta-filtering: the spec's cost strategy is to avoid wasting tokens/CPU on
OCR-ing screens that haven't meaningfully changed. `grim` captures a frame,
OpenCV diffs it against the previous capture (mean absolute pixel difference),
and Tesseract only runs when the diff crosses a threshold.

Region selection (`slurp`) is intentionally not wired in here: `slurp` blocks
on interactive mouse input, which has no meaning for an unattended/background
observer loop. Callers that want a specific region should pass `geometry`
(the same "X,Y WxH" string `slurp` would normally produce) captured once,
up front, interactively.
"""
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
import pytesseract


class CaptureError(Exception):
    """Raised when `grim` fails to produce a screenshot."""


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

    def observe(self) -> ObservationResult:
        frame = self._capture_frame()
        changed = self._has_changed(frame)
        self._previous_frame = frame
        if not changed:
            return ObservationResult(changed=False, text=None)
        return ObservationResult(changed=True, text=pytesseract.image_to_string(frame))

    def _capture_frame(self) -> np.ndarray:
        with tempfile.NamedTemporaryFile(suffix=".png") as tmp_file:
            self._run_grim(Path(tmp_file.name))
            frame = cv2.imread(tmp_file.name)
        if frame is None:
            raise CaptureError("grim produced an unreadable image")
        return frame

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
