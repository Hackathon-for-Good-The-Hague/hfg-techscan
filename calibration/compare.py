"""Compare AI scan scores against human jury scores from the calibration data."""
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from techscan.util import read_json


def load_calibration(path="calibration/hfg8-jury-scores.json"):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_scan_results(workdir="work-hfg8/results"):
    results = {}
    for p in Path(workdir).glob("*.json"):
        if p.name.startswith("_"):
            continue
        results[p.stem] = read_json(p, {})
    return results


def compare(cal_path="calibration/hfg8-jury-scores.json", results_dir="work-hfg8/results"):
    cal = load_calibration(cal_path)
    results = load_scan_results(results_dir)
    rubric = cal["rubric"]
    criteria = list(rubric.keys())

    print(f"Calibration: {cal['event']} ({len(cal['finalists'])} finalists, {len(cal['jury_members'])} jury members)")
    print(f"Criteria: {', '.join(criteria)}")
    print()

    # Compute jury averages per finalist per criterion
    jury_avg = {}
    for finalist in cal["finalists"]:
        slug = finalist["slug"]
        jury_avg[slug] = {}
        for crit in criteria:
            scores = []
            for jm in cal["jury_members"]:
                s = (jm.get("scores") or {}).get(slug, {}).get(crit)
                if s is not None:
                    scores.append(s)
            jury_avg[slug][crit] = sum(scores) / len(scores) if scores else None

    # Compare with AI scores
    print(f"{'Team':40s}", end="")
    for crit in criteria:
        w = rubric[crit]["weight"]
        print(f"  {crit[:8]:>8s}", end="")
    print(f"  {'weighted':>8s}  {'jury_wt':>8s}  {'delta':>8s}")
    print("-" * 120)

    all_ai_weighted = []
    all_jury_weighted = []
    per_crit_diffs = {c: [] for c in criteria}

    for finalist in cal["finalists"]:
        slug = finalist["slug"]
        r = results.get(slug, {})
        rv = r.get("review") or {}
        ai_scores = rv.get("scores") or {}

        print(f"{finalist['project']:40s}", end="")
        ai_weighted = 0
        jury_weighted = 0
        for crit in criteria:
            ai_val = ai_scores.get(crit)
            jury_val = jury_avg[slug].get(crit)
            w = rubric[crit]["weight"]
            if ai_val is not None:
                ai_weighted += ai_val * w
            if jury_val is not None:
                jury_weighted += jury_val * w
            ai_str = str(ai_val or "-")
            jury_str = f"({jury_val:.1f})" if jury_val else "(-)"
            print(f"  {ai_str:>3s}{jury_str:>5s}", end="")
            if ai_val is not None and jury_val is not None:
                per_crit_diffs[crit].append(ai_val - jury_val)

        delta = ai_weighted - jury_weighted
        print(f"  {ai_weighted:>8.1f}  {jury_weighted:>8.1f}  {delta:>+8.1f}")
        if ai_weighted:
            all_ai_weighted.append(ai_weighted)
        if jury_weighted:
            all_jury_weighted.append(jury_weighted)

    print()
    print("Per-criterion mean absolute error (AI - jury avg):")
    for crit in criteria:
        diffs = per_crit_diffs[crit]
        if diffs:
            mae = sum(abs(d) for d in diffs) / len(diffs)
            bias = sum(diffs) / len(diffs)
            print(f"  {crit:20s}  MAE={mae:.2f}  bias={bias:+.2f}  (n={len(diffs)})")

    if all_ai_weighted and all_jury_weighted and len(all_ai_weighted) >= 3:
        n = len(all_ai_weighted)
        mean_ai = sum(all_ai_weighted) / n
        mean_jury = sum(all_jury_weighted) / n
        cov = sum((a - mean_ai) * (j - mean_jury) for a, j in zip(all_ai_weighted, all_jury_weighted)) / n
        std_ai = math.sqrt(sum((a - mean_ai) ** 2 for a in all_ai_weighted) / n)
        std_jury = math.sqrt(sum((j - mean_jury) ** 2 for j in all_jury_weighted) / n)
        corr = cov / (std_ai * std_jury) if std_ai and std_jury else 0
        print(f"\nWeighted total correlation: r = {corr:.3f} (n={n})")
        print(f"Target: r > 0.6")


if __name__ == "__main__":
    compare(*sys.argv[1:])
