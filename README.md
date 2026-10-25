# Auto-Remediate

**Auto-Remediate** is an automated self-healing terminal tool for Python Celery background workers and web services. When an unhandled exception crashes a task in staging or production, Auto-Remediate intercepts the failure, extracts clean stack traces and frame locals with automated PII/secret scrubbing, prompts an open-weight LLM (Gemma 4 via Gemini API or self-hosted vLLM) to write a failing reproducer test and a code patch, and **proves the fix by running pytest in an isolated temporary sandbox** before presenting a projector-ready review and opening a Pull Request.

---

## How It Works: The 6-Stage Remediation Loop

```
[1. Failure Capture] ──> [2. Agent Diagnosis] ──> [3. Tool Exploration]
         │                         │                        │
         ▼                         ▼                        ▼
  Celery Signal / Sentry    Gemma 4 / OpenAI API     Read-only (read_file, grep)
  Redacts secrets & PII     Produces test + patch    Prevents traversal / escapes
         │                         │                        │
         └─────────────────────────┼────────────────────────┘
                                   │
                                   ▼
                       [4. Verification Sandbox]
                                   │
                   ┌───────────────┴───────────────┐
                   │ Stage 1: Reproducer fails     │
                   │ Stage 2: Patch applied        │
                   │ Stage 3: Reproducer passes    │
                   │ Stage 4: Test suite passes    │
                   └───────────────┬───────────────┘
                                   │
                                   ▼
                        [5. Unified Git Diff]
                                   │
                                   ▼
                     [6. Terminal Review & PR]
```

1. **Capture**: Intercepts `celery.signals.task_failure` (or Sentry issue webhooks). Filters out framework frames, redacts secrets (`password`, `api_key`, `token`) and PII (emails, 13–16 digit numbers), and saves `failures/<id>.json`.
2. **Diagnose**: Assembles a rich contextual prompt with innermost source code, locals, and arguments. The LLM identifies the root cause and provides line-by-line evidence.
3. **Explore**: If additional context is needed, the agent invokes sandboxed, read-only tools (`read_file`, `grep`) bounded within the repository root.
4. **Reproduce & Patch**: The model outputs a minimal standalone `pytest` reproducer test (`test_repro_<id>.py`) alongside complete replacement contents for the failing source files.
5. **Verify (Code-Driven Ground Truth)**: A clean copy of the repo is generated in a temporary sandbox. The reproducer **must fail** before the patch, the patch is applied, the reproducer **must pass**, and the existing test suite **must pass without regressions**.
6. **Deliver**: Displays rich formatted panels with highlighted locals, step traces, syntax-highlighted diffs, and opens a git branch and pull request via the GitHub CLI (`gh`).

---

## Quickstart

### 1. Installation
Clone the repository and install dependencies in a virtual environment:

```bash
git clone https://github.com/Jinsssss333/hacktoberfest_cittic.git
cd hacktoberfest_cittic
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
```

### 2. Configure Environment
Copy `.env.example` to `.env`:

```bash
cp .env.example .env
```

Configure your LLM provider credentials in `.env`:
```env
LLM_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/
LLM_MODEL=gemini-2.5-flash
LLM_API_KEY=YOUR_GEMINI_API_KEY
```

### 3. Trigger a Demo Task Crash
Run the demo Celery task in eager mode to simulate an unhandled crash:

```bash
python -m demo_app.trigger --case null
```

### 4. Review & Remediate
List captured failures:

```bash
python -m autoremediate.cli list
```

Run the interactive remediation and verification engine:

```bash
python -m autoremediate.cli fix 1
```

Or replay an existing run offline without calling the LLM:

```bash
python -m autoremediate.cli fix 1 --replay
```

To apply verified patches directly or create a pull request:

```bash
python -m autoremediate.cli fix 1 --apply
python -m autoremediate.cli fix 1 --pr
```

### 5. Sentry Webhook Ingestion
Auto-Remediate can listen for incoming Sentry crash webhooks:

```bash
# Start the HTTP webhook server:
python -m autoremediate.cli serve-webhook --port 8000

# Verify the Sentry pipeline with HMAC signature verification:
python scripts/verify_sentry.py
```

---

## Model Configuration

Auto-Remediate supports any OpenAI-compatible API endpoint:
- **Default (Google AI Studio)**: Gemma 4 / Gemini models via Google AI Studio's OpenAI-compatible endpoint.
- **Self-Hosted (DigitalOcean GPU / vLLM)**: Run open-weight models locally on dedicated GPU infrastructure. See [docs/digitalocean-gpu.md](docs/digitalocean-gpu.md) for setup instructions.
- **Fallback Rollover**: Configure `FALLBACK_BASE_URL`, `FALLBACK_MODEL`, and `FALLBACK_API_KEY` in `.env` to automatically failover if the primary provider encounters rate limits or connection errors.

---

## Safety Model

- **Temporary Sandbox Execution**: All reproducer tests and patches run inside an isolated `tempfile` copy. Real repository files are never modified until all 4 verification stages pass and `--apply` or `--pr` is explicitly specified.
- **Secret & PII Redaction**: Passwords, authorization tokens, API keys, emails, and credit card numbers are scrubbed at depth before records touch disk or LLM prompts.
- **Zero-Crash Signal Hook**: The Celery hook wraps all operations in fault-tolerant exception handlers. It can never interrupt or crash worker task loops.
- **Human-in-the-Loop PRs**: Auto-Remediate generates pull requests with full reproduction evidence for human developer review; it never performs auto-merges to production branches.

---

## Known Limitations

- **Celery & Sentry Focused**: Current native capture hooks target Celery signals and Sentry webhook events.
- **Read-Only Exploration**: Tool calling is restricted to read-only `read_file` and `grep` within the repo root; no write tools exist during the agent phase.
- **No Live DB State Inspection**: Operates purely on captured frame locals and arguments rather than inspecting live database records.
- **Subprocess Isolation**: Relies on temporary directories and separate Python processes; full Docker/container isolation is on the roadmap.

---

## Roadmap

- [x] Celery failure capture hook with secret & PII redaction
- [x] Safe exploration tools (`read_file`, `grep`) with directory traversal protection
- [x] 4-stage sandbox verification engine via pytest
- [x] Rich terminal interface with offline replay support
- [x] GitHub PR creation and branch management
- [x] Sentry APM Webhook ingestion adapter (`autoremediate/sentry.py`)
- [ ] Docker containerized test execution sandbox
- [ ] Interactive terminal diff review and staging

---

*This project was built with OpenCode.*