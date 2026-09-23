#!/usr/bin/env python3
"""A Jev-router merese magyar tesztkeszleten. Hasznalat: python eval/eval_router.py eval/hu_prompts.csv
CSV oszlopok: id,prompt,task,difficulty,destructive  (difficulty: 0/1/2, destructive: 0/1)
Kimenet: pontossag, confidence-kuszob tabla, javasolt ROUTER_MIN_CONFIDENCE, es eval/results.csv."""
import csv
import importlib.util
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("router", ROOT / ".claude" / "hooks" / "router_hook.py")
router = importlib.util.module_from_spec(spec)
spec.loader.exec_module(router)

TARGET_ACC = 0.90  # ennyi pontossag kell a kuszob feletti dontesekre


def main(path):
    with open(path, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    questions = router.build_questions(router.load_json("skills.json", {}))
    out = []
    for r in rows:
        try:
            a = router.ask_jev(r["prompt"], questions)["answers"]
        except Exception as exc:  # egy hibas sor ne allitsa le a merest
            print(f"  HIBA a(z) {r['id']}. sornal: {type(exc).__name__}: {exc}")
            continue
        out.append({
            "id": r["id"], "prompt": r["prompt"],
            "task_true": r["task"], "task_pred": a["task"]["choice"], "task_conf": float(a["task"]["confidence"]),
            "diff_true": int(r["difficulty"]), "diff_pred": int(round(float(a["difficulty"]["score"]))),
            "destr_true": int(r["destructive"]), "destr_p": float(a["destructive"]["noul"]),
        })
    n = len(out)
    if not n:
        sys.exit("Egyetlen sikeres Jev-hivas sem volt; ellenorizd a kulcsot es a halozatot.")
    ok = [o["task_true"] == o["task_pred"] for o in out]
    print(f"Minta: {n}\nFeladattipus pontossag: {sum(ok) / n:.0%}")
    errs = Counter((o["task_true"], o["task_pred"]) for o in out if o["task_true"] != o["task_pred"])
    for (t, p), c in errs.most_common(5):
        print(f"  teves: {t} -> {p}: {c}x")

    print("\nKuszob | lefedettseg | pontossag a kuszob felett")
    best = None
    for t in (0.4, 0.5, 0.6, 0.7, 0.8, 0.9):
        cov = [o for o in out if o["task_conf"] >= t]
        acc = sum(o["task_true"] == o["task_pred"] for o in cov) / len(cov) if cov else 0.0
        print(f"  {t:.1f}  | {len(cov) / n:>6.0%}      | {acc:.0%}")
        if best is None and cov and acc >= TARGET_ACC:
            best = t
    print(f"Javasolt ROUTER_MIN_CONFIDENCE: {best if best is not None else 'nincs ilyen kuszob, bovitsd a kriteriumokat'}")

    exact = sum(o["diff_true"] == o["diff_pred"] for o in out) / n
    near = sum(abs(o["diff_true"] - o["diff_pred"]) <= 1 for o in out) / n
    print(f"\nNehezseg: pontos {exact:.0%}, +-1 szinten belul {near:.0%}")

    pos = [o for o in out if o["destr_true"]]
    flagged = [o for o in out if o["destr_p"] >= router.DESTRUCTIVE_T or router.DESTRUCTIVE_RE.search(o["prompt"])]
    if pos:
        recall = sum(1 for o in pos if o in flagged) / len(pos)
        print(f"Destruktiv (Jev + regex): felismeres {recall:.0%} ({len(pos)} pozitivbol), riasztas osszesen {len(flagged)}")
        if recall < 1:
            print("  FIGYELEM: kimaradt destruktiv prompt. Csokkentsd a kuszobot vagy bovitsd a regexet.")

    with open(ROOT / "eval" / "results.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(out[0]))
        w.writeheader()
        w.writerows(out)
    print("\nReszletek: eval/results.csv")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else str(ROOT / "eval" / "hu_prompts.csv"))
