import sys
from config import api_key, voice_model
from voice import HyprCore
from processor import PromptProcessor


if __name__ == "__main__":
    processor = PromptProcessor("init")
    core = HyprCore(api_key, voice_model, processor)
    
    user_input = " ".join(sys.argv[1:])
    if user_input.strip():
        result = core.processor.process(user_input)
        
        print(result)
        
        core.speak(result)
        