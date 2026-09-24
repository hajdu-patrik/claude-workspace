#!/usr/bin/env python3
"""jev-router installer - one command for Windows, macOS and Linux.

    python install.py                 interactive setup (recommended)
    python install.py --yes           non-interactive, accept the defaults
    python install.py --dry-run       show what would change, change nothing
    python install.py detect          report which AI tools are installed and logged in
    python install.py models --probe  test which Codex/Antigravity models your accounts can use
    python install.py remote [--name "My PC"] [--workdir <folder>] [--remove]   phone / other-device access
    python install.py skills [--apply]  re-link skills, regenerate workers, rebuild the catalog
    python install.py doctor          read-only health report
    python install.py uninstall       remove hooks, MCP entries and remote access (skills stay)
    python install.py route [--provider claude] [--json] <prompt text...>
                                      the routing decision for one prompt or sub-task (no text: read
                                      stdin); side-effect free; exit 0 ok, 2 usage error, 1 error

`python -m jev_router <command>` is equivalent. Other programs call the route command through
the installer's shim: python ~/.jev-router/bin/route.py --json "<text>"

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

from . import core, doctor, hooks, hub, integrations, platforms as P, remote

PKG = Path(__file__).resolve().parent
REPO = PKG.parent

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


COMMANDS = ("setup", "detect", "models", "remote", "skills", "doctor", "uninstall", "route")
FLAGS, POSITIONAL, COMMAND, YES, DRY = {}, [], "setup", False, False


def configure(argv):
    """Parse the command line into the module-level settings used by every step."""
    global FLAGS, POSITIONAL, COMMAND, YES, DRY
    if any(a in ("-h", "--help", "help") for a in argv):
        print(__doc__)
        sys.exit(0)
    FLAGS, POSITIONAL = parse_args(argv)
    COMMAND = POSITIONAL[0] if POSITIONAL else "setup"
    if COMMAND == "route":  # main() dispatches `route` only as the first argument
        print(f"route: must be the first argument\n{ROUTE_USAGE}", file=sys.stderr)
        sys.exit(2)
    if COMMAND not in COMMANDS or len(POSITIONAL) > 1:
        sys.exit(f"Unknown command: {' '.join(POSITIONAL)}. Use one of: {', '.join(COMMANDS)} (quote names with spaces).")
    YES = "--yes" in FLAGS
    DRY = "--dry-run" in FLAGS


def say(msg=""):
    print(msg, flush=True)


def interactive():
    """A person can answer questions: stdin is a real console (not a pipe, a file or NUL)."""
    return P.is_terminal(sys.stdin)


def ask(question, default="y"):
    """--yes accepts the default. Without a terminal (and without --yes) nothing is changed:
    every question is answered "no", so an unattended run can never move files unasked."""
    if YES:
        return default.lower().startswith("y")
    if not interactive():
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
def report(lines, indent=""):
    """(tool, ok, message) lines from remote.setup() / remote.remove()."""
    for tool, ok, msg in lines:
        say(f"{indent}[{'OK' if ok else '!!'}] {tool}: {msg}")


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
        while info["logged_in"] is False and not YES and interactive():
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
    elif not YES and interactive():
        token = getpass.getpass("  JEV token (input hidden; Enter = use the built-in local model): ").strip()
        if token:
            cfg["typesafe_api_key"] = token
    say("  Backend: " + ("JEV" if cfg.get("typesafe_api_key") or os.environ.get("TYPESAFE_API_KEY")
                         else "built-in local model (add a token later with --jev-token=...)"))


# --- 3 + 4. hooks, skills, agents ------------------------------------------------------------------------------
def connect(providers):
    say("\n== 3/5  Hooks + MCP server")
    integrations.install(providers, apply=not DRY)
    say("\n== 4/5  Shared skill folder (~/.skills) and worker agents")
    hub.PROVIDERS = tuple(providers)
    hub.APPLY = not DRY
    if "--no-migrate" not in FLAGS:
        hub.APPLY = False
        say("  Skills that would move into ~/.skills (each replaced by a link, so every tool keeps it):")
        hub.cmd_migrate()
        hub.APPLY = not DRY and ask("  Move them now?", "y")
        if hub.APPLY:
            hub.cmd_migrate()
        hub.APPLY = not DRY
    hub.cmd_link()
    hub.cmd_agents()
    if not DRY:
        hub.cmd_catalog()
    hub.cmd_doctor()


# --- 5. optional extras -----------------------------------------------------------------------------------------
def ask_remote_name(cfg):
    """The machine name for remote access: typed in, else the saved one, else the hostname."""
    default = cfg.get("remote_name") or P.hostname()
    if YES or not interactive():
        return default
    return input(f"  Name shown on your other devices [{default}]: ").strip() or default


def extras(providers, cfg):
    say("\n== 5/5  Optional extras")
    name = FLAGS.get("--remote")
    if name or (not YES and ask("  Set up remote access (control this computer from a phone / another device)?", "n")):
        if not isinstance(name, str):
            name = ask_remote_name(cfg)
        report(remote.setup(name, providers, apply=not DRY, workdir=remote_workdir(cfg)), indent="  ")
    say("  Speech-to-text (dictation into any app): see docs/speech-to-text.md")
    offer_handy = P.IS_WINDOWS and not YES and not DRY and not P.find_exe("handy")
    if offer_handy and ask("  Install Handy (offline dictation) with winget now?", "n"):
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
    say("  * Health check any time: python install.py doctor")


# --- sub-commands ----------------------------------------------------------------------------------------------------
def codex_accepts(codex, model_id):
    """True if the account can use this Codex model (one tiny low-effort prompt)."""
    code, out = P.run([codex, "exec", "--skip-git-repo-check", "-m", model_id, "-c", "model_reasoning_effort=low",
                       "#norouter Reply with exactly: OK"], timeout=180)
    low = out.lower()
    return code == 0 and "ok" in low and "not supported" not in low and "does not exist" not in low


def record_model(local, provider, model_id, ok):
    local.setdefault(provider, {})[model_id] = {"selectable": ok}
    say(f"  {model_id:<28} {'available' if ok else 'not available'}")


def probe_models():
    """Tests every catalog model of Codex (one tiny prompt each) and reads Antigravity's model list;
    the result goes to ~/.jev-router/models.local.json and overrides models.json per account."""
    catalog = json.loads((PKG / "config" / "models.json").read_text(encoding="utf-8"))
    local = json.loads(MODELS_LOCAL.read_text(encoding="utf-8")) if MODELS_LOCAL.exists() else {}
    if codex := P.find_exe("codex"):
        say("Codex: testing each model with a one-word prompt (about 10-60 s per model)...")
        for m in catalog["codex"]["models"]:
            record_model(local, "codex", m["id"], codex_accepts(codex, m["id"]))
    if agy := P.find_exe("agy"):
        _, out = P.run([agy, "models"], timeout=180)
        listed = {line.split()[0] for line in out.splitlines() if line.strip() and not line.startswith("Fetching")}
        for m in catalog["antigravity"]["models"]:
            slugs = {m["slug"].format(id=m["id"], effort=e) for e in (m["levels"] or [""])}
            record_model(local, "antigravity", m["id"], bool(slugs & listed))
    if not DRY:
        STATE.mkdir(parents=True, exist_ok=True)
        MODELS_LOCAL.write_text(json.dumps(local, indent=2), encoding="utf-8")
        hub.APPLY = True
        hub.cmd_agents()  # agents/roles for exactly the available models
    say(f"Saved to {MODELS_LOCAL}")


def run_skills():
    """Re-link skills, regenerate worker agents and rebuild the catalog for the installed tools."""
    found = P.detect(deep=False)
    hub.PROVIDERS = tuple(p for p, i in found.items() if i["installed"])
    hub.APPLY = "--apply" in FLAGS
    hub.cmd_link()
    hub.cmd_agents()
    if hub.APPLY:
        hub.cmd_catalog()
    hub.cmd_doctor()
    if not hub.APPLY:
        say("\nDry run only. Re-run with --apply to write the changes.")


# --- route: the decision as a side-effect-free query (for sub-tasks, other programs) ------------------------
ROUTE_USAGE = "usage: python install.py route [--provider claude] [--json] [--] <prompt text...>   (no text: read stdin)"
ROUTE_PROVIDERS = ALL + ("claude-chat",)
ROUTE_KEYS = ("provider", "model", "effort", "agent", "tier", "task", "difficulty", "extra_agents", "destructive",
              "skill", "verify", "lang", "backend", "text", "note")


def provider_option(argv, i):
    """(provider, index of its last argument) for `--provider X` or `--provider=X` at argv[i]."""
    _, eq, provider = argv[i].partition("=")
    if not eq:
        i += 1
        provider = argv[i] if i < len(argv) else ""
    if provider not in ROUTE_PROVIDERS:
        raise ValueError(f"--provider must be one of: {', '.join(ROUTE_PROVIDERS)}")
    return provider, i


def parse_route_args(argv):
    """(provider, as_json, prompt text or None when none was given), or None for --help.
    Raises ValueError on a usage error; its message never echoes an option's value."""
    provider, as_json, words, i = "claude", False, [], 0
    while i < len(argv):
        a = argv[i]
        if a == "--":
            words += argv[i + 1:]
            break
        if a in ("-h", "--help"):
            return None
        if a == "--json":
            as_json = True
        elif a == "--provider" or a.startswith("--provider="):
            provider, i = provider_option(argv, i)
        elif a.startswith("--"):
            raise ValueError(f"unknown option {a.partition('=')[0]}")
        else:
            words.append(a)
        i += 1
    return provider, as_json, (" ".join(words) if words else None)


def read_stdin():
    """The piped prompt ('' for an interactive terminal: never wait for typing). utf-8-sig: PowerShell
    pipes can prepend a BOM."""
    stream = sys.stdin
    if stream is None or P.is_terminal(stream):
        return ""
    data = stream.buffer.read() if hasattr(stream, "buffer") else stream.read()
    return data.decode("utf-8-sig", errors="replace") if isinstance(data, bytes) else data


def route_decision(prompt, provider):
    """The decision core.route() makes for the hooks, as a flat JSON-ready dict with ROUTE_KEYS.
    No side effects: no queue state, no routing log. #norouter / #privat: not routed at all, and the
    prompt never leaves this machine (only the local safety regex and language detection run)."""
    out = dict.fromkeys(ROUTE_KEYS)
    out.update(provider=provider, extra_agents=0, text="")
    if any(tag in prompt.lower() for tag in hooks.SKIP_TAGS):
        out.update(destructive=core.is_destructive(prompt), lang=core.lang.detect(prompt),
                   note="#norouter / #privat: not routed, nothing sent to TypeSafe.")
        return out
    d, text, hit, error = core.route(prompt, provider)
    out.update(model=d.get("target_model"), effort=d.get("effort"), agent=d.get("target_agent"), tier=d["primary"],
               task=d["task"], difficulty=d["level"], extra_agents=d.get("extra_agents", 0), destructive=bool(hit),
               skill=d.get("skill"), verify=d.get("verify") or None, lang=d["lang"], backend=d.get("backend"), text=text)
    if error:  # only the exception type: its message could carry request details
        out["note"] = f"JEV unavailable ({error.split(':', 1)[0]}); the built-in classifier decided."
    return out


def run_route(argv):
    """`route` command. Exit codes: 0 success, 2 usage error, 1 unexpected error (type only on stderr)."""
    try:
        parsed = parse_route_args(argv)
    except ValueError as exc:
        print(f"route: {exc}\n{ROUTE_USAGE}", file=sys.stderr)
        return 2
    if parsed is None:
        print(ROUTE_USAGE)
        return 0
    provider, as_json, prompt = parsed
    try:
        prompt = (read_stdin() if prompt is None else prompt).strip()
        if not prompt:
            print(f"route: empty prompt\n{ROUTE_USAGE}", file=sys.stderr)
            return 2
        result = route_decision(prompt, provider)
        if as_json:
            print(json.dumps(result))  # ASCII-escaped: safe for any console code page
        elif result["text"]:
            print(result["text"])
    except Exception as exc:  # noqa: BLE001 - stable exit code; never print details (could hold secrets)
        print(type(exc).__name__, file=sys.stderr)
        return 1
    return 0


def run_setup():
    """The interactive setup (steps 1-5). Returns the exit code."""
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


def run_remote():
    if "--remove" in FLAGS:
        if DRY:
            say("Dry run: would remove the remote-access services (scheduled tasks / launchd / systemd) "
                "and stop the Antigravity and Codex remote daemons.")
        else:
            report(remote.remove())
        return
    found = P.detect(deep=False)
    providers = [p for p, i in found.items() if i["installed"]]
    cfg = load_config()
    name = FLAGS.get("--name") if isinstance(FLAGS.get("--name"), str) else (cfg.get("remote_name") or P.hostname())
    report(remote.setup(name, providers, apply=not DRY, workdir=remote_workdir(cfg)))


def run_uninstall():
    integrations.install(ALL, apply=not DRY, uninstall=True)
    if not DRY:
        report(remote.remove())
    say("Hooks, MCP entries and remote access removed. Your skills stay in ~/.skills (and linked).")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["route"]:  # before configure(): the prompt text is free-form
        return run_route(argv[1:])
    configure(argv)
    commands = {"setup": run_setup, "doctor": doctor.main, "skills": run_skills, "models": probe_models,
                "detect": lambda: print_report(P.detect()), "remote": run_remote, "uninstall": run_uninstall}
    return commands[COMMAND]() or 0  # the sub-commands return None (success) or an exit code
