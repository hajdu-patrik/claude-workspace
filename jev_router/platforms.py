#!/usr/bin/env python3
"""OS abstraction for the installer: config locations, directory links, executables, detection.

Windows uses directory junctions (no admin rights or Developer Mode needed); macOS and Linux use
symlinks. Every path is derived from the current user's home directory - nothing machine-specific
is stored in the repository.
"""
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

HOME = Path.home()
IS_WINDOWS = sys.platform.startswith("win")
IS_MAC = sys.platform == "darwin"


# --- locations ---------------------------------------------------------------------------------
def claude_desktop_config():
    """Claude desktop app's MCP config (Chat / Cowork)."""
    if IS_WINDOWS:
        return Path(os.environ.get("APPDATA", HOME / "AppData" / "Roaming")) / "Claude" / "claude_desktop_config.json"
    if IS_MAC:
        return HOME / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    return Path(os.environ.get("XDG_CONFIG_HOME", HOME / ".config")) / "Claude" / "claude_desktop_config.json"


PATHS = {
    "claude_settings": HOME / ".claude" / "settings.json",
    "claude_skills": HOME / ".claude" / "skills",
    "claude_agents": HOME / ".claude" / "agents",
    "codex_home": HOME / ".codex",
    "codex_hooks": HOME / ".codex" / "hooks.json",
    "codex_config": HOME / ".codex" / "config.toml",
    "codex_agents": HOME / ".codex" / "agents",
    "codex_skills": HOME / ".agents" / "skills",          # personal skills location Codex scans
    "agy_config": HOME / ".gemini" / "config",
    "agy_hooks": HOME / ".gemini" / "config" / "hooks.json",
    "agy_skills_json": HOME / ".gemini" / "config" / "skills.json",
    "agy_mcp": HOME / ".gemini" / "config" / "mcp_config.json",
}


# --- links -----------------------------------------------------------------------------------------
def is_link(p):
    """Symlink or Windows junction. Path.is_junction() only exists on Python 3.12+, so older
    versions check the reparse-point attribute directly."""
    p = Path(p)
    try:
        if p.is_symlink():
            return True
        if hasattr(p, "is_junction"):
            return p.is_junction()
        return IS_WINDOWS and bool(os.lstat(p).st_file_attributes & 0x400)  # FILE_ATTRIBUTE_REPARSE_POINT
    except (OSError, AttributeError):
        return False


def link_dir(link, target):
    """Create a directory link: junction on Windows, symlink elsewhere."""
    link, target = Path(link), Path(target)
    link.parent.mkdir(parents=True, exist_ok=True)
    if IS_WINDOWS:
        r = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True, text=True)
        if r.returncode:
            raise OSError((r.stderr or r.stdout).strip())
    else:
        os.symlink(target, link, target_is_directory=True)


def unlink_dir(link):
    """Remove a directory link only - never the target's contents."""
    link = Path(link)
    if IS_WINDOWS:
        os.rmdir(link)
    else:
        link.unlink()


# --- executables -------------------------------------------------------------------------------
def find_exe(name):
    """PATH first, then the installers' default locations (a fresh install is often not on PATH yet)."""
    found = shutil.which(name)
    if found:
        return found
    candidates = []
    if name == "agy":
        candidates = [HOME / ".local" / "bin" / "agy", HOME / ".agy" / "bin" / "agy"]
        if IS_WINDOWS and os.environ.get("LOCALAPPDATA"):
            candidates.insert(0, Path(os.environ["LOCALAPPDATA"]) / "agy" / "bin" / "agy.exe")
    elif name == "claude":
        candidates = [HOME / ".local" / "bin" / ("claude.exe" if IS_WINDOWS else "claude"), HOME / ".claude" / "local" / "claude"]
    for c in candidates:
        if str(c) and Path(c).is_file():
            return str(c)
    return None


def short_path(p):
    """Windows 8.3 short form of an existing path (no spaces), else the path unchanged. Antigravity
    runs hook commands through `cmd /c`, which mangles quoted paths - a short path needs no quotes."""
    p = str(p)
    if not IS_WINDOWS or " " not in p:
        return p
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(1024)
        if ctypes.windll.kernel32.GetShortPathNameW(p, buf, 1024):
            return buf.value
    except (OSError, AttributeError):
        pass
    return p


def python_exe():
    """A stable interpreter for hooks: the base interpreter when running inside a virtualenv (the
    venv may be deleted later), else sys.executable."""
    exe = sys.executable or ("python" if IS_WINDOWS else "python3")
    if sys.prefix != getattr(sys, "base_prefix", sys.prefix):
        exe = getattr(sys, "_base_executable", exe) or exe
    return exe


def python_exe_windowless():
    """Interpreter for stdio servers (MCP): pythonw.exe on Windows. A host that runs without a
    console (e.g. a detached background daemon) would otherwise open a terminal window for
    python.exe. pythonw still talks over the stdin/stdout pipes the host passes it."""
    exe = python_exe()
    if IS_WINDOWS:
        w = Path(exe).with_name("pythonw.exe")
        if Path(exe).name.lower() == "python.exe" and w.is_file():
            return str(w)
    return exe


def shell_arg(p):
    """One command-line argument, safe for sh (Claude/Codex on POSIX), bash/cmd (Windows) and
    Antigravity's `cmd /c`: short path on Windows, shell-quoted on POSIX."""
    import shlex
    if IS_WINDOWS:
        return short_path(p).replace("\\", "/")
    return shlex.quote(str(p))


def python_cmd():
    """Interpreter as a ready-to-use command-line word."""
    return shell_arg(python_exe())


def run(argv, timeout=30):
    """(returncode, combined output) - never raises."""
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL,
                           encoding="utf-8", errors="replace")
        return r.returncode, ((r.stdout or "") + (r.stderr or "")).strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, f"{type(exc).__name__}: {exc}"


# --- detection ---------------------------------------------------------------------------------
PROVIDERS = {
    "claude": {"label": "Claude Code", "exe": "claude",
               "install": "https://code.claude.com/docs/en/quickstart",
               "login": "claude  (then run /login inside it)"},
    "codex": {"label": "OpenAI Codex CLI", "exe": "codex",
              "install": "npm install -g @openai/codex   (or https://developers.openai.com/codex)",
              "login": "codex login"},
    "antigravity": {"label": "Google Antigravity CLI", "exe": "agy",
                    "install": "https://antigravity.google/docs/cli/install/",
                    "login": "agy  (sign in with Google in the browser window that opens)"},
}


def detect_one(provider, deep=True):
    """{"installed", "path", "version", "logged_in" (True/False/None=unknown), "detail"}."""
    spec = PROVIDERS[provider]
    exe = find_exe(spec["exe"])
    info = {"provider": provider, "label": spec["label"], "installed": bool(exe), "path": exe,
            "version": None, "logged_in": None, "detail": ""}
    if not exe:
        return info
    _, out = run([exe, "--version"], timeout=30)
    info["version"] = (out.splitlines() or [""])[0][:80]
    if provider == "claude":
        code, out = run([exe, "auth", "status"], timeout=30)
        try:
            info["logged_in"] = bool(json.loads(out[out.index("{"):]).get("loggedIn"))
        except (ValueError, AttributeError):
            info["detail"] = out[:120]
    elif provider == "codex":
        code, out = run([exe, "login", "status"], timeout=30)
        info["logged_in"] = code == 0 and "logged in" in out.lower() and "not logged in" not in out.lower()
    elif provider == "antigravity" and deep:
        code, out = run([exe, "models"], timeout=120)
        low = out.lower()
        if "not logged in" in low or "sign in" in low:
            info["logged_in"] = False
        elif re.search(r"^\S+\s+\S", out, re.M) and code == 0:
            info["logged_in"] = True
        else:
            info["detail"] = out[:120]
    return info


def detect(deep=True):
    return {p: detect_one(p, deep) for p in PROVIDERS}


def hostname():
    import socket
    return socket.gethostname()
