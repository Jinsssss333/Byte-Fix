# Auto-Remediate: Architecture, Anti-Hallucination Guardrails & Project Plan

> **Document Purpose**: Single source of truth for the project state, architectural extensions (Sentry integration, Git worktree isolation), anti-hallucination protocols, and phased implementation tasks.

---

## 1. Project Overview & Core Philosophy

**Auto-Remediate** is an autonomous debugging and remediation pipeline for crashed Python Celery tasks (with Sentry APM webhook integration). It leverages an open-weight LLM (e.g., Gemma 4 via Gemini API / OpenAI-compatible endpoint) to diagnose errors, generate isolated reproducer tests, and propose minimal code patches.

### The Golden Rule: Code-Driven Ground Truth (Zero Hallucination Acceptance)
- **The LLM never decides pass or fail.** The model's claims are treated as untrusted hypotheses.
- **Verification is code-driven:** `autoremediate/verify.py` executes real `pytest` commands inside an isolated temporary sandbox.
- Fixes are accepted **only** when:
  1. The reproducer test fails against the unpatched code with the matching exception (`reproducer_fails_before`).
  2. The patch is applied cleanly in the sandbox (`patch_applied`).
  3. The reproducer test passes against the patched code (`reproducer_passes_after`).
  4. The full existing test suite passes with zero regressions (`suite_passes`).

---

## 2. Anti-Hallucination & Context Matching Protocols

To prevent the agent from hallucinating dependencies, non-existent files, speculative fixes, or invalid formats, the following constraints are strictly enforced:

### A. Strict Input/Output Protocol
1. **One-Action Turns**:
   - The LLM must output **either** exactly one JSON line for a tool call:
     ```json
     {"action": "read_file", "args": {"path": "demo_app/tasks.py"}}
     ```
   - **Or** a structured `<final>` tag block (with `<root_cause>`, `<evidence>`, `<confidence>`, `<reproducer_test>`, `<patched_file path="...">`):
     ```xml
     <final>
       <root_cause>...</root_cause>
       <evidence>- ...</evidence>
       <confidence>high|medium|low</confidence>
       <reproducer_test>...</reproducer_test>
       <patched_file path="demo_app/tasks.py">...</patched_file>
     </final>
     ```
   - **Or** a `<give_up>` tag block (when bugs depend on flaky external networks/APIs).
2. **Single Correction Turn**:
   - If model output violates format, `CORRECTION_MESSAGE` is sent once. If the second reply fails to parse, the loop aborts with `AGENT_ERROR` (no infinite hallucination loops).
3. **No Speculative Patches for External Failures**:
   - Flaky external network issues (e.g., `TimeoutError` from third-party APIs) must produce `<give_up>`, demonstrating algorithmic restraint rather than inventing fake mocks or unnecessary retries.

### B. Sandboxed Tool Safety (No Imaginary Files)
- `read_file` and `grep` only resolve within the validated `repo_root`.
- Absolute paths, `..` directory traversal, and symlink escapes return immediate `ERROR: ...` responses.
- Patched file paths must already exist in the target repository and **must never** target files inside the test directory.

### C. Zero In-Place Mutation During Verification
- Model patches and reproducer tests are written **only inside a temporary `shutil.copytree` sandbox**.
- The main repository files are never modified until all 4 verification stages pass and the user explicitly passes `--apply` or `--pr`.

---

## 3. Sentry Integration Architecture

In addition to direct Celery crash capture via signals, Auto-Remediate supports **Sentry Webhook Ingestion** to act as a production self-healing engine.

```mermaid
flowchart TD
    subgraph CrashSources["1. Crash Sources"]
        C1[Celery Worker Crash] -->|celery.signals.task_failure| HOOK[autoremediate/hook.py]
        C2[Production Service Crash] -->|Captured by Sentry SDK| SENTRY[Sentry Cloud / Self-Hosted]
    end

    subgraph Ingestion["2. Normalization & Storage"]
        HOOK -->|Write JSON| STORE[(failures/<id>.json)]
        SENTRY -->|Issue Webhook POST| WH[autoremediate/sentry.py\nWebhook Ingestion Endpoint]
        WH -->|Payload Normalizer| STORE
    end

    subgraph RemediationEngine["3. Auto-Remediate Core Engine"]
        STORE --> AGENT[autoremediate/agent.py\nGemma 4 / OpenAI Client]
        AGENT -->|Tolerant Parser + 8-step Loop| TOOLS[autoremediate/tools.py\nread_file, grep]
        TOOLS --> AGENT
        AGENT -->|Reproducer + Patch| VERIFY[autoremediate/verify.py\nTemp Sandbox]
    end

    subgraph IsolatedExecution["4. Verification Sandbox"]
        VERIFY -->|Step 1| S1[Reproducer Fails Before]
        S1 -->|Step 2| S2[Patch Applied in Temp Copy]
        S2 -->|Step 3| S3[Reproducer Passes After]
        S3 -->|Step 4| S4[Suite Passes Zero Regressions]
    end

    subgraph Delivery["5. Delivery & PR"]
        S4 -->|VERIFIED| WT[Git Worktree / Branch\nautoremediate/fix-<id>]
        WT --> PR[GitHub PR via `gh` / GitLab MR]
        S4 -->|REJECTED| LOG[Audit Log & Sentry Status Note]
    end
```

### A. Sentry Payload Normalizer (`autoremediate/adapters/sentry.py`)
Converts Sentry's rich webhook event JSON into Auto-Remediate's `failures/<id>.json` schema:
- `id`: Sentry Event ID or incremented integer ID.
- `timestamp`: UTC ISO timestamp from Sentry event.
- `task_name`: `event.transaction` or tag `task_name`.
- `args` / `kwargs`: Extracted from Sentry `extra` or Celery context.
- `exception_type` & `exception_message`: Extracted from `event.exception.values[0]`.
- `traceback`: Reconstructed from Sentry stacktrace.
- `frames`: Filtered stack frames (excluding `site-packages` and `.venv`), extracting code snippet and local variables (`vars`).
- `git_sha`: Extracted from `event.release` or local git HEAD.

### B. Ingestion Server (`autoremediate/webhook.py`)
- Lightweight webhook endpoint built with standard Python library (`http.server`) to avoid adding unnecessary heavy dependencies.
- Validates Sentry signature headers (`sentry-hook-resource`, `sentry-hook-signature`).
- Stores normalized failure into `failures/<id>.json` and triggers agent remediation.

### C. Git Worktree & Branch Isolation
- Live developer or server branches remain clean.
- Auto-Remediate creates an isolated worktree/branch:
  ```bash
  git worktree add ../fix-<id> -b autoremediate/fix-<id>
  ```
- Patched files + `test_repro_<id>.py` committed, then pushed with a structured PR generated via `gh pr create`.

---

## 4. Failure Data Contract (`failures/<id>.json`)

The single standard interface connecting hooks, Sentry, agent, and verifier:

```json
{
  "id": "1",
  "timestamp": "2026-10-25T10:15:00Z",
  "task_name": "demo_app.tasks.send_receipt",
  "task_id": "edd40437-afe5-4375-b568-433081032904",
  "args": [
    {
      "id": 42,
      "customer": {"name": "Asha Rao", "email": null},
      "items": [{"price": 10.0, "qty": 2}, {"price": 4.5, "qty": 1}]
    }
  ],
  "kwargs": {},
  "exception_type": "AttributeError",
  "exception_message": "'NoneType' object has no attribute 'lower'",
  "traceback": "Traceback (most recent call last):\n...",
  "frames": [
    {
      "file": "demo_app/tasks.py",
      "line": 11,
      "function": "send_receipt",
      "code": "email = order[\"customer\"][\"email\"].lower()",
      "locals": {
        "order": "{'id': 42, 'customer': {'name': 'Asha Rao', 'email': None}, ...}"
      }
    }
  ],
  "git_sha": "abc1234"
}
```

---

## 5. Implementation Roadmap & Status Tracker

| Task | Title | Description | Status |
|---|---|---|---|
| **T0** | **Environment & Repo Align** | Move kit files to repo root, initialize `.venv`, install `requirements.txt`, configure `.env`. | `Pending` |
| **T1** | **Model Gate Script** | Build `scripts/model_check.py` to verify LLM produces valid `<final>` tags. | `Pending` |
| **T2** | **Capture Hook** | Implement `autoremediate/hook.py` (PII/secret redaction, Celery listener, `failures/<id>.json` output). | `Pending` |
| **T3** | **Sandboxed Tools** | Implement `autoremediate/tools.py` (`read_file`, `grep` with path safety). | `Pending` |
| **T4** | **Verification Engine** | Implement `autoremediate/verify.py` (temp sandbox, 4-stage pytest verification, unified diff). | `Pending` |
| **T5** | **LLM Client** | Implement `autoremediate/llm.py` (OpenAI-compatible client with automatic fallback). | `Pending` |
| **T6** | **Agent Loop** | Implement `autoremediate/agent.py` (`parse_action`, message builder, 8-turn convergence loop). | `Pending` |
| **T7** | **Rich CLI** | Implement `autoremediate/cli.py` (`list`, `fix`, `--replay`, `--apply`, `--pr`, projector-ready Rich panels). | `Pending` |
| **T8** | **Replay & PR Flow** | Offline replay verification without LLM, graceful git/gh degradation. | `Pending` |
| **T9** | **Docs & Packaging** | Complete `README.md`, `pyproject.toml`, DigitalOcean GPU vLLM guide. | `Pending` |
| **T10** | **Stretch / Generalization** | Add tests for Decimal serialization bug and flaky API restraint. | `Pending` |
| **EXT-1** | **Sentry Webhook Adapter** | Add `autoremediate/sentry.py` (normalizer + lightweight webhook listener + fixture test). | `Planned` |

---

## 6. How to Use this Document for Context Matching

1. **Before any prompt or code change**: Reference the specific task number (e.g., T2, T4) and the signatures in [SPEC.md](file:///home/sub-zero/Documents/projects/hacktoberfest_cittic/auto-remediate-kit/kit/SPEC.md).
2. **Never deviate from the schema**: All adapters (Celery, Sentry, or custom logs) must output to the exact `failures/<id>.json` schema.
3. **Always run acceptance commands**: Each task must pass its specific offline pytest acceptance suite before committing or advancing.
