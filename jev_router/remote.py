#!/usr/bin/env python3
"""Optional remote-access module: reach this computer from a phone or another device through each
tool's own remote feature, started automatically at logon, under ONE machine name.

    python install.py remote --name "My Workstation"     # set up (name defaults to the hostname)
    python install.py remote --remove                    # undo

What it sets up (only for tools that are installed):
  Claude       `claude remote-control --name <name>` kept running at logon
               (Windows: scheduled task, macOS: launchd agent, Linux: systemd --user service)
  Antigravity  `agy remote-control start --name <name> --session` (the CLI registers its own autostart;
               on Windows it is wrapped so it starts without a window)
  Codex        macOS/Linux: `codex remote-control start` (pair with `codex remote-control pair`);
               Windows: `codex app-server --remote-control --listen off` kept running at logon by a
               scheduled task - the `start` daemon cannot detach there, as every process (Explorer
               included) runs inside a Job Object without breakaway permission.
On Windows nothing opens a window: console programs run under `conhost.exe --headless`. The desktop
apps are not started; opened by hand they work as usual. The machine name is stored in
~/.jev-router/config.json, never in the repository.
The computer must be on, awake and logged in for any of this to be reachable.
"""
import base64
import json
import os
import plistlib
import time
from pathlib import Path

from . import platforms as P

BIN = P.HOME / ".jev-router" / "bin"
RUN_KEY = r"HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
AGY_RUN_VALUE, TASK_ONCE = "AntigravityCliDaemon", "JevRouter-Setup"
CONFIG = P.HOME / ".jev-router" / "config.json"
TASK_CLAUDE, TASK_CODEX = "JevRouter-ClaudeRemote", "JevRouter-CodexRemote"
LEGACY_TASKS = ("ClaudeRemoteControl", "ChatGPTAutostart", "CodexRemoteControl", "JevRouter-ChatGPT")
LAUNCHD_LABEL = "com.jev-router.claude-remote"
LAUNCHD = P.HOME / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"
LOG = P.HOME / ".jev-router" / "logs" / "claude-remote.log"
SYSTEMD = P.HOME / ".config" / "systemd" / "user" / "jev-router-claude-remote.service"


def save_name(name, workdir=None):
    cfg = json.loads(CONFIG.read_text(encoding="utf-8")) if CONFIG.exists() else {}
    cfg["remote_name"] = name
    if workdir:
        cfg["remote_workdir"] = str(workdir)
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def _ps(script):
    return P.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script], timeout=60)


def _conhost():
    """`conhost.exe --headless <program>` runs a console program in a console that is never shown -
    not even handed to Windows Terminal, the default terminal on Windows 11 (a plain `cmd.exe` task
    or Run entry opens a terminal window at every logon)."""
    return str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "conhost.exe")


def _hidden_ps(script):
    """(execute, arguments) for a scheduled task that runs a PowerShell script without a window."""
    encoded = base64.b64encode(script.encode("utf-16-le")).decode()
    return _conhost(), f"--headless powershell.exe -NoProfile -ExecutionPolicy Bypass -EncodedCommand {encoded}"


def _run_once(script):
    """Run a PowerShell script through the Task Scheduler; returns the last line it outputs ("" if it
    did not finish in 30 s). Needed for HKCU writes: a process in an MSIX container - e.g. a `python`
    started through the Python Install Manager's alias, or anything run from a packaged app such as
    the Claude desktop app - and its child processes only see a private copy of HKCU. Scheduled tasks
    run outside it. The result goes through a file: conhost does not pass the exit code on."""
    result = P.HOME / ".jev-router" / "state" / "setup-task.txt"
    result.parent.mkdir(parents=True, exist_ok=True)
    result.unlink(missing_ok=True)
    literal = str(result).replace("'", "''")
    exe, args = _hidden_ps(f"& {{ {script} }} | Select-Object -Last 1 | Set-Content -LiteralPath '{literal}'")
    _ps(f'$a = New-ScheduledTaskAction -Execute "{exe}" -Argument "{args}"; '
        f'Register-ScheduledTask -TaskName "{TASK_ONCE}" -Action $a -RunLevel Limited -Force | Out-Null; '
        f'Start-ScheduledTask -TaskName "{TASK_ONCE}"')
    for _ in range(60):
        time.sleep(0.5)
        if result.exists() and (out := result.read_text(encoding="utf-8", errors="replace").strip()):
            break
    else:
        out = ""
    _ps(f'Unregister-ScheduledTask -TaskName "{TASK_ONCE}" -Confirm:$false -ErrorAction SilentlyContinue')
    result.unlink(missing_ok=True)
    return out


def _hide_agy_autostart():
    """`agy remote-control start` registers `agy.exe remote-control serve` under HKCU\\...\\Run - a
    console program, so every logon opened a terminal window. Prefix it with `conhost.exe --headless`.
    True = done (or already done), False = the entry does not exist or could not be changed."""
    return _run_once(
        f"$k = '{RUN_KEY}'; $v = (Get-ItemProperty $k -ErrorAction SilentlyContinue).{AGY_RUN_VALUE}; "
        "if (-not $v) { 'missing'; return }; "
        f"if ($v -notlike '*--headless*') {{ Set-ItemProperty $k {AGY_RUN_VALUE} ('\"{_conhost()}\" --headless ' + $v) }}; "
        f"if ((Get-ItemProperty $k).{AGY_RUN_VALUE} -like '*--headless*') {{ 'ok' }} else {{ 'failed' }}") == "ok"


def _win_loop(task, script_name, workdir, command, kill):
    """Keep `command` (a cmd.exe line) running from logon: a restart-after-30-s loop script run by a
    scheduled task under `conhost.exe --headless`. `kill` (PowerShell condition on $_) matches a
    previous instance's processes - the scheduler ignores Start while the old loop still runs."""
    script = BIN / script_name
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(f'@echo off\ncd /d "{workdir}"\n:loop\n{command}\ntimeout /t 30 /nobreak >nul\ngoto loop\n',
                      encoding="utf-8", newline="\r\n")
    _ps(f'Stop-ScheduledTask -TaskName "{task}" -ErrorAction SilentlyContinue; '
        f'Get-CimInstance Win32_Process | ? {{ ($_.Name -eq "cmd.exe" -and $_.CommandLine -match "{script_name}") -or ({kill}) }} '
        '| % { Stop-Process -Id $_.ProcessId -Force }')
    return _win_task(task, _conhost(), f'--headless cmd.exe /c "{script}"', str(workdir))


def _win_task(name, execute, argument, workdir):
    argument = argument.replace("'", "''")  # inside a single-quoted PowerShell string
    return _ps(
        f'$a = New-ScheduledTaskAction -Execute "{execute}" -Argument \'{argument}\' -WorkingDirectory "{workdir}"; '
        '$t = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\\$env:USERNAME"; '
        '$s = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero); '
        f'Register-ScheduledTask -TaskName "{name}" -Action $a -Trigger $t -Settings $s -RunLevel Limited -Force | Out-Null; '
        f'Start-ScheduledTask -TaskName "{name}"')


def claude_trusts(folder):
    """True if Claude Code's workspace-trust dialog was accepted for `folder` (~/.claude.json).
    Remote Control refuses untrusted folders - and the home directory is never trusted."""
    try:
        projects = json.loads((P.HOME / ".claude.json").read_text(encoding="utf-8")).get("projects", {})
    except (OSError, ValueError):
        return False

    def norm(p):
        return str(p).replace("\\", "/").rstrip("/").lower()
    return any(norm(k) == norm(folder) and v.get("hasTrustDialogAccepted") for k, v in projects.items())


def _launchd_plist(name, claude, workdir):
    """launchd agent plist (bytes). plistlib escapes &, <, quotes in names and paths. PATH is copied
    from this process so an npm/Homebrew/nvm `claude` (a `#!/usr/bin/env node` script) finds node."""
    return plistlib.dumps({
        "Label": LAUNCHD_LABEL,
        "ProgramArguments": [str(claude), "remote-control", "--name", str(name)],
        "WorkingDirectory": str(workdir),
        "EnvironmentVariables": {"PATH": os.environ.get("PATH", "/usr/bin:/bin:/usr/sbin:/sbin")},
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 30,
        "StandardOutPath": str(LOG),
        "StandardErrorPath": str(LOG),
    })


def _one_line(value):
    return " ".join(str(value).splitlines())


def _sd_quote(value, dollar=True):
    """A double-quoted systemd argument: backslash and `"` backslash-escaped, `%` specifiers doubled and,
    where the setting expands variables (ExecStart=), `$` doubled."""
    value = _one_line(value).replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
    if dollar:
        value = value.replace("$", "$$")
    return f'"{value}"'


def _systemd_unit(name, claude, workdir):
    """systemd --user unit text with specifiers/quoting escaped. Description= and WorkingDirectory=
    only expand `%` specifiers; Environment= does no `$` expansion, so `$` is left alone there."""
    path = os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")
    return f"""[Unit]
Description=Claude Code Remote Control ({_one_line(name).replace("%", "%%")})

[Service]
ExecStart={_sd_quote(claude)} remote-control --name {_sd_quote(name)}
WorkingDirectory={_one_line(workdir).replace("%", "%%")}
Environment={_sd_quote("PATH=" + path, dollar=False)}
Restart=always
RestartSec=30

[Install]
WantedBy=default.target
"""


def _setup_claude(name, claude, workdir):
    if P.IS_WINDOWS:
        for legacy in LEGACY_TASKS:
            _ps(f'Unregister-ScheduledTask -TaskName "{legacy}" -Confirm:$false -ErrorAction SilentlyContinue')
        code, out = _win_loop(TASK_CLAUDE, "claude-remote.cmd", workdir, f'"{claude}" remote-control --name "{name}"',
                              '($_.Name -like "claude*.exe" -and $_.CommandLine -match " remote-control ") -or '
                              '($_.Name -eq "cmd.exe" -and $_.CommandLine -match "start-rc\\.cmd")')
    elif P.IS_MAC:
        LAUNCHD.parent.mkdir(parents=True, exist_ok=True)
        LOG.parent.mkdir(parents=True, exist_ok=True)  # launchd does not create the log folder
        LAUNCHD.write_bytes(_launchd_plist(name, claude, workdir))
        domain = f"gui/{os.getuid()}"
        P.run(["launchctl", "bootout", f"{domain}/{LAUNCHD_LABEL}"])  # fails harmlessly when not loaded
        out = ""
        for _ in range(3):  # bootout finishes asynchronously; bootstrap may briefly fail right after it
            _, out = P.run(["launchctl", "bootstrap", domain, str(LAUNCHD)])
            code, _ = P.run(["launchctl", "print", f"{domain}/{LAUNCHD_LABEL}"])
            if code == 0:
                break
            time.sleep(1)
    else:
        SYSTEMD.parent.mkdir(parents=True, exist_ok=True)
        SYSTEMD.write_text(_systemd_unit(name, claude, workdir), encoding="utf-8")
        P.run(["systemctl", "--user", "daemon-reload"])
        P.run(["systemctl", "--user", "enable", SYSTEMD.name])
        # restart (not just start): a changed name/workdir must take effect on re-run
        code, out = P.run(["systemctl", "--user", "restart", SYSTEMD.name])
    return code == 0, out


def setup(name, providers, apply=True, workdir=None):
    """Returns a list of (tool, ok, message) lines for the report. `workdir`: the folder remote
    Claude sessions start in - it must be a folder Claude Code trusts (never the home directory)."""
    report = []
    workdir = workdir or P.HOME
    if not apply:
        return [(p, True, f"would set up remote access as \"{name}\"") for p in providers]
    save_name(name, workdir)
    if "claude" in providers and (claude := P.find_exe("claude")):
        if not claude_trusts(workdir):
            report.append(("claude", False, f"{workdir} is not a trusted Claude Code folder: run `claude` there once, "
                                            "accept the trust dialog, then re-run `python install.py remote`"))
        else:
            ok, out = _setup_claude(name, claude, workdir)
            report.append(("claude", ok, f"Remote Control server \"{name}\" (sessions start in {workdir})" if ok else out[:200]))
    if "antigravity" in providers and (agy := P.find_exe("agy")):
        P.run([agy, "remote-control", "stop"], timeout=60)
        code, out = P.run([agy, "remote-control", "start", "--name", name, "--session"], timeout=120)
        hidden = "" if not P.IS_WINDOWS else ", starts hidden at logon" if _hide_agy_autostart() else \
            ", autostart entry not found - it may open a terminal window at logon"
        report.append(("antigravity", code == 0, f"daemon \"{name}\" (antigravity.google.com){hidden}" if code == 0 else out[:200]))
    if "codex" in providers:
        if P.IS_WINDOWS and (codex := P.find_exe("codex")):
            # one remote connection per computer: while the ChatGPT app holds it, this one retries
            code, out = _win_loop(TASK_CODEX, "codex-remote.cmd", workdir,
                                  f'call "{codex}" app-server --remote-control --listen off',
                                  '$_.Name -in "codex.exe", "node.exe" -and $_.CommandLine -match "app-server --remote-control"')
            report.append(("codex", code == 0, "remote app server runs hidden from logon; pair once: ChatGPT app > Settings > "
                                               "Connections > Control this PC, or `codex remote-control pair`" if code == 0 else out[:200]))
        elif not P.IS_WINDOWS and (codex := P.find_exe("codex")):
            code, out = P.run([codex, "remote-control", "start"], timeout=120)
            report.append(("codex", code == 0, "daemon started; pair a phone: codex remote-control pair" if code == 0 else out[:200]))
    return report


def remove():
    report = []
    if P.IS_WINDOWS:
        for task in (TASK_CLAUDE, TASK_CODEX) + LEGACY_TASKS:
            _ps(f'Stop-ScheduledTask -TaskName "{task}" -ErrorAction SilentlyContinue; '
                f'Unregister-ScheduledTask -TaskName "{task}" -Confirm:$false -ErrorAction SilentlyContinue')
        report.append(("claude/codex", True, "scheduled tasks removed"))
    elif P.IS_MAC and LAUNCHD.exists():
        P.run(["launchctl", "bootout", f"gui/{os.getuid()}/{LAUNCHD_LABEL}"])  # ignore failure: may not be loaded
        LAUNCHD.unlink()
        report.append(("claude", True, "launchd agent removed"))
    elif SYSTEMD.exists():
        P.run(["systemctl", "--user", "disable", "--now", SYSTEMD.name])
        SYSTEMD.unlink()
        P.run(["systemctl", "--user", "daemon-reload"])
        report.append(("claude", True, "systemd user service removed"))
    if agy := P.find_exe("agy"):
        P.run([agy, "remote-control", "stop"], timeout=60)
        report.append(("antigravity", True, "daemon stopped"))
    if not P.IS_WINDOWS and (codex := P.find_exe("codex")):
        P.run([codex, "remote-control", "stop"], timeout=60)
        report.append(("codex", True, "daemon stopped"))
    return report
