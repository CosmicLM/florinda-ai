import sys
from google import genai
from config import API_KEY, VOICE_MODEL, DEBUG
from voice import HyprYapHandling
from processor import HandleHyprResult, HyprInstructionOrchestrator, HyprInstructionResult
from executor import SystemTerminal


if __name__ == "__main__":
    client = genai.Client(api_key=API_KEY)
    processor = HyprInstructionOrchestrator(client)
    core = HyprYapHandling(VOICE_MODEL)
    terminal = SystemTerminal()
    
    user_input = " ".join(sys.argv[1:])
    if user_input.strip():
        result = processor._hypr_orchestra_unit(user_input)
        HandleHyprResult(result, yap_model=core, terminal=terminal)
        
        if DEBUG:
            print(result)
        
        core.stream_vocal_synthesis(result["speak"])
