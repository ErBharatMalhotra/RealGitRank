"""12-month public-activity store for the RealGitRank score.

The score is one absolute number: how many public GitHub activity events a
developer produced in the last 12 months.

    activity = pushes + merged PRs + closed issues + PR reviews
               + issue comments + releases

Why monthly rollups instead of the 30-day daily shards: the daily shards are
pruned to keep the repository small, and a year of them would be hundreds of
megabytes. One compact file per month (``data/activity/monthly/<YYYY-MM>.json.gz``)
keeps the same information for the score at a fraction of the size.

Shards are written two ways and both are idempotent:

* daily ingest appends one day (``add_day``); a day already listed in ``days`` is
  skipped, so re-running a date never double counts;
* the optional BigQuery backfill writes complete past months (``merge_month``)
  with ``source: "bigquery"`` and the month's full day count. A backfilled month
  is never mixed with daily appends, so counts cannot be counted twice.

The public event feed no longer carries a push's commit list (GitHub trimmed
``PushEvent`` payloads to ref/head/before/push_id), so a push is one event here,
not the number of commits it contained. That limitation is stated on the
methodology page instead of being hidden behind a different metric name.
"""
import gzip
import json
import os
from datetime import date

# The score covers the current (partial) month plus the newest 12 complete
# months, so a 12-month window is never short by the month in progress.
COMPLETE_MONTHS = 12
MAX_MONTHS = COMPLETE_MONTHS + 1
MONTHLY_DIR = os.path.join("data", "activity", "monthly")
REPO_CAP = 10

# GitHub's own Copilot agent account pushes and opens PRs like an app, not a
# person, and would otherwise sit at the top of a human activity ranking.
BOT_LOGINS = {"github-actions", "dependabot", "renovate[bot]", "dependabot-preview[bot]", "Copilot"}

# shard key -> public row field
COMPONENTS = (
    ("p", "total_pushes"),
    ("m", "merged_prs"),
    ("i", "closed_issues"),
    ("rv", "pr_reviews"),
    ("ic", "issue_comments"),
    ("rel", "releases"),
)

# shard accumulator field -> shard key
_AGG_FIELDS = {
    "p": "total_pushes",
    "m": "merged_prs",
    "i": "closed_issues",
    "rv": "pr_reviews",
    "ic": "issue_comments",
    "rel": "releases",
}


def is_bot_login(login):
    return not login or "[bot]" in login or login in BOT_LOGINS


def month_of(date_str):
    return str(date_str)[:7]


def month_path(month, monthly_dir=MONTHLY_DIR):
    return os.path.join(monthly_dir, f"{month}.json.gz")


def load_month(month, monthly_dir=MONTHLY_DIR):
    try:
        with gzip.open(month_path(month, monthly_dir), "rt", encoding="utf-8") as f:
            payload = json.load(f)
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def save_month(payload, monthly_dir=MONTHLY_DIR):
    os.makedirs(monthly_dir, exist_ok=True)
    path = month_path(payload["month"], monthly_dir)
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump(payload, f, separators=(",", ":"))
    return path


def _new_record():
    return {"p": 0, "m": 0, "i": 0, "rv": 0, "ic": 0, "rel": 0, "d": 0, "mr": {}}


def _record_activity(record):
    return sum(int(record.get(key) or 0) for key, _ in COMPONENTS)


def add_day(date_str, agg, monthly_dir=MONTHLY_DIR):
    """Append one day's per-user activity to its month shard (idempotent).

    ``agg`` is the ingest accumulator (``ingest_gharchive.aggregate_events``):
    each value carries ``total_pushes``, ``merged_prs``, ``closed_issues``,
    ``pr_reviews``, ``issue_comments``, ``releases`` and ``merged_repos``.
    CI-attributed pushes are deliberately not part of this sum: they are the
    bot's work, not the account's own activity.
    """
    date_str = str(date_str)
    month = month_of(date_str)
    payload = load_month(month, monthly_dir) or {"month": month, "days": [], "users": {}}
    users = payload.setdefault("users", {})
    days = payload.setdefault("days", [])

    if date_str in days:
        return month_path(month, monthly_dir), len(users)
    if payload.get("source") == "bigquery":
        # A backfilled complete month already contains this date.
        return month_path(month, monthly_dir), len(users)

    added = 0
    for login, record in agg.items():
        if is_bot_login(login):
            continue
        values = {key: int(record.get(field) or 0) for key, field in _AGG_FIELDS.items()}
        if not any(values.values()):
            continue
        entry = users.setdefault(login, _new_record())
        for key, value in values.items():
            entry[key] = int(entry.get(key) or 0) + value
        entry["d"] = int(entry.get("d") or 0) + 1
        for repo, count in (record.get("merged_repos") or {}).items():
            entry.setdefault("mr", {})[repo] = int(entry["mr"].get(repo, 0)) + int(count)
        added += 1

    days.append(date_str)
    days.sort()
    payload["days_covered"] = len(days)
    payload.pop("source", None)
    path = save_month(payload, monthly_dir)
    return path, added


def merge_month(month, counters, monthly_dir=MONTHLY_DIR):
    """Write a complete month of per-user counters (BigQuery backfill).

    ``counters`` maps login -> ``{p,m,i,rv,ic,rel}``. Existing daily data for the
    month is replaced, so running the backfill twice is safe.
    """
    users = {}
    for login, values in counters.items():
        if is_bot_login(login):
            continue
        record = _new_record()
        for key, _ in COMPONENTS:
            record[key] = int(values.get(key) or 0)
        if _record_activity(record) <= 0:
            continue
        users[login] = record

    year, month_no = (int(part) for part in month.split("-"))
    days_in_month = (date(year + (month_no == 12), (month_no % 12) + 1, 1) - date(year, month_no, 1)).days
    payload = {
        "month": month,
        "days": [],
        "days_covered": days_in_month,
        "source": "bigquery",
        "users": users,
    }
    path = save_month(payload, monthly_dir)
    return path, len(users)


def seed_from_rolling(rolling_dir="data/rolling", monthly_dir=MONTHLY_DIR):
    """Fold existing daily shards into monthly activity shards (idempotent).

    Used on the first run after the score change and every run afterwards: days
    already recorded in a month are skipped, so this never double counts.
    """
    try:
        names = sorted(n for n in os.listdir(rolling_dir) if n.endswith(".json.gz"))
    except OSError:
        return 0

    # Group by month first: rewriting a month shard once per day would serialise
    # the whole month's user map dozens of times.
    by_month = {}
    for name in names:
        by_month.setdefault(month_of(name), []).append(name)

    seeded = 0
    for month, month_files in by_month.items():
        payload = load_month(month, monthly_dir) or {"month": month, "days": [], "users": {}}
        if payload.get("source") == "bigquery":
            continue
        users = payload.setdefault("users", {})
        days = payload.setdefault("days", [])
        changed = False
        for name in month_files:
            date_str = name[: -len(".json.gz")]
            if date_str in days:
                continue
            try:
                with gzip.open(os.path.join(rolling_dir, name), "rt", encoding="utf-8") as f:
                    day = json.load(f)
            except (OSError, ValueError):
                continue
            for login, record in (day.get("users") or {}).items():
                if not isinstance(record, dict) or is_bot_login(login):
                    continue
                values = {
                    "p": int(record.get("p") or 0),
                    "m": int(record.get("m") or 0),
                    "i": int(record.get("ci") or 0),
                    "rv": int(record.get("rv") or 0),
                    "ic": int(record.get("ic") or 0),
                    "rel": int(record.get("rel") or 0),
                }
                if not any(values.values()):
                    continue
                entry = users.setdefault(login, _new_record())
                for key, value in values.items():
                    entry[key] = int(entry.get(key) or 0) + value
                entry["d"] = int(entry.get("d") or 0) + 1
                for repo, count in (record.get("mr") or {}).items():
                    entry.setdefault("mr", {})[repo] = int(entry["mr"].get(repo, 0)) + int(count)
            days.append(date_str)
            seeded += 1
            changed = True
        if changed:
            days.sort()
            payload["days_covered"] = len(days)
            save_month(payload, monthly_dir)
    return seeded


def _months_back(end_date, count=MAX_MONTHS):
    year, month = (int(part) for part in month_of(end_date).split("-"))
    months = []
    for _ in range(count):
        months.append(f"{year:04d}-{month:02d}")
        month -= 1
        if month == 0:
            month = 12
            year -= 1
    return sorted(months)


def load_annual(end_date, monthly_dir=MONTHLY_DIR, max_months=MAX_MONTHS):
    """Merge the newest `max_months` monthly shards into one per-user record."""
    months = _months_back(end_date, max_months)
    users = {}
    used = []
    days_covered = 0

    for month in months:
        payload = load_month(month, monthly_dir)
        if not payload:
            continue
        used.append(month)
        counted_days = [d for d in (payload.get("days") or []) if str(d) <= str(end_date)]
        if payload.get("source") == "bigquery":
            days_covered += int(payload.get("days_covered") or 0)
        else:
            days_covered += len(counted_days)

        for login, record in (payload.get("users") or {}).items():
            entry = users.setdefault(login, _new_record())
            for key, _ in COMPONENTS:
                entry[key] = int(entry.get(key) or 0) + int(record.get(key) or 0)
            entry["mr"] = entry.get("mr") or {}
            for repo, count in (record.get("mr") or {}).items():
                entry["mr"][repo] = int(entry["mr"].get(repo, 0)) + int(count)
            entry["d"] += int(record.get("d") or 0)

    return users, {
        "window_days": days_covered,
        "window_months": len(used),
        "months": used,
        "window_start": f"{used[0]}-01" if used else None,
        "window_end": str(end_date),
    }


def _top_repos(counter, cap=REPO_CAP):
    return dict(sorted(counter.items(), key=lambda kv: (-int(kv[1]), kv[0]))[:cap])


def build_rows(end_date, monthly_dir=MONTHLY_DIR, max_months=MAX_MONTHS):
    """Return (rows, meta) with one absolute-activity row per developer."""
    users, meta = load_annual(end_date, monthly_dir, max_months)
    rows = []
    for login, record in users.items():
        activity = _record_activity(record)
        if activity <= 0:
            continue
        row = {"username": login, "country": "", "primary_language": "Unknown"}
        for key, field in COMPONENTS:
            row[field] = int(record.get(key) or 0)
        row["active_days"] = int(record.get("d") or 0)
        row["rank_score"] = activity
        row["window_days"] = meta["window_days"]
        merged = _top_repos(record.get("mr") or {})
        if merged:
            row["counted_merges"] = merged
        rows.append(row)

    rows.sort(key=lambda r: (-r["rank_score"], -r["merged_prs"], r["username"]))
    meta = dict(meta)
    meta["users"] = len(rows)
    return rows, meta


def write_parquet(rows, path="data/activity.parquet"):
    """Write the scoring columns to parquet (provenance fields excluded)."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    columns = (
        "username", "total_pushes", "merged_prs", "closed_issues", "pr_reviews",
        "issue_comments", "releases", "active_days", "window_days", "rank_score",
    )
    table = pa.Table.from_pylist([{k: r.get(k) for k in columns} for r in rows])
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    pq.write_table(table, path)
    return path


def prune_months(end_date, monthly_dir=MONTHLY_DIR, keep=MAX_MONTHS):
    """Delete monthly shards outside the window (12 complete months + current)."""
    oldest = _months_back(end_date, keep)[0]
    try:
        names = [n for n in os.listdir(monthly_dir) if n.endswith(".json.gz")]
    except OSError:
        return 0
    removed = 0
    for name in names:
        if name[: -len(".json.gz")] < oldest:
            try:
                os.remove(os.path.join(monthly_dir, name))
                removed += 1
            except OSError:
                pass
    return removed
