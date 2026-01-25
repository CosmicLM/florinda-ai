import subprocess
import os

def execute_system_command(cmd):
    """
    WARNING: Full access mode. Executes any command passed by the AI.
    """
    try:
        # We use shell=True to allow piping and complex bash syntax
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        return {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "code": result.returncode
        }
    except Exception as e:
        return {"error": str(e)}

# This is where the AI's output is piped in a real integration
# For now, we will use this as our execution engine.