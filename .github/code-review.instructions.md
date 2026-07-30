---
description: "Use when writing or reviewing Python code for flora-ai. Covers architectural integrity, naming clarity, error handling patterns, and safety constraints specific to voice pipeline and shell execution. Apply the 20-line rule, SRP, dependency inversion, and null avoidance."
applyTo: "**/*.py"
---

# Florinda Code Review & Architectural Principles

This guide enforces **deep architectural thinking** over surface compliance. Every module must withstand scrutiny on naming, intent, error handling, and single responsibility.

## High-Level Logic: The Voice Pipeline

The system pipelines input through 4 critical stages:

```
(1) Input Processing → (2) API Transformation → (3) Safe Execution → (4) Voice Output
```

Each stage must be **isolated, testable, and single-purposed**. Violations here cascade failures through the entire pipeline.

---

## PART I: NAMING & INTENT

**Why this matters**: A name should answer why something exists. If you can't name it clearly, you don't understand it.

### Revealing Intent

#### ✅ **DO**: Names that tell the story

- `parsed_response = self._parse_response_for_command_and_speech()` 
  - Intent: Extract execution command and voice output from AI response
  - Why this method exists: API returns mixed data; we need to separate concerns

- `is_null_command(cmd)` 
  - Intent: Boolean check for forbidden placeholder values
  - Why: Shell safety gate; "null" is our no-op marker

- `handle_api_timeout_with_fallback()` 
  - Intent: Wrap API errors, return graceful degradation
  - Why: Network calls fail; user expects something, not silence

#### ❌ **DON'T**: Noise words and false signals

- `response_data`, `cmd_text`, `result_obj` → Generic; doesn't explain purpose
- `do_process()`, `process_input()` → Vague action; use verbs that reveal intent
- `CoreModule`, `Helper`, `Manager` → Wrong abstraction layer

### Disinformation Check

- **Wrong**: Class named `Pipeline` that contains only a single `execute()` method
  - **Why it fails**: "Pipeline" implies orchestration; a single method isn't orchestration
  - **Fix**: Name it `CommandRouter` or `ResponseDispatcher`

- **Wrong**: Method named `get_output()` that also modifies internal state
  - **Why it fails**: Command-query separation violated; "get" says read-only
  - **Fix**: Name it `extract_and_log_output()` or split into two methods

### Parts of Speech Rule

- **Classes** = Nouns: `PromptProcessor`, `SystemTerminal`, `HyprCore`
- **Methods** = Verbs: `speak()`, `run_command()`, `parse_response()`
- **Booleans** = Predicates: `is_null_command()`, `has_pipe_separator()`, `was_successful()`

---

## PART II: FUNCTION INTEGRITY

**Why this matters**: Functions that do too much hide bugs and make testing impossible.

### The 20-Line Rule

Every function should fit on one screen (< 20 lines of actual logic, excluding comments).

#### ❌ Current risk: `process()` in processor.py

Looking at the code, if `process()` does:
1. API call
2. Response parsing
3. Error handling for missing `.text` 
4. Speech synthesis call
5. Command execution
6. Output composition

→ This is **5 different responsibilities**. Each belongs in its own method.

#### ✅ Correct structure (step-by-step)

```
process(user_input):
    Step 1: Call API, catch timeout
    Step 2: Validate response exists (null check)
    Step 3: Delegate to _handle_parsed_response()
    Step 4: Return composed result
    
_handle_parsed_response(response_text):
    Step 1: Parse COMMAND:|SPEECH: format
    Step 2: Validate command safety (null check)
    Step 3: Return {command, speech} tuple
    
_execute_with_safety(command, speech):
    Step 1: Run SystemTerminal.run_command()
    Step 2: Compose output
    Step 3: Return result
```

**Why**: Each function does ONE thing; each is testable in isolation.

### Singularity: One Job, Done Well

Question every function:
- **Does it have a reason to change?** If yes, extract that reason into a separate function.
- **Can I name it with a verb + clear object?** If it's "do_stuff()", split it.

**Example violation**: 
```python
def process(user_input):
    # This validates, transforms, executes, AND speaks
    # 4 reasons to change = 4 functions should exist
```

### Abstraction Level: The Stepdown Rule

All statements in a function should be at the **same level of abstraction**.

#### ❌ Bad: Mixing abstraction levels
```python
def process(user_input):
    response = self.client.models.generate_content(...)  # HIGH: API layer
    if "|" not in response.text:                         # MED: parsing logic
        return response.text                              # LOW: string return
    parts = response.text.split("|", 1)                  # MED: parsing
    cmd = parts[0].replace("COMMAND:", "").strip()       # LOW: string ops
```

#### ✅ Good: Consistent abstraction
```python
def process(user_input):
    api_response = self._call_gemini_api(user_input)     # HIGH: API
    return self._handle_parsed_response(api_response)    # HIGH: routing
    
def _call_gemini_api(prompt):
    return self.client.models.generate_content(...)      # MID: API call + error handling
    
def _handle_parsed_response(raw_text):
    parsed = self._parse_response(raw_text)               # MID: parsing
    return parsed                                         # HIGH: return
```

**Why**: New person reading `process()` understands the flow without diving into implementation details.

### Argument Count: 0-2 Is the Limit

**Why**: More than 2 arguments suggests the function is trying to do too much, or arguments are related (→ make an object).

#### ❌ Bad: Too many arguments
```python
def execute_and_speak(cmd, speech, voice_model, user_context, should_log):
    # 5 arguments = 5 different concerns mixed together
```

#### ✅ Good: Use an object when arguments are related
```python
class ExecutionContext:
    def __init__(self, cmd, speech, voice_model):
        self.cmd = cmd
        self.speech = speech
        self.voice_model = voice_model

def execute_and_speak(context, should_log):
    # 2 arguments, one is a cohesive object
```

### Side Effects: Hidden Tasks Are Bugs

If a function is named `parse_response()` but also calls `self.speak()`, that's a **hidden side effect**. 

**Rule**: A function should do exactly what its name says, nothing more.

#### ❌ Hidden side effect in processor.py
```python
def process(user_input):
    # ... parsing ...
    self.speak(speech_part)  # HIDDEN: This isn't in the name!
    return result
```

#### ✅ Separated concerns
```python
def process(user_input):
    # Only processing; returns data
    parsed = self._parse_and_execute(user_input)
    return parsed

def handle_response(parsed_data):
    # Caller decides to speak
    self.speak(parsed_data['speech'])
```

### Command-Query Separation

A function either:
- **Changes state** (command): returns nothing or success/failure
- **Returns information** (query): doesn't modify state

**Never do both.**

#### ❌ Violates separation
```python
def get_and_execute_command(user_input):
    cmd = self.parse(user_input)      # QUERY: retrieves data
    self.terminal.run_command(cmd)    # COMMAND: modifies system state
    return cmd                         # Returns data AND side-effected
```

#### ✅ Separated
```python
def parse_command(user_input):
    # QUERY: Returns data only
    return self.parse(user_input)

def execute_command(cmd):
    # COMMAND: Modifies state only
    return self.terminal.run_command(cmd)
```

---

## PART III: ERROR HANDLING

**Why this matters**: Silent failures hide Florinda's problems. Errors should be loud and specific.

### Exceptions Over Error Codes

**Never** return error codes like `{"status": "error", "code": 500}`. Raise exceptions.

#### ❌ Bad: Error codes
```python
def run_command(cmd):
    if not cmd:
        return {"status": "failed", "message": "Empty command"}
    result = subprocess.run(...)
    if result.returncode != 0:
        return {"status": "failed", "stderr": result.stderr}
    return {"status": "success", "stdout": result.stdout}
```

**Problem**: Caller must check `.get("status")` every time. Easy to miss.

#### ✅ Good: Exceptions
```python
def run_command(cmd):
    if not cmd:
        raise ValueError("Command cannot be empty")
    result = subprocess.run(...)
    if result.returncode != 0:
        raise RuntimeError(f"Command failed: {result.stderr}")
    return result.stdout
```

**Why**: Caller must handle it or propagate; can't ignore.

### The Null Rule: Never Return or Receive Null

If a value could be null, that's a bug in your design. Use exceptions or return a wrapper object.

#### ❌ Bad: Returns None/null
```python
def parse_response(raw_text):
    if "|" not in raw_text:
        return None  # Caller has to check "if result is None"
    return parse(raw_text)
```

#### ✅ Good: Exceptions or wrapped return
```python
# Option 1: Raise exception
def parse_response(raw_text):
    if "|" not in raw_text:
        raise ValueError("Response missing COMMAND:|SPEECH: separator")
    return parse(raw_text)

# Option 2: Return wrapper (for expected, non-error cases)
class ParsedResponse:
    def __init__(self, has_command, command=None, speech=""):
        self.has_command = has_command
        self.command = command
        self.speech = speech

def parse_response(raw_text):
    if "|" not in raw_text:
        return ParsedResponse(has_command=False, speech=raw_text)
    return ParsedResponse(has_command=True, command=..., speech=...)
```

### Try-Catch Extraction

Error handling logic should be in **dedicated functions**, not inline.

#### ❌ Bad: Error logic mixed with main logic
```python
def process(user_input):
    try:
        response = self.client.models.generate_content(...)
        parsed = self._parse_response(response.text)
        result = self.execute(parsed['cmd'])
        self.speak(parsed['speech'])
        return result
    except Exception as e:
        print(f"Error: {e}")
        return "I encountered an error"
```

**Problem**: Can't test error path; unclear what's being handled.

#### ✅ Good: Dedicated error handler
```python
def process(user_input):
    try:
        response = self._call_api_safely(user_input)
        parsed = self._parse_response_safely(response.text)
        result = self._execute_and_speak_safely(parsed)
        return result
    except APIError as e:
        return self._handle_api_error(e)
    except ParseError as e:
        return self._handle_parse_error(e)

def _handle_api_error(error):
    # Dedicated error response
    return "I couldn't reach Gemini. Try again in a moment."

def _handle_parse_error(error):
    # Dedicated error response
    return "I didn't understand my own response. Something went wrong."
```

**Why**: Each error type has its own handler; testable; clear recovery.

---

## PART IV: CLASS DESIGN

**Why this matters**: Bad class design forces bad functions. Classes define your system's boundaries.

### Single Responsibility Principle (SRP)

**Each class has ONE reason to change.**

#### ❌ This class violates SRP
```python
class AIAssistant:
    # Reason 1 to change: API format changes
    def call_gemini(self):
        ...
    
    # Reason 2 to change: Response parsing rules change
    def parse_response(self):
        ...
    
    # Reason 3 to change: Shell behavior changes
    def run_command(self):
        ...
    
    # Reason 4 to change: TTS behavior changes
    def speak(self):
        ...
```

**Problem**: If shell execution changes, you must modify AIAssistant. It shouldn't know about shells.

#### ✅ Correct: Each class has ONE reason
```python
class PromptProcessor:
    # Reason to change: Gemini API format or parsing rules
    def process(self, user_input): ...

class SystemTerminal:
    # Reason to change: Shell execution behavior
    def run_command(self, cmd): ...

class HyprCore:
    # Reason to change: TTS/voice settings
    def speak(self, text): ...
```

### Law of Demeter: Avoid Train Wrecks

**A module should not rely on internal structure of objects it receives.**

#### ❌ Bad: Train wreck (reaching through multiple layers)
```python
def execute(ai_response):
    cmd = ai_response.parser.output.command  # Reaches through 3 layers
    self.terminal.run_command(cmd)
```

**Problem**: If parser structure changes, execution breaks.

#### ✅ Good: Ask for what you need
```python
def execute(parsed_response):
    cmd = parsed_response.get_command()  # Encapsulated method
    self.terminal.run_command(cmd)
```

### Dependency Inversion: Depend on Abstractions, Not Concretions

Classes should depend on **interfaces**, not on specific implementations.

#### ❌ Bad: Depends on concrete class
```python
class HyprCore:
    def __init__(self):
        self.tts = PiperTTS()  # Locked to specific TTS
        self.api = GeminiAPI()  # Locked to specific API

    def speak(self, text):
        self.tts.synthesize(text)  # Cannot swap for different TTS
```

**Problem**: Can't test with mock TTS; can't switch to different provider.

#### ✅ Good: Depends on abstraction
```python
class TextToSpeechInterface:
    def synthesize(self, text): raise NotImplementedError

class HyprCore:
    def __init__(self, tts_provider: TextToSpeechInterface):
        self.tts = tts_provider  # Any implementation works

    def speak(self, text):
        self.tts.synthesize(text)  # Can be PiperTTS, MockTTS, other
```

**Why**: Testable; swappable; follows Open/Closed Principle.

---

## PART V: FORMATTING & TESTING

### Newspaper Metaphor

Structure code like a newspaper:
- **Headline (top)**: High-level orchestration
- **First paragraph (upper)**: Key steps
- **Details (bottom)**: Implementation

#### ✅ Good structure
```python
def main():
    # High-level: what Florinda does
    input_text = get_user_input()
    response = process_with_ai(input_text)
    execute_and_speak(response)

def process_with_ai(input_text):
    # Mid-level: AI processing
    raw_response = call_gemini_api(input_text)
    return parse_response_safely(raw_response)

def call_gemini_api(prompt):
    # Low-level: API details
    try:
        response = self.client.models.generate_content(...)
        return response
    except Exception as e:
        raise APIError(str(e))
```

**Why**: New developers scan from top and understand flow before diving into details.

### F.I.R.S.T. Tests

Every function with logic must have a test. Tests must be:

- **Fast**: Runs in milliseconds
- **Independent**: No dependencies on other tests
- **Repeatable**: Same result every run
- **Self-Validating**: Passes or fails; no "check the log manually"
- **Timely**: Written with the code, not after

#### ✅ Example test for safety gate
```python
def test_run_command_rejects_null_placeholder():
    terminal = SystemTerminal()
    with pytest.raises(ValueError):
        terminal.run_command("null")

def test_run_command_rejects_empty_string():
    terminal = SystemTerminal()
    with pytest.raises(ValueError):
        terminal.run_command("")

def test_parse_response_without_pipe_treats_as_speech_only():
    processor = PromptProcessor()
    result = processor._parse_response("Just a normal response")
    assert result["execute"] == "null"
    assert result["speak"] == "Just a normal response"
```

---

## PART VI: CODE REVIEW GRADING

When you review code, apply this blunt rubric:

### Violations Checklist

Go through each code submission and mark violations:

- [ ] Function > 20 lines of logic?
- [ ] Function does multiple things?
- [ ] Returns or passes null?
- [ ] Method name doesn't match its behavior?
- [ ] Mixed abstraction levels in one function?
- [ ] More than 2 arguments (without object)?
- [ ] Hidden side effects?
- [ ] Error handling inline instead of delegated?
- [ ] Class has multiple reasons to change?
- [ ] "Train wreck" property access (a.b().c.d)?
- [ ] Depends on concrete classes instead of interfaces?
- [ ] Missing tests for logic branches?

### Verdicts

- **0 violations**: Professional. Merge it.
- **1-2 violations**: Legacy-adjacent. Request specific fixes before merge.
- **3+ violations**: Legacy Trash. Reject; require rewrite.

---

## PART VII: COMMENTARY PLAN

Specific comments to include (not "what the code does", but "why it exists"):

### ✅ DO: Intent comments
```python
# We reject "null" because Gemini may output it as a no-op placeholder
if cmd.lower() == NULL_COMMAND:
    raise ValueError(...)

# Split on first pipe only; response may contain pipes in the speech text
cmd_part, speech_part = raw_text.split("|", 1)

# Piper requires raw PCM audio; aplay expects 22050 Hz, 16-bit signed
self.piper_cmd = f"piper-tts --model {model} --output_raw | aplay -r 22050 -f S16_LE -t raw"
```

### ❌ DON'T: Useless comments
```python
# Check if cmd exists
if cmd:  # Obviously wrong; we don't need this

# Return the result
return result  # What else would you return?

# API response
response = client.generate_content(...)  # This is already clear from the code
```

---

## GROWTH TIP: The Boy Scout Rule

Before you finish, find ONE thing you made slightly cleaner than you found it:

- Split a 30-line function into three 8-line functions
- Renamed a variable from `tmp` to `parsed_command_data`
- Extracted magic string `"null"` into named constant `NULL_COMMAND`
- Added one missing test for an error path

**Every commit should leave the codebase more readable than it was.**

---

## Exception Rule: When You Can Provide a Snippet

If a user is genuinely stuck on one specific thing (e.g., "How do I structure the try-catch for API timeout?"), provide ONE small example (5-10 lines max), then immediately tell them to type it out themselves and adapt it to their context. Never paste ready-to-run code.

**Example exception response:**
> "Your API call needs a timeout wrapper. Here's the pattern:
> ```python
> try:
>     response = self.client.models.generate_content(..., timeout=10)
> except Timeout:
>     return "API timed out; please try again"
> ```
> Now adapt this to your PromptProcessor; think about what fallback message makes sense for your use case, and type it out."

---

## Closing: The Real Standard

This isn't about checking boxes. It's about building a system where:
- A new developer can understand `process()` without reading 5 helper functions
- When you change how shell execution works, you only modify `SystemTerminal`
- When tests fail, they fail loudly and specifically
- You can refactor without fear of hidden side effects

**Systems that embody these principles are fragile-free, testable, and team-friendly.**

