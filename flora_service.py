"""flora_service.py — always-on entrypoint: screen watching + push-to-talk voice input.

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
import subprocess
import sys
import threading
import time
from typing import Optional

from colorama import init
from google import genai

from infra.activity_log import ActivityLog
from watchers.check_in_watcher import CheckInWatcher
from config import ConfigurationError, ConfigVault
from infra.conversation_memory import ConversationMemory
from executor import SystemTerminal
from flora_daemon import FloraDaemon
from backends.local_brain import LocalBrain
from voice.mic_recorder import MicRecorder
from watchers.morning_briefing import MorningBriefing
from processor import PromptProcessor
from voice.ptt_ipc import PushToTalkServer
from watchers.quantum_watcher import QuantumWatcher
from watchers.screen_observer import ScreenObserver
from state_manifest import StateManifest
from infra.status_broadcaster import STATE_IDLE, STATE_WATCHING, StatusBroadcaster
from infra.sys_info_cache import SysInfoCache
from watchers.system_health_watcher import SystemHealthWatcher
from watchers.task_watcher import TaskWatcher
from voice.voice import AudioEngine
from voice.voice_input import TranscriptionEngine
from watchers.watch_toggle import is_paused as watch_is_paused

logger = logging.getLogger(__name__)

CONFIRM_PHRASES = {"yes", "confirm", "do it", "go ahead", "execute"}
DENY_PHRASES = {"no", "cancel", "stop", "never mind", "nevermind"}
TRUST_SESSION_PHRASES = {"trust this session", "trust the session", "trust yourself", "stop asking me", "stop asking"}


def _play_cue(sound_path: Optional[str], label: str) -> None:
    """Module-level (not a FloraService method) so main() can use it to build
    FloraDaemon's on_talking callback without needing a FloraService instance
    to exist yet — FloraService itself is constructed after FloraDaemon."""
    if not sound_path:
        return
    try:
        subprocess.Popen(["paplay", sound_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        logger.exception("failed to play %s cue", label)


class ConfirmGate:
    """Voice-mode replacement for FloraDaemon's blocking input() confirm.

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


class FloraService:
    """Composition root for the always-on service: screen-watch + push-to-talk threads."""

    def __init__(
        self,
        settings,
        daemon: FloraDaemon,
        confirm_gate: ConfirmGate,
        mic: MicRecorder,
        stt: TranscriptionEngine,
        observer: ScreenObserver,
        sys_info: SysInfoCache,
        ptt_queue: "queue.Queue[str]",
        ptt_server: PushToTalkServer,
        audio: AudioEngine,
        terminal: SystemTerminal,
        status: StatusBroadcaster,
        quantum_watcher: Optional[QuantumWatcher] = None,
        activity_log: Optional[ActivityLog] = None,
        task_watcher: Optional[TaskWatcher] = None,
        morning_briefing: Optional[MorningBriefing] = None,
        system_health_watcher: Optional[SystemHealthWatcher] = None,
        check_in_watcher: Optional[CheckInWatcher] = None,
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
        self._status = status
        self._quantum_watcher = quantum_watcher
        self._activity_log = activity_log
        self._task_watcher = task_watcher
        self._morning_briefing = morning_briefing
        self._system_health_watcher = system_health_watcher
        self._check_in_watcher = check_in_watcher
        self._stop_event = threading.Event()

    def attach_quantum_watcher(self, watcher: QuantumWatcher) -> None:
        """Set after construction since QuantumWatcher's on_comment callback
        is this instance's own _speak_proactive_comment method — avoids a
        construction-order chicken-and-egg in main()."""
        self._quantum_watcher = watcher

    def attach_task_watcher(self, watcher: TaskWatcher) -> None:
        """Same construction-order reasoning as attach_quantum_watcher."""
        self._task_watcher = watcher

    def attach_system_health_watcher(self, watcher: SystemHealthWatcher) -> None:
        """Same construction-order reasoning as attach_quantum_watcher."""
        self._system_health_watcher = watcher

    def attach_check_in_watcher(self, watcher: CheckInWatcher) -> None:
        """Same construction-order reasoning as attach_quantum_watcher."""
        self._check_in_watcher = watcher

    def attach_morning_briefing(self, briefing: MorningBriefing) -> None:
        """Same construction-order reasoning as attach_quantum_watcher."""
        self._morning_briefing = briefing

    def run_forever(self) -> None:
        threads = {
            "ptt-ipc": threading.Thread(target=self._ptt_server.run, name="ptt-ipc", daemon=True),
            "screen-watch": threading.Thread(target=self._screen_watch_loop, name="screen-watch", daemon=True),
            "voice-worker": threading.Thread(target=self._voice_worker_loop, name="voice-worker", daemon=True),
        }
        for thread in threads.values():
            thread.start()
        self._install_signal_handlers()
        logger.info("flora_service started (pid=%d)", os.getpid())
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
                    self._status.clear()
                    sys.exit(1)
            self._stop_event.wait(2)
        logger.info("flora_service shutting down")
        self._ptt_server.stop()
        self._status.clear()
        # WHY join with a timeout: threads are daemon=True so the process would
        # exit immediately once main() returns, racing PushToTalkServer's
        # finally-block socket cleanup (its accept() loop notices stop_event
        # only on its next ~1s timeout tick) — join gives it that window.
        for thread in threads.values():
            thread.join(timeout=3)
        self._audio.close()

    # --- screen watch ---

    def _screen_watch_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                if self._settings.screen_watch_enabled and not watch_is_paused():
                    self._run_screen_watch_tick()
                    # WHY check-in lives inside this same gate, unlike
                    # system-health below: it reads real screen content (via
                    # sys_info), so the same privacy pause that stops OCR
                    # must also stop it from commenting on screen content —
                    # system health (memory/disk) has nothing to do with
                    # screen content and should keep reporting regardless.
                    if self._check_in_watcher is not None:
                        self._check_in_watcher.poll_once()
                if self._task_watcher is not None:
                    self._task_watcher.poll_once()
                if self._morning_briefing is not None:
                    self._morning_briefing.poll_once()
                if self._system_health_watcher is not None:
                    self._system_health_watcher.poll_once()
            except Exception:
                logger.exception("screen-watch tick failed, continuing")
            self._stop_event.wait(self._settings.screen_watch_interval_s)

    def _run_screen_watch_tick(self) -> None:
        """WHY the idle check before AND after: this runs on its own thread,
        separate from the voice-worker thread that owns listening/thinking/
        talking — only flip to "watching" if nothing else is already
        happening (a real interaction must never be visually interrupted by
        the background screen-watch pipeline), and only flip back to idle
        afterward if the state is STILL "watching" (if a voice turn started
        mid-tick and moved it on, resetting to idle here would incorrectly
        stomp on that)."""
        was_idle = self._status.get_state() == STATE_IDLE
        if was_idle:
            self._status.set_watching()
        try:
            result = self._observer.observe()
            if result.changed and result.text:
                self._sys_info.update(result.text)
                if self._quantum_watcher is not None:
                    self._quantum_watcher.observe(result.text)
        finally:
            if was_idle and self._status.get_state() == STATE_WATCHING:
                self._status.set_idle()

    def _speak_proactive_comment(self, comment: str, label: str = "FLORINDA") -> bool:
        """on_comment/on_report callback for QuantumWatcher and TaskWatcher —
        runs on the screen-watch thread, a separate thread from voice-worker's
        own conversation flow. Returns whether the comment was actually
        spoken, so a caller that must not silently drop a report (see
        TaskWatcher, which only archives a task log once its report has
        actually been delivered) knows to retry on a later tick instead.

        WHY the idle check: this fires asynchronously, with no relation to
        whatever the user is currently doing — speaking over an active PTT
        interaction (listening/thinking/talking) would talk over the user or
        interleave with an unrelated reply. Staying silent and letting the
        next tick try again is preferable to interrupting.
        """
        if self._status.get_state() != STATE_IDLE:
            logger.info("suppressing proactive comment, not idle: %r", comment)
            return False
        self._status.set_talking()
        try:
            self._audio.stream_vocal_synthesis(comment)
            if self._activity_log:
                self._activity_log.write(label, comment)
            self._audio.wait_until_done()  # don't go idle while this is still audibly playing
        finally:
            self._status.set_idle()
        return True

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
                press_time = time.monotonic()
                self._start_recording_silently()
                if self._await_confirmed_hold(press_time):
                    self._announce_listening()
                    recording_started_at = press_time
                else:
                    recording_started_at = None  # too-brief tap, already discarded
            elif message == "RELEASE":
                self._handle_release()
                recording_started_at = None

    def _start_recording_silently(self) -> None:
        """Starts capturing audio the instant the button goes down, with no
        chime or status change yet — so a genuine hold never loses its first
        moment of speech while we wait to see if it's actually a hold."""
        try:
            self._mic.start()
        except Exception:
            logger.exception("failed to start mic recording")

    def _await_confirmed_hold(self, press_time: float) -> bool:
        """Blocks (on this thread only — the ptt-ipc thread keeps queuing
        independently) until the press has genuinely been held for
        ptt_min_hold_ms. Returns True once that's confirmed (still down,
        listening should now be announced). Returns False if a RELEASE
        arrives first — a too-brief, likely accidental tap — in which case
        the silently-started recording is discarded here and the chime/
        listening status never fire at all, which is the whole point:
        observed live, EVERY touch played the cue and flipped the widget to
        "listening" even for taps discarded a moment later; a real hold is
        now required before either happens.
        """
        threshold_s = self._settings.ptt_min_hold_ms / 1000
        while True:
            remaining = threshold_s - (time.monotonic() - press_time)
            if remaining <= 0:
                return True
            try:
                message = self._ptt_queue.get(timeout=remaining)
            except queue.Empty:
                return True
            if message == "RELEASE":
                self._discard_short_recording()
                return False
            # a stray extra PRESS while already down — ignore, keep waiting

    def _discard_short_recording(self) -> None:
        try:
            self._mic.stop()
        except Exception:
            logger.exception("failed to stop short-tap mic recording")

    def _announce_listening(self) -> None:
        # WHY interrupt() fires HERE, not on raw PRESS anymore: it used to be
        # wired as PushToTalkServer's on_press callback, firing on literally
        # every touch, tap or hold — observed live/reported: a brief
        # accidental tap would barge in on an active reply just as readily
        # as a real hold, which is exactly backwards from what a barge-in
        # gesture should require. Moving it to the confirmed-hold point
        # means a genuine hold still interrupts fast (barely slower than
        # before — the hold-confirmation threshold is only ~300ms), while a
        # brief tap no longer touches anything that's currently talking.
        self._daemon.interrupt()
        self._status.set_listening()
        self._play_listening_cue()

    def _play_listening_cue(self) -> None:
        """Fire-and-forget, non-blocking — a UI nicety, never allowed to add
        latency to the actual recording start."""
        _play_cue(self._settings.ptt_listen_sound, "listening")

    def _start_talking(self) -> None:
        """WHY a shared helper instead of calling set_talking() directly at
        each call site: this marks specifically the "was thinking (just
        transcribed real speech), now about to answer" transition — used at
        the sites that don't go through FloraDaemon.run_daemon() at all (a
        confirmed command, the trust-session/cancel acknowledgments), which
        need it called directly since there's no daemon leg for them to hook
        into. The main voice turn gets the equivalent via FloraDaemon's own
        on_talking callback (see main()) instead, since that path can have
        several talking/thinking legs within one call and FloraDaemon is the
        only thing that knows when each one starts. Deliberately NOT used
        for _speak_proactive_comment's idle-to-talking jump either, which
        never went through a thinking phase in the first place."""
        self._status.set_talking()
        _play_cue(self._settings.ptt_talking_sound, "talking")

    def _handle_release(self) -> None:
        try:
            wav_path, held_s = self._mic.stop()
        except Exception:
            logger.exception("failed to stop mic recording")
            self._status.set_idle()
            return
        if wav_path is None or held_s * 1000 < self._settings.ptt_min_hold_ms:
            if wav_path is not None:
                wav_path.unlink(missing_ok=True)
            self._status.set_idle()
            return
        self._status.set_thinking()
        try:
            transcript = self._stt.transcribe(wav_path)
        finally:
            wav_path.unlink(missing_ok=True)
        if transcript:
            self._handle_transcript(transcript)
        else:
            self._status.set_idle()

    def _handle_transcript(self, transcript: str) -> None:
        """WHY wrapped in try/finally here rather than in each branch below:
        every branch (empty text, confirm, deny, trust-only, a full AI turn)
        needs to end back at "idle" — bracketing once here guarantees that
        regardless of which branch runs or whether it raises, instead of
        needing every branch to remember to reset status itself.

        WHY wait_until_done() before set_idle(): every branch below may have
        just queued speech (stream_vocal_synthesis is fire-and-forget), so
        going idle immediately after routing returns can happen while Florinda
        is still audibly talking — observed live. This is the single choke
        point all of those branches funnel through, so it's the one place
        this needs to be added to cover every one of them.
        """
        try:
            self._route_transcript(transcript)
        finally:
            self._audio.wait_until_done()
            self._status.set_idle()

    def _route_transcript(self, transcript: str) -> None:
        """WHY trust-session is checked before the pending gate, not as just
        another confirm phrase: it should work whether or not there's a
        currently pending command — said on its own it just stops future
        asking; said while something's pending, it also approves that one,
        since asking to be trusted implies "yes, and stop asking" rather than
        "yes, just this once"."""
        text = transcript.strip().lower()
        if not text:
            return
        trusting_now = text in TRUST_SESSION_PHRASES
        if trusting_now:
            self._daemon.enable_always_execute()
        if self._confirm_gate.has_pending():
            self._resolve_pending(text, transcript, trusting_now)
            return
        if trusting_now:
            self._start_talking()
            self._audio.stream_vocal_synthesis(
                "Understood. I will run commands without asking for the rest of this session."
            )
            return
        self._run_voice_turn(transcript)

    def _resolve_pending(self, text: str, transcript: str, trusting_now: bool = False) -> None:
        command = self._confirm_gate.take_pending()
        if trusting_now or text in CONFIRM_PHRASES:
            self._run_confirmed_command(command)
        elif text in DENY_PHRASES:
            self._start_talking()
            self._audio.stream_vocal_synthesis("Cancelled.")
        else:
            self._run_voice_turn(transcript)  # unrelated speech: stale pending was already dropped

    def _run_voice_turn(self, transcript: str) -> None:
        # WHY no _start_talking() here: status stays "thinking" (set by
        # _handle_release before transcription) until FloraDaemon itself
        # flips to "talking" via its own on_talking callback, the moment
        # the FIRST real sentence of the reply is ready to speak — not
        # preemptively, before any generation has even started.
        try:
            self._daemon.run_daemon(transcript, sys_info=self._sys_info.read())
        except Exception:
            logger.exception("run_daemon failed for transcript: %r", transcript)

    def _run_confirmed_command(self, command: str) -> None:
        self._start_talking()
        if self._activity_log:
            self._activity_log.write("COMMAND", command)
        try:
            output = self._terminal.run_command(command)
            if self._activity_log:
                self._activity_log.write("OUTPUT", output[:1000] if output else "")
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
    # WHY only built when ai_provider is actually "gemini": see matching
    # comment in flora_daemon.py's main() — genai.Client(api_key=None)
    # raises immediately at construction, not lazily, which would crash
    # startup for a user running purely on openai/anthropic.
    client = None
    if settings.ai_provider == "gemini":
        client = genai.Client(
            api_key=settings.api_key,
            # WHY retry_options attempts=1: see matching comment in
            # flora_daemon.py's main() — google-genai's default 5 retries with
            # exponential backoff turned a 10s timeout into a real 52s+ wait
            # before the offline fallback ever got a chance to run.
            http_options={
                "timeout": int(settings.gemini_timeout_s * 1000),
                "retry_options": {"attempts": 1},
            },
        )
    processor = PromptProcessor(
        client, settings.ai_model, settings.ai_model_light,
        offline_model=settings.offline_fallback_model if settings.offline_fallback_enabled else None,
        ollama_host=settings.ollama_host,
        use_claude_cli_for_deep=settings.use_claude_cli_for_deep,
        claude_cli_timeout_s=settings.claude_cli_timeout_s,
        ai_provider=settings.ai_provider,
        openai_api_key=settings.openai_api_key,
        openai_base_url=settings.openai_base_url,
        openai_model=settings.openai_model,
        openai_model_light=settings.openai_model_light,
        anthropic_api_key=settings.anthropic_api_key,
        anthropic_model=settings.anthropic_model,
        anthropic_model_light=settings.anthropic_model_light,
    )
    terminal = SystemTerminal()
    audio = AudioEngine(settings.voice_model, settings.debug)
    state = StateManifest(settings.state_path)

    confirm_gate = ConfirmGate(audio, settings.pending_confirm_timeout_s)
    memory = ConversationMemory(
        settings.memory_path, settings.memory_max_turns, settings.memory_reset_after_s
    )
    activity_log = ActivityLog(settings.activity_log_path, settings.activity_log_max_lines)

    # Built before FloraDaemon (and before FloraService, which doesn't exist
    # yet either) specifically so its own on_talking callback can be handed
    # in at construction time — FloraDaemon needs to know when each leg of a
    # reply starts talking/thinking, and this closure only needs `status`
    # and `settings`, not a FloraService instance.
    status = StatusBroadcaster(settings.status_path, settings.waybar_signal_num)
    status.set_idle()

    def _on_talking() -> None:
        status.set_talking()
        _play_cue(settings.ptt_talking_sound, "talking")

    daemon = FloraDaemon(
        processor, terminal, audio, state, confirm_fn=confirm_gate.request, memory=memory,
        activity_log=activity_log, on_thinking=status.set_thinking, on_talking=_on_talking,
    )

    ptt_queue: "queue.Queue[str]" = queue.Queue()
    # WHY no on_press here anymore: interrupt() now fires from
    # _announce_listening(), once a press is confirmed as a genuine hold —
    # see that method's docstring for why a raw, unconfirmed tap shouldn't
    # be enough to barge in on an active reply.
    ptt_server = PushToTalkServer(settings.ptt_socket_path, ptt_queue)
    mic = MicRecorder(settings.mic_source)

    print("Loading speech-to-text model...")
    stt = TranscriptionEngine(settings.stt_model, settings.stt_device)
    print("Model loaded.")

    observer = ScreenObserver()
    sys_info = SysInfoCache(max_chars=settings.sys_info_max_chars)

    service = FloraService(
        settings, daemon, confirm_gate, mic, stt, observer, sys_info,
        ptt_queue, ptt_server, audio, terminal, status,
        activity_log=activity_log,
    )

    local_brain = LocalBrain(settings.local_model, settings.ollama_host)

    if settings.quantum_watch_enabled:
        quantum_watcher = QuantumWatcher(
            local_brain, settings.quantum_watch_keywords, settings.quantum_watch_cooldown_s,
            on_comment=lambda c: service._speak_proactive_comment(c, label="FLORINDA (quantum-watch)"),
        )
        service.attach_quantum_watcher(quantum_watcher)

    task_watcher = TaskWatcher(
        on_report=lambda c: service._speak_proactive_comment(c, label="FLORINDA (task-report)")
    )
    service.attach_task_watcher(task_watcher)

    if settings.morning_briefing_enabled:
        morning_briefing = MorningBriefing(
            on_report=lambda c: service._speak_proactive_comment(c, label="FLORINDA (morning)"),
            web_search_host=settings.web_search_host,
        )
        service.attach_morning_briefing(morning_briefing)

    system_health_watcher = SystemHealthWatcher(
        on_report=lambda c: service._speak_proactive_comment(c, label="FLORINDA (system-health)"),
        cooldown_s=settings.system_health_cooldown_s,
    )
    service.attach_system_health_watcher(system_health_watcher)

    if settings.check_in_enabled:
        check_in_watcher = CheckInWatcher(
            local_brain, get_screen_text=sys_info.read, cooldown_s=settings.check_in_cooldown_s,
            on_comment=lambda c: service._speak_proactive_comment(c, label="FLORINDA (check-in)"),
        )
        service.attach_check_in_watcher(check_in_watcher)

    service.run_forever()


if __name__ == "__main__":
    main()
