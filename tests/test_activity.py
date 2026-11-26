"""The 12-month activity store: idempotent daily adds, backfill months, totals."""
import gzip
import json
import os

import activity


def agg(**users):
    """Build an ingest-style accumulator for one day."""
    out = {}
    for login, values in users.items():
        out[login] = {
            "total_pushes": values.get("p", 0),
            "merged_prs": values.get("m", 0),
            "closed_issues": values.get("i", 0),
            "pr_reviews": values.get("rv", 0),
            "issue_comments": values.get("ic", 0),
            "releases": values.get("rel", 0),
            "merged_repos": values.get("mr", {}),
        }
    return out


def test_add_day_writes_monthly_shard(tmp_path):
    path, users = activity.add_day("2026-11-20", agg(alice={"p": 3, "m": 1}), str(tmp_path))
    assert users == 1
    assert os.path.basename(path) == "2026-11.json.gz"

    payload = activity.load_month("2026-11", str(tmp_path))
    assert payload["days"] == ["2026-11-20"]
    assert payload["days_covered"] == 1
    assert payload["users"]["alice"]["p"] == 3
    assert payload["users"]["alice"]["m"] == 1
    assert payload["users"]["alice"]["d"] == 1


def test_add_day_is_idempotent_for_the_same_date(tmp_path):
    activity.add_day("2026-11-20", agg(alice={"p": 3}), str(tmp_path))
    activity.add_day("2026-11-20", agg(alice={"p": 3}), str(tmp_path))
    payload = activity.load_month("2026-11", str(tmp_path))
    assert payload["users"]["alice"]["p"] == 3
    assert payload["days"] == ["2026-11-20"]


def test_add_day_sums_different_days(tmp_path):
    activity.add_day("2026-11-20", agg(alice={"p": 3, "m": 1}), str(tmp_path))
    activity.add_day("2026-11-21", agg(alice={"p": 4, "rv": 2}), str(tmp_path))
    payload = activity.load_month("2026-11", str(tmp_path))
    assert payload["users"]["alice"]["p"] == 7
    assert payload["users"]["alice"]["m"] == 1
    assert payload["users"]["alice"]["rv"] == 2
    assert payload["users"]["alice"]["d"] == 2
    assert payload["days"] == ["2026-11-20", "2026-11-21"]


def test_bots_are_not_stored(tmp_path):
    activity.add_day("2026-11-20", agg(**{
        "dependabot[bot]": {"p": 50},
        "github-actions": {"p": 50},
        "Copilot": {"p": 50},
        "human": {"p": 2},
    }), str(tmp_path))
    payload = activity.load_month("2026-11", str(tmp_path))
    assert set(payload["users"]) == {"human"}


def test_merge_month_backfill_replaces_and_is_idempotent(tmp_path):
    activity.add_day("2026-10-02", agg(alice={"p": 1}), str(tmp_path))
    path, users = activity.merge_month("2026-10", {
        "alice": {"p": 100, "m": 5},
        "bob": {"p": 3},
    }, str(tmp_path))
    assert users == 2
    payload = activity.load_month("2026-10", str(tmp_path))
    assert payload["source"] == "bigquery"
    assert payload["days_covered"] == 31
    assert payload["users"]["alice"]["p"] == 100

    # A daily add for a backfilled month must not double count it.
    activity.add_day("2026-10-03", agg(alice={"p": 9}), str(tmp_path))
    again = activity.load_month("2026-10", str(tmp_path))
    assert again["users"]["alice"]["p"] == 100


def test_load_annual_sums_months_and_counts_days(tmp_path):
    for month, day in (("2025-12-15", "2025-12"), ("2026-01-10", "2026-01")):
        activity.add_day(month, agg(alice={"p": 5}), str(tmp_path))
    users, meta = activity.load_annual("2026-11-24", str(tmp_path))
    assert users["alice"]["p"] == 10
    assert meta["window_months"] == 2
    assert meta["window_days"] == 2
    assert meta["window_start"] == "2025-12-01"
    assert meta["window_end"] == "2026-11-24"


def test_load_annual_keeps_only_twelve_months(tmp_path):
    activity.add_day("2024-01-05", agg(old={"p": 999}), str(tmp_path))
    activity.add_day("2026-11-05", agg(new={"p": 1}), str(tmp_path))
    users, meta = activity.load_annual("2026-11-24", str(tmp_path))
    assert "old" not in users
    assert users["new"]["p"] == 1
    assert meta["window_months"] == 1


def test_build_rows_ranks_by_total_and_exposes_components(tmp_path):
    activity.add_day("2026-11-20", agg(
        alice={"p": 2, "m": 1, "mr": {"acme/app": 1}},
        bob={"p": 10},
    ), str(tmp_path))
    rows, meta = activity.build_rows("2026-11-24", str(tmp_path))
    assert [r["username"] for r in rows] == ["bob", "alice"]
    top = rows[1]
    assert top["rank_score"] == 3
    assert top["merged_prs"] == 1
    assert top["counted_merges"] == {"acme/app": 1}
    assert meta["users"] == 2


def test_seed_from_rolling_reads_daily_shards_once(tmp_path):
    rolling = tmp_path / "rolling"
    rolling.mkdir()
    monthly = tmp_path / "monthly"
    shard = {
        "date": "2026-11-20",
        "users": {"alice": {"p": 4, "m": 2, "ci": 1, "rv": 0, "ic": 0, "rel": 0, "mr": {"a/b": 2}}},
    }
    with gzip.open(rolling / "2026-11-20.json.gz", "wt", encoding="utf-8") as f:
        json.dump(shard, f)

    assert activity.seed_from_rolling(str(rolling), str(monthly)) == 1
    assert activity.seed_from_rolling(str(rolling), str(monthly)) == 0
    payload = activity.load_month("2026-11", str(monthly))
    assert payload["users"]["alice"] == {"p": 4, "m": 2, "i": 1, "rv": 0, "ic": 0, "rel": 0, "d": 1, "mr": {"a/b": 2}}


def test_window_keeps_twelve_complete_months_plus_the_current_one(tmp_path):
    """Off-by-one: the month in progress must not push out the 12th complete month."""
    for month in ("2025-10", "2025-11", "2026-11"):
        activity.merge_month(month, {"dev": {"p": 1}}, str(tmp_path))
    assert activity.prune_months("2026-11-24", str(tmp_path)) == 1
    assert sorted(os.listdir(tmp_path)) == ["2025-11.json.gz", "2026-11.json.gz"]

    users, meta = activity.load_annual("2026-11-24", str(tmp_path))
    assert meta["window_months"] == 2
    assert users["dev"]["p"] == 2


def test_prune_months_keeps_the_window(tmp_path):
    for month in ("2024-01", "2025-12", "2026-11"):
        activity.merge_month(month, {"dev": {"p": 1}}, str(tmp_path))
    assert activity.prune_months("2026-11-24", str(tmp_path)) == 1
    assert sorted(os.listdir(tmp_path)) == ["2025-12.json.gz", "2026-11.json.gz"]
