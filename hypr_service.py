"""hypr_service.py — always-on entrypoint: screen watching + push-to-talk voice input.

WHY plain threading, not asyncio: everything else in this codebase
(SystemTerminal, AudioEngine, ScreenObserver) is synchronous/blocking-
subprocess style. Three long-lived loops sharing a queue don't need more
than threads — asyncio would mean wrapping every existing blocking call in
run_in_executor for no real benefit here.
"""
import logging
import os
import queue
import signal
import sys
import threading
import time
from typing import Optional

from colorama import init
from google import genai

from config import ConfigurationError, ConfigVault
from executor import SystemTerminal
from hypr_daemon import HyprDaemon
from mic_recorder import MicRecorder
from processor import PromptProcessor
from ptt_ipc import PushToTalkServer
from screen_observer import ScreenObserver
from state_manifest import StateManifest
from sys_info_cache import SysInfoCache
from voice import AudioEngine
from voice_input import TranscriptionEngine

logger = logging.getLogger(__name__)

CONFIRM_PHRASES = {"yes", "confirm", "do it", "go ahead", "execute"}
DENY_PHRASES = {"no", "cancel", "stop", "never mind", "nevermind"}


class ConfirmGate:
    """Voice-mode replacement for HyprDaemon's blocking input() confirm.

    WHY: a systemd service has no attached TTY, so input() would just hang
    forever. request() never blocks and never auto-executes on the same
    turn — it speaks the pending command and defers to the next voice turn,
    which either confirms, denies, or (if unrelated) drops the pending
    command outright. There is no "confirm all" escape hatch in voice mode.
    """

    def __init__(self, audio: AudioEngine, timeout_s: float) -> None:
        self._audio = audio
        self._timeout_s = timeout_s
        self._pending_command: Optional[str] = None
        self._expires_at: float = 0.0

    def request(self, command: str) -> bool:
        self._audio.stream_vocal_synthesis(
            f"I would like to run: {command}. Hold Super and say confirm to approve, or cancel to drop it."
        )
        self._pending_command = command
        self._expires_at = time.monotonic() + self._timeout_s
        return False

    def has_pending(self) -> bool:
        return self._pending_command is not None and time.monotonic() < self._expires_at

    def take_pending(self) -> Optional[str]:
        command = self._pending_command if self.has_pending() else None
        self._pending_command = None
        self._expires_at = 0.0
        return command


class HyprService:
    """Composition root for the always-on service: screen-watch + push-to-talk threads."""

    def __init__(
        self,
        settings,
        daemon: HyprDaemon,
        confirm_gate: ConfirmGate,
        mic: MicRecorder,
        stt: TranscriptionEngine,
        observer: ScreenObserver,
        sys_info: SysInfoCache,
        ptt_queue: "queue.Queue[str]",
        ptt_server: PushToTalkServer,
        audio: AudioEngine,
        terminal: SystemTerminal,
    ) -> None:
        self._settings = settings
        self._daemon = daemon
        self._confirm_gate = confirm_gate
        self._mic = mic
        self._stt = stt
        self._observer = observer
        self._sys_info = sys_info
        self._ptt_queue = ptt_queue
        self._ptt_server = ptt_server
        self._audio = audio
        self._terminal = terminal
        self._stop_event = threading.Event()

    def run_forever(self) -> None:
        threads = {
            "ptt-ipc": threading.Thread(target=self._ptt_server.run, name="ptt-ipc", daemon=True),
            "screen-watch": threading.Thread(target=self._screen_watch_loop, name="screen-watch", daemon=True),
            "voice-worker": threading.Thread(target=self._voice_worker_loop, name="voice-worker", daemon=True),
        }
        for thread in threads.values():
            thread.start()
        self._install_signal_handlers()
        logger.info("hypr_service started (pid=%d)", os.getpid())
        self._supervise(threads)

    def _install_signal_handlers(self) -> None:
        signal.signal(signal.SIGTERM, lambda *_: self._stop_event.set())
        signal.signal(signal.SIGINT, lambda *_: self._stop_event.set())

    def _supervise(self, threads: dict) -> None:
        while not self._stop_event.is_set():
            for name, thread in threads.items():
                if not thread.is_alive():
                    logger.error("%s thread died unexpectedly, exiting for systemd restart", name)
                    self._ptt_server.stop()
                    sys.exit(1)
            self._stop_event.wait(2)
        logger.info("hypr_service shutting down")
        self._ptt_server.stop()
        # WHY join with a timeout: threads are daemon=True so the process would
        # exit immediately once main() returns, racing PushToTalkServer's
        # finally-block socket cleanup (its accept() loop notices stop_event
        # only on its next ~1s timeout tick) — join gives it that window.
        for thread in threads.values():
            thread.join(timeout=3)
        self._audio.close()

    # --- screen watch ---

    def _screen_watch_loop(self) -> None:
        if not self._settings.screen_watch_enabled:
            return
        while not self._stop_event.is_set():
            try:
                result = self._observer.observe()
                if result.changed and result.text:
                    self._sys_info.update(result.text)
            except Exception:
                logger.exception("screen-watch tick failed, continuing")
            self._stop_event.wait(self._settings.screen_watch_interval_s)

    # --- voice worker ---

    def _voice_worker_loop(self) -> None:
        """WHY the recording_started_at tracking: if a RELEASE is ever lost
        (dropped keybind event, a bug, anything), a live mic recording must
        not run forever — this force-stops it after ptt_max_recording_s."""
        recording_started_at: Optional[float] = None
        while not self._stop_event.is_set():
            try:
                message = self._ptt_queue.get(timeout=1)
            except queue.Empty:
                if recording_started_at is not None and (
                    time.monotonic() - recording_started_at > self._settings.ptt_max_recording_s
                ):
                    logger.warning("PTT recording exceeded max duration, force-stopping")
                    self._handle_release()
                    recording_started_at = None
                continue
            logger.info("PTT message received: %s", message)
            if message == "PRESS":
                self._handle_press()
                recording_started_at = time.monotonic()
            elif message == "RELEASE":
                self._handle_release()
                recording_started_at = None

    def _handle_press(self) -> None:
        try:
            self._mic.start()
        except Exception:
            logger.exception("failed to start mic recording")

    def _handle_release(self) -> None:
        try:
            wav_path, held_s = self._mic.stop()
        except Exception:
            logger.exception("failed to stop mic recording")
            return
        if wav_path is None or held_s * 1000 < self._settings.ptt_min_hold_ms:
            if wav_path is not None:
                wav_path.unlink(missing_ok=True)
            return
        try:
            transcript = self._stt.transcribe(wav_path)
        finally:
            wav_path.unlink(missing_ok=True)
        if transcript:
            self._handle_transcript(transcript)

    def _handle_transcript(self, transcript: str) -> None:
        text = transcript.strip().lower()
        if not text:
            return
        if self._confirm_gate.has_pending():
            self._resolve_pending(text, transcript)
            return
        self._run_voice_turn(transcript)

    def _resolve_pending(self, text: str, transcript: str) -> None:
        command = self._confirm_gate.take_pending()
        if text in CONFIRM_PHRASES:
            self._run_confirmed_command(command)
        elif text in DENY_PHRASES:
            self._audio.stream_vocal_synthesis("Cancelled.")
        else:
            self._run_voice_turn(transcript)  # unrelated speech: stale pending was already dropped

    def _run_voice_turn(self, transcript: str) -> None:
        try:
            self._daemon.run_daemon(transcript, sys_info=self._sys_info.read())
        except Exception:
            logger.exception("run_daemon failed for transcript: %r", transcript)

    def _run_confirmed_command(self, command: str) -> None:
        try:
            output = self._terminal.run_command(command)
            self._audio.stream_vocal_synthesis(output[:400] if output else "Done.")
        except Exception:
            logger.exception("confirmed command execution failed: %r", command)
            self._audio.stream_vocal_synthesis("That command failed. Check the logs.")


def main() -> None:
    init()
    try:
        vault = ConfigVault()
    except ConfigurationError as error:
        print(f"CRITICAL: {error}")
        sys.exit(1)

    settings = vault.settings
    client = genai.Client(api_key=settings.api_key)
    processor = PromptProcessor(client, settings.ai_model, settings.ai_model_light)
    terminal = SystemTerminal()
    audio = AudioEngine(settings.voice_model, settings.debug)
    state = StateManifest(settings.state_path)

    confirm_gate = ConfirmGate(audio, settings.pending_confirm_timeout_s)
    daemon = HyprDaemon(processor, terminal, audio, state, confirm_fn=confirm_gate.request)

    ptt_queue: "queue.Queue[str]" = queue.Queue()
    ptt_server = PushToTalkServer(settings.ptt_socket_path, ptt_queue)
    mic = MicRecorder(settings.mic_source)

    print("Loading speech-to-text model...")
    stt = TranscriptionEngine(settings.stt_model, settings.stt_device)
    print("Model loaded.")

    observer = ScreenObserver()
    sys_info = SysInfoCache(max_chars=settings.sys_info_max_chars)

    service = HyprService(
        settings, daemon, confirm_gate, mic, stt, observer, sys_info,
        ptt_queue, ptt_server, audio, terminal,
    )
    service.run_forever()


if __name__ == "__main__":
    main()
