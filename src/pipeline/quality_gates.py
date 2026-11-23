"""Data quality gates — fail the pipeline if today's snapshot is implausible.

Gates (tuned in config/pipeline.yaml -> quality_gates):
1. row count below a share of the 30-day median
2. any null/empty username
3. score distribution shifted more than K sigma from the rolling baseline
4. bot-classification rate jumped more than X times the baseline

Hard invariants always apply: scores stay inside [0, 1000] and never lose all variance.
A rolling baseline of the last 30 successful runs lives in data/gate_history.json.
"""
import json, os, sys, statistics
from datetime import datetime, timezone

try:
    import yaml
except ImportError:  # pragma: no cover - config falls back to defaults
    yaml = None

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CONFIG_PATH = os.path.join(ROOT, "config", "pipeline.yaml")

DEFAULT_GATES = {
    "row_count_min_pct_of_30d_median": 80,
    "username_null_max_pct": 0,
    "score_shift_sigma_max": 3,
    "bot_classification_jump_max_x": 2,
    "min_baseline_days": 5,
    "bot_rate_floor": 0.005,
    "min_rows_absolute": 10,
}
HISTORY_LIMIT = 30


def load_gate_config(config_path=CONFIG_PATH):
    gates = dict(DEFAULT_GATES)
    if yaml is None or not os.path.exists(config_path):
        return gates
    with open(config_path, encoding="utf-8") as f:
        doc = yaml.safe_load(f) or {}
    for key, value in (doc.get("quality_gates") or {}).items():
        if value is not None:
            gates[key] = value
    return gates


def summarize(leaderboard, score_version=None):
    scores = [float(u.get("rank_score") or 0) for u in leaderboard]
    rows = len(leaderboard)
    bots = sum(1 for u in leaderboard if u.get("is_bot_classified"))
    summary = {
        "rows": rows,
        "null_usernames": sum(1 for u in leaderboard if not u.get("username")),
        "score_min": min(scores) if scores else 0.0,
        "score_max": max(scores) if scores else 0.0,
        "score_mean": round(statistics.fmean(scores), 3) if scores else 0.0,
        "score_std": round(statistics.pstdev(scores), 3) if len(scores) > 1 else 0.0,
        "bot_rate": round(bots / rows, 5) if rows else 0.0,
    }
    if score_version:
        summary["score_version"] = score_version
    return summary


def evaluate(today, history, gates=None):
    """Return (errors, notes) for today's summary against the rolling baseline."""
    gates = dict(DEFAULT_GATES) if gates is None else gates
    errors, notes = [], []

    if today["rows"] < gates["min_rows_absolute"]:
        errors.append(f"row count too low: {today['rows']}")

    null_pct = (today["null_usernames"] / today["rows"] * 100.0) if today["rows"] else 0.0
    if null_pct > float(gates["username_null_max_pct"]):
        errors.append(
            f"null usernames: {today['null_usernames']} "
            f"({null_pct:.2f}% > {gates['username_null_max_pct']}% allowed)"
        )
    if today["score_max"] > 1000 or today["score_min"] < 0:
        errors.append("score out of range")
    if today["score_std"] == 0:
        errors.append("zero variance in scores")

    baseline = [h for h in history[-HISTORY_LIMIT:] if isinstance(h, dict) and "rows" in h]
    min_days = gates["min_baseline_days"]
    if len(baseline) < min_days:
        notes.append(f"baseline {len(baseline)}/{min_days} runs - growth/shift/jump gates skipped")
        return errors, notes

    median_rows = statistics.median(h["rows"] for h in baseline)
    min_rows = median_rows * gates["row_count_min_pct_of_30d_median"] / 100.0
    if today["rows"] < min_rows:
        errors.append(
            f"row count {today['rows']} < {min_rows:.0f} "
            f"({gates['row_count_min_pct_of_30d_median']}% of 30d median {median_rows:.0f})"
        )

    ref_mean = statistics.median(h.get("score_mean", 0.0) for h in baseline)
    ref_sigma = statistics.median(h.get("score_std", 0.0) for h in baseline)
    shift = abs(today["score_mean"] - ref_mean)
    if ref_sigma > 0 and shift > gates["score_shift_sigma_max"] * ref_sigma:
        errors.append(
            f"score mean shifted {shift:.1f} > {gates['score_shift_sigma_max']}σ "
            f"(σ={ref_sigma:.1f}, baseline mean={ref_mean:.1f}, today={today['score_mean']:.1f})"
        )

    ref_bot = statistics.median(h.get("bot_rate", 0.0) for h in baseline)
    if ref_bot >= gates["bot_rate_floor"] and today["bot_rate"] > ref_bot * gates["bot_classification_jump_max_x"]:
        errors.append(
            f"bot classification rate {today['bot_rate']:.3%} > "
            f"{gates['bot_classification_jump_max_x']}× baseline {ref_bot:.3%}"
        )

    return errors, notes


def append_history(history, today, run_date):
    """Record today's summary, replacing any earlier entry for the same date."""
    entry = dict(today)
    entry["date"] = run_date
    kept = [h for h in history if isinstance(h, dict) and h.get("date") != run_date]
    kept.append(entry)
    return kept[-HISTORY_LIMIT:]


def _run_date(status_path):
    if os.path.exists(status_path):
        try:
            with open(status_path, encoding="utf-8") as f:
                stamp = json.load(f).get("last_run_utc")
            if stamp:
                return str(stamp)[:10]
        except Exception:
            pass
    return datetime.now(timezone.utc).date().isoformat()


SIZE_LIMITS_MB = {"leaderboard": 80, "rolling": 250}


def size_errors():
    """Keep the git-backed data and static site inside free hosting limits."""
    errors = []
    lb_path = os.path.join("data", "leaderboard.json")
    if os.path.exists(lb_path):
        size_mb = os.path.getsize(lb_path) / (1024 * 1024)
        if size_mb > SIZE_LIMITS_MB["leaderboard"]:
            errors.append(f"leaderboard.json {size_mb:.1f} MB > {SIZE_LIMITS_MB['leaderboard']} MB")
    rolling_dir = os.path.join("data", "rolling")
    if os.path.isdir(rolling_dir):
        total = sum(
            os.path.getsize(os.path.join(rolling_dir, name))
            for name in os.listdir(rolling_dir)
            if name.endswith(".json.gz")
        )
        size_mb = total / (1024 * 1024)
        if size_mb > SIZE_LIMITS_MB["rolling"]:
            errors.append(f"rolling shards {size_mb:.1f} MB > {SIZE_LIMITS_MB['rolling']} MB")
    return errors


def main():
    lb_path = os.path.join("data", "leaderboard.json")
    hist_path = os.path.join("data", "gate_history.json")
    status_path = os.path.join("data", "status.json")

    if not os.path.exists(lb_path):
        print("GATE FAIL: missing data/leaderboard.json")
        return 1

    with open(lb_path, encoding="utf-8") as f:
        leaderboard = json.load(f)
    # Signals live in a gitignored sidecar, not in the committed leaderboard.
    try:
        from automation import apply_signals, load_signals

        leaderboard = apply_signals(leaderboard, load_signals())
    except ImportError:
        pass
    history = []
    if os.path.exists(hist_path):
        try:
            with open(hist_path, encoding="utf-8") as f:
                history = json.load(f)
        except Exception as e:
            print(f"note: ignoring unreadable gate history ({e})")
            history = []

    score_version = None
    if os.path.exists(status_path):
        try:
            with open(status_path, encoding="utf-8") as f:
                score_version = json.load(f).get("score_version")
        except Exception:
            score_version = None
    today = summarize(leaderboard, score_version)
    if score_version:
        # A new score scale must not be compared with the old scale's baseline.
        history = [h for h in history if not isinstance(h, dict) or h.get("score_version") == score_version]
    gates = load_gate_config()
    errors, notes = evaluate(today, history, gates)
    errors.extend(size_errors())

    for note in notes:
        print("note:", note)
    if errors:
        for err in errors:
            print("GATE FAIL:", err)
        return 1

    with open(hist_path, "w", encoding="utf-8") as f:
        json.dump(append_history(history, today, _run_date(status_path)), f, indent=2)
    print(
        f"Quality gates: PASS {today['rows']} users "
        f"(mean {today['score_mean']:.1f}, bot rate {today['bot_rate']:.2%})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
