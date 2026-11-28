"""RealGitRank pipeline runner.

Flow: gharchive.org -> daily rolling shard -> monthly activity rollups ->
12-month absolute activity score -> leaderboard + top-1,000 profiles + coverage.

No mock data: if the real ingest fails, the run fails instead of publishing
invented numbers. A changed score version clears day-over-day movement so a
scale migration does not look like a giant rank shift.
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import activity
from coverage import write_coverage
from history import (
    append_user_history,
    apply_deltas,
    clear_movement,
    load_previous_snapshot,
    prune_snapshots,
    write_snapshot,
)
from profiles import PROFILE_LIMIT, prune_profiles_for_leaderboard
from rolling import WINDOW_DAYS, prune_window, window_source_stats
from scoring_engine import rank_users

SCORE_VERSION = "v3-activity"
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


def stage(label):
    """Progress + peak RSS, so a killed run says where it died and how big it got."""
    peak = 0.0
    try:
        import resource

        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0
    except (ImportError, OSError):
        pass
    print(f"[stage] {label} peak_rss_mb={peak:.0f}", flush=True)


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
    # Portfolio context from enrichment (never part of the score): carried forward
    # so a run that cannot query the API does not lose it.
    for key in ("followers", "commits_1y", "contributions_1y", "stars_total", "repos_total"):
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

        print("Ingesting real GitHub event data (gharchive.org)...", flush=True)
        if not ingest():
            print("FAIL: real ingest produced no data; refusing to publish invented numbers")
            return 1
        stage("ingest")
    else:
        from rolling import available_dates

        shards = available_dates()
        if not shards:
            print("FAIL: RGR_OFFLINE=1 but no rolling shards exist")
            return 1
        end_date = shards[-1]

    # Daily shards are pruned after 30 days, so the 12-month score lives in the
    # monthly store. Seeding is idempotent: already-recorded days are skipped.
    seeded = activity.seed_from_rolling()
    if seeded:
        print(f"Seeded {seeded} daily shards into the monthly activity store", flush=True)

    rows, window = activity.build_rows(end_date)
    stage(f"merge 12-month activity ({len(rows)} users)")
    if not rows:
        print(f"FAIL: no activity rows in the 12-month window ending {end_date}")
        return 1
    print(
        f"Activity window {window['window_start']}..{window['window_end']} "
        f"({window['window_months']}/{activity.MAX_MONTHS} months, "
        f"{window['window_days']} days, {len(rows)} users)",
        flush=True,
    )

    activity.write_parquet(rows, "data/activity.parquet")
    del rows
    stage("write parquet")
    # Rank straight to disk: the detailed board stays in memory, every other
    # account is written to the long tail row by row, so a 12-month window never
    # needs a dict per account in RAM.
    tail_count = 0
    tail_file = open(LONG_TAIL_PATH, "w", encoding="utf-8")
    tail_file.write("[")

    def write_tail(entry):
        nonlocal tail_count
        if tail_count:
            tail_file.write(",")
        tail_file.write(json.dumps({
            "username": entry["username"],
            "rank": entry["rank"],
            "rank_score": entry.get("rank_score"),
            "merged_prs": entry.get("merged_prs"),
            "total_pushes": entry.get("total_pushes"),
        }, separators=(",", ":"), ensure_ascii=False))
        tail_count += 1

    try:
        scored, total_users = rank_users("data/activity.parquet", LEADERBOARD_LIMIT, write_tail)
    finally:
        tail_file.write("]")
        tail_file.close()
    stage(f"rank {total_users} users")

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

    # Enrich in place: copying every scored row into a second dict keyed by name
    # was another full copy of the board (millions of rows) at peak memory.
    for entry in scored:
        entry.update(carried_profile_fields(entry["username"], old_by_name, old_profiles))
        entry["score_version"] = SCORE_VERSION
        entry["window_start"] = window["window_start"]
        entry["window_end"] = window["window_end"]

    # rank_users returns the detailed board already in rank order with ranks set.
    lb = scored

    # Merged-repo provenance is published for the detailed board only. Repo lists
    # are not kept in the 12-month merge (millions of accounts, gigabytes), so
    # they are read back from the monthly shards for these names alone.
    merges_by_name = activity.merges_for(
        (entry["username"] for entry in lb), end_date
    )
    for entry in lb:
        merged = merges_by_name.get(entry["username"])
        if merged:
            entry["counted_merges"] = merged
    del merges_by_name
    stage("repo detail for detailed board")

    run_date = today.isoformat()
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

    with open("data/leaderboard.json", "w", encoding="utf-8") as f:
        json.dump(lb, f, indent=2)

    print(
        f"Live ranked users: {total_users} ({len(lb)} detailed + {tail_count} compact)",
        flush=True,
    )

    os.makedirs("data/profiles", exist_ok=True)
    for entry in lb[:PROFILE_LIMIT]:
        username = entry["username"]
        profile = dict(entry)
        profile["rank"] = entry["rank"]
        merged_repos = entry.get("counted_merges") or {}
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
        print(f"Pruned {removed_shards} daily shards older than {WINDOW_DAYS} days")
    removed_months = activity.prune_months(end_date)
    if removed_months:
        print(f"Pruned {removed_months} activity months older than {activity.MAX_MONTHS} months")

    generated_at = datetime.now(timezone.utc).isoformat()
    coverage_meta = {
        "generated_at": generated_at,
        "score_version": SCORE_VERSION,
        "source": window_source_stats(end_date),
        "users_total": total_users,
        "users_ranked": total_users,
        "users_listed": 0,
        "months_available": window["window_months"],
        **window,
    }
    coverage = write_coverage(lb, coverage_meta, load_json("data/validation.json", None))

    with open("data/status.json", "w", encoding="utf-8") as f:
        json.dump({
            "last_run_utc": generated_at,
            "data_age_hours": 0,
            "status": "ok",
            "users": total_users,
            "users_total": total_users,
            "users_ranked": total_users,
            "users_listed": 0,
            "listed_only": 0,
            "detailed": len(lb),
            "new_today": len(scored),
            "source": "real",
            "ingest": coverage_meta["source"],
            "score_version": SCORE_VERSION,
            "score_unit": "public GitHub activity events over the last 12 complete months plus the current month",
            "window_days": window["window_days"],
            "window_months": window["window_months"],
            "window_start": window["window_start"],
            "window_end": window["window_end"],
            "shards": window["window_months"],
        }, f, indent=2)

    print(
        f"Scored {total_users} users from real events. Top: {lb[0]['username']} "
        f"({lb[0]['rank_score']} activity, {lb[0]['merged_prs']} merged PRs)",
        flush=True,
    )
    print(
        f"Coverage: merged-PR signal {coverage['signals']['merged_prs']['pct']}%, "
        f"pushes {coverage['signals']['total_pushes']['pct']}%",
        flush=True,
    )
    stage("final")


if __name__ == "__main__":
    sys.exit(main())
