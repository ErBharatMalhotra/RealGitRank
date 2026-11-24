"""RealGitRank pipeline runner.

Flow: gharchive.org -> daily rolling shard -> newest 30 shards -> percentile
scoring -> leaderboard + top-1,000 profiles + coverage.

No mock data: if the real ingest fails, the run fails instead of publishing
invented numbers. Scores are computed from the window only (no 70/30 blending
with an older scale), and a changed score version clears day-over-day movement
so a scale migration does not look like a giant rank shift.
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from automation import strip_signals, write_signals
from coverage import write_coverage
from history import (
    append_user_history,
    apply_deltas,
    assign_tiers,
    clear_movement,
    load_previous_snapshot,
    prune_snapshots,
    write_snapshot,
)
from profiles import PROFILE_LIMIT, prune_profiles_for_leaderboard
from rolling import (
    WINDOW_DAYS,
    build_window_rows,
    prune_window,
    window_source_stats,
    write_rows_parquet,
)
from scoring_engine import compute_rank_scores

SCORE_VERSION = "v2-percentile"
# Detailed rows committed to git. Everyone else is still ranked and published
# (profile/score/search); the compact long tail is written to a gitignored file
# that the chunk step then turns into public payload pages.
LEADERBOARD_LIMIT = 50000
LONG_TAIL_PATH = "data/long_tail.json"


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def carried_profile_fields(username, old_by_name, old_profiles):
    """Enrichment (country/language/avatar/bio/company) from the previous run."""
    prev = old_by_name.get(username) or {}
    prof = old_profiles.get(username) or {}
    carried = {
        "country": prev.get("country") or prof.get("country") or "",
        "primary_language": prev.get("primary_language") or prof.get("primary_language") or "Unknown",
        "avatar_url": prev.get("avatar_url") or prof.get("avatar_url") or "",
        "bio": prev.get("bio") or prof.get("bio") or "",
        "company": prev.get("company") or prof.get("company") or "",
        "location": prev.get("location") or prof.get("location") or "",
    }
    # Lifetime portfolio context from enrichment: carried forward so a run that
    # cannot query the API does not lose it. Never used by the scoring engine.
    for key in ("followers", "commits_1y", "stars_total", "repos_total"):
        value = prev.get(key) or prof.get(key)
        if value:
            carried[key] = value
    return carried


def main():
    offline = os.environ.get("RGR_OFFLINE") == "1"
    today = datetime.now(timezone.utc).date()
    end_date = (today - timedelta(days=1)).isoformat()

    if not offline:
        from ingest_gharchive import ingest

        print("Ingesting real GitHub event data (gharchive.org)...")
        if not ingest():
            print("FAIL: real ingest produced no data; refusing to publish invented numbers")
            return 1
    else:
        from rolling import available_dates

        shards = available_dates()
        if not shards:
            print("FAIL: RGR_OFFLINE=1 but no rolling shards exist")
            return 1
        end_date = shards[-1]

    rows, window = build_window_rows(end_date)
    if not rows:
        print(f"FAIL: no rows in the rolling window ending {end_date}")
        return 1
    print(
        f"Window {window['window_start']}..{window['window_end']} "
        f"({window['shards']}/{WINDOW_DAYS} shards, {len(rows)} users)"
    )

    write_rows_parquet(rows, "data/raw.parquet")
    scored = compute_rank_scores("data/raw.parquet")

    old_status = load_json("data/status.json", {})
    old_lb = load_json("data/leaderboard.json", [])
    old_by_name = {u.get("username"): u for u in old_lb if u.get("username")}
    old_profiles = {}
    if os.path.isdir("data/profiles"):
        for name in os.listdir("data/profiles"):
            if name.endswith(".json"):
                payload = load_json(os.path.join("data/profiles", name), None)
                if isinstance(payload, dict) and payload.get("username"):
                    old_profiles[payload["username"]] = payload

    # Provenance lives in the window rows (it is not a parquet/scoring column).
    merges_by_name = {r["username"]: (r.get("merged_repos") or {}) for r in rows}
    ci_by_name = {r["username"]: int(r.get("ci_pushes") or 0) for r in rows}

    by_name = {}
    for rec in scored:
        username = rec["username"]
        entry = dict(rec)
        entry.update(carried_profile_fields(username, old_by_name, old_profiles))
        entry["score_version"] = SCORE_VERSION
        entry["window_start"] = window["window_start"]
        entry["window_end"] = window["window_end"]
        if ci_by_name.get(username):
            # Disclosed per user: CI-attributed pushes, not authored commits.
            entry["ci_pushes"] = ci_by_name[username]
        by_name[username] = entry

    ranked = sorted(
        by_name.values(),
        key=lambda u: (u.get("rank_score", 0), u.get("merged_prs", 0), u.get("username", "")),
        reverse=True,
    )
    for index, entry in enumerate(ranked, 1):
        entry["rank"] = index
    lb = ranked[:LEADERBOARD_LIMIT]

    run_date = today.isoformat()
    # Percentile/tier are computed over the whole ranked population, not the slice.
    assign_tiers(ranked, total=len(ranked))
    clear_movement(lb)
    prev_date, previous = load_previous_snapshot("data/history", run_date)
    if previous and old_status.get("score_version") == SCORE_VERSION:
        matched = apply_deltas(lb, previous)
        print(f"Movement vs {prev_date}: {matched} returning users matched")
    elif previous:
        print(f"Score version changed to {SCORE_VERSION}: movement reset for this run")
    write_snapshot("data/history", run_date, lb)
    pruned_snapshots = prune_snapshots("data/history")
    if pruned_snapshots:
        print(f"Pruned {pruned_snapshots} old history snapshots")

    # Classification values are internal only: keep them in a gitignored sidecar
    # for the coverage/quality-gate steps, never in the committed leaderboard.
    write_signals(lb)
    with open("data/leaderboard.json", "w", encoding="utf-8") as f:
        json.dump([strip_signals(entry) for entry in lb], f, indent=2)

    # Everyone outside the detailed slice stays ranked and searchable, with the
    # fields a profile/mini-card needs. Derived data: never committed.
    tail = [
        {
            "username": entry["username"],
            "rank": entry["rank"],
            "rank_score": entry.get("rank_score"),
            "percentile": entry.get("percentile"),
            "tier": entry.get("tier"),
            "merged_prs": entry.get("merged_prs"),
            "total_pushes": entry.get("total_pushes"),
        }
        for entry in ranked[LEADERBOARD_LIMIT:]
    ]
    with open(LONG_TAIL_PATH, "w", encoding="utf-8") as f:
        json.dump(tail, f, separators=(",", ":"), ensure_ascii=False)
    print(f"Live ranked users: {len(ranked)} ({len(lb)} detailed + {len(tail)} compact)")

    os.makedirs("data/profiles", exist_ok=True)
    for entry in lb[:PROFILE_LIMIT]:
        username = entry["username"]
        profile = strip_signals(entry)
        profile["rank"] = entry["rank"]
        merged_repos = merges_by_name.get(username) or {}
        if merged_repos:
            profile["counted_merges"] = [
                {"repo": repo, "prs": int(count)}
                for repo, count in sorted(merged_repos.items(), key=lambda kv: (-int(kv[1]), kv[0]))[:10]
            ]
        profile["history"] = list(old_profiles.get(username, {}).get("history") or [])
        append_user_history(profile, run_date, entry["rank"], entry["rank_score"])
        with open(f"data/profiles/{username}.json", "w", encoding="utf-8") as f:
            json.dump(profile, f, indent=2)

    pruned_profiles = prune_profiles_for_leaderboard("data/profiles", lb)
    if pruned_profiles:
        print(f"Pruned {pruned_profiles} profiles for users outside the top {PROFILE_LIMIT}")

    removed_shards = prune_window(end_date)
    if removed_shards:
        print(f"Pruned {removed_shards} shards older than {WINDOW_DAYS} days")

    generated_at = datetime.now(timezone.utc).isoformat()
    coverage_meta = {
        "generated_at": generated_at,
        "score_version": SCORE_VERSION,
        "source": window_source_stats(end_date),
        "users_total": len(ranked),
        **window,
    }
    coverage = write_coverage(lb, coverage_meta, load_json("data/validation.json", None))

    with open("data/status.json", "w", encoding="utf-8") as f:
        json.dump({
            "last_run_utc": generated_at,
            "data_age_hours": 0,
            "status": "ok",
            "users": len(ranked),
            "ci_pushes": sum(ci_by_name.values()),
            "detailed": len(lb),
            "new_today": len(scored),
            "source": "real",
            "ingest": coverage_meta["source"],
            "score_version": SCORE_VERSION,
            "window_days": window["window_days"],
            "window_start": window["window_start"],
            "window_end": window["window_end"],
            "shards": window["shards"],
        }, f, indent=2)

    print(
        f"Scored {len(scored)} users from real events. Top: {lb[0]['username']} "
        f"({lb[0]['rank_score']}, {lb[0]['merged_prs']} merged PRs)"
    )
    print(
        f"Coverage: quality {coverage['pillars']['code_health']['pct']}%, "
        f"automation evidence {coverage['automation_evidence']['pct']}%"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
