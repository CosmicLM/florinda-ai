"""voice.py — The Voice: streams text to a local Piper TTS + aplay pipeline without blocking."""
import os
import signal
import subprocess
import threading
import time
from typing import Optional

# WHY these exist: stream_vocal_synthesis is deliberately fire-and-forget
# (writes text to piper's stdin and returns immediately, so text generation
# is never blocked waiting on audio playback) — but that means nothing in
# this codebase otherwise knows when speech actually FINISHES playing.
# Observed live: a status widget fix that flipped away from "talking" as
# soon as code moved on to the next step (running a command, going idle)
# did so while audio was still audibly playing, since "handed to piper" and
# "finished playing through the speakers" are not the same moment. There is
# no real completion signal available from the warm piper|aplay pipeline
# (it's one long-lived process, not spawned per utterance), so this
# estimates playback duration from text length instead.
#
# WHY these specific numbers: measured directly against this project's own
# voice model — piped raw text through piper-tts alone (no aplay) and
# computed EXACT audio duration from the raw PCM byte count (bytes / 2 /
# 22050, matching the S16_LE/22050Hz format _spawn_pipeline uses) for
# several real sentence lengths. Longer sentences converged to ~17.5
# chars/sec; short ones showed a real fixed overhead of several hundred ms
# regardless of length. 16 chars/sec (slightly slower than measured) and a
# 0.5s floor bias this estimate toward slightly OVER-waiting rather than
# under — a status transition firing a bit late reads as mildly sluggish;
# firing early means claiming "idle" while Florinda is still audibly talking,
# which is the actual bug this exists to prevent.
_ESTIMATED_CHARS_PER_SECOND = 16.0
_MIN_UTTERANCE_S = 0.5


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
        self._busy_until = 0.0  # monotonic time estimated playback catches up

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
            duration = max(_MIN_UTTERANCE_S, len(text) / _ESTIMATED_CHARS_PER_SECOND)
            now = time.monotonic()
            self._busy_until = max(self._busy_until, now) + duration

    def wait_until_done(self) -> None:
        """Blocks until all speech queued so far is estimated to have
        finished playing. Call this before any status transition away from
        "talking" (going idle, starting a command, moving to the next
        recursive leg) — see the module docstring for why it's needed."""
        remaining = self._busy_until - time.monotonic()
        if remaining > 0:
            time.sleep(remaining)

    def close(self) -> None:
        """Cleanly stop the pipeline — call this on service shutdown."""
        with self._lock:
            if self._pipeline is None:
                return
            try:
                self._pipeline.stdin.close()
                self._pipeline.wait(timeout=3)
            except Exception:
                self._kill_pipeline_group()
            self._pipeline = None

    def interrupt(self) -> None:
        """Stops whatever is currently playing, right now — called the
        instant a new push-to-talk press comes in, so the user can barge in
        on Florinda mid-sentence instead of waiting for her to finish. A plain
        terminate() on the wrapping shell process would NOT reliably stop
        piper/aplay: sh doesn't forward signals to a pipeline's children by
        default, so this kills the whole process group instead (see
        start_new_session=True in _spawn_pipeline). The next
        stream_vocal_synthesis call transparently spawns a fresh pipeline.
        """
        with self._lock:
            self._busy_until = 0.0  # nothing left to wait for once playback is killed
            if self._pipeline is None or self._pipeline.poll() is not None:
                return
            self._kill_pipeline_group()
            self._pipeline = None

    def _kill_pipeline_group(self) -> None:
        try:
            os.killpg(self._pipeline.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

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
            start_new_session=True,  # own process group, so interrupt() can killpg reliably
        )
