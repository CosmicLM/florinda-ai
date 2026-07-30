"""config.py — Env/.env manager. A pure library: never prints, never exits."""
import logging
import os
from pathlib import Path
from typing import Literal, Optional

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

NULL_COMMAND = "null"  # AI protocol's "no action" sentinel — not env-derived, always importable.


class ConfigurationError(Exception):
    """Raised when required Florinda environment configuration is missing or invalid.

    WHY: config.py must stay a pure library — only flora_daemon.py (the entrypoint)
    is allowed to print to the user and call sys.exit(). This lets any module
    import config.py without risking process termination as a side effect.
    """


class FloraSettings(BaseModel):
    """Structural contract for Florinda's environment-derived configuration."""
    model_config = ConfigDict(frozen=True)

    # --- Primary AI provider — pluggable so ANY provider's API key can be
    # attached, not just Gemini. "gemini" (default) preserves this project's
    # original behavior exactly; the other two are genuine alternatives, not
    # fallbacks — see openai_backend.py/anthropic_backend.py. ---
    ai_provider: Literal["gemini", "openai", "anthropic"] = Field(
        default="gemini", description="FLORA_AI_PROVIDER"
    )
    api_key: Optional[str] = Field(default=None, description="FLORA_API_KEY (Gemini)")
    voice_model: Optional[str] = Field(default=None, description="DEFAULT_VOICE_MODEL")
    ai_model: str = Field(default="gemini-3-flash-preview")
    ai_model_light: Optional[str] = Field(
        default=None,
        description="Cheaper/faster model for high-volume tasks (falls back to ai_model)",
    )

    # --- Generic OpenAI-compatible provider (openai_backend.py) — covers
    # OpenAI itself, Azure OpenAI, Mistral, Groq, OpenRouter, Together, or a
    # self-hosted vLLM/llama.cpp/Ollama OpenAI-compat endpoint. No default
    # model is guessed — every provider names its models differently, so
    # this must be set explicitly when ai_provider="openai". ---
    openai_api_key: Optional[str] = Field(default=None, description="FLORA_OPENAI_API_KEY")
    openai_base_url: str = Field(
        default="https://api.openai.com/v1", description="FLORA_OPENAI_BASE_URL"
    )
    openai_model: Optional[str] = Field(default=None, description="FLORA_OPENAI_MODEL")
    openai_model_light: Optional[str] = Field(
        default=None, description="FLORA_OPENAI_MODEL_LIGHT — falls back to openai_model"
    )

    # --- Native Anthropic API provider (anthropic_backend.py) — for direct
    # Anthropic API billing, distinct from claude_cli_backend.py's
    # subscription-based `claude` CLI route. ---
    anthropic_api_key: Optional[str] = Field(default=None, description="FLORA_ANTHROPIC_API_KEY")
    anthropic_model: str = Field(default="claude-opus-5", description="FLORA_ANTHROPIC_MODEL")
    anthropic_model_light: str = Field(
        default="claude-haiku-4-5", description="FLORA_ANTHROPIC_MODEL_LIGHT"
    )

    debug: bool = Field(default=False)
    log_path: Path = Field(default_factory=lambda: Path.home() / ".local/share/flora-ai/flora-ai.log")
    state_path: Path = Field(default_factory=lambda: Path.home() / ".local/share/flora-ai/state.json")

    # --- Always-on service: speech-to-text ---
    stt_model: str = Field(default="small.en", description="FLORA_STT_MODEL")
    stt_device: str = Field(default="cpu", description="FLORA_STT_DEVICE")

    # --- Always-on service: screen watching ---
    screen_watch_enabled: bool = Field(default=True, description="FLORA_SCREEN_WATCH_ENABLED")
    screen_watch_interval_s: float = Field(default=4.0, description="FLORA_SCREEN_WATCH_INTERVAL_S")
    sys_info_max_chars: int = Field(default=4000, description="FLORA_SYS_INFO_MAX_CHARS")

    # --- Always-on service: push-to-talk ---
    ptt_min_hold_ms: int = Field(default=350, description="FLORA_PTT_MIN_HOLD_MS")
    ptt_max_recording_s: float = Field(default=30.0, description="FLORA_PTT_MAX_RECORDING_S")
    ptt_socket_path: Path = Field(
        default_factory=lambda: Path.home() / ".local/share/flora-ai/ptt.sock"
    )
    pending_confirm_timeout_s: float = Field(default=60.0, description="FLORA_CONFIRM_TIMEOUT_S")
    mic_source: Optional[str] = Field(default=None, description="FLORA_MIC_SOURCE")

    # --- Always-on service: status broadcasting + audio cues ---
    ptt_listen_sound: Optional[str] = Field(
        default="/usr/share/sounds/Pop/stereo/notification/message.oga",
        description="FLORA_PTT_LISTEN_SOUND — empty/unset disables the cue",
    )
    ptt_talking_sound: Optional[str] = Field(
        default="/usr/share/sounds/Pop/stereo/notification/system-ready.oga",
        description="FLORA_PTT_TALKING_SOUND — plays when Florinda stops thinking and "
        "starts speaking its answer; empty/unset disables the cue",
    )
    status_path: Path = Field(default_factory=lambda: Path.home() / ".local/share/flora-ai/status.json")
    waybar_signal_num: int = Field(default=8, description="FLORA_WAYBAR_SIGNAL_NUM")

    # --- Always-on service: conversation memory ---
    memory_path: Path = Field(default_factory=lambda: Path.home() / ".local/share/flora-ai/conversation.json")
    memory_max_turns: int = Field(default=10, description="FLORA_MEMORY_MAX_TURNS")
    memory_reset_after_s: float = Field(default=1800.0, description="FLORA_MEMORY_RESET_AFTER_S")

    # --- Autonomous skills ---
    skills_dir: Path = Field(default_factory=lambda: Path("./skills"))

    # --- Local model (Ollama) for background jobs ---
    local_model: str = Field(default="phi4-mini", description="FLORA_LOCAL_MODEL")
    ollama_host: str = Field(default="http://localhost:11434", description="FLORA_OLLAMA_HOST")

    # --- Offline fallback: main assistant degrades to a local Ollama model
    # when Gemini can't be reached at all, instead of going silent ---
    offline_fallback_enabled: bool = Field(default=True, description="FLORA_OFFLINE_FALLBACK_ENABLED")
    offline_fallback_model: str = Field(default="llama3.1:8b", description="FLORA_OFFLINE_FALLBACK_MODEL")
    # WHY this needs to be explicit: observed live — google-genai's Client
    # defaults to timeout=None when no http_options are given, which httpx
    # treats as "wait forever," not "use a sane default." Under a real
    # "connected but no working internet" condition (packets vanish, no
    # clean/instant refusal) that leaves Florinda hanging indefinitely before it
    # ever gets the chance to detect failure and fall back to Ollama —
    # exactly the reported symptom ("it doesn't use the offline model"),
    # since it never actually got there. This bounds the worst case.
    gemini_timeout_s: float = Field(default=10.0, description="FLORA_GEMINI_TIMEOUT_S")

    # --- "Deep" tier reasoning via the user's own Claude subscription (claude_cli_backend.py) ---
    use_claude_cli_for_deep: bool = Field(default=True, description="FLORA_USE_CLAUDE_CLI_FOR_DEEP")
    claude_cli_timeout_s: float = Field(default=60.0, description="FLORA_CLAUDE_CLI_TIMEOUT_S")

    # --- Web search (SearXNG, flora-ai's own container — see docker-compose.yml) ---
    web_search_host: str = Field(default="http://127.0.0.1:8092", description="FLORA_WEB_SEARCH_HOST")

    # --- Quantum-keyword screen watcher ---
    quantum_watch_enabled: bool = Field(default=True, description="FLORA_QUANTUM_WATCH_ENABLED")
    quantum_watch_keywords: list[str] = Field(default_factory=lambda: [
        "quantum", "qubit", "qiskit", "superposition", "entanglement", "decoherence",
        "hadamard", "bloch sphere", "quantum circuit", "quantum gate", "unitary",
        "ansatz", "vqe", "qaoa", "hamiltonian", "pauli", "transpile", "statevector",
    ])
    quantum_watch_cooldown_s: float = Field(default=300.0, description="FLORA_QUANTUM_WATCH_COOLDOWN_S")

    # --- System health watcher (memory/disk pressure that would degrade Florinda's own responsiveness) ---
    system_health_cooldown_s: float = Field(default=1800.0, description="FLORA_SYSTEM_HEALTH_COOLDOWN_S")

    # --- Periodic "check up on what I'm working on" watcher ---
    check_in_enabled: bool = Field(default=True, description="FLORA_CHECK_IN_ENABLED")
    check_in_cooldown_s: float = Field(default=1800.0, description="FLORA_CHECK_IN_COOLDOWN_S")

    # --- Morning briefing: greeting + today's tasks + quantum news, once per day ---
    morning_briefing_enabled: bool = Field(default=True, description="FLORA_MORNING_BRIEFING_ENABLED")

    # --- Knowledge base (read-only reference) ---
    knowledge_base_path: Path = Field(
        default_factory=lambda: Path.home() / "Documents/Research/quantum-knowledge-base"
    )

    # --- Always-on service: human-readable activity transcript ---
    activity_log_path: Path = Field(
        default_factory=lambda: Path.home() / ".local/share/flora-ai/activity.log"
    )
    activity_log_max_lines: int = Field(default=2000, description="FLORA_ACTIVITY_LOG_MAX_LINES")

    @model_validator(mode="after")
    def _require_selected_provider_credentials(self) -> "FloraSettings":
        """WHY conditional rather than three independently-required fields:
        api_key being unconditionally required (min_length=1) was the whole
        blocker to attaching any other provider's key — a user running
        purely on OpenAI or Anthropic shouldn't need a Gemini key they'll
        never use just to satisfy validation. Only the credential for the
        ACTUALLY selected ai_provider is required; the other two providers'
        fields stay fully optional either way."""
        if self.ai_provider == "gemini" and not self.api_key:
            raise ValueError("FLORA_API_KEY is required when FLORA_AI_PROVIDER=gemini")
        if self.ai_provider == "openai" and not (self.openai_api_key and self.openai_model):
            raise ValueError(
                "FLORA_OPENAI_API_KEY and FLORA_OPENAI_MODEL are both required when FLORA_AI_PROVIDER=openai"
            )
        if self.ai_provider == "anthropic" and not self.anthropic_api_key:
            raise ValueError("FLORA_ANTHROPIC_API_KEY is required when FLORA_AI_PROVIDER=anthropic")
        return self


class ConfigVault:
    """Loads, validates, and exposes Florinda's runtime configuration."""

    def __init__(self, env_file: Optional[str] = None) -> None:
        load_dotenv(env_file)
        self.settings: FloraSettings = self._load_settings()
        self._prepare_logging()

    def _load_settings(self) -> FloraSettings:
        try:
            return FloraSettings(
                api_key=os.getenv("FLORA_API_KEY") or None,
                voice_model=os.getenv("DEFAULT_VOICE_MODEL"),
                ai_model_light=os.getenv("FLORA_AI_MODEL_LIGHT"),
                debug=os.getenv("FLORA_DEBUG", "false").lower() == "true",
                **self._service_overrides(),
            )
        except ValidationError as error:
            raise ConfigurationError(self._summarize(error)) from error

    @staticmethod
    def _service_overrides() -> dict:
        """Env overrides for the always-on service — omitted keys fall back to FloraSettings defaults."""
        overrides = {}
        if (v := os.getenv("FLORA_AI_PROVIDER")) is not None:
            overrides["ai_provider"] = v
        if (v := os.getenv("FLORA_OPENAI_API_KEY")) is not None:
            overrides["openai_api_key"] = v
        if (v := os.getenv("FLORA_OPENAI_BASE_URL")) is not None:
            overrides["openai_base_url"] = v
        if (v := os.getenv("FLORA_OPENAI_MODEL")) is not None:
            overrides["openai_model"] = v
        if (v := os.getenv("FLORA_OPENAI_MODEL_LIGHT")) is not None:
            overrides["openai_model_light"] = v
        if (v := os.getenv("FLORA_ANTHROPIC_API_KEY")) is not None:
            overrides["anthropic_api_key"] = v
        if (v := os.getenv("FLORA_ANTHROPIC_MODEL")) is not None:
            overrides["anthropic_model"] = v
        if (v := os.getenv("FLORA_ANTHROPIC_MODEL_LIGHT")) is not None:
            overrides["anthropic_model_light"] = v
        if (v := os.getenv("FLORA_STT_MODEL")) is not None:
            overrides["stt_model"] = v
        if (v := os.getenv("FLORA_STT_DEVICE")) is not None:
            overrides["stt_device"] = v
        if (v := os.getenv("FLORA_SCREEN_WATCH_ENABLED")) is not None:
            overrides["screen_watch_enabled"] = v.lower() == "true"
        if (v := os.getenv("FLORA_SCREEN_WATCH_INTERVAL_S")) is not None:
            overrides["screen_watch_interval_s"] = float(v)
        if (v := os.getenv("FLORA_SYS_INFO_MAX_CHARS")) is not None:
            overrides["sys_info_max_chars"] = int(v)
        if (v := os.getenv("FLORA_PTT_MIN_HOLD_MS")) is not None:
            overrides["ptt_min_hold_ms"] = int(v)
        if (v := os.getenv("FLORA_PTT_MAX_RECORDING_S")) is not None:
            overrides["ptt_max_recording_s"] = float(v)
        if (v := os.getenv("FLORA_CONFIRM_TIMEOUT_S")) is not None:
            overrides["pending_confirm_timeout_s"] = float(v)
        if (v := os.getenv("FLORA_MIC_SOURCE")) is not None:
            overrides["mic_source"] = v
        if (v := os.getenv("FLORA_PTT_LISTEN_SOUND")) is not None:
            overrides["ptt_listen_sound"] = v or None
        if (v := os.getenv("FLORA_PTT_TALKING_SOUND")) is not None:
            overrides["ptt_talking_sound"] = v or None
        if (v := os.getenv("FLORA_WAYBAR_SIGNAL_NUM")) is not None:
            overrides["waybar_signal_num"] = int(v)
        if (v := os.getenv("FLORA_MEMORY_MAX_TURNS")) is not None:
            overrides["memory_max_turns"] = int(v)
        if (v := os.getenv("FLORA_MEMORY_RESET_AFTER_S")) is not None:
            overrides["memory_reset_after_s"] = float(v)
        if (v := os.getenv("FLORA_LOCAL_MODEL")) is not None:
            overrides["local_model"] = v
        if (v := os.getenv("FLORA_OLLAMA_HOST")) is not None:
            overrides["ollama_host"] = v
        if (v := os.getenv("FLORA_OFFLINE_FALLBACK_ENABLED")) is not None:
            overrides["offline_fallback_enabled"] = v.lower() == "true"
        if (v := os.getenv("FLORA_OFFLINE_FALLBACK_MODEL")) is not None:
            overrides["offline_fallback_model"] = v
        if (v := os.getenv("FLORA_GEMINI_TIMEOUT_S")) is not None:
            overrides["gemini_timeout_s"] = float(v)
        if (v := os.getenv("FLORA_USE_CLAUDE_CLI_FOR_DEEP")) is not None:
            overrides["use_claude_cli_for_deep"] = v.lower() == "true"
        if (v := os.getenv("FLORA_CLAUDE_CLI_TIMEOUT_S")) is not None:
            overrides["claude_cli_timeout_s"] = float(v)
        if (v := os.getenv("FLORA_QUANTUM_WATCH_ENABLED")) is not None:
            overrides["quantum_watch_enabled"] = v.lower() == "true"
        if (v := os.getenv("FLORA_QUANTUM_WATCH_COOLDOWN_S")) is not None:
            overrides["quantum_watch_cooldown_s"] = float(v)
        if (v := os.getenv("FLORA_MORNING_BRIEFING_ENABLED")) is not None:
            overrides["morning_briefing_enabled"] = v.lower() == "true"
        if (v := os.getenv("FLORA_ACTIVITY_LOG_MAX_LINES")) is not None:
            overrides["activity_log_max_lines"] = int(v)
        if (v := os.getenv("FLORA_WEB_SEARCH_HOST")) is not None:
            overrides["web_search_host"] = v
        if (v := os.getenv("FLORA_SYSTEM_HEALTH_COOLDOWN_S")) is not None:
            overrides["system_health_cooldown_s"] = float(v)
        if (v := os.getenv("FLORA_CHECK_IN_ENABLED")) is not None:
            overrides["check_in_enabled"] = v.lower() == "true"
        if (v := os.getenv("FLORA_CHECK_IN_COOLDOWN_S")) is not None:
            overrides["check_in_cooldown_s"] = float(v)
        return overrides

    @staticmethod
    def _summarize(error: ValidationError) -> str:
        """WHY the message text is included, not just field names: a
        whole-model @model_validator (like _require_selected_provider_credentials
        above) has no specific field location (`loc` is empty) — without the
        message text, a provider-credential error summarized to a bare
        "Invalid Florinda configuration for: " with nothing after the colon,
        discarding the one piece of information that actually explains what's
        wrong."""
        parts = []
        for e in error.errors():
            field = ".".join(str(p) for p in e["loc"])
            parts.append(f"{field}: {e['msg']}" if field else e["msg"])
        return f"Invalid Florinda configuration — {'; '.join(parts)}"

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
