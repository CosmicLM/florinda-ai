"""task_runner.py — runs a command in a real, visible terminal (so the user
can interact with prompts like a sudo password) while capturing its full
transcript, so task_watcher.py can read the result afterward and Florinda can
report back autonomously once it's done.

WHY this exists: some commands (AUR helpers like yay, anything needing sudo)
cannot run through Florinda's own non-interactive subprocess at all — they need
a real TTY a human can actually type into (see INSTRUCTION.md's "NEVER wrap
yay in pkexec" note). This spawns exactly that: a kitty window running the
command under `script`, which allocates a real pty so interactive prompts
work normally, while leaving a full transcript (plus the command's real exit
code, via script's own `-e` flag and "COMMAND_EXIT_CODE" footer) behind for
task_watcher.py to pick up once the window closes.
"""
import shlex
import subprocess
import time
import uuid
from pathlib import Path

TASKS_DIR = Path.home() / ".local/share/flora-ai/tasks"
DONE_MARKER = "Script done on"  # script's own footer line — no custom sentinel needed


def run(command_parts: list[str]) -> Path:
    TASKS_DIR.mkdir(parents=True, exist_ok=True)
    task_id = f"{int(time.time())}-{uuid.uuid4().hex[:8]}"
    log_path = TASKS_DIR / f"{task_id}.log"
    command_str = " ".join(shlex.quote(part) for part in command_parts)
    inner = f"script -qec {shlex.quote(command_str)} {shlex.quote(str(log_path))}"
    subprocess.Popen(
        ["kitty", "--class", "flora-task", "--title", "Florinda Task", "-e", "sh", "-c", inner],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return log_path


def _main() -> None:
    import sys

    # WHY not argparse for the command itself: argparse's positional nargs
    # ("+"/REMAINDER) still treats a leading "-" as "this must be one of MY
    # options" and errors out — which breaks on essentially every real
    # command (`yay -Syu`, `bash -c ...`). "run" is the only subcommand this
    # tool has, so there's nothing ambiguous about just taking everything
    # after it verbatim.
    if len(sys.argv) < 3 or sys.argv[1] != "run":
        print("usage: task_runner.py run <command...>", file=sys.stderr)
        sys.exit(1)
    command_parts = sys.argv[2:]
    log_path = run(command_parts)
    print(
        f"Started {' '.join(command_parts)!r} in a new terminal window. "
        f"I'll let you know the result once it finishes (tracking: {log_path})."
    )


if __name__ == "__main__":
    _main()
