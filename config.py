import os
import sys
from dotenv import load_dotenv

load_dotenv()

# Identity & Neural Link
api_key = os.getenv("HYPR_API_KEY")
if not api_key:
    print("CRITICAL: HYPR_API_KEY not found.")
    sys.exit(1)
    
voice_model = os.getenv("DEFAULT_VOICE_MODEL")

# Import system-wide constants to ensure consistent behavior across all Hypr modules.
NULL_COMMAND = "null"