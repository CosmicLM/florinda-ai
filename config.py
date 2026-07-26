"""config.py — Env/.env manager. A pure library: never prints, never exits."""
import logging
import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, ValidationError

NULL_COMMAND = "null"  # AI protocol's "no action" sentinel — not env-derived, always importable.


class ConfigurationError(Exception):
    """Raised when required Hypr environment configuration is missing or invalid.

    WHY: config.py must stay a pure library — only hypr_daemon.py (the entrypoint)
    is allowed to print to the user and call sys.exit(). This lets any module
    import config.py without risking process termination as a side effect.
    """


class HyprSettings(BaseModel):
    """Structural contract for Hypr's environment-derived configuration."""
    model_config = ConfigDict(frozen=True)

    api_key: str = Field(min_length=1, description="HYPR_API_KEY")
    voice_model: Optional[str] = Field(default=None, description="DEFAULT_VOICE_MODEL")
    ai_model: str = Field(default="gemini-3-flash-preview")
    ai_model_light: Optional[str] = Field(
        default=None,
        description="Cheaper/faster model for high-volume tasks (falls back to ai_model)",
    )
    debug: bool = Field(default=False)
    log_path: Path = Field(default_factory=lambda: Path.home() / ".local/share/hypr-ai/hypr-ai.log")
    state_path: Path = Field(default_factory=lambda: Path.home() / ".local/share/hypr-ai/state.json")

    # --- Always-on service: speech-to-text ---
    stt_model: str = Field(default="small.en", description="HYPR_STT_MODEL")
    stt_device: str = Field(default="cpu", description="HYPR_STT_DEVICE")

    # --- Always-on service: screen watching ---
    screen_watch_enabled: bool = Field(default=True, description="HYPR_SCREEN_WATCH_ENABLED")
    screen_watch_interval_s: float = Field(default=4.0, description="HYPR_SCREEN_WATCH_INTERVAL_S")
    sys_info_max_chars: int = Field(default=4000, description="HYPR_SYS_INFO_MAX_CHARS")

    # --- Always-on service: push-to-talk ---
    ptt_min_hold_ms: int = Field(default=350, description="HYPR_PTT_MIN_HOLD_MS")
    ptt_max_recording_s: float = Field(default=30.0, description="HYPR_PTT_MAX_RECORDING_S")
    ptt_socket_path: Path = Field(
        default_factory=lambda: Path.home() / ".local/share/hypr-ai/ptt.sock"
    )
    pending_confirm_timeout_s: float = Field(default=60.0, description="HYPR_CONFIRM_TIMEOUT_S")
    mic_source: Optional[str] = Field(default=None, description="HYPR_MIC_SOURCE")


class ConfigVault:
    """Loads, validates, and exposes Hypr's runtime configuration."""

    def __init__(self, env_file: Optional[str] = None) -> None:
        load_dotenv(env_file)
        self.settings: HyprSettings = self._load_settings()
        self._prepare_logging()

    def _load_settings(self) -> HyprSettings:
        try:
            return HyprSettings(
                api_key=os.getenv("HYPR_API_KEY") or "",
                voice_model=os.getenv("DEFAULT_VOICE_MODEL"),
                ai_model_light=os.getenv("HYPR_AI_MODEL_LIGHT"),
                debug=os.getenv("HYPR_DEBUG", "false").lower() == "true",
                **self._service_overrides(),
            )
        except ValidationError as error:
            raise ConfigurationError(self._summarize(error)) from error

    @staticmethod
    def _service_overrides() -> dict:
        """Env overrides for the always-on service — omitted keys fall back to HyprSettings defaults."""
        overrides = {}
        if (v := os.getenv("HYPR_STT_MODEL")) is not None:
            overrides["stt_model"] = v
        if (v := os.getenv("HYPR_STT_DEVICE")) is not None:
            overrides["stt_device"] = v
        if (v := os.getenv("HYPR_SCREEN_WATCH_ENABLED")) is not None:
            overrides["screen_watch_enabled"] = v.lower() == "true"
        if (v := os.getenv("HYPR_SCREEN_WATCH_INTERVAL_S")) is not None:
            overrides["screen_watch_interval_s"] = float(v)
        if (v := os.getenv("HYPR_SYS_INFO_MAX_CHARS")) is not None:
            overrides["sys_info_max_chars"] = int(v)
        if (v := os.getenv("HYPR_PTT_MIN_HOLD_MS")) is not None:
            overrides["ptt_min_hold_ms"] = int(v)
        if (v := os.getenv("HYPR_PTT_MAX_RECORDING_S")) is not None:
            overrides["ptt_max_recording_s"] = float(v)
        if (v := os.getenv("HYPR_CONFIRM_TIMEOUT_S")) is not None:
            overrides["pending_confirm_timeout_s"] = float(v)
        if (v := os.getenv("HYPR_MIC_SOURCE")) is not None:
            overrides["mic_source"] = v
        return overrides

    @staticmethod
    def _summarize(error: ValidationError) -> str:
        fields = [".".join(str(p) for p in e["loc"]) for e in error.errors()]
        return f"Invalid Hypr configuration for: {', '.join(fields)}"

    def _prepare_logging(self) -> None:
        self.settings.log_path.parent.mkdir(parents=True, exist_ok=True)
        handlers = [logging.FileHandler(self.settings.log_path)]
        if self.settings.debug:
            handlers.append(logging.StreamHandler())
        logging.basicConfig(
            level=logging.DEBUG if self.settings.debug else logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
            handlers=handlers,
        )
