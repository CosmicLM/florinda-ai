"""executor.py — The Hand: runs shell commands only after the caller has validated intent."""
import subprocess

from config import NULL_COMMAND


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
        completed = subprocess.run(shell_command, shell=True, capture_output=True, text=True)
        return completed.stdout if completed.returncode == 0 else completed.stderr

    @staticmethod
    def _reject_blank_or_null(shell_command: str) -> None:
        if not shell_command or shell_command.lower() == NULL_COMMAND:
            raise ValueError("Command cannot be empty or have a 'null' placeholder.")
