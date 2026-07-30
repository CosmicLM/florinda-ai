"""state_manifest.py — persists Florinda's context across CLI invocations.

WHY: the spec calls for state.json to live on an encrypted USB "Ark" partition.
No such drive is attached to this machine yet, so this stores to a local path
instead; swapping `state_path` to a mounted Ark partition later is a one-line
config change (see config.FloraSettings.state_path), not a rewrite of this class.
"""
import json
from pathlib import Path
from typing import Any


class StateManifest:
    """Loads and saves Florinda's persisted state as a flat JSON dict."""

    def __init__(self, state_path: Path) -> None:
        self._state_path = state_path

    def load(self) -> dict[str, Any]:
        """Return the persisted state, or an empty dict if none exists yet."""
        if not self._state_path.exists():
            return {}
        with open(self._state_path, "r") as state_file:
            return json.load(state_file)

    def save(self, data: dict[str, Any]) -> None:
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._state_path, "w") as state_file:
            json.dump(data, state_file, indent=2)


if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory() as tmp_dir:
        manifest = StateManifest(Path(tmp_dir) / "state.json")
        assert manifest.load() == {}
        manifest.save({"last_goal": "verify StateManifest round-trips"})
        assert manifest.load() == {"last_goal": "verify StateManifest round-trips"}
        print("StateManifest self-check OK")
