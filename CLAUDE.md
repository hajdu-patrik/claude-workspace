Respond in the language of the user's prompt (Hungarian or English); the `[router]` context's
"Respond in ..." line says which.

# Work rules

- Every prompt comes with a `[router]` context (from the global UserPromptSubmit hook, see
  `router/`). Follow it: if it names a subagent (e.g. `deep-worker-xhigh`), delegate to it; if it
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
- Worker agents are generated from `agents/*.md` + `router/targets.json`
  (`python router/skills_hub.py agents --apply`) into `~/.claude/agents` and `~/.codex/agents`;
  edit the templates / targets, never the generated files.
- Tests: `python -m pytest tests -q`; router quality: `python eval/eval_router.py`.
