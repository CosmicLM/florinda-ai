from config import NULL_COMMAND, AI_MODEL

'''
This orchestrator class is crucial for the functionality of Hypr-AI, it is purposefully included to help the AI parse the text
to then perform a command. Without this, the fundamental aspect of Hypr would be obsolete, control the system as it was intended.
 
'''

class HyprInstructionOrchestrator:
    def __init__(self,ai_client):
        self.client = ai_client
            
    def construe_response(self, raw_text):   
        if "|" not in raw_text:
            return {
                "execute": NULL_COMMAND,
                "speak": raw_text.strip()
            }
            
        # Split on pipe to take command and speech parts
        split_parts = raw_text.split("|", 1)
        
        system_shell_command = split_parts[0].replace("COMMAND:", "").strip()
        assistant_speech_text = split_parts[1].replace("SPEECH:", "").strip()
        return {      
            "execute": system_shell_command,
            "speak": assistant_speech_text
        } 
        
    def _hypr_orchestra_unit(self, user_input):
        try:
           
            actual_answer = self.client.models.generate_content(
                model=AI_MODEL,
                contents=user_input,
                config={
                    "system_instruction": "You are Hypr. Format: COMMAND: bash | SPEECH: [text]."
                }
            )       
            
            #Just in case API does not return actual input
            full_hypr_text = getattr(actual_answer, "text", "") or ""
            
             #Set a guard clause against empty API responses
            if not full_hypr_text.strip():
                 full_hypr_text = ""
                
            #Delegate parsing to handle all cases including empty input
            construed_content = self.construe_response(full_hypr_text)
            return construed_content
        
        except Exception as e:
            return {
            "execute": NULL_COMMAND,
            "speak": f"Brain Error: {str(e)}"
            }
        
        #Return error dict with NULL_COMMAND
        
        