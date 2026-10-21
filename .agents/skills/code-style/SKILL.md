---
name: code-style
description: Write and refactor code for readability with direct implementations and minimal abstraction. Use when simplifying code, improving clarity, or writing task scripts with explicit parameters and sequential commands.
---

# Code Style

Apply these principles when writing or refactoring code across languages and projects. Prefer direct, clear implementations that make the current task easy to understand, modify, and debug. **Simplify code to improve readability, not to reduce line count.** Prefer a few extra clear lines over complex one-liners, hidden control flow, or added indirection that only makes the code appear shorter.

## Follow Existing Style and Task Scope

Read applicable project instructions and nearby code first. Follow existing conventions for naming, configuration access, data handling, logging, imports, and tests. When styles differ across a project, follow the current module; organize standalone scripts around the current task. Explicit user preferences take precedence.

Make changes needed for the current task. Do not build a general framework for hypothetical requirements or include unrelated formatting changes or refactors.

## Simplify Code and Improve Readability

- **Avoid excessive function splitting:** Do not create tiny, single-use functions containing only a few lines unless they clearly improve readability.
- **Avoid meaningless abstraction:** Reduce unnecessary wrappers, intermediate layers, classes, and design patterns. Do not abstract for its own sake or add indirection merely to shorten a call site.
- **Keep logic coherent:** Keep related operations together and present them in execution order so readers can follow the complete flow without frequently jumping between functions or files.
- **Extract functions deliberately:** Extract a function or module only for reuse, separation of responsibilities, independent testing, or a clear readability improvement. Do not split code merely to shorten a function or hide complexity in another file.
- **Remove redundancy:** Reduce unnecessary variables, duplicated logic, nested conditions, and intermediate steps. Keep variables that explain intent and clear step-by-step code. Allow small amounts of straightforward repetition; avoid extreme compression and complex one-liners.
- **Preserve behavior:** Unless the user explicitly requests a behavior change, preserve functionality, outputs, interfaces, side effects, and execution order. Do not remove interfaces used by existing callers or include unrelated refactors.

## Shell Script Style

Apply this section to Shell scripts (such as Bash `.sh` files) that users run directly, including experiment launchers and data processing wrappers. Organize them around the current task. Do not default to project-wide entry points, configuration frameworks, or automatic discovery. Prefer explicit configuration followed by sequential commands:

- Put user-editable parameters at the top, using clear variable names, simple assignments, and brief comments where needed. Users edit these values directly.
- Write commands directly in execution order, using simple loops and conditions where necessary. Shared parameters may live in one simple common file; avoid multiple layers of shared scripts or command wrappers.
- Use tools and the runtime available in the current terminal, such as `python`. Users prepare their environment. Unless requested, do not detect, select, or activate Conda or other environments inside scripts.
- Unless the task requires it, do not add command-line parsing, automatic configuration loading, environment detection, or compatibility logic, or turn a task script into a general-purpose framework.
- Do not embed Python in Shell scripts or transfer configuration fields through numeric indices. When configuration parsing is needed, prefer having the program that uses the configuration read it directly.
- Preserve stop-on-error behavior, overwrite protection, and other necessary checks. When simplifying existing scripts, follow the behavior-preservation rule. Changes to parameter interfaces or usage require user authorization and corresponding documentation updates.

These preferences apply specifically to Shell scripts that users run and edit directly. They do not prescribe the structure of Python modules or core application logic.

## Reduce Unnecessary Defensive Code

Trust established types, configuration structures, function contracts, and upstream guarantees. Defensive code should address real boundaries and actual risks rather than add layers of protection against hypothetical problems.

1. **Avoid excessive parameter validation:** Do not repeatedly check internal parameters for type, `None`, or emptiness. Use parameters directly when upstream code guarantees their validity. Prefer validating once at system entry points or where data enters the system.
2. **Avoid excessive `try-except`:** Catch only exceptions that can be reasonably handled, recovered from, or given necessary error context. Do not wrap every block in exception handling or use `except Exception` to conceal real problems.
3. **Avoid unnecessary fallbacks:** Do not add defaults, alternative implementations, or nested `dict.get` calls for hypothetical inputs or environments. When a critical operation fails, do not silently return `None`, an empty list, or a default result. **Prefer failing fast to hiding errors.**
4. **Avoid excessive compatibility code:** Target explicitly supported APIs, data formats, and environments. Do not preemptively support older versions, multiple input forms, or additional environments without a real requirement.
5. **Avoid excessive dynamic attribute checks:** Access attributes directly when object types and structures are known. Do not repeatedly use `hasattr()`, `getattr()`, or `isinstance()`, or use dynamic checks to conceal inconsistent interface definitions.
6. **Avoid repeated boundary checks:** Check a condition once at the appropriate location rather than repeating it across call layers. Use program-controlled data directly when its validity is guaranteed, while retaining necessary boundary checks.
7. **Avoid overhandling theoretical edge cases:** Do not introduce complex branches for error scenarios without an explicit requirement or realistic basis. Focus on problems that are plausible and have a meaningful impact.
8. **Avoid meaningless logging and error wrapping:** Do not add `try-except + logging` to every function or catch, log, and rethrow the same exception unchanged, producing duplicate logs. Log or wrap errors only where useful context can be added, and preserve the original exception information.

9. **Do not use SHA256 hash validation:** Do not compute, store, or compare SHA256 or other file-content hashes to validate sources, templates, assignments, recordings, or generated artifacts. Do not substitute another digest algorithm or add hash caches and skip-check switches. Use explicit paths, task and instance identities, schema compatibility, and actual state checks for pipeline associations. Git revision metadata may still be recorded for provenance.

### Defensive Code to Retain

Simplification does not mean removing error handling. Retain checks and handling proportional to actual risks in these cases:

- **External inputs:** Validate user input, configuration files, third-party API data, and network responses at the boundary where they enter the system.
- **External resources:** Handle real failure conditions involving files, network connections, databases, GPUs, and necessary resource cleanup. Prefer native errors and context managers rather than wrapping every operation in exception handling.
- **Critical constraints:** Retain checks for tensor shape, dtype, device, data ranges, and other conditions that affect computational correctness. Do not repeat checks already reliably guaranteed upstream.
- **Recoverable errors:** Handle cases that genuinely require retries, skipping, or degradation. Make the handling conditions and outcomes explicit; do not silently swallow failures.

## Use Necessary Validation

Prefer existing checks and focused smoke checks for the change. Add or update tests only when requested, required by project conventions, or useful for verifying important behavior or preventing a plausible regression. Do not add tests that merely mirror the implementation for reversible, low-impact changes.

Keep validation proportional to the change. When no suitable check exists, explain the validation limit rather than adding meaningless tests to inflate coverage.
