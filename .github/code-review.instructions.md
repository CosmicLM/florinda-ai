---
description: "Use when writing or reviewing Python code for florinda-ai. Covers this project's actual established conventions: WHY-driven commentary, no hardcoded dev-machine paths, real complete mechanics over stubs, and the safety checkpoints around command execution."
applyTo: "**/*.py"
---

# Florinda Code Review Principles

This project has an established, consistent style across `backends/`,
`tools/`, `watchers/`, `infra/`, and the entry points. Review against
*these* conventions — not a generic clean-code checklist — since several
generic rules (strict function-length caps, "never return None") don't
match how this codebase is actually written and would produce reviews that
fight the existing design instead of catching real problems.

## PART I: COMMENTS — WHY, not WHAT

The codebase's comments consistently explain **why something non-obvious
is true**, often citing a specific bug that was actually observed running
the assistant — not what the following line does.

### ✅ DO: real example from this codebase (`flora_daemon.py`)
```python
# WHY this exists: observed live — asked to "summarize" a Canvas quiz page
# right after already opening it, the model had no ground truth about what
# it had just tried and silently re-ran the identical open-url command,
# describing a vague restatement as if it were new information. Telling the
# model "don't repeat yourself" in prose alone is easy to ignore under
# uncertainty; these two constants back that instruction with real,
# code-enforced state instead of relying on the model to reconstruct it.
_RECENT_ACTIONS_WINDOW_S = 300.0
_RECENT_ACTIONS_MAX = 3
```
This is the bar: cite what was actually observed (a real failure mode,
a real constraint, a real API quirk), then explain the fix's reasoning —
not a restatement of the code.

### ❌ DON'T: comments that restate the code
```python
# Increment the counter
counter += 1

# Loop over the list
for item in items:
```
If removing the comment wouldn't confuse a future reader, it shouldn't be
there. Flag comments like this as noise, not as missing-documentation.

### Hardcoded dev-machine paths are a real, previously-shipped bug class
This project has shipped literal `/home/<dev-machine-username>/...` paths
into `INSTRUCTION.md`'s tool-invocation examples at least twice (see the
`PROJECT_DIR`/`_REPO_ROOT` comments in `processor.py` and `flora_daemon.py`
for the incident writeups) — each time breaking on a fresh install on a
different machine. Flag any new literal absolute path containing a
username or this specific dev machine's layout; it should be
`Path(__file__).resolve().parent`, `$PROJECT_DIR` (in prompt templates), or
similar.

## PART II: NO STUBBED MECHANICS

When a function's whole reason to exist is a specific mechanic (a DB
query, a subprocess pipeline, an API call), that mechanic must be real and
complete — not a `# TODO: implement the actual logic` placeholder. A
`tools/*.py` script exists specifically to give the model (and, in the
teaching tool `learnxinyminutes_docs.py`, the user) working code for one
concrete thing; withholding that one thing defeats the file's purpose.

Generic/placeholder naming is fine and often correct (see
`learnxinyminutes_docs.py`'s boilerplate output) — a wrong *guess* at
domain naming is worse than an honest placeholder. What's not fine is a
placeholder standing in for the mechanism itself.

## PART III: THE COMMAND-EXECUTION SAFETY CHECKPOINTS

Three checkpoints exist between "the model wants to run a command" and
"a command actually executes," each independently:

1. `NULL_COMMAND` / blank rejection — `executor.py`'s `SystemTerminal.run_command` (`_reject_blank_or_null`)
2. Confirm-gating — `flora_daemon.py`'s `_confirm_and_run`: any command containing `sudo` always asks first (`_SUDO_TOKEN_RE`); this is a deliberate, explained design choice (see its comment), not an oversight
3. Repeat-block — `_check_repeat_block`: hard-refuses to literally re-run the same command within 90s, regardless of whether the model reads the recent-actions context it's given

**Never bypass these by calling `subprocess.run`/`os.system` directly** in
new code — route through `SystemTerminal.run_command()` so all three stay
in force. If a new feature seems to need to skip one of them, that's a
design conversation, not a quiet workaround.

## PART IV: CONFIGURATION — everything through `FloraSettings`

New settings belong in `config.py`'s `FloraSettings` (a frozen pydantic
model) plus a line in `ConfigVault._service_overrides()`, not a scattered
`os.getenv(...)` in whichever module needs it. If a setting is only
required for a specific provider/mode (see `ai_provider`'s conditional
`_require_selected_provider_credentials`), make it conditionally required,
not unconditionally required with a workaround default — a user on
`FLORA_AI_PROVIDER=anthropic` shouldn't need a Gemini key they'll never
use.

## PART V: ERROR HANDLING — matches this codebase's actual pattern

This codebase does **not** follow a blanket "never return None, always
raise" rule — `Optional`/`None` returns are the normal, correct pattern
here for "nothing to do" (e.g. `_maybe_execute` returning `None` for
`NULL_COMMAND`, `_check_repeat_block` returning `Optional[str]`).

Broad `except Exception:` at an isolation boundary, paired with
`logger.exception(...)`, is the deliberate pattern for keeping one
subsystem's failure from taking down another — e.g. `_speak_chunk`
isolating a TTS pipeline failure from being mislabeled as an orchestration
failure, or `_archive_stale_conversation` not letting a research-library
write failure lose the conversation memory it already popped. Don't flag
these as "silently swallowing errors" — check instead that the exception
is actually logged (`logger.exception`, not a bare `pass`) and that the
boundary is a real isolation point, not just convenience.

Raise exceptions (`ValueError`, `ConfigurationError`, etc.) for genuine
programmer/config errors that should stop execution — e.g.
`SystemTerminal._reject_blank_or_null`, `config.py`'s
`ConfigurationError`. The distinction is: *expected, handleable outcome*
→ `None`/`Optional` return; *invalid state that shouldn't be silently
tolerated* → exception.

## PART VI: GATE EXPENSIVE WORK BEHIND CHEAP, DETERMINISTIC CHECKS

Recurring pattern across `watchers/` and `processor.py`: before doing
something expensive (a model call, a full watcher cycle), run a cheap
deterministic pre-filter and skip if it doesn't match. Examples:
`quantum_watcher.py`'s keyword regex before treating screen text as
quantum-related, `processor.py`'s `_needs_deep_reasoning` regex/length
check before routing to the slower "deep" tier. New watchers or routing
logic should follow this shape rather than asking a model to judge its own
question's complexity first.

## PART VII: FUNCTION SIZE AND ABSTRACTION

Favor small, single-purpose functions when a section of logic is genuinely
reusable or independently testable (see `flora_daemon.py`'s decomposition
of `run_daemon` into `_generate`, `_handle_instruction`, `_maybe_execute`,
`_confirm_and_run`, etc.). But **do not** treat a hard line-count as the
metric — this codebase's functions routinely carry substantial WHY
commentary that inflates line count without adding complexity. Judge on:
does the function have one clear responsibility, and can you name it
accurately? A well-named 40-line function with 25 lines of WHY comments
explaining real constraints is not a violation; a 15-line function doing
three unrelated things is.

## Review Checklist

- [ ] New comments explain *why* (citing a real constraint/bug where applicable), not *what* the code already says
- [ ] No new hardcoded absolute paths tied to a specific dev machine/user
- [ ] No stubbed-out mechanic where the function's whole purpose is that mechanic
- [ ] New command execution goes through `SystemTerminal.run_command()`, not a direct `subprocess`/`os.system` call
- [ ] New settings live in `FloraSettings` + `_service_overrides()`, not scattered `os.getenv`
- [ ] `except Exception` blocks log via `logger.exception` and sit at a real isolation boundary, not a silent `pass`
- [ ] Expensive/model-call-triggering logic has a cheap deterministic pre-filter where one makes sense
- [ ] Each function has one clear, nameable responsibility (commentary volume isn't part of this judgment)

## Providing a snippet when someone's stuck

If a contributor is stuck on one specific mechanic, give a real, complete
example matching this codebase's own patterns above (not a toy
generic-clean-code example) and point at the closest existing file to
model it after — e.g. "look at how `backends/openai_backend.py` shapes
its call to match `backends/anthropic_backend.py`'s interface."
