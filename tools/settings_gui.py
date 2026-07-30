#!/usr/bin/env python3
"""settings_gui.py — a GTK settings window for Florinda: AI provider + API
keys, feature toggles (morning briefing and friends), and the file paths
a user might actually want to point somewhere else (voice model,
knowledge-base vault). Edits .env directly and offers to restart
flora-daemon.service so changes take effect immediately.

WHY GTK3/PyGObject instead of pulling in a new GUI toolkit: dbus-python +
PyGObject + cairo are already hard requirements project-wide
(screencast_portal.py's WM-agnostic screen-capture fallback needs them on
every install, verified live during the earlier build-toolchain fixes) —
reusing them here costs nothing extra, rather than adding Tkinter/Qt/etc.
as a second toolkit just for this one window.

WHY this edits .env directly instead of going through config.py: config.py
is deliberately read-only (see its own docstring — "a pure library: never
prints, never exits") and has no write path at all; this needs one. .env
is parsed into an ordered list of raw lines rather than a plain dict, so
saving preserves any FLORA_* variable this window doesn't have a control
for (advanced tuning knobs from SYSTEM_REQUIREMENTS.md, for instance)
instead of silently dropping it.

WHY defaults come from FloraSettings.model_fields, not from constructing a
FloraSettings instance: constructing one can raise ConfigurationError if
no provider credential is set yet at all (a brand-new .env, or one being
edited before any key has been entered) — reading the class-level field
defaults needs no valid instance and works identically either way.

Launch: `python3 tools/settings_gui.py` (append `&` when invoking as an AI
COMMAND — this blocks on its own event loop until the window is closed,
same reasoning as any other detached GUI launch in this project).
"""
import os
import re
import subprocess
import sys
from pathlib import Path

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import FloraSettings  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = REPO_ROOT / ".env"

_KEY_RE = re.compile(r'^([A-Za-z_][A-Za-z0-9_]*)=')


def _default(field_name: str) -> str:
    # WHY get_default(call_default_factory=True), not plain .default:
    # verified live — fields declared with default_factory= (like
    # knowledge_base_path) have .default equal to PydanticUndefined, not
    # the actual computed value; get_default() is pydantic v2's own API for
    # resolving either kind of field uniformly.
    value = FloraSettings.model_fields[field_name].get_default(call_default_factory=True)
    return "" if value is None else str(value)


def load_env(path: Path) -> tuple[dict, list]:
    """Returns (values, raw_lines) — values is KEY -> unquoted string for
    every FLORA_*/DEFAULT_* line present; raw_lines is the file's original
    lines, kept around so save_env() can preserve formatting/ordering for
    keys it doesn't touch."""
    values: dict = {}
    raw_lines: list = []
    if path.exists():
        raw_lines = path.read_text().splitlines()
    for line in raw_lines:
        m = _KEY_RE.match(line)
        if m:
            values[m.group(1)] = line.split("=", 1)[1].strip().strip('"')
    return values, raw_lines


def save_env(path: Path, values: dict, raw_lines: list) -> None:
    seen = set()
    new_lines = []
    for line in raw_lines:
        m = _KEY_RE.match(line)
        if m and m.group(1) in values:
            key = m.group(1)
            new_lines.append(f'{key}="{values[key]}"')
            seen.add(key)
        else:
            new_lines.append(line)
    for key, value in values.items():
        if key not in seen and value:
            new_lines.append(f'{key}="{value}"')
    path.write_text("\n".join(new_lines) + "\n")
    os.chmod(path, 0o600)


def _service_active() -> bool:
    result = subprocess.run(
        ["systemctl", "--user", "is-active", "--quiet", "flora-daemon.service"]
    )
    return result.returncode == 0


class SettingsWindow(Gtk.Window):
    def __init__(self) -> None:
        super().__init__(title="Florinda Settings")
        self.set_default_size(480, 420)
        self.set_border_width(12)
        self.connect("destroy", Gtk.main_quit)

        self._values, self._raw_lines = load_env(ENV_PATH)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.add(outer)

        notebook = Gtk.Notebook()
        outer.pack_start(notebook, True, True, 0)
        notebook.append_page(self._build_provider_tab(), Gtk.Label(label="AI Provider"))
        notebook.append_page(self._build_features_tab(), Gtk.Label(label="Features"))
        notebook.append_page(self._build_paths_tab(), Gtk.Label(label="File Paths"))

        self._status_label = Gtk.Label(label="")
        outer.pack_start(self._status_label, False, False, 0)

        button_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        button_row.set_halign(Gtk.Align.END)
        outer.pack_start(button_row, False, False, 0)
        close_button = Gtk.Button(label="Close")
        close_button.connect("clicked", lambda *_: self.destroy())
        button_row.pack_start(close_button, False, False, 0)
        save_button = Gtk.Button(label="Save")
        save_button.get_style_context().add_class("suggested-action")
        save_button.connect("clicked", self._on_save)
        button_row.pack_start(save_button, False, False, 0)

    # --- AI Provider tab ---

    def _build_provider_tab(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_border_width(10)

        current_provider = self._values.get("FLORA_AI_PROVIDER", "gemini") or "gemini"
        provider_row, self._provider_combo = _make_combo_row(
            "Primary provider:", ["gemini", "openai", "anthropic"], current_provider
        )
        box.pack_start(provider_row, False, False, 0)

        stack = Gtk.Stack()
        self._provider_combo.connect("changed", lambda combo: stack.set_visible_child_name(
            _selected(combo)
        ))

        gemini_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        gemini_row, self._gemini_key_entry = _make_secret_row(
            "Gemini API key:", self._values.get("FLORA_API_KEY", "")
        )
        gemini_box.pack_start(gemini_row, False, False, 0)
        stack.add_named(gemini_box, "gemini")

        openai_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        openai_key_row, self._openai_key_entry = _make_secret_row(
            "API key:", self._values.get("FLORA_OPENAI_API_KEY", "")
        )
        openai_box.pack_start(openai_key_row, False, False, 0)
        openai_url_row, self._openai_url_entry = _make_entry_row(
            "Base URL:", self._values.get("FLORA_OPENAI_BASE_URL", _default("openai_base_url"))
        )
        openai_box.pack_start(openai_url_row, False, False, 0)
        openai_model_row, self._openai_model_entry = _make_entry_row(
            "Model:", self._values.get("FLORA_OPENAI_MODEL", "")
        )
        openai_box.pack_start(openai_model_row, False, False, 0)
        openai_light_row, self._openai_light_entry = _make_entry_row(
            "Model (light, optional):", self._values.get("FLORA_OPENAI_MODEL_LIGHT", "")
        )
        openai_box.pack_start(openai_light_row, False, False, 0)
        stack.add_named(openai_box, "openai")

        anthropic_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        anthropic_key_row, self._anthropic_key_entry = _make_secret_row(
            "Anthropic API key:", self._values.get("FLORA_ANTHROPIC_API_KEY", "")
        )
        anthropic_box.pack_start(anthropic_key_row, False, False, 0)
        anthropic_model_row, self._anthropic_model_entry = _make_entry_row(
            "Model:", self._values.get("FLORA_ANTHROPIC_MODEL", _default("anthropic_model"))
        )
        anthropic_box.pack_start(anthropic_model_row, False, False, 0)
        anthropic_light_row, self._anthropic_light_entry = _make_entry_row(
            "Model (light):",
            self._values.get("FLORA_ANTHROPIC_MODEL_LIGHT", _default("anthropic_model_light")),
        )
        anthropic_box.pack_start(anthropic_light_row, False, False, 0)
        stack.add_named(anthropic_box, "anthropic")

        stack.set_visible_child_name(current_provider)
        box.pack_start(stack, False, False, 0)

        box.pack_start(Gtk.Separator(), False, False, 4)
        claude_cli_default = self._values.get(
            "FLORA_USE_CLAUDE_CLI_FOR_DEEP", _default("use_claude_cli_for_deep")
        )
        self._claude_cli_check = Gtk.CheckButton(
            label="Route 'deep' reasoning through a Claude Code CLI subscription (needs `claude` installed and logged in)"
        )
        self._claude_cli_check.set_active(str(claude_cli_default).lower() == "true")
        _wrap_check_label(self._claude_cli_check)
        box.pack_start(self._claude_cli_check, False, False, 0)

        return box

    # --- Features tab ---

    def _build_features_tab(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_border_width(10)

        self._feature_checks = {}
        features = [
            ("FLORA_MORNING_BRIEFING_ENABLED", "morning_briefing_enabled",
             "Morning briefing (greeting + today's tasks + quantum news, once per day)"),
            ("FLORA_SCREEN_WATCH_ENABLED", "screen_watch_enabled",
             "Screen watching (screenshot + OCR pipeline)"),
            ("FLORA_QUANTUM_WATCH_ENABLED", "quantum_watch_enabled",
             "Quantum-keyword screen watcher"),
            ("FLORA_CHECK_IN_ENABLED", "check_in_enabled",
             "Periodic \"what are you working on\" check-in"),
            ("FLORA_OFFLINE_FALLBACK_ENABLED", "offline_fallback_enabled",
             "Fall back to a local Ollama model when the primary AI provider is unreachable"),
        ]
        for env_key, field_name, label in features:
            current = self._values.get(env_key, _default(field_name))
            check = Gtk.CheckButton(label=label)
            check.set_active(str(current).lower() == "true")
            _wrap_check_label(check)
            box.pack_start(check, False, False, 0)
            self._feature_checks[env_key] = check

        return box

    # --- File Paths tab ---

    def _build_paths_tab(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_border_width(10)

        box.pack_start(Gtk.Label(label="Voice model (.onnx):", xalign=0), False, False, 0)
        self._voice_chooser = Gtk.FileChooserButton(
            title="Choose a Piper voice model", action=Gtk.FileChooserAction.OPEN
        )
        onnx_filter = Gtk.FileFilter()
        onnx_filter.set_name("Piper voice models (*.onnx)")
        onnx_filter.add_pattern("*.onnx")
        self._voice_chooser.add_filter(onnx_filter)
        current_voice = self._values.get("DEFAULT_VOICE_MODEL", "")
        if current_voice and Path(current_voice).exists():
            self._voice_chooser.set_filename(current_voice)
        box.pack_start(self._voice_chooser, False, False, 0)

        box.pack_start(Gtk.Label(label="Knowledge-base vault folder:", xalign=0), False, False, 8)
        self._kb_chooser = Gtk.FileChooserButton(
            title="Choose the knowledge-base vault folder",
            action=Gtk.FileChooserAction.SELECT_FOLDER,
        )
        current_kb = self._values.get("FLORA_KNOWLEDGE_BASE_PATH", _default("knowledge_base_path"))
        if current_kb and Path(current_kb).exists():
            self._kb_chooser.set_filename(current_kb)
        box.pack_start(self._kb_chooser, False, False, 0)

        return box

    # --- Save ---

    def _on_save(self, *_args) -> None:
        provider = _selected(self._provider_combo)
        self._values["FLORA_AI_PROVIDER"] = provider
        self._values["FLORA_API_KEY"] = self._gemini_key_entry.get_text()
        self._values["FLORA_OPENAI_API_KEY"] = self._openai_key_entry.get_text()
        self._values["FLORA_OPENAI_BASE_URL"] = self._openai_url_entry.get_text()
        self._values["FLORA_OPENAI_MODEL"] = self._openai_model_entry.get_text()
        self._values["FLORA_OPENAI_MODEL_LIGHT"] = self._openai_light_entry.get_text()
        self._values["FLORA_ANTHROPIC_API_KEY"] = self._anthropic_key_entry.get_text()
        self._values["FLORA_ANTHROPIC_MODEL"] = self._anthropic_model_entry.get_text()
        self._values["FLORA_ANTHROPIC_MODEL_LIGHT"] = self._anthropic_light_entry.get_text()
        self._values["FLORA_USE_CLAUDE_CLI_FOR_DEEP"] = (
            "true" if self._claude_cli_check.get_active() else "false"
        )
        for env_key, check in self._feature_checks.items():
            self._values[env_key] = "true" if check.get_active() else "false"
        voice_path = self._voice_chooser.get_filename()
        if voice_path:
            self._values["DEFAULT_VOICE_MODEL"] = voice_path
        kb_path = self._kb_chooser.get_filename()
        if kb_path:
            self._values["FLORA_KNOWLEDGE_BASE_PATH"] = kb_path

        try:
            save_env(ENV_PATH, self._values, self._raw_lines)
        except OSError as error:
            self._status_label.set_text(f"Failed to save: {error}")
            return

        if _service_active():
            self._prompt_restart()
        else:
            self._status_label.set_text(f"Saved {ENV_PATH}.")

    def _prompt_restart(self) -> None:
        dialog = Gtk.MessageDialog(
            transient_for=self,
            modal=True,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.YES_NO,
            text="Saved. Restart flora-daemon.service now for these changes to take effect?",
        )
        response = dialog.run()
        dialog.destroy()
        if response == Gtk.ResponseType.YES:
            result = subprocess.run(
                ["systemctl", "--user", "restart", "flora-daemon.service"],
                capture_output=True, text=True,
            )
            if result.returncode == 0:
                self._status_label.set_text("Saved and restarted flora-daemon.service.")
            else:
                self._status_label.set_text(f"Saved, but restart failed: {result.stderr.strip()}")
        else:
            self._status_label.set_text(f"Saved {ENV_PATH} — restart flora-daemon.service manually to apply.")


def _wrap_check_label(check: Gtk.CheckButton) -> None:
    """Gtk.CheckButton has no set_line_wrap of its own (verified live —
    AttributeError, unlike Gtk.Label) — the label is an internal child
    widget, so wrapping has to go through that instead."""
    child = check.get_child()
    if isinstance(child, Gtk.Label):
        child.set_line_wrap(True)


def _make_entry_row(label: str, initial: str) -> tuple[Gtk.Widget, Gtk.Entry]:
    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    row.pack_start(Gtk.Label(label=label, xalign=0), False, False, 0)
    entry = Gtk.Entry()
    entry.set_text(initial)
    entry.set_hexpand(True)
    row.pack_start(entry, True, True, 0)
    return row, entry


def _make_secret_row(label: str, initial: str) -> tuple[Gtk.Widget, Gtk.Entry]:
    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    row.pack_start(Gtk.Label(label=label, xalign=0), False, False, 0)
    entry = Gtk.Entry()
    entry.set_text(initial)
    entry.set_visibility(False)
    entry.set_hexpand(True)
    row.pack_start(entry, True, True, 0)
    toggle = Gtk.ToggleButton(label="Show")

    def _on_toggle(button: Gtk.ToggleButton) -> None:
        entry.set_visibility(button.get_active())
        button.set_label("Hide" if button.get_active() else "Show")

    toggle.connect("toggled", _on_toggle)
    row.pack_start(toggle, False, False, 0)
    return row, entry


def _make_combo_row(label: str, options: list, current: str) -> tuple[Gtk.Widget, Gtk.ComboBoxText]:
    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    row.pack_start(Gtk.Label(label=label, xalign=0), False, False, 0)
    combo = Gtk.ComboBoxText()
    for option in options:
        combo.append(option, option)
    combo.set_active_id(current if current in options else options[0])
    row.pack_start(combo, True, True, 0)
    return row, combo


def _selected(combo: Gtk.ComboBoxText) -> str:
    return combo.get_active_id() or "gemini"


def main() -> None:
    GLib.set_prgname("flora-settings")
    window = SettingsWindow()
    window.show_all()
    Gtk.main()


if __name__ == "__main__":
    main()
