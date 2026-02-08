import subprocess
from config import voice_model

class HyprCore:
    def __init__(self):
        self.voice_model = voice_model
        self.piper_cmd = f"piper-tts --model {self.voice_model} --output_raw | aplay -r 22050 -f S16_LE -t raw"

    def speak(self, text):
        if text:
            subprocess.Popen(f'echo "{text}" | {self.piper_cmd}', shell=True)