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
| `python install.py skills [--apply]` | re-link skills, regenerate workers, rebuild the catalog |
| `python install.py doctor` | health report |
| `python install.py route [--provider claude] [--json] <text>` | routing decision for one prompt or sub-task, side-effect free ([details](#routing-decision-on-demand)) |

**One-time steps after installing:** Codex runs a new hook only after you trust it (`codex` → `/hooks`).
For Claude desktop *Chat/Cowork*, restart the app and add to *Settings → Profile → Personal preferences*:
*"Before answering any new request, call the jev-router route_prompt tool with my message and follow its instructions."*

---

## 🧭 How It Works

```
prompt ─► hook / MCP tool ─► jev_router/core.route()
                               ├─ lang.detect()             answer language
                               ├─ is_destructive()          regex safety net (+ JEV verdict)
                               ├─ catalog.prefilter()       shared skill catalog → ≤ 8 candidates
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
| Scripts, other agents and projects (per sub-task) | `route` command via the shim `~/.jev-router/bin/route.py` |

### Model and effort are enforced, not suggested

No tool lets a hook switch the running model. jev-router therefore generates one **worker agent per
(model, effort) pair** – Claude subagents `<model>-worker-<effort>` (plus `test-worker-<effort>`),
Codex roles `<model>-<effort>` – and the router delegates to the right one. Antigravity agents can
pin only a model tier (flash / pro), not an effort, and only as subagents: jev-router generates
`gemini-flash-worker` and `gemini-pro-worker` (`~/.gemini/config/agents/`), and hard requests are
delegated to the Pro one; otherwise the model choice is advisory (or enforced through `cli-bridge`).

Each agent's instructions come from the template of its model's **role** in `models.json` –
`fast`, `balanced`, `deep` (`jev_router/templates/agents/<role>-worker.md`), plus `test-worker` for
the test tier – so a new model needs one line in `models.json`, not a new template.

| Provider | Models (catalog: `jev_router/config/models.json`) | Effort levels |
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

### Routing decision on demand

The hooks route each user prompt. To get a decision for a single sub-task – from a script, another
agent or another project – call the `route` command. It runs the same pipeline (`core.route()`) but
has no side effects: no queue state, no log.

```bash
python ~/.jev-router/bin/route.py --json "add a pagination parameter to the quotes API endpoint"
python install.py route [--provider claude|claude-chat|codex|antigravity] [--json] <text>   # no text: stdin
```

Without `--json` it prints the `[router] …` instruction. With `--json` it prints one object:
`model` (e.g. `sonnet`; `null` when the tier answers in-session), `effort`, `agent` (the worker to
delegate to, or `null`), `tier`, `task`, `difficulty`, `extra_agents`, `destructive`, `skill`,
`verify`, `lang`, `backend`, `text` and `note`. A `#norouter` / `#privat` prompt is not routed
(`model: null`, nothing is sent to TypeSafe). Exit codes: `0` success, `2` usage error (e.g. an
empty prompt), `1` unexpected error (only the exception type goes to stderr). The installer writes
the shim, so callers never need to know where the repository lives.

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
| Per-user state | `~/.jev-router/` – `config.json`, `models.local.json`, `logs/`, `state/`, `bin/` (shims `run_hook.py`, `mcp_server.py`, `route.py`) |
| Model catalog, tiers, routing table | `jev_router/config/models.json`, `targets.json`, `routes.json` |

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

```
install.py                 entry point: `python install.py [command]` (same as `python -m jev_router`)
pyproject.toml             package metadata, console script `jev-router`, pytest settings
jev_router/                the package
├── cli.py                 commands: setup, detect, models, remote, skills, doctor, uninstall, route
├── core.py                classification, decision, rendering, safety regex, JEV client + built-in classifier
├── lang.py                Hungarian / English detection
├── catalog.py             skill catalog and pre-filter
├── hooks.py               hook entry point for all three tools (python -m jev_router.hooks)
├── queue_state.py         queue protection
├── mcp_server.py          MCP server: route_prompt, list_skills, get_skill (python -m jev_router.mcp_server)
├── hub.py                 shared skill folder, links, worker generation
├── integrations.py        hook + MCP registration per tool, ~/.jev-router/bin shims
├── platforms.py           OS abstraction (paths, links, executables, detection)
├── remote.py              optional remote access
├── doctor.py              health report
├── config/                models.json (catalog + policy), routes.json (task → tier), targets.json (tier → worker)
├── templates/agents/      role templates fast/balanced/deep/test-worker.md (rendered into
│                          ~/.claude/agents, ~/.codex/agents and ~/.gemini/config/agents)
└── skills/                skills bundled with jev-router (cli-bridge)
tests/                     unit tests, model-policy test
eval/                      100 Hungarian + 100 English labelled prompts, evaluation script
docs/                      speech-to-text and remote-access guides
```

---

## 🧪 Development

```bash
pip install -e .[dev]              # optional: editable install, adds the `jev-router` command
python -m pytest tests -q          # unit tests incl. the model-policy check
python eval/eval_router.py         # full pipeline on the labelled prompts (exit 1 below target)
```

Evaluation targets: task accuracy ≥ 85 % per language, destructive-request recall 100 %,
false positives < 5 %, reply language 100 %. The test suite runs on Windows, macOS and Linux in CI.

See [CHANGELOG.md](CHANGELOG.md) for the release history and [CLAUDE.md](CLAUDE.md) for contributor
conventions.

---

## 📄 License

[MIT](LICENSE.md). Product names are trademarks of their respective owners.
