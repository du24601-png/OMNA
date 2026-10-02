"""v2 split quality gate on the synthetic instruction files.

Runs the no-model path (split_markdown + dedupe) on every file listed in
fixtures/agent_files/expected.json and scores it against the hand-written
expectations. Gate: category accuracy >= 80% and manual work <= 20%.
Manual work = missing items + wrong category + extra candidates + duplicates
left unmerged. Writes tests/results/split_gate.json, or split_gate_holdout.json
with the argument `holdout` (fixtures/agent_files/holdout). Exit code 1 if the gate
fails; the numbers are reported either way.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOLDOUT = len(sys.argv) > 1 and sys.argv[1] == "holdout"
FIXTURES = ROOT / "fixtures" / "agent_files" / ("holdout" if HOLDOUT else "")
RESULT = ROOT / "tests" / "results" / ("split_gate_holdout.json" if HOLDOUT else "split_gate.json")
sys.path.insert(0, str(ROOT / "server"))

from zhiwo.services.split import dedupe, normalize, split_markdown  # noqa: E402

ACCURACY_GATE = 0.80
MANUAL_GATE = 0.20


def _matches(expected: str, content: str) -> bool:
    left, right = normalize(expected), normalize(content)
    return bool(left and right) and (left in right or right in left)


def score_file(name: str, spec: dict) -> dict:
    text = (FIXTURES / name).read_text(encoding="utf-8")
    raw = split_markdown(text)
    kept, merged = dedupe(raw, [])
    used: set[int] = set()
    rows = []
    for category, expected in spec["items"]:
        index = next((i for i, item in enumerate(kept) if i not in used and _matches(expected, item["content"])), None)
        if index is None:
            rows.append({"expected": expected, "want": category, "got": None, "result": "missing"})
            continue
        used.add(index)
        got = kept[index]["category"]
        rows.append({"expected": expected, "want": category, "got": got, "content": kept[index]["content"], "result": "ok" if got == category else "wrong_category"})
    extra = [{"content": item["content"], "category": item["category"]} for i, item in enumerate(kept) if i not in used]
    unmerged = max(0, spec["duplicates"] - merged)
    verbatim = sum(1 for item in raw if item["evidence"] in text)
    return {
        "file": name,
        "items": len(spec["items"]),
        "candidates": len(kept),
        "correct": sum(1 for row in rows if row["result"] == "ok"),
        "wrong_category": sum(1 for row in rows if row["result"] == "wrong_category"),
        "missing": sum(1 for row in rows if row["result"] == "missing"),
        "extra": len(extra),
        "expected_duplicates": spec["duplicates"],
        "merged": merged,
        "unmerged_duplicates": unmerged,
        "evidence_verbatim": f"{verbatim}/{len(raw)}",
        "rows": rows,
        "extra_candidates": extra,
    }


def main() -> int:
    spec = json.loads((FIXTURES / "expected.json").read_text(encoding="utf-8"))["files"]
    files = [score_file(name, entry) for name, entry in spec.items()]
    items = sum(item["items"] for item in files)
    correct = sum(item["correct"] for item in files)
    manual = sum(item["missing"] + item["wrong_category"] + item["extra"] + item["unmerged_duplicates"] for item in files)
    accuracy = correct / items
    manual_ratio = manual / items
    passed = accuracy >= ACCURACY_GATE and manual_ratio <= MANUAL_GATE
    summary = {
        "gate": "v2 split quality",
        "set": "holdout" if HOLDOUT else "tuning",
        "method": "split_markdown + dedupe, no model",
        "files": len(files),
        "items": items,
        "category_accuracy": round(accuracy, 4),
        "manual_work_ratio": round(manual_ratio, 4),
        "thresholds": {"category_accuracy": ACCURACY_GATE, "manual_work_ratio": MANUAL_GATE},
        "pass": passed,
        "per_file": files,
    }
    RESULT.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for item in files:
        print(f"{item['file']:<28} items {item['items']:>2}  ok {item['correct']:>2}  wrong {item['wrong_category']}  missing {item['missing']}  extra {item['extra']}  merged {item['merged']}/{item['expected_duplicates']}  evidence {item['evidence_verbatim']}")
    print(f"category accuracy {accuracy:.1%} (gate >= {ACCURACY_GATE:.0%}); manual work {manual}/{items} = {manual_ratio:.1%} (gate <= {MANUAL_GATE:.0%}); {'PASS' if passed else 'FAIL'}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
