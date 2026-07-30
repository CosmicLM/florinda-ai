"""voice_input.py — local speech-to-text via faster-whisper.

WHY faster-whisper: ships a cp314 wheel (verified installable on this exact
Python before choosing it), runs fully local/offline (no audio leaves the
machine for transcription — only the resulting text goes to Gemini), and
push-to-talk clips are short enough that CPU int8 inference finishes well
under a second, no GPU wrangling needed for this workload.
"""
import logging
from pathlib import Path
from typing import Union

from faster_whisper import WhisperModel

logger = logging.getLogger(__name__)


class TranscriptionEngine:
    """Wraps a faster-whisper model, loaded once and reused for every push-to-talk turn."""

    def __init__(self, model_name: str = "small.en", device: str = "cpu") -> None:
        compute_type = "int8" if device == "cpu" else "float16"
        self._model = WhisperModel(model_name, device=device, compute_type=compute_type)

    def transcribe(self, wav_path: Union[str, Path]) -> str:
        """Transcribe `wav_path`; returns "" on any failure rather than raising."""
        try:
            segments, _ = self._model.transcribe(str(wav_path), language="en", vad_filter=True)
            return " ".join(segment.text.strip() for segment in segments).strip()
        except Exception:
            logger.exception("STT failed for %s", wav_path)
            return ""


if __name__ == "__main__":
    import sys
    import time

    from mic_recorder import MicRecorder

    print("Loading model (small.en, cpu)...")
    start = time.monotonic()
    engine = TranscriptionEngine()
    print(f"Model loaded in {time.monotonic() - start:.1f}s")

    recorder = MicRecorder()
    print("Recording 4 seconds — say something...")
    recorder.start()
    time.sleep(4)
    wav_path, held = recorder.stop()
    if wav_path is None:
        print(f"No audio captured (held {held:.2f}s).")
        sys.exit(1)

    start = time.monotonic()
    text = engine.transcribe(wav_path)
    print(f"Transcribed in {time.monotonic() - start:.2f}s: {text!r}")
    wav_path.unlink()
