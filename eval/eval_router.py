#!/usr/bin/env python3
"""Router quality measurement on the Hungarian and English test sets, through the FULL pipeline
(core.route: classify -> decide -> effort -> skill -> render), exactly as the hooks run it.

Usage:  python eval/eval_router.py                      # both eval/hu_prompts.csv and eval/en_prompts.csv
        python eval/eval_router.py eval/en_prompts.csv  # one file
Backend: ROUTER_BACKEND=local|jev (default auto: JEV if TYPESAFE_API_KEY is set, else the local mock).
CSV columns: id,prompt,task,difficulty,destructive   (difficulty 0/1/2, destructive 0/1)
Targets: task accuracy >= 85% per language, destructive recall 100%, destructive false-positive rate < 5%,
reply-language detection 100%. Exit code 1 if any target is missed (usable in CI).
Details: eval/results_<name>.csv (git-ignored).
"""
import csv
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "router"))
import core  # noqa: E402

TARGETS = {"task": 0.85, "destr_recall": 1.0, "destr_fp": 0.05, "lang": 1.0}


def evaluate(path):
    lang_expected = "en" if "en_" in Path(path).name else "hu"
    with open(path, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    out = []
    for r in rows:
        d, text, hit, err = core.route(r["prompt"], "claude")
        out.append({"id": r["id"], "prompt": r["prompt"], "task_true": r["task"], "task_pred": d["task"],
                    "task_conf": d["task_conf"], "diff_true": int(r["difficulty"]), "diff_pred": d["level"],
                    "destr_true": int(r["destructive"]), "destr_pred": int(hit), "lang": d.get("lang"),
                    "tier": d["primary"] + (f"+{d['verify']}" if d.get("verify") else ""), "effort": d.get("effort"),
                    "skill": d.get("skill") or "", "backend": d["backend"], "error": err or ""})
    n = len(out)
    name = Path(path).stem
    print(f"\n=== {name}  ({n} prompts, backend: {out[0]['backend'] if out else '-'})")
    task_acc = sum(o["task_true"] == o["task_pred"] for o in out) / n
    print(f"Task type accuracy:   {task_acc:.0%}")
    for (t, p), c in Counter((o["task_true"], o["task_pred"]) for o in out if o["task_true"] != o["task_pred"]).most_common(6):
        print(f"    {t} -> {p}: {c}x")
    exact = sum(o["diff_true"] == o["diff_pred"] for o in out) / n
    near = sum(abs(o["diff_true"] - o["diff_pred"]) <= 1 for o in out) / n
    print(f"Difficulty:           exact {exact:.0%}, within +-1 {near:.0%}")
    pos = [o for o in out if o["destr_true"]]
    neg = [o for o in out if not o["destr_true"]]
    recall = sum(o["destr_pred"] for o in pos) / len(pos) if pos else 1.0
    fp = sum(o["destr_pred"] for o in neg) / len(neg) if neg else 0.0
    print(f"Destructive:          recall {recall:.0%} ({len(pos)} positives), false positives {fp:.0%}")
    for o in pos:
        if not o["destr_pred"]:
            print(f"    MISSED: {o['prompt']}")
    for o in neg:
        if o["destr_pred"]:
            print(f"    FALSE+: {o['prompt']}")
    lang_acc = sum(o["lang"] == lang_expected for o in out) / n
    print(f"Reply language ({lang_expected}):  {lang_acc:.0%}")
    for o in out:
        if o["lang"] != lang_expected:
            print(f"    LANG: {o['prompt']}")
    print("Tiers:                " + ", ".join(f"{k} {v}" for k, v in Counter(o["tier"] for o in out).most_common()))
    print("Efforts:              " + ", ".join(f"{k} {v}" for k, v in Counter(o["effort"] for o in out).most_common()))
    skills = Counter(o["skill"] for o in out if o["skill"])
    print("Skills picked:        " + (", ".join(f"{k} {v}" for k, v in skills.most_common()) or "none"))
    with open(ROOT / "eval" / f"results_{name}.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(out[0]))
        w.writeheader()
        w.writerows(out)
    ok = (task_acc >= TARGETS["task"] and recall >= TARGETS["destr_recall"] and fp < TARGETS["destr_fp"]
          and lang_acc >= TARGETS["lang"])
    print("RESULT:               " + ("PASS" if ok else "FAIL") + f"  (targets: task>={TARGETS['task']:.0%}, recall 100%, FP<5%, lang 100%)")
    return ok


def main(paths):
    results = [evaluate(p) for p in paths]
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or [str(ROOT / "eval" / "hu_prompts.csv"), str(ROOT / "eval" / "en_prompts.csv")]))
