from config import NULL_COMMAND, AI_MODEL
from executor import SystemTerminal
from voice import HyprYapHandling
from string import Template
from termcolor import colored

'''
This is a class for the HyprInstructor to return
'''
class HyprInstructionResult:
    def __init__(self, speech, recursive = None, info = None, command = NULL_COMMAND) -> None:
        self.command = command if command != "" else NULL_COMMAND
        self.speech = speech
        self.recursive = recursive == "Y"
        self.info = info
'''
This orchestrator class is crucial for the functionality of Hypr-AI, it is purposefully included to help the AI parse the text
to then perform a command. Without this, the fundamental aspect of Hypr would be obsolete, control the system as it was intended.
 
'''

class HyprInstructionOrchestrator:
    def __init__(self, ai_client ,prompt_path = None):
        self.client = ai_client
        self.EOC = "<END>" # End-Of-Command
        self.prompt_path = prompt_path or "./INSTRUCTION.md"

    def _load_prompt(self):
        with open(self.prompt_path, "r") as f:
            template = Template(f.read())
            return template.safe_substitute(EOC = self.EOC, SYS_INFO = "") # inside the instruction, theres an {EOC} which will be replaced with the current class end of file
        #TODO: support sysinfo in the prompt to simplify

    def construe_response(self, raw_text):   
        if self.EOC not in raw_text:
            return HyprInstructionResult(speech=raw_text.strip())
        # Split on pipe to take command and speech parts
        split_parts = raw_text.split(self.EOC, 3)
        
        system_shell_command = split_parts[0].replace("COMMAND:", "").strip()
        assistant_speech_text = split_parts[1].replace("SPEECH:", "").strip()
        recursive_action = split_parts[2].replace("RECURSIVE:", "").strip()
        recursive_info = split_parts[3].replace("INFO:", "").strip()
        return HyprInstructionResult(speech=assistant_speech_text, command=system_shell_command, recursive=recursive_action, info=recursive_info)
        
    def _hypr_orchestra_unit(self, user_input):
        try:
            sys_prompt = self._load_prompt()
            actual_answer = self.client.models.generate_content(
                model=AI_MODEL,
                contents=user_input,
                config={"system_instruction": sys_prompt}
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
            print(e.__str__())
            return HyprInstructionResult(speech="An Error Had Uccured. pls help.")
        #Return error dict with NULL_COMMAND <-- opipoy here... i dont see why you need to do it like that :/ (still kept the NULL_COMMAND, just moved it to class ^^^)
        # Alternative solution:
        # make a log class, that will capture and log errors etc.
        
'''
This class is made to handle the result from HyprInstructor
'''
class HandleHyprResult:

    def __init__(self, result:HyprInstructionResult, yap_model:HyprYapHandling, terminal:SystemTerminal, orcastra:HyprInstructionOrchestrator, session_path:str|None = None) -> None:
        self.session_path = session_path or "./SESSION.md"
        self.result = result
        self.yap_model = yap_model
        self.terminal = terminal
        self.orcastra = orcastra
        self.execute_all_commands = False


    def _execute_command(self, command:str):

        if self.execute_all_commands:
            answer = "Y"
        else:
            answer = input(f"The AI Asks Permission To Run Command:\n{colored(command, 'red')}\nProcced? [All/Y/n] ")

        if answer == "All":
            self.execute_all_commands = True
        if answer == "Y" or self.execute_all_commands:
            print(colored("Excecuting The Following Command:\n" + colored(command, 'dark_grey')))
            return self.terminal.run_command(command)
        else:
            return "User Decided Not To Excecute Command"


    def _handle_result(self, result:HyprInstructionResult):
        output = None

        self._speak(result.speech)
        if result.command != NULL_COMMAND:
            output = self._execute_command(result.command)

        if result.recursive:
            with open(self.session_path, "r") as f:
                session_prompt = Template(f.read())
            session_prompt = session_prompt.safe_substitute(INFO = result.info, COMMAND=result.command, OUTPUT=output or "No Command Sent")
            self._handle_result(self.orcastra._hypr_orchestra_unit(session_prompt))



    def handle_result(self):
        self._handle_result(self.result)


    def _speak(self, speech:str):
        print(speech)
        self.yap_model.stream_vocal_synthesis(speech)
