Respond in the language of the user's prompt (Hungarian or English); the `[router]` context's
"Respond in ..." line says which.

# Work rules

- Every prompt comes with a `[router]` context (from the global UserPromptSubmit hook, see
  `router/`). Follow it: if it names a subagent (e.g. `opus-worker-xhigh`), delegate to it; if it
  names a skill, use it; if it names the `cli-bridge` skill, use that.
- The router is a suggestion. If it's clearly wrong (e.g. sends a trivial question to Opus), you
  may decide otherwise, but say why in one line.
- If SAFETY appears in the context: list the exact actions, and only carry them out after an
  explicit "yes" reply.
- If the router says "unavailable", answer directly; don't try to fix it.
- Manual override tags: Claude `#fable #sonnet #opus #codex #antigravity`; Codex / Antigravity
  `#fast #main #deep`. `#norouter` / `#privat`: no routing, the prompt is never sent to TypeSafe.
- Model-family policy (`router/models.json`): for Claude only sonnet / opus / fable, always via
  the generic alias (never a pinned, dated model ID). Haiku can never be picked. Codex and
  Antigravity targets are verified against this account's live model lists (see models.json).
- Skills live in the shared hub `~/.skills` (linked into Claude Code, Codex and Antigravity by
  `python router/skills_hub.py link --apply`). Repo-owned skills are in `skills/` here and are
  exposed through the hub. The router's skill index is `~/.skills/catalog.json`
  (`python router/skills_hub.py catalog`).
- Worker agents (`<model>-worker-<effort>`, Codex `<model>-<effort>`) are generated from `agents/*.md` + `router/targets.json`
  (`python router/skills_hub.py agents --apply`) into `~/.claude/agents` and `~/.codex/agents`;
  edit the templates / targets, never the generated files.
- Tests: `python -m pytest tests -q`; router quality: `python eval/eval_router.py`.

# Project context (for every new session, incl. ones started remotely from the phone / another laptop)

Machine: "Razer Blade-16" (home PC) - Windows 11, RTX 4090 Laptop. Remote access: Claude
Remote Control server `Razer Blade-16` (start-rc.cmd, `ClaudeRemoteControl` logon task; the Claude
app starts at logon too), Antigravity daemon `razer-blade-16-rising-photon`, Codex via the ChatGPT
app (`ChatGPTAutostart` logon task + one-time "Control this PC" pairing). Cloud sessions are a
separate environment ("Claude GitHub Session") - don't mix them up.

Goal of this repo: one prompt router for Claude Code, Codex and Antigravity (all modes: CLI,
desktop Code tab, Chat/Cowork via MCP). Per prompt JEV (TypeSafe; local mock until the account
exists) decides model (from every selectable model) + reasoning effort (never `ultra`) + skill; reply language follows the prompt (hu/en).
All skills live in the shared hub `~/.skills` and are usable cross-tool. Details: README.md.

Status (2026-09-24): done and live-tested - hooks for all 3 tools, all models/efforts verified live (models.json), generated
worker agents (Claude 18, Codex 34 roles),
skill hub (149 skills, 187 in catalog), MCP router, cli-bridge, 68 unit tests, eval 100 hu + 100 en
(PASS), Handy installed for Hungarian dictation, phone access for Claude + Antigravity.
Open (user's manual steps): trust the Codex hook (`codex` -> /hooks); restart the Claude app and
add the route_prompt line to Personal preferences; Handy first-run setup (Whisper Large v3,
Hungarian, hotkey); pair Codex in the ChatGPT app; set TYPESAFE_API_KEY when JEV access arrives.
Health check: `python scripts/check_tools.py`.
