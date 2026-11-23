"""Run the same test set through every framework and print one comparison table.

    python compare.py                 # all four
    python compare.py langchain haystack
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ALL = ["llamaindex", "langchain", "langgraph", "haystack"]
METRICS = ["answer_correctness", "faithfulness", "answer_relevance", "context_recall"]


def main(names: list[str]) -> int:
    rows = []
    for name in names:
        out = HERE / f"result-{name}.json"
        out.unlink(missing_ok=True)  # never report an earlier run's numbers if this run fails
        print(f"== {name}: ragops test -f {name}.yaml", flush=True)
        proc = subprocess.run([sys.executable, "-m", "ragops.cli", "test", "-f", f"{name}.yaml", "--json-out", str(out),
                               "--out", f"report-{name}.md"], cwd=HERE)
        if not out.exists():
            rows.append((name, None))
            print(f"   {name} did not finish (exit {proc.returncode}); see the error above", flush=True)
            continue
        rows.append((name, json.loads(out.read_text(encoding="utf-8"))["summary"]))
    print("\n| Framework | Pass rate | " + " | ".join(m.replace("_", " ") for m in METRICS) +
          " | Doc hit rate | p50 latency |")
    print("|---" * (len(METRICS) + 4) + "|")
    for name, s in rows:
        if s is None:
            print(f"| {name} | error |" + " – |" * (len(METRICS) + 2))
            continue
        m = s["metrics"]
        cells = [f"{m[k]:.3f}" if m.get(k) is not None else "–" for k in METRICS]
        hit = f"{s['hit_rate']:.0%}" if s.get("hit_rate") is not None else "–"
        print(f"| {name} | {s['pass_rate']:.0%} | " + " | ".join(cells) + f" | {hit} | {s['latency_p50_ms']:.0f} ms |")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or ALL))
