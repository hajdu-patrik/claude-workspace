#!/usr/bin/env python3
"""MCP (stdio) server exposing the router to modes that run no hooks: Claude desktop Chat and
Cowork, and as an extra tool in Codex / Antigravity. Standard library only (newline-delimited
JSON-RPC 2.0 over stdin/stdout, the MCP stdio transport).

Tools:
  route_prompt(prompt, provider="claude-chat")  -> the same decision the hooks inject, plus the
                                                   chosen skill's SKILL.md inline
  list_skills(query="", limit=20)                -> skills from the shared catalog (~/.skills)
  get_skill(name)                                -> one skill's SKILL.md (works cross-tool, and in
                                                   Cowork's VM where local paths are not readable)

Registered by router/install_hooks.py via the shim ~/.jev-router/bin/mcp_server.py. A hook is
automatic; an MCP tool is only called if the model decides to - the Claude "Personal
preferences" line in README.md asks it to call route_prompt first.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import core  # noqa: E402
import skill_index  # noqa: E402

PROTOCOL = "2025-06-18"
MAX_SKILL_CHARS = 20000

TOOLS = [
    {"name": "route_prompt",
     "description": "Call FIRST for every new user request. Returns the router's decision (task type, difficulty, "
                    "recommended model and reasoning effort, safety flag, reply language) and, if a skill fits, that "
                    "skill's instructions. Follow the returned instructions.",
     "inputSchema": {"type": "object", "required": ["prompt"], "properties": {
         "prompt": {"type": "string", "description": "The user's request, verbatim."},
         "provider": {"type": "string", "enum": ["claude-chat", "claude", "codex", "antigravity"],
                      "description": "Which tool is asking (default claude-chat: Claude desktop Chat/Cowork)."}}}},
    {"name": "list_skills",
     "description": "List skills available on this machine (shared hub + every tool's own skills), best matches first.",
     "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}}}},
    {"name": "get_skill",
     "description": "Return the full SKILL.md instructions of one skill by name (as listed by list_skills).",
     "inputSchema": {"type": "object", "required": ["name"], "properties": {"name": {"type": "string"}}}},
]


def read_skill(entry):
    try:
        text = (Path(entry["path"]) / "SKILL.md").read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return f"(could not read {entry['path']}/SKILL.md: {exc})"
    return text[:MAX_SKILL_CHARS] + ("\n...[truncated]" if len(text) > MAX_SKILL_CHARS else "")


def tool_route_prompt(args):
    prompt = str(args.get("prompt", "")).strip()
    provider = args.get("provider") or "claude-chat"
    if not prompt:
        return "Empty prompt - nothing to route.", True
    d, text, hit, error = core.route(prompt, provider)
    out = [text, "", "Decision: " + json.dumps({k: d.get(k) for k in
           ("task", "level", "primary", "effort", "skill", "lang", "backend", "skill_candidates")}, ensure_ascii=False)]
    if error:
        out.append(f"(JEV unavailable, local router used: {error})")
    if d.get("skill"):
        entry = next((s for s in skill_index.load_catalog() if s["name"] == d["skill"]), None)
        if entry:
            out += ["", f"=== Skill `{entry['name']}` (SKILL.md) ===", read_skill(entry)]
    return "\n".join(out), False


def tool_list_skills(args):
    cat = skill_index.load_catalog()
    limit = int(args.get("limit") or 20)
    q = str(args.get("query") or "").strip()
    items = [s for _, s in skill_index.prefilter(q, cat, limit, min_score=0.5)] if q else cat[:limit]
    if not items:
        return "No matching skill.", False
    return "\n".join(f"- {s['name']} [{', '.join(s['native_in'])}]: {s['description'][:200]}" for s in items), False


def tool_get_skill(args):
    name = str(args.get("name", "")).strip()
    entry = next((s for s in skill_index.load_catalog() if s["name"] == name or s["name"].split(":")[-1] == name), None)
    if not entry:
        return f"Unknown skill '{name}'. Use list_skills.", True
    return f"Skill `{entry['name']}` - folder: {entry['path']}\n\n{read_skill(entry)}", False


HANDLERS = {"route_prompt": tool_route_prompt, "list_skills": tool_list_skills, "get_skill": tool_get_skill}


def handle(msg):
    """One JSON-RPC message -> response dict, or None for notifications."""
    method, mid, params = msg.get("method"), msg.get("id"), msg.get("params") or {}
    if mid is None:
        return None  # notification (e.g. notifications/initialized)
    if method == "initialize":
        result = {"protocolVersion": params.get("protocolVersion") or PROTOCOL,
                  "capabilities": {"tools": {"listChanged": False}},
                  "serverInfo": {"name": "jev-router", "version": "1.0.0"},
                  "instructions": "Call route_prompt with the user's request before answering it, then follow its instructions."}
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        fn = HANDLERS.get(params.get("name"))
        if not fn:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": f"unknown tool {params.get('name')}"}}
        try:
            text, is_error = fn(params.get("arguments") or {})
        except Exception as exc:  # noqa: BLE001 - report, never crash the server
            text, is_error = f"{type(exc).__name__}: {exc}", True
        result = {"content": [{"type": "text", "text": text}], "isError": is_error}
    else:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"method not found: {method}"}}
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def main():
    stdin = open(sys.stdin.fileno(), "r", encoding="utf-8", errors="replace", newline="\n", closefd=False)
    stdout = open(sys.stdout.fileno(), "w", encoding="utf-8", newline="\n", closefd=False)
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            resp = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
        else:
            batch = msg if isinstance(msg, list) else [msg]
            resp = [r for r in (handle(m) for m in batch if isinstance(m, dict)) if r]
            resp = (resp if isinstance(msg, list) else (resp[0] if resp else None))
        if resp:
            stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            stdout.flush()


if __name__ == "__main__":
    main()
