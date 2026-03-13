import sys
from config import API_KEY, VOICE_MODEL
from voice import HyprYapHandling
from processor import HyprInstructionOrchestrator


if __name__ == "__main__":
    processor = HyprInstructionOrchestrator("gemini-3.1-flash-lite")
    core = HyprYapHandling(API_KEY, VOICE_MODEL, processor)
    
    user_input = " ".join(sys.argv[1:])
    if user_input.strip():
        result = core.processor.process(user_input)
        
        print(result)
        
        core.speak(result)