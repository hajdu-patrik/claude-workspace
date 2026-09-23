#!/usr/bin/env python3
"""Shared skill hub (~/.skills) + generated worker agents, for Claude Code, Codex and Antigravity.

    python router/skills_hub.py migrate [--apply]   move ~/.claude/skills/<real dirs> into ~/.skills
    python router/skills_hub.py link    [--apply]   link every hub skill into each tool + repo skills into the hub
    python router/skills_hub.py agents  [--apply]   generate the <tier>-worker-<effort> agents (targets.json)
    python router/skills_hub.py catalog              rebuild ~/.skills/catalog.json (router's skill index)
    python router/skills_hub.py doctor               report broken links, duplicates, folders without SKILL.md
    python router/skills_hub.py all     [--apply]   migrate + link + agents + catalog + doctor

Without --apply every mutating command is a DRY RUN: it only prints what it would do.

Layout (the hub is the single source of truth; everything else is a directory junction - no
admin rights or Developer Mode needed on Windows):
    ~/.skills/<name>/SKILL.md            real folder (moved here from ~/.claude/skills)
    ~/.skills/<repo skill>  -> <repo>/skills/<name>      (repo-owned skills stay version-controlled)
    ~/.claude/skills/<name> -> ~/.skills/<name>          Claude Code (CLI + desktop Code tab)
    ~/.agents/skills/<name> -> ~/.skills/<name>          Codex (CLI + ChatGPT app's Codex mode)
    ~/.gemini/config/skills.json  entries: [{"path": "C:/Users/<you>/.skills"}]  Antigravity (absolute path!)
Per-skill junctions rather than one junction for the whole folder: ~/.claude/skills also holds
app-managed content (synced/) and a fully symlinked skills dir is a known Claude Code regression.

Safety: never deletes a real directory. Only junctions this tool can prove it owns (pointing into
the hub or the repo's skills/) are ever removed/replaced; a name collision is reported, not resolved.
"""
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import skill_index  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
HOME = Path.home()
HUB = skill_index.HUB
REPO_SKILLS = REPO / "skills"
AGENT_TEMPLATES = REPO / "agents"
CLAUDE_SKILLS = HOME / ".claude" / "skills"
CODEX_SKILLS = HOME / ".agents" / "skills"
LEGACY_CODEX_SKILLS = HOME / ".codex" / "skills"
AGY_SKILLS_JSON = HOME / ".gemini" / "config" / "skills.json"
CLAUDE_AGENTS = HOME / ".claude" / "agents"
CODEX_AGENTS = HOME / ".codex" / "agents"
CODEX_CONFIG = HOME / ".codex" / "config.toml"
APP_MANAGED = {"synced"}  # folders inside ~/.claude/skills owned by the desktop app
GEN_MARK = "generated-by: jev-router"
TOML_BEGIN, TOML_END = "# >>> jev-router agents (generated - edit router/targets.json, not this block)", "# <<< jev-router agents"

APPLY = False


def act(msg, fn=None):
    print(("[DO]  " if APPLY else "[DRY] ") + msg)
    if APPLY and fn:
        fn()


def is_junction(p):
    try:
        return p.is_junction() or p.is_symlink()
    except OSError:
        return False


def target_of(p):
    try:
        return Path(os.path.realpath(p))
    except OSError:
        return None


def mk_junction(link, target):
    link.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True, text=True)
    if r.returncode:
        raise OSError((r.stderr or r.stdout).strip())


def rm_junction(link):
    os.rmdir(link)  # removes the link only, never the target's contents


def owned(link):
    """A junction we created: it points into the hub or into the repo's skills/."""
    if not is_junction(link):
        return False
    t = target_of(link)
    hub, repo = target_of(HUB), target_of(REPO_SKILLS)
    return t is not None and any(str(t).lower().startswith(str(b).lower()) for b in (hub, repo) if b)


def ensure_link(link, target, label):
    if is_junction(link):
        if target_of(link) == target_of(target):
            return
        if owned(link) or not link.exists():
            act(f"{label}: re-point {link} -> {target}", lambda: (rm_junction(link), mk_junction(link, target)))
        else:
            print(f"[SKIP] {label}: {link} is a foreign link -> {target_of(link)}")
        return
    if link.exists():
        print(f"[SKIP] {label}: {link} is a real folder (name collision with the hub) - resolve manually")
        return
    act(f"{label}: link {link} -> {target}", lambda: mk_junction(link, target))


def hub_skills():
    return sorted(p for p in HUB.iterdir() if (p / "SKILL.md").is_file()) if HUB.is_dir() else []


# --- commands -------------------------------------------------------------------------------------
def cmd_migrate():
    if not CLAUDE_SKILLS.is_dir():
        print("nothing to migrate")
        return
    moved = 0
    for d in sorted(CLAUDE_SKILLS.iterdir()):
        if d.name in APP_MANAGED or is_junction(d) or not d.is_dir():
            continue
        if not (d / "SKILL.md").is_file():
            print(f"[SKIP] {d.name}: no SKILL.md")
            continue
        dest = HUB / d.name
        if dest.exists():
            print(f"[SKIP] {d.name}: already exists in the hub - resolve manually")
            continue

        def move(d=d, dest=dest):
            HUB.mkdir(parents=True, exist_ok=True)
            shutil.move(str(d), str(dest))
            mk_junction(d, dest)
        act(f"move {d} -> {dest} (+ junction back)", move)
        moved += 1
    print(f"migrate: {moved} skill folder(s) {'moved' if APPLY else 'would be moved'}")


def cmd_link():
    # 1) repo-owned skills are exposed through the hub
    for d in sorted(p for p in REPO_SKILLS.iterdir() if (p / "SKILL.md").is_file()):
        ensure_link(HUB / d.name, d, f"hub <- repo '{d.name}'")
    # 2) every hub skill into Claude Code and Codex
    names = set()
    for s in hub_skills() + ([HUB / d.name for d in REPO_SKILLS.iterdir() if (d / "SKILL.md").is_file()] if not APPLY else []):
        if s.name in names:
            continue
        names.add(s.name)
        ensure_link(CLAUDE_SKILLS / s.name, s, "claude")
        ensure_link(CODEX_SKILLS / s.name, s, "codex")
    # 3) drop stale links we own (target gone / skill removed from the hub), and legacy locations
    for base in (CLAUDE_SKILLS, CODEX_SKILLS, LEGACY_CODEX_SKILLS, REPO / ".claude" / "skills"):
        if not base.is_dir():
            continue
        for link in base.iterdir():
            legacy = base in (LEGACY_CODEX_SKILLS, REPO / ".claude" / "skills")
            if owned(link) and (legacy or link.name not in names or not link.exists()):
                act(f"remove stale/legacy link {link}", lambda l=link: rm_junction(l))
    repo_agents = REPO / ".claude" / "agents"
    if is_junction(repo_agents):
        act(f"remove legacy project agents link {repo_agents} (agents are now user-level)", lambda: rm_junction(repo_agents))
    # 4) Antigravity: one manifest entry for the whole hub. Must be an ABSOLUTE path: agy 1.2.9
    #    rejects "~/.skills" at runtime ("must be an absolute path"), despite its docs.
    cfg = {}
    if AGY_SKILLS_JSON.exists():
        try:
            cfg = json.loads(AGY_SKILLS_JSON.read_text(encoding="utf-8"))
        except ValueError:
            print(f"[FAIL] {AGY_SKILLS_JSON}: invalid JSON - fix manually")
            return
    entries = cfg.get("entries", [])
    repo_path = str(REPO_SKILLS).replace("\\", "/")
    hub_path = str(HUB).replace("\\", "/")
    new_entries = [e for e in entries if e.get("path") not in (repo_path, "~/.skills", hub_path)] + [{"path": hub_path}]
    if new_entries != entries:
        cfg["entries"] = new_entries
        act(f"{AGY_SKILLS_JSON}: entries -> {new_entries}",
            lambda: AGY_SKILLS_JSON.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8"))


def _split_template(path):
    text = path.read_text(encoding="utf-8")
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", text, re.S)
    fm = dict(re.findall(r"^(\w+):\s*(.*)$", m.group(1), re.M)) if m else {}
    return fm, (m.group(2) if m else text).strip()


def planned_agents():
    """[(provider, name, model, effort, description, body)] from targets.json tiers with agent+efforts."""
    targets = json.loads((REPO / "router" / "targets.json").read_text(encoding="utf-8"))
    out = []
    for provider in ("claude", "codex"):
        for tier, spec in targets.get(provider, {}).get("tiers", {}).items():
            if not isinstance(spec, dict) or not spec.get("agent") or not spec.get("efforts"):
                continue
            tpl = AGENT_TEMPLATES / f"{spec['agent']}.md"
            fm, body = _split_template(tpl) if tpl.exists() else ({}, "Do the delegated task carefully.")
            for effort in spec["efforts"]:
                desc = (fm.get("description") or f"{tier} worker").rstrip(".")
                out.append((provider, f"{spec['agent']}-{effort}", spec["model"], effort,
                            f"{desc} Fixed model {spec['model']}, reasoning effort {effort}. "
                            f"Use when the [router] context names {spec['agent']}-{effort}.", body))
    return out


def cmd_agents():
    plan = planned_agents()
    # Claude: one .md per variant
    want = {name for p, name, *_ in plan if p == "claude"}
    for provider, name, model, effort, desc, body in plan:
        if provider != "claude":
            continue
        path = CLAUDE_AGENTS / f"{name}.md"
        content = (f"---\nname: {name}\ndescription: {desc}\nmodel: {model}\neffort: {effort}\n# {GEN_MARK}\n---\n{body}\n")
        if not path.exists() or path.read_text(encoding="utf-8") != content:
            act(f"write {path}", lambda p=path, c=content: (p.parent.mkdir(parents=True, exist_ok=True),
                                                             p.write_text(c, encoding="utf-8")))
    for f in CLAUDE_AGENTS.glob("*.md") if CLAUDE_AGENTS.is_dir() else []:
        if f.stem not in want and GEN_MARK in f.read_text(encoding="utf-8", errors="replace"):
            act(f"remove stale generated agent {f}", f.unlink)
    # Codex: role file per variant + one managed block in config.toml
    block = [TOML_BEGIN]
    want = set()
    for provider, name, model, effort, desc, body in plan:
        if provider != "codex":
            continue
        want.add(name)
        path = CODEX_AGENTS / f"{name}.toml"
        role = (f"# {GEN_MARK}\nmodel = {json.dumps(model)}\nmodel_reasoning_effort = {json.dumps(effort)}\n"
                f"developer_instructions = {json.dumps(body)}\n")
        if not path.exists() or path.read_text(encoding="utf-8") != role:
            act(f"write {path}", lambda p=path, c=role: (p.parent.mkdir(parents=True, exist_ok=True),
                                                          p.write_text(c, encoding="utf-8")))
        block += [f"[agents.{name}]", f"description = {json.dumps(desc)}",
                  f"config_file = {json.dumps(str(path).replace(chr(92), '/'))}", ""]
    for f in CODEX_AGENTS.glob("*.toml") if CODEX_AGENTS.is_dir() else []:
        if f.stem not in want and GEN_MARK in f.read_text(encoding="utf-8", errors="replace"):
            act(f"remove stale generated codex role {f}", f.unlink)
    block.append(TOML_END)
    cfg = CODEX_CONFIG.read_text(encoding="utf-8") if CODEX_CONFIG.exists() else ""
    pattern = re.compile(re.escape(TOML_BEGIN) + r".*?" + re.escape(TOML_END) + r"\n?", re.S)
    new_block = "\n".join(block) + "\n"
    new_cfg = pattern.sub(new_block, cfg) if pattern.search(cfg) else cfg.rstrip("\n") + "\n\n" + new_block
    if new_cfg != cfg:
        act(f"{CODEX_CONFIG}: update [agents.*] block ({len(want)} roles)",
            lambda: CODEX_CONFIG.write_text(new_cfg, encoding="utf-8"))


def cmd_catalog():
    items = skill_index.write_catalog()
    by = {}
    for s in items:
        for p in s["native_in"]:
            by[p] = by.get(p, 0) + 1
    print(f"catalog: {len(items)} skills -> {skill_index.CATALOG}  (native: {by})")


def cmd_doctor():
    problems = 0
    for base in (CLAUDE_SKILLS, CODEX_SKILLS, HUB):
        if not base.is_dir():
            print(f"[WARN] missing: {base}")
            continue
        for p in base.iterdir():
            if p.name in APP_MANAGED or p.is_file():
                continue
            if is_junction(p) and not p.exists():
                print(f"[FAIL] broken link: {p}")
                problems += 1
            elif p.is_dir() and not (p / "SKILL.md").is_file():
                print(f"[WARN] no SKILL.md: {p}")
    names = {}
    for s in skill_index.build_catalog():
        base = s["name"].split(":")[-1]
        names.setdefault(base, []).append(s["name"])
    for base, full in names.items():
        if len(full) > 1:
            print(f"[INFO] same skill name from several sources: {', '.join(full)}")
    print(f"doctor: {problems} problem(s)")
    return problems


def main():
    global APPLY
    args = sys.argv[1:]
    APPLY = "--apply" in args
    args = [a for a in args if a != "--apply"]
    cmd = args[0] if args else "doctor"
    steps = {"migrate": [cmd_migrate], "link": [cmd_link], "agents": [cmd_agents], "catalog": [cmd_catalog],
             "doctor": [cmd_doctor], "all": [cmd_migrate, cmd_link, cmd_agents, cmd_catalog, cmd_doctor]}
    if cmd not in steps:
        print(__doc__)
        return 2
    for step in steps[cmd]:
        print(f"== {step.__name__[4:]}")
        step()
    return 0


if __name__ == "__main__":
    sys.exit(main())
