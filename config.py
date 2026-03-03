import os
import sys
from dotenv import load_dotenv

load_dotenv()

# Identity & Neural Link
API_KEY = os.getenv("HYPR_API_KEY")
if not API_KEY:
    print("CRITICAL: HYPR_API_KEY not found.")
    sys.exit(1)
    
VOICE_MODEL = os.getenv("DEFAULT_VOICE_MODEL")

AI_MODEL = "gemini-3-flash-preview"

# Import system-wide constants to ensure consistent behavior across all Hypr modules.
NULL_COMMAND = "null"