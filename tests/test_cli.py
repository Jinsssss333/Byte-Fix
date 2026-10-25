"""Tests for autoremediate.cli."""
import json
import shutil
from pathlib import Path
from typer.testing import CliRunner

from autoremediate.cli import app
from autoremediate.agent import AgentResult, parse_action

runner = CliRunner()


def test_cli_unknown_id(tmp_path, monkeypatch):
    monkeypatch.setenv("FAILURES_DIR", str(tmp_path))
    res = runner.invoke(app, ["fix", "999"])
    assert res.exit_code == 2
    assert "not found" in res.output.lower()


def test_cli_list_command(tmp_path, monkeypatch):
    monkeypatch.setenv("FAILURES_DIR", str(tmp_path))
    # Empty
    res_empty = runner.invoke(app, ["list"])
    assert "No captured failures found" in res_empty.output

    # With one failure
    fixture_path = Path("tests/fixtures/failure_1.json")
    shutil.copy(fixture_path, tmp_path / "1.json")

    res = runner.invoke(app, ["list"])
    assert res.exit_code == 0
    assert "send_receipt" in res.output


def test_cli_fix_failure_1_verified(tmp_path, monkeypatch):
    failures_dir = tmp_path / "failures"
    recordings_dir = tmp_path / "recordings"
    failures_dir.mkdir()
    recordings_dir.mkdir()

    monkeypatch.setenv("FAILURES_DIR", str(failures_dir))
    monkeypatch.setenv("RECORDINGS_DIR", str(recordings_dir))

    shutil.copy(Path("tests/fixtures/failure_1.json"), failures_dir / "1.json")
    canned_final = Path("tests/fixtures/canned_final_1.txt").read_text(encoding="utf-8")
    final_payload = parse_action(canned_final)

    # Monkeypatch run_agent
    def fake_run_agent(failure, repo_root, on_step=None, max_steps=8):
        if on_step:
            on_step({"step": 1, "type": "final", "detail": "test", "model": "fake-model"})
        return AgentResult(kind="final", payload=final_payload, trace=[{"step": 1, "type": "final", "detail": "test", "model": "fake-model"}])

    monkeypatch.setattr("autoremediate.cli.run_agent", fake_run_agent)

    res = runner.invoke(app, ["fix", "1"])
    assert res.exit_code == 0
    assert "VERIFIED" in res.output
    assert "FAILED before patch" in res.output
    assert "PASSED after patch" in res.output
    assert "Pull Request Description" in res.output

    # Verify recording was saved
    rec_file = recordings_dir / "run_1.json"
    assert rec_file.exists()


def test_cli_fix_failure_3_give_up(tmp_path, monkeypatch):
    failures_dir = tmp_path / "failures"
    failures_dir.mkdir()
    monkeypatch.setenv("FAILURES_DIR", str(failures_dir))

    shutil.copy(Path("tests/fixtures/failure_3_external.json"), failures_dir / "3.json")
    canned_giveup = Path("tests/fixtures/canned_giveup_3.txt").read_text(encoding="utf-8")
    giveup_payload = parse_action(canned_giveup)

    def fake_run_agent(failure, repo_root, on_step=None, max_steps=8):
        return AgentResult(kind="give_up", payload=giveup_payload, trace=[])

    monkeypatch.setattr("autoremediate.cli.run_agent", fake_run_agent)

    res = runner.invoke(app, ["fix", "3"])
    assert res.exit_code == 1
    assert "COULD NOT REPRODUCE" in res.output
    assert "Proposed Patch" not in res.output


def test_cli_replay_works_without_llm(tmp_path, monkeypatch):
    failures_dir = tmp_path / "failures"
    recordings_dir = tmp_path / "recordings"
    failures_dir.mkdir()
    recordings_dir.mkdir()

    monkeypatch.setenv("FAILURES_DIR", str(failures_dir))
    monkeypatch.setenv("RECORDINGS_DIR", str(recordings_dir))

    shutil.copy(Path("tests/fixtures/failure_1.json"), failures_dir / "1.json")
    canned_final = Path("tests/fixtures/canned_final_1.txt").read_text(encoding="utf-8")
    final_payload = parse_action(canned_final)

    # Save recording
    rec_data = {
        "failure_id": "1",
        "final": final_payload,
        "trace": [{"step": 1, "type": "final", "detail": "canned", "model": "test-model"}],
    }
    (recordings_dir / "run_1.json").write_text(json.dumps(rec_data), encoding="utf-8")

    # Monkeypatch run_agent to raise to ensure it is never called
    def error_run_agent(*args, **kwargs):
        raise RuntimeError("LLM called during replay!")

    monkeypatch.setattr("autoremediate.cli.run_agent", error_run_agent)

    res = runner.invoke(app, ["fix", "1", "--replay"])
    assert res.exit_code == 0
    assert "Loaded replay" in res.output
    assert "VERIFIED" in res.output


def test_cli_apply_only_on_verified(tmp_path, monkeypatch):
    failures_dir = tmp_path / "failures"
    recordings_dir = tmp_path / "recordings"
    failures_dir.mkdir()
    recordings_dir.mkdir()

    monkeypatch.setenv("FAILURES_DIR", str(failures_dir))
    monkeypatch.setenv("RECORDINGS_DIR", str(recordings_dir))

    shutil.copy(Path("tests/fixtures/failure_1.json"), failures_dir / "1.json")
    canned_final = Path("tests/fixtures/canned_final_1.txt").read_text(encoding="utf-8")
    final_payload = parse_action(canned_final)

    applied_called = False

    def fake_apply_patch(final, repo_root):
        nonlocal applied_called
        applied_called = True
        return ["demo_app/tasks.py"]

    monkeypatch.setattr("autoremediate.cli.apply_patch_to_repo", fake_apply_patch)

    def fake_run_agent(failure, repo_root, on_step=None, max_steps=8):
        return AgentResult(kind="final", payload=final_payload, trace=[])

    monkeypatch.setattr("autoremediate.cli.run_agent", fake_run_agent)

    res = runner.invoke(app, ["fix", "1", "--apply"])
    assert res.exit_code == 0
    assert applied_called is True
    assert "Applied patch to 1 file(s)" in res.output


def test_cli_pr_graceful_on_gh_missing(tmp_path, monkeypatch):
    failures_dir = tmp_path / "failures"
    recordings_dir = tmp_path / "recordings"
    failures_dir.mkdir()
    recordings_dir.mkdir()

    monkeypatch.setenv("FAILURES_DIR", str(failures_dir))
    monkeypatch.setenv("RECORDINGS_DIR", str(recordings_dir))

    shutil.copy(Path("tests/fixtures/failure_1.json"), failures_dir / "1.json")
    canned_final = Path("tests/fixtures/canned_final_1.txt").read_text(encoding="utf-8")
    final_payload = parse_action(canned_final)

    monkeypatch.setattr("autoremediate.cli.apply_patch_to_repo", lambda final, repo_root: ["demo_app/tasks.py"])

    def fake_run_agent(failure, repo_root, on_step=None, max_steps=8):
        return AgentResult(kind="final", payload=final_payload, trace=[])

    monkeypatch.setattr("autoremediate.cli.run_agent", fake_run_agent)

    # Fake subprocess.run to simulate gh missing
    import subprocess
    original_subprocess_run = subprocess.run

    def fake_subp_run(cmd, *args, **kwargs):
        if cmd[0] == "gh":
            raise FileNotFoundError("gh command not found")
        if cmd[0] == "git":
            return original_subprocess_run(["echo", "ok"], capture_output=True, text=True)
        return original_subprocess_run(cmd, *args, **kwargs)

    monkeypatch.setattr("subprocess.run", fake_subp_run)

    repro_file = Path("demo_app/tests/test_repro_1.py")
    try:
        res = runner.invoke(app, ["fix", "1", "--pr"])
        assert res.exit_code == 0
        assert "Manual steps to create PR" in res.output
        assert "gh pr create" in res.output
    finally:
        if repro_file.exists():
            repro_file.unlink()


def test_cli_serve_webhook_command(monkeypatch):
    class FakeServer:
        def __init__(self, addr, handler):
            self.addr = addr
            self.handler = handler

        def serve_forever(self):
            raise KeyboardInterrupt()

        def server_close(self):
            pass

    monkeypatch.setattr("http.server.HTTPServer", FakeServer)
    res = runner.invoke(app, ["serve-webhook", "--port", "9999"])
    assert res.exit_code == 0
    assert "9999" in res.output
    assert "Webhook server stopped" in res.output

