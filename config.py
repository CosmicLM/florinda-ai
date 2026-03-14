import os
import sys
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