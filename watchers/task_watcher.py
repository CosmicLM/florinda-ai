"""task_watcher.py — watches ~/.local/share/flora-ai/tasks/*.log for background
tasks started via task_runner.py, and reports back once each finishes,
instead of leaving the user to go check a terminal window themselves.

WHY this is pure code, not a model call: task_runner.py runs every command
under `script -e`, which already writes the REAL exit code (a
`[COMMAND_EXIT_CODE="N"]` footer) and the original command line (a
`[COMMAND="..."]` header) into the log — verified live (`cat -A` on a real
script(1) transcript). Whether a task succeeded or failed is therefore
already known for certain by the time this runs; asking a model to
"summarize" it could only add latency and a real risk of paraphrasing or
inventing details (e.g. a package count) that aren't literally in the
transcript, for a judgment call the code doesn't need to make.

WHY moved to tasks/reported/ instead of deleted or left in place: leaving a
finished log where it is would re-trigger a report on every future poll
(and again after a service restart, since "already reported" is only
tracked in memory) — moving it out of the watched glob is what actually
stops that, while still keeping the transcript around if the user wants to
go check it later.

WHY a report is only archived AFTER a successful delivery, not as soon as
it's seen: on_report (Florinda's proactive-comment path) can decline to speak
right now if the user is mid-conversation — observed live: a report
generated while the system wasn't idle got archived immediately anyway,
permanently losing it since nothing would ever retry an already-archived
log. Reports are composed once and cached in memory so a delayed retry
doesn't re-parse the log every poll while waiting for an idle moment.
"""
import logging
import re
from pathlib import Path
from typing import Callable, Dict, Optional

from tools.task_runner import DONE_MARKER, TASKS_DIR

logger = logging.getLogger(__name__)

_EXIT_CODE_RE = re.compile(r'COMMAND_EXIT_CODE="(\d+)"')
# WHY the lookahead instead of matching a fixed suffix: verified live —
# script(1)'s header attributes after COMMAND="..." differ depending on
# whether it's attached to a real pty. Run directly from an existing shell
# it appends `<not executed on terminal>`; run the way task_runner.py
# actually does it (spawned inside kitty, a real pty) it instead appends
# `TERM="..." TTY="..." COLUMNS="..." LINES="..."` — no fixed suffix text is
# safe to depend on, only that the COMMAND value's closing quote is followed
# by a space or the closing `]`.
_STARTED_COMMAND_RE = re.compile(r'\[COMMAND="(.*?)"(?=[\s\]])')
_MAX_TAIL_CHARS = 200


class TaskWatcher:
    """Polls task_runner.py's log directory for finished tasks and reports on them."""

    def __init__(
        self, on_report: Callable[[str], bool], tasks_dir: Path = TASKS_DIR
    ) -> None:
        self._on_report = on_report
        self._tasks_dir = tasks_dir
        self._pending_reports: Dict[Path, str] = {}

    def poll_once(self) -> None:
        if not self._tasks_dir.exists():
            return
        self._compose_newly_finished()
        self._deliver_pending()

    def _compose_newly_finished(self) -> None:
        for log_path in self._tasks_dir.glob("*.log"):
            if log_path in self._pending_reports:
                continue
            try:
                content = log_path.read_text(errors="ignore")
            except OSError:
                continue
            if DONE_MARKER not in content:
                continue
            self._pending_reports[log_path] = _compose_report(content)

    def _deliver_pending(self) -> None:
        for log_path, report in list(self._pending_reports.items()):
            if self._on_report(report):
                del self._pending_reports[log_path]
                self._archive(log_path)

    def _archive(self, log_path: Path) -> None:
        reported_dir = self._tasks_dir / "reported"
        reported_dir.mkdir(parents=True, exist_ok=True)
        try:
            log_path.rename(reported_dir / log_path.name)
        except OSError:
            logger.exception("failed to archive finished task log %s", log_path)


def _compose_report(content: str) -> str:
    command = _extract_command(content)
    exit_code = _extract_exit_code(content)
    subject = f"Your command, {command},".strip() if command else "Your background task"

    if exit_code is None:
        return f"{subject} finished, but I couldn't find its exit code in the log."
    if exit_code == 0:
        report = f"{subject} finished successfully."
    else:
        report = f"{subject} failed with exit code {exit_code}."
        tail = _extract_tail(content)
        if tail:
            report += f" Last output: {tail}"
    return report


def _extract_command(content: str) -> Optional[str]:
    match = _STARTED_COMMAND_RE.search(content)
    return match.group(1) if match else None


def _extract_exit_code(content: str) -> Optional[int]:
    match = _EXIT_CODE_RE.search(content)
    return int(match.group(1)) if match else None


def _extract_tail(content: str) -> str:
    """The last real output line before script's own done-footer — likely
    the actual error message, without needing anything to interpret it."""
    body_lines = [
        line.rstrip("\r") for line in content.splitlines()
        if line.strip("\r") and not line.startswith("Script started on") and not line.startswith("Script done on")
    ]
    if not body_lines:
        return ""
    tail = body_lines[-1].strip()
    return tail[:_MAX_TAIL_CHARS]


if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory() as tmp_dir:
        tasks_dir = Path(tmp_dir)
        unfinished = tasks_dir / "unfinished.log"
        unfinished.write_text("hello\nstill running...\n")

        success_log = (
            'Script started on 2026-07-29 13:00:00-06:00 [COMMAND="echo hello" <not executed on terminal>]\r\n'
            "hello\r\n"
            '\nScript done on 2026-07-29 13:00:01-06:00 [COMMAND_EXIT_CODE="0"]\n'
        )
        finished = tasks_dir / "finished.log"
        finished.write_text(success_log)

        # Simulate the user being mid-conversation: delivery fails at first.
        delivery_allowed = [False]
        reports: list[str] = []

        def on_report(report: str) -> bool:
            if not delivery_allowed[0]:
                return False
            reports.append(report)
            return True

        watcher = TaskWatcher(on_report=on_report, tasks_dir=tasks_dir)
        watcher.poll_once()

        assert reports == [], "should not report while delivery is refused"
        assert finished.exists(), "must NOT archive until delivery actually succeeds"
        print("OK: undelivered report is neither lost nor archived")

        delivery_allowed[0] = True
        watcher.poll_once()  # no new composition — reuses the cached report
        assert reports == ["Your command, echo hello, finished successfully."], reports
        assert not finished.exists()
        assert (tasks_dir / "reported" / "finished.log").exists()
        print("OK: retried delivery succeeds once idle, then archives")

        assert unfinished.exists(), "unfinished task log should never be touched"
        print("OK: unfinished task left alone")

        watcher.poll_once()
        assert reports == ["Your command, echo hello, finished successfully."], "should not re-report after archiving"
        print("OK: no duplicate report on a later poll")

        failure_log = (
            'Script started on 2026-07-29 13:05:00-06:00 [COMMAND="false thing" <not executed on terminal>]\r\n'
            "some real error output\r\n"
            '\nScript done on 2026-07-29 13:05:01-06:00 [COMMAND_EXIT_CODE="1"]\n'
        )
        failed = tasks_dir / "failed.log"
        failed.write_text(failure_log)
        watcher.poll_once()
        assert reports[-1] == (
            "Your command, false thing, failed with exit code 1. Last output: some real error output"
        ), reports[-1]
        print("OK: a failed task reports the real exit code and last real output line")

        no_exit_code_log = "Script started on 2026-07-29 13:10:00-06:00 [COMMAND=\"weird\"]\nScript done on 2026-07-29 13:10:01-06:00\n"
        broken = tasks_dir / "broken.log"
        broken.write_text(no_exit_code_log)
        watcher.poll_once()
        assert "couldn't find its exit code" in reports[-1], reports[-1]
        print("OK: a log missing the exit-code footer is reported honestly rather than guessed at")

        # Real format verified live: task_runner.py always spawns script(1)
        # inside a kitty pty, which appends TERM=/TTY=/COLUMNS=/LINES=
        # attributes after COMMAND="..." instead of "<not executed on
        # terminal>" — the regex must handle this shape too, not just the
        # one produced when script is run directly from an existing shell.
        real_pty_log = (
            'Script started on 2026-07-29 13:39:12-06:00 [COMMAND="bash -c \'echo doing real work; sleep 1; exit 7\'" '
            'TERM="xterm-kitty" TTY="/dev/pts/2" COLUMNS="88" LINES="20"]\r\n'
            "doing real work\r\n"
            '\nScript done on 2026-07-29 13:39:13-06:00 [COMMAND_EXIT_CODE="7"]\n'
        )
        real_pty = tasks_dir / "real_pty.log"
        real_pty.write_text(real_pty_log)
        watcher.poll_once()
        assert reports[-1] == (
            "Your command, bash -c 'echo doing real work; sleep 1; exit 7', failed with exit code 7. "
            "Last output: doing real work"
        ), reports[-1]
        print("OK: the real kitty-pty header format (TERM=/TTY=/...) is parsed correctly too")

        print("TaskWatcher self-check OK")
