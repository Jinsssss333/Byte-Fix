<div align="center">

# 🩺 Auto-Remediate

**Self-healing for Python Celery workers and web services, powered by open-weight LLMs.**

![Python](https://img.shields.io/badge/python-3.10%2B-blue?logo=python&logoColor=white)
![License: MIT](https://img.shields.io/badge/license-MIT-green)
![Hacktoberfest](https://img.shields.io/badge/Hacktoberfest-friendly-ff69b4)
![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen)

</div>

---

## 📖 Overview

When an unhandled exception crashes a task in staging or production, **Auto-Remediate** steps in:

1. Captures the failure and scrubs secrets and PII.
2. Asks an open-weight LLM (Gemma 4 via the Gemini API, or self-hosted vLLM) for a **failing reproducer test** and a **code patch**.
3. **Proves the fix** by running `pytest` in an isolated temporary sandbox.
4. Presents a terminal review and opens a Pull Request for a human to approve.

Nothing is merged automatically, and nothing touches your real repository until the fix is verified.

## ✨ Features

- 🎣 **Failure capture** via Celery `task_failure` signals or Sentry webhooks
- 🔒 **Automatic redaction** of passwords, tokens, API keys, emails, and card-like numbers
- 🧠 **LLM diagnosis** with root cause and line-by-line evidence
- 🔍 **Safe exploration tools** (`read_file`, `grep`), read-only and confined to the repo root
- ✅ **4-stage sandbox verification** powered by pytest
- 🖥️ **Rich terminal UI** with highlighted locals, traces, and diffs
- 🔁 **Offline replay** of previous runs without calling the LLM
- 🔀 **Provider fallback** if the primary LLM hits rate limits or connection errors
- 📬 **GitHub PR creation** through the `gh` CLI

## 🔄 How It Works: The 6-Stage Remediation Loop

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

| Stage | What happens |
|-------|--------------|
| **Capture** | Intercepts `celery.signals.task_failure` (or Sentry issue webhooks), filters framework frames, redacts secrets/PII, and saves `failures/<id>.json`. |
| **Diagnose** | Builds a prompt from the innermost source code, locals, and arguments. The LLM identifies the root cause. |
| **Explore** | If more context is needed, the agent uses read-only tools bounded to the repository root. |
| **Reproduce & Patch** | The model outputs a minimal `pytest` reproducer (`test_repro_<id>.py`) plus replacement contents for the failing source files. |
| **Verify** | In a clean temp copy of the repo: the reproducer must fail, the patch is applied, the reproducer must pass, and the existing suite must pass with no regressions. |
| **Deliver** | Shows formatted panels and a syntax-highlighted diff, then creates a branch and PR via `gh`. |

## 🚀 Quickstart

### Prerequisites

- Python 3.10+
- [GitHub CLI (`gh`)](https://cli.github.com/), only needed for `--pr`
- An API key for a Gemini or OpenAI-compatible endpoint, or a self-hosted vLLM server

### 1. Install

```bash
git clone https://github.com/Jinsssss333/hacktoberfest_cittic.git
cd hacktoberfest_cittic
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
```

### 2. Configure

```bash
cp .env.example .env
```

Edit `.env` with your provider credentials:

```env
LLM_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/
LLM_MODEL=gemini-2.5-flash
LLM_API_KEY=YOUR_GEMINI_API_KEY
```

### 3. Trigger a demo crash

Run the demo Celery task in eager mode to simulate an unhandled failure:

```bash
python -m demo_app.trigger --case null
```

### 4. Review and remediate

```bash
# List captured failures
python -m autoremediate.cli list

# Interactive remediation + verification
python -m autoremediate.cli fix 1

# Replay a previous run offline (no LLM call)
python -m autoremediate.cli fix 1 --replay

# Apply the verified patch locally, or open a PR
python -m autoremediate.cli fix 1 --apply
python -m autoremediate.cli fix 1 --pr
```

### 5. Sentry webhook ingestion (optional)

```bash
# Start the webhook server
python -m autoremediate.cli serve-webhook --port 8000

# Verify the pipeline with HMAC signature checking
python scripts/verify_sentry.py
```

## ⚙️ Model Configuration

Auto-Remediate works with any OpenAI-compatible endpoint.

| Option | Description |
|--------|-------------|
| **Google AI Studio** (default) | Gemma 4 / Gemini models through the OpenAI-compatible endpoint. |
| **Self-hosted (vLLM / GPU)** | Run open-weight models on your own hardware. See [`docs/digitalocean-gpu.md`](docs/digitalocean-gpu.md). |
| **Fallback rollover** | Set `FALLBACK_BASE_URL`, `FALLBACK_MODEL`, and `FALLBACK_API_KEY` to fail over automatically on rate limits or connection errors. |

## 🛡️ Safety Model

- **Sandboxed execution**: reproducers and patches run in an isolated `tempfile` copy. Real files change only after all 4 verification stages pass *and* you pass `--apply` or `--pr`.
- **Secret and PII redaction**: scrubbed before anything is written to disk or sent to the LLM.
- **Zero-crash hook**: the Celery hook wraps everything in fault-tolerant handlers, so it can never interrupt a worker loop.
- **Human in the loop**: PRs include full reproduction evidence. There are no auto-merges.

## 📁 Project Structure

```
.
├── autoremediate/   # Core package: capture, diagnosis, sandbox, CLI, Sentry adapter
├── demo_app/        # Demo Celery app for triggering sample crashes
├── scripts/         # Helper scripts (e.g. Sentry verification)
├── tests/           # Test suite
├── docs/            # Additional documentation (e.g. GPU hosting)
├── failures/        # Captured failure records (generated at runtime)
├── recordings/      # Saved runs for offline replay
├── ARCHITECTURE_PLAN.md
├── SPEC.md
├── TASKS.md
└── .env.example
```

## 🧪 Running Tests

```bash
pytest
```

## 🤝 Contributing

Contributions are welcome, and the project is **Hacktoberfest-friendly**! 🎃

1. **Fork** the repository and create a branch:
   ```bash
   git checkout -b feature/your-feature-name
   ```
2. Make your changes and add tests where relevant.
3. Run `pytest` and make sure everything passes.
4. Commit with a clear message and push your branch.
5. Open a **Pull Request** describing what you changed and why.

Not sure where to start? Check [`TASKS.md`](TASKS.md) and the [issue tracker](https://github.com/Jinsssss333/hacktoberfest_cittic/issues), or pick something from the roadmap below.

## 🗺️ Roadmap

- [x] Celery failure capture hook with secret & PII redaction
- [x] Safe exploration tools (`read_file`, `grep`) with traversal protection
- [x] 4-stage sandbox verification engine via pytest
- [x] Rich terminal interface with offline replay
- [x] GitHub PR creation and branch management
- [x] Sentry webhook ingestion adapter
- [ ] Docker-containerized test execution sandbox
- [ ] Interactive terminal diff review and staging
- [ ] Capture hooks for more frameworks beyond Celery and Sentry

## ⚠️ Known Limitations

- Native capture hooks currently target Celery and Sentry only.
- Agent tools are read-only (`read_file`, `grep`) and limited to the repo root.
- No live database inspection; it works from captured frame locals and arguments.
- Isolation uses temp directories and separate processes, not full containers (yet).

## 📄 License

Released under the [MIT License](LICENSE).

---

<div align="center">
<sub>Built with OpenCode.</sub>
</div>
