"""Tests for the queue protection, user config and the cross-platform installer pieces."""
import io
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "router"))
import core  # noqa: E402
import install_hooks  # noqa: E402
import platforms as P  # noqa: E402
import queue_state  # noqa: E402
import run_hook  # noqa: E402


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(run_hook, "LOG_FILE", tmp_path / "routing.jsonl")
    monkeypatch.setattr(run_hook, "SEEN_FILE", tmp_path / "seen.json")
    monkeypatch.setattr(core, "STATE_DIR", tmp_path)
    monkeypatch.setattr(core, "BACKEND", "local")
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    return tmp_path


def hook(monkeypatch, capsys, provider, event, payload):
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(json.dumps(payload).encode("utf-8"))))
    assert run_hook.main([provider, event]) == 0
    out = capsys.readouterr().out.strip()
    return json.loads(out) if out else None


def ctx(out):
    return out["hookSpecificOutput"]["additionalContext"] if out else ""


# --- queue protection ------------------------------------------------------------------------------
def test_queue_same_session(monkeypatch, capsys):
    first = hook(monkeypatch, capsys, "claude", "UserPromptSubmit", {"prompt": "Refactor the parser module", "session_id": "s1", "cwd": "/w"})
    assert "QUEUE" not in ctx(first)
    second = hook(monkeypatch, capsys, "claude", "UserPromptSubmit", {"prompt": "Also add a README", "session_id": "s1", "cwd": "/w"})
    assert "QUEUE: 1 earlier request" in ctx(second) and "Refactor the parser module" in ctx(second)
    assert hook(monkeypatch, capsys, "claude", "Stop", {"session_id": "s1"}) is None
    third = hook(monkeypatch, capsys, "claude", "UserPromptSubmit", {"prompt": "What is TCP?", "session_id": "s1", "cwd": "/w"})
    assert "QUEUE" not in ctx(third)


def test_queue_other_session_same_folder(monkeypatch, capsys):
    hook(monkeypatch, capsys, "codex", "UserPromptSubmit", {"prompt": "Migrate the database schema", "session_id": "a", "cwd": "/repo"})
    out = hook(monkeypatch, capsys, "claude", "UserPromptSubmit", {"prompt": "Fix the login bug", "session_id": "b", "cwd": "/repo"})
    assert "CONCURRENCY: another codex session" in ctx(out)
    out = hook(monkeypatch, capsys, "claude", "UserPromptSubmit", {"prompt": "Fix the login bug", "session_id": "c", "cwd": "/other"})
    assert "CONCURRENCY" not in ctx(out)


def test_queue_private_prompt_still_protected_but_not_routed(monkeypatch, capsys):
    hook(monkeypatch, capsys, "claude", "UserPromptSubmit", {"prompt": "Build the report", "session_id": "p", "cwd": "/w"})
    out = hook(monkeypatch, capsys, "claude", "UserPromptSubmit", {"prompt": "#privat secret stuff", "session_id": "p", "cwd": "/w"})
    assert ctx(out).startswith("QUEUE") and "[router]" not in ctx(out)


def test_queue_stale_entries_expire(tmp_path, monkeypatch):
    monkeypatch.setattr(queue_state, "TTL_S", 0)
    queue_state.on_submit(tmp_path, "claude", "s", "/w", "old")
    assert queue_state.on_submit(tmp_path, "claude", "s", "/w", "new")["ahead"] == []


def test_antigravity_stop_prints_json(monkeypatch, capsys):
    assert hook(monkeypatch, capsys, "antigravity", "Stop", {"conversationId": "x", "fullyIdle": True}) == {}


# --- user config -------------------------------------------------------------------------------------
def test_jev_token_from_config_and_env(tmp_path, monkeypatch):
    assert core.jev_key() is None and not core.use_jev()
    (tmp_path / "config.json").write_text(json.dumps({"typesafe_api_key": "tok"}), encoding="utf-8")
    monkeypatch.setattr(core, "BACKEND", "auto")
    assert core.jev_key() == "tok" and core.use_jev()
    monkeypatch.setenv("TYPESAFE_API_KEY", "env")
    assert core.jev_key() == "env"


def test_model_overrides_per_account(tmp_path):
    assert "gpt-6-luna" in core.models_for("codex")
    (tmp_path / "models.local.json").write_text(json.dumps({"codex": {"gpt-6-luna": {"selectable": False},
                                                                      "gpt-6-sol": {"selectable": True}}}), encoding="utf-8")
    models = core.models_for("codex")
    assert "gpt-6-luna" not in models and "gpt-6-sol" in models and "ultra" not in models["gpt-6-sol"]["levels"]


# --- platform + installer ------------------------------------------------------------------------------
def test_link_dir_roundtrip(tmp_path):
    target = tmp_path / "hub" / "skill"
    target.mkdir(parents=True)
    (target / "SKILL.md").write_text("x", encoding="utf-8")
    link = tmp_path / "tool" / "skill"
    P.link_dir(link, target)
    assert P.is_link(link) and (link / "SKILL.md").read_text(encoding="utf-8") == "x"
    P.unlink_dir(link)
    assert not link.exists() and (target / "SKILL.md").exists()  # the target is never touched


def test_install_hooks_idempotent_and_uninstall(tmp_path, monkeypatch):
    paths = {k: tmp_path / Path(v).relative_to(P.HOME) for k, v in P.PATHS.items()}
    monkeypatch.setattr(P, "PATHS", paths)
    monkeypatch.setattr(P, "claude_desktop_config", lambda: tmp_path / "claude_desktop_config.json")
    monkeypatch.setattr(install_hooks, "SHIM_HOOK", tmp_path / "bin" / "run_hook.py")
    monkeypatch.setattr(install_hooks, "SHIM_MCP", tmp_path / "bin" / "mcp_server.py")
    paths["claude_settings"].parent.mkdir(parents=True)
    paths["claude_settings"].write_text(json.dumps({"model": "sonnet", "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "other"}]}]}}))
    assert install_hooks.install(apply=True) > 0
    assert install_hooks.install(apply=True) == 0                      # second run: nothing changes
    s = json.loads(paths["claude_settings"].read_text())
    assert s["model"] == "sonnet" and len(s["hooks"]["Stop"]) == 2      # foreign hook kept, ours added
    agy = json.loads(paths["agy_hooks"].read_text())
    assert set(agy["router"]) == {"PreInvocation", "Stop"}
    assert "jev-router" in paths["codex_config"].read_text()
    install_hooks.install(apply=True, uninstall=True)
    s = json.loads(paths["claude_settings"].read_text())
    assert s["hooks"]["Stop"] == [{"hooks": [{"type": "command", "command": "other"}]}] and "UserPromptSubmit" not in s["hooks"]
    assert "jev-router" not in paths["codex_config"].read_text()


def test_python_cmd_has_no_spaces():
    assert " " not in P.python_cmd()


def test_codex_mcp_keeps_following_generated_block(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text('model = "x"\n\n[mcp_servers.jev-router]\ncommand = "old"\nargs = ["a"]\n\n'
                   "# >>> jev-router agents (generated - edit router/targets.json, not this block)\n"
                   '[agents.a-low]\ndescription = "d"\n# <<< jev-router agents\n', encoding="utf-8")
    w = install_hooks.Writer(apply=True)
    install_hooks.codex_mcp(cfg, w)
    text = cfg.read_text(encoding="utf-8")
    assert "# >>> jev-router agents" in text and text.count("[mcp_servers.jev-router]") == 1
    w2 = install_hooks.Writer(apply=True)
    install_hooks.codex_mcp(cfg, w2)
    assert w2.changes == 0  # idempotent
    install_hooks.codex_mcp(cfg, install_hooks.Writer(apply=True), uninstall=True)
    text = cfg.read_text(encoding="utf-8")
    assert "mcp_servers.jev-router" not in text and "[agents.a-low]" in text
