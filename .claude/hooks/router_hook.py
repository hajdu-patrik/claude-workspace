#!/usr/bin/env python3
"""Claude Code UserPromptSubmit hook: Jev-alapu router, helyi kulcsszavas tartalekkal. Csak standard konyvtar.

Backend: ha van TYPESAFE_API_KEY, a Jev dont; ha nincs, vagy a Jev-hivas elbukik, a helyi osztalyozo.

Bemenet: a hook JSON-ja stdin-en ({"prompt": ...}).
Kimenet: JSON additionalContext-tel. Mindig exit 0: a router sosem blokkolja a promptot.
"""
import hashlib
import json
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(os.environ.get("CLAUDE_PROJECT_DIR") or Path(__file__).resolve().parents[2])
CFG_DIR = ROOT / ".claude" / "router"
LOG_FILE = ROOT / "logs" / "routing.jsonl"

API_URL = os.environ.get("TYPESAFE_API_URL", "https://api.typesafe.ai/v1/systemone")
JEV_MODEL = os.environ.get("JEV_MODEL", "jev-1.13.0")  # pinelt verzio, nem alias
TIMEOUT_S = float(os.environ.get("JEV_TIMEOUT", "4"))
MIN_CONF = float(os.environ.get("ROUTER_MIN_CONFIDENCE", "0.6"))
DESTRUCTIVE_T = float(os.environ.get("ROUTER_DESTRUCTIVE_THRESHOLD", "0.3"))
LONG_CTX_T = float(os.environ.get("ROUTER_LONG_CONTEXT_THRESHOLD", "0.7"))
MAX_STATE_CHARS = 6000  # a Jev state-limitje 32k token; roviden tartjuk (context rot)
CLOUD = os.environ.get("ROUTER_MODE", "").lower() == "cloud"

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
# Kodszintu biztonsagi halo a Jev mellett (a Jev prompt injectionnel befolyasolhato).
# Ekezet nelkuli, kisbetus szovegen fut (is_destructive normalizal), igy "Töröld" es "torold" is talal.
DESTRUCTIVE_RE = re.compile(
    r"(torol|delete|\brm\s+-|drop\s+(table|database)|truncate|kuldd?\s+el|elkuld|send\s+(an?\s+)?(e-?mail|message)"
    r"|\butal|fizess|fizesd|vasarol|rendeld\s+meg|\border\b|publikal|kozze|publish|posztold|tweeteld"
    r"|push\w*\s+(-f\b|--force)|force[- ]?push|push\w*\s+force|force-?szal|reset\s+--hard|felulir|overwrite"
    r"|\bformat\w*\s+(a\s+)?(lemez|disk|meghajto|drive)|uninstall|eltavolit)"
)


def _norm(text):
    t = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in t if not unicodedata.combining(c))


def is_destructive(prompt):
    return bool(DESTRUCTIVE_RE.search(_norm(prompt)))


# Kezi felulbiralas a promptban: #fable #sonnet #opus #codex #gemini #norouter #privat
# Haiku szandekosan nincs felsorolva: a Claude-oldali routing csak sonnet/opus/fable-t valaszthat.
OVERRIDES = {"#fable": "fast", "#sonnet": "main", "#opus": "deep", "#codex": "cli:codex", "#gemini": "cli:gemini"}
SKIP_TAGS = ("#norouter", "#privat")

AGENTS = {
    "main": "Answer directly in this session; do not spawn a subagent.",
    "fast": "Delegate the work to the `fast-worker` subagent (Fable).",
    "test": "Delegate the work to the `test-worker` subagent (Sonnet).",
    "deep": "Delegate the work to the `deep-worker` subagent (Opus).",
    "cli:codex": "Use the `cli-bridge` skill to run this task with Codex (ChatGPT subscription).",
    "cli:gemini": "Use the `cli-bridge` skill to run this task with Gemini (Google AI Pro, long context).",
}
VERIFY = {
    "codex": "Then get an independent review of the result via the `cli-bridge` skill (codex).",
    "gemini": "Then have Gemini solve it independently via the `cli-bridge` skill (gemini) and compare; report any disagreement.",
}


# --- Helyi (kulcs nelkuli) backend -------------------------------------------------------------
# Kulcsszo-alapu osztalyozo magyar + angol promptokra. A Jev-valasszal azonos alaku "answers"-t ad,
# igy a decide() valtozatlan. ROUTER_BACKEND: auto (alap: Jev ha van kulcs, kulonben local) | jev | local.
BACKEND = os.environ.get("ROUTER_BACKEND", "auto").lower()

LOCAL_TASK_RE = {  # ekezet nelkuli, kisbetus szovegen fut
    "test": r"\bteszt|pytest|unit ?test|unittest|\bjest\b|vitest|playwright|coverage|lefedettseg|\btests?\b",
    "code": r"\bkod|refaktor|refactor|\bbug|fuggveny|osztaly|\bmodul|\bapi\b|endpoint|python|javascript|typescript|"
            r"react|next\.?js|\bjava\b|c#|\bsql\b|script|exception|stack ?trace|\bgit\b|commit|\bmerge\b|deploy|"
            r"docker|\.py\b|\.js\b|\.ts\b|implementa|debug|compile|backend|frontend|\brepo|push|branch|pull request|vegpont|fastapi|django|flask|node_modules|fuggoseg|npm\b|\bpip\b",
    "math": r"\bmatek|matematik|oldd meg|egyenlet|bizonyits|integral|deriv|matrix|sajatertek|valoszinuseg|szamold ki|hatarertek|"
            r"\bprim|lemma|\bproof|equation|negyzete|gyoke|szazalek|\d\s*[a-z]\s*[-+*/=]|=\s*\d|\d\s*[-+*/^]\s*\d",
    "study": r"egyetemi|jegyzet|eloadas|vizsga|\bzh\b|kollokvium|tantargy|szakdolgozat|diplomamunka|\btetel|egyetem|felev|"
             r"kurzus|foglald ossze|osszefoglal|konspektus|flashcard",
    "research": r"legfrissebb|legujabb|aktualis|\bma\b|\bmai\b|jelenleg|hirek|\bnews\b|latest|current|arfolyam|"
                r"mennyibe kerul|holnap|idojaras|hany fok|\bara\b|ki (a|az) (jelenlegi )?\w+ (elnoke|vezerigazgatoja|miniszterelnoke)",
    "qa": r"^(mi|mik|ki|kik|mikor|hol|miert|hogyan|hany|melyik|mennyi|what|who|when|where|why|how)\b|magyarazd|"
          r"mit jelent|mi az a|\bexplain",
    "general": r"\birj\b|keszits|tervezd|szervezd|rendezd|\blista|e-?mail|\blevel|mappa|fajl|jegyzokonyv",
}
LOCAL_PRIORITY = ["test", "math", "code", "study", "research", "qa", "general"]  # dontetlennel ez a sorrend
LOCAL_HARD_RE = r"\begesz\b|\bteljes\b|architektur|nehez|bonyolult|reszletes|mikroszolgaltatas|migral|optimaliz|" \
                r"hexagonal|\d{2,}\s*oldal|tobb (fajl|modul)"
LOCAL_LONG_RE = r"\d{2,}\s*oldal|(egesz|teljes|osszes) (kodbazis|repo|projekt|konyv|fajl)"


def local_answers(prompt):
    """Jev-kompatibilis valasz kulcsszavak alapjan. Determinisztikus, halozat nelkul, <1 ms."""
    t = _norm(prompt)
    scores = {k: len(re.findall(p, t)) for k, p in LOCAL_TASK_RE.items()}
    ranked = sorted(LOCAL_PRIORITY, key=lambda k: (-scores[k], LOCAL_PRIORITY.index(k)))
    top, second = ranked[0], ranked[1]
    if scores[top] == 0:
        task, conf = "general", 0.3
    else:
        task, conf = top, min(0.9, 0.5 + 0.15 * (scores[top] - scores[second]))
    hard = bool(re.search(LOCAL_HARD_RE, t))
    if hard:
        level = 2
    elif len(t) < 50 and task in ("qa", "general", "research", "math") and not re.search(r"bizonyits|proof", t):
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


def load_json(name, default):
    try:
        return json.loads((CFG_DIR / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def safe_key(name):
    return "skill_" + re.sub(r"[^a-z0-9_]", "_", name.lower())


def build_questions(skills):
    q = {
        "task": {
            "type": "choice",
            "instructions": "What kind of request is this? The text may be in Hungarian.",
            "criteria": TASKS,
        },
        "difficulty": {
            "type": "score",
            "instructions": "How hard is this request for a skilled expert?",
            "criteria": DIFFICULTY,
        },
        "long_context": {
            "type": "noul",
            "instructions": "The request involves reading a long document, many files, or a whole codebase",
        },
        "needs_web": {
            "type": "noul",
            "instructions": "Answering requires up-to-date information from the internet",
        },
        "destructive": {
            "type": "noul",
            "instructions": "The request asks to delete data, send a message to someone, publish something, or spend money",
        },
    }
    if skills:
        criteria = dict(skills)
        criteria["none"] = "No listed skill is clearly needed for this request"
        q["skill"] = {"type": "choice", "instructions": "Which skill best fits this request?", "criteria": criteria}
        for name, desc in skills.items():  # speculative fan-out: egy Noul skillenkent
            q[safe_key(name)] = {"type": "noul", "instructions": f"The request needs this capability: {desc}"}
    return q


def ask_jev(prompt, questions):
    key = os.environ.get("TYPESAFE_API_KEY")
    if not key:
        raise RuntimeError("TYPESAFE_API_KEY hianyzik")
    body = json.dumps({"state": prompt[:MAX_STATE_CHARS], "model": JEV_MODEL, "questions": questions}).encode("utf-8")
    req = urllib.request.Request(
        API_URL,
        data=body,
        method="POST",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
        return json.loads(resp.read().decode("utf-8"))


def decide(answers, routes, skills):
    """A Jev-valaszokbol determinisztikusan donto kod. Visszaad: dict a donteshez."""
    task = answers["task"]
    diff = answers["difficulty"]
    level = int(round(float(diff.get("score", 1))))
    level = max(0, min(level, len(DIFFICULTY) - 1))
    if float(diff.get("confidence", 0)) < MIN_CONF:
        level = max(level, 1)  # bizonytalan nehezseg: ne menjunk a legolcsobbra

    notes = []
    if float(task.get("confidence", 0)) < MIN_CONF:
        target = routes.get("default", "main")
        notes.append("routing uncertain")
    else:
        target = routes["table"].get(task["choice"], ["main"] * 3)[level]

    if float(answers["long_context"]["noul"]) >= LONG_CTX_T and task["choice"] in ("study", "qa", "general", "research"):
        target = routes.get("long_context_target", "cli:gemini")

    if float(answers["needs_web"]["noul"]) >= LONG_CTX_T:
        notes.append("needs current information: use web search")

    primary, _, verify = target.partition("+verify:")
    if CLOUD:  # felhoben nincs Codex/Gemini bejelentkezes
        primary = routes.get("cloud_replace", {}).get(primary, primary)
        verify = ""

    skill = None
    s = answers.get("skill")
    if s and s.get("choice") != "none" and float(s.get("confidence", 0)) >= MIN_CONF:
        if float(answers.get(safe_key(s["choice"]), {}).get("noul", 0)) >= 0.5:
            skill = s["choice"]

    return {
        "task": task["choice"],
        "task_conf": round(float(task.get("confidence", 0)), 2),
        "level": level,
        "primary": primary,
        "verify": verify,
        "skill": skill,
        "destructive_p": round(float(answers["destructive"]["noul"]), 2),
        "notes": notes,
    }


def render(d, destructive_hit):
    parts = [f"[router] backend={d.get('backend', 'override')} task={d['task']} difficulty={d['level']} conf={d['task_conf']}."]
    parts.append(AGENTS.get(d["primary"], AGENTS["main"]))
    if d.get("verify") in VERIFY:
        parts.append(VERIFY[d["verify"]])
    if d.get("skill"):
        parts.append(f"Relevant skill: `{d['skill']}`.")
    if destructive_hit:
        parts.append("SAFETY: this may be irreversible. List the exact actions and ask for explicit confirmation before executing any of them.")
    if d.get("notes"):
        parts.append("Note: " + ", ".join(d["notes"]) + ".")
    parts.append("Respond in Hungarian.")
    return " ".join(parts)


def log(entry):
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def emit(text):
    # ASCII-escape (json.dumps alapertelmezes): Windows-konzolkodolas-biztos
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": text}}))


def main():
    try:
        prompt = (json.loads(sys.stdin.buffer.read().decode("utf-8")).get("prompt") or "").strip()
    except (ValueError, UnicodeDecodeError, AttributeError):  # Windows: stdin-t mindig UTF-8-kent olvassuk
        return 0
    if len(prompt) < 3 or prompt.startswith("/") or any(t in prompt.lower() for t in SKIP_TAGS):
        return 0  # nincs routing: parancs, ures vagy privat prompt (nem megy a TypeSafe-hez)

    regex_hit = is_destructive(prompt)
    entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "sha": hashlib.sha256(prompt.encode()).hexdigest()[:12],
             "prompt": prompt[:500], "cloud": CLOUD}

    forced = next((v for k, v in OVERRIDES.items() if k in prompt.lower()), None)
    if forced:
        d = {"task": "override", "task_conf": 1.0, "level": 1, "primary": forced, "verify": "",
             "skill": None, "destructive_p": None, "notes": ["manual override"]}
        if CLOUD:
            d["primary"] = load_json("routes.json", {}).get("cloud_replace", {}).get(forced, forced)
        entry.update(d)
        log(entry)
        emit(render(d, regex_hit))
        return 0

    routes = load_json("routes.json", {"default": "main", "table": {}})
    skills = load_json("skills.json", {})
    t0 = time.perf_counter()
    use_jev = BACKEND == "jev" or (BACKEND == "auto" and bool(os.environ.get("TYPESAFE_API_KEY")))
    d = None
    if use_jev:
        try:
            result = ask_jev(prompt, build_questions(skills))
            d = decide(result["answers"], routes, skills)
            d["jev_model"] = result.get("model")
            d["backend"] = "jev"
        except Exception as exc:  # halozat, timeout, 429, rossz valasz: helyi backendre esunk vissza
            entry["error"] = f"{type(exc).__name__}: {exc}"[:200]
    if d is None:
        try:
            d = decide(local_answers(prompt), routes, {})
            d["backend"] = "local"
        except Exception as exc:  # pl. hibas routes.json: soha ne blokkoljunk
            entry.update({"error": f"{type(exc).__name__}: {exc}"[:200],
                          "latency_ms": int((time.perf_counter() - t0) * 1000)})
            log(entry)
            emit("[router] unavailable; answer directly in this session."
                 + (" SAFETY: ask for explicit confirmation before any irreversible action." if regex_hit else "")
                 + " Respond in Hungarian.")
            return 0

    entry.update(d)
    entry["latency_ms"] = int((time.perf_counter() - t0) * 1000)
    log(entry)
    emit(render(d, regex_hit or d["destructive_p"] >= DESTRUCTIVE_T))
    return 0


if __name__ == "__main__":
    sys.exit(main())
