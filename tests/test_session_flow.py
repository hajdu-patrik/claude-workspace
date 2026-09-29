"""Continuation of the previous decision, pasted blocks, subagent hand-backs and the new Claude events."""
import io
import json
import sys

import pytest

from jev_router import core, hooks as run_hook, queue_state


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(run_hook, "LOG_FILE", tmp_path / "routing.jsonl")
    monkeypatch.setattr(run_hook, "SUBAGENT_LOG", tmp_path / "subagents.jsonl")
    monkeypatch.setattr(run_hook, "SEEN_FILE", tmp_path / "seen.json")
    monkeypatch.setattr(core, "STATE_DIR", tmp_path)
    monkeypatch.setattr(core, "BACKEND", "local")
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    return tmp_path


def hook(monkeypatch, capsys, event, payload, provider="claude"):
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(json.dumps(payload).encode("utf-8"))))
    assert run_hook.main([provider, event]) == 0
    out = capsys.readouterr().out.strip()
    return json.loads(out)["hookSpecificOutput"]["additionalContext"] if out else ""


def last_log(tmp_path, name="routing.jsonl"):
    return json.loads((tmp_path / name).read_text(encoding="utf-8").splitlines()[-1])


@pytest.mark.parametrize("prompt, expected", [
    ("mehet", True), ("igen torold!", True), ("Mehet fel!", True), ("hogy allunk?", True), ("yes, do it", True),
    ("go ahead", True), ("Try again", True), ("koszi, mehet tovabb", True),
    ("ok, most irj teszteket", False), ("igen, de irj egy uj modult a backendre", False),
    ("irj egy fuggvenyt", False), ('mehet <pasted_content id="1"> log </pasted_content id="1">', False),
])
def test_is_continuation(prompt, expected):
    assert core.is_continuation(prompt) is expected


def test_go_ahead_keeps_the_previous_decision(tmp_path, monkeypatch, capsys):
    first = hook(monkeypatch, capsys, "UserPromptSubmit",
                 {"prompt": "Refaktoráld az egész backendet hexagonális architektúrára", "session_id": "s", "cwd": "/w"})
    before = last_log(tmp_path)
    hook(monkeypatch, capsys, "Stop", {"session_id": "s"})
    text = hook(monkeypatch, capsys, "UserPromptSubmit", {"prompt": "mehet", "session_id": "s", "cwd": "/w"})
    after = last_log(tmp_path)
    assert after["backend"] == "continuation"
    assert (after["primary"], after["effort"], after["level"]) == (before["primary"], before["effort"], before["level"])
    assert after["target_agent"] == before["target_agent"] and after["target_agent"] in first
    assert "continues the previous request" in text


def test_go_ahead_without_history_is_routed_normally(tmp_path, monkeypatch, capsys):
    hook(monkeypatch, capsys, "UserPromptSubmit", {"prompt": "mehet", "session_id": "fresh", "cwd": "/w"})
    assert last_log(tmp_path)["backend"] == "local"


def test_continuation_is_per_session_and_ends_with_the_session(tmp_path, monkeypatch, capsys):
    hook(monkeypatch, capsys, "UserPromptSubmit", {"prompt": "Write unit tests for the parser", "session_id": "a", "cwd": "/w"})
    hook(monkeypatch, capsys, "UserPromptSubmit", {"prompt": "yes", "session_id": "b", "cwd": "/w"})
    assert last_log(tmp_path)["backend"] == "local"
    hook(monkeypatch, capsys, "SessionEnd", {"session_id": "a"})
    assert queue_state.recall(tmp_path, "claude", "a") is None


def test_safety_is_judged_on_the_go_ahead_itself(tmp_path, monkeypatch, capsys):
    hook(monkeypatch, capsys, "UserPromptSubmit", {"prompt": "Írj egy parser modult", "session_id": "d", "cwd": "/w"})
    text = hook(monkeypatch, capsys, "UserPromptSubmit", {"prompt": "igen torold!", "session_id": "d", "cwd": "/w"})
    assert last_log(tmp_path)["backend"] == "continuation"
    assert "SAFETY" in text


def test_pasted_block_does_not_decide_language_or_task():
    prompt = ('Valami hiba van a pipeline-ban, javitsd: <pasted_content id="p"> Some checks were not successful. '
              'Process completed with exit code 1. The job failed on ubuntu-latest. </pasted_content id="p">')
    d, text, _, _ = core.route(prompt, "claude")
    assert d["lang"] == "hu"
    assert d["task"] == "code"
    assert core.split_pasted(prompt)[0].startswith("Valami hiba")


def test_pasted_command_still_counts_for_safety():
    d, text, hit, _ = core.route('futtasd le ezt: <pasted_content id="p"> rm -rf /var/data </pasted_content id="p">', "claude")
    assert hit and "SAFETY" in text


def test_subagent_hand_back_is_not_routed(tmp_path, monkeypatch, capsys):
    text = hook(monkeypatch, capsys, "UserPromptSubmit",
                {"prompt": '<agent-message from="abc"> [Subagent hand-back] final report ...', "session_id": "h"})
    assert text == ""
    assert not (tmp_path / "routing.jsonl").exists()


def test_stop_failure_releases_the_queue(monkeypatch, capsys):
    hook(monkeypatch, capsys, "UserPromptSubmit", {"prompt": "Refactor the parser module", "session_id": "f", "cwd": "/w"})
    hook(monkeypatch, capsys, "StopFailure", {"session_id": "f", "error_type": "rate_limit"})
    text = hook(monkeypatch, capsys, "UserPromptSubmit", {"prompt": "Add a README", "session_id": "f", "cwd": "/w"})
    assert "QUEUE" not in text


def test_subagent_start_is_logged_for_compliance(tmp_path, monkeypatch, capsys):
    assert hook(monkeypatch, capsys, "SubagentStart",
                {"session_id": "c", "prompt_id": "p1", "agent_type": "opus-worker-high"}) == ""
    entry = last_log(tmp_path, "subagents.jsonl")
    assert entry["agent_type"] == "opus-worker-high" and entry["prompt_id"] == "p1"
    assert entry["session"] and entry["session"] != "c"


def test_agents_md_marks_a_foreign_project(tmp_path):
    here, other = tmp_path / "here", tmp_path / "other"
    here.mkdir()
    (here / "CLAUDE.md").write_text("x", encoding="utf-8")
    (other / "src").mkdir(parents=True)
    (other / "AGENTS.md").write_text("x", encoding="utf-8")
    note = core.foreign_project_note(f"look at {other / 'src' / 'main.py'}", str(here))
    assert note and str(other) in note


def test_delegation_compliance_counts():
    from jev_router import doctor
    routed = [
        {"provider": "claude", "session": "s", "prompt_id": "a", "ts": "2026-09-29T10:00:00", "target_agent": "opus-worker-high"},
        {"provider": "claude", "session": "s", "prompt_id": "b", "ts": "2026-09-29T10:05:00", "target_agent": "sonnet-worker-low"},
        {"provider": "claude", "session": "s", "ts": "2026-09-29T10:10:00", "target_agent": "opus-worker-max"},
        {"provider": "claude", "session": "s", "ts": "2026-09-29T10:20:00"},
    ]
    subagents = [
        {"session": "s", "prompt_id": "a", "ts": "2026-09-29T10:01:00", "agent_type": "opus-worker-high"},
        {"session": "s", "prompt_id": "b", "ts": "2026-09-29T10:06:00", "agent_type": "Explore"},
        {"session": "s", "ts": "2026-09-29T10:21:00", "agent_type": "opus-worker-max"},
    ]
    assert doctor.delegation_compliance(routed, subagents) == {"followed": 1, "elsewhere": 1, "in_session": 1}
