# Changelog

All notable changes to this project. The format follows [Keep a Changelog](https://keepachangelog.com/);
dates are ISO 8601.

---

## [3.0.1] – 2026-09-24 · Cross-platform review fixes

### Fixed
- macOS: the launchd plist is written with `plistlib` (names with `&`/`<` no longer break it), gets
  the caller's `PATH` (npm/Homebrew/nvm `claude` finds `node`), a 30 s throttle and a log file, and
  is loaded with `launchctl bootout`/`bootstrap` and verified with `launchctl print`.
- Linux: systemd `%`/`$`/quote escaping; `restart` after `enable`, so a new name or folder takes effect.
- Queue: a turn cancelled with Esc (Claude runs no `Stop` hook then) no longer blocks the next prompt –
  entries of a session whose transcript has been silent for `ROUTER_QUEUE_IDLE_MIN` (default 10) are
  dropped; Antigravity `Stop` clears only when `fullyIdle`; the lock is released only by its owner;
  atomic state writes; invalid `ROUTER_QUEUE_TTL_MIN` no longer crashes the hook.
- Installer: unknown sub-commands are rejected; without a terminal and without `--yes` nothing is
  moved; `remote --remove --dry-run` changes nothing; the token file is created owner-only; a config
  file with invalid JSON is reported and skipped instead of aborting; the first `.bak` is never
  overwritten; migration only touches the selected tools' skill folders.
- Hook commands are shell-safe (quoted on POSIX, short paths on Windows); MCP entries use the plain
  interpreter path; a virtualenv's base interpreter is used so hooks survive the venv's removal.
- Link ownership uses real path containment (`~/.skills-old` is not `~/.skills`); `LOCALAPPDATA`
  is only consulted on Windows.

## [3.0.0] – 2026-09-24 · Open-source release

### Added
- **Cross-platform installer** `install.py` (Windows, macOS, Linux): detects Claude Code, Codex and
  Antigravity, reports install / login state and guides the login, asks for an optional JEV token,
  connects hooks + MCP for every logged-in tool, migrates all tools' skills into `~/.skills`,
  generates workers, offers remote access and speech-to-text. Sub-commands `detect`,
  `models --probe`, `remote`, `uninstall`; flags `--yes`, `--dry-run`, `--providers`, `--jev-token`.
- **Queue protection** (`router/queue_state.py`): `Stop` hooks for all three tools; a prompt sent
  while earlier work in the same session is unfinished gets a `QUEUE:` instruction, a prompt in a
  folder where another session is working gets a `CONCURRENCY:` warning. Entries expire after
  `ROUTER_QUEUE_TTL_MIN`.
- **OS abstraction** (`router/platforms.py`): junctions on Windows, symlinks on macOS/Linux; config
  locations per OS; executable discovery; login detection.
- **Optional remote-access module** (`router/remote.py`, `docs/remote-access.md`) with a
  user-chosen machine name (default: hostname) and working folder (`--workdir`, must be a folder
  Claude Code trusts – never the home directory), stored in `~/.jev-router/config.json`.
- JEV token in `~/.jev-router/config.json` (environment variable still wins).
- Per-account model availability: `python install.py models --probe` writes
  `~/.jev-router/models.local.json`, which overrides the catalog's defaults.
- `docs/speech-to-text.md` (install, model choice by hardware, phone dictation).
- CI on Windows, macOS and Linux.

### Changed
- License: **MIT** (was proprietary).
- All documentation rewritten in English and made generic; no personal or machine data.
- Skill migration covers every tool's personal skill folder, not only Claude's.
- Hooks call the absolute Python interpreter (unless its path contains spaces).

### Fixed
- Codex `config.toml`: removing the MCP section no longer swallows the comment line that starts the
  generated agents block (which led to duplicate `[agents.*]` tables); the section is replaced in place.

### Removed
- Windows-only `scripts/setup-windows.ps1` and `start-rc.cmd` (replaced by `install.py`).

## [2.3.0] – 2026-09-24

### Added
- Parallel-agent decision (0 – +4) with code-level clamps.
- JEV `model` question over every selectable model; effort clamped to the chosen model.
- Model-named workers: Claude `<model>-worker-<effort>`, Codex roles `<model>-<effort>`.

### Changed
- `ultra` effort banned for every provider.
- Codex catalog verified per model; `codex debug models` shows the CLI's catalog, not what an
  account may use.
- Codex's own `config.toml` tables (hook trust) are kept outside the generated agents block.
- Antigravity `skills.json` lists the repository's `skills/` too (agy does not follow links).

## [2.0.0] – 2026-09-23

### Added
- Global hooks for Claude Code, Codex and Antigravity via shims in `~/.jev-router/bin`.
- Shared skill folder `~/.skills` and a machine-wide skill catalog with a Hungarian → English glossary.
- Stdio MCP server (`route_prompt`, `list_skills`, `get_skill`) for hook-less modes.
- Hungarian / English language detection; answer language follows the prompt.
- 100 + 100 labelled evaluation prompts; pytest suite; health report.

### Fixed
- The global hook read its configuration from the current project instead of the repository.
- Subagents existed only inside the repository.
- Destructive-request false positives ("remove the unused import", "sort in order").
- Math false positive on OAuth codes; invalid Codex effort levels; Antigravity hook firing on every
  model call; harness notifications being routed; secrets in the routing log.

## [1.0.0] – 2026-09-23

- Initial router: Claude Code hook, routing table, subagents, cloud mode, model-family policy,
  local keyword classifier with JEV fallback.

---

## Known limitations

- Hooks cannot switch the running model; model choice is enforced through generated workers.
  Antigravity has no fixed-model agents, so its model choice is advisory.
- Claude desktop *Chat/Cowork* and ChatGPT's plain chat run no hooks. Chat/Cowork is covered by the
  MCP tool (called when the personal-preferences instruction is set); plain ChatGPT chat is not.
- The Claude desktop `/` menu does not list link-based skills; the model still loads them.
- Antigravity `skills.json` paths must be absolute, and links inside a skills entry are not followed.
- On Windows, the Codex remote-control daemon cannot detach when every process runs inside a Job
  Object; the ChatGPT desktop app hosts remote access instead.
- The built-in classifier weighs pasted text like the user's own words; very long pasted English text
  can make it pick English or a higher difficulty for a Hungarian request.
