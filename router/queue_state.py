#!/usr/bin/env python3
"""Queue protection: a new prompt must never stop, restart or overwrite work still in progress.

All three tools already queue messages typed while the agent is busy (Claude Code, Codex and
Antigravity's "Queued Messages: Queue" default). What they don't do is tell the model that the new
message arrived while earlier work is unfinished - so a model may abandon or redo it. This module
tracks work in flight and turns it into explicit context:

  UserPromptSubmit (Antigravity: first PreInvocation of a turn) -> on_submit(): register the request;
      if the same session still has unfinished requests -> "QUEUE: ... finish it first"
      if ANOTHER session is working in the same folder     -> "CONCURRENCY: ... don't touch its files"
  Stop (all three tools)                                     -> on_stop(): the session is idle again.

State lives in ~/.jev-router/state/inflight.json, guarded by a lock file; entries older than
ROUTER_QUEUE_TTL_MIN (default 120) are treated as stale (crashed sessions never block anyone).
"""
import json
import os
import time
from pathlib import Path

TTL_S = 60 * float(os.environ.get("ROUTER_QUEUE_TTL_MIN", "120"))
LOCK_WAIT_S = 2.0


def _paths(state_dir):
    d = Path(state_dir) / "state"
    return d / "inflight.json", d / "inflight.lock"


class _Lock:
    """Cross-platform lock via O_EXCL lock file; a lock older than 10 s is considered abandoned."""

    def __init__(self, path):
        self.path = path

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.time() + LOCK_WAIT_S
        while True:
            try:
                os.close(os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
                return self
            except FileExistsError:
                try:
                    if time.time() - self.path.stat().st_mtime > 10:
                        self.path.unlink()
                        continue
                except OSError:
                    pass
                if time.time() > deadline:
                    return self  # never block a prompt: proceed without the lock
                time.sleep(0.05)

    def __exit__(self, *exc):
        try:
            self.path.unlink()
        except OSError:
            pass


def _load(path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _prune(data, now):
    for key in list(data):
        data[key] = [e for e in data[key] if now - e.get("ts", 0) < TTL_S]
        if not data[key]:
            del data[key]


def on_submit(state_dir, provider, session_id, cwd, summary):
    """Register a new request. Returns {"ahead": [entries still running in this session],
    "others": [entries of OTHER sessions working in the same folder]}."""
    if not session_id:
        return {"ahead": [], "others": []}
    path, lock = _paths(state_dir)
    now = time.time()
    key = f"{provider}:{session_id}"
    norm_cwd = os.path.normcase(os.path.abspath(cwd)) if cwd else ""
    with _Lock(lock):
        data = _load(path)
        _prune(data, now)
        ahead = list(data.get(key, []))
        others = [e for k, v in data.items() if k != key for e in v[:1] if norm_cwd and e.get("cwd") == norm_cwd]
        data.setdefault(key, []).append({"ts": now, "cwd": norm_cwd, "summary": summary, "provider": provider})
        try:
            path.write_text(json.dumps(data), encoding="utf-8")
        except OSError:
            pass
    return {"ahead": ahead, "others": others}


def on_stop(state_dir, provider, session_id):
    """The session finished its turn: nothing of it is running any more."""
    if not session_id:
        return
    path, lock = _paths(state_dir)
    with _Lock(lock):
        data = _load(path)
        if data.pop(f"{provider}:{session_id}", None) is not None:
            try:
                path.write_text(json.dumps(data), encoding="utf-8")
            except OSError:
                pass


def render(info):
    """Context lines for the model ('' when there is nothing to protect)."""
    parts = []
    if info.get("ahead"):
        first = info["ahead"][0].get("summary") or "an earlier request"
        n = len(info["ahead"])
        parts.append(f"QUEUE: {n} earlier request(s) in this session are still in progress (oldest: \"{first}\"). "
                     "Do not stop, restart, undo or overwrite that work. Finish it first, then handle this request.")
    if info.get("others"):
        e = info["others"][0]
        parts.append(f"CONCURRENCY: another {e.get('provider', 'agent')} session is working in this folder "
                     f"(\"{e.get('summary') or 'unknown task'}\"). Do not modify or revert files it is changing; "
                     "if you must touch the same files, ask the user first.")
    return " ".join(parts)
