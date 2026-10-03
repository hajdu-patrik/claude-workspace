"""SkillSpector gate for the shared skill folder (https://github.com/NVIDIA/SkillSpector).

Every hub skill is scanned before it is linked into the tools. On a DO_NOT_INSTALL verdict the user is
asked (default: no) whether to move it to ~/.jev-router/quarantine/, where no tool (Antigravity reads the
whole hub) and no catalog sees it; a "no" is remembered for that exact content. Unattended runs only
warn: static analysis also flags legitimate skills that run scripts. `skillscan.allow` in config.json
(or --allow-skill) silences the verdict and restores a quarantined skill. CAUTION only warns.
Static analysis by default (`--no-llm`: nothing leaves the machine); `skillscan.llm: true` lets
SkillSpector's own provider settings (SKILLSPECTOR_PROVIDER, ...) add its LLM analysis.
SkillSpector is optional (Python 3.12+, installed as its own tool): without it skills are linked
unscanned with a warning (also when it does not start), and so is a skill whose scan fails.
"""
import hashlib
import json
import os
import shutil
import tempfile
import time
from pathlib import Path

from . import platforms as P

STATE = P.HOME / ".jev-router"
QUARANTINE = STATE / "quarantine"
CACHE = STATE / "state" / "skillscan.json"
EXE = "skillspector"
INSTALL_HINT = "uv tool install git+https://github.com/NVIDIA/skillspector.git"
SAFE, CAUTION, BLOCK = "SAFE", "CAUTION", "DO_NOT_INSTALL"
TIMEOUT_S = {False: 600, True: 1800}  # static / with LLM analysis; large skills take minutes
_SKIP_PARTS = {".git", "__pycache__", "node_modules"}


def find_scanner():
    return P.find_exe(EXE)


UV_TOOL_HINT = ("SkillSpector's uv tool folder is probably not visible to this Python (a packaged Python on "
                "Windows virtualizes %APPDATA%). Reinstall it outside AppData: set UV_TOOL_DIR to e.g. "
                r"%USERPROFILE%\.local\share\uv\tools and run "
                "`uv tool install --force git+https://github.com/NVIDIA/skillspector.git` "
                "(`uv tool upgrade` then needs the same UV_TOOL_DIR).")


class StartError(str):
    """scanner_version() result when the scanner does not start: the first line of its output."""


def scanner_version(exe):
    """The version; "unknown" when it starts but prints nothing usable; a StartError when it does not start."""
    code, out = P.run([exe, "--version"], timeout=30)
    out = (out or "").strip()
    if code != 0:
        return StartError(out.splitlines()[0][:200] if out else f"exit {code}")
    return out.split()[-1] if out else "unknown"


def fingerprint(skill_dir):
    """Content hash of the whole skill folder: any edit or added script triggers a new scan."""
    h = hashlib.sha256()
    root = Path(skill_dir)
    for f in sorted(p for p in root.rglob("*") if p.is_file() and not _SKIP_PARTS & set(p.relative_to(root).parts)):
        h.update(f.relative_to(root).as_posix().encode("utf-8") + b"\0")
        try:
            h.update(f.read_bytes())
        except OSError:
            h.update(b"<unreadable>")
        h.update(b"\0")
    return h.hexdigest()


def scan_command(exe, skill_dir, report, llm):
    return [exe, "scan", str(skill_dir), "--format", "json", "--output", str(report)] + ([] if llm else ["--no-llm"])


def run_scan(exe, skill_dir, llm):
    """{recommendation, score, max_severity, llm_available}, or {error}. The JSON decides, not the exit code."""
    fd, report = tempfile.mkstemp(prefix="skillscan-", suffix=".json")
    os.close(fd)
    try:
        code, out = P.run(scan_command(exe, skill_dir, report, llm), timeout=TIMEOUT_S[bool(llm)])
        try:
            data = json.loads(Path(report).read_text(encoding="utf-8"))
            risk = data["risk_assessment"]
            return {"recommendation": risk["recommendation"], "score": risk.get("score"),
                    "max_severity": risk.get("max_issue_severity"),
                    "llm_available": bool((data.get("metadata") or {}).get("llm_available"))}
        except (OSError, ValueError, KeyError, TypeError):
            last = out.strip().splitlines()[-1:] if out else []
            return {"error": f"exit {code}: {last[0][:200] if last else 'no report'}"}
    finally:
        Path(report).unlink(missing_ok=True)


def load_cache():
    try:
        return json.loads(CACHE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_cache(cache):
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(cache, indent=1, sort_keys=True), encoding="utf-8")


def verdict(skill_dir, exe, version, llm, cache):
    """Cached per content hash, scanner version and scan mode, failed scans too (no retry until a change)."""
    key = f"{fingerprint(skill_dir)}:{version}:{'llm' if llm else 'static'}"
    hit = cache.get(Path(skill_dir).name)
    if hit and hit.get("key") == key:
        return dict(hit["result"], accepted=hit.get("accepted", False))
    result = run_scan(exe, skill_dir, llm)
    if llm and "error" not in result and not result.get("llm_available"):
        print(f"[WARN] skillscan: {Path(skill_dir).name}: LLM analysis unavailable (check SKILLSPECTOR_PROVIDER "
              "and its key) - static verdict used, not cached")
        return run_scan(exe, skill_dir, False)
    cache[Path(skill_dir).name] = {"key": key, "result": result}
    return result


def _quarantine_dest(name):
    dest = QUARANTINE / name
    return dest if not dest.exists() else QUARANTINE / f"{name}@{time.strftime('%Y%m%d-%H%M%S')}"


def restore_allowed(hub, allow, act):
    """An allowed skill comes back from quarantine (the newest copy) unless the hub has that name again."""
    if not QUARANTINE.is_dir():
        return
    for name in sorted(allow):
        copies = sorted(q for q in QUARANTINE.iterdir() if q.is_dir() and q.name.split("@")[0] == name)
        if copies and not (hub / name).exists():
            src = copies[-1]
            act(f"skillscan: restore allowed skill {src} -> {hub / name}", lambda s=src: shutil.move(str(s), str(hub / name)))


def _blocked(s, r, act, confirm, cache):
    """True when the user chose quarantine; confirm() is None when nobody can be asked."""
    head = f"skillscan: {s.name}: {BLOCK} (risk {r.get('score')}, max {r.get('max_severity')})"
    if r.get("accepted"):
        print(f"[WARN] {head} - linked, kept by you earlier")
        return False
    answer = confirm(f"  {head}. Review: {EXE} scan \"{s}\"\n  Move it to quarantine (linked nowhere)?")
    if answer is None:
        print(f"[WARN] {head} - linked; asked only in an interactive run that applies changes")
        return False
    if not answer:
        cache.get(s.name, {})["accepted"] = True
        print(f"[WARN] {head} - linked, kept by you (asked again only if it changes)")
        return False
    dest = _quarantine_dest(s.name)
    act(f"{head} - quarantine -> {dest}",
        lambda: (QUARANTINE.mkdir(parents=True, exist_ok=True), shutil.move(str(s), str(dest))))
    return True


def gate(skills, act, apply, llm=False, allow=(), confirm=lambda question: None):
    """Scan the given hub skills; returns the names that must not be linked."""
    allow = set(allow)
    if not skills:
        return set()
    exe = find_scanner()
    if not exe:
        print(f"[WARN] skillscan: SkillSpector not found - {len(skills)} skill(s) linked unscanned. Install: {INSTALL_HINT}")
        return set()
    version = scanner_version(exe)
    if isinstance(version, StartError):
        hint = f" {UV_TOOL_HINT}" if "trampoline" in version.lower() else ""
        print(f"[WARN] skillscan: SkillSpector does not start ({version}) - {len(skills)} skill(s) linked "
              f"unscanned.{hint}")
        return set()
    cache, blocked = load_cache(), set()
    print(f"skillscan: SkillSpector {version}, {'static + LLM' if llm else 'static'} analysis of {len(skills)} skill(s)")
    for s in skills:
        r = verdict(s, exe, version, llm, cache)
        rec, score = r.get("recommendation"), r.get("score")
        if "error" in r:
            print(f"[WARN] skillscan: {s.name}: scan failed ({r['error']}) - linked unscanned until it changes")
        elif rec == BLOCK and s.name in allow:
            print(f"[WARN] skillscan: {s.name}: {BLOCK} (risk {score}) - linked anyway, allowed in config")
        elif rec == BLOCK:
            if _blocked(s, r, act, confirm, cache):
                blocked.add(s.name)
        elif rec == CAUTION:
            print(f"[WARN] skillscan: {s.name}: {CAUTION} (risk {score}) - linked; review it: {EXE} scan \"{s}\"")
        elif rec != SAFE:
            print(f"[WARN] skillscan: {s.name}: unknown verdict {rec!r} - linked")
    if blocked:
        print(f"skillscan: {len(blocked)} skill(s) quarantined; restore one with --allow-skill=<name>")
    if apply:
        save_cache(cache)
    return blocked
