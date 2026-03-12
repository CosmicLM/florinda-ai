from google import genai
from config import api_key, NULL_COMMAND

class HyprInstructionOrchestrator:
    def __init__(self,ai_client):
        self.client = ai_client
        
    def _parse_response(self, raw_text):
    # Model format: "COMMAND: bash | SPEECH: text"
    # If no pipe, treat entire response as speech with no command execution
   
        if "|" not in raw_text:
            return {
                "execute": NULL_COMMAND,
                "speak": raw_text.strip()
            }
            
        # Split on pipe to extract command and speech components
        split_parts = raw_text.split("|", 1)
        
        system_shell_command = split_parts[0].replace("COMMAND:", "").strip()
        assistant_speech_text = split_parts[1].replace("SPEECH:", "").strip()
      
      
        return {
            
            "execute": system_shell_command,
            "speak": assistant_speech_text
        } 
                       
    #orchestra in galicia works as an orgnanized set of group, just like how hypr_orchestra_units organizes the response and full_text         
    def _hypr_orchestra_unit(self, user_input):
        try:
           
            response = self.client.models.generate_content(
                hypr_model="gemini-3.1-flash-lite",
                persona_contents=user_input,
                config={
                    "system_instruction": "You are Hypr. Format: COMMAND: bash | SPEECH: [text]."
                }
            )       
            
            full_text = response.text
        
             # Guard against empty API responses - fail gracefully.
            if not response.text.strip():
                 full_text = ""
                
            # Delegate parsing to _parse_response; it handles all cases including empty input
            parsed_text = self._parse_response(full_text)
            return parsed_text
                
        except Exception as e:
             # Return error dict with NULL_COMMAND so caller knows no execution should occur
            return {
                "execute": NULL_COMMAND,
                "speak": f"Brain Error: {str(e)}"
            }