from config import NULL_COMMAND

class HyprInstructionOrchestrator:
    def __init__(self,ai_client):
        self.client = ai_client
            
    #Construe means to interpret, parse of define something, I believe it is appropriate to use this term instead of parse.
    def construe_response(self, raw_text):
    # Model format: "COMMAND: bash | SPEECH: text"
    # If there are no pipes the code will then treat entire response as speech with no command execution.
   
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
           
            actual_answer = self.client.models.generate_content(
                model="gemini-3.1-flash-lite",
                contents=user_input,
                config={
                    "system_instruction": "You are Hypr. Format: COMMAND: bash | SPEECH: [text]."
                }
            )       
            
            #Just in case API does not return actual input, I have included a sort of filter to turn the output (doesnt matter what it is)
            #To an actual string, Which i then pass it on to the guard clause.
            
            full_hypr_text = getattr(actual_answer, "text", "") or ""
             # Guard against empty API responses - fail gracefully.
            if not full_hypr_text.strip():
                 full_hypr_text = ""
                
            # Delegate parsing (or in this case, construe) to construe_response; it handles all cases including empty input
            construed_content = self.construe_response(full_hypr_text)
            return construed_content
        
        except Exception as e:
            return {
            "execute": NULL_COMMAND,
            "speak": f"Brain Error: {str(e)}"
            }
        
        # Return error dict with NULL_COMMAND so caller knows no execution should occur