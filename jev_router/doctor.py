#!/usr/bin/env python3
"""Read-only health report (`python install.py doctor`)."""
import json
import os
import re
from pathlib import Path

from . import platforms as P, remote

HOME = P.HOME
STATE = HOME / ".jev-router"


def line(ok, label, detail=""):
    print(f"[{'OK' if ok else '!!'}]  {label:<34} {detail}")


def jload(p):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None


def has_hook(cfg, event):
    return any("jev-router" in json.dumps(g) for g in (cfg or {}).get("hooks", {}).get(event, []))


def interpreters(claude_settings, codex_toml):
    """A Python update that removes the registered interpreter silently stops every hook."""
    found = []
    hook = next((h.get("command", "") for g in (claude_settings or {}).get("hooks", {}).get("UserPromptSubmit", [])
                 for h in g.get("hooks", []) if "jev-router" in h.get("command", "")), "")
    if hook:
        m = re.match(r'\s*"([^"]+)"|\s*\'([^\']+)\'|\s*(\S+)', hook)
        found.append(("Claude hook", next(g for g in m.groups() if g)))
    for label, cfg in (("Antigravity MCP", jload(P.PATHS["agy_mcp"])), ("Claude desktop MCP", jload(P.claude_desktop_config()))):
        cmd = ((cfg or {}).get("mcpServers", {}).get("jev-router") or {}).get("command")
        if cmd:
            found.append((label, cmd))
    m = re.search(r'\[mcp_servers\.jev-router\]\ncommand = "([^"]+)"', codex_toml or "")
    if m:
        found.append(("Codex MCP", m.group(1)))
    return found


def report_tools():
    print("== Tools")
    for info in P.detect(deep=False).values():
        li = {True: "logged in", False: "NOT logged in", None: ""}[info["logged_in"]]
        line(info["installed"], info["label"], f"{info['version'] or 'not installed'}  {li}".strip())


def report_hooks():
    print("\n== Router hooks")
    line((STATE / "bin" / "run_hook.py").is_file(), "hook shim", "~/.jev-router/bin/run_hook.py")
    cs, cx = jload(P.PATHS["claude_settings"]), jload(P.PATHS["codex_hooks"])
    agy = (jload(P.PATHS["agy_hooks"]) or {}).get("router", {})
    line(has_hook(cs, "UserPromptSubmit") and has_hook(cs, "Stop"), "Claude UserPromptSubmit + Stop")
    line(has_hook(cx, "UserPromptSubmit") and has_hook(cx, "Stop"), "Codex UserPromptSubmit + Stop", "(trust once: codex -> /hooks)")
    line("PreInvocation" in agy and "Stop" in agy, "Antigravity PreInvocation + Stop")
    return cs


def report_mcp():
    print("\n== MCP router (hook-less modes)")
    ok = "jev-router" in json.dumps(jload(P.claude_desktop_config()) or {})
    line(ok, "Claude desktop (Chat/Cowork)", "" if ok else "missing - the app rewrites its config from memory: "
         "close the Claude app, run python install.py --yes, reopen it")
    cfg_toml = P.PATHS["codex_config"].read_text(encoding="utf-8") if P.PATHS["codex_config"].exists() else ""
    line("[mcp_servers.jev-router]" in cfg_toml, "Codex")
    line("jev-router" in json.dumps(jload(P.PATHS["agy_mcp"]) or {}), "Antigravity")
    return cfg_toml


def count_in(folder, test):
    return sum(1 for p in folder.iterdir() if test(p)) if folder.is_dir() else 0


def report_skills(cfg_toml):
    print("\n== Skill hub + agents")
    hub = HOME / ".skills"
    n_hub = count_in(hub, lambda p: (p / "SKILL.md").is_file())
    line(n_hub > 0, "~/.skills", f"{n_hub} skills")
    for label, base in [("Claude links", P.PATHS["claude_skills"]), ("Codex links", P.PATHS["codex_skills"])]:
        n = count_in(base, P.is_link)
        line(n > 0, label, f"{n} links")
    agy_skills = jload(P.PATHS["agy_skills_json"]) or {}
    line(any(e.get("path", "").endswith("/.skills") for e in agy_skills.get("entries", [])), "Antigravity skills.json")
    cat = jload(hub / "catalog.json") or {}
    line(bool(cat.get("skills")), "catalog.json", f"{len(cat.get('skills', []))} skills, generated {cat.get('generated', '-')}")
    ca = list(P.PATHS["claude_agents"].glob("*-worker-*.md")) if P.PATHS["claude_agents"].is_dir() else []
    line(len(ca) > 0, "Claude worker agents", f"{len(ca)}")
    line("[agents." in cfg_toml, "Codex worker roles", f"{cfg_toml.count('[agents.')}")
    n_agy = count_in(P.PATHS["agy_agents"], lambda p: (p / "agent.md").is_file())
    line(n_agy > 0, "Antigravity worker agents", f"{n_agy}")


def report_config():
    print("\n== Configuration (~/.jev-router/config.json)")
    cfg = jload(STATE / "config.json") or {}
    jev = os.environ.get("TYPESAFE_API_KEY") or cfg.get("typesafe_api_key")
    line(True, "Decision backend", "JEV (token configured)" if jev else "built-in local model (no JEV token)")
    line(True, "Remote access name", cfg.get("remote_name") or "not set up (python install.py remote)")
    line(True, "Per-account model overrides", "yes" if (STATE / "models.local.json").exists()
         else "no (python install.py models --probe)")
    return cfg


def report_interpreters(claude_settings, cfg_toml):
    print("\n== Interpreter used by hooks and MCP")
    for label, exe in interpreters(claude_settings, cfg_toml):
        line(Path(exe).is_file(), label, exe if Path(exe).is_file() else
             f"{exe} is gone (Python updated or removed?) - re-run: python install.py --yes")


def report_remote(cfg):
    if not cfg.get("remote_name"):
        return
    print(f"\n== Remote access \"{cfg['remote_name']}\"")
    for tool, ok, detail in remote.status():
        line(ok, tool, detail)


def last_prompts(log):
    last = {}
    if not log.exists():
        return last
    for raw in log.read_text(encoding="utf-8", errors="replace").splitlines()[-500:]:
        try:
            e = json.loads(raw)
        except ValueError:
            continue
        last[e.get("provider")] = e.get("ts")
    return last


def report_activity():
    print("\n== Router activity (~/.jev-router/logs/routing.jsonl)")
    last = last_prompts(STATE / "logs" / "routing.jsonl")
    for p, ts in sorted(last.items()):
        line(True, f"last {p} prompt", ts)
    if not last:
        line(False, "no routed prompt logged yet")


def main():
    report_tools()
    claude_settings = report_hooks()
    cfg_toml = report_mcp()
    report_skills(cfg_toml)
    cfg = report_config()
    report_interpreters(claude_settings, cfg_toml)
    report_remote(cfg)
    report_activity()
