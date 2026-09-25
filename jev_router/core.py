#!/usr/bin/env python3
"""Provider-independent router core: classification + decision + rendering. Standard library only.

Used by every entry point (hooks.py for Claude Code / Codex CLI / Antigravity CLI hooks, and
mcp_server.py for the hook-less chat modes). This file knows nothing about any tool's I/O
format: just (prompt, provider) -> decision -> instruction text.

Backend: if TYPESAFE_API_KEY is set, JEV (TypeSafe) decides; if not, or the JEV call fails, the
local keyword classifier (local_answers) does - the temporary "mock JEV". ROUTER_BACKEND=
jev|local|auto switches it without code changes once real JEV access exists.

Per-provider "what to pick" (tier -> agent/model/effort/text) lives in routes.json +
targets.json, not here: adding a provider or renaming a model only means editing those.

Config is always read from this package (jev_router/config/), never from the current project -
the hook is installed globally and runs inside arbitrary other projects.
"""
import json
import os
import re
import sys
import unicodedata
import urllib.request
from pathlib import Path

from . import catalog, lang

CFG_DIR = Path(__file__).resolve().parent / "config"
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
                 r"\bprove\b|\bproof\b|from scratch|nulladrol|semmibol|end-to-end|\be2e\b|security audit|biztonsagi audit")
LOCAL_LONG_RE = (r"\d{2,}\s*oldal|(egesz|teljes|osszes) (kodbazis|repo|projekt|konyv|fajl)|"
                 r"\d{2,}\s*pages?|(entire|whole|full) (codebase|repo|project|book|file)")


# --- Parallel agents (token budget!) -------------------------------------------------------------
# How many EXTRA agents may run in parallel next to the primary worker. Every extra agent multiplies
# token usage, so this is deliberately strict: 0 is the answer for the vast majority of requests.
MAX_EXTRA_AGENTS = int(os.environ.get("ROUTER_MAX_EXTRA_AGENTS", "4"))
AGENTS_MIN_CONF = 0.7  # fixed and strict, independent of ROUTER_MIN_CONFIDENCE: below it, one agent fewer
AGENT_CRITERIA = {
    "0": "DEFAULT - choose this for the vast majority of requests. One agent does the whole job: questions, "
         "explanations, a bug fix, a feature in one area, a test file, a document, a refactor of one module.",
    "1": "+1 extra agent: the task has two clearly independent, substantial parts that gain real time in parallel "
         "(e.g. implement a feature AND independently write its test suite, or research AND implement).",
    "2": "+2 extra agents: a genuinely complex task with three independent substantial workstreams "
         "(e.g. backend change + frontend change + database migration for one feature).",
    "3": "+3 extra agents: building a complete new page/product feature FROM SCRATCH with backend + frontend + "
         "data layer/tests.",
    "4": "+4 extra agents: very rare - only an exceptionally large from-scratch build that ALSO requires writing "
         "extra tooling first (e.g. a scraper/crawler or custom tools) on top of backend + frontend.",
}
_STREAMS = {
    "backend": r"\bbackend|\bapi\b|endpoint|vegpont|\bserver\b|szerver|\bservice\b|szolgaltatas",
    "frontend": r"\bfrontend|\bui\b|felulet|\bpage\b|\boldal\b|oldalt|\breact\b|\bvue\b|\bcomponent|komponens",
    "data": r"adatbazis|\bdatabase\b|\bdb\b|migrac|migration|\bschema\b|\bsema\b",
    "tests": r"\btest|\bteszt",
    "tooling": r"scraper|scrape|crawler|\btool(s|ing)?\b|eszkoz|\bparser\b",
}
_FROM_SCRATCH = r"from scratch|semmibol|nulladrol|\bnew (page|site|app|feature)\b|uj (oldal|oldalt|alkalmazas|appot)|teljes (oldal|oldalt)"


def local_agents_answer(t, level):
    """Mock of JEV's parallel-agent question - strict on purpose (t: normalized prompt)."""
    if level < 2:
        return {"choice": "0", "confidence": 0.9}
    streams = {k for k, p in _STREAMS.items() if re.search(p, t)}
    extra = 0
    if len(streams) >= 2:
        extra = 1
    if len(streams) >= 3:
        extra = 2
    if re.search(_FROM_SCRATCH, t) and {"backend", "frontend"} <= streams:
        extra = 3
        if "tooling" in streams:
            extra = 4
    return {"choice": str(extra), "confidence": 0.75}


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
        "agents": local_agents_answer(t, level),
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


def user_config():
    """Per-user settings written by the installer (~/.jev-router/config.json): JEV token, remote
    machine name, ... Never part of the repository."""
    try:
        return json.loads((STATE_DIR / "config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def jev_key():
    """TypeSafe/JEV token: the environment wins, then the installer's config file."""
    return os.environ.get("TYPESAFE_API_KEY") or user_config().get("typesafe_api_key") or None


def model_overrides():
    """Per-user model availability (~/.jev-router/models.local.json, written by
    `python install.py models --probe`): {provider: {model_id: {"selectable": bool}}}. The repo's
    models.json is a generic catalog; what a given account can actually use differs per plan."""
    try:
        return json.loads((STATE_DIR / "models.local.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def base_provider(provider):
    """'claude-chat' (Claude desktop Chat/Cowork via MCP) shares Claude's routes/models/skills."""
    return provider.split("-")[0]


def safe_key(name):
    return "skill_" + re.sub(r"[^a-z0-9_]", "_", name.lower())


def excluded_efforts():
    """Levels no provider may ever get (policy.excluded_efforts, default 'ultra')."""
    return set(load_json("models.json", {}).get("policy", {}).get("excluded_efforts", ["ultra"]))


def models_for(provider):
    """{id: model_dict} of the models JEV may choose for this provider (selectable=true), each
    with 'levels' already stripped of the excluded efforts."""
    cfg = load_json("models.json", {})
    m = cfg.get(provider) or cfg.get(base_provider(provider)) or {}
    local = model_overrides().get(base_provider(provider), {})
    banned = excluded_efforts()
    out = {}
    for model in m.get("models", []):
        if local.get(model["id"], {}).get("selectable", model.get("selectable")):
            out[model["id"]] = dict(model, levels=[l for l in model.get("levels", []) if l not in banned])
    return out


def effort_levels_for(provider):
    """{"levels": [...], "excluded": [...]} - union of every selectable model's levels, minus the
    excluded ones, in EFFORT_ORDER. None if the provider has no models recorded."""
    models = models_for(provider)
    levels = sorted({l for m in models.values() for l in m["levels"]},
                    key=lambda l: EFFORT_ORDER.index(l) if l in EFFORT_ORDER else 99)
    if not levels:
        return None
    return {"levels": levels, "excluded": sorted(excluded_efforts()),
            "note": "The chosen level is clamped to what the chosen model supports."}


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
def build_questions(skills, effort=None, models=None):
    """skills: {name: description} - only the pre-filtered candidates, never the whole catalog.
    models: {id: model_dict} from models_for() - every selectable model becomes a criterion of the
    'model' question, with the effort levels it supports."""
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
        "agents": {"type": "choice",
                   "instructions": "How many EXTRA agents should work in parallel next to the main one? Be very strict: "
                                   "every extra agent multiplies token usage. When in doubt, choose the lower number.",
                   "criteria": {k: v for k, v in AGENT_CRITERIA.items() if int(k) <= MAX_EXTRA_AGENTS}},
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
    if models and len(models) > 1:
        q["model"] = {"type": "choice",
                      "instructions": "Which model of this provider fits this request best (capability vs. cost and speed)?",
                      "criteria": {mid: m.get("description", mid) + (f" (effort levels: {', '.join(m['levels'])})" if m["levels"] else "")
                                   for mid, m in models.items()}}
    return q


def ask_jev(prompt, questions):
    key = jev_key()
    if not key:
        raise RuntimeError("no JEV token (TYPESAFE_API_KEY or ~/.jev-router/config.json)")
    body = json.dumps({"state": prompt[:MAX_STATE_CHARS], "model": JEV_MODEL, "questions": questions}).encode("utf-8")
    req = urllib.request.Request(API_URL, data=body, method="POST",
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
        return json.loads(resp.read().decode("utf-8"))


def use_jev(backend=None):
    if backend:
        return backend == "jev"
    return BACKEND == "jev" or (BACKEND == "auto" and bool(jev_key()))


def classify(prompt, skills=None, backend=None, effort=None, models=None):
    """(prompt) -> JEV-shaped answers dict. The single function that changes behaviour once real
    JEV access exists: decide() and its callers stay the same.

    skills: [(score, skill_dict)] pre-filter candidates (see catalog.prefilter).
    effort: dict from effort_levels_for(provider). JEV picks the level itself; the mock derives
    it from difficulty. An excluded level (e.g. 'ultra' for Claude) is stripped here in code too,
    never trusting the model-based answer alone for a hard constraint."""
    candidates = skills or []
    if use_jev(backend):
        answers = _jev_answers(prompt, candidates, effort, models)
    else:
        answers = _mock_answers(prompt, candidates, effort)
    return _enforce_policy(answers, effort, models)


def _jev_answers(prompt, candidates, effort, models):
    result = ask_jev(prompt, build_questions({s["name"]: s["description"][:300] for _, s in candidates}, effort, models))
    answers = result["answers"]
    answers["_backend"] = "jev"
    answers["_jev_model"] = result.get("model")
    return answers


def _mock_answers(prompt, candidates, effort):
    """The local mock: keyword answers + the pre-filter's clear skill winner + an effort from difficulty."""
    answers = local_answers(prompt)
    answers["_backend"] = "local"
    picked = local_skill_answer(candidates)
    if picked:
        answers["skill"] = {"choice": picked[0], "confidence": picked[1]}
        answers[safe_key(picked[0])] = {"noul": 0.8}
    choice = _mock_effort(float(answers["difficulty"]["score"]), effort)
    if choice:
        answers["effort"] = {"choice": choice, "confidence": 0.5}
    return answers


def _mock_effort(lvl, effort):
    """0 -> lowest, 1 -> middle, 2 -> second-highest: the mock never picks the very top level
    (max/ultra) on its own - that is left to JEV or an explicit override. None without levels."""
    if not effort or not effort.get("levels"):
        return None
    usable = [l for l in effort["levels"] if l not in set(effort.get("excluded", []))]
    if not usable:
        return None
    idx = round(lvl / 2 * (len(usable) - 1) * 0.8) if len(usable) > 1 else 0
    return usable[max(0, min(idx, len(usable) - 1))]


def _enforce_policy(answers, effort, models):
    """Hard policy, never trusting the model-based answer alone: an excluded level ('ultra') becomes
    the highest allowed one, and a model JEV invented (or one not selectable) is dropped."""
    banned = excluded_efforts()
    if answers.get("effort", {}).get("choice") in banned:
        usable = [l for l in (effort or {}).get("levels", EFFORT_ORDER) if l not in banned]
        answers["effort"] = {"choice": usable[-1] if usable else "max", "confidence": answers["effort"].get("confidence", 0.5)}
    if "model" in answers and (not models or answers["model"].get("choice") not in models):
        answers.pop("model")
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
    m = answers.get("model")
    if m and float(m.get("confidence", 0)) >= MIN_CONF:
        result["model"] = m["choice"]  # JEV's explicit model pick (validated in classify)
    result["extra_agents"] = extra_agents(answers.get("agents"), level)
    return result


def extra_agents(ans, level):
    """Strict clamp of the parallel-agent answer: 0..MAX_EXTRA_AGENTS, one step lower when the answer
    is not confident, and never more than 1 extra for a request below the 'hard' difficulty level."""
    if not ans:
        return 0
    try:
        n = int(str(ans.get("choice", "0")).lstrip("+"))
    except ValueError:
        return 0
    if float(ans.get("confidence", 0)) < AGENTS_MIN_CONF:
        n -= 1
    if level < 2:
        n = min(n, 1)
    return max(0, min(n, MAX_EXTRA_AGENTS))


def resolve_tier(d, targets, models=None, session_model=None):
    """(text, effort, model, agent) for the decided tier. model is None when the tier answers
    in-session (plain text); agent is the worker the text delegates to, None when the text names none.

    A tier in targets.json is plain text (answer in-session), or a spec
    {"model", "efforts", "agent", "text", "same_model_text", "slug"}. When JEV picked a model
    (d["model"]) and the tier is listed in targets["model_pick_tiers"], targets["model_pick"] is
    used with that model instead. Effort: clamped to the model's real levels (models.json); the
    local mock is additionally kept inside the tier's own 'efforts' range. Placeholders in
    agent/text: {model}, {model_} (dots -> '_', TOML-safe role names), {effort}, {slug}, {agent}."""
    spec = _tier_spec(d, targets)
    if isinstance(spec, str):
        return spec, d.get("effort"), None, None
    model = spec.get("model", "")
    mdef = (models or {}).get(model)
    effort = _tier_effort(d, spec, mdef)
    slug = ((mdef or {}).get("slug") or spec.get("slug") or "{id}").format(id=model, effort=effort or "")
    fields = {"model": model, "model_": model.replace(".", "_"), "effort": effort or "default", "slug": slug}
    fields["agent"] = spec.get("agent", "").format(**fields)
    same_model = session_model and session_model == model and spec.get("same_model_text")
    template = spec["same_model_text"] if same_model else spec.get("text", "")
    agent = fields["agent"] if fields["agent"] and "{agent}" in template else None
    return template.format(**fields), effort, model, agent


def _tier_spec(d, targets):
    """The decided tier's spec from targets.json - targets['model_pick'] with JEV's model when JEV
    picked one for a tier listed in targets['model_pick_tiers']."""
    tiers = targets.get("tiers", {})
    chosen = d.get("model")
    if chosen and targets.get("model_pick") and d["primary"] in targets.get("model_pick_tiers", []):
        return dict(targets["model_pick"], model=chosen)
    return tiers.get(d["primary"]) or tiers.get("main") or "Answer directly in this session."


def _tier_effort(d, spec, mdef):
    """The decided effort clamped to the tier's range (not for JEV's own model pick) and then to
    the model's real levels; None for a model without effort levels."""
    effort = d.get("effort")
    if spec.get("efforts") and not (d.get("model") and d.get("backend") == "jev"):
        effort = clamp_effort(effort, spec["efforts"])
    if mdef is not None:
        effort = clamp_effort(effort, mdef["levels"]) if mdef["levels"] else None
    return effort


def render(d, destructive_hit, targets, provider="claude", lang_code="hu", models=None, session_model=None):
    """The instruction text injected in front of the model's turn."""
    text, effort, model, agent = resolve_tier(d, targets, models, session_model)
    d["effort"] = effort
    if model:
        d["target_model"] = model
    if agent:
        d["target_agent"] = agent
    parts = [f"[router] backend={d.get('backend', 'override')} task={d['task']} difficulty={d['level']} "
             f"conf={d['task_conf']} lang={lang_code}.", text]
    if effort and effort not in text:
        parts.append(f"Reasoning effort: {effort}.")
    n = d.get("extra_agents", 0)
    if n:
        parts.append(f"Parallelism: up to {n} extra agent(s) may run in parallel (same model and effort as above), "
                     f"only for genuinely independent parts; split the work, then merge and verify the results.")
    elif d.get("task") != "override":
        parts.append("Parallelism: none - no extra parallel agents.")
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


# Only the start of an absolute path - a real path can contain spaces ("7. Félév"), so the rest is
# grown word by word by _longest_existing_prefix instead of matched in one no-whitespace token.
FOREIGN_PATH_START_RE = re.compile(r'[A-Za-z]:[\\/]|(?<![:\w])/(?=\S)')
FOREIGN_PATH_MAX_CHARS = 200  # bounds how far a candidate can grow
FOREIGN_PATH_MAX_WORDS = 12
FOREIGN_PATH_MAX_STARTS = 20  # bounds how many candidate starts a pathological prompt can trigger


def _project_root(path):
    """Nearest existing ancestor of `path` (itself included) that owns a CLAUDE.md or .claude/, or
    None. Bounded walk: a pathological path must never hang a hook."""
    node = path if path.is_dir() else path.parent
    for _ in range(50):
        if node.exists() and ((node / "CLAUDE.md").is_file() or (node / ".claude").is_dir()):
            return node
        parent = node.parent
        if parent == node:
            return None
        node = parent
    return None


def _deepest_existing_ancestor(path):
    """`path` itself if it exists, else its nearest existing ancestor; None if nothing exists."""
    for node in (path, *path.parents):
        if node.exists():
            return node
    return None


def _longest_existing_prefix(text):
    """The longest existing filesystem path found by growing a candidate word by word through
    `text` (bounded) and, at each step, walking up to the candidate's nearest existing ancestor.
    Handles both a trailing non-path suffix in one no-whitespace token (".../other/src/app.py more
    text": stop at ".../other") and a space-containing path component ("7. Félév": a later word
    completes it) without needing to know in advance which case applies."""
    words = text[:FOREIGN_PATH_MAX_CHARS].split()
    best_len, best = -1, None
    candidate = ""
    for word in words[:FOREIGN_PATH_MAX_WORDS]:
        candidate = f"{candidate} {word}".strip() if candidate else word
        trimmed = candidate.rstrip(".,;:'\")]}")
        try:
            ancestor = _deepest_existing_ancestor(Path(trimmed))
        except OSError:
            break
        if ancestor is not None and len(str(ancestor)) > best_len:
            best_len, best = len(str(ancestor)), str(ancestor)
    return best


def foreign_project_note(prompt, cwd):
    """None, or one short heads-up when `prompt` names an absolute path into a DIFFERENT project
    (its own CLAUDE.md/.claude) than `cwd`: Workflow and Agent tool custom subagent types are scoped
    to the session's own root, so a workflow built for that other project's agents (e.g. orchestrator,
    backend, frontend) cannot resolve them from here. Purely additive and best-effort: never raises,
    and a path that cannot be resolved is silently skipped rather than reported."""
    try:
        if not cwd:
            return None
        cwd_path = Path(cwd).resolve()
        here = _project_root(cwd_path) or cwd_path
        starts = FOREIGN_PATH_START_RE.finditer(prompt)
        for count, match in enumerate(starts):
            if count >= FOREIGN_PATH_MAX_STARTS:
                break
            existing = _longest_existing_prefix(prompt[match.start():])
            if not existing:
                continue
            try:
                resolved = Path(existing).resolve()
            except OSError:
                continue
            project = _project_root(resolved)
            if not project or project == here or here.is_relative_to(project) or project.is_relative_to(here):
                continue
            return (f"this prompt names {project}, a different project with its own CLAUDE.md/.claude config - "
                    f"Workflow and Agent tool custom subagent types are scoped to this session's own root "
                    f"({here}), not to that path")
    except Exception:  # noqa: BLE001 - best-effort only, must never affect routing
        pass
    return None


def route(prompt, provider, backend=None, session_model=None):
    """Full pipeline used by both the hook and the MCP server. session_model: the model the calling
    session already runs (Codex's hook payload reports it) - lets a tier say "stay in-session".
    Returns (decision_dict, rendered_text, destructive_hit, error_or_None)."""
    routes_all, targets_all = load_json("routes.json", {}), load_json("targets.json", {})
    routes = routes_all.get(provider) or routes_all.get(base_provider(provider)) or {"default": "main", "table": {}}
    targets = targets_all.get(provider) or targets_all.get(base_provider(provider)) or {"tiers": {"main": "Answer directly in this session."}}
    effort = effort_levels_for(provider)
    models = models_for(provider)
    lang_code = lang.detect(prompt)
    regex_hit = is_destructive(prompt)

    forced = override_for(prompt, targets)
    if forced:
        primary, _ = apply_cloud(forced, "", routes)
        d = {"task": "override", "task_conf": 1.0, "level": 1, "primary": primary, "verify": "", "skill": None,
             "destructive_p": None, "notes": ["manual override"], "backend": "override", "lang": lang_code}
        return d, render(d, regex_hit, targets, provider, lang_code, models, session_model), regex_hit, None

    candidates = catalog.prefilter(prompt, catalog.load_catalog(), SKILL_CANDIDATES)
    by_name = {s["name"]: s for _, s in candidates}
    error = None
    try:
        answers = classify(prompt, candidates, backend=backend, effort=effort, models=models)
    except Exception as exc:  # network, timeout, 429, malformed JEV response: fall back to the mock
        error = f"{type(exc).__name__}: {exc}"[:200]
        answers = classify(prompt, candidates, backend="local", effort=effort, models=models)
    d = decide(answers, routes, by_name)
    d["backend"] = answers.get("_backend", "local")
    d["lang"] = lang_code
    d["skill_candidates"] = [s["name"] for _, s in candidates[:5]]
    if answers.get("_jev_model"):
        d["jev_model"] = answers["_jev_model"]
    hit = regex_hit or (d["destructive_p"] is not None and d["destructive_p"] >= DESTRUCTIVE_T)
    return d, render(d, hit, targets, provider, lang_code, models, session_model), hit, error


def override_for(prompt, targets):
    """Manual override tags (#opus, #fast, ...) - whole-tag match, so #fast never fires on #faster."""
    low = prompt.lower()
    for tag, tier in targets.get("overrides", {}).items():
        if re.search(r"(?<![\w#])" + re.escape(tag.lower()) + r"(?![\w-])", low):
            return tier
    return None
