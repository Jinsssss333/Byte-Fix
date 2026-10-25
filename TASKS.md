# TASKS: ordered build list (5 hours)

Do tasks in order, one per OpenCode session turn. After each task: acceptance command must pass, then commit (`git add -A && git commit -m "T<n>: ..."`).
Paste the **Prompt** under each task into OpenCode. Text in **YOU** is for the human.

Time guide: T0-T1 30 min | T2-T3 60 min | T4-T6 80 min | T7 40 min | T8-T9 40 min | rehearse 30 min. Skip T10 unless T7 is stable by hour 3:30.

---

## T0. Environment (YOU, 10 min)
YOU: create the repo, copy this kit into it, then:
```
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env     # fill LLM_* from Google AI Studio (Gemma 4 via Gemini API)
git init && git add -A && git commit -m "kit"
python -m pytest -q      # expect: 3 passed
python -m demo_app.trigger --case null   # expect: state=FAILURE
```
Add an MIT or Apache-2.0 LICENSE file now.

## T1. Model gate (OpenCode writes the script, YOU judge the result)
**Prompt:**
> Read AGENTS.md and SPEC.md sections 2, 3 and 7. Create `scripts/model_check.py`: load `.env`, build the first user message with `FIRST_MESSAGE_TEMPLATE` from `autoremediate/prompts.py` using `tests/fixtures/failure_1.json` and the real contents of `demo_app/tasks.py` (innermost frame file), send SYSTEM_PROMPT plus that message to the primary model using the `openai` client exactly as in SPEC section 7, print the raw reply, then print whether it contains `<final>` with a `<reproducer_test>` and a `<patched_file`, or a tool-call JSON line, or neither. Do not import any other autoremediate module. No tests needed for this task.

**Acceptance:** `python scripts/model_check.py` prints a reply and a verdict.
**YOU:** run it 3 times. The model must produce a usable `<final>` (or a sensible `read_file` call) at least 2 of 3 times. If not: change the model, switch to the fallback provider, or loosen the wording in `prompts.py`. Do not continue until this is decided.

## T2. Capture hook
**Prompt:**
> Read AGENTS.md. Implement `autoremediate/hook.py` exactly per SPEC.md sections 4 and 5 (hook signatures). Write `tests/test_hook.py`: (a) `redact` masks password/token keys at depth, masks emails and long digit strings in strings only when REDACT_PII=1, and leaves None and numbers alone; (b) `capture_failure` with a real exception raised from `demo_app.tasks.send_receipt.run(order_with_none_email)` writes `<tmp>/1.json` whose `exception_type` is AttributeError, with a frame for `demo_app/tasks.py` whose locals contain the order, and with NO frames from site-packages; (c) ids increment; (d) `load_failure` and `list_failures` work; (e) the handler never raises even when given garbage. Use `tmp_path` and pass `failures_dir`.

**Acceptance:** `python -m pytest tests/test_hook.py -q` passes. Then manually: `python -m demo_app.trigger --case null` creates `failures/1.json` (the demo app already calls `install()`).

## T3. Tools
**Prompt:**
> Implement `autoremediate/tools.py` per SPEC.md section 5. Write `tests/test_tools.py` using `tmp_path`: read_file returns numbered lines and truncates; grep finds matches, respects max_matches, skips ignored dirs; absolute paths, `..` traversal and symlink escapes are rejected with an ERROR string from `run_tool`; unknown actions return an ERROR string.

**Acceptance:** `python -m pytest tests/test_tools.py -q`

## T4. Verification (most important module, no LLM)
**Prompt:**
> Implement `autoremediate/verify.py` per SPEC.md section 6 and the signatures in section 5. Write `tests/test_verify.py`. Build the `final` dict by writing a small helper in the test that parses `tests/fixtures/canned_final_1.txt` (do not depend on agent.py yet; use a few lines of regex in the test). Cases: (1) failure_1 + canned final against the real repo root -> VERIFIED, `diff` contains `+` lines for demo_app/tasks.py, all four stages passed; (2) a reproducer that passes before patching -> REJECTED_NO_REPRO; (3) a patch that also breaks `send_receipt`'s output format -> REGRESSION or PATCH_FAILED as appropriate; (4) a patched_files path inside `demo_app/tests` -> INVALID_PATCH; (5) a reproducer with an infinite loop and timeout=2 -> TIMEOUT; (6) a patched path that does not exist -> INVALID_PATCH; (7) the real repo files are unchanged after every case. Also implement `apply_patch_to_repo` and test it on a tmp copy of the repo.

**Acceptance:** `python -m pytest tests/test_verify.py -q`

## T5. LLM client
**Prompt:**
> Implement `autoremediate/llm.py` per SPEC.md section 7. Write `tests/test_llm.py` with a fake OpenAI client (monkeypatch the client constructor): primary success; primary raises -> fallback used and label ends with " (fallback)"; empty primary reply -> fallback; both fail -> RuntimeError; missing config -> RuntimeError with the exact message in the spec. No network.

**Acceptance:** `python -m pytest tests/test_llm.py -q`

## T6. Agent loop
**Prompt:**
> Implement `autoremediate/agent.py` per SPEC.md sections 3 and 5. Use `SYSTEM_PROMPT`, `FIRST_MESSAGE_TEMPLATE` and `CORRECTION_MESSAGE` from `autoremediate/prompts.py` without editing that file. Write `tests/test_agent.py` with fake `chat_fn` callables: (1) parse_action handles a tool call, a final (use `tests/fixtures/canned_final_1.txt`), a give_up (use `canned_giveup_3.txt`), fenced code inside tags, and raises ValueError on garbage; (2) a run that does read_file then returns the canned final -> kind "final" and a 2-entry trace; (3) one garbage reply then a valid final -> succeeds with a parse_error entry in the trace; (4) two garbage replies -> kind "error"; (5) a fake that always asks for tools -> kind "error" mentioning "did not converge"; (6) chat_fn raising -> kind "error"; (7) `build_first_message` for failure_1 contains the task name, the source of demo_app/tasks.py and the name of the existing test file. Then add an end-to-end test: fake chat_fn returns the canned final, then `verify.verify` returns VERIFIED.

**Acceptance:** `python -m pytest tests/test_agent.py -q`, then `python -m pytest -q` (everything green).

## T7. CLI
**Prompt:**
> Implement `autoremediate/cli.py` per SPEC.md section 8 with Typer and Rich, runnable as `python -m autoremediate.cli` (add `if __name__ == "__main__": app()`). Implement `list` and `fix <id>` with `--replay`, `--apply`, `--pr` and all seven display sections in order, exit codes per section 9, and recordings per section 8. Also add `pyproject.toml` with a console script `autoremediate = "autoremediate.cli:app"`. Write `tests/test_cli.py` using `typer.testing.CliRunner` and monkeypatching `agent.run_agent`/`llm.chat` with fakes: fix on failure_1 (copy the fixture into a tmp failures dir via FAILURES_DIR) with the canned final prints VERIFIED and exits 0; fix on failure_3 with the give_up canned text prints COULD NOT REPRODUCE, shows no diff, exits 1; `--replay` after a successful run works with the LLM patched to raise; unknown id exits 2. Keep the Rich layout clean and readable on a projector.

**Acceptance:** `python -m pytest tests/test_cli.py -q`
**YOU:** then run the real thing:
```
rm -f failures/*.json && python -m demo_app.trigger --case null
python -m autoremediate.cli list
python -m autoremediate.cli fix 1
```
Success = VERIFIED in 4 of 5 consecutive runs. If the model keeps failing in the same way, tune `prompts.py` yourself (not OpenCode), re-run T1's script to check, and retry.

## T8. Replay, PR, and polish
**Prompt:**
> Review `autoremediate/cli.py` against SPEC.md section 8. Make sure `--replay` works fully offline (LLM patched to raise), `--apply` writes only when VERIFIED, and `--pr` degrades gracefully when `git` or `gh` are unavailable (print the manual commands). Fix any gaps. Add tests for these paths. Do not add new features.

**Acceptance:** `python -m pytest -q` all green. YOU: run `fix 1 --replay` with Wi-Fi off.

## T9. README, docs, hygiene
**Prompt:**
> Write `README.md`: one-paragraph pitch, how it works (the 6-stage loop), a text architecture diagram, quickstart (install, `.env`, trigger a crash, `autoremediate fix 1`), model configuration (any OpenAI-compatible endpoint; Gemma 4 via the Gemini API is the default for the hackathon), safety model (temp-copy execution, secret redaction, hook never raises, human-reviewed PRs, no auto-merge), known limitations (only Celery, read-only file tools, no DB inspection yet, no Docker sandbox yet), roadmap, and a note that this project was built with OpenCode. Then write `docs/digitalocean-gpu.md`: step-by-step for self-hosting an open-weight model behind vLLM's OpenAI-compatible server on a DigitalOcean GPU Droplet and pointing `LLM_BASE_URL` at it. Mark it clearly as "documented, not yet demoed" and tell the reader to check current vLLM and DigitalOcean docs for exact commands. Do not invent version numbers. Leave a placeholder `docs/demo.gif`.

**Acceptance:** README renders and every command in it exists. `grep -rn "API_KEY=" . --include=*.md --include=*.py` shows no real keys.

## T10. Stretch (only if T7 is stable by hour 3:30)
**Prompt:**
> Do not change any source files; only add tests and fixtures. Add `tests/test_generalization.py` proving the pipeline also verifies a second bug (create `failure_2_decimal.json` and `canned_final_2.txt` yourself, modelled on failure_1.json and canned_final_1.txt: the fix converts Decimals using `default=str` in `json.dumps`, the reproducer calls `generate_invoice.run(order)` and asserts the result parses as JSON). The test parses the canned file with `agent.parse_action` and asserts `verify.verify` returns VERIFIED against the real repo.

**Acceptance:** `python -m pytest tests/test_generalization.py -q`

---

## Final checklist (YOU)
- [ ] `fix 1` VERIFIED 4 of 5 runs; `recordings/run_1.json` saved from a good run
- [ ] `fix 1 --replay` works with Wi-Fi off
- [ ] `fix 3` shows COULD NOT REPRODUCE (the restraint demo)
- [ ] Backup screen recording done, `docs/demo.gif` added
- [ ] No secrets in git history, `.env` not committed
- [ ] Repo public, LICENSE present, README complete
- [ ] Submitted to both tracks
