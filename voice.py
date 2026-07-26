"""voice.py — The Voice: streams text to a local Piper TTS + aplay pipeline without blocking."""
import subprocess


class AudioEngine:
    """The Voice: converts text to speech via a local Piper TTS process."""

    def __init__(self, voice_model: str, debug: bool = False) -> None:
        self._voice_model = voice_model
        self._debug = debug

    def stream_vocal_synthesis(self, text: str) -> None:
        """Speak `text` in the background; returns immediately (non-blocking).

        WHY: `text` originates from the AI and must never be interpolated into
        a shell string (that was the old injection vector: an `echo "{text}"`
        f-string). It's written to the pipeline's stdin instead, so shell
        metacharacters in `text` are inert.
        """
        if not text or not text.strip():
            return
        pipeline = self._spawn_pipeline()
        pipeline.stdin.write((text + "\n").encode())
        pipeline.stdin.close()

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
