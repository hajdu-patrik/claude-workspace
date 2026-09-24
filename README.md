# JEV Prompt Router for Claude Code, Codex & Antigravity

![Python](https://img.shields.io/badge/Python-3.14-3776AB?style=flat&logo=python&logoColor=white)
![Stdlib Only](https://img.shields.io/badge/Dependencies-Stdlib_Only-2E7D32?style=flat)
![Claude Code](https://img.shields.io/badge/Claude_Code-Hook_%2B_Agents-D97757?style=flat&logo=anthropic&logoColor=white)
![Codex](https://img.shields.io/badge/Codex_CLI-Hook_%2B_Roles-412991?style=flat&logo=openai&logoColor=white)
![Antigravity](https://img.shields.io/badge/Antigravity-PreInvocation_Hook-4285F4?style=flat&logo=google&logoColor=white)
![MCP](https://img.shields.io/badge/MCP-stdio_Server-000000?style=flat)
![Windows](https://img.shields.io/badge/Platform-Windows_11-0078D6?style=flat&logo=windows&logoColor=white)
![Tests](https://img.shields.io/badge/Tests-78_passing-success?style=flat&logo=pytest&logoColor=white)
![License](https://img.shields.io/badge/License-Proprietary-red?style=flat)

A single prompt router that runs **before every prompt** in Claude Code, OpenAI Codex and Google
Antigravity – in every project, in the desktop apps, from the phone and in hook-less chat modes.

For each request the router decides **which model, which reasoning effort, how many parallel agents
and which skill** to use, adds a safety gate for irreversible actions, and makes the model answer in
the language of the prompt (Hungarian or English). The decision maker is **JEV (TypeSafe)**; until
an account is available, a local classifier with the exact same answer schema stands in for it.

---

## ✨ Key Engineering Features

* **One Core, Three Hosts:** A provider-independent core (`router/core.py`) is reached through each
  tool's native pre-prompt hook – Claude `UserPromptSubmit`, Codex `UserPromptSubmit`, Antigravity
  `PreInvocation` (prompt read back from the transcript, injected once per turn).
* **Enforced Model & Effort Selection:** Hooks cannot switch the running model, so every decision is
  enforced by delegating to a **generated worker with a fixed model and effort** – one Claude subagent
  or Codex role for every (model, effort) pair that is actually available on the account.
* **Hard Policy in Code, Not in Prompts:** `ultra` effort is never offered and is stripped from any
  answer; Haiku and pinned model IDs are rejected; efforts are clamped to what the chosen model
  really supports; a regex safety net backs up the model's own "destructive" verdict.
* **Strict Parallelism Budget:** JEV picks 0 – +4 extra agents per task, with code-level clamps
  (one lower on low confidence, at most +1 unless the task is hard), because agents multiply tokens.
* **Shared Skill Hub:** Every skill lives once in `~/.skills` and is linked into all three tools; a
  machine-wide catalog lets the router pick a skill – even one that belongs to another tool.
* **Hook-less Modes Covered:** A dependency-free stdio **MCP server** exposes `route_prompt`,
  `list_skills` and `get_skill` to Claude desktop Chat/Cowork, Codex and Antigravity.
* **Bilingual by Design:** Hungarian + English keyword model, language detection, and 100 + 100
  labelled evaluation prompts measured through the full pipeline.
* **Fail-Open & Private:** The hook never blocks a prompt, falls back from JEV to the local model on
  any error, and logs only a redacted, truncated prompt (`#privat` / `#norouter` skip routing).

---

## 🧭 How It Works

```
prompt ─► hook / MCP tool ─► core.route()
                               ├─ lang.detect()            hu | en
                               ├─ is_destructive()         regex safety net (+ JEV verdict)
                               ├─ skill_index.prefilter()  skill catalog → ≤ 8 candidates
                               ├─ classify()               JEV (TYPESAFE_API_KEY) or local model
                               ├─ decide()                 routes.json: task × difficulty → tier
                               └─ render()                 targets.json: tier → agent / model / effort
─► "[router] … Delegate to `opus-worker-xhigh` (opus, effort xhigh). Parallelism: none … Respond in Hungarian."
```

| Surface | Mechanism |
| --- | --- |
| Claude Code CLI, desktop Code tab, Remote Control | `UserPromptSubmit` hook (`~/.claude/settings.json`) |
| Codex CLI, ChatGPT app (Codex mode) | `UserPromptSubmit` hook (`~/.codex/hooks.json`) |
| Antigravity CLI & app | `PreInvocation` hook (`~/.gemini/config/hooks.json`) |
| Claude desktop Chat / Cowork | MCP tool `route_prompt` |
| Claude Code on the web (cloud) | project hook with `--cloud-only` (routes to `main`) |

---

## 🧠 Models, Effort & Parallelism

The router only offers models verified to work on this machine's accounts (`router/models.json`).

| Provider | Selectable models | Effort levels | Enforcement |
| --- | --- | --- | --- |
| **Claude** | fable, sonnet, opus (generic aliases only) | low · medium · high · xhigh · max | `<model>-worker-<effort>` subagents |
| **Codex** | gpt-6-luna, gpt-5.6-terra, gpt-5.6-luna, gpt-reserve | low · medium · high · xhigh · max | `<model>-<effort>` roles |
| **Antigravity** | gemini-3.8 / 3.7 / 3.6-flash, gemini-3.1-pro, claude-sonnet-4-6, claude-opus-4-6-thinking, gpt-oss-120b | encoded in the model slug | advisory + `cli-bridge` |

| Extra agents | When |
| --- | --- |
| **0** | default – the vast majority of requests |
| **+1** | two clearly independent, substantial parts |
| **+2** | three independent workstreams (e.g. backend + frontend + migration) |
| **+3** | a complete new page / feature from scratch (backend + frontend + data layer) |
| **+4** | very rare – the same, plus custom tooling such as a scraper |

**Overrides:** Claude `#fable #sonnet #opus #codex #antigravity` · Codex / Antigravity
`#fast #main #deep` · `#norouter` / `#privat` = no routing, nothing sent to TypeSafe.

---

## 🛠️ Technology Stack

* **Language:** [Python 3.14](https://www.python.org/) (standard library only – no runtime dependencies)
* **Hosts:** [Claude Code](https://code.claude.com/), [OpenAI Codex](https://developers.openai.com/codex), [Google Antigravity](https://antigravity.google/)
* **Protocol:** [Model Context Protocol](https://modelcontextprotocol.io/) (newline-delimited JSON-RPC over stdio)
* **Decision Engine:** JEV / TypeSafe (`TYPESAFE_API_KEY`), local classifier as drop-in fallback
* **Speech-to-Text:** [Handy](https://github.com/cjpais/Handy) with Whisper Large v3 (offline, GPU)
* **Quality Gate:** [pytest](https://pytest.org/) unit suite + bilingual evaluation harness

---

## 📂 Repository Layout

| Path | Purpose |
| --- | --- |
| `router/core.py` | classification, decision, rendering, safety regex, JEV client + local model |
| `router/run_hook.py` | hook entry point for all three tools |
| `router/mcp_server.py` | MCP server (`route_prompt`, `list_skills`, `get_skill`) |
| `router/skill_index.py` · `router/skills_hub.py` | skill catalog + pre-filter · hub migration, linking, agent generation |
| `router/install_hooks.py` | installs hooks + MCP for every tool (dry run by default) |
| `router/models.json` · `routes.json` · `targets.json` | verified models + policy · task → tier · tier → agent / model / effort |
| `agents/` · `skills/` | worker templates · repo-owned skills (e.g. `cli-bridge`) |
| `tests/` · `eval/` | unit tests · 100 HU + 100 EN labelled prompts |
| `scripts/` | `setup-windows.ps1`, `check_tools.py` (health report), `check_models.py` (policy check) |

Runtime state lives outside the repository in `~/.jev-router/` (space-free shims, logs, backups).

---

## 🚀 Setup

```powershell
python router/install_hooks.py --apply      # hooks + MCP for Claude, Codex, Antigravity (.bak backups)
python router/skills_hub.py all --apply     # skill hub, links, generated agents, catalog
python scripts/check_tools.py               # health report
```

Full machine setup incl. phone access and autostart:
`powershell -ExecutionPolicy Bypass -File scripts\setup-windows.ps1 -AutoStart`.
JEV is enabled by setting the user environment variable `TYPESAFE_API_KEY` – no code change needed.

**One-time manual steps:** trust the Codex hook (`codex` → `/hooks`); pair the phone in the ChatGPT
app (Settings → Connections → *Control this PC*); for Claude Chat/Cowork add to Settings → Profile →
Personal preferences: *"Before answering any new request, call the jev-router route_prompt tool with
my message and follow its instructions."*

---

## 📱 Remote Access & Dictation

The PC appears under the same name, **Razer Blade-16**, in every tool. It must be switched on,
awake and logged in; all remote services start automatically at logon.

| Tool | From the phone / another laptop |
| --- | --- |
| Claude | Claude app → Code, or claude.ai/code → *Razer Blade-16* |
| Gemini (Antigravity) | antigravity.google.com → *Razer Blade-16* |
| ChatGPT (Codex) | ChatGPT app, paired once with a QR code |

**Hungarian dictation:** on the PC hold `Ctrl+Space` in any app (Handy, Whisper Large v3, language
set to Hungarian); on the phone use the keyboard's microphone (Gboard / iOS) with Hungarian enabled.

---

## 🧪 Testing & Debugging

```powershell
python -m pytest tests -q          # unit suite: language, safety regex, routing, hooks, MCP, policy
python eval/eval_router.py         # full pipeline on 100 HU + 100 EN prompts (exit 1 below target)
python scripts/check_models.py     # every model / effort named anywhere is allowed and available
```

Targets: task accuracy ≥ 85 % per language, destructive recall 100 %, false positives < 5 %,
reply language 100 %.

* Routing log: `~/.jev-router/logs/routing.jsonl` (full prompt text only with `ROUTER_LOG_PROMPTS=1`).
* Manual run: `echo {"prompt":"Refaktoráld az auth modult"} | python router/run_hook.py claude UserPromptSubmit`
* Skill search: `python router/skill_index.py "excel táblázat"` · hub health: `python router/skills_hub.py doctor`
* Environment: `ROUTER_BACKEND`, `ROUTER_MIN_CONFIDENCE`, `ROUTER_MAX_EXTRA_AGENTS`, `ROUTER_SKILL_CANDIDATES`,
  `TYPESAFE_API_URL`, `JEV_MODEL`, `JEV_TIMEOUT`, `JEV_ROUTER_HOME`, `JEV_SKILLS_HUB`.

---

## ⚠️ Important Notice: Project Status

This repository is published **for portfolio and demonstration purposes only**.

**This is not an open-source project.** You are strictly prohibited from copying, distributing,
modifying, or using this code for any academic, commercial, or personal projects. Please see the
`LICENSE.md` file for a detailed breakdown of these restrictions.

---

## 📦 Release Log

Version history, verification results, platform findings and open items are maintained in
[`CHANGELOG.md`](CHANGELOG.md).
