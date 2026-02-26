import subprocess
from config import voice_model #included the voice model on config because of accessibility to change voice model
from processor import PromptProcessor
class HyprCore:
    def __init__(self, api_key, voice_model, PromptProcessor):
        self.voice_model = voice_model
        self.processor = PromptProcessor 
        self.api_key = api_key
        self.piper_cmd = f"piper-tts --model {self.voice_model} --output_raw | aplay -r 22050 -f S16_LE -t raw"
        
        

    def speak(self, text):
        if text:
            subprocess.Popen(f'echo "{text}" | {self.piper_cmd}', shell=True)
            