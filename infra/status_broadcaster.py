"""status_broadcaster.py — publishes Florinda's current state for the Waybar widget.

WHY a file + signal instead of, say, a socket: Waybar's custom modules are
built around re-running an exec script (on an interval, or immediately on a
signal) and parsing its stdout as JSON — there's no push API to hook into
directly. Writing a small state file and nudging Waybar with a real-time
signal (the same "signal": N mechanism this machine's own waybar config
already uses for custom/updates) is the natural fit for that model, not a
workaround.
"""
import json
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional

STATE_IDLE = "idle"
STATE_LISTENING = "listening"
STATE_THINKING = "thinking"
STATE_TALKING = "talking"
STATE_WATCHING = "watching"


class StatusBroadcaster:
    """Writes Florinda's current state to disk and nudges Waybar to refresh."""

    def __init__(self, status_path: Path, signal_num: int, blink_interval_s: float = 0.4) -> None:
        self._status_path = status_path
        self._signal_num = signal_num
        self._blink_interval_s = blink_interval_s
        self._blink_stop: Optional[threading.Event] = None
        self._blink_thread: Optional[threading.Thread] = None

    def set_idle(self) -> None:
        self._stop_blinking()
        self._set(STATE_IDLE)

    def set_listening(self) -> None:
        self._stop_blinking()
        self._set(STATE_LISTENING)

    def set_thinking(self) -> None:
        self._stop_blinking()
        self._set(STATE_THINKING)

    def set_watching(self) -> None:
        """Screen-watch pipeline (screenshot + OCR + analysis) is actively
        running a tick — distinct from idle so the widget gives real visual
        confirmation the background watcher is alive, not just silently
        polling with no feedback."""
        self._stop_blinking()
        self._set(STATE_WATCHING)

    def set_talking(self) -> None:
        """WHY a background blink thread instead of just writing STATE_TALKING
        once: a static state can't visually read as "live" — this pulses a
        `phase` field between 0/1 on its own timer, independent of whatever
        polling interval Waybar's module config uses, so the widget can
        alternate a solid/hollow dot for as long as Florinda is actually talking
        (see waybar_flora_status.py). Restarting the thread on every call is
        harmless — repeated set_talking() calls (once per recursive leg of a
        reply) just resets the blink phase, not a bug worth guarding against.
        """
        self._stop_blinking()
        self._blink_stop = threading.Event()
        self._blink_thread = threading.Thread(target=self._blink_loop, daemon=True)
        self._blink_thread.start()

    def _blink_loop(self) -> None:
        phase = 0
        stop_event = self._blink_stop
        while not stop_event.is_set():
            self._set(STATE_TALKING, phase=phase)
            phase = 1 - phase
            stop_event.wait(self._blink_interval_s)

    def _stop_blinking(self) -> None:
        if self._blink_thread is not None:
            self._blink_stop.set()
            self._blink_thread.join(timeout=1)
            self._blink_thread = None
            self._blink_stop = None

    def get_state(self) -> Optional[str]:
        """Returns the current state, or None if no status has been set yet
        (or the file is unreadable) — used by background jobs that want to
        speak proactively (e.g. quantum_watcher.py) to check they're not
        about to talk over an active push-to-talk interaction."""
        try:
            with open(self._status_path, "r") as status_file:
                return json.load(status_file).get("state")
        except (OSError, json.JSONDecodeError):
            return None

    def clear(self) -> None:
        """Removes the status file so the widget shows "offline" rather than
        a stale "idle" that would wrongly imply the service is still up.
        Call this on shutdown, not just on process death — a leftover file
        from an unclean exit is caught anyway by the reader script's
        staleness check, but a clean shutdown can do better than "wait up to
        60s for it to look stale"."""
        self._stop_blinking()
        self._status_path.unlink(missing_ok=True)
        self._nudge_waybar()

    def _set(self, state: str, phase: Optional[int] = None) -> None:
        self._status_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"state": state, "updated_at": time.time()}
        if phase is not None:
            payload["phase"] = phase
        with open(self._status_path, "w") as status_file:
            json.dump(payload, status_file)
        self._nudge_waybar()

    def _nudge_waybar(self) -> None:
        """Best-effort only — a UI refresh nicety, not a correctness-critical
        path. Waybar not running (or pkill missing) is silently ignored;
        Waybar's own interval-based fallback poll will catch up regardless."""
        try:
            subprocess.run(
                ["pkill", f"-SIGRTMIN+{self._signal_num}", "waybar"],
                capture_output=True,
                timeout=1,
            )
        except Exception:
            pass


if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory() as tmp_dir:
        broadcaster = StatusBroadcaster(Path(tmp_dir) / "status.json", signal_num=8, blink_interval_s=0.05)
        for setter, expected in [
            (broadcaster.set_idle, STATE_IDLE),
            (broadcaster.set_listening, STATE_LISTENING),
            (broadcaster.set_thinking, STATE_THINKING),
            (broadcaster.set_watching, STATE_WATCHING),
        ]:
            setter()
            with open(broadcaster._status_path) as f:
                data = json.load(f)
            assert data["state"] == expected, data
            assert "phase" not in data, data
            print(f"OK: {expected} -> {data}")

        broadcaster.set_talking()
        time.sleep(0.03)
        with open(broadcaster._status_path) as f:
            first = json.load(f)
        assert first["state"] == STATE_TALKING and first["phase"] == 0, first
        time.sleep(0.08)
        with open(broadcaster._status_path) as f:
            second = json.load(f)
        assert second["phase"] == 1, second
        print(f"OK: talking blinks phase 0 -> 1 on its own timer ({first} -> {second})")

        broadcaster.set_idle()
        assert broadcaster._blink_thread is None
        print("OK: leaving talking stops the blink thread")

        print("StatusBroadcaster self-check OK")
