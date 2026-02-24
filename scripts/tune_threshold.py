"""
Confidence threshold tuning script.

Runs the benchmark with different threshold values and prints a comparison table.
Usage:
    source cactus/venv/bin/activate
    python scripts/tune_threshold.py
"""

import os
os.environ["CACTUS_NO_CLOUD_TELE"] = "1"

import sys
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_project_root, os.path.join(_project_root, "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import main
from benchmark import BENCHMARKS, compute_f1, compute_total_score

THRESHOLDS = [0.99, 0.85, 0.70, 0.50, 0.30, 0.10, 0.0]


def run_with_threshold(threshold):
    results = []
    for case in BENCHMARKS:
        result = main.generate_hybrid(case["messages"], case["tools"], confidence_threshold=threshold)
        f1 = compute_f1(result["function_calls"], case["expected_calls"])
        results.append({
            "name": case["name"],
            "difficulty": case["difficulty"],
            "total_time_ms": result["total_time_ms"],
            "f1": f1,
            "source": result.get("source", "unknown"),
        })
    return results


def main_tune():
    print(f"\n{'Threshold':>10} | {'Score':>6} | {'F1 easy':>8} | {'F1 med':>7} | {'F1 hard':>8} | {'On-dev%':>7} | {'Avg ms':>7}")
    print("-" * 75)

    best_score = -1
    best_threshold = None

    for t in THRESHOLDS:
        results = run_with_threshold(t)
        score = compute_total_score(results)

        def avg_f1(diff):
            g = [r["f1"] for r in results if r["difficulty"] == diff]
            return sum(g) / len(g) if g else 0

        on_device = sum(1 for r in results if r["source"] == "on-device")
        on_device_pct = 100 * on_device / len(results)
        avg_time = sum(r["total_time_ms"] for r in results) / len(results)

        print(f"{t:>10.2f} | {score:>5.1f}% | {avg_f1('easy'):>8.3f} | {avg_f1('medium'):>7.3f} | {avg_f1('hard'):>8.3f} | {on_device_pct:>6.0f}% | {avg_time:>6.0f}ms")

        if score > best_score:
            best_score = score
            best_threshold = t

    print(f"\n✅ Best threshold: {best_threshold} → score {best_score:.1f}%")
    print(f"   Update confidence_threshold default in generate_hybrid() to: {best_threshold}")


if __name__ == "__main__":
    main_tune()
