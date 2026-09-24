# Changelog

All notable changes, verification results and open items of the JEV prompt router.
Dates are ISO (`YYYY-MM-DD`); newest first.

---

## [2.3.0] – 2026-09-24 · Remote access, naming & parallelism

### Added
- **Parallel-agent decision (0 – +4):** new JEV question `agents` with strict criteria; code-level
  clamps (one lower below 0.7 confidence, at most +1 unless hard, cap `ROUTER_MAX_EXTRA_AGENTS`).
  Rendered as `Parallelism: none` / `Parallelism: up to N extra agent(s)`. All 200 eval prompts → 0.
- **JEV model question:** every selectable model of the provider is offered; the effort is clamped to
  the chosen model's real levels. Codex hook passes the session's model so a tier can stay in-session.
- Model-named workers: Claude `<fable|sonnet|opus>-worker-<effort>` (15) + `test-worker-*` (3);
  Codex roles `<model>-<effort>` (20). Old `fast/main/deep-worker-*` removed automatically.
- Remote access under one name, **Razer Blade-16**, for Claude (Remote Control server, logon task,
  Claude app autostart, new sessions auto-connected), Antigravity (`agy remote-control --name`) and
  ChatGPT/Codex (`ChatGPTAutostart` logon task; phone paired via *Control this PC*).
- Project context section in `CLAUDE.md` for sessions started remotely.
- `README.md` rewritten in portfolio style; `LICENSE.md` (proprietary) added; status moved here.

### Changed
- **`ultra` effort banned for every provider** (`models.json` → `policy.excluded_efforts`): never
  offered to JEV, stripped from answers, no agent/role generated.
- Codex model list re-verified **per model against the ChatGPT account** (see Findings).
- `probe_models.py` reads `codex debug models` instead of the stale `models_cache.json`.
- `skills_hub.py agents` keeps Codex's own tables (e.g. `[hooks.state]` hook trust) outside the
  generated `[agents.*]` block.
- Antigravity `skills.json` also lists the repo's `skills/` (agy does not follow junctions).

### Removed
- `CodexRemoteControl` scheduled task (cannot work on this machine – see Findings).

## [2.0.0] – 2026-09-23 · Router v2

### Added
- Global hooks for **Claude Code, Codex CLI and Antigravity CLI** via space-free shims in
  `~/.jev-router/bin` (Antigravity runs hooks through `cmd /c`, which breaks on quoted paths with spaces).
- Shared skill hub `~/.skills` (148 skills migrated from `~/.claude/skills`, per-skill junctions into
  Claude and Codex) + machine-wide skill catalog with Hungarian → English glossary pre-filter.
- Stdio MCP server (`route_prompt`, `list_skills`, `get_skill`) registered in Claude desktop, Codex and
  Antigravity; `claude-chat` target for hook-less Chat/Cowork.
- Hungarian / English language detection; answer language follows the prompt.
- 100 Hungarian + 100 English labelled evaluation prompts through the full pipeline; pytest suite.
- `scripts/check_tools.py` health report; `cli-bridge` skill with `--model` / `--effort`.
- Hungarian dictation: Handy (Whisper, offline) installed.

### Fixed
- Global hook read its config from the *current project* (`CLAUDE_PROJECT_DIR`) – now from the repo.
- Subagents existed only inside this repo – now user-level and generated.
- Destructive regex false positives ("Remove the unused import", "sort in order") – now object-bound.
- Math false positive on OAuth codes; invalid Codex effort levels; Antigravity hook firing on every
  model call; harness notifications being routed; secrets in the routing log (now redacted).

## [1.x] – 2026-09-23 · Initial router

- Phase-1 scaffold: Claude hook, routes, subagents, cloud mode, Windows setup script.
- Model-family policy (Haiku excluded, generic aliases), local keyword backend with JEV fallback,
  spoken task prefixes, UTF-8 BOM tolerant hook stdin.

---

## Verification snapshot (2026-09-24)

| Check | Result |
| --- | --- |
| Unit tests | 78 passed |
| Eval – task accuracy | HU 91 % · EN 90 % (target ≥ 85 %) |
| Eval – destructive recall / false positives | 100 % / 0 % |
| Eval – reply language | 100 % |
| Claude CLI (foreign folder) | hook fires, hu + en, delegation to a generated worker observed |
| Codex CLI + ChatGPT app (Codex mode) | hook fires (trusted), roles visible to `spawn_agent` |
| Antigravity CLI | transcript parsing, once-per-turn injection, cross-tool skill suggestion |
| Skills visible | Claude all hub skills; Codex and Antigravity spot checks all present |
| cli-bridge | Codex and Antigravity round-trips OK |
| MCP | `route_prompt` via a real MCP client, SKILL.md returned inline |
| Remote access | Claude RC server + session online; ChatGPT *Control this PC* on, phone paired |

## Findings (verified live)

- **Codex models on a ChatGPT account:** working – gpt-6-luna, gpt-5.6-terra, gpt-5.6-luna,
  gpt-reserve. Rejected – gpt-6-astra, gpt-6-sol, gpt-5.6-sol ("not supported when using Codex with a
  ChatGPT account"), gpt-5.5 (404). `codex debug models` lists the binary's catalog, not the account's.
- **Codex remote-control daemon** cannot detach on this Windows build: every process, Explorer
  included, runs inside a Job Object without breakaway (tried shell, Task Scheduler, WMI, Explorer).
  The ChatGPT desktop app hosts the remote connection instead.
- **Antigravity:** no prompt-submit hook (only `PreInvocation`); `skills.json` paths must be absolute
  (`~/` rejected despite the docs); junctions inside a skills entry are not followed; the desktop app
  2.17 has no Remote Control toggle – the CLI daemon provides it.
- **Claude:** hooks cannot switch model or effort (hence generated workers); Cowork and claude.ai chat
  run no hooks (hence MCP); the desktop `/` menu does not list junction-linked skills (model still uses them).
- The ChatGPT app's *Hooks* settings page shows "No hooks found" although the hook runs.
- Router misclassifies very long pasted content (e.g. an English README pasted into a Hungarian
  prompt): language, difficulty and SAFETY follow the pasted text.

## Open items

- [ ] JEV / TypeSafe account → set `TYPESAFE_API_KEY` (local model until then).
- [ ] Cloud sandboxes: Claude – connect GitHub on claude.ai/code and name the environment
      "Claude GitHub Session"; Codex – create an environment on chatgpt.com/codex (plan may limit it).
- [ ] Claude Chat/Cowork: add the `route_prompt` line to Personal preferences; restart the Claude app.
- [ ] Verify "Razer Blade-16" on antigravity.google.com from the phone.
- [ ] Handy: select Whisper Large v3 after download, set language to Hungarian.
- [ ] Optional: keep the PC awake on AC power (currently only the apps' own keep-awake settings).
- [ ] Improve routing of long pasted content (weight the user's own lines over quoted text).
