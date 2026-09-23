---
name: cli-bridge
description: Run a task or get an independent second opinion from the Codex (ChatGPT) or Gemini (Google AI Pro) CLI. Use when the [router] context mentions cli-bridge, or the user writes #codex or #gemini.
---

# cli-bridge

1. A másik modell nem látja ezt a beszélgetést. Írj teljes, önálló promptot a
   `logs/cli_prompt.txt` fájlba: feladat, releváns fájlnevek és kódrészletek,
   elvárt kimeneti forma, és hogy magyarul válaszoljon. Titkot (kulcs, jelszó) ne írj bele.
2. Futtatás a repó gyökeréből:
   `python3 .claude/skills/cli-bridge/ask_cli.py codex < logs/cli_prompt.txt`
   (Windowson `python`; Gemini esetén `gemini` az első argumentum.)
3. Ellenőrzés (verify) esetén vesd össze a saját eredményeddel. Sorold fel az
   eltéréseket, és indokold, melyik a helyes. Ne írd felül vakon a sajátodat.
4. Kilépési kód 2 vagy 3 (nincs telepítve, felhő, időtúllépés): jelezd egy sorban,
   és folytasd a `deep-worker` subagenttel.
5. A Codexet ne indítsd `--sandbox` kapcsolóval: véleményt kérünk tőle, nem fájlírást.
