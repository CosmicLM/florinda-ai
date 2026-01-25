import os
import subprocess
import sys
from google import genai # Note the change in import
from dotenv import load_dotenv

# Identity & Neural Link

load_dotenv()

api_key = os.getenv("HYPR_API_KEY")
if not api_key:
    print("CRITICAL: HYPR_API_KEY not found.")
    sys.exit(1)

# New Client Initialization
client = genai.Client(api_key=api_key)

class HyprCore:
    def __init__(self):
        self.voice_model = os.path.expanduser("~/Projects/hypr-ai/voice/en_GB-cori-high.onnx")
        self.piper_cmd = f"piper-tts --model {self.voice_model} --output_raw | aplay -r 22050 -f S16_LE -t raw"

    def speak(self, text):
        if text:
            subprocess.Popen(f'echo "{text}" | {self.piper_cmd}', shell=True)

    def execute(self, cmd):
        if cmd and cmd.lower() != "null":
            print(f"HYPR EXEC: {cmd}")
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
            return result.stdout if result.returncode == 0 else result.stderr
        return "No action required."

    def process(self, prompt):
        try:
            # New SDK uses client.models.generate_content
            response = client.models.generate_content(
                model="gemini-3-flash-preview", # Updated to the latest 2026 model
                contents=prompt,
                config={
                    "system_instruction": "You are Hypr. Format: COMMAND: bash | SPEECH: [text]."
                }
            )
            
            # The text is now accessed through the .text attribute of the response object
            full_text = response.text
            
            if "|" in full_text:
                parts = full_text.split("|")
                cmd_part = parts[0].replace("COMMAND:", "").strip()
                speech_part = parts[1].replace("SPEECH:", "").strip()
                self.speak(speech_part)
                output = self.execute(cmd_part)
                return f"Hypr: {speech_part}\nSystem: {output}"
            
            return f"Hypr: {full_text}"
                
        except Exception as e:
            return f"Brain Error: {str(e)}"

if __name__ == "__main__":
    core = HyprCore()
    user_input = " ".join(sys.argv[1:])
    if user_input.strip():
        print(core.process(user_input))