"""Publish what the pipeline can and cannot measure.

Writes ``data/coverage.json`` next to the leaderboard so the site, the API and
llms.txt can state real coverage instead of implying every signal is observed.
"""
import json
import os

COVERAGE_PATH = "data/coverage.json"

# Public-facing activity components (score v3). Each row states how many ranked
# developers actually have that component above zero in the 12-month window.
SIGNALS = (
    ("total_pushes", "Pushes"),
    ("merged_prs", "Merged pull requests"),
    ("closed_issues", "Closed issues"),
    ("pr_reviews", "Pull request reviews"),
    ("issue_comments", "Issue comments"),
    ("releases", "Releases"),
)


def _pct(part, total):
    return round(part / total * 100.0, 1) if total else 0.0


def build_coverage(entries, meta=None, validation=None):
    meta = meta or {}
    total = len(entries)
    observed = {key: 0 for key, _ in SIGNALS}
    country = 0
    language = 0
    for entry in entries:
        for key in observed:
            try:
                if int(entry.get(key) or 0) > 0:
                    observed[key] += 1
            except (TypeError, ValueError):
                continue
        if entry.get("country"):
            country += 1
        if entry.get("primary_language") not in (None, "", "Unknown"):
            language += 1

    users_total = int(meta.get("users_total") or total)
    users_ranked = int(meta.get("users_ranked") or users_total)

    return {
        "generated_at": meta.get("generated_at"),
        "score_version": meta.get("score_version", "v3-activity"),
        "window_days": meta.get("window_days"),
        "window_months": meta.get("window_months"),
        "window_start": meta.get("window_start"),
        "window_end": meta.get("window_end"),
        "users": total,
        "users_detailed": total,
        "users_total": users_total,
        "users_ranked": users_ranked,
        "source": meta.get("source"),
        "signals": {
            key: {"label": label, "observed": observed[key], "pct": _pct(observed[key], total)}
            for key, label in SIGNALS
        },
        "country": {"known": country, "pct": _pct(country, total)},
        "language": {"known": language, "pct": _pct(language, total)},
        "validation": validation,
        "notes": (
            "`source` describes how much of the public GitHub event stream the newest "
            "daily shards actually contain (hourly files downloaded, events seen, bot "
            "events skipped). The score itself is one absolute number: public pushes, "
            "merged pull requests, closed issues, pull-request reviews, issue comments "
            "and releases added up over the rolling 12-month window. `signals` states "
            "how many ranked developers have each component above zero. Nothing is "
            "percentile-ranked, and no signal is inferred when the feed does not carry "
            "it. GitHub's own contribution count (which includes commits) is published "
            "separately by the GitHub API for the top 10,000 and never merged into this "
            "score."
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

    entries = load("data/leaderboard.json", [])
    status = load("data/status.json", {})
    validation = load("data/validation.json", None)
    payload = write_coverage(entries, {
        "generated_at": status.get("last_run_utc"),
        "score_version": status.get("score_version"),
        "window_days": status.get("window_days"),
        "window_months": status.get("window_months"),
        "window_start": status.get("window_start"),
        "window_end": status.get("window_end"),
        "users_total": status.get("users_total") or status.get("users"),
        "users_ranked": status.get("users_ranked"),
        "source": status.get("ingest"),
    }, validation)
    print(
        f"Coverage: {payload['users']} users, merged-PR signal "
        f"{payload['signals']['merged_prs']['pct']}%, pushes "
        f"{payload['signals']['total_pushes']['pct']}%"
    )
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
