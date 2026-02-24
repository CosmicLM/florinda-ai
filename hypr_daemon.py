import sys
from config import api_key, voice_model
from voice import HyprCore
from processor import PromptProcessor


if __name__ == "__main__":
    core = HyprCore(api_key, voice_model, PromptProcessor)
    user_input = " ".join(sys.argv[1:])
    if user_input.strip():
        print(core.processor(user_input))
        