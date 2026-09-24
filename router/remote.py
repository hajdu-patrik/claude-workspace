#!/usr/bin/env python3
"""Optional remote-access module: reach this computer from a phone or another device through each
tool's own remote feature, started automatically at logon, under ONE machine name.

    python install.py remote --name "My Workstation"     # set up (name defaults to the hostname)
    python install.py remote --remove                    # undo

What it sets up (only for tools that are installed):
  Claude       `claude remote-control --name <name>` kept running at logon
               (Windows: scheduled task, macOS: launchd agent, Linux: systemd --user service)
  Antigravity  `agy remote-control start --name <name> --session` (the CLI registers its own autostart)
  Codex        macOS/Linux: `codex remote-control start` (pair with `codex remote-control pair`);
               Windows: the ChatGPT desktop app is started at logon and hosts the connection - the
               Codex daemon cannot detach there when processes run inside a Job Object.
The machine name is stored in ~/.jev-router/config.json, never in the repository.
The computer must be on, awake and logged in for any of this to be reachable.
"""
import json
import os
import plistlib
import time

import platforms as P

BIN = P.HOME / ".jev-router" / "bin"
CONFIG = P.HOME / ".jev-router" / "config.json"
TASK_CLAUDE, TASK_CHATGPT = "JevRouter-ClaudeRemote", "JevRouter-ChatGPT"
LEGACY_TASKS = ("ClaudeRemoteControl", "ChatGPTAutostart", "CodexRemoteControl")
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


def _win_task(name, execute, argument, workdir):
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
        script = BIN / "claude-remote.cmd"
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text(f'@echo off\r\ncd /d "{workdir}"\r\n:loop\r\n"{claude}" remote-control --name "{name}"\r\n'
                          'timeout /t 30 /nobreak >nul\r\ngoto loop\r\n', encoding="utf-8")
        for legacy in LEGACY_TASKS:
            _ps(f'Unregister-ScheduledTask -TaskName "{legacy}" -Confirm:$false -ErrorAction SilentlyContinue')
        # stop a previous instance first: the scheduler ignores Start while the old loop still runs
        _ps(f'Stop-ScheduledTask -TaskName "{TASK_CLAUDE}" -ErrorAction SilentlyContinue; '
            'Get-CimInstance Win32_Process | ? { ($_.Name -like "claude*.exe" -and $_.CommandLine -match " remote-control ") '
            '-or ($_.Name -eq "cmd.exe" -and $_.CommandLine -match "claude-remote\\.cmd|start-rc\\.cmd") } '
            '| % { Stop-Process -Id $_.ProcessId -Force }')
        code, out = _win_task(TASK_CLAUDE, "cmd.exe", f'/c "{script}"', str(workdir))
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
        report.append(("antigravity", code == 0, f"daemon \"{name}\" (antigravity.google.com)" if code == 0 else out[:200]))
    if "codex" in providers:
        if P.IS_WINDOWS:
            code, aumid = _ps('$p = Get-AppxPackage OpenAI.Codex; if ($p) { $p.PackageFamilyName + "!App" }')
            if aumid.strip():
                code, out = _win_task(TASK_CHATGPT, "explorer.exe", f"shell:AppsFolder\\{aumid.strip()}", str(P.HOME))
                report.append(("codex", code == 0, "ChatGPT app starts at logon; pair once: Settings > Connections > "
                                                   "Control this PC > scan the QR code with the ChatGPT mobile app"))
            else:
                report.append(("codex", False, "ChatGPT desktop app not installed - it hosts Codex remote access on Windows"))
        elif codex := P.find_exe("codex"):
            code, out = P.run([codex, "remote-control", "start"], timeout=120)
            report.append(("codex", code == 0, "daemon started; pair a phone: codex remote-control pair" if code == 0 else out[:200]))
    return report


def remove():
    report = []
    if P.IS_WINDOWS:
        for task in (TASK_CLAUDE, TASK_CHATGPT) + LEGACY_TASKS:
            _ps(f'Unregister-ScheduledTask -TaskName "{task}" -Confirm:$false -ErrorAction SilentlyContinue')
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
