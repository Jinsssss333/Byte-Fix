# Auto-Remediate: agent rules

You are helping build **Auto-Remediate**, a terminal tool that catches crashed Celery tasks, has an open-weight LLM (Gemma 4 via the Gemini API) write a failing reproducer test plus a patch, and **proves the fix by running pytest**. This is a 5-hour hackathon build. Speed and a working demo path matter more than completeness.

## Read first
- `SPEC.md` is the source of truth for contracts, signatures, formats and error handling.
- `TASKS.md` is the ordered work list. Do ONE task at a time, in order.

## Hard rules
1. Do only the task you were given. No extra features, no refactors, no renames of files or functions listed in SPEC.md.
2. Do NOT modify these files unless the task says so: `autoremediate/prompts.py`, `demo_app/*`, `tests/fixtures/*`, `AGENTS.md`, `SPEC.md`, `TASKS.md`.
3. The LLM never decides pass or fail. `verify.py` runs pytest and decides. Never let model output set a status.
4. Never use native function calling. Tool calls are one JSON line, final answers are tagged sections (see SPEC.md section 3).
5. Never write secrets into files. Config comes from `.env` via python-dotenv. Never read or edit `.env`.
6. New dependencies: only those in `requirements.txt`. Ask before adding any other.
7. Every module gets a pytest file in `tests/`. Tests must run offline with NO network and NO live LLM: use the fixtures in `tests/fixtures/` and fake `chat_fn` functions.
8. Python 3.11+. Type hints on public functions. Short docstrings. Standard library where possible.
9. Model patches are applied only inside a temp copy of the repo, never the real repo, unless the user passes `--apply` or `--pr`.
10. Keep files small. If a file passes about 250 lines, stop and tell the user.

## Workflow for every task
1. Re-read the relevant SPEC.md sections and the task's acceptance command.
2. Write or update the tests first when practical.
3. Implement.
4. Run the acceptance command. If it fails, fix it. Do not move on.
5. Run the full suite: `python -m pytest -q`.
6. Report in 3-5 lines: what changed, what passed, anything uncertain. Then STOP and wait for the next task.

## Commands
- Install: `pip install -r requirements.txt`
- Tests: `python -m pytest -q`
- Trigger a demo crash: `python -m demo_app.trigger --case null`
- CLI (after T7): `python -m autoremediate.cli --help`
