"""circuit_vision.py — reads a quantum circuit diagram directly off a real
screenshot using a vision-capable model, and transcribes it into Qiskit
source code.

WHY this needs real image understanding, not screen_observer.py's existing
OCR pipeline: Tesseract reads TEXT off the screen — a circuit diagram is
lines, boxes, and control dots, which OCR either ignores or turns into
unrelated fragments of nearby labels. There is no text representation of
"a filled dot on the q0 wire connected down to an X box on q2" for OCR to
even attempt to extract. Only a vision model looking at the actual pixels
can transcribe that.

WHY the prompt below insists on transcribing ONLY what's drawn, wire by
wire, rather than asking "what circuit is this": a vision model asked to
identify a diagram will happily pattern-match it to the nearest well-known
named circuit (e.g. "this looks like teleportation") and then recite the
textbook version of THAT from training data — which silently drops or
reorders whatever is actually different about the one on screen (extra
qubits, a different gate order, a variant construction). The whole point of
this tool is to reproduce the exact diagram in front of the user, not the
canonical one it resembles.

Two backends:
  --backend cloud (default) — routes the screenshot through whichever cloud
  provider is already configured as FLORA_AI_PROVIDER (Gemini/Anthropic/
  OpenAI). Stronger at precise multi-qubit layouts than a small local model,
  subject to that provider's normal cost/quota.

  --backend local — routes through the same local Ollama vision model
  screen_control.py already uses for GUI grounding (qwen2.5vl:3b via
  Ollama — `ollama pull qwen2.5vl:3b` if not already pulled), no quota or
  API cost. Untested on circuit-diagram transcription specifically (unlike
  screen_control.py's GUI-grounding use, which was benchmarked live) — the
  two backends are meant to be compared directly, not assumed equivalent.

Output is bare Qiskit source only — no markdown fences, no commentary —
meant to be piped straight into qiskit_runner.py's `run`, the same way any
other Qiskit code is executed in this project:

    python3 tools/circuit_vision.py read | python3 tools/qiskit_runner.py run
"""
import base64
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Literal

_VISION_MODEL = "qwen2.5vl:3b"  # same model + rationale as skills/screen_control.py
# WHY 120s, well above screen_control.py's 45s cap: that cap exists only
# because skill_manager.py's run_skill wraps every skill in a hardcoded 60s
# subprocess timeout — this tool is invoked directly (not through
# skill_manager), so no such outer ceiling applies. Verified live on this
# machine (chronically low on free RAM — often under 300MB free with
# several GB swapped, per screen_control.py's own notes): a whole-screen
# screenshot with this tool's longer transcription prompt (more to process
# than a single UI element) still timed out at 90s under real memory
# pressure, so 120s is the realistic floor, not a guess.
_TIMEOUT_S = 120.0

_TRANSCRIBE_PROMPT = """You are looking at a screenshot that contains a quantum circuit diagram \
(it may be a paper figure, a tutorial page, a slide, or a hand-drawn diagram — the whole screen, \
not necessarily just the diagram itself).

Transcribe EXACTLY what is drawn into real Qiskit source code. Do not identify the circuit by name \
and do not recall a textbook/standard version of a similarly-named circuit from your training data — \
even if it looks like a well-known circuit (teleportation, a Bell pair, QFT, ...), reproduce THIS \
diagram's actual wires and gates, in THIS exact order, not the canonical version. If this diagram \
differs from the standard one in any way (extra qubits, a different gate, a different order), that \
difference must show up in the code.

Work wire by wire, left to right:
- Count the qubit wires (horizontal lines), top to bottom = q0, q1, q2, ... Count the classical bits/
  registers the same way.
- For each wire, read off every gate box in left-to-right order exactly as positioned: H, X, Y, Z, S,
  Sdg, T, Tdg, RX/RY/RZ (read the angle if one is written), etc.
- A filled dot connected by a vertical line to another gate/dot is a controlled operation — CX/CNOT
  (dot to an oplus/X symbol), CZ (dot to dot), a Toffoli/CCX (two dots to an oplus), or a controlled
  version of whatever gate is at the other end. Preserve which wire is the control and which is the
  target.
- A vertical dashed line across wires is a barrier — circuit.barrier().
- A meter/gauge symbol on a qubit wire is a measurement into the classical bit its double-line
  connects to — circuit.measure(qubit, clbit).
- A gate box connected to a classical wire (often drawn with a double line and a label like "=1" or
  a small classical-control box) is a classically-controlled gate, conditioned on a measured value —
  use circuit.x(qubit).c_if(creg, value) (or the equivalent), matching the exact condition shown.
- If the image is too unclear to read a specific gate confidently, insert a Python comment on that
  line saying exactly what's ambiguous rather than guessing a plausible-looking gate.

Output ONLY Python source code, nothing else — no markdown code fences, no explanation before or
after. Build the circuit with QuantumCircuit(num_qubits, num_clbits) (or separate QuantumRegister/
ClassicalRegister if the diagram itself shows named registers), and end the script with
circuit.draw(output="mpl") so the diagram can be rendered back for comparison. If the diagram has
any measurements, also actually run it (AerSimulator, sample counts) and call
plot_histogram(counts) (imported from qiskit.visualization) so results can be shown too — printing
a counts dict as text is not the same thing."""

_CODE_FENCE_RE = re.compile(r"^```[a-zA-Z]*\n?|```$", re.MULTILINE)


class CircuitVisionError(Exception):
    """Raised when the screenshot can't be captured or the vision call fails."""


def _screenshot() -> bytes:
    # WHY PNG, not screen_control.py's JPEG: that tool trades fidelity for
    # speed because it only needs an approximate click point. Transcribing
    # thin gate-box borders and control dots accurately is exactly the kind
    # of fine detail JPEG's lossy compression degrades — lossless matters
    # more here than the few extra seconds of upload time.
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp_file:
        path = Path(tmp_file.name)
    try:
        result = subprocess.run(["grim", str(path)], capture_output=True, text=True, timeout=10)
        if result.returncode != 0:
            raise CircuitVisionError(f"grim failed: {result.stderr.strip()}")
        return path.read_bytes()
    finally:
        path.unlink(missing_ok=True)


def _strip_code_fences(text: str) -> str:
    return _CODE_FENCE_RE.sub("", text).strip()


def _read_local(image_bytes: bytes) -> str:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from backends.local_brain import LocalBrain, LocalBrainError
    from config import ConfigVault

    settings = ConfigVault().settings
    brain = LocalBrain(_VISION_MODEL, host=settings.ollama_host, timeout_s=_TIMEOUT_S)
    try:
        return brain.generate(_TRANSCRIBE_PROMPT, images=[image_bytes])
    except LocalBrainError as error:
        raise CircuitVisionError(f"local vision transcription failed: {error}") from error


def _read_cloud(image_bytes: bytes) -> str:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from config import ConfigVault

    settings = ConfigVault().settings
    if settings.ai_provider == "gemini":
        return _read_gemini(image_bytes, settings)
    if settings.ai_provider == "anthropic":
        return _read_anthropic(image_bytes, settings)
    return _read_openai(image_bytes, settings)


def _read_gemini(image_bytes: bytes, settings) -> str:
    from google import genai
    from google.genai import types

    if not settings.api_key:
        raise CircuitVisionError("FLORA_API_KEY is not set — required for the cloud backend with FLORA_AI_PROVIDER=gemini")
    # WHY a fixed timeout here, not settings.gemini_timeout_s: that field
    # (default 10s) is tuned for the conversational turn's fail-fast-to-
    # offline-fallback behavior (see flora_daemon.py) — an image analysis
    # call genuinely needs longer than a quick text reply.
    client = genai.Client(api_key=settings.api_key, http_options={"timeout": int(_TIMEOUT_S * 1000)})
    try:
        response = client.models.generate_content(
            model=settings.ai_model,
            contents=[types.Part.from_bytes(data=image_bytes, mime_type="image/png"), _TRANSCRIBE_PROMPT],
        )
    except Exception as error:  # google.genai raises its own APIError hierarchy
        raise CircuitVisionError(f"Gemini vision call failed: {error}") from error
    return response.text or ""


def _read_anthropic(image_bytes: bytes, settings) -> str:
    import anthropic

    if not settings.anthropic_api_key:
        raise CircuitVisionError("FLORA_ANTHROPIC_API_KEY is not set — required for the cloud backend with FLORA_AI_PROVIDER=anthropic")
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    b64 = base64.b64encode(image_bytes).decode()
    try:
        response = client.messages.create(
            model=settings.anthropic_model,
            max_tokens=2048,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}},
                    {"type": "text", "text": _TRANSCRIBE_PROMPT},
                ],
            }],
        )
    except anthropic.APIError as error:
        raise CircuitVisionError(f"Anthropic vision call failed: {error}") from error
    return "".join(block.text for block in response.content if block.type == "text")


def _read_openai(image_bytes: bytes, settings) -> str:
    import openai

    if not (settings.openai_api_key and settings.openai_model):
        raise CircuitVisionError(
            "FLORA_OPENAI_API_KEY and FLORA_OPENAI_MODEL are required for the cloud backend with FLORA_AI_PROVIDER=openai"
        )
    client = openai.OpenAI(api_key=settings.openai_api_key, base_url=settings.openai_base_url)
    b64 = base64.b64encode(image_bytes).decode()
    try:
        response = client.chat.completions.create(
            model=settings.openai_model,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                    {"type": "text", "text": _TRANSCRIBE_PROMPT},
                ],
            }],
        )
    except openai.APIError as error:
        raise CircuitVisionError(f"OpenAI vision call failed: {error}") from error
    return response.choices[0].message.content or ""


def read(backend: Literal["cloud", "local"] = "cloud") -> str:
    image_bytes = _screenshot()
    raw = _read_local(image_bytes) if backend == "local" else _read_cloud(image_bytes)
    code = _strip_code_fences(raw)
    if not code:
        raise CircuitVisionError("vision model returned no transcription")
    return code


def _main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Transcribe a quantum circuit diagram off the screen into Qiskit code")
    subparsers = parser.add_subparsers(dest="action", required=True)
    read_parser = subparsers.add_parser("read")
    read_parser.add_argument("--backend", choices=["cloud", "local"], default="cloud")
    args = parser.parse_args()

    if args.action == "read":
        try:
            print(read(args.backend))
        except CircuitVisionError as error:
            print(f"Error: {error}", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    _main()
