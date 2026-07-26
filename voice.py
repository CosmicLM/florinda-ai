"""voice.py — The Voice: streams text to a local Piper TTS + aplay pipeline without blocking."""
import subprocess
import threading
from typing import Optional


class AudioEngine:
    """The Voice: converts text to speech via a persistent, warm local Piper TTS process.

    WHY persistent instead of spawning fresh per call: piper-tts pays a real
    model-load cost on every process start. Verified live that piper happily
    processes multiple lines sent over time on the same stdin without it
    being closed between them — this keeps one pipeline alive across the
    whole process lifetime instead of re-paying that cost per utterance.
    """

    def __init__(self, voice_model: str, debug: bool = False) -> None:
        self._voice_model = voice_model
        self._debug = debug
        self._lock = threading.Lock()
        self._pipeline: Optional[subprocess.Popen] = None

    def stream_vocal_synthesis(self, text: str) -> None:
        """Speak `text`; returns as soon as the line is handed to the pipeline.

        WHY: `text` originates from the AI and must never be interpolated into
        a shell string (that was the old injection vector: an `echo "{text}"`
        f-string). It's written to the pipeline's stdin instead, so shell
        metacharacters in `text` are inert.
        """
        if not text or not text.strip():
            return
        with self._lock:
            self._ensure_pipeline_alive()
            self._pipeline.stdin.write((text + "\n").encode())
            self._pipeline.stdin.flush()

    def close(self) -> None:
        """Cleanly stop the pipeline — call this on service shutdown."""
        with self._lock:
            if self._pipeline is None:
                return
            try:
                self._pipeline.stdin.close()
                self._pipeline.wait(timeout=3)
            except Exception:
                self._pipeline.kill()
            self._pipeline = None

    def _ensure_pipeline_alive(self) -> None:
        if self._pipeline is not None and self._pipeline.poll() is None:
            return  # still running, nothing to do
        self._pipeline = self._spawn_pipeline()

    def _spawn_pipeline(self) -> subprocess.Popen:
        piper_cmd = f"piper-tts --model {self._voice_model} --output_raw"
        aplay_cmd = "aplay -r 22050 -f S16_LE -t raw"
        stderr = None if self._debug else subprocess.DEVNULL
        return subprocess.Popen(
            f"{piper_cmd} | {aplay_cmd}",
            shell=True,
            stdin=subprocess.PIPE,
            stderr=stderr,
        )
