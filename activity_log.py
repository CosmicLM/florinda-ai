"""activity_log.py — append-only, human-readable transcript of what Florinda is
doing, for the read-only "show me what's running" view opened by clicking
the Waybar widget (see modules.json's custom/hypr on-click, which tails this
file in a terminal).

WHY a separate file from flora-ai.log: that log (config.py's logger) is for
exceptions/warnings only — this one is a deliberately human-facing
transcript of normal operation (what was heard, what was said, what command
ran and what it returned), meant to be watched live in a terminal, not
grepped for errors.
"""
import time
from pathlib import Path


class ActivityLog:
    """Appends timestamped, labeled lines to a bounded plain-text log."""

    def __init__(self, path: Path, max_lines: int = 2000) -> None:
        self._path = path
        self._max_lines = max_lines

    def write(self, label: str, text: str) -> None:
        if not text or not text.strip():
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        timestamp = time.strftime("%H:%M:%S")
        with open(self._path, "a") as log_file:
            for line in text.splitlines():
                log_file.write(f"[{timestamp}] {label}: {line}\n")
        self._trim()

    def _trim(self) -> None:
        """WHY read-the-whole-file-back instead of tracking a running count:
        this is a low-frequency, human-facing log (one write per spoken turn
        or executed command, not a hot path) — simplicity here costs nothing
        that matters."""
        try:
            lines = self._path.read_text().splitlines()
        except OSError:
            return
        if len(lines) > self._max_lines:
            self._path.write_text("\n".join(lines[-self._max_lines :]) + "\n")


if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory() as tmp_dir:
        path = Path(tmp_dir) / "activity.log"
        log = ActivityLog(path, max_lines=3)

        log.write("YOU", "what time is it")
        log.write("HYPR", "It's 3 PM.")
        content = path.read_text()
        assert "YOU: what time is it" in content, content
        assert "HYPR: It's 3 PM." in content, content
        print("OK: basic write works")

        log.write("COMMAND", "date")
        log.write("OUTPUT", "Sun Jul 26 15:00:00 MDT 2026")
        lines = path.read_text().splitlines()
        assert len(lines) == 3, lines  # trimmed to max_lines=3
        assert lines[0].endswith("HYPR: It's 3 PM."), lines
        print("OK: trims to max_lines, keeping the most recent")

        log.write("EMPTY", "")
        assert len(path.read_text().splitlines()) == 3, "empty text should be a no-op"
        print("OK: empty text is a no-op")

        print("ActivityLog self-check OK")
