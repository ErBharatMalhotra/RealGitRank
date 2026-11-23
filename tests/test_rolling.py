"""Rolling shards: daily events must aggregate into a real 30-day window."""
import gzip
import json
from datetime import datetime

from ingest_gharchive import aggregate_events
from rolling import build_window_rows, prune_window, window_dates, write_day_shard


def event(login, etype, repo="owner/repo", when=None, payload=None):
    return {
        "actor": {"login": login},
        "type": etype,
        "repo": {"name": repo},
        "org": {},
        "created_at": when or datetime(2026, 11, 20, 10),
        "payload": payload or {},
    }


def push(login, messages, when=None, repo="owner/repo"):
    return event(
        login, "PushEvent", repo=repo, when=when,
        payload={"commits": [{"message": m} for m in messages]},
    )


def merged(login, repo="owner/repo", when=None, changed_files=None):
    pr = {"merged": True, "title": "add tests", "body": ""}
    if changed_files is not None:
        pr["changed_files"] = changed_files
    return event(login, "PullRequestEvent", repo=repo, when=when,
                 payload={"action": "closed", "pull_request": pr})


def test_window_aggregates_multiple_days(tmp_path):
    day1 = aggregate_events([
        push("alice", ["add tests", "fix"], when=datetime(2026, 11, 16, 9)),
        merged("alice", repo="upstream/lib", changed_files=6),
        event("fan", "WatchEvent", repo="alice/app"),
    ])
    day2 = aggregate_events([
        push("alice", ["refactor"], when=datetime(2026, 11, 23, 15)),
        merged("alice", repo="alice/app", changed_files=2),
    ])
    write_day_shard("2026-11-16", day1, str(tmp_path))
    write_day_shard("2026-11-23", day2, str(tmp_path))

    rows, meta = build_window_rows("2026-11-23", days=30, rolling_dir=str(tmp_path))
    alice = next(r for r in rows if r["username"] == "alice")

    assert meta["shards"] == 2
    assert meta["window_start"] == "2026-11-16"
    assert alice["total_pushes"] == 2
    assert alice["merged_prs"] == 2
    assert alice["merged_prs_external_weighted"] == 2.5
    assert alice["active_shipping_weeks"] == 2
    assert alice["active_days"] == 2
    assert alice["stars_fraud_filtered"] == 1
    assert alice["avg_files_changed"] == 4.0
    assert alice["test_file_ratio"] == 0.6
    assert alice["entropy_known"] is True
    assert alice["files_known"] is True
    assert alice["merged_repos"] == {"alice/app": 1, "upstream/lib": 1}


def test_unknown_signals_stay_unknown(tmp_path):
    agg = aggregate_events([push("bob", [])])
    write_day_shard("2026-11-20", agg, str(tmp_path))
    rows, _ = build_window_rows("2026-11-20", days=30, rolling_dir=str(tmp_path))
    bob = next(r for r in rows if r["username"] == "bob")
    assert bob["entropy_known"] is False
    assert bob["files_known"] is False
    assert bob["unique_commit_messages"] != bob["unique_commit_messages"]  # NaN
    assert bob["avg_files_changed"] != bob["avg_files_changed"]  # NaN


def test_pr_only_users_keep_their_merges(tmp_path):
    agg = aggregate_events([merged("carol", repo="carol/tool")])
    write_day_shard("2026-11-20", agg, str(tmp_path))
    rows, _ = build_window_rows("2026-11-20", days=30, rolling_dir=str(tmp_path))
    carol = next(r for r in rows if r["username"] == "carol")
    assert carol["merged_prs"] == 1
    assert carol["merged_prs_external_weighted"] == 1.0
    assert carol["merged_repos"] == {"carol/tool": 1}


def test_window_bounds_and_pruning(tmp_path):
    for day in ("2026-10-01", "2026-11-20", "2026-11-21"):
        write_day_shard(day, aggregate_events([push(f"u{day[-2:]}", ["x"])]), str(tmp_path))
    assert window_dates("2026-11-21", days=30, rolling_dir=str(tmp_path)) == [
        "2026-11-20", "2026-11-21",
    ]
    assert prune_window("2026-11-21", keep_days=30, rolling_dir=str(tmp_path)) == 1


def test_shard_is_compact_gzip(tmp_path):
    agg = aggregate_events([push("dave", ["a", "b"], repo="dave/app")])
    path, users = write_day_shard("2026-11-20", agg, str(tmp_path))
    assert users == 1
    with gzip.open(path, "rt", encoding="utf-8") as f:
        payload = json.load(f)
    assert payload["date"] == "2026-11-20"
    assert payload["users"]["dave"]["p"] == 1
    assert payload["users"]["dave"]["r"] == ["dave/app"]
