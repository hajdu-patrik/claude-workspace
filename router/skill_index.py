#!/usr/bin/env python3
"""Skill catalog + cheap pre-filter for the router. Standard library only.

Every skill on this machine - whichever tool it was installed for - is indexed into one catalog
(~/.skills/catalog.json) so the router (JEV, or the local mock until JEV access exists) can pick
the right one for a prompt, and a skill native to one provider can still be used by another
(the router then tells the model to read that skill's SKILL.md directly).

Sources (read-only; only ~/.skills is managed by us, the rest belong to their apps):
  ~/.skills/<name>/SKILL.md                       - the shared hub (linked into all three tools)
  ~/.claude/skills/synced/**/SKILL.md             - skills synced by the Claude desktop app
  ~/.claude/plugins/synced/**/SKILL.md            - Claude plugin skills (plugin:skill)
  ~/.codex/skills/.system/<name>/SKILL.md         - Codex built-ins
  ~/.gemini/antigravity-cli/builtin/skills/<name> - Antigravity CLI built-ins

~170 skills is far too many to ask JEV about in one request (one choice criterion + one Noul
question each), so prefilter() narrows them to a handful of candidates with an IDF-weighted
keyword overlap first. Skill descriptions are English while prompts are often Hungarian, hence
the small HU->EN glossary.
"""
import json
import math
import os
import re
import time
import unicodedata
from pathlib import Path

HOME = Path.home()
HUB = Path(os.environ.get("JEV_SKILLS_HUB", str(HOME / ".skills")))
CATALOG = HUB / "catalog.json"
PROVIDERS = ["claude", "codex", "antigravity"]

SOURCES = [
    # (root, recursive, native_in, name prefix: None | "plugin" (from the plugin dir) | fixed string)
    # Claude exposes desktop-synced skills as "anthropic-skills:<name>" and plugin skills as
    # "<plugin>:<name>" - the catalog uses the same names so "Relevant skill: X" is invocable as-is.
    (HUB, False, PROVIDERS, None),
    (HOME / ".claude" / "skills" / "synced", True, ["claude"], "anthropic-skills"),
    (HOME / ".claude" / "plugins" / "synced", True, ["claude"], "plugin"),
    (HOME / ".codex" / "skills" / ".system", False, ["codex"], None),
    (HOME / ".gemini" / "antigravity-cli" / "builtin" / "skills", False, ["antigravity"], None),
]

STOP = {
    "the", "and", "for", "with", "use", "used", "using", "when", "this", "that", "from", "into", "your",
    "you", "are", "not", "any", "all", "can", "will", "about", "such", "also", "other", "than", "then",
    "user", "users", "asks", "asked", "wants", "want", "need", "needs", "skill", "skills", "does", "dont",
    "only", "like", "more", "these", "those", "their", "them", "have", "has", "it's", "its", "via", "e.g",
    "egy", "hogy", "nem", "kell", "legyen", "kerlek", "nekem", "majd", "csak", "illetve", "valamint",
    "irj", "keszits", "csinald", "nezd", "ezt", "azt", "meg", "van", "vagy", "mint", "minden",
}
# Hungarian (accent-stripped) stem -> English words that skill descriptions actually use.
GLOSSARY = {
    "prezentac": "presentation slides deck pptx", "dia": "slides deck", "diasor": "slides deck pptx",
    "dokumentum": "document docx", "word": "docx document", "tablazat": "spreadsheet excel xlsx",
    "excel": "spreadsheet xlsx", "pdf": "pdf", "teszt": "test testing", "hibakeres": "debug debugging bug",
    "hiba": "bug debug error", "terv": "plan planning", "tervez": "design plan", "dizajn": "design ui",
    "felulet": "ui interface frontend design", "weboldal": "website frontend web", "adatbazis": "database sql",
    "kep": "image", "grafikon": "chart visualization", "diagram": "diagram chart", "osszefoglal": "summarize summary",
    "kodellenor": "code review", "review": "review", "atnez": "review", "bongesz": "browser",
    "telepit": "deploy install", "felho": "cloud", "kereses": "search", "jegyzet": "notes memory",
    "emlekez": "memory", "reggel": "morning briefing", "otlet": "brainstorming ideas", "refaktor": "refactor",
    "skill": "skill skills", "keszseg": "skill skills", "agens": "agent agents", "utemez": "schedule",
}
FORMAT_NAMES = {"pdf", "docx", "xlsx", "pptx", "csv"}
_TOK = re.compile(r"[a-z0-9][a-z0-9+#.-]{2,}")


def _norm(text):
    t = unicodedata.normalize("NFKD", (text or "").lower())
    return "".join(c for c in t if not unicodedata.combining(c))


def stem(t):
    """Crude English stemmer - enough to match debug/debugging, test/tests/testing, deploy/deployed."""
    for suf in ("ing", "ions", "ion", "ed", "es", "s"):
        if len(t) > len(suf) + 3 and t.endswith(suf):
            return t[: -len(suf)]
    return t


def tokens(text):
    out = set()
    for t in _TOK.findall(_norm(text)):
        t = t.strip(".-")
        if t not in STOP and len(t) >= 3:
            out.add(stem(t))
    return out


def parse_frontmatter(skill_md):
    """(name, description) from a SKILL.md YAML frontmatter - tolerant, no YAML dependency."""
    try:
        text = Path(skill_md).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None, None
    m = re.match(r"^﻿?---\s*\n(.*?)\n---", text, re.S)
    if not m:
        return None, None
    fm, name, desc = m.group(1), None, None
    nm = re.search(r"^name:\s*['\"]?(.+?)['\"]?\s*$", fm, re.M)
    if nm:
        name = nm.group(1).strip()
    dm = re.search(r"^description:\s*(.*)$", fm, re.M)
    if dm:
        first = dm.group(1).strip()
        if first in (">", "|", ">-", "|-", ""):  # folded/literal block: collect indented lines
            rest = fm[dm.end():].split("\n")
            lines = []
            for line in rest[1:] if rest and not rest[0].strip() else rest:
                if line.startswith((" ", "\t")):
                    lines.append(line.strip())
                elif line.strip():
                    break
            desc = " ".join(lines)
        else:
            desc = first.strip("'\"")
    return name, desc


def _skill_dirs(root, recursive):
    if not root.is_dir():
        return []
    if recursive:
        return sorted({p.parent for p in root.rglob("SKILL.md")})
    return sorted(p for p in root.iterdir() if (p / "SKILL.md").is_file())


def build_catalog():
    """Scans every source, dedups by name (first source wins: the hub beats app-managed copies)."""
    seen, items = set(), []
    for root, recursive, native_in, prefix in SOURCES:
        for d in _skill_dirs(root, recursive):
            name, desc = parse_frontmatter(d / "SKILL.md")
            name = name or d.name
            if prefix == "plugin":
                try:  # .../plugins/synced/<account>/<plugin>/skills/<skill>/SKILL.md
                    name = f"{d.relative_to(root).parts[1]}:{name}"
                except (ValueError, IndexError):
                    pass
            elif prefix:
                name = f"{prefix}:{name}"
            if name in seen:
                continue
            seen.add(name)
            items.append({"name": name, "description": (desc or "")[:600],
                          "path": str(d).replace("\\", "/"), "native_in": list(native_in)})
    return items


def write_catalog(items=None):
    items = build_catalog() if items is None else items
    HUB.mkdir(parents=True, exist_ok=True)
    CATALOG.write_text(json.dumps({"generated": time.strftime("%Y-%m-%dT%H:%M:%S"), "skills": items},
                                  indent=1, ensure_ascii=False), encoding="utf-8")
    return items


def load_catalog():
    try:
        return json.loads(CATALOG.read_text(encoding="utf-8")).get("skills", [])
    except (OSError, ValueError):
        return []


def _expand(prompt_tokens):
    out = set(prompt_tokens)
    for t in prompt_tokens:
        for prefix, words in GLOSSARY.items():
            if t.startswith(prefix):
                out.update(stem(w) for w in words.split())
    return out


def prefilter(prompt, catalog, n=8, min_score=1.5):
    """[(score, skill_dict)] best-first, at most n, only candidates scoring >= min_score.
    Score: IDF-weighted overlap of prompt tokens with the skill's name+description tokens; a hit
    on the skill's own name counts double (naming a skill explicitly is the strongest signal)."""
    if not catalog:
        return []
    # name tokens: only the part after "plugin:" - the plugin/vendor prefix says nothing about the task
    docs = [(s, tokens(s["name"].split(":")[-1].replace("-", " ")), tokens(s["description"])) for s in catalog]
    df = {}
    for _, nt, dt in docs:
        for t in nt | dt:
            df[t] = df.get(t, 0) + 1
    N = len(docs)
    q = _expand(tokens(prompt))
    raw = _norm(prompt)
    scored = []
    for s, nt, dt in docs:
        score, hits, name_hits = 0.0, 0, 0
        for t in q:
            if t in nt or t in dt:
                w = math.log(1 + N / df.get(t, 1))
                score += 2 * w if t in nt else w
                hits += 1
                name_hits += t in nt
        # the name counts as "hit" only if most of its words match AND the prompt overlaps the skill in
        # at least two places ("auth" alone does not make google-cloud-recipe-auth the skill for
        # "refactor the auth module"; "learning" alone does not make `learn` the skill for an ML question)
        cov = name_hits / len(nt) if nt else 0.0
        # e.g. "brainstorm" is distinctive on its own; short everyday words ("learn", "docs") are not
        rare_name = cov == 1.0 and all(df.get(t, 0) <= 3 and len(t) >= 6 for t in nt)
        short_single = (len(nt) == 1 and len(next(iter(nt))) < 6  # "learn", "docs": too ambiguous alone
                        and next(iter(nt)) not in FORMAT_NAMES)
        name_hit = cov >= 0.66 and (hits >= 2 or rare_name) and not short_single
        base = s["name"].split(":")[-1].lower()
        if s["name"].lower() in raw:  # explicit mention, e.g. "use the cloud-run-basics skill"
            score += 6
            name_hit = True
        elif ("-" in base or base in FORMAT_NAMES or re.search(r"\b" + re.escape(base) + r"\s+(skill|keszseg)", raw)) and re.search(r"(?<![\w-])" + re.escape(base) + r"(?![a-z0-9])", raw):
            # (?![a-z0-9]) rather than (?![\w-]): Hungarian suffixes are hyphenated ("pdf-et")
            score += 4  # a distinctive base name ("pptx", "cloud-run-basics") named in the prompt
            name_hit = True
        if score >= min_score:
            # _hits/_name_hit: evidence strength, used by the local mock to decide whether to commit
            scored.append((round(score, 2), dict(s, _hits=hits, _name_hit=name_hit)))
    scored.sort(key=lambda x: -x[0])
    return scored[:n]


if __name__ == "__main__":
    import sys
    cat = write_catalog()
    print(f"{len(cat)} skills -> {CATALOG}")
    if len(sys.argv) > 1:
        for sc, s in prefilter(" ".join(sys.argv[1:]), cat):
            print(f"  {sc:6.2f}  {s['name']}")
