#!/usr/bin/env python3
"""jev-router installer - one command for Windows, macOS and Linux.

    python install.py                 interactive setup (recommended)
    python install.py --yes           non-interactive, accept the defaults
    python install.py --dry-run       show what would change, change nothing
    python install.py detect          report which AI tools are installed and logged in
    python install.py models --probe  test which Codex/Antigravity models your accounts can use
    python install.py remote [--name "My PC"] [--workdir <folder>] [--remove]   phone / other-device access
    python install.py uninstall       remove hooks, MCP entries and remote access (skills stay)

Options: --providers=claude,codex,antigravity  --jev-token=<token>  --remote[=<name>]  --no-migrate

Steps of the interactive setup:
  1. detect Claude Code, Codex and Antigravity (installed? logged in?) and help you log in
  2. JEV / TypeSafe token (optional - without it the built-in local model decides)
  3. hooks + MCP server for every logged-in tool
  4. shared skill folder ~/.skills: existing skills moved there and linked into every tool;
     worker agents generated for every available model x effort
  5. optional: remote access and speech-to-text
Requires Python 3.10+ and nothing else.
"""
import getpass
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO / "router"))
import install_hooks  # noqa: E402
import platforms as P  # noqa: E402
import remote  # noqa: E402
import skills_hub  # noqa: E402

STATE = P.HOME / ".jev-router"
CONFIG = STATE / "config.json"
MODELS_LOCAL = STATE / "models.local.json"
ALL = ("claude", "codex", "antigravity")

VALUE_FLAGS = ("--name", "--providers", "--jev-token", "--remote", "--workdir")


def parse_args(argv):
    """Accepts both `--flag=value` and `--flag value` (for the flags that take a value)."""
    flags, positional, i = {}, [], 0
    while i < len(argv):
        a = argv[i]
        if a.startswith("--"):
            key, eq, val = a.partition("=")
            if eq:
                flags[key] = val
            elif key in VALUE_FLAGS and i + 1 < len(argv) and not argv[i + 1].startswith("--") \
                    and not (key == "--remote" and argv[i + 1] in ("detect", "models", "remote", "uninstall")):
                flags[key] = argv[i + 1]
                i += 1
            else:
                flags[key] = True
        else:
            positional.append(a)
        i += 1
    return flags, positional


FLAGS, POSITIONAL = parse_args(sys.argv[1:])
COMMANDS = ("setup", "detect", "models", "remote", "uninstall")
COMMAND = POSITIONAL[0] if POSITIONAL else "setup"
if COMMAND not in COMMANDS or len(POSITIONAL) > 1:
    sys.exit(f"Unknown command: {' '.join(POSITIONAL)}. Use one of: {', '.join(COMMANDS)} (quote names with spaces).")
YES = "--yes" in FLAGS
DRY = "--dry-run" in FLAGS


def say(msg=""):
    print(msg, flush=True)


def ask(question, default="y"):
    """--yes accepts the default. Without a terminal (and without --yes) nothing is changed:
    every question is answered "no", so an unattended run can never move files unasked."""
    if YES:
        return default.lower().startswith("y")
    if not sys.stdin.isatty():
        return False
    ans = input(f"{question} [{'Y/n' if default.lower().startswith('y') else 'y/N'}] ").strip().lower()
    return (ans or default).startswith("y")


def load_config():
    try:
        return json.loads(CONFIG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_config(cfg):
    if DRY:
        return
    STATE.mkdir(parents=True, exist_ok=True)
    # created owner-only from the start (it holds the JEV token); os.open's mode is ignored on Windows
    fd = os.open(CONFIG, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(json.dumps(cfg, indent=2))
    if not P.IS_WINDOWS:
        os.chmod(CONFIG, 0o600)  # an existing file keeps its old mode otherwise


# --- 1. detection ------------------------------------------------------------------------------------
def print_report(found):
    say(f"\n{'Tool':<26}{'Installed':<11}{'Logged in':<11}Version")
    for info in found.values():
        li = {True: "yes", False: "NO", None: "unknown"}[info["logged_in"]] if info["installed"] else "-"
        say(f"{info['label']:<26}{'yes' if info['installed'] else 'no':<11}{li:<11}{info['version'] or ''}")


def detect_and_login():
    say("== 1/5  Detecting AI tools (this can take a minute: Antigravity is asked for its model list)")
    found = P.detect()
    print_report(found)
    for p, info in found.items():
        spec = P.PROVIDERS[p]
        if not info["installed"]:
            say(f"\n  {spec['label']} is not installed - optional. Install: {spec['install']}")
            continue
        while info["logged_in"] is False and not YES and sys.stdin.isatty():
            say(f"\n  {spec['label']} is installed but NOT logged in. In another terminal run:\n      {spec['login']}")
            if input("  Press Enter when done (s = skip this tool): ").strip().lower() == "s":
                break
            info.update(P.detect_one(p))
    chosen = FLAGS.get("--providers")
    if isinstance(chosen, str):
        providers = [p for p in chosen.split(",") if p in ALL]
    else:
        providers = [p for p, i in found.items() if i["installed"] and i["logged_in"] is not False]
    say(f"\n  Tools to connect: {', '.join(providers) or 'none'}")
    return providers


# --- 2. JEV token -----------------------------------------------------------------------------------------
def configure_jev(cfg):
    say("\n== 2/5  JEV / TypeSafe (the routing decision engine)")
    token = FLAGS.get("--jev-token")
    if isinstance(token, str) and token:
        cfg["typesafe_api_key"] = token
    elif os.environ.get("TYPESAFE_API_KEY"):
        say("  Using TYPESAFE_API_KEY from the environment.")
    elif cfg.get("typesafe_api_key"):
        say("  A token is already configured.")
    elif not YES and sys.stdin.isatty():
        token = getpass.getpass("  JEV token (input hidden; Enter = use the built-in local model): ").strip()
        if token:
            cfg["typesafe_api_key"] = token
    say("  Backend: " + ("JEV" if cfg.get("typesafe_api_key") or os.environ.get("TYPESAFE_API_KEY")
                         else "built-in local model (add a token later with --jev-token=...)"))


# --- 3 + 4. hooks, skills, agents ------------------------------------------------------------------------------
def connect(providers):
    say("\n== 3/5  Hooks + MCP server")
    install_hooks.install(providers, apply=not DRY)
    say("\n== 4/5  Shared skill folder (~/.skills) and worker agents")
    skills_hub.PROVIDERS = tuple(providers)
    skills_hub.APPLY = not DRY
    if "--no-migrate" not in FLAGS:
        skills_hub.APPLY = False
        say("  Skills that would move into ~/.skills (each replaced by a link, so every tool keeps it):")
        skills_hub.cmd_migrate()
        skills_hub.APPLY = not DRY and ask("  Move them now?", "y")
        if skills_hub.APPLY:
            skills_hub.cmd_migrate()
        skills_hub.APPLY = not DRY
    skills_hub.cmd_link()
    skills_hub.cmd_agents()
    if not DRY:
        skills_hub.cmd_catalog()
    skills_hub.cmd_doctor()


# --- 5. optional extras -----------------------------------------------------------------------------------------
def extras(providers, cfg):
    say("\n== 5/5  Optional extras")
    name = FLAGS.get("--remote")
    if name or (not YES and ask("  Set up remote access (control this computer from a phone / another device)?", "n")):
        if not isinstance(name, str):
            default = cfg.get("remote_name") or P.hostname()
            name = (input(f"  Name shown on your other devices [{default}]: ").strip() if sys.stdin.isatty() and not YES else "") or default
        for tool, ok, msg in remote.setup(name, providers, apply=not DRY, workdir=remote_workdir(cfg)):
            say(f"  [{'OK' if ok else '!!'}] {tool}: {msg}")
    say("  Speech-to-text (dictation into any app): see docs/speech-to-text.md")
    if P.IS_WINDOWS and not YES and not DRY and not P.find_exe("handy") and ask("  Install Handy (offline dictation) with winget now?", "n"):
        os.system("winget install --id cjpais.Handy -e --accept-source-agreements --accept-package-agreements")


def remote_workdir(cfg):
    """Folder remote Claude sessions start in: --workdir, else the saved one, else this repository.
    Claude Code only serves trusted folders, and never the home directory."""
    wd = FLAGS.get("--workdir")
    return str(Path(wd).resolve()) if isinstance(wd, str) else (cfg.get("remote_workdir") or str(REPO))


def next_steps(providers):
    say("\nDone. Next steps:")
    if "codex" in providers:
        say("  * Codex runs a new or changed hook only after you trust it once: run `codex`, type /hooks, trust jev-router.")
    if "claude" in providers:
        say("  * Claude desktop Chat/Cowork: restart the app, then add to Settings > Profile > Personal preferences:")
        say('      "Before answering any new request, call the jev-router route_prompt tool with my message and follow its instructions."')
    say("  * Optional: `python install.py models --probe` checks which Codex/Antigravity models your account can use.")
    say("  * Health check any time: python scripts/check_tools.py")


# --- sub-commands ----------------------------------------------------------------------------------------------------
def probe_models():
    """Tests every catalog model of Codex (one tiny prompt each) and reads Antigravity's model list;
    the result goes to ~/.jev-router/models.local.json and overrides models.json per account."""
    catalog = json.loads((REPO / "router" / "models.json").read_text(encoding="utf-8"))
    local = json.loads(MODELS_LOCAL.read_text(encoding="utf-8")) if MODELS_LOCAL.exists() else {}
    if codex := P.find_exe("codex"):
        say("Codex: testing each model with a one-word prompt (about 10-60 s per model)...")
        for m in catalog["codex"]["models"]:
            code, out = P.run([codex, "exec", "--skip-git-repo-check", "-m", m["id"], "-c", "model_reasoning_effort=low",
                               "#norouter Reply with exactly: OK"], timeout=180)
            low = out.lower()
            ok = code == 0 and "ok" in low and "not supported" not in low and "does not exist" not in low
            local.setdefault("codex", {})[m["id"]] = {"selectable": ok}
            say(f"  {m['id']:<28} {'available' if ok else 'not available'}")
    if agy := P.find_exe("agy"):
        code, out = P.run([agy, "models"], timeout=180)
        listed = {line.split()[0] for line in out.splitlines() if line.strip() and not line.startswith("Fetching")}
        for m in catalog["antigravity"]["models"]:
            slugs = {m["slug"].format(id=m["id"], effort=e) for e in (m["levels"] or [""])}
            ok = bool(slugs & listed)
            local.setdefault("antigravity", {})[m["id"]] = {"selectable": ok}
            say(f"  {m['id']:<28} {'available' if ok else 'not available'}")
    if not DRY:
        STATE.mkdir(parents=True, exist_ok=True)
        MODELS_LOCAL.write_text(json.dumps(local, indent=2), encoding="utf-8")
        skills_hub.APPLY = True
        skills_hub.cmd_agents()  # agents/roles for exactly the available models
    say(f"Saved to {MODELS_LOCAL}")


def main():
    if COMMAND == "detect":
        print_report(P.detect())
        return 0
    if COMMAND == "models":
        probe_models()
        return 0
    if COMMAND == "remote":
        if "--remove" in FLAGS:
            if DRY:
                say("Dry run: would remove the remote-access services (scheduled tasks / launchd / systemd) "
                    "and stop the Antigravity and Codex remote daemons.")
                return 0
            for tool, ok, msg in remote.remove():
                say(f"[{'OK' if ok else '!!'}] {tool}: {msg}")
            return 0
        found = P.detect(deep=False)
        providers = [p for p, i in found.items() if i["installed"]]
        cfg = load_config()
        name = FLAGS.get("--name") if isinstance(FLAGS.get("--name"), str) else (cfg.get("remote_name") or P.hostname())
        for tool, ok, msg in remote.setup(name, providers, apply=not DRY, workdir=remote_workdir(cfg)):
            say(f"[{'OK' if ok else '!!'}] {tool}: {msg}")
        return 0
    if COMMAND == "uninstall":
        install_hooks.install(ALL, apply=not DRY, uninstall=True)
        if not DRY:
            for tool, ok, msg in remote.remove():
                say(f"[{'OK' if ok else '!!'}] {tool}: {msg}")
        say("Hooks, MCP entries and remote access removed. Your skills stay in ~/.skills (and linked).")
        return 0
    say("jev-router setup" + (" (dry run - nothing will be changed)" if DRY else ""))
    providers = detect_and_login()
    if not providers:
        say("\nNo logged-in tool found. Install and log in to at least one of them, then run this again.")
        return 1
    cfg = load_config()
    configure_jev(cfg)
    save_config(cfg)
    connect(providers)
    extras(providers, load_config() if not DRY else cfg)
    next_steps(providers)
    return 0


if __name__ == "__main__":
    sys.exit(main())
