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

import platforms as P

BIN = P.HOME / ".jev-router" / "bin"
CONFIG = P.HOME / ".jev-router" / "config.json"
TASK_CLAUDE, TASK_CHATGPT = "JevRouter-ClaudeRemote", "JevRouter-ChatGPT"
LEGACY_TASKS = ("ClaudeRemoteControl", "ChatGPTAutostart", "CodexRemoteControl")
LAUNCHD = P.HOME / "Library" / "LaunchAgents" / "com.jev-router.claude-remote.plist"
SYSTEMD = P.HOME / ".config" / "systemd" / "user" / "jev-router-claude-remote.service"


def save_name(name):
    cfg = json.loads(CONFIG.read_text(encoding="utf-8")) if CONFIG.exists() else {}
    cfg["remote_name"] = name
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


def _setup_claude(name, claude):
    if P.IS_WINDOWS:
        script = BIN / "claude-remote.cmd"
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text(f'@echo off\r\ncd /d "%USERPROFILE%"\r\n:loop\r\n"{claude}" remote-control --name "{name}"\r\n'
                          'timeout /t 30 /nobreak >nul\r\ngoto loop\r\n', encoding="utf-8")
        for legacy in LEGACY_TASKS:
            _ps(f'Unregister-ScheduledTask -TaskName "{legacy}" -Confirm:$false -ErrorAction SilentlyContinue')
        _ps('Get-CimInstance Win32_Process | ? { $_.Name -like "claude*" -and $_.CommandLine -match "remote-control" } '
            '| % { Stop-Process -Id $_.ProcessId -Force }')
        code, out = _win_task(TASK_CLAUDE, "cmd.exe", f'/c "{script}"', str(P.HOME))
    elif P.IS_MAC:
        LAUNCHD.parent.mkdir(parents=True, exist_ok=True)
        LAUNCHD.write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.jev-router.claude-remote</string>
  <key>ProgramArguments</key><array><string>{claude}</string><string>remote-control</string><string>--name</string><string>{name}</string></array>
  <key>WorkingDirectory</key><string>{P.HOME}</string>
  <key>RunAtLoad</key><true/><key>KeepAlive</key><true/>
</dict></plist>
""", encoding="utf-8")
        P.run(["launchctl", "unload", str(LAUNCHD)])
        code, out = P.run(["launchctl", "load", "-w", str(LAUNCHD)])
    else:
        SYSTEMD.parent.mkdir(parents=True, exist_ok=True)
        SYSTEMD.write_text(f"""[Unit]
Description=Claude Code Remote Control ({name})

[Service]
ExecStart="{claude}" remote-control --name "{name}"
WorkingDirectory={P.HOME}
Restart=always
RestartSec=30

[Install]
WantedBy=default.target
""", encoding="utf-8")
        P.run(["systemctl", "--user", "daemon-reload"])
        code, out = P.run(["systemctl", "--user", "enable", "--now", SYSTEMD.name])
    return code == 0, out


def setup(name, providers, apply=True):
    """Returns a list of (tool, ok, message) lines for the report."""
    report = []
    if not apply:
        return [(p, True, f"would set up remote access as \"{name}\"") for p in providers]
    save_name(name)
    if "claude" in providers and (claude := P.find_exe("claude")):
        ok, out = _setup_claude(name, claude)
        report.append(("claude", ok, f"Remote Control server \"{name}\" at logon" if ok else out[:200]))
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
        P.run(["launchctl", "unload", "-w", str(LAUNCHD)])
        LAUNCHD.unlink()
        report.append(("claude", True, "launchd agent removed"))
    elif SYSTEMD.exists():
        P.run(["systemctl", "--user", "disable", "--now", SYSTEMD.name])
        SYSTEMD.unlink()
        report.append(("claude", True, "systemd user service removed"))
    if agy := P.find_exe("agy"):
        P.run([agy, "remote-control", "stop"], timeout=60)
        report.append(("antigravity", True, "daemon stopped"))
    if not P.IS_WINDOWS and (codex := P.find_exe("codex")):
        P.run([codex, "remote-control", "stop"], timeout=60)
        report.append(("codex", True, "daemon stopped"))
    return report
