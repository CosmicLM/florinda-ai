"""system_health_watcher.py — proactively detects things that would degrade
Florinda's own responsiveness (severe memory pressure, heavy swapping, a nearly
full disk) and tells the user directly, instead of them just noticing Florinda
feels slower than usual with no idea why.

WHY this is deterministic (plain threshold checks), not a model call:
verified live earlier this session — a real request to a local vision model
took 3.5s once and 76s another time, same query, same model, entirely
because of how much free RAM was available at that moment (240MB free,
5.6GB swapped, at the time of the slow run). That's numeric, factual
information a model isn't needed to detect or phrase — a plain templated
sentence is both more reliable and faster than asking a local model to
describe its own memory readings back to the user, and avoids the
hallucination risk documented in quantum_watcher.py for anything more
open-ended than "is this number over this threshold."

WHY a cooldown per condition, not one shared cooldown: memory pressure and
disk space are independent problems that can each become true at different
times — a single shared cooldown could suppress a genuinely new disk-space
warning just because a memory warning had already fired minutes earlier.
"""
import logging
import subprocess
import time
from typing import Callable, Optional

logger = logging.getLogger(__name__)

_LOW_FREE_MEM_MB = 500
_HIGH_SWAP_USED_MB = 2048
_LOW_DISK_FREE_PCT = 5  # percent free remaining counts as "nearly full"


class SystemHealthWatcher:
    """Polls basic system resource health and proactively reports degraded conditions."""

    def __init__(self, on_report: Callable[[str], bool], cooldown_s: float = 1800.0) -> None:
        self._on_report = on_report
        self._cooldown_s = cooldown_s
        self._last_reported_at: dict = {}

    def poll_once(self) -> None:
        self._maybe_report("memory", _memory_message(_memory_info()))
        self._maybe_report("disk", _disk_message(_disk_info()))

    def _maybe_report(self, key: str, message: Optional[str]) -> None:
        if message is None:
            return
        if time.monotonic() - self._last_reported_at.get(key, 0.0) < self._cooldown_s:
            return
        if self._on_report(message):
            self._last_reported_at[key] = time.monotonic()


def _memory_message(memory: dict) -> Optional[str]:
    if not memory:
        return None
    if memory.get("swap_used_mb", 0) >= _HIGH_SWAP_USED_MB:
        return (
            f"Heads up — your system is under heavy memory pressure right now, "
            f"{memory['mem_free_mb']}MB free with {memory['swap_used_mb']}MB swapped to disk. "
            f"I might respond slower than usual, especially anything using a local model."
        )
    if memory.get("mem_free_mb", 10_000) <= _LOW_FREE_MEM_MB:
        return f"Heads up — you're very low on free memory right now, only {memory['mem_free_mb']}MB free."
    return None


def _disk_message(disk: dict) -> Optional[str]:
    try:
        use_pct = int(disk.get("use_pct", "0%").rstrip("%"))
    except (ValueError, AttributeError):
        return None
    if use_pct >= (100 - _LOW_DISK_FREE_PCT):
        return f"Heads up — your disk is {use_pct}% full, only {disk.get('avail_mb')}MB left."
    return None


def _memory_info() -> dict:
    result = subprocess.run(["free", "-m"], capture_output=True, text=True, timeout=5)
    info: dict = {}
    for line in result.stdout.splitlines():
        if line.startswith("Mem:"):
            _, total, used, free, *_ = line.split()
            info["mem_total_mb"] = int(total)
            info["mem_used_mb"] = int(used)
            info["mem_free_mb"] = int(free)
        elif line.startswith("Swap:"):
            _, total, used, free = line.split()
            info["swap_total_mb"] = int(total)
            info["swap_used_mb"] = int(used)
    return info


def _disk_info(path: str = "/") -> dict:
    result = subprocess.run(["df", "-BM", path], capture_output=True, text=True, timeout=5)
    lines = result.stdout.splitlines()
    if len(lines) < 2:
        return {}
    parts = lines[1].split()
    return {"size_mb": parts[1].rstrip("M"), "used_mb": parts[2].rstrip("M"), "avail_mb": parts[3].rstrip("M"), "use_pct": parts[4]}


if __name__ == "__main__":
    calls: list[str] = []

    watcher = SystemHealthWatcher(on_report=lambda m: (calls.append(m), True)[1], cooldown_s=100)

    assert _memory_message({"mem_free_mb": 5000, "swap_used_mb": 0}) is None
    assert _memory_message({"mem_free_mb": 5000, "swap_used_mb": 3000}) is not None
    assert _memory_message({"mem_free_mb": 200, "swap_used_mb": 0}) is not None
    print("OK: memory message fires on high swap or low free RAM, not otherwise")

    assert _disk_message({"use_pct": "50%", "avail_mb": "200000"}) is None
    assert _disk_message({"use_pct": "97%", "avail_mb": "500"}) is not None
    print("OK: disk message fires only when nearly full")

    real_memory = _memory_info()
    real_disk = _disk_info()
    assert "mem_free_mb" in real_memory, real_memory
    assert "use_pct" in real_disk, real_disk
    print(f"OK: real system readings — memory={real_memory}, disk={real_disk}")

    watcher._maybe_report("memory", "test message one")
    watcher._maybe_report("memory", "test message two")
    assert calls == ["test message one"], "cooldown should suppress the second report for the same key"
    watcher._maybe_report("disk", "different key message")
    assert calls == ["test message one", "different key message"], "a different key must not share the cooldown"
    print("OK: cooldown is per-condition, not shared")

    print("SystemHealthWatcher self-check OK")
