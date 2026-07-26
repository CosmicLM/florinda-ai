"""ptt_ipc.py — Unix domain socket server receiving PRESS/RELEASE signals from Hyprland.

WHY a socket instead of e.g. signals or a file watch: the Hyprland bind needs
to hand off to an already-running process near-instantly (see
scripts/hypr_ptt_hook.py) — a lightweight local socket round-trip is the
simplest thing that's both fast and lets the hook fail silently (daemon not
running yet) without blocking key dispatch.
"""
import logging
import queue
import socket
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

VALID_MESSAGES = {"PRESS", "RELEASE"}


class PushToTalkServer:
    """Accepts PRESS/RELEASE messages over a Unix socket and queues them for a consumer."""

    def __init__(self, socket_path: Path, message_queue: "queue.Queue[str]") -> None:
        self._socket_path = socket_path
        self._queue = message_queue
        self._stop_event = threading.Event()
        self._server_socket: socket.socket | None = None

    def run(self) -> None:
        """Accept loop — call this as a thread target. Exits cleanly on stop()."""
        self._bind_socket()
        try:
            while not self._stop_event.is_set():
                self._accept_one()
        finally:
            self._cleanup_socket()

    def stop(self) -> None:
        self._stop_event.set()

    def _bind_socket(self) -> None:
        self._socket_path.parent.mkdir(parents=True, exist_ok=True)
        if self._socket_path.exists():
            self._socket_path.unlink()
        self._server_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server_socket.bind(str(self._socket_path))
        self._server_socket.settimeout(1.0)
        self._server_socket.listen(1)

    def _accept_one(self) -> None:
        try:
            connection, _ = self._server_socket.accept()
        except socket.timeout:
            return
        try:
            self._handle_connection(connection)
        except Exception:
            logger.exception("PTT connection handling failed")
        finally:
            connection.close()

    def _handle_connection(self, connection: socket.socket) -> None:
        data = connection.recv(64).decode(errors="ignore").strip()
        if data in VALID_MESSAGES:
            logger.info("PTT socket received: %s", data)
            self._queue.put(data)
        else:
            logger.warning("PTT socket received unrecognized message: %r", data)

    def _cleanup_socket(self) -> None:
        if self._server_socket is not None:
            self._server_socket.close()
        if self._socket_path.exists():
            self._socket_path.unlink()


if __name__ == "__main__":
    import socket as socket_module
    import time

    test_socket_path = Path("/tmp/hypr-ai-ptt-selftest.sock")
    q: "queue.Queue[str]" = queue.Queue()
    server = PushToTalkServer(test_socket_path, q)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    time.sleep(0.2)

    with socket_module.socket(socket_module.AF_UNIX, socket_module.SOCK_STREAM) as client:
        client.connect(str(test_socket_path))
        client.sendall(b"PRESS\n")

    message = q.get(timeout=2)
    assert message == "PRESS", message
    server.stop()
    thread.join(timeout=2)
    print("PushToTalkServer self-check OK")
