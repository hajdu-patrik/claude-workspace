#!/usr/bin/env python3
"""Shared skill hub (~/.skills) + generated worker agents for Claude Code, Codex and Antigravity.

Driven by the installer: `python install.py` (full setup) and `python install.py skills [--apply]`
(re-link, regenerate agents, rebuild the catalog, check health). Mutating steps are dry runs unless
APPLY is set.

Layout (the hub is the single source of truth; everything else is a directory link - no
admin rights or Developer Mode needed on Windows):
    ~/.skills/<name>/SKILL.md            real folder (moved here from each tool's own skill folder)
    ~/.skills/<bundled>     -> jev_router/skills/<name>  (skills shipped with jev-router)
    ~/.claude/skills/<name> -> ~/.skills/<name>          Claude Code (CLI + desktop Code tab)
    ~/.agents/skills/<name> -> ~/.skills/<name>          Codex (CLI + ChatGPT app's Codex mode)
    ~/.gemini/config/skills.json  entries: [<absolute ~/.skills>, <absolute jev_router/skills>]  Antigravity
Links are junctions on Windows and symlinks on macOS/Linux, one per skill rather than one for the
whole folder: ~/.claude/skills also holds app-managed content (synced/), and a fully linked skills
directory is a known Claude Code regression.

Safety: never deletes a real directory. Only junctions this tool can prove it owns (pointing into
the hub or jev_router/skills/) are ever removed/replaced; a name collision is reported, not resolved.
"""
import json
import os
import re
import shutil
import sys
from pathlib import Path

from . import catalog, platforms as P

PKG = Path(__file__).resolve().parent
REPO = PKG.parent
HOME = Path.home()
HUB = catalog.HUB
REPO_SKILLS = PKG / "skills"            # skills bundled with jev-router
AGENT_TEMPLATES = PKG / "templates" / "agents"
CLAUDE_SKILLS = P.PATHS["claude_skills"]
CODEX_SKILLS = P.PATHS["codex_skills"]
LEGACY_CODEX_SKILLS = HOME / ".codex" / "skills"
AGY_SKILLS_JSON = P.PATHS["agy_skills_json"]
CLAUDE_AGENTS = P.PATHS["claude_agents"]
CODEX_AGENTS = P.PATHS["codex_agents"]
CODEX_CONFIG = P.PATHS["codex_config"]
PROVIDERS = ("claude", "codex", "antigravity")  # narrowed by the installer to what is installed
APP_MANAGED = {"synced"}  # folders inside ~/.claude/skills owned by the desktop app
GEN_MARK = "generated-by: jev-router"
TOML_BEGIN, TOML_END = "# >>> jev-router agents (generated - edit jev_router/config/targets.json, not this block)", "# <<< jev-router agents"

APPLY = False


def act(msg, fn=None):
    print(("[DO]  " if APPLY else "[DRY] ") + msg)
    if APPLY and fn:
        fn()


def is_junction(p):
    return P.is_link(p)


def target_of(p):
    try:
        return Path(os.path.realpath(p))
    except OSError:
        return None


def mk_junction(link, target):
    P.link_dir(link, target)  # junction on Windows, symlink on macOS/Linux


def rm_junction(link):
    P.unlink_dir(link)  # removes the link only, never the target's contents


def owned(link):
    """A junction we created: it points into the hub or into the repo's skills/."""
    if not is_junction(link):
        return False
    t = target_of(link)
    if t is None:
        return False
    for base in (target_of(HUB), target_of(REPO_SKILLS)):
        # real path containment (not a string prefix: ~/.skills-old must not count as ~/.skills)
        if base is not None and (t == base or base in t.parents):
            return True
    return False


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
# User-installed skill folders of every tool; each real folder is moved into the hub and replaced by
# a link, so the tool keeps working and every other tool gets the skill too. App-managed folders
# (Claude desktop's synced/, Codex's .system/) stay where they are - the catalog indexes them.
MIGRATE_SOURCES = {"claude": [CLAUDE_SKILLS], "codex": [CODEX_SKILLS, LEGACY_CODEX_SKILLS],
                   "antigravity": [HOME / ".gemini" / "config" / "skills"]}


def cmd_migrate():
    moved = 0
    sources = [src for p in PROVIDERS for src in MIGRATE_SOURCES.get(p, [])]  # only the selected tools
    candidates = [d for src in sources if src.is_dir() for d in sorted(src.iterdir())]
    if not candidates:
        print("nothing to migrate")
        return
    for d in candidates:
        if d.name in APP_MANAGED or d.name.startswith(".") or is_junction(d) or not d.is_dir():
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
        if "claude" in PROVIDERS:
            ensure_link(CLAUDE_SKILLS / s.name, s, "claude")
        if "codex" in PROVIDERS:
            ensure_link(CODEX_SKILLS / s.name, s, "codex")
    # 3) drop stale links we own (target gone / skill removed from the hub); ~/.codex/skills is
    #    Codex's legacy location - its skills are served from ~/.agents/skills instead
    for base in (CLAUDE_SKILLS, CODEX_SKILLS, LEGACY_CODEX_SKILLS):
        if not base.is_dir():
            continue
        for link in base.iterdir():
            if owned(link) and (base == LEGACY_CODEX_SKILLS or link.name not in names or not link.exists()):
                act(f"remove stale/legacy link {link}", lambda l=link: rm_junction(l))
    if "antigravity" not in PROVIDERS:
        return
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
    # The repo's skills/ is listed too: agy does not follow directory junctions inside an entry
    # (verified 2026-09-24: the junctioned ~/.skills/cli-bridge was not loaded, real folders were).
    # keep foreign entries that still exist; drop ours and any path that no longer exists (a moved checkout)
    kept = [e for e in entries if e.get("path") not in (repo_path, "~/.skills", hub_path)
            and Path(os.path.expanduser(str(e.get("path", "")))).exists()]
    new_entries = kept + [{"path": hub_path}, {"path": repo_path}]
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
    """[(provider, name, model, effort, description, body)].

    1) Every selectable model x every allowed effort level (models.json, 'ultra' already removed
       by core.models_for) under the provider's agent_template - so whatever model + effort JEV
       picks, a matching fixed agent/role exists.
    2) Tier-specific agents whose template isn't the provider's generic one (e.g. test-worker-*),
       for that tier's own effort range.
    Body/description come from agents/<name>.md (Claude: <model>-worker.md, Codex: codex-worker.md)."""
    from . import core  # local import: only needed here
    targets = json.loads((PKG / "config" / "targets.json").read_text(encoding="utf-8"))
    out, seen = [], set()

    def template(name):
        tpl = AGENT_TEMPLATES / f"{name}.md"
        return _split_template(tpl) if tpl.exists() else ({}, "Do the delegated task carefully.")

    def add(provider, agent_tpl, model, effort, tpl_name):
        name = agent_tpl.format(model=model, model_=model.replace(".", "_"), effort=effort)
        if (provider, name) in seen:
            return
        seen.add((provider, name))
        fm, body = template(tpl_name)
        desc = (fm.get("description") or f"{model} worker").rstrip(".")
        out.append((provider, name, model, effort,
                    f"{desc} Fixed model {model}, reasoning effort {effort}. Use when the [router] context names {name}.", body))

    for provider in (p for p in ("claude", "codex") if p in PROVIDERS):
        cfg = targets.get(provider, {})
        generic = cfg.get("agent_template")
        for model, mdef in core.models_for(provider).items():
            for effort in mdef["levels"]:
                add(provider, generic, model, effort, f"{model}-worker" if provider == "claude" else "codex-worker")
        for spec in cfg.get("tiers", {}).values():
            if isinstance(spec, dict) and spec.get("agent") and spec["agent"] != generic:
                for effort in spec.get("efforts", []):
                    add(provider, spec["agent"], spec["model"], effort, spec["agent"].split("-{")[0])
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
    for f in CLAUDE_AGENTS.glob("*.md") if CLAUDE_AGENTS.is_dir() and "claude" in PROVIDERS else []:
        if f.stem not in want and GEN_MARK in f.read_text(encoding="utf-8", errors="replace"):
            act(f"remove stale generated agent {f}", f.unlink)
    # Codex: role file per variant + one managed block in config.toml
    if "codex" not in PROVIDERS:
        return
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
    # match the marker by its stable prefix: older versions wrote a different hint after it
    pattern = re.compile(r"# >>> jev-router agents[^\n]*\n.*?" + re.escape(TOML_END) + r"\n?", re.S)
    new_block = "\n".join(block) + "\n"
    old = pattern.search(cfg)
    if old:
        # Codex appends its own tables (e.g. [hooks.state] = the user's hook trust) at the end of the
        # file, which can land INSIDE our block: keep every non-[agents.*] table, re-emitted after it.
        tables = re.split(r"(?m)^(?=\[)", old.group(0).replace(TOML_END, ""))
        foreign = "".join(t for t in tables if t.startswith("[") and not t.startswith("[agents.")).strip("\n")
        new_cfg = cfg[:old.start()] + new_block + cfg[old.end():]
        if foreign:
            new_cfg = new_cfg.rstrip("\n") + "\n\n" + foreign + "\n"
    else:
        new_cfg = cfg.rstrip("\n") + "\n\n" + new_block
    if new_cfg != cfg:
        act(f"{CODEX_CONFIG}: update [agents.*] block ({len(want)} roles)",
            lambda: CODEX_CONFIG.write_text(new_cfg, encoding="utf-8"))


def cmd_catalog():
    items = catalog.write_catalog()
    by = {}
    for s in items:
        for p in s["native_in"]:
            by[p] = by.get(p, 0) + 1
    print(f"catalog: {len(items)} skills -> {catalog.CATALOG}  (native: {by})")


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
    for s in catalog.build_catalog():
        base = s["name"].split(":")[-1]
        names.setdefault(base, []).append(s["name"])
    for base, full in names.items():
        if len(full) > 1:
            print(f"[INFO] same skill name from several sources: {', '.join(full)}")
    print(f"doctor: {problems} problem(s)")
    return problems

