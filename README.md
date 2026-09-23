# claude-workspace – Jev-router

Claude Code munkakörnyezet: minden prompt előtt egy `UserPromptSubmit` hook (`.claude/hooks/router_hook.py`)
besorolja a promptot, és a `routes.json` alapján delegálási utasítást ad
(fable / sonnet / opus subagent, helyben Codex / Gemini CLI). Ugyanez fut a gépen,
Remote Controlon (telefon → gép) és Claude Code on the weben (felhő, gép kikapcsolva).

## Router backendek

| Backend | Mikor | Megjegyzés |
|---|---|---|
| `jev` | van `TYPESAFE_API_KEY` | TypeSafe Jev; hiba/timeout esetén automatikusan `local` |
| `local` | nincs kulcs (most) | kulcsszavas magyar/angol osztályozó, hálózat nélkül, <1 ms |

Kényszerítés: `ROUTER_BACKEND=local` vagy `ROUTER_BACKEND=jev`. A logsor `backend` mezője mutatja, melyik döntött.
Mérés: `python eval/eval_router.py` (65 címkézett magyar prompt).

## Fázisok

| Fázis | Tartalom | Állapot |
|---|---|---|
| 1 | Repó + router + subagentek; telefon → gép (Remote Control); felhős út (claude.ai/code) | most |
| 2 | Magyar diktálás (Gboard / iOS / Win+H) | 5 perc, csak beállítás |
| 3 | Codex + Gemini CLI (`cli-bridge` skill), csak helyben | később |
| 4 | Mérés: `eval/eval_router.py`, küszöbök és `routes.json` hangolása | helyi backenddel már most; Jev-kulcs után újra |
| 5 | Jev bekapcsolása: kulcs helyben (`setup-windows.ps1` újrafuttatása) és a felhős környezetben | ha lesz hozzáférés |

## 1. fázis – gyors indítás (Windows)

```powershell
# a repó gyökerében (pl. C:\dev\claude-workspace)
powershell -ExecutionPolicy Bypass -File scripts\setup-windows.ps1 -KeepAwake -AutoStart
claude                                   # workspace trust + /login (claude.ai fiók, NEM API-kulcs)
claude remote-control --name "Otthoni gep"
```

Telefon: Claude app → Code → a session zöld ponttal → prompt.

## Felhős környezet (claude.ai/code, egyszer)

- Repó: ez a repó (privát GitHub).
- Környezet neve: `router`; Network access: Custom, allowed domain: `api.typesafe.ai`
  (+ default package manager lista).
- Environment variables:
  ```
  ROUTER_MODE=cloud
  # később, Jev-hozzáféréssel:
  # TYPESAFE_API_KEY=<külön, felhős kulcs>
  ```

## Felülbírálás a promptban

`#fable` `#sonnet` `#opus` `#codex` `#gemini` – kényszerített cél.
`#norouter` / `#privat` – nincs routing, a prompt nem megy a TypeSafe-hez.

## Modellcsalád-szabály

A Claude-oldali routing mindig a `.claude/router/models.json`-ban rögzített
szabályt követi: csak `sonnet` / `opus` / `fable` választható, mindig a család
generikus aliasával (nem rögzített, dátumozott modell-ID-vel) – így automatikusan
a legfrissebb verziót kapja (pl. Opusnál most 5.5, nem 5.0 vagy 4.8). A Haiku
családot a routing sosem választja. Ugyanezt az elvet vezetjük be Codexnél és
Gemininél is a 3. fázisban, amint a `cli-bridge` bővül.

## Hibakeresés

Minden döntés/hiba: `logs/routing.jsonl` (nincs verziókezelve). `error` mező = a Jev-hívás bukott,
a munka `main` fallbackkel ment tovább. Nincs új sor = a hook nem futott (`/hooks`, `claude --debug`).

## Kulcsok

A `TYPESAFE_API_KEY` soha nem kerül a repóba – helyben felhasználói környezeti változó,
felhőben a környezet beállításai.
