import subprocess
import os 
import sys

class HyprDaemon:
    def __init__(self):
        
        self.pip_cmd = "piper --model ./voice/en_GB-cori-high.onnx --output_raw | aplay -r 22050 -f S16_LE -t raw"
        
    def speak(self, text):
        """Pipes text directorly to speakers. """
        print(f'echo "HYPR:{text}" | {self.piper_cmd}', shell=True)
    
    def execute_unrestricted(self, command):
        """
        FULL ACCESS PROTOCOL: Executes any bash command.
        Hypothesis: Root-level commands will require sudoers config.
        """
        try:
            result = subprocess.run(command, shell=True, capture_output=True, text=True)
            if result.returncode == 0:
                return f"Success: {result.stdout}"
            else:
                return f"Error: {result.stderr}"
        except Exception as e:
            return f"System Failure: {str(e)}"
        
        results = [self.execute_unrestricted(c) for c in cmds]
        return results
    
hypr = HyprDaemon()
    