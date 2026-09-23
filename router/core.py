#!/usr/bin/env python3
"""Provider-independent router core: classification + decision + rendering. Standard library only.

Used by every entry point (run_hook.py for Claude Code / Codex CLI / Antigravity CLI hooks, and
mcp_server.py for the hook-less chat modes). This file knows nothing about any tool's I/O
format: just (prompt, provider) -> decision -> instruction text.

Backend: if TYPESAFE_API_KEY is set, JEV (TypeSafe) decides; if not, or the JEV call fails, the
local keyword classifier (local_answers) does - the temporary "mock JEV". ROUTER_BACKEND=
jev|local|auto switches it without code changes once real JEV access exists.

Per-provider "what to pick" (tier -> agent/model/effort/text) lives in routes.json +
targets.json, not here: adding a provider or renaming a model only means editing those.

Config is always read from THIS repo (located via __file__), never from the current project -
the hook is installed globally and runs inside arbitrary other projects.
"""
import json
import os
import re
import sys
import unicodedata
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lang  # noqa: E402
import skill_index  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CFG_DIR = ROOT / "router"
STATE_DIR = Path(os.environ.get("JEV_ROUTER_HOME", str(Path.home() / ".jev-router")))

API_URL = os.environ.get("TYPESAFE_API_URL", "https://api.typesafe.ai/v1/systemone")
JEV_MODEL = os.environ.get("JEV_MODEL", "jev-1.13.0")  # pinned version, not an alias
TIMEOUT_S = float(os.environ.get("JEV_TIMEOUT", "4"))
MIN_CONF = float(os.environ.get("ROUTER_MIN_CONFIDENCE", "0.6"))
DESTRUCTIVE_T = float(os.environ.get("ROUTER_DESTRUCTIVE_THRESHOLD", "0.3"))
LONG_CTX_T = float(os.environ.get("ROUTER_LONG_CONTEXT_THRESHOLD", "0.7"))
MAX_STATE_CHARS = 6000  # JEV's state limit is 32k tokens; keep it short (context rot)
SKILL_CANDIDATES = int(os.environ.get("ROUTER_SKILL_CANDIDATES", "8"))
BACKEND = os.environ.get("ROUTER_BACKEND", "auto").lower()

TASKS = {
    "qa": "A short factual question or a request to explain a concept",
    "general": "A practical everyday task: writing, planning, organizing files or documents",
    "math": "A math problem, calculation, or proof",
    "study": "University coursework: lecture notes, long documents, exam preparation",
    "code": "Writing, fixing, or refactoring program code",
    "test": "Writing, running, or analyzing software tests",
    "research": "Needs current information from the web",
}
DIFFICULTY = [
    "Trivial: an expert answers in seconds",
    "Moderate: needs a few minutes of focused work",
    "Hard: needs deep multi-step reasoning or large changes",
]
EFFORT_ORDER = ["low", "medium", "high", "xhigh", "max", "ultra"]


def _norm(text):
    t = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in t if not unicodedata.combining(c))


# --- Safety net ------------------------------------------------------------------------------
# Code-level backup for JEV's destructive-question (JEV itself can be swayed by prompt injection).
# Runs on lowercased, accent-stripped text. Deliberately object-bound: a bare "remove"/"order"/
# "pay" is NOT enough ("Remove the unused import", "sort these in order" are harmless) - it needs
# a destructive object or a money/publishing context.
_OBJ = (r"(file|fajl|folder|mappa|director|konyvtar|branch|repo|database|adatbazis|tabla|table|"
        r"record|rekord|user|felhasznalo|account|fiok|profil|email|level|uzenet|message|commit|"
        r"backup|mentes|data|adat|disk|lemez|partition|particio|bucket|cluster|server|szerver|"
        r"container|kontener|volume|project|projekt|everything|mindent|all\b|osszes|program|app\b|"
        r"alkalmazas|package|csomag|photo|foto|kep|document|dokumentum)")
DESTRUCTIVE_RE = re.compile(
    r"(\btorol\w*|\btorl\w*|\bdelete\w*|\berase\w*|\bwipe\w*|\bpurge\w*|\bdestroy\w*"
    r"|\b(remove|eltavolit\w*|tavolits\w*\s+el|uninstall\w*)\b[^.?!\n]{0,40}" + _OBJ +
    r"|\brm\s+-\w*[rf]|\brmdir\b|\bdel\s+/[sq]|drop\s+(table|database|schema)|truncate\s+(table\s+)?\w+"
    r"|git\s+(reset\s+--hard|clean\s+-\w*f|push\s+\S*\s*(-f\b|--force))|force[- ]?push|force-?szal"
    r"|\bfelulir\w*|\bird\s+felul\b|\boverwrite\w*"
    r"|\bforma(t|z)\w*\s+(meg\s+)?(a\s+|az\s+|the\s+)?(\w:\s*)?(lemez|disk|meghajto|drive|partition|particio)"
    r"|\bkuld(d|jed|jon)?\s+el\b|\belkuld\w*|\bkuld\w*\s+([\w-]+\s+){0,3}(uzenet|e-?mail|level|sms)"
    r"|send\s+(an?\s+|the\s+|this\s+|that\s+)?(e-?mail|message|mail|dm|sms|invite|text)"
    r"|\bsend\w*\s[^.?!\n]{0,50}\b(to|by|via)\s+(my\s|him\b|her\b|them\b|the\s+(team|client|customer|boss|group|channel)|\S+@\S+|e-?mail|slack|teams|whatsapp|messenger)"
    r"|\bpublikal\w*|\bkozze\b|\bkozzete\w*|\bpublish\w*|\bposztol\w*|\btweetel\w*|\btweet\s+(it|this)"
    r"|\bpost\w*\s[^.?!\n]{0,30}\b(on|to)\s+(twitter|x\b|linkedin|facebook|instagram|reddit|slack|discord|the\s+blog|my\s+blog)"
    r"|\butal(d|j|jon|ok|junk)\b|\batutal\w*|\bfizes(s|sd|sen)\b|\bfizesd\s+ki|\bvasarol(j|d|jon)\b|\bvegy(el|ed|uk)\s+(meg|egy)"
    r"|\brendeld\s+meg|\bplace\s+(an?\s+)?order|\border\s+[\w\s]{1,30}\b(from|on|online)\b|\bbuy\s+(me\s+)?(an?|the|some|\d+)\b"
    r"|\bpurchase\w*|\bpay\s+(for|the|my|\$|\d)|\btransfer\s+(the\s+)?(\$|\d|money|funds|rent|payment|salary)|\bcheckout\b)"
)


def is_destructive(prompt):
    return bool(DESTRUCTIVE_RE.search(_norm(prompt)))


# --- Local (keyless) backend = temporary JEV mock ---------------------------------------------
# Keyword classifier for Hungarian + English prompts, producing an answers dict shaped exactly
# like the JEV response so decide() never needs to know which backend answered.
LOCAL_TASK_RE = {  # lowercased, accent-stripped text; every category has Hungarian + English keywords
    "test": r"\bteszt|pytest|unit ?test|unittest|\bjest\b|vitest|playwright|cypress|coverage|lefedettseg|\btests?\b|"
            r"\bmock|assert|\bqa\b|\bspec\b|\btesting\b",
    "code": r"\bkod|\bcode\b|refaktor|refactor|\bbug\b|fuggveny|\bfunction\b|osztaly|\bclass\b|\bmodul|\bmodule\b|"
            r"\bapi\b|endpoint|python|javascript|typescript|"
            r"react|next\.?js|\bjava\b|c#|\bsql\b|script|exception|\berror\b|stack ?trace|\bgit\b|commit|\bmerge\b|deploy|"
            r"docker|\.py\b|\.js\b|\.ts\b|implementa|debug|compile|backend|frontend|\brepo|\bpush\b|branch|pull request|"
            r"vegpont|fastapi|django|flask|node_modules|fuggoseg|\bdependenc|npm\b|\bpip\b|\bhook|\bconfig|\bcli\b|"
            r"\bbuild\b|\bregex|\bjson\b|\byaml\b|\bhtml\b|\bcss\b|\bbash\b|powershell|\bfix\b|javits",
    "math": r"\bmatek|matematik|\bmath\b|oldd meg|\bsolve\b|egyenlet|\bequation\b|bizonyits|\bproof|\bprove\b|integral|deriv|"
            r"matrix|sajatertek|eigenvalue|valoszinuseg|probability|szamold ki|\bcalculate\b|hatarertek|\blimit\b|"
            r"\bprim\b|primszam|\bprime\b|lemma|negyzete|gyoke|square root|szazalek|percent|"
            r"(?<![a-z0-9])\d+(\.\d+)?\s*[-+*/^]\s*\d+(\.\d+)?(?![a-z0-9])|(?<![a-z0-9])\d+\s?[a-z]\s*[-+*/=]\s*\d",
    "study": r"egyetemi|jegyzet|eloadas|vizsga|\bzh\b|kollokvium|tantargy|szakdolgozat|diplomamunka|\btetel|egyetem|felev|"
             r"kurzus|foglald ossze|osszefoglal|konspektus|flashcard|"
             r"\buniversity\b|\blecture\b|\bexam\b|midterm|\bcourse\b|\bthesis\b|\bsemester\b|\bsummari[sz]e\b|study notes",
    "research": r"legfrissebb|legujabb|aktualis|\bma\b|\bmai\b|jelenleg|hirek|\bnews\b|latest|\bcurrent\b|arfolyam|"
                r"mennyibe kerul|holnap|\btomorrow\b|\btoday\b|idojaras|\bweather\b|hany fok|\bara\b|\bprice\b|exchange rate|"
                r"ki (a|az) (jelenlegi )?\w+ (elnoke|vezerigazgatoja|miniszterelnoke)|who is the current \w+|"
                r"\bthis (week|month|year)\b|\bezen a heten\b|\bidei\b",
    "qa": r"^(mi|mik|ki|kik|mikor|hol|miert|hogyan|hany|melyik|mennyi|what|who|when|where|why|how|is|are|does|do)\b|"
          r"magyarazd|mit jelent|mi az a|\bexplain|what (does|is)|what's the difference|kulonbseg|difference between",
    "general": r"\birj\b|keszits|tervezd|szervezd|rendezd|\blista|e-?mail|\blevel|mappa|fajl|jegyzokonyv|"
               r"\bwrite\b|\bcreate\b|\bplan\b|\borganize\b|\blist\b|\bfolder\b|\bfile\b|\bdocument\b|\bletter\b|"
               r"\bnote\b|\bemail\b|\bdraft\b|\btranslate\b|forditsd|\bprezentac|\bpresentation\b|\btablazat|spreadsheet",
}
LOCAL_PRIORITY = ["test", "math", "code", "study", "research", "qa", "general"]  # tie-break order
LOCAL_HARD_RE = (r"(egesz|teljes|osszes) (kodbazis|repo|projekt|rendszer|alkalmazas|architektur|modul)|architektur|"
                 r"\bnehez|bonyolult|reszletes|mikroszolgaltatas|migral|optimaliz|hexagonal|\d{2,}\s*oldal|"
                 r"tobb (fajl|modul)|bizonyits|\bentire\b|\bwhole\b|\barchitecture\b|\bcomplex\b|\bdetailed\b|"
                 r"microservice|\bmigrate\b|\bmigration\b|optimi[sz]e|\d{2,}\s*pages?|multiple (files|modules)|"
                 r"\bprove\b|\bproof\b|from scratch|nulladrol|end-to-end|\be2e\b|security audit|biztonsagi audit")
LOCAL_LONG_RE = (r"\d{2,}\s*oldal|(egesz|teljes|osszes) (kodbazis|repo|projekt|konyv|fajl)|"
                 r"\d{2,}\s*pages?|(entire|whole|full) (codebase|repo|project|book|file)")


def local_answers(prompt):
    """JEV-compatible answers from keywords. Deterministic, no network, well under 1 ms."""
    t = _norm(prompt)
    scores = {k: len(re.findall(p, t)) for k, p in LOCAL_TASK_RE.items()}
    ranked = sorted(LOCAL_PRIORITY, key=lambda k: (-scores[k], LOCAL_PRIORITY.index(k)))
    top, second = ranked[0], ranked[1]
    if scores[top] == 0:
        task, conf = "general", 0.3
    else:
        task, conf = top, min(0.9, 0.5 + 0.15 * (scores[top] - scores[second]))
    if re.search(LOCAL_HARD_RE, t):
        level = 2
    elif len(t) < 60 and task in ("qa", "general", "research", "math") and not re.search(r"bizonyits|proof|prove", t):
        level = 0
    else:
        level = 1
    return {
        "task": {"choice": task, "confidence": conf},
        "difficulty": {"score": level, "confidence": 0.7},
        "long_context": {"noul": 0.8 if re.search(LOCAL_LONG_RE, t) else 0.1},
        "needs_web": {"noul": 0.8 if task == "research" else 0.1},
        "destructive": {"noul": 0.9 if is_destructive(prompt) else 0.05},
    }


def local_skill_answer(candidates):
    """Mock of JEV's skill choice: take the pre-filter's best candidate only when it clearly wins."""
    if not candidates:
        return None
    best_score, best = candidates[0]
    second = candidates[1][0] if len(candidates) > 1 else 0.0
    strong = best.get("_name_hit")  # the skill's own name must match - never commit on stray description words
    if strong and best_score >= 6.0 and best_score >= 1.3 * second:
        return best["name"], min(0.9, 0.5 + best_score / 30)
    return None


# --- Config ----------------------------------------------------------------------------------
def load_json(name, default):
    try:
        return json.loads((CFG_DIR / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def base_provider(provider):
    """'claude-chat' (Claude desktop Chat/Cowork via MCP) shares Claude's routes/models/skills."""
    return provider.split("-")[0]


def safe_key(name):
    return "skill_" + re.sub(r"[^a-z0-9_]", "_", name.lower())


def effort_levels_for(provider):
    """{"levels": [...], "excluded": [...], "note": ...} - the provider's reasoning-effort
    vocabulary from models.json, or None if nothing is recorded (then no effort question)."""
    models = load_json("models.json", {})
    m = models.get(provider) or models.get(base_provider(provider))
    if not m:
        return None
    ef = m.get("effort_levels") or {}
    levels = ef.get("levels")
    if not levels and m.get("available_models"):  # codex: union of every model's own levels
        levels = sorted({l for model in m["available_models"] for l in model.get("levels", [])},
                        key=lambda l: EFFORT_ORDER.index(l) if l in EFFORT_ORDER else 99)
    if not levels:
        return None
    return {"levels": levels, "excluded": ef.get("excluded", []), "note": ef.get("note", "")}


def clamp_effort(effort, allowed):
    """Nearest allowed level to `effort` (by EFFORT_ORDER distance; ties go to the higher one).
    This is what makes a tier's effort always valid for the model behind it (e.g. never 'ultra'
    on a model that tops out at 'max')."""
    if not allowed:
        return effort
    if not effort or effort in allowed:
        return effort or allowed[len(allowed) // 2]
    pos = EFFORT_ORDER.index(effort) if effort in EFFORT_ORDER else 2
    return min(allowed, key=lambda a: (abs((EFFORT_ORDER.index(a) if a in EFFORT_ORDER else 2) - pos),
                                       -(EFFORT_ORDER.index(a) if a in EFFORT_ORDER else 2)))


# --- JEV ---------------------------------------------------------------------------------------
def build_questions(skills, effort=None):
    """skills: {name: description} - only the pre-filtered candidates, never the whole catalog."""
    q = {
        "task": {"type": "choice", "instructions": "What kind of request is this? The text may be in Hungarian or English.",
                 "criteria": TASKS},
        "difficulty": {"type": "score", "instructions": "How hard is this request for a skilled expert?",
                       "criteria": DIFFICULTY},
        "long_context": {"type": "noul",
                         "instructions": "The request involves reading a long document, many files, or a whole codebase"},
        "needs_web": {"type": "noul", "instructions": "Answering requires up-to-date information from the internet"},
        "destructive": {"type": "noul",
                        "instructions": "The request asks to delete data, send a message to someone, publish something, or spend money"},
    }
    if skills:
        criteria = dict(skills)
        criteria["none"] = "No listed skill is clearly needed for this request"
        q["skill"] = {"type": "choice", "instructions": "Which skill best fits this request?", "criteria": criteria}
        for name, desc in skills.items():  # speculative fan-out: one Noul per candidate skill
            q[safe_key(name)] = {"type": "noul", "instructions": f"The request needs this capability: {desc}"}
    if effort and effort.get("levels"):
        usable = [l for l in effort["levels"] if l not in set(effort.get("excluded", []))]
        instructions = "Which reasoning-effort level should the model use for this request?"
        if effort.get("note"):
            instructions += " " + effort["note"]
        q["effort"] = {"type": "choice", "instructions": instructions,
                       "criteria": {l: f"Use the '{l}' reasoning-effort level for this request." for l in usable}}
    return q


def ask_jev(prompt, questions):
    key = os.environ.get("TYPESAFE_API_KEY")
    if not key:
        raise RuntimeError("TYPESAFE_API_KEY is missing")
    body = json.dumps({"state": prompt[:MAX_STATE_CHARS], "model": JEV_MODEL, "questions": questions}).encode("utf-8")
    req = urllib.request.Request(API_URL, data=body, method="POST",
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
        return json.loads(resp.read().decode("utf-8"))


def use_jev(backend=None):
    if backend:
        return backend == "jev"
    return BACKEND == "jev" or (BACKEND == "auto" and bool(os.environ.get("TYPESAFE_API_KEY")))


def classify(prompt, skills=None, backend=None, effort=None):
    """(prompt) -> JEV-shaped answers dict. The single function that changes behaviour once real
    JEV access exists: decide() and its callers stay the same.

    skills: [(score, skill_dict)] pre-filter candidates (see skill_index.prefilter).
    effort: dict from effort_levels_for(provider). JEV picks the level itself; the mock derives
    it from difficulty. An excluded level (e.g. 'ultra' for Claude) is stripped here in code too,
    never trusting the model-based answer alone for a hard constraint."""
    candidates = skills or []
    if use_jev(backend):
        result = ask_jev(prompt, build_questions({s["name"]: s["description"][:300] for _, s in candidates}, effort))
        answers = result["answers"]
        answers["_backend"] = "jev"
        answers["_jev_model"] = result.get("model")
    else:
        answers = local_answers(prompt)
        answers["_backend"] = "local"
        picked = local_skill_answer(candidates)
        if picked:
            answers["skill"] = {"choice": picked[0], "confidence": picked[1]}
            answers[safe_key(picked[0])] = {"noul": 0.8}
        if effort and effort.get("levels"):
            usable = [l for l in effort["levels"] if l not in set(effort.get("excluded", []))]
            if usable:
                lvl = float(answers["difficulty"]["score"])
                # 0 -> lowest, 1 -> middle, 2 -> second-highest: the mock never picks the very top
                # level (max/ultra) on its own - that is left to JEV or an explicit override.
                idx = round(lvl / 2 * (len(usable) - 1) * 0.8) if len(usable) > 1 else 0
                answers["effort"] = {"choice": usable[max(0, min(idx, len(usable) - 1))], "confidence": 0.5}

    if effort and effort.get("excluded") and answers.get("effort", {}).get("choice") in set(effort["excluded"]):
        usable = [l for l in effort["levels"] if l not in set(effort["excluded"])]
        if usable:
            answers["effort"] = {"choice": usable[-1], "confidence": answers["effort"].get("confidence", 0.5)}
    return answers


# --- Decision ------------------------------------------------------------------------------------
def is_cloud():
    return os.environ.get("ROUTER_MODE", "").lower() == "cloud" or os.environ.get("CLAUDE_CODE_REMOTE", "").lower() == "true"


def apply_cloud(primary, verify, routes):
    """No Codex/Antigravity login and no user-level agents in a cloud sandbox."""
    if is_cloud():
        return routes.get("cloud_replace", {}).get(primary, primary), ""
    return primary, verify


def decide(answers, routes, skills_by_name=None):
    """Deterministic decision from the answers, with a DIFFERENT routes.json block per provider.
    'primary' is a tier name (main/fast/deep/test, or for Claude cli:codex / cli:antigravity);
    render() translates tier -> concrete agent/model/text via targets.json."""
    task = answers["task"]
    diff = answers["difficulty"]
    level = max(0, min(int(round(float(diff.get("score", 1)))), len(DIFFICULTY) - 1))
    if float(diff.get("confidence", 0)) < MIN_CONF:
        level = max(level, 1)  # uncertain difficulty: don't go for the cheapest option

    notes = []
    if float(task.get("confidence", 0)) < MIN_CONF:
        target = routes.get("default", "main")
        notes.append("routing uncertain")
    else:
        target = routes.get("table", {}).get(task["choice"], ["main"] * 3)[level]

    if float(answers["long_context"]["noul"]) >= LONG_CTX_T and task["choice"] in ("study", "qa", "general", "research"):
        target = routes.get("long_context_target", "deep")
    if float(answers["needs_web"]["noul"]) >= LONG_CTX_T:
        notes.append("needs current information: use web search")

    primary, _, verify = target.partition("+verify:")
    primary, verify = apply_cloud(primary, verify, routes)

    skill = None
    s = answers.get("skill")
    if s and s.get("choice") not in (None, "none") and float(s.get("confidence", 0)) >= MIN_CONF:
        if float(answers.get(safe_key(s["choice"]), {}).get("noul", 0)) >= 0.5:
            skill = s["choice"]

    result = {
        "task": task["choice"], "task_conf": round(float(task.get("confidence", 0)), 2), "level": level,
        "primary": primary, "verify": verify, "skill": skill,
        "destructive_p": round(float(answers["destructive"]["noul"]), 2), "notes": notes,
    }
    if skill and skills_by_name and skill in skills_by_name:
        result["skill_path"] = skills_by_name[skill]["path"]
        result["skill_native"] = skills_by_name[skill]["native_in"]
    if "effort" in answers:
        result["effort"] = answers["effort"].get("choice")
    return result


def resolve_tier(d, targets):
    """(text, effort) for the decided tier. A tier in targets.json is either plain text, or
    {"text": ..., "agent": ..., "model": ..., "efforts": [...]}: the effort is clamped to what
    that tier's model supports and {agent}/{model}/{effort} placeholders are filled in."""
    tiers = targets.get("tiers", {})
    spec = tiers.get(d["primary"]) or tiers.get("main") or "Answer directly in this session."
    if isinstance(spec, str):
        return spec, d.get("effort")
    effort = clamp_effort(d.get("effort"), spec.get("efforts", []))
    agent = spec.get("agent", "")
    if agent and effort and spec.get("efforts"):
        agent = f"{agent}-{effort}"
    model = spec.get("model", "")
    if "{effort}" in model:
        model = model.replace("{effort}", effort or "")
    text = spec.get("text", "").format(agent=agent, model=model, effort=effort or "default")
    return text, effort


def render(d, destructive_hit, targets, provider="claude", lang_code="hu"):
    """The instruction text injected in front of the model's turn."""
    text, effort = resolve_tier(d, targets)
    d["effort"] = effort
    parts = [f"[router] backend={d.get('backend', 'override')} task={d['task']} difficulty={d['level']} "
             f"conf={d['task_conf']} lang={lang_code}.", text]
    if effort and "{effort}" not in json.dumps(targets.get("tiers", {}).get(d["primary"], "")):
        parts.append(f"Reasoning effort: {effort}.")
    if d.get("verify") in targets.get("verify", {}):
        parts.append(targets["verify"][d["verify"]])
    if d.get("skill"):
        if base_provider(provider) in d.get("skill_native", [base_provider(provider)]):
            parts.append(f"Relevant skill: `{d['skill']}` - use it.")
        else:
            parts.append(f"Relevant skill: `{d['skill']}` (from another tool) - read and follow "
                         f"{d.get('skill_path', '')}/SKILL.md before starting.")
    if destructive_hit:
        parts.append("SAFETY: this may be irreversible. List the exact actions and ask for explicit confirmation before executing any of them.")
    if d.get("notes"):
        parts.append("Note: " + ", ".join(d["notes"]) + ".")
    parts.append(lang.respond_line(lang_code))
    return " ".join(p for p in parts if p)


def route(prompt, provider, backend=None):
    """Full pipeline used by both the hook and the MCP server.
    Returns (decision_dict, rendered_text, destructive_hit, error_or_None)."""
    routes_all, targets_all = load_json("routes.json", {}), load_json("targets.json", {})
    routes = routes_all.get(provider) or routes_all.get(base_provider(provider)) or {"default": "main", "table": {}}
    targets = targets_all.get(provider) or targets_all.get(base_provider(provider)) or {"tiers": {"main": "Answer directly in this session."}}
    effort = effort_levels_for(provider)
    lang_code = lang.detect(prompt)
    regex_hit = is_destructive(prompt)

    forced = override_for(prompt, targets)
    if forced:
        primary, _ = apply_cloud(forced, "", routes)
        d = {"task": "override", "task_conf": 1.0, "level": 1, "primary": primary, "verify": "", "skill": None,
             "destructive_p": None, "notes": ["manual override"], "backend": "override", "lang": lang_code}
        return d, render(d, regex_hit, targets, provider, lang_code), regex_hit, None

    catalog = skill_index.load_catalog()
    candidates = skill_index.prefilter(prompt, catalog, SKILL_CANDIDATES)
    by_name = {s["name"]: s for _, s in candidates}
    error = None
    try:
        answers = classify(prompt, candidates, backend=backend, effort=effort)
    except Exception as exc:  # network, timeout, 429, malformed JEV response: fall back to the mock
        error = f"{type(exc).__name__}: {exc}"[:200]
        answers = classify(prompt, candidates, backend="local", effort=effort)
    d = decide(answers, routes, by_name)
    d["backend"] = answers.get("_backend", "local")
    d["lang"] = lang_code
    d["skill_candidates"] = [s["name"] for _, s in candidates[:5]]
    if answers.get("_jev_model"):
        d["jev_model"] = answers["_jev_model"]
    hit = regex_hit or (d["destructive_p"] is not None and d["destructive_p"] >= DESTRUCTIVE_T)
    return d, render(d, hit, targets, provider, lang_code), hit, error


def override_for(prompt, targets):
    """Manual override tags (#opus, #fast, ...) - whole-tag match, so #fast never fires on #faster."""
    low = prompt.lower()
    for tag, tier in targets.get("overrides", {}).items():
        if re.search(r"(?<![\w#])" + re.escape(tag.lower()) + r"(?![\w-])", low):
            return tier
    return None
