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

Worker agents (one fixed model + effort each; Antigravity pins only a model tier):
    ~/.claude/agents/<name>.md                    Claude Code subagents
    ~/.codex/agents/<name>.toml + [agents.*]      Codex roles (block in ~/.codex/config.toml)
    ~/.gemini/config/agents/<name>/agent.md       Antigravity subagents
The body and description come from the template of the model's role (models.json 'role'):
jev_router/templates/agents/<fast|balanced|deep|test>-worker.md.

Safety: never deletes a real directory. Only junctions this tool can prove it owns (pointing into
the hub or jev_router/skills/) are ever removed/replaced; a name collision is reported, not resolved.
A stale generated agent file (marked GEN_MARK) is removed; its Antigravity folder only when then empty.
"""
import itertools
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
AGY_AGENTS = P.PATHS["agy_agents"]
DEFAULT_ROLE = "balanced"
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
    return sorted(p for p in HUB.iterdir() if catalog.is_skill_dir(p)) if HUB.is_dir() else []


def repo_skills():
    return sorted(p for p in REPO_SKILLS.iterdir() if catalog.is_skill_dir(p))


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
        if not catalog.is_skill_dir(d):
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
    for d in repo_skills():
        ensure_link(HUB / d.name, d, f"hub <- repo '{d.name}'")
    names = _link_hub_skills()
    _drop_stale_links(names)
    if "antigravity" in PROVIDERS:
        _register_hub_with_antigravity()


def _link_hub_skills():
    """2) every hub skill into Claude Code and Codex. Returns the skill names."""
    names = set()
    # a dry run has not linked the repo skills into the hub yet: plan their tool links too
    pending = [] if APPLY else [HUB / d.name for d in repo_skills()]
    for s in hub_skills() + pending:
        if s.name in names:
            continue
        names.add(s.name)
        if "claude" in PROVIDERS:
            ensure_link(CLAUDE_SKILLS / s.name, s, "claude")
        if "codex" in PROVIDERS:
            ensure_link(CODEX_SKILLS / s.name, s, "codex")
    return names


def _drop_stale_links(names):
    """3) drop stale links we own (target gone / skill removed from the hub); ~/.codex/skills is
    Codex's legacy location - its skills are served from ~/.agents/skills instead."""
    for base in (CLAUDE_SKILLS, CODEX_SKILLS, LEGACY_CODEX_SKILLS):
        links = base.iterdir() if base.is_dir() else []
        for link in links:
            if owned(link) and (base == LEGACY_CODEX_SKILLS or link.name not in names or not link.exists()):
                act(f"remove stale/legacy link {link}", lambda l=link: rm_junction(l))


def _register_hub_with_antigravity():
    """4) Antigravity: one manifest entry for the whole hub. Must be an ABSOLUTE path: agy 1.2.9
    rejects "~/.skills" at runtime ("must be an absolute path"), despite its docs."""
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


_TEMPLATE_FRONTMATTER = re.compile(r"---[ \t]*\n(.*?)\n---[ \t]*\n", re.S)
_TEMPLATE_FIELD = re.compile(r"^(\w+):(.*)$", re.M)


def _split_template(path):
    text = path.read_text(encoding="utf-8")
    m = _TEMPLATE_FRONTMATTER.match(text)
    fm = {k: v.strip() for k, v in _TEMPLATE_FIELD.findall(m.group(1))} if m else {}
    return fm, (text[m.end():] if m else text).strip()


def _template(name):
    tpl = AGENT_TEMPLATES / f"{name}.md"
    return _split_template(tpl) if tpl.exists() else ({}, "Do the delegated task carefully.")


def _role_template(mdef):
    """Template file of a model's role (models.json 'role'): fast-worker, balanced-worker, deep-worker."""
    return f"{mdef.get('role') or DEFAULT_ROLE}-worker"


def _generic_variants(provider, generic):
    """(agent name template, model, effort, template file) for every selectable model x every allowed
    effort under the provider's generic agent template."""
    from . import core  # local import: only needed here
    for model, mdef in core.models_for(provider).items():
        for effort in mdef["levels"]:
            yield generic, model, effort, _role_template(mdef)


def _tier_variants(tiers, generic):
    """(agent name template, model, effort, template file) of the tier-specific agents."""
    specs = [s for s in tiers.values() if isinstance(s, dict) and s.get("agent") and s["agent"] != generic]
    for spec in specs:
        for effort in spec.get("efforts", []):
            yield spec["agent"], spec["model"], effort, spec["agent"].split("-{")[0]


def planned_agents():
    """[(provider, name, model, effort, description, body)].

    1) Every selectable model x every allowed effort level (models.json, 'ultra' already removed
       by core.models_for) under the provider's agent_template - so whatever model + effort JEV
       picks, a matching fixed agent/role exists.
    2) Tier-specific agents whose template isn't the provider's generic one (e.g. test-worker-*),
       for that tier's own effort range.
    3) Antigravity: one agent per model tier (effort None) - see _antigravity_agents.
    Body/description come from templates/agents/<role>-worker.md (the model's role in models.json;
    a tier agent such as test-worker-{effort} uses test-worker.md)."""
    targets = json.loads((PKG / "config" / "targets.json").read_text(encoding="utf-8"))
    out = _antigravity_agents(targets) if "antigravity" in PROVIDERS else []
    seen = set()
    for provider in (p for p in ("claude", "codex") if p in PROVIDERS):
        cfg = targets.get(provider, {})
        generic = cfg.get("agent_template")
        variants = itertools.chain(_generic_variants(provider, generic), _tier_variants(cfg.get("tiers", {}), generic))
        for agent_tpl, model, effort, tpl_name in variants:
            name = agent_tpl.format(model=model, model_=model.replace(".", "_"), effort=effort)
            if (provider, name) in seen:
                continue
            seen.add((provider, name))
            fm, body = _template(tpl_name)
            desc = (fm.get("description") or f"{model} worker").rstrip(".")
            out.append((provider, name, model, effort,
                        f"{desc}. Fixed model {model}, reasoning effort {effort}. Use when the [router] context names {name}.", body))
    return out


def _antigravity_agents(targets):
    """[(provider, name, tier, None, description, body)]: one agent per model tier. An Antigravity agent
    pins only a tier (flash / pro, models.json 'agent_tier'), never an effort, so the name carries the
    tier (targets.json antigravity.agent_template) and the first selectable model of a tier defines it."""
    from . import core  # local import: only needed here
    agent_tpl = targets.get("antigravity", {}).get("agent_template")
    if not agent_tpl:
        return []
    out, seen = [], set()
    tiered = [(m, d) for m, d in core.models_for("antigravity").items() if d.get("agent_tier")]
    for model, mdef in tiered:
        name = agent_tpl.format(tier=mdef["agent_tier"])
        if name in seen:
            continue
        seen.add(name)
        fm, body = _template(_role_template(mdef))
        desc = (fm.get("description") or f"{model} worker").rstrip(".")
        out.append(("antigravity", name, mdef["agent_tier"], None,
                    f"{desc}. Model tier {mdef['agent_tier']} ({model}); Antigravity sets the reasoning effort. "
                    f"Use when the [router] context names {name}.", body))
    return out


def _write_if_changed(path, content):
    if not path.exists() or path.read_text(encoding="utf-8") != content:
        act(f"write {path}", lambda: (path.parent.mkdir(parents=True, exist_ok=True),
                                      path.write_text(content, encoding="utf-8")))


def _remove_stale(folder, pattern, want, label, name_of=lambda f: f.stem, remove=lambda f: f.unlink()):
    """Removes the generated files in `folder` that are no longer planned (never a hand-written one)."""
    for f in folder.glob(pattern) if folder.is_dir() else []:
        if name_of(f) not in want and GEN_MARK in f.read_text(encoding="utf-8", errors="replace"):
            act(f"remove stale generated {label} {f}", lambda f=f: remove(f))


def _remove_agent_folder(agent_md):
    """An Antigravity agent: its agent.md, then the folder - only when nothing else is left in it."""
    agent_md.unlink()
    if not any(agent_md.parent.iterdir()):
        agent_md.parent.rmdir()


def cmd_agents():
    plan = planned_agents()
    _write_claude_agents([a for a in plan if a[0] == "claude"])
    if "codex" in PROVIDERS:
        _write_codex_agents([a for a in plan if a[0] == "codex"])
    if "antigravity" in PROVIDERS:
        _write_antigravity_agents([a for a in plan if a[0] == "antigravity"])


def _write_antigravity_agents(plan):
    """Antigravity: <name>/agent.md per model tier, as a subagent only - selected as the main agent
    (`agy --agent`), agy keeps the session's model. The system prompt sits under an H1 heading."""
    for _, name, tier, _, desc, body in plan:
        _write_if_changed(AGY_AGENTS / name / "agent.md",
                          f"---\nname: {name}\ndescription: {json.dumps(desc)}\nmodel: {tier}\nsubagent: true\n"
                          f"mainAgent: false\n# {GEN_MARK}\n---\n# Instructions\n{body}\n")
    _remove_stale(AGY_AGENTS, "*/agent.md", {a[1] for a in plan}, "antigravity agent",
                  name_of=lambda f: f.parent.name, remove=_remove_agent_folder)


def _write_claude_agents(plan):
    """Claude: one .md per variant."""
    for _, name, model, effort, desc, body in plan:
        _write_if_changed(CLAUDE_AGENTS / f"{name}.md",
                          f"---\nname: {name}\ndescription: {desc}\nmodel: {model}\neffort: {effort}\n# {GEN_MARK}\n---\n{body}\n")
    if "claude" in PROVIDERS:
        _remove_stale(CLAUDE_AGENTS, "*.md", {a[1] for a in plan}, "agent")


def _write_codex_agents(plan):
    """Codex: role file per variant + one managed block in config.toml."""
    block = [TOML_BEGIN]
    for _, name, model, effort, desc, body in plan:
        path = CODEX_AGENTS / f"{name}.toml"
        _write_if_changed(path, f"# {GEN_MARK}\nmodel = {json.dumps(model)}\nmodel_reasoning_effort = {json.dumps(effort)}\n"
                                f"developer_instructions = {json.dumps(body)}\n")
        block += [f"[agents.{name}]", f"description = {json.dumps(desc)}",
                  f"config_file = {json.dumps(str(path).replace(chr(92), '/'))}", ""]
    want = {a[1] for a in plan}
    _remove_stale(CODEX_AGENTS, "*.toml", want, "codex role")
    block.append(TOML_END)
    cfg = CODEX_CONFIG.read_text(encoding="utf-8") if CODEX_CONFIG.exists() else ""
    new_cfg = _with_agents_block(cfg, "\n".join(block) + "\n")
    if new_cfg != cfg:
        act(f"{CODEX_CONFIG}: update [agents.*] block ({len(want)} roles)",
            lambda: CODEX_CONFIG.write_text(new_cfg, encoding="utf-8"))


def _with_agents_block(cfg, new_block):
    """config.toml text with our generated [agents.*] block replaced, or appended when missing."""
    # match the marker by its stable prefix: older versions wrote a different hint after it
    pattern = re.compile(r"# >>> jev-router agents[^\n]*\n.*?" + re.escape(TOML_END) + r"\n?", re.S)
    old = pattern.search(cfg)
    if not old:
        return cfg.rstrip("\n") + "\n\n" + new_block
    # Codex appends its own tables (e.g. [hooks.state] = the user's hook trust) at the end of the
    # file, which can land INSIDE our block: keep every non-[agents.*] table, re-emitted after it.
    tables = re.split(r"(?m)^(?=\[)", old.group(0).replace(TOML_END, ""))
    foreign = "".join(t for t in tables if t.startswith("[") and not t.startswith("[agents.")).strip("\n")
    new_cfg = cfg[:old.start()] + new_block + cfg[old.end():]
    return new_cfg.rstrip("\n") + "\n\n" + foreign + "\n" if foreign else new_cfg


def cmd_catalog():
    items = catalog.write_catalog()
    by = {}
    for s in items:
        for p in s["native_in"]:
            by[p] = by.get(p, 0) + 1
    print(f"catalog: {len(items)} skills -> {catalog.CATALOG}  (native: {by})")


def _check_skill_folder(base):
    """Reports broken links and folders without a SKILL.md; returns the number of broken links."""
    problems = 0
    for p in base.iterdir():
        if p.name in APP_MANAGED or p.is_file():
            continue
        if is_junction(p) and not p.exists():
            print(f"[FAIL] broken link: {p}")
            problems += 1
        elif p.is_dir() and not catalog.is_skill_dir(p):
            print(f"[WARN] no SKILL.md: {p}")
    return problems


def cmd_doctor():
    problems = 0
    for base in (CLAUDE_SKILLS, CODEX_SKILLS, HUB):
        if base.is_dir():
            problems += _check_skill_folder(base)
        else:
            print(f"[WARN] missing: {base}")
    names = {}
    for s in catalog.build_catalog():
        names.setdefault(s["name"].split(":")[-1], []).append(s["name"])
    for full in names.values():
        if len(full) > 1:
            print(f"[INFO] same skill name from several sources: {', '.join(full)}")
    print(f"doctor: {problems} problem(s)")
    return problems

