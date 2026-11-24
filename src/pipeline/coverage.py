"""Publish what the pipeline can and cannot measure.

Writes ``data/coverage.json`` next to the leaderboard so the site, the API and
llms.txt can state real coverage instead of implying every pillar is observed.
"""
import json
import os

COVERAGE_PATH = "data/coverage.json"

PILLARS = (
    ("delivery", "Merged pull requests (external merges weighted)"),
    ("code_health", "Test-signal ratio and multi-file PRs"),
    ("adoption", "Stars and forks received"),
    ("consistency", "Active shipping weeks and push-hour spread"),
)


def _pct(part, total):
    return round(part / total * 100.0, 1) if total else 0.0


def build_coverage(entries, meta=None, validation=None):
    meta = meta or {}
    total = len(entries)
    measured = {"delivery": 0, "code_health": 0, "adoption": 0, "consistency": 0}
    automation_evidence = 0
    country = 0
    language = 0
    for e in entries:
        flags = e.get("measured") or {}
        for key in measured:
            if flags.get(key):
                measured[key] += 1
        if e.get("bot_evidence"):
            automation_evidence += 1
        if e.get("country"):
            country += 1
        if e.get("primary_language") not in (None, "", "Unknown"):
            language += 1

    return {
        "generated_at": meta.get("generated_at"),
        "score_version": meta.get("score_version", "v2-percentile"),
        "window_days": meta.get("window_days"),
        "window_start": meta.get("window_start"),
        "window_end": meta.get("window_end"),
        "users": total,
        "users_total": int(meta.get("users_total") or total),
        "source": meta.get("source"),
        "pillars": {
            key: {"label": label, "measured": measured[key], "pct": _pct(measured[key], total)}
            for key, label in PILLARS
        },
        "automation_evidence": {"measured": automation_evidence, "pct": _pct(automation_evidence, total)},
        "country": {"known": country, "pct": _pct(country, total)},
        "language": {"known": language, "pct": _pct(language, total)},
        "validation": validation,
        "notes": (
            "`source` describes how much of the public GitHub event stream this window "
            "actually contains (hourly files downloaded, events seen, bot events skipped). "
            "gharchive.org is a partial mirror of public events, so the ranking covers the "
            "activity it observed, not every commit on GitHub. Automation signals "
            "(commit-message entropy and PR changed-file counts) are only known when the "
            "feed carries them; unknown signals are disclosed here and never turned into "
            "a guessed adjustment. Missing pillars are excluded from the weighted score "
            "and the remaining weights are renormalised."
        ),
    }


def write_coverage(entries, meta=None, validation=None, path=COVERAGE_PATH):
    payload = build_coverage(entries, meta, validation)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    return payload


def main():
    """Recompute coverage after enrichment touched the leaderboard."""
    def load(path, default):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return default

    try:
        from .automation import apply_signals, load_signals
    except ImportError:  # run as a plain script: src/pipeline is on sys.path
        from automation import apply_signals, load_signals

    entries = apply_signals(load("data/leaderboard.json", []), load_signals())
    status = load("data/status.json", {})
    validation = load("data/validation.json", None)
    payload = write_coverage(entries, {
        "generated_at": status.get("last_run_utc"),
        "score_version": status.get("score_version"),
        "window_days": status.get("window_days"),
        "window_start": status.get("window_start"),
        "window_end": status.get("window_end"),
        "users_total": status.get("users"),
        "source": status.get("ingest"),
    }, validation)
    print(
        f"Coverage: {payload['users']} users, quality "
        f"{payload['pillars']['code_health']['pct']}%, automation evidence "
        f"{payload['automation_evidence']['pct']}%"
    )
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
