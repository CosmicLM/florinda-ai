import subprocess
from config import NULL_COMMAND
 #Standard output and standard error are the two main output streams for Hypr to emit text.
 
class SystemTerminal: 
 def run_command(self, cmd):
     
     #Reject empty or placehold 'null' strings to prevent shell error
     if not cmd or cmd.lower() == NULL_COMMAND:
         raise ValueError("Command cannot be empty or have a 'null' placeholder.")
     result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
     
     #distinguish between successfull output and error messaged based on the process exit code
     return result.stdout if result.returncode == 0 else result.stderr
 