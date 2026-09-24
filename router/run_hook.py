#!/usr/bin/env python3
"""Shared "before prompt" hook entry point for Claude Code / Codex CLI / Antigravity CLI.

Claude Code and Codex CLI: UserPromptSubmit, stdin JSON with a "prompt" field, stdout
{"hookSpecificOutput": {"hookEventName": ..., "additionalContext": ...}}.

Antigravity CLI: there is no prompt-submit event, only PreInvocation, which (1) fires before
EVERY model call of a turn, (2) carries no prompt text - only metadata incl. transcriptPath and
conversationId - and (3) wants {"injectSteps": [{"ephemeralMessage": ...}]}. So for Antigravity
the latest USER_INPUT line is read back from the transcript (format verified against a real run,
see models.json) and the context is injected only once per user turn (tracked per
conversationId + step_index in ~/.jev-router/state/).

Usage (installed globally by router/install_hooks.py, via the space-free shim
~/.jev-router/bin/run_hook.py - Antigravity's `cmd /c` breaks on quoted paths with spaces):
    python run_hook.py claude      UserPromptSubmit
    python run_hook.py codex       UserPromptSubmit
    python run_hook.py antigravity PreInvocation
    python run_hook.py claude      UserPromptSubmit --cloud-only   # project-level hook, cloud sandboxes only

Exit code: always 0 - the router never blocks a prompt.
"""
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import core  # noqa: E402

LOG_FILE = core.STATE_DIR / "logs" / "routing.jsonl"
SEEN_FILE = core.STATE_DIR / "state" / "antigravity_seen.json"
SKIP_TAGS = ("#norouter", "#privat")
# Harness-injected pseudo-prompts (background task results, reminders) are not user requests.
SYSTEM_PREFIXES = ("<task-notification", "<system-reminder", "[system notification", "<command-", "<local-command",
                   "caveat: the messages below")
SECRET_RE = re.compile(r"(sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|AIza[0-9A-Za-z_-]{30,}|xox[abp]-[\w-]{10,}"
                       r"|\b\d/0A[\w-]{20,}|eyJ[\w-]{10,}\.[\w-]{10,}\.[\w-]{10,}|\b(?=[\w+=-]*\d)(?=[\w+=-]*[A-Za-z])[\w+=-]{32,}\b)")


def redact(text):
    return SECRET_RE.sub("[redacted]", text)


def log(entry):
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _antigravity_prompt(payload):
    """(prompt, turn_key) of the latest user turn in the transcript, or (None, None).
    Never raises: a missing/odd transcript just means the hook does nothing this call."""
    try:
        path = payload.get("transcriptPath")
        if not path:
            return None, None
        lines = [l for l in Path(path).read_text(encoding="utf-8", errors="replace").splitlines() if l.strip()]
        for line in reversed(lines):
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if not isinstance(entry, dict) or entry.get("type") != "USER_INPUT":
                continue
            content = entry.get("content") or ""
            m = re.search(r"<USER_REQUEST>\s*(.*?)\s*</USER_REQUEST>", content, re.S)
            text = (m.group(1) if m else content).strip()
            if text:
                return text, f"{payload.get('conversationId', '')}:{entry.get('step_index', len(lines))}"
    except Exception:  # noqa: BLE001 - a hook must never crash the host tool
        pass
    return None, None


def _first_time(turn_key):
    """True only for the first PreInvocation of a given user turn (persisted, small LRU)."""
    try:
        seen = json.loads(SEEN_FILE.read_text(encoding="utf-8")) if SEEN_FILE.exists() else []
    except (OSError, ValueError):
        seen = []
    if turn_key in seen:
        return False
    seen = (seen + [turn_key])[-200:]
    try:
        SEEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        SEEN_FILE.write_text(json.dumps(seen), encoding="utf-8")
    except OSError:
        pass
    return True


def extract_prompt(raw, provider):
    """Returns (prompt_text_or_None, payload_dict_or_None, turn_key_or_None)."""
    try:
        payload = json.loads(raw.decode("utf-8-sig"))  # utf-8-sig: PowerShell 5.1 pipes can prepend a BOM
    except (ValueError, UnicodeDecodeError, AttributeError):
        return None, None, None
    if not isinstance(payload, dict):
        return None, None, None
    if provider == "antigravity":
        prompt, key = _antigravity_prompt(payload)
        return prompt, payload, key
    prompt = payload.get("prompt")
    return (prompt.strip() if isinstance(prompt, str) else None), payload, None


def emit(text, hook_event_name, provider):
    # ASCII-escaped JSON (json.dumps default): safe for any console code page
    if provider == "antigravity":
        print(json.dumps({"injectSteps": [{"ephemeralMessage": text}]}))
    else:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": hook_event_name, "additionalContext": text}}))


def should_skip(prompt):
    low = prompt.lstrip().lower()
    return (len(prompt) < 3 or low.startswith("/") or low.startswith(SYSTEM_PREFIXES)
            or any(t in low for t in SKIP_TAGS))


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    cloud_only = "--cloud-only" in args
    args = [a for a in args if a != "--cloud-only"]
    if len(args) != 2:
        print("Usage: run_hook.py <claude|codex|antigravity> <hook_event_name> [--cloud-only]", file=sys.stderr)
        return 0
    if cloud_only and not core.is_cloud():
        return 0  # locally the global (user-level) hook already runs - avoid double injection
    provider, hook_event_name = args

    raw = sys.stdin.buffer.read()
    prompt, payload, turn_key = extract_prompt(raw, provider)
    if prompt is None:
        if payload is None:
            log({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "provider": provider, "error": "stdin: not a JSON object",
                 "stdin_head": redact(raw[:80].decode("utf-8", "replace"))})
        return 0
    if should_skip(prompt):
        return 0  # command, harness message, or private prompt (#norouter/#privat: never sent to TypeSafe)
    if provider == "antigravity" and turn_key and not _first_time(turn_key):
        return 0  # same user turn, later model call

    t0 = time.perf_counter()
    entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "provider": provider,
             "cwd": (payload.get("cwd") or (payload.get("workspacePaths") or [""])[0]) if payload else "",
             "sha": hashlib.sha256(prompt.encode()).hexdigest()[:12],
             "prompt": redact(prompt if os.environ.get("ROUTER_LOG_PROMPTS") == "1" else prompt[:200])}
    try:
        # Codex reports the session's active model in the hook payload: lets a tier stay in-session
        session_model = payload.get("model") if provider == "codex" and isinstance(payload.get("model"), str) else None
        d, text, _, error = core.route(prompt, provider, session_model=session_model)
        if error:
            entry["error"] = error
    except Exception as exc:  # e.g. a malformed routes.json: never block
        entry["error"] = f"{type(exc).__name__}: {exc}"[:200]
        entry["latency_ms"] = int((time.perf_counter() - t0) * 1000)
        log(entry)
        emit("[router] unavailable; answer directly in this session."
             + (" SAFETY: ask for explicit confirmation before any irreversible action." if core.is_destructive(prompt) else "")
             + " " + core.lang.respond_line(core.lang.detect(prompt)), hook_event_name, provider)
        return 0
    entry.update(d)
    entry["latency_ms"] = int((time.perf_counter() - t0) * 1000)
    log(entry)
    emit(text, hook_event_name, provider)
    return 0


if __name__ == "__main__":
    sys.exit(main())
