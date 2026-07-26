import os
import sys
import logging
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# --- Credentials ---
API_KEY = os.getenv("HYPR_API_KEY")
if not API_KEY:
    print("CRITICAL: HYPR_API_KEY not found.")
    sys.exit(1)

# --- AI ---
AI_MODEL = "gemini-3-flash-preview"

# --- Voice ---
VOICE_MODEL = os.getenv("DEFAULT_VOICE_MODEL")

# --- Constants ---
NULL_COMMAND = "null"

# --- Developer Options ---
DEBUG = os.getenv("HYPR_DEBUG", "false").lower() == "true"

# --- Logging ---
LOG_PATH = Path.home() / ".local/share/hypr-ai/hypr-ai.log"
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

_handlers = [logging.FileHandler(LOG_PATH)]
if DEBUG:
    _handlers.append(logging.StreamHandler())

logging.basicConfig(
    level=logging.DEBUG if DEBUG else logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=_handlers,
)