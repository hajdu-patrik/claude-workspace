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

State lives in ~/.jev-router/state/inflight.json, guarded by a lock file. An entry is ignored when
it is older than ROUTER_QUEUE_TTL_MIN (default 120), or when the session's transcript has not been
written for ROUTER_QUEUE_IDLE_MIN (default 10) - a turn the user cancelled (Claude runs no Stop hook
on Esc) or a crashed session must never make the next prompt resume old work.
"""
import json
import os
import time
from pathlib import Path

def _minutes(var, default):
    try:
        return 60 * float(os.environ.get(var, default))
    except ValueError:
        return 60 * float(default)


TTL_S = _minutes("ROUTER_QUEUE_TTL_MIN", "120")
IDLE_S = _minutes("ROUTER_QUEUE_IDLE_MIN", "10")
LOCK_WAIT_S = 2.0


def _paths(state_dir):
    d = Path(state_dir) / "state"
    return d / "inflight.json", d / "inflight.lock"


class _Lock:
    """Cross-platform lock via O_EXCL lock file; a lock older than 10 s is considered abandoned."""

    def __init__(self, path):
        self.path = path
        self.acquired = False

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.time() + LOCK_WAIT_S
        while True:
            try:
                os.close(os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
                self.acquired = True
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
        if not self.acquired:
            return  # never remove a lock someone else holds
        try:
            self.path.unlink()
        except OSError:
            pass


def _save(path, data):
    """Atomic write: readers never see a half-written file."""
    tmp = path.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass


def _load(path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _prune(data, now):
    """The entries younger than TTL_S; a session left without any is dropped."""
    fresh = {key: [e for e in entries if now - e.get("ts", 0) < TTL_S] for key, entries in data.items()}
    return {key: entries for key, entries in fresh.items() if entries}


def on_submit(state_dir, provider, session_id, cwd, summary, last_activity=None):
    """Register a new request. Returns {"ahead": [entries still running in this session],
    "others": [entries of OTHER sessions working in the same folder]}.
    last_activity: mtime of the session's transcript - if the session has been silent for
    IDLE_S, its earlier entries belong to a cancelled or crashed turn and are dropped."""
    if not session_id:
        return {"ahead": [], "others": []}
    path, lock = _paths(state_dir)
    now = time.time()
    key = f"{provider}:{session_id}"
    norm_cwd = os.path.normcase(os.path.abspath(cwd)) if cwd else ""
    with _Lock(lock):
        data = _prune(_load(path), now)
        if last_activity is not None and now - last_activity > IDLE_S:
            data.pop(key, None)
        ahead = list(data.get(key, []))
        others = [e for k, v in data.items() if k != key for e in v[:1] if norm_cwd and e.get("cwd") == norm_cwd]
        data.setdefault(key, []).append({"ts": now, "cwd": norm_cwd, "summary": summary, "provider": provider})
        _save(path, data)
    return {"ahead": ahead, "others": others}


def on_stop(state_dir, provider, session_id):
    """The session finished its turn: nothing of it is running any more."""
    if not session_id:
        return
    path, lock = _paths(state_dir)
    with _Lock(lock):
        data = _load(path)
        if data.pop(f"{provider}:{session_id}", None) is not None:
            _save(path, data)


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
