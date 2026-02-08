import subprocess

 #Standard output and standard error are the two main output streams for Hypr to emit text.
 
class CommandExecutor: 
 def execute(self, cmd):
        if cmd and cmd.lower() != "null":
            print(f"HYPR EXEC: {cmd}")
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
            return result.standardOutput if result.returncode == 0 else result.standardError #Change abbreviated versions of Standard code to actual name
        return "No action required."
