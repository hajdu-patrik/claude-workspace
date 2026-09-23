#!/usr/bin/env python3
"""A .claude/router/models.json modellcsalad-szabalyanak ellenorzese. Csak standard konyvtar.

Ellenorzi: .claude/settings.json "model" es minden .claude/agents/*.md "model:" mezo
  - az engedelyezett csaladok (allowed_families) generikus aliasa legyen (pl. "opus"),
  - kizart csalad (excluded_families, pl. haiku) ne szerepeljen,
  - rogzitett/datumozott modell-ID (pl. "claude-opus-5-5") ne szerepeljen.
Kilepesi kod: 0 = rendben, 1 = szabalysertes.  Hasznalat: python scripts/check_models.py
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    policy = json.loads((ROOT / ".claude/router/models.json").read_text(encoding="utf-8"))["claude"]
    allowed = set(policy["allowed_families"])
    excluded = set(policy.get("excluded_families", []))
    found = []
    settings = json.loads((ROOT / ".claude/settings.json").read_text(encoding="utf-8"))
    if "model" in settings:
        found.append((".claude/settings.json", settings["model"]))
    for md in sorted((ROOT / ".claude/agents").glob("*.md")):
        m = re.search(r"^model:\s*(\S+)\s*$", md.read_text(encoding="utf-8"), re.M)
        if m:
            found.append((str(md.relative_to(ROOT)).replace("\\", "/"), m.group(1)))
    errors = []
    for where, model in found:
        fam = model.lower()
        if any(x in fam for x in excluded):
            errors.append(f"{where}: '{model}' kizart csalad ({', '.join(sorted(excluded))})")
        elif fam not in allowed:
            errors.append(f"{where}: '{model}' nem generikus alias; engedelyezett: {', '.join(sorted(allowed))}")
        else:
            print(f"[OK]   {where}: {model}")
    for e in errors:
        print(f"[HIBA] {e}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
