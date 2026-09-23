#!/usr/bin/env python3
"""Read-only health report of the whole setup: tool versions, logins, hooks, skills hub, agents,
MCP registrations, recent router activity. Changes nothing.   Usage: python scripts/check_tools.py
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

HOME = Path.home()
ROOT = Path(__file__).resolve().parents[1]
AGY = shutil.which("agy") or str(Path(os.environ.get("LOCALAPPDATA", "")) / "agy" / "bin" / "agy.exe")


def run(argv, timeout=30):
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL,
                           encoding="utf-8", errors="replace")
        return (r.stdout or r.stderr).strip().splitlines()[0] if (r.stdout or r.stderr).strip() else "(no output)"
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"(unavailable: {type(exc).__name__})"


def line(ok, label, detail=""):
    print(f"[{'OK' if ok else '!!'}]  {label:<34} {detail}")


def jload(p):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None


def main():
    print("== Tools")
    for label, argv in [("Claude Code CLI", ["claude", "--version"]), ("Codex CLI", ["codex", "--version"]),
                        ("Codex login", ["codex", "login", "status"]), ("Antigravity CLI", [AGY, "--version"]),
                        ("Python", [sys.executable, "--version"]), ("Git", ["git", "--version"])]:
        exe = shutil.which(argv[0]) or (argv[0] if Path(argv[0]).is_file() else None)
        line(bool(exe), label, run(argv) if exe else "not found")
    apps = run(["powershell", "-NoProfile", "-Command",
                "(Get-AppxPackage | ? { $_.Name -match '^(Claude|OpenAI.Codex)$' } | % { $_.Name + ' ' + $_.Version }) -join '; '"])
    line("Claude" in apps, "Desktop apps (MSIX)", apps)
    line((Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "antigravity").is_dir(), "Antigravity desktop app")

    print("\n== Router hooks")
    shim = HOME / ".jev-router" / "bin" / "run_hook.py"
    line(shim.is_file(), "hook shim", str(shim))
    cs = jload(HOME / ".claude" / "settings.json") or {}
    line("jev-router" in json.dumps(cs.get("hooks", {})), "Claude UserPromptSubmit")
    line("jev-router" in json.dumps(jload(HOME / ".codex" / "hooks.json") or {}), "Codex UserPromptSubmit", "(trust via `codex` -> /hooks)")
    line("jev-router" in json.dumps(jload(HOME / ".gemini" / "config" / "hooks.json") or {}), "Antigravity PreInvocation")

    print("\n== MCP router (hook-less modes)")
    line("jev-router" in json.dumps(jload(Path(os.environ.get("APPDATA", "")) / "Claude" / "claude_desktop_config.json") or {}),
         "Claude desktop (Chat/Cowork)")
    cfg = (HOME / ".codex" / "config.toml").read_text(encoding="utf-8") if (HOME / ".codex" / "config.toml").exists() else ""
    line("[mcp_servers.jev-router]" in cfg, "Codex")
    line("jev-router" in json.dumps(jload(HOME / ".gemini" / "config" / "mcp_config.json") or {}), "Antigravity")

    print("\n== Skills hub + agents")
    hub = HOME / ".skills"
    n_hub = sum(1 for p in hub.iterdir() if (p / "SKILL.md").is_file()) if hub.is_dir() else 0
    line(n_hub > 0, "~/.skills", f"{n_hub} skills")
    for label, base in [("Claude links", HOME / ".claude" / "skills"), ("Codex links", HOME / ".agents" / "skills")]:
        n = sum(1 for p in base.iterdir() if p.is_junction()) if base.is_dir() else 0
        line(n >= n_hub and n > 0, label, f"{n} junctions")
    agy = jload(HOME / ".gemini" / "config" / "skills.json") or {}
    line(any(e.get("path", "").endswith("/.skills") for e in agy.get("entries", [])), "Antigravity skills.json -> ~/.skills")
    cat = jload(hub / "catalog.json") or {}
    line(bool(cat.get("skills")), "catalog.json", f"{len(cat.get('skills', []))} skills, generated {cat.get('generated', '-')}")
    ca = list((HOME / ".claude" / "agents").glob("*-worker-*.md"))
    line(len(ca) > 0, "Claude worker agents", f"{len(ca)}")
    line("[agents." in cfg, "Codex worker roles", f"{cfg.count('[agents.')}")

    print("\n== Router activity (~/.jev-router/logs/routing.jsonl)")
    log = HOME / ".jev-router" / "logs" / "routing.jsonl"
    if log.exists():
        lines = log.read_text(encoding="utf-8", errors="replace").splitlines()[-200:]
        by = {}
        for l in lines:
            try:
                e = json.loads(l)
            except ValueError:
                continue
            by[e.get("provider")] = e.get("ts")
        for p, ts in sorted(by.items()):
            line(True, f"last {p} prompt", ts)
    else:
        line(False, "no routed prompt logged yet")
    print(f"\nBackend: {'JEV (TYPESAFE_API_KEY set)' if os.environ.get('TYPESAFE_API_KEY') else 'local mock (no TYPESAFE_API_KEY)'}")


if __name__ == "__main__":
    main()
