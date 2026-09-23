# claude-workspace – JEV prompt router

One router for **Claude Code, Codex and Antigravity**. It runs before every prompt, in every
project, and decides three things per request:

1. **model tier + reasoning effort** – enforced by delegating to a generated worker agent that has a
   fixed model and effort (no hook in any of the three tools can switch the running model itself),
2. **skill** – picked from one shared catalog of every skill on the machine, including skills that
   belong to a *different* tool,
3. **safety + language** – a SAFETY line before irreversible actions, and a reply in the prompt's
   language (Hungarian or English).

The decision maker is **JEV (TypeSafe)**. Until an account exists, a local keyword classifier
("mock JEV") answers in exactly the same shape; setting `TYPESAFE_API_KEY` switches to JEV with no
code change (JEV errors/timeouts fall back to the mock automatically).

```
 prompt ─► hook / MCP tool ─► router/core.route()
                                 ├─ lang.detect()            hu | en
                                 ├─ is_destructive()         regex safety net (+ JEV's own verdict)
                                 ├─ skill_index.prefilter()  ~187 skills → ≤8 candidates
                                 ├─ classify()               JEV (TYPESAFE_API_KEY) or local mock
                                 ├─ decide()                 routes.json: task × difficulty → tier
                                 └─ render()                 targets.json: tier → agent/model/effort text
 ─► "[router] … Delegate to `deep-worker-xhigh` (opus, effort xhigh) … Respond in Hungarian."
```

## Where it runs

| Surface | Mechanism | Status (verified 2026-09-23) |
|---|---|---|
| Claude Code CLI + desktop **Code** tab (+ Remote Control from the phone) | `UserPromptSubmit` hook, `~/.claude/settings.json` | ✅ live; delegates to `fast/main/test/deep-worker-<effort>` |
| Codex CLI + ChatGPT app **Codex** mode | `UserPromptSubmit` hook, `~/.codex/hooks.json` | ✅ live, **after** you trust it once (`codex` → `/hooks`); roles `deep-worker-<effort>` |
| Antigravity CLI + app | `PreInvocation` hook, `~/.gemini/config/hooks.json` (prompt read from the transcript, injected once per turn) | ✅ live |
| Claude desktop **Chat / Cowork** | MCP tool `route_prompt` (no hooks exist there) | ✅ server registered; the model calls it because of the Personal-preferences line below |
| Codex / Antigravity (extra) | same MCP server | ✅ registered |
| ChatGPT plain chat | – | ❌ only remote MCP connectors; not possible free + safely |
| Claude Code on the web (cloud) | project hook with `--cloud-only` in `.claude/settings.json` | ✅ routes to `main` (no agents / CLIs in the sandbox) |

## Layout

| Path | What |
|---|---|
| `router/core.py` | classification, decision, rendering, `route()` pipeline, safety regex, JEV client + mock |
| `router/lang.py` | Hungarian/English detection → "Respond in …" |
| `router/skill_index.py` | skill catalog (`~/.skills/catalog.json`) + pre-filter (IDF overlap, HU→EN glossary) |
| `router/run_hook.py` | hook entry point for all three tools |
| `router/mcp_server.py` | stdio MCP server: `route_prompt`, `list_skills`, `get_skill` |
| `router/install_hooks.py` | installs hooks + MCP for all tools (dry run by default, `--apply`) |
| `router/skills_hub.py` | skill hub: `migrate`, `link`, `agents`, `catalog`, `doctor`, `all` (dry run by default) |
| `router/routes.json` / `targets.json` / `models.json` | task×difficulty → tier; tier → agent/model/effort; verified model lists + policy |
| `agents/*.md` | worker templates → generated `~/.claude/agents/<tier>-worker-<effort>.md` and Codex roles |
| `skills/` | repo-owned skills (exposed through `~/.skills`) – e.g. `cli-bridge` |
| `tests/`, `eval/` | unit tests; 100 Hungarian + 100 English labelled prompts |
| `scripts/` | `setup-windows.ps1`, `check_tools.py` (health report), `check_models.py` (model policy) |

Runtime state lives outside the repo, in `~/.jev-router/` (`bin/` space-free shims that the hooks
call, `logs/routing.jsonl`, `state/`, `backup/`).

## Install / re-install

```powershell
python router/install_hooks.py --apply        # hooks + MCP (writes .bak next to every changed file)
python router/skills_hub.py all --apply       # hub migrate + links + agents + catalog + doctor
python scripts/check_tools.py                 # health report
```
Or everything incl. power settings and phone access: `powershell -ExecutionPolicy Bypass -File scripts\setup-windows.ps1 -KeepAwake -AutoStart`.

Why shims: Antigravity runs hook commands through `cmd /c`, which breaks on quoted paths with
spaces (`E:/Coding Projects/...`). All hooks call `python C:/Users/<you>/.jev-router/bin/run_hook.py`.

## Shared skills (`~/.skills`)

`~/.skills` is the single source of truth (148 skills migrated from `~/.claude/skills`, plus
junctions to repo-owned skills). Each tool sees it natively:

| Tool | How |
|---|---|
| Claude Code | per-skill junctions `~/.claude/skills/<name>` → hub (`synced/` stays app-managed) |
| Codex | per-skill junctions `~/.agents/skills/<name>` → hub |
| Antigravity | `~/.gemini/config/skills.json` → `{"path": "C:/Users/<you>/.skills"}` (**absolute** – agy rejects `~/`) |

Add a skill: put `<name>/SKILL.md` into `~/.skills` (or `skills/` in this repo), then
`python router/skills_hub.py all --apply`.

The catalog also indexes app-managed skills (Claude desktop-synced `anthropic-skills:*`, Claude
plugins `plugin:skill`, Codex `.system`, Antigravity built-ins). If the chosen skill isn't native to
the current tool, the router says *"read and follow `<path>`/SKILL.md"* – that's how Codex or
Antigravity use a Claude skill and vice versa. The mock only commits to a skill when the skill's
own name clearly matches (or you name it: *"use the xlsx skill"*); JEV gets the top-8 candidates.

Known cosmetic limitation: the Claude desktop app's `/` menu doesn't list junction-linked skills
(anthropics/claude-code#68318); the model still loads and uses them.

## Models and effort

| Provider | Tiers (targets.json) | How it's enforced |
|---|---|---|
| Claude | `main` (in-session) · `fast` fable low–high · `sonnet` · `test` sonnet medium–xhigh · `deep` opus high–max · `cli:codex` · `cli:antigravity` | generated subagents with `model:` + `effort:` frontmatter |
| Codex | `main` · `fast` · `deep` gpt-5.6-terra high–ultra | `[agents.deep-worker-<effort>]` roles in `~/.codex/config.toml` |
| Antigravity | `main`/`fast` gemini-3.8-flash-{low,medium,high} · `deep` gemini-3.1-pro-high | advisory (no fixed-model agents in agy); enforced via cli-bridge `agy --model` |

The decided effort is always clamped into what the tier's model supports (e.g. never `ultra` for
Claude). Policy: Claude only via generic aliases (fable/sonnet/opus), never Haiku, never a dated
ID – `python scripts/check_models.py` checks templates, settings and targets.

Overrides: Claude `#fable #sonnet #opus #codex #antigravity`; Codex/Antigravity `#fast #main #deep`;
`#norouter` / `#privat` = no routing, nothing sent to TypeSafe.

## Testing

```powershell
python -m pytest tests -q          # 68 unit tests: language, safety regex (hits + false positives), classifier,
                                   # effort clamp, per-provider routing, hook I/O for all 3 tools, MCP protocol
python eval/eval_router.py         # full pipeline on 100 HU + 100 EN prompts, exit 1 if a target is missed
```
Latest (local mock): task accuracy HU 91% / EN 90% (target ≥ 85%), destructive recall 100%,
false positives 0%, reply-language 100%. Live end-to-end runs (2026-09-23) from a foreign folder:
Claude CLI (hu+en, delegation to `fast-worker-low` observed), Codex CLI (hook + 4 roles visible),
Antigravity CLI (transcript parse, hu reply, cross-tool skill), cli-bridge both ways, MCP via a real client.

## Phone access (PC must be on and awake)

* **Claude (primary)** – Remote Control. `start-rc.cmd` runs `claude remote-control --name "Otthoni gep"`
  in a loop; `setup-windows.ps1 -AutoStart` registers it as the `ClaudeRemoteControl` logon task,
  `-KeepAwake` disables sleep on AC and makes closing the lid do nothing. On the phone: Claude app →
  Code → "Otthoni gep". The router hook runs there too (same `~/.claude/settings.json`).
* **Codex** – ChatGPT desktop app → Settings → Connections → *Control this PC*, scan the QR code
  with the ChatGPT mobile app (alternative: `codex remote-control start` + `codex remote-control pair`).
* **Antigravity** – `agy remote-control start` (background daemon, `agy remote-control status`);
  open it from any phone browser / install as a web app.

## Hungarian speech-to-text (free)

* **PC: Handy** (open source, offline, `winget install cjpais.Handy`). First start: Settings →
  Model → *Whisper Large v3* (or *Large v3 Turbo* for speed; runs on the RTX 4090 GPU) → Language
  **Hungarian** (not auto) → pick a push-to-talk hotkey → enable *Start at login*. Hold the hotkey,
  speak, release: the text is typed into whatever app has focus (Claude, ChatGPT, Antigravity, terminal).
* **Phone:** Gboard (Android) or iOS keyboard dictation with Hungarian enabled – tap the keyboard
  mic inside the Claude / ChatGPT app instead of the app's own voice button (which lacks Hungarian).
* Built-in fallback: Windows `Win+H` (supports Hungarian, lower accuracy).

## One-time manual steps

1. **Codex hook trust:** run `codex` once → `/hooks` → trust the jev-router hook.
2. **Claude desktop Chat/Cowork:** restart the Claude app (loads the MCP server), then Settings →
   Profile → Personal preferences, add:
   `Before answering any new request, call the jev-router route_prompt tool with my message and follow its instructions.`
3. **Handy:** model + Hungarian + hotkey (above).
4. **Phone pairing** for Codex / Antigravity (above).
5. **JEV:** when you get access, set the user env var `TYPESAFE_API_KEY` (setup script asks) – done.

## Debugging

* Every routed prompt: `~/.jev-router/logs/routing.jsonl` (provider, cwd, task, tier, effort, skill,
  latency, error; prompt truncated to 200 chars with secrets redacted, full text only with `ROUTER_LOG_PROMPTS=1`).
* Manual run: `echo {"prompt":"Refaktoráld az auth modult"} | python router/run_hook.py claude UserPromptSubmit`
* Skill search: `python router/skill_index.py "excel táblázat"`; hub health: `python router/skills_hub.py doctor`.
* Env knobs: `ROUTER_BACKEND`, `ROUTER_MIN_CONFIDENCE`, `ROUTER_DESTRUCTIVE_THRESHOLD`, `ROUTER_SKILL_CANDIDATES`,
  `TYPESAFE_API_URL`, `JEV_MODEL`, `JEV_TIMEOUT`, `JEV_ROUTER_HOME`, `JEV_SKILLS_HUB`.
