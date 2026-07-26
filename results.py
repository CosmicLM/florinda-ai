from __future__ import annotations
from string import Template
from typing import TYPE_CHECKING

from executor import SystemTerminal
from voice import HyprYapHandling
from termcolor import colored
from config import NULL_COMMAND

if TYPE_CHECKING:
    from processor import HyprInstructionOrchestrator

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
