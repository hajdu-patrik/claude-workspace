#!/usr/bin/env python3
"""Read-only health report: installed tools and logins, hooks, MCP registrations, skill hub, agents,
configuration and recent router activity. Changes nothing.   Usage: python scripts/check_tools.py
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "router"))
import platforms as P  # noqa: E402

HOME = P.HOME


def line(ok, label, detail=""):
    print(f"[{'OK' if ok else '!!'}]  {label:<34} {detail}")


def jload(p):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None


def has_hook(cfg, event):
    return any("jev-router" in json.dumps(g) for g in (cfg or {}).get("hooks", {}).get(event, []))


def main():
    print("== Tools")
    for info in P.detect(deep=False).values():
        li = {True: "logged in", False: "NOT logged in", None: ""}[info["logged_in"]]
        line(info["installed"], info["label"], f"{info['version'] or 'not installed'}  {li}".strip())

    print("\n== Router hooks")
    line((HOME / ".jev-router" / "bin" / "run_hook.py").is_file(), "hook shim", "~/.jev-router/bin/run_hook.py")
    cs, cx = jload(P.PATHS["claude_settings"]), jload(P.PATHS["codex_hooks"])
    agy = (jload(P.PATHS["agy_hooks"]) or {}).get("router", {})
    line(has_hook(cs, "UserPromptSubmit") and has_hook(cs, "Stop"), "Claude UserPromptSubmit + Stop")
    line(has_hook(cx, "UserPromptSubmit") and has_hook(cx, "Stop"), "Codex UserPromptSubmit + Stop", "(trust once: codex -> /hooks)")
    line("PreInvocation" in agy and "Stop" in agy, "Antigravity PreInvocation + Stop")

    print("\n== MCP router (hook-less modes)")
    line("jev-router" in json.dumps(jload(P.claude_desktop_config()) or {}), "Claude desktop (Chat/Cowork)")
    cfg_toml = P.PATHS["codex_config"].read_text(encoding="utf-8") if P.PATHS["codex_config"].exists() else ""
    line("[mcp_servers.jev-router]" in cfg_toml, "Codex")
    line("jev-router" in json.dumps(jload(P.PATHS["agy_mcp"]) or {}), "Antigravity")

    print("\n== Skill hub + agents")
    hub = HOME / ".skills"
    n_hub = sum(1 for p in hub.iterdir() if (p / "SKILL.md").is_file()) if hub.is_dir() else 0
    line(n_hub > 0, "~/.skills", f"{n_hub} skills")
    for label, base in [("Claude links", P.PATHS["claude_skills"]), ("Codex links", P.PATHS["codex_skills"])]:
        n = sum(1 for p in base.iterdir() if P.is_link(p)) if base.is_dir() else 0
        line(n > 0, label, f"{n} links")
    agy_skills = jload(P.PATHS["agy_skills_json"]) or {}
    line(any(e.get("path", "").endswith("/.skills") for e in agy_skills.get("entries", [])), "Antigravity skills.json")
    cat = jload(hub / "catalog.json") or {}
    line(bool(cat.get("skills")), "catalog.json", f"{len(cat.get('skills', []))} skills, generated {cat.get('generated', '-')}")
    ca = list(P.PATHS["claude_agents"].glob("*-worker-*.md")) if P.PATHS["claude_agents"].is_dir() else []
    line(len(ca) > 0, "Claude worker agents", f"{len(ca)}")
    line("[agents." in cfg_toml, "Codex worker roles", f"{cfg_toml.count('[agents.')}")

    print("\n== Configuration (~/.jev-router/config.json)")
    cfg = jload(HOME / ".jev-router" / "config.json") or {}
    jev = os.environ.get("TYPESAFE_API_KEY") or cfg.get("typesafe_api_key")
    line(True, "Decision backend", "JEV (token configured)" if jev else "built-in local model (no JEV token)")
    line(True, "Remote access name", cfg.get("remote_name") or "not set up (python install.py remote)")
    line(True, "Per-account model overrides", "yes" if (HOME / ".jev-router" / "models.local.json").exists()
         else "no (python install.py models --probe)")

    print("\n== Router activity (~/.jev-router/logs/routing.jsonl)")
    log = HOME / ".jev-router" / "logs" / "routing.jsonl"
    last = {}
    if log.exists():
        for raw in log.read_text(encoding="utf-8", errors="replace").splitlines()[-500:]:
            try:
                e = json.loads(raw)
            except ValueError:
                continue
            last[e.get("provider")] = e.get("ts")
    for p, ts in sorted(last.items()):
        line(True, f"last {p} prompt", ts)
    if not last:
        line(False, "no routed prompt logged yet")


if __name__ == "__main__":
    main()
