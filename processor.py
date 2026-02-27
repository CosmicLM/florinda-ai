from google import genai
from config import api_key

class PromptProcessor:
    def __init__(self,user_input):
        self.client = genai.Client(api_key=api_key)
    
    def process(self, user_input):
        try:
            # New SDK uses client.models.generate_content
            response = self.client.models.generate_content(
                model="gemini-3-flash-preview",
                contents=user_input,
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
        
    def speak(self, text):
        print(f"TTS: {text}")
    
    def execute(self, cmd):
        
        return f"Executing {cmd}..."