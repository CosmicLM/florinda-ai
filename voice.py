import subprocess
from config import DEBUG
class HyprYapHandling:
    def __init__(self, a_voz_model,):
        self.voice_model = a_voz_model
        commandante_tts_command = f"piper-tts --model {self.voice_model} --output_raw"
        gaita_output_command = "aplay -r 22050 -f S16_LE -t raw"
        self.piper_cmd = f"{commandante_tts_command} | {gaita_output_command}"
        
        
#Standard engineering flow for in case the speech processes the text from the piper module
    def stream_vocal_synthesis(self, text):
        if not text or not text.strip():
            return
        stderr = None if DEBUG else subprocess.DEVNULL
        subprocess.Popen(f'echo "{text}" | {self.piper_cmd}', shell=True, stderr=stderr)
            