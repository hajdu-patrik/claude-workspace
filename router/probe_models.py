#!/usr/bin/env python3
"""Report which models are currently actually available to this user's Codex CLI and Antigravity
CLI accounts, so router/targets.json and router/models.json can be kept in sync with reality
instead of guessed model names. Read-only: makes no changes, safe to re-run any time (e.g. after
finishing Antigravity CLI login, or after a Codex model list refresh).

Usage: python router/probe_models.py
"""
import json
import shutil
import subprocess
from pathlib import Path

HOME = Path.home()


def probe_codex():
    """Live catalog via `codex debug models`. ~/.codex/models_cache.json is NOT reliable: the
    ChatGPT app's bundled (older) client rewrites it with a partial list (verified 2026-09-24)."""
    print("=== Codex CLI (codex debug models) ===")
    exe = shutil.which("codex")
    if not exe:
        print("codex not found on PATH.")
        return
    r = subprocess.run([exe, "debug", "models"], capture_output=True, text=True, timeout=60,
                       encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL)
    out = r.stdout
    try:
        d = json.JSONDecoder().raw_decode(out[out.index('{"models"'):])[0]
    except ValueError:
        print("Could not parse `codex debug models` output:", (out or r.stderr)[:300])
        return
    for m in d.get("models", []):
        levels = ", ".join(l["effort"] for l in m.get("supported_reasoning_levels", []))
        print(f"  {m['slug']:26s} vis={m.get('visibility', '?'):5s} [{levels}] - {m.get('description', '')}")


def probe_antigravity():
    print("\n=== Antigravity CLI ===")
    agy = shutil.which("agy") or str(Path.home() / "AppData" / "Local" / "agy" / "bin" / "agy.exe")
    if not Path(agy).exists():
        print("agy not found - install it (see README) or open a new terminal if just installed.")
        return
    r = subprocess.run([agy, "models"], capture_output=True, text=True, timeout=30)
    out = (r.stdout + r.stderr).strip()
    if "sign in" in out.lower() or "please sign in" in out.lower():
        print("Not logged in yet. Run `agy` (no arguments) in your own terminal, finish the")
        print("Google sign-in in the browser, then re-run this script.")
    else:
        print(out)


if __name__ == "__main__":
    probe_codex()
    probe_antigravity()
