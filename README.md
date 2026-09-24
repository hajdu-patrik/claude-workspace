# jev-router – One Prompt Router for Claude Code, Codex & Antigravity

![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?style=flat&logo=python&logoColor=white)
![Stdlib Only](https://img.shields.io/badge/Dependencies-None-2E7D32?style=flat)
![Claude Code](https://img.shields.io/badge/Claude_Code-supported-D97757?style=flat&logo=anthropic&logoColor=white)
![Codex](https://img.shields.io/badge/OpenAI_Codex-supported-412991?style=flat&logo=openai&logoColor=white)
![Antigravity](https://img.shields.io/badge/Google_Antigravity-supported-4285F4?style=flat&logo=google&logoColor=white)
![MCP](https://img.shields.io/badge/MCP-stdio_server-000000?style=flat)
![Platforms](https://img.shields.io/badge/Platforms-Windows_%7C_macOS_%7C_Linux-555555?style=flat)
![License](https://img.shields.io/badge/License-MIT-yellow?style=flat)

**jev-router** runs before every prompt you send to Claude Code, OpenAI Codex or Google Antigravity –
in every project, in the desktop apps and in remote sessions – and decides for that single request:

* **which model** and **which reasoning effort** to use,
* **how many extra agents** may work in parallel (strict token budget),
* **which skill** fits – from one shared folder that all three tools read,
* whether the request is **irreversible** (then the model must ask before acting),
* **which language** to answer in (the language of the prompt).

It also **protects running work**: a prompt sent while an earlier one is still being processed is
queued behind it and must not stop or overwrite it.

Decisions come from **JEV (TypeSafe)** when you configure a token. Without one, a built-in local
classifier with the same answer format decides – everything works out of the box.

---

## 🚀 Quick Start

Requirements: Python 3.10+ and at least one of [Claude Code](https://code.claude.com/docs/en/quickstart),
[Codex CLI](https://developers.openai.com/codex) or [Antigravity CLI](https://antigravity.google/docs/cli/install/).

```bash
git clone <this repository> jev-router
cd jev-router
python install.py
```

The installer walks you through five steps:

1. **Detect** which of Claude Code, Codex and Antigravity are installed and logged in, and tell you
   how to log in to the ones that are not.
2. **JEV token** – paste it, or press Enter to use the built-in local classifier.
3. **Hooks + MCP server** for every logged-in tool.
4. **Shared skill folder** `~/.skills` – existing skills of every tool are moved there and linked
   back, so each tool sees all of them; worker agents are generated for every model × effort.
5. **Optional extras** – remote access from your phone, speech-to-text.

Every change is shown first, changed config files get a `.bak` copy, and re-running is safe.
Preview without changing anything: `python install.py --dry-run`.

| Command | Purpose |
| --- | --- |
| `python install.py detect` | report installed / logged-in tools |
| `python install.py models --probe` | test which models your accounts may use (stored per user) |
| `python install.py remote --name "My PC" [--workdir <folder>]` | remote access from other devices ([guide](docs/remote-access.md)) |
| `python install.py uninstall` | remove hooks, MCP entries and remote access (skills stay) |
| `python scripts/check_tools.py` | health report |

**One-time steps after installing:** Codex runs a new hook only after you trust it (`codex` → `/hooks`).
For Claude desktop *Chat/Cowork*, restart the app and add to *Settings → Profile → Personal preferences*:
*"Before answering any new request, call the jev-router route_prompt tool with my message and follow its instructions."*

---

## 🧭 How It Works

```
prompt ─► hook / MCP tool ─► router/core.route()
                               ├─ lang.detect()             answer language
                               ├─ is_destructive()          regex safety net (+ JEV verdict)
                               ├─ skill_index.prefilter()   shared skill catalog → ≤ 8 candidates
                               ├─ classify()                JEV (token) or built-in classifier
                               ├─ decide()                  routes.json: task × difficulty → tier
                               └─ render()                  targets.json: tier → worker, model, effort
       + queue_state            earlier work still running? → "finish it first"
─► "[router] … Delegate to `opus-worker-xhigh` (opus, effort xhigh). Parallelism: none. Respond in English."
```

| Surface | Mechanism |
| --- | --- |
| Claude Code (CLI, desktop *Code*, Remote Control) | `UserPromptSubmit` + `Stop` hooks |
| Codex (CLI, ChatGPT app in Codex mode) | `UserPromptSubmit` + `Stop` hooks |
| Antigravity (CLI, desktop app) | `PreInvocation` + `Stop` hooks (prompt read from the transcript, injected once per turn) |
| Claude desktop *Chat / Cowork* (no hooks there) | MCP tool `route_prompt` |
| Claude Code on the web (cloud sandbox) | project hook with `--cloud-only` |

### Model and effort are enforced, not suggested

No tool lets a hook switch the running model. jev-router therefore generates one **worker agent per
(model, effort) pair** – Claude subagents `<model>-worker-<effort>` (plus `test-worker-<effort>`),
Codex roles `<model>-<effort>` – and the router delegates to the right one. Antigravity has no
fixed-model agents, so its model choice is advisory (or enforced through the `cli-bridge` skill).

| Provider | Models (catalog: `router/models.json`) | Effort levels |
| --- | --- | --- |
| Claude | fable, sonnet, opus – generic aliases only, never Haiku | low · medium · high · xhigh · max |
| Codex | gpt-6-luna, gpt-5.6-terra, gpt-5.6-luna, gpt-reserve by default; more after `models --probe` | low … max (per model) |
| Antigravity | Gemini 3.8 / 3.7 / 3.6 Flash, Gemini 3.1 Pro, Claude Sonnet/Opus 4.6, GPT-OSS 120B | part of the model name |

The `ultra` effort level is never offered, stripped from any answer and has no worker.

### Parallel agents

| Extra agents | When |
| --- | --- |
| **0** | default – the vast majority of requests |
| **+1** | two clearly independent, substantial parts |
| **+2** | three independent workstreams (e.g. backend + frontend + migration) |
| **+3** | a complete new page or feature from scratch (backend + frontend + data layer) |
| **+4** | very rare – the same, plus custom tooling such as a scraper |

Code-level clamps: one fewer when the answer is not confident, at most +1 unless the request is hard.

### Queue protection

All three tools already queue messages typed while the agent is busy. jev-router adds the missing
context: the new prompt is told that earlier work in the same session is still running and must be
finished first – never stopped, restarted or overwritten. If another session works in the same
folder, the prompt is warned not to modify that session's files. State expires automatically, so a
crashed session never blocks anything.

### Shared skills

`~/.skills` is the single skill folder. Claude Code and Codex see it through per-skill links
(junctions on Windows, symlinks elsewhere); Antigravity through its `skills.json`. Skills that tools
manage themselves (Claude desktop's synced skills, plugins, Codex built-ins) stay in place but are
indexed too, so the router can hand a skill of one tool to another ("read and follow `<path>/SKILL.md`").

### Overrides

Claude `#fable #sonnet #opus #codex #antigravity` · Codex / Antigravity `#fast #main #deep` ·
`#norouter` / `#privat`: no routing, nothing is sent to TypeSafe (queue protection still applies).

---

## 🎙️ Speech-to-Text

Dictate prompts into any app, in any language Whisper supports – offline and free.
See **[docs/speech-to-text.md](docs/speech-to-text.md)** for installation, model choice by hardware
and phone dictation.

## 📱 Remote Access

Control your computer from a phone or another device under one machine name, with every tool
starting its remote service at logon. See **[docs/remote-access.md](docs/remote-access.md)**.

---

## ⚙️ Configuration

| Setting | Where |
| --- | --- |
| JEV token | `python install.py --jev-token=<token>` or environment variable `TYPESAFE_API_KEY` |
| Per-user state | `~/.jev-router/` – `config.json`, `models.local.json`, `logs/`, `state/`, `bin/` |
| Model catalog, tiers, routing table | `router/models.json`, `router/targets.json`, `router/routes.json` |

| Environment variable | Default | Meaning |
| --- | --- | --- |
| `ROUTER_BACKEND` | `auto` | `jev`, `local` or `auto` (JEV when a token exists) |
| `ROUTER_MIN_CONFIDENCE` | `0.6` | below it the task falls back to the default tier |
| `ROUTER_MAX_EXTRA_AGENTS` | `4` | hard cap for parallel agents |
| `ROUTER_QUEUE_TTL_MIN` | `120` | minutes after which an unfinished queue entry is ignored |
| `ROUTER_SKILL_CANDIDATES` | `8` | skills offered to JEV per request |
| `ROUTER_LOG_PROMPTS` | unset | `1` = log full prompts (default: first 200 characters, secrets redacted) |
| `TYPESAFE_API_URL`, `JEV_MODEL`, `JEV_TIMEOUT` | – | JEV endpoint, pinned model version, timeout in seconds |

---

## 📂 Repository Layout

| Path | Purpose |
| --- | --- |
| `install.py` | cross-platform installer (detect, log in, connect, skills, extras, uninstall) |
| `router/core.py` | classification, decision, rendering, safety regex, JEV client + built-in classifier |
| `router/run_hook.py` · `router/queue_state.py` | hook entry point for all tools · queue protection |
| `router/mcp_server.py` | MCP server: `route_prompt`, `list_skills`, `get_skill` |
| `router/skill_index.py` · `router/skills_hub.py` | skill catalog + pre-filter · shared folder, links, worker generation |
| `router/install_hooks.py` · `router/platforms.py` · `router/remote.py` | hooks/MCP · OS abstraction · remote access |
| `router/*.json` | model catalog and policy, routing table, tier targets |
| `agents/` · `skills/` | worker templates · bundled skills (`cli-bridge`) |
| `tests/` · `eval/` | unit tests · 100 Hungarian + 100 English labelled prompts |
| `docs/` · `scripts/` | guides · health report and model-policy check |

---

## 🧪 Development

```bash
python -m pytest tests -q          # unit tests
python eval/eval_router.py         # full pipeline on the labelled prompts (exit 1 below target)
python scripts/check_models.py     # every model / effort referenced is allowed and in the catalog
```

Evaluation targets: task accuracy ≥ 85 % per language, destructive-request recall 100 %,
false positives < 5 %, reply language 100 %. The test suite runs on Windows, macOS and Linux in CI.

See [CHANGELOG.md](CHANGELOG.md) for the release history and [CLAUDE.md](CLAUDE.md) for contributor
conventions.

---

## 📄 License

[MIT](LICENSE.md). Product names are trademarks of their respective owners.
