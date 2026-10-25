"""Failure capture hook for Celery tasks with secret and PII redaction."""
import os
import re
import json
import linecache
import subprocess
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_INSTALLED = False

SECRET_KEY_PATTERN = re.compile(
    r"(?i)(password|passwd|secret|token|api[_-]?key|authorization|credential)"
)
EMAIL_PATTERN = re.compile(r"[\w\.-]+@[\w\.-]+\.\w+")
DIGIT_PATTERN = re.compile(r"\b\d{13,16}\b")


def _is_json_primitive(val: Any) -> bool:
    return val is None or isinstance(val, (int, float, str, bool))


def _to_json_safe(val: Any) -> Any:
    if _is_json_primitive(val):
        return val
    if isinstance(val, dict):
        return {str(k): _to_json_safe(v) for k, v in val.items()}
    if isinstance(val, (list, tuple, set)):
        return [_to_json_safe(x) for x in val]
    return repr(val)


def redact(value: Any, key: str | None = None) -> Any:
    """Mask password/token keys at depth, mask emails and long digit strings if REDACT_PII=1.
    
    Leaves None and numbers alone.
    """
    if key and SECRET_KEY_PATTERN.search(key):
        return "[REDACTED]"

    if value is None or isinstance(value, (int, float, bool)):
        return value

    if isinstance(value, str):
        redact_pii = os.getenv("REDACT_PII", "1") == "1"
        if redact_pii:
            masked = EMAIL_PATTERN.sub("[REDACTED_EMAIL]", value)
            masked = DIGIT_PATTERN.sub("[REDACTED_NUMBER]", masked)
            return masked
        return value

    if isinstance(value, dict):
        return {k: redact(v, key=str(k)) for k, v in value.items()}

    if isinstance(value, (list, tuple)):
        redacted_list = [redact(item, key=key) for item in value]
        return redacted_list if isinstance(value, list) else tuple(redacted_list)

    return value


def _get_git_sha() -> str:
    try:
        res = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip()
    except Exception:
        pass
    return "unknown"


def capture_failure(
    exc: BaseException,
    task_name: str,
    args: Any,
    kwargs: Any,
    task_id: str | None = None,
    tb: Any = None,
    failures_dir: Path | str | None = None,
) -> Path:
    """Capture exception details and write a failure record to JSON."""
    try:
        dir_path = Path(failures_dir or os.getenv("FAILURES_DIR", "failures")).resolve()
        dir_path.mkdir(parents=True, exist_ok=True)

        existing_ids = []
        for p in dir_path.glob("*.json"):
            if p.stem.isdigit():
                existing_ids.append(int(p.stem))
        next_id = max(existing_ids, default=0) + 1
        id_str = str(next_id)

        target_file = dir_path / f"{id_str}.json"

        real_tb = tb if tb is not None else getattr(exc, "__traceback__", None)
        traceback_str = "".join(traceback.format_exception(type(exc), exc, real_tb))

        repo_root = Path.cwd().resolve()
        frames = []
        if real_tb is not None:
            for f, lineno in traceback.walk_tb(real_tb):
                co = f.f_code
                filename = co.co_filename
                frame_path = Path(filename).resolve()

                try:
                    rel_path = frame_path.relative_to(repo_root)
                except ValueError:
                    continue

                rel_str = str(rel_path)
                if (
                    "site-packages" in rel_str
                    or rel_str.startswith(".venv")
                    or "celery" in rel_str
                ):
                    continue

                code_line = linecache.getline(str(frame_path), lineno).strip()
                frame_locals = {}
                for k, v in f.f_locals.items():
                    safe_v = redact(v, key=str(k))
                    frame_locals[str(k)] = repr(safe_v)[:300]

                frames.append(
                    {
                        "file": rel_str,
                        "line": lineno,
                        "function": co.co_name,
                        "code": code_line,
                        "locals": frame_locals,
                    }
                )

        clean_args = _to_json_safe(redact(args))
        clean_kwargs = _to_json_safe(redact(kwargs))

        failure_record = {
            "id": id_str,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "task_name": task_name,
            "task_id": str(task_id) if task_id else "",
            "args": clean_args if isinstance(clean_args, list) else [clean_args],
            "kwargs": clean_kwargs if isinstance(clean_kwargs, dict) else {},
            "exception_type": type(exc).__name__,
            "exception_message": str(exc),
            "traceback": traceback_str,
            "frames": frames,
            "git_sha": _get_git_sha(),
        }

        target_file.write_text(json.dumps(failure_record, indent=2), encoding="utf-8")
        return target_file
    except Exception:
        # Guarantee hook never raises to protect workers
        fallback = Path(failures_dir or "failures") / "error.json"
        return fallback


def on_task_failure(
    sender: Any = None,
    task_id: str | None = None,
    exception: BaseException | None = None,
    args: Any = None,
    kwargs: Any = None,
    traceback: Any = None,
    einfo: Any = None,
    **kw: Any,
) -> None:
    """Celery signal handler for task_failure."""
    try:
        if exception is None:
            return
        task_name = getattr(sender, "name", str(sender)) if sender else "unknown"
        capture_failure(
            exc=exception,
            task_name=task_name,
            args=args or (),
            kwargs=kwargs or {},
            task_id=str(task_id) if task_id else None,
            tb=traceback,
        )
    except Exception:
        pass


def install() -> None:
    """Connect Celery task_failure signal handler idempotently."""
    global _INSTALLED
    if _INSTALLED:
        return
    try:
        from celery.signals import task_failure

        task_failure.connect(on_task_failure, weak=False)
        _INSTALLED = True
    except Exception:
        pass


def load_failure(failure_id: str, failures_dir: Path | str | None = None) -> dict:
    """Load a failure record by ID. Raises FileNotFoundError if missing."""
    dir_path = Path(failures_dir or os.getenv("FAILURES_DIR", "failures")).resolve()
    target_file = dir_path / f"{failure_id}.json"
    if not target_file.exists():
        raise FileNotFoundError(f"Failure record {failure_id} not found at {target_file}")
    return json.loads(target_file.read_text(encoding="utf-8"))


def list_failures(failures_dir: Path | str | None = None) -> list[dict]:
    """List all captured failure records, newest first."""
    dir_path = Path(failures_dir or os.getenv("FAILURES_DIR", "failures")).resolve()
    if not dir_path.exists():
        return []

    failures = []
    files = sorted(
        dir_path.glob("*.json"),
        key=lambda p: int(p.stem) if p.stem.isdigit() else -1,
        reverse=True,
    )
    for p in files:
        if p.stem.isdigit():
            try:
                failures.append(json.loads(p.read_text(encoding="utf-8")))
            except Exception:
                continue
    return failures
