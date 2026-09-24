# Remote Access Guide

Send prompts to your computer from a phone or another device through each tool's own remote
feature. The router, skills and agents work exactly as when you type locally, because the session
runs on your computer.

```bash
python install.py remote --name "My Workstation"                  # name defaults to the hostname
python install.py remote --name "My Workstation" --workdir ~/code  # folder remote sessions start in
python install.py remote --remove                                 # undo
```

The name and folder are stored in `~/.jev-router/config.json`; the name is shown on your other
devices. Use the same name for every tool so you always recognize the machine.

**Working folder:** remote Claude sessions start in `--workdir` (default: the jev-router folder).
Claude Code only serves folders whose workspace-trust dialog was accepted, and never the home
directory: run `claude` once in that folder and accept the dialog before setting up remote access.

## What gets set up

| Tool | On the computer (starts automatically at logon) | On the phone / another device |
| --- | --- | --- |
| **Claude Code** | `claude remote-control --name <name>` – Windows scheduled task `JevRouter-ClaudeRemote`, macOS launchd agent `com.jev-router.claude-remote`, Linux `systemd --user` service `jev-router-claude-remote` | Claude app → **Code**, or [claude.ai/code](https://claude.ai/code) → *<name>* |
| **Antigravity** | `agy remote-control start --name <name> --session` (the CLI registers its own autostart) | [antigravity.google.com](https://antigravity.google.com) → *<name>* (can be installed as a web app for notifications) |
| **Codex** | macOS / Linux: `codex remote-control start`. Windows: the ChatGPT desktop app is started at logon (task `JevRouter-ChatGPT`) and hosts the connection | ChatGPT app, after a one-time pairing (below) |

### One-time pairing for Codex

- **Windows:** ChatGPT desktop app → Settings → **Connections** → turn on *Control this PC* → scan
  the QR code with the ChatGPT mobile app. Optionally enable *Keep this PC awake*.
- **macOS / Linux:** run `codex remote-control pair` and follow the instructions.

The pairing persists. Pair again only after signing out, reinstalling the app, switching phones or
revoking the device.

Why the ChatGPT app on Windows: `codex remote-control start` must detach a background daemon. On
Windows builds where every process (Explorer included) runs inside a Job Object without breakaway
permission, the daemon cannot detach from any launcher; the ChatGPT app hosts the connection itself.

## Requirements

- The computer must be **switched on, awake and logged in** – remote services start at logon.
  Use the tools' own keep-awake options (Claude app, ChatGPT *Keep this PC awake*, Antigravity
  *Prevent Sleep*) or your OS power settings.
- Each tool must be logged in with the same account you use on the other device.
- Claude Remote Control requires a Claude subscription that includes Claude Code; Codex remote and
  Antigravity remote control depend on your plan and region.

## Cloud sessions (computer switched off)

Remote control needs your computer. To run tasks when it is off, use the tools' cloud sandboxes;
they work on a GitHub copy of your repository, not on this computer:

- **Claude Code on the web:** connect GitHub at [claude.ai/code](https://claude.ai/code) and create
  an environment. Give it a name distinct from your machine (for example *Cloud*), so cloud and
  local sessions are never confused. This repository's project hook (`--cloud-only`) routes there too.
- **Codex Cloud:** create an environment at [chatgpt.com/codex](https://chatgpt.com/codex), then use
  `codex cloud exec --env <id> "<task>"` or the ChatGPT app.
