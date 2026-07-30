"""figure_popup.py — pops an image up in its own kitty window immediately,
shared by qiskit_runner.py and latex_runner.py so this logic exists once.

WHY nothing here auto-closes: the circuit diagram used to close itself after
a fixed timer (10-100s) on the assumption it was just a quick reference the
user glances at once. Reported live: that's wrong — the user wants it to
stay up exactly as long as they're actually looking at it, same as a results
figure. A fixed timer either closes it too early (mid-read) or lingers
uselessly if they were faster than that — there's no timer that's correct
for every viewing. Every popup now behaves the same way: stays open until
the user closes the window themselves.
"""
import subprocess


def popup_image(path: str, title: str) -> None:
    """Shows `path` in its own kitty window immediately. Stays open (an
    interactive shell after icat) until the user closes it themselves."""
    inner = f"kitty +kitten icat {path!r} && exec sh"
    subprocess.Popen(
        ["kitty", "--class", "flora-figure", "--title", title, "-e", "sh", "-c", inner],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
