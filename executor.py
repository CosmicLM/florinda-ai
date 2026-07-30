"""executor.py — The Hand: runs shell commands only after the caller has validated intent."""
import os
import subprocess
import sys
from pathlib import Path

from config import NULL_COMMAND

# WHY prepend the venv's own bin dir to every command's PATH: observed live
# — flora-daemon.service runs under venv/bin/python3 (per its systemd
# ExecStart), but subprocess.run(shell=True) with no env override inherits
# the DAEMON's plain PATH (confirmed via /proc/<pid>/environ: no
# venv/bin anywhere in it), not an activated-shell PATH. INSTRUCTION.md
# documents every tool as a bare `python3 <script>.py` COMMAND — under the
# daemon's real PATH that resolves to /usr/bin/python3 (system Python),
# which is missing every third-party package the venv actually has.
# Real failure this caused: `python3 rdkit_runner.py run` raised
# "ModuleNotFoundError: No module named 'rdkit'" even though rdkit was
# correctly installed in venv, because the daemon's own subprocess never
# saw the venv at all. sys.executable is used (not a hardcoded path) since
# the daemon process itself is guaranteed to already be running under
# venv/bin/python3 — this is the same venv, however the project directory
# is currently named, with no path to keep in sync by hand.
#
# WHY .parent, not .resolve().parent: verified live — venv/bin/python3 is
# itself a symlink to the real system interpreter (e.g. /usr/bin/python3.*).
# Resolving it follows that symlink all the way through, landing on
# /usr/bin (the system dir the venv points AT), not venv/bin (the dir the
# symlink actually LIVES in and where all its installed console-script
# wrappers — pip, pyflakes, etc. — actually sit). Only Path(), no resolve.
_VENV_BIN_DIR = str(Path(sys.executable).parent)


class SystemTerminal:
    """The Hand: executes a single shell command and reports its raw output."""

    def run_command(self, shell_command: str) -> str:
        """Execute `shell_command`, returning stdout on success or stderr on failure.

        WHY: NULL_COMMAND is the AI protocol's "no action" sentinel — running it
        literally would shell out to a program named "null". This is the last
        checkpoint before subprocess.run, so the guard clause lives here even
        though callers are also expected to check upstream.
        """
        self._reject_blank_or_null(shell_command)
        env = os.environ.copy()
        env["PATH"] = f"{_VENV_BIN_DIR}{os.pathsep}{env.get('PATH', '')}"
        completed = subprocess.run(shell_command, shell=True, capture_output=True, text=True, env=env)
        return completed.stdout if completed.returncode == 0 else completed.stderr

    @staticmethod
    def _reject_blank_or_null(shell_command: str) -> None:
        if not shell_command or shell_command.lower() == NULL_COMMAND:
            raise ValueError("Command cannot be empty or have a 'null' placeholder.")
