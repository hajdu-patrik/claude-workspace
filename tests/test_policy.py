#!/usr/bin/env python3
"""Checks the model policy in jev_router/config/models.json against every place a model or effort is named.
Standard library only.

  - Claude: .claude/settings.json "model", jev_router/templates/agents/*.md "model:", every Claude tier model in
    jev_router/config/targets.json: generic alias of an allowed family, never haiku, never a pinned/dated ID.
  - Every provider: each tier model exists in models.json (for Claude's cli:* tiers: in that
    other provider's list), each tier effort is supported by that model, and no excluded effort
    (policy.excluded_efforts, 'ultra') appears in any tier.
  - Every selectable model's role has a worker template; an agent_tier is one Antigravity accepts.
Runs as part of the test suite: python -m pytest tests -q
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "jev_router"


def main():
    models = json.loads((PKG / "config" / "models.json").read_text(encoding="utf-8"))
    targets = json.loads((PKG / "config" / "targets.json").read_text(encoding="utf-8"))
    banned = set(models.get("policy", {}).get("excluded_efforts", ["ultra"]))
    claude = models["claude"]
    allowed, excluded = set(claude["allowed_families"]), set(claude.get("excluded_families", []))
    errors, found = [], []

    settings = json.loads((ROOT / ".claude/settings.json").read_text(encoding="utf-8"))
    if "model" in settings:
        found.append((".claude/settings.json", settings["model"]))
    for md in sorted((PKG / "templates" / "agents").glob("*.md")):
        m = re.search(r"^model:\s*(\S+)\s*$", md.read_text(encoding="utf-8"), re.M)
        if m:
            found.append((str(md.relative_to(ROOT)).replace("\\", "/"), m.group(1)))
    for where, model in found:
        fam = model.lower()
        if any(x in fam for x in excluded):
            errors.append(f"{where}: '{model}' is an excluded family")
        elif fam not in allowed:
            errors.append(f"{where}: '{model}' is not a generic alias ({', '.join(sorted(allowed))})")
        else:
            print(f"[OK]   {where}: {model}")

    catalogs = {p: {m["id"]: m for m in models.get(p, {}).get("models", [])} for p in ("claude", "codex", "antigravity")}
    for provider, cfg in targets.items():
        if provider.startswith("_"):
            continue
        home = provider.split("-")[0]
        for tier, spec in cfg.get("tiers", {}).items():
            if not isinstance(spec, dict) or not spec.get("model"):
                continue
            src = {"cli:codex": "codex", "cli:antigravity": "antigravity"}.get(tier, home)
            mdef = catalogs.get(src, {}).get(spec["model"])
            where = f"targets.json {provider}.{tier}"
            if mdef is None:
                errors.append(f"{where}: model '{spec['model']}' is not in models.json[{src}]")
                continue
            if not mdef.get("selectable"):
                errors.append(f"{where}: model '{spec['model']}' is not selectable")
            for e in spec.get("efforts", []):
                if e in banned:
                    errors.append(f"{where}: effort '{e}' is excluded by policy")
                elif mdef["levels"] and e not in mdef["levels"]:
                    errors.append(f"{where}: effort '{e}' not supported by '{spec['model']}' ({mdef['levels']})")
            print(f"[OK]   {where}: {spec['model']} {spec.get('efforts', [])}")
    for p, cat in catalogs.items():
        sel = [m for m in cat.values() if m.get("selectable")]
        print(f"[OK]   models.json {p}: {len(sel)} selectable model(s), {len(cat) - len(sel)} not selectable")
        errors += role_errors(p, sel)

    for e in errors:
        print(f"[FAIL] {e}")
    return 1 if errors else 0


AGY_AGENT_TIERS = {"inherit", "flash", "pro", "flash_lite"}  # the only values agy accepts in an agent's `model`


def role_errors(provider, selectable):
    """Every selectable model has a role with a worker template; an Antigravity agent_tier is one agy accepts."""
    errors = []
    for m in selectable:
        where = f"models.json {provider}.{m['id']}"
        if not (PKG / "templates" / "agents" / f"{m.get('role', 'balanced')}-worker.md").is_file():
            errors.append(f"{where}: role '{m.get('role')}' has no templates/agents/<role>-worker.md")
        if "agent_tier" in m and (provider != "antigravity" or m["agent_tier"] not in AGY_AGENT_TIERS):
            errors.append(f"{where}: agent_tier '{m['agent_tier']}' (Antigravity only: {sorted(AGY_AGENT_TIERS)})")
    return errors


def test_model_policy():
    assert main() == 0
