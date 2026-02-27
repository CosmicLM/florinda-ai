# Hypr - Technical Log: Syntax & Logic

## 2026-02-26: PromptProcessor Instantiation Fix
**Issue:** System was returning `<processor.PromptProcessor object at...>` instead of the Gemini string.

**Root Cause:** The `HyprCore` was being passed the `PromptProcessor` class reference rather than a living instance. Calling it in `main.py` simply initialized the class without executing the logic.

**Fix:**
1. Instantiate the object: `processor = PromptProcessor("init")`
2. Explicitly call the `.process()` method in the main loop:
```python
if user_input.strip():
    print(core.processor.process(user_input))