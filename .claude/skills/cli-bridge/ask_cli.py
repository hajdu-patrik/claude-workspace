#!/usr/bin/env python3
"""Codex vagy Gemini CLI futtatasa headless modban. A prompt STDIN-en megy at, nem argumentumkent:
igy nincs parancssori injection-kockazat (Windows .cmd shimeknel sem), es nincs hossz-limit.

Hasznalat:  python ask_cli.py codex  < prompt.txt
            python ask_cli.py gemini < prompt.txt
Kilepesi kodok: 0 siker, 2 ismeretlen eszkoz vagy nincs telepitve, 3 idotullepes, egyeb: az eszkoz sajat hibakodja.
"""
import os
import shutil
import subprocess
import sys

TOOLS = {
    # codex exec -: a promptot stdin-rol olvassa; alapbol read-only sandboxban fut (csak velemeny, nem ir fajlt).
    "codex": ["codex", "exec", "-"],
    # gemini: nem-TTY stdin mellett headless modban fut es a stdin a prompt.
    "gemini": ["gemini"],
}
TIMEOUT_S = int(os.environ.get("CLI_BRIDGE_TIMEOUT", "600"))


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in TOOLS:
        print(f"Hasznalat: ask_cli.py {{{'|'.join(TOOLS)}}} < prompt", file=sys.stderr)
        return 2
    if os.environ.get("ROUTER_MODE", "").lower() == "cloud":
        print("cli-bridge: felhoben nem erheto el (nincs Codex/Gemini bejelentkezes).", file=sys.stderr)
        return 2
    cmd = TOOLS[sys.argv[1]]
    exe = shutil.which(cmd[0])
    if not exe:
        print(f"cli-bridge: a '{cmd[0]}' parancs nincs a PATH-on.", file=sys.stderr)
        return 2
    prompt = sys.stdin.buffer.read().decode("utf-8-sig", errors="replace").strip()
    if not prompt:
        print("cli-bridge: ures prompt.", file=sys.stderr)
        return 2
    try:
        r = subprocess.run([exe] + cmd[1:], input=prompt.encode("utf-8"), capture_output=True, timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired:
        print(f"cli-bridge: {cmd[0]} idotullepes ({TIMEOUT_S} s).", file=sys.stderr)
        return 3
    sys.stdout.buffer.write(r.stdout)
    if r.returncode:
        sys.stderr.buffer.write(r.stderr[-4000:])
    return r.returncode


if __name__ == "__main__":
    sys.exit(main())
