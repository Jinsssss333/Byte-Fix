# SPEC: contracts for Auto-Remediate

Everything OpenCode needs to implement `autoremediate/` without guessing. Files already provided (do not rewrite): `autoremediate/prompts.py`, everything in `demo_app/`, everything in `tests/fixtures/`.

## 1. Repo layout

```
autoremediate/
  __init__.py
  hook.py        # T2  capture failures
  tools.py       # T3  read_file, grep
  verify.py      # T4  temp copy, patch, pytest, diff
  llm.py         # T5  model client + fallback
  agent.py       # T6  parse + loop
  cli.py         # T7  Typer + Rich
  prompts.py     # PROVIDED
demo_app/        # PROVIDED (celery_app, tasks, trigger, tests)
tests/           # tests for autoremediate (one file per module)
  fixtures/      # PROVIDED
failures/        # captured crashes (failures/<id>.json)
recordings/      # cached successful runs (recordings/run_<id>.json)
```

## 2. Config (python-dotenv, read from `.env`)

| Var | Default | Meaning |
|---|---|---|
| LLM_BASE_URL, LLM_MODEL, LLM_API_KEY | none | Primary model. Never hardcode a model name or URL |
| FALLBACK_BASE_URL, FALLBACK_MODEL, FALLBACK_API_KEY | empty | Optional fallback |
| TARGET_TESTS | demo_app/tests | Test directory of the target repo, relative to repo root |
| FAILURES_DIR | failures | |
| RECORDINGS_DIR | recordings | |
| REDACT_PII | 1 | Also mask emails and 13-16 digit numbers in captured values |

Repo root = current working directory.

## 3. Agent protocol

The model replies with exactly one of three things (full wording is in `prompts.py`):

**Tool call**: one line of JSON.
```
{"action": "read_file", "args": {"path": "demo_app/tasks.py"}}
{"action": "grep", "args": {"pattern": "email", "path": "demo_app"}}
```

**Final**: tagged sections. See `tests/fixtures/canned_final_1.txt` for a complete valid example.
```
<final><root_cause>..</root_cause><evidence>- ..</evidence><confidence>high</confidence>
<reproducer_test>..python..</reproducer_test>
<patched_file path="demo_app/tasks.py">..complete file..</patched_file></final>
```

**Give up**: see `tests/fixtures/canned_giveup_3.txt`.

### parse_action(text) -> dict
Tolerant parser. Return one of:
- `{"kind": "tool", "action": "read_file"|"grep", "args": {...}}`
- `{"kind": "final", "root_cause": str, "evidence": [str], "confidence": str, "reproducer_test": str, "patched_files": {path: content}}`
- `{"kind": "give_up", "reason": str, "evidence": [str]}`

Rules: strip markdown fences (``` and ```python) found INSIDE reproducer_test and patched_file contents; strip leading/trailing whitespace but keep a trailing newline on file contents; for tool calls, find the first balanced `{...}` in the text; evidence lines are the lines starting with `-`. Raise `ValueError` if nothing valid is found, or if `final` lacks reproducer_test or any patched_file.

## 4. Failure JSON (written by the hook, read by agent and verify)

See `tests/fixtures/failure_1.json` for the exact shape. Fields: id (str), timestamp (UTC ISO), task_name, task_id, args (list), kwargs (dict), exception_type, exception_message, traceback (str), frames (list of {file, line, function, code, locals}), git_sha.

Hook rules:
- Frames: walk the traceback with `traceback.walk_tb`. Keep ONLY frames whose file is inside the repo root and not in site-packages or `.venv`. Celery's own frames appear first in the real traceback and MUST be dropped. Store `file` relative to the repo root.
- `code` is the source line (use `linecache`). `locals` maps name -> `repr(value)` truncated to 300 chars.
- Redaction: if a key matches `(?i)password|passwd|secret|token|api[_-]?key|authorization|credential` set the value to `"[REDACTED]"`, at any depth in args, kwargs and locals. If REDACT_PII=1, also replace email addresses with `[REDACTED_EMAIL]` and 13-16 digit numbers with `[REDACTED_NUMBER]` inside STRING values only. `None` and numbers are left alone.
- args and kwargs must be made JSON safe (`default=repr` or a helper). The hook must never raise: wrap everything in try/except and swallow errors, so the hook can never break the worker.
- id = (highest existing integer id in FAILURES_DIR) + 1, as a string.
- git_sha from `git rev-parse --short HEAD`, or `"unknown"` if that fails.
- The Celery signal `task_failure` fires in both worker and eager mode (verified). Handler signature: `on_task_failure(sender=None, task_id=None, exception=None, args=None, kwargs=None, traceback=None, einfo=None, **kw)`. `sender` is the task (use `sender.name`). If `traceback` is None use `exception.__traceback__`.
- `install()` connects the handler with `task_failure.connect(on_task_failure, weak=False)` and is idempotent.

## 5. Public signatures

```python
# hook.py
def redact(value, key: str | None = None): ...
def capture_failure(exc, task_name, args, kwargs, task_id=None, tb=None, failures_dir=None) -> Path: ...
def on_task_failure(sender=None, task_id=None, exception=None, args=None, kwargs=None, traceback=None, einfo=None, **kw) -> None: ...
def install() -> None: ...
def load_failure(failure_id: str, failures_dir=None) -> dict: ...      # FileNotFoundError if missing
def list_failures(failures_dir=None) -> list[dict]: ...                 # newest first

# tools.py   (all paths are relative to repo_root; reject absolute paths, "..", and anything resolving outside repo_root)
def read_file(path: str, repo_root: Path, max_lines: int = 200) -> str: ...      # numbered lines "  12 | code"; adds "... (truncated)" if cut
def grep(pattern: str, path: str, repo_root: Path, max_matches: int = 30) -> str: ...  # "file:line: text"; skips .git, __pycache__, .venv, failures, recordings
def run_tool(action: str, args: dict, repo_root: Path) -> str: ...      # never raises; returns "ERROR: ..." text on bad input

# llm.py
def chat(messages: list[dict]) -> tuple[str, str]: ...                  # (reply_text, model_label); falls back if configured

# agent.py
@dataclass
class AgentResult:
    kind: str            # "final" | "give_up" | "error"
    payload: dict        # output of parse_action for final/give_up, or {"reason": str} for error
    trace: list[dict]    # one entry per step: {"step": int, "type": "tool"|"final"|"give_up"|"parse_error"|"llm_error", "detail": str, "model": str}
def parse_action(text: str) -> dict: ...
def build_first_message(failure: dict, repo_root: Path, tests_dir: str) -> str: ...
def run_agent(failure, repo_root, chat_fn=None, on_step=None, max_steps=8) -> AgentResult: ...   # chat_fn defaults to llm.chat

# verify.py
@dataclass
class Stage:
    name: str            # "reproducer_fails_before" | "patch_applied" | "reproducer_passes_after" | "suite_passes"
    passed: bool
    output: str          # pytest output, truncated to 3000 chars
@dataclass
class VerifyResult:
    status: str          # see section 6
    reason: str
    stages: list[Stage]
    diff: str            # unified diff of patched files vs the real repo ("" if none)
def verify(failure: dict, final: dict, repo_root: Path, tests_dir: str, timeout: int = 60) -> VerifyResult: ...
def apply_patch_to_repo(final: dict, repo_root: Path) -> list[str]: ...   # writes patched files into the REAL repo, returns paths
```

`build_first_message`: fill `FIRST_MESSAGE_TEMPLATE`. `source_path` = the file of the LAST (innermost) frame in `failure["frames"]`; if there are no frames, use the first path found in the traceback; if no file can be read, put "(source unavailable)". `frame_locals` = that frame's locals, one `name = value` per line. `existing_tests` = list of `*.py` files under tests_dir, one per line. Truncate source to 400 lines. Use `json.dumps` for args and kwargs.

`run_agent` loop: build messages = [system, first user message]. For each step up to `max_steps`: call `chat_fn(messages)`; on exception record `llm_error` and return kind "error". Parse with `parse_action`. On ValueError: append the bad reply as assistant plus `CORRECTION_MESSAGE` as user, record `parse_error`, and allow ONE such retry in total across the run (the second parse failure returns kind "error"). On tool: run `run_tool`, append assistant reply and a user message `"TOOL RESULT:\n" + result`, record the step. On final or give_up: return. If steps run out: return kind "error" with reason "agent did not converge in N steps". Call `on_step(entry)` after every step if provided.

## 6. Verification (code-driven, the core guarantee)

`verify()` steps, in order. Stop at the first failure and return.

1. **Validate paths.** Each patched file path must be relative, inside repo_root, must already exist in the repo, and must NOT be inside the tests directory. Otherwise status `INVALID_PATCH`.
2. **Temp copy.** `shutil.copytree` repo_root to a temp dir, ignoring `.git`, `__pycache__`, `.venv`, `.pytest_cache`, `failures`, `recordings`.
3. **Write the reproducer** to `<tests_dir>/test_repro_<failure id>.py` in the temp copy.
4. **Run it BEFORE the patch.** `[sys.executable, "-m", "pytest", path, "-q", "-x", "-p", "no:cacheprovider"]`, cwd = temp copy, `PYTHONPATH` = temp copy, timeout. Stage `reproducer_fails_before` passes only if the return code is non-zero AND the output contains the failure's `exception_type` string. If the return code is 0 -> status `REJECTED_NO_REPRO` ("reproducer passed before the patch"). If it failed but without the exception type -> `REJECTED_WRONG_FAILURE`. On timeout -> `TIMEOUT`.
5. **Apply the patch** in the temp copy (stage `patch_applied`).
6. **Run the reproducer again.** Must return code 0, else status `PATCH_FAILED` (stage `reproducer_passes_after`).
7. **Run the full suite** `pytest <tests_dir>`. Must return code 0, else status `REGRESSION` (stage `suite_passes`).
8. All good -> status `VERIFIED`. Build `diff` with `difflib.unified_diff` between real repo files and patched contents (labels `a/<path>` and `b/<path>`).

Statuses: `VERIFIED`, `REJECTED_NO_REPRO`, `REJECTED_WRONG_FAILURE`, `PATCH_FAILED`, `REGRESSION`, `INVALID_PATCH`, `TIMEOUT`. The CLI adds its own display states: `GAVE_UP` and `AGENT_ERROR`.

Verification must run with no network and no LLM. Unit-test it with `tests/fixtures/failure_1.json` and `canned_final_1.txt` (parsed through `parse_action`): expect `VERIFIED`. Also test: a reproducer that passes before the patch -> `REJECTED_NO_REPRO`; a patch that breaks another test -> `REGRESSION`; a patch path in the tests dir -> `INVALID_PATCH`.

## 7. LLM client

- Use the `openai` package: `OpenAI(base_url=..., api_key=..., timeout=60)`. `client.chat.completions.create(model=..., messages=..., temperature=0.1)`. Do NOT pass `response_format`, `tools` or any provider-specific parameters.
- `chat()` returns `(text, label)` where label is the model name. If the primary raises any exception (or returns empty text) and the fallback is configured, call the fallback and return its label with a suffix `" (fallback)"`. If no fallback or it also fails, raise `RuntimeError` with a short message.
- Raise `RuntimeError("LLM not configured: set LLM_BASE_URL, LLM_MODEL, LLM_API_KEY in .env")` if the primary vars are missing.

## 8. CLI (Typer + Rich), entry `python -m autoremediate.cli`

```
list                              # table: id, time, task, exception
fix <id> [--replay] [--apply] [--pr]
```
`fix` display order (Rich panels, keep it readable on a projector):
1. **Failure** panel: task, exception, and the frame code line. Highlight any local whose value is `None` in red.
2. **Agent trace**: one line per step as it happens (`on_step`): step number, type, short detail, model label.
3. **Reproducer**: syntax-highlighted test code, then a red `FAILED before patch` line (from stage 1).
4. **Patch**: syntax-highlighted diff, then a green `PASSED after patch` line.
5. **Regression check**: `N existing tests pass` line.
6. **Result banner**: green `VERIFIED` plus confidence, root cause and evidence; or red/yellow banner with the status and reason. For `give_up` show a yellow `COULD NOT REPRODUCE` banner with the reason and evidence, and NO diff.
7. **PR description** (markdown) for VERIFIED results: title, root cause, evidence, files changed, how it was verified, confidence, and a line "Generated by Auto-Remediate. Review before merging."

Flow: load failure -> (if not `--replay`) `run_agent` -> on a `final`, save `recordings/run_<id>.json` containing `{"failure_id", "final", "trace"}` -> `verify`. With `--replay`, load that recording instead of calling the LLM (verification still runs for real, so replay works offline). `--apply` writes the patched files into the real repo via `apply_patch_to_repo`, only when VERIFIED. `--pr` implies `--apply`, then runs: `git checkout -b autoremediate/fix-<id>`, `git add` the patched files and the reproducer (saved into the real tests dir as `test_repro_<id>.py`), `git commit -m "fix: <task_name> (auto-remediate)"`, `gh pr create --title ... --body <pr description>`. If `git` or `gh` fails, print the error and the manual commands; never crash.
Exit code 0 only when VERIFIED.

## 9. Error handling table

| Situation | Behavior |
|---|---|
| Failure id not found | Print a clear message, exit 2 |
| LLM not configured / API error | Print the error, suggest `--replay` if a recording exists, exit 1 |
| Model reply unparseable | One corrective retry, then AGENT_ERROR |
| Agent hits max steps | AGENT_ERROR "did not converge" |
| Model gives up | GAVE_UP banner, exit 1, no diff |
| Reproducer passes before patch | REJECTED_NO_REPRO, show reason, no diff |
| Pytest hangs | TIMEOUT after 60s per run |
| Patch path invalid | INVALID_PATCH |
| Hook raises internally | Swallowed silently; never break the Celery worker |
| `gh` missing | Print manual PR steps |

## 10. Demo bugs (for reference only; never hint them to the model)

1. `send_receipt` crashes with AttributeError when `customer.email` is None (primary).
2. `generate_invoice` crashes with TypeError because Decimal is not JSON serializable (stretch).
3. `convert_total` raises TimeoutError from a fake flaky API; correct behavior is GAVE_UP (the "restraint" demo).
