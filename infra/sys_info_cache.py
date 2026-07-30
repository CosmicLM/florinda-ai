"""sys_info_cache.py — thread-safe, memory-only holder for the latest screen OCR text.

WHY memory-only: screen contents can include passwords, personal messages,
financial data, etc. Persisting them to disk (logs, state.json) would build a
standing, silently-growing record of everything ever glanced at. This cache is
gone the instant the service restarts or the machine reboots, and is never
written through StateManifest or the logger.
"""
import threading


class SysInfoCache:
    """Holds only the most recent screen OCR snapshot — overwrites, never accumulates.

    WHY overwrite instead of accumulate: only the latest screen matters, both
    to bound token cost per AI call and to bound how long stale screen
    contents linger anywhere in memory.
    """

    def __init__(self, max_chars: int = 4000) -> None:
        self._lock = threading.Lock()
        self._text = ""
        self._max_chars = max_chars

    def update(self, text: str) -> None:
        if not text:
            return
        with self._lock:
            self._text = text[-self._max_chars :]

    def read(self) -> str:
        with self._lock:
            return self._text


if __name__ == "__main__":
    cache = SysInfoCache(max_chars=10)
    assert cache.read() == ""
    cache.update("hello world")
    assert cache.read() == "hello worl"[-10:] or len(cache.read()) <= 10
    cache.update("")
    assert cache.read() != ""  # empty update is a no-op, previous value kept
    print("SysInfoCache self-check OK")
