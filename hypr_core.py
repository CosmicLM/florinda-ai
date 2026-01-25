import subprocess
import os


class HyprCore:
    def __init__(self):
        self.allowed_apps = {
            "spotify": "spotify",
            "browser": "io.gitlab.librewolf-community",
            "temrinal": "kitty",
            "git": "git",
            "hyprland": "hyprctl"
        }
        
    def execute(self, action, params=""):
        if action in self.allowed_apps:
            cmd = f"{self.allowed_apps[action]} {params}"
            print(f"HYPR: Executiing {cmd}...")
            subprocess.Popen(cmd, shell=True)
            return "Action executed."
        else:
            return "Action unauthorized or unknown."
        
core = HyprCore()