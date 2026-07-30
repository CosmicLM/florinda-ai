"""mic_recorder.py — starts/stops a microphone recording to a temp WAV file.

WHY arecord over parecord: parecord (PulseAudio/PipeWire client) was tested
directly on this machine and connects to the real hardware source cleanly,
but consistently writes an empty (44-byte, header-only) WAV file regardless
of signal used to stop it or which directory it writes to — a real,
reproducible bug in this environment, not a hypothetical. arecord (ALSA,
routed through PipeWire's ALSA compat layer) was tested the same way and
correctly captured real audio data, so it's used here instead.
"""
import os
import signal
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Optional


class MicRecorder:
    """Records mic audio to a WAV file between start() and stop()."""

    def __init__(self, device: Optional[str] = None) -> None:
        self._device = device or "default"
        self._process: Optional[subprocess.Popen] = None
        self._wav_path: Optional[Path] = None
        self._started_at: Optional[float] = None

    def start(self) -> None:
        """Begin recording; a no-op if already recording (guards a double-PRESS)."""
        if self._process is not None:
            return
        fd, path = tempfile.mkstemp(suffix=".wav", prefix="flora-ptt-")
        os.close(fd)
        self._wav_path = Path(path)
        self._process = subprocess.Popen(
            ["arecord", "-D", self._device, "-f", "S16_LE", "-r", "16000", "-c", "1", str(self._wav_path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self._started_at = time.monotonic()

    def stop(self) -> tuple[Optional[Path], float]:
        """Stop recording; returns (wav_path, held_seconds). wav_path is None if nothing was recorded."""
        if self._process is None:
            return None, 0.0
        held_seconds = time.monotonic() - self._started_at
        self._terminate_recorder()
        wav_path = self._wav_path
        self._process = None
        self._wav_path = None
        self._started_at = None
        if wav_path is None or not wav_path.exists() or wav_path.stat().st_size <= 44:
            self._discard(wav_path)
            return None, held_seconds
        return wav_path, held_seconds

    def _terminate_recorder(self) -> None:
        self._process.send_signal(signal.SIGINT)
        try:
            self._process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait(timeout=2)

    @staticmethod
    def _discard(wav_path: Optional[Path]) -> None:
        if wav_path is not None and wav_path.exists():
            wav_path.unlink()


if __name__ == "__main__":
    recorder = MicRecorder()
    print("Recording 2 seconds — say something...")
    recorder.start()
    time.sleep(2)
    wav_path, held = recorder.stop()
    if wav_path is None:
        print(f"No audio captured (held {held:.2f}s) — mic may be muted or unavailable.")
    else:
        size = wav_path.stat().st_size
        print(f"Captured {size} bytes over {held:.2f}s at {wav_path}")
        wav_path.unlink()
