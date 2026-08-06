"""wake_word.py — hands-free "hey mycroft" activation: continuously listens
for a wake phrase and, on detection, feeds the SAME PRESS/RELEASE protocol
PushToTalkServer already produces into the same ptt_queue — so
flora_service.py's existing _voice_worker_loop/_handle_release/MicRecorder/
TranscriptionEngine pipeline handles the rest completely unchanged. See
SETUP.md for how to train a real custom "Hey Florinda" model; this ships
with openWakeWord's pretrained hey_mycroft model as a working default.

WHY this doesn't run its own recording/transcription: PushToTalkServer
already does exactly that on PRESS/RELEASE — duplicating it here would mean
two independent capture/transcribe pipelines to keep in sync. Pushing the
same two strings onto the same queue.Queue lets everything downstream of
PRESS/RELEASE stay exactly as it is, including barge-in (a wake-word PRESS
interrupts an in-progress reply the same way a real button press does).

WHY inference_framework="onnx" is passed explicitly: verified live —
openwakeword's own PyPI metadata unconditionally requires tflite-runtime on
Linux, and no tflite-runtime wheel exists for this project's Python version
at all (see requirements.txt's own WHY note — install.py installs
openwakeword with --no-deps for exactly this reason). Its ONNX backend
never imports tflite_runtime, confirmed live; forcing it here (rather than
relying on openWakeWord's own auto-detection, which could try tflite
first) is what keeps that import path from ever being touched.

WHY a single 20ms capture cadence serves both wake-word scoring AND
end-of-utterance silence detection: openWakeWord accepts audio of any
length (multiples of 1280 samples/80ms just reduce CPU overhead and add up
to one extra 80ms of latency otherwise — verified via its own predict()
docstring) and webrtcvad requires EXACTLY 10/20/30ms frames at 16kHz — 320
samples (20ms) satisfies both, so one continuous capture loop feeds
whichever detector is active at the time instead of needing two capture
cadences.

WHY the mic is read continuously via sounddevice rather than MicRecorder's
arecord-to-file approach: MicRecorder is deliberately one-shot
(start()/stop() bound to an actual utterance) with no Python-level
streaming callback — this needs continuous frame-by-frame access for
wake-word scoring, which is exactly what sounddevice's blocking
RawInputStream.read() gives. Both can read the same source concurrently:
PipeWire's ALSA-compat layer (which MicRecorder's arecord already routes
through, see mic_recorder.py) supports multiple simultaneous capture
clients — this still needs an actual live mic test once deployed (see the
implementation plan's Verification section), not just an assumption.

WHY the model is loaded lazily, not in __init__, and the stream is closed
the instant listening is disallowed: this is the privacy story for
wake-word, matching watch_toggle.py's screen-watch pause — "paused" means
the microphone is actually not being read, not just that predictions are
being ignored.
"""
import logging
import queue
import threading
import time
from typing import Callable, Optional, Protocol

import numpy as np
import sounddevice as sd

# WHY `import _webrtcvad` (the compiled C extension the `webrtcvad` package
# builds) instead of `import webrtcvad` (its pure-Python wrapper): verified
# live — that wrapper's module-level `pkg_resources.get_distribution(...)`
# call (only ever used to set __version__) raises ModuleNotFoundError on
# this environment's setuptools (83.0.0), which no longer ships
# pkg_resources at all. The real VAD logic lives entirely in `_webrtcvad`
# (a native extension `webrtcvad`'s own install already builds/installs);
# _Vad below replicates the wrapper's is_speech() validation exactly
# (compared directly against its source) without going through the broken
# import.
import _webrtcvad
from openwakeword.model import Model

logger = logging.getLogger(__name__)


class _Vad:
    """Minimal replacement for webrtcvad.Vad — see the import WHY note above."""

    def __init__(self, mode: int) -> None:
        self._vad = _webrtcvad.create()
        _webrtcvad.init(self._vad)
        _webrtcvad.set_mode(self._vad, mode)

    def is_speech(self, buf: bytes, sample_rate: int) -> bool:
        length = len(buf) // 2
        return bool(_webrtcvad.process(self._vad, sample_rate, buf, length))


SAMPLE_RATE = 16000
_FRAME_MS = 20
FRAME_SAMPLES = SAMPLE_RATE * _FRAME_MS // 1000  # 320
# WHY 2 (of 0-3, least to most aggressive at filtering out non-speech):
# moderate default — 3 is tuned for noisy environments and can clip the
# trailing syllable of quiet speech, 0 is too permissive to reliably detect
# silence at all. No real-room tuning data exists yet; see this module's
# WHY note on live verification.
_VAD_MODE = 2
_IDLE_POLL_S = 1.0  # how often to re-check enabled/paused while not actively listening


class _AudioStream(Protocol):
    """Just enough of sounddevice.RawInputStream's interface for _read_frame
    to depend on — lets the self-check below exercise real detection logic
    against a fake stream instead of needing real mic hardware."""

    def read(self, frames: int) -> tuple[bytes, bool]: ...


class WakeWordListener:
    """Continuously listens for a wake phrase and pushes PRESS/RELEASE onto
    `ptt_queue` — the same queue PushToTalkServer feeds. See module docstring."""

    def __init__(
        self,
        ptt_queue: "queue.Queue[str]",
        model_name: str,
        threshold: float,
        silence_timeout_s: float,
        max_utterance_s: float,
        should_listen: Callable[[], bool],
        mic_device: Optional[str] = None,
    ) -> None:
        self._queue = ptt_queue
        self._model_name = model_name
        self._threshold = threshold
        self._silence_timeout_s = silence_timeout_s
        self._max_utterance_s = max_utterance_s
        self._should_listen = should_listen
        self._mic_device = mic_device
        self._stop_event = threading.Event()
        self._model: Optional[Model] = None
        self._vad = _Vad(_VAD_MODE)

    def run(self) -> None:
        """Accept loop — call this as a thread target. Exits cleanly on stop()."""
        while not self._stop_event.is_set():
            if self._should_listen():
                try:
                    self._listen_until_paused_or_stopped()
                except Exception:
                    logger.exception("wake-word listening loop failed, retrying after backoff")
                    self._stop_event.wait(_IDLE_POLL_S)
            else:
                self._stop_event.wait(_IDLE_POLL_S)

    def stop(self) -> None:
        self._stop_event.set()

    def _ensure_model_loaded(self) -> Model:
        if self._model is None:
            logger.info("loading wake-word model %r (onnx backend)", self._model_name)
            self._model = Model(wakeword_models=[self._model_name], inference_framework="onnx")
        return self._model

    def _listen_until_paused_or_stopped(self) -> None:
        model = self._ensure_model_loaded()
        with sd.RawInputStream(
            samplerate=SAMPLE_RATE, channels=1, dtype="int16",
            blocksize=FRAME_SAMPLES, device=self._mic_device,
        ) as stream:
            while not self._stop_event.is_set() and self._should_listen():
                frame = self._read_frame(stream)
                if frame is None:
                    continue
                scores = model.predict(frame)
                if scores.get(self._model_name, 0.0) >= self._threshold:
                    self._handle_trigger(stream)

    @staticmethod
    def _read_frame(stream: _AudioStream) -> Optional[np.ndarray]:
        data, overflowed = stream.read(FRAME_SAMPLES)
        if overflowed:
            logger.warning("wake-word audio input overflowed a frame, dropping it")
        return np.frombuffer(data, dtype=np.int16)

    def _handle_trigger(self, stream: _AudioStream) -> None:
        """Pushes PRESS immediately, then keeps reading frames from the same
        stream — classifying each with webrtcvad instead of openWakeWord —
        until it's seen real speech followed by `silence_timeout_s` of
        trailing silence, or `max_utterance_s` elapses (a hard safety cap:
        unlike a real button, nothing here can force a RELEASE if silence
        detection ever fails to fire)."""
        logger.info("wake word %r detected, starting utterance capture", self._model_name)
        self._queue.put("PRESS")
        started_at = time.monotonic()
        silence_started_at: Optional[float] = None
        heard_speech = False
        while not self._stop_event.is_set():
            if time.monotonic() - started_at > self._max_utterance_s:
                logger.warning("wake-word utterance exceeded max duration, force-releasing")
                break
            frame = self._read_frame(stream)
            if frame is None:
                continue
            is_speech = self._vad.is_speech(frame.tobytes(), SAMPLE_RATE)
            if is_speech:
                heard_speech = True
                silence_started_at = None
                continue
            if not heard_speech:
                continue  # still waiting for the user to actually start talking
            if silence_started_at is None:
                silence_started_at = time.monotonic()
            elif time.monotonic() - silence_started_at >= self._silence_timeout_s:
                break
        self._queue.put("RELEASE")


if __name__ == "__main__":
    class _FakeStream:
        """Deterministic silence — enough to exercise predict()/is_speech()
        against the real ONNX/webrtcvad pipeline without real mic hardware."""

        def read(self, frames: int) -> tuple[bytes, bool]:
            return np.zeros(frames, dtype=np.int16).tobytes(), False

    q: "queue.Queue[str]" = queue.Queue()
    listener = WakeWordListener(
        ptt_queue=q, model_name="hey_mycroft", threshold=0.5,
        silence_timeout_s=0.2, max_utterance_s=5.0, should_listen=lambda: False,
    )

    # Idle path: should_listen() False means run() must never touch audio at all.
    thread = threading.Thread(target=listener.run, daemon=True)
    thread.start()
    time.sleep(0.3)
    assert q.empty(), "listener pushed something while should_listen() was False"
    listener.stop()
    thread.join(timeout=2)
    print("OK: idle loop never opens a stream while disallowed")

    # Real detection path against synthetic silence (no mic hardware needed):
    # predict() must run without touching tflite_runtime, and silence must
    # never look like speech to the VAD.
    model = listener._ensure_model_loaded()
    silent_frame = np.zeros(FRAME_SAMPLES, dtype=np.int16)
    scores = model.predict(silent_frame)
    assert listener._model_name in scores, scores
    assert not listener._vad.is_speech(silent_frame.tobytes(), SAMPLE_RATE)
    print("OK: real ONNX predict() + webrtcvad both run cleanly on synthetic silence")

    # _handle_trigger's silence-timeout path, fed entirely from _FakeStream
    # (all-silence) — heard_speech never flips True, so it must NOT release
    # on silence alone; force it by monkeypatching is_speech to simulate
    # "spoke, then went quiet."
    calls = {"n": 0}
    real_is_speech = listener._vad.is_speech
    def fake_is_speech(frame_bytes, rate):
        calls["n"] += 1
        return calls["n"] <= 2  # "speech" for the first 2 frames, silence after
    listener._vad.is_speech = fake_is_speech
    listener._stop_event.clear()
    listener._handle_trigger(_FakeStream())
    assert list(q.queue) == ["PRESS", "RELEASE"], list(q.queue)
    listener._vad.is_speech = real_is_speech
    print("OK: PRESS then RELEASE after speech-then-silence")

    print("wake_word.py self-check OK")
