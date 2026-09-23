#!/usr/bin/env python3
"""Checks the model-family policy in router/models.json. Standard library only.

Checks:
  - Claude: .claude/settings.json "model", every agents/*.md "model:" field and every Claude tier
    model in router/targets.json must be the generic alias of an allowed family (e.g. "opus"),
    never an excluded family (haiku) or a pinned/dated model ID (e.g. "claude-opus-5-5").
  - Codex / Antigravity: every tier model in router/targets.json (with each allowed effort filled
    in for "{effort}" templates) must exist in that provider's verified available_models list,
    and a Codex tier's efforts must be supported by that model.
Exit code: 0 = OK, 1 = policy violation.  Usage: python scripts/check_models.py
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    policy = json.loads((ROOT / "router/models.json").read_text(encoding="utf-8"))["claude"]
    allowed = set(policy["allowed_families"])
    excluded = set(policy.get("excluded_families", []))
    found = []
    settings = json.loads((ROOT / ".claude/settings.json").read_text(encoding="utf-8"))
    if "model" in settings:
        found.append((".claude/settings.json", settings["model"]))
    for md in sorted((ROOT / "agents").glob("*.md")):
        m = re.search(r"^model:\s*(\S+)\s*$", md.read_text(encoding="utf-8"), re.M)
        if m:
            found.append((str(md.relative_to(ROOT)).replace("\\", "/"), m.group(1)))
    targets = json.loads((ROOT / "router/targets.json").read_text(encoding="utf-8"))
    for tier, spec in targets.get("claude", {}).get("tiers", {}).items():
        if isinstance(spec, dict) and spec.get("agent") and spec.get("model"):
            found.append((f"targets.json claude.{tier}", spec["model"]))
    errors = []
    models = json.loads((ROOT / "router/models.json").read_text(encoding="utf-8"))
    for provider in ("codex", "antigravity"):
        avail = {m["slug"]: m.get("levels") for m in models.get(provider, {}).get("available_models", [])}
        for tier, spec in targets.get(provider, {}).get("tiers", {}).items():
            if not isinstance(spec, dict) or not spec.get("model"):
                continue
            for effort in spec.get("efforts") or [None]:
                slug = spec["model"].replace("{effort}", effort or "")
                if slug not in avail:
                    errors.append(f"targets.json {provider}.{tier}: model '{slug}' is not in the verified model list")
                elif provider == "codex" and effort and avail[slug] and effort not in avail[slug]:
                    errors.append(f"targets.json codex.{tier}: effort '{effort}' not supported by '{slug}'")
                else:
                    print(f"[OK]   targets.json {provider}.{tier}: {slug}" + (f" @ {effort}" if effort and provider == "codex" else ""))
    for where, model in found:
        fam = model.lower()
        if any(x in fam for x in excluded):
            errors.append(f"{where}: '{model}' is an excluded family ({', '.join(sorted(excluded))})")
        elif fam not in allowed:
            errors.append(f"{where}: '{model}' is not a generic alias; allowed: {', '.join(sorted(allowed))}")
        else:
            print(f"[OK]   {where}: {model}")
    for e in errors:
        print(f"[FAIL] {e}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
