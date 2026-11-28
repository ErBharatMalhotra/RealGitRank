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

import numpy as np

# The score covers the current (partial) month plus the newest 12 complete
# months, so a 12-month window is never short by the month in progress.
COMPLETE_MONTHS = 12
MAX_MONTHS = COMPLETE_MONTHS + 1
MONTHLY_DIR = os.path.join("data", "activity", "monthly")
REPO_CAP = 10
# Merged-repo detail is published for the detailed board only; keeping it for
# every account in a multi-million-user merge costs hundreds of megabytes.
MERGES_LIMIT = 50_000

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


class _MergedRecord:
    """One user's annual totals.

    A slots object instead of a dict: the 12-month merge holds every account
    seen in a year (millions), and a dict per user was several gigabytes on the
    CI runner, which killed the process. ``record[key]`` and ``.get(key)`` keep
    the mapping API the rest of the module already uses.
    """

    # No per-user merged-repo map here on purpose: a dict per account with merged
    # PRs over a full year of BigQuery data costs gigabytes, and the repo detail
    # is only published for the detailed board. ``merges_for`` reads it back from
    # the monthly shards for those few thousand accounts instead.
    __slots__ = ("p", "m", "i", "rv", "ic", "rel", "d")

    def __init__(self):
        self.p = self.m = self.i = self.rv = self.ic = self.rel = self.d = 0

    def __getitem__(self, key):
        return getattr(self, key)

    def get(self, key, default=None):
        return getattr(self, key, default)


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
            entry = users.get(login)
            if entry is None:
                entry = _MergedRecord()
                users[login] = entry
            entry.p += int(record.get("p") or 0)
            entry.m += int(record.get("m") or 0)
            entry.i += int(record.get("i") or 0)
            entry.rv += int(record.get("rv") or 0)
            entry.ic += int(record.get("ic") or 0)
            entry.rel += int(record.get("rel") or 0)
            entry.d += int(record.get("d") or 0)

    return users, {
        "window_days": days_covered,
        "window_months": len(used),
        "months": used,
        "window_start": f"{used[0]}-01" if used else None,
        "window_end": str(end_date),
    }


def _top_repos(counter, cap=REPO_CAP):
    return dict(sorted(counter.items(), key=lambda kv: (-int(kv[1]), kv[0]))[:cap])


def merges_for(names, end_date, monthly_dir=MONTHLY_DIR, max_months=MAX_MONTHS, cap=REPO_CAP):
    """Per-repo merged-PR counts for a small set of logins.

    Second streaming pass over the monthly shards: the 12-month merge keeps no
    repo detail (that was hundreds of megabytes for accounts nobody profiled),
    so the top of the board gets its detail here.
    """
    wanted = {str(name) for name in names if name}
    if not wanted:
        return {}
    totals = {}
    for month in _months_back(end_date, max_months):
        payload = load_month(month, monthly_dir)
        if not payload:
            continue
        for login, record in (payload.get("users") or {}).items():
            if login not in wanted:
                continue
            repo_counts = record.get("mr") or {}
            if not repo_counts:
                continue
            entry = totals.setdefault(login, {})
            for repo, count in repo_counts.items():
                entry[repo] = entry.get(repo, 0) + int(count)
    return {login: _top_repos(counter, cap) for login, counter in totals.items()}


# Annual totals are held as parallel int64 columns keyed by a login -> slot dict.
# A dict or object per account for a 20M-account window cost ~15 GB on the 16 GB
# CI runner and the process was killed there; columns keep the merge proportional
# to the number of accounts and copy nothing on the way to parquet.
_MERGE_COLUMNS = ("p", "m", "i", "rv", "ic", "rel", "d")
_PARQUET_COLUMNS = (
    "username", "total_pushes", "merged_prs", "closed_issues", "pr_reviews",
    "issue_comments", "releases", "active_days", "window_days", "rank_score",
)


def _grow(values, needed):
    capacity = max(needed, int(values.shape[1] * 1.5) + 1024)
    grown = np.zeros((values.shape[0], capacity), dtype=np.int64)
    grown[:, : values.shape[1]] = values
    return grown


class ColumnarTotals:
    """Per-login annual counters in one matrix instead of millions of dicts."""

    __slots__ = ("index", "values", "size")

    def __init__(self, capacity=4096):
        self.index = {}
        self.values = np.zeros((len(_MERGE_COLUMNS), capacity), dtype=np.int64)
        self.size = 0

    def add_month(self, users):
        index, values, size = self.index, self.values, self.size
        for login, record in users.items():
            slot = index.get(login)
            if slot is None:
                if size >= values.shape[1]:
                    values = _grow(values, size + 1)
                    self.values = values
                slot = size
                index[login] = slot
                size += 1
                for row, key in enumerate(_MERGE_COLUMNS):
                    values[row, slot] = record.get(key) or 0
            else:
                for row, key in enumerate(_MERGE_COLUMNS):
                    values[row, slot] += record.get(key) or 0
        self.size = size


def build_columns(end_date, monthly_dir=MONTHLY_DIR, max_months=MAX_MONTHS):
    """Return (columns, meta): parquet columns, one absolute-activity row per developer.

    Same window and same arithmetic as ``load_annual``/``build_rows``, but without
    a per-user record: the merge accumulates into numpy columns and the parquet
    table is built from those columns directly.
    """
    months = _months_back(end_date, max_months)
    totals = ColumnarTotals()
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
        totals.add_month(payload.get("users") or {})
        # Drop the month's parsed shard before the next one is loaded.
        del payload

    meta = {
        "window_days": days_covered,
        "window_months": len(used),
        "months": used,
        "window_start": f"{used[0]}-01" if used else None,
        "window_end": str(end_date),
    }

    names = list(totals.index.keys())
    values = totals.values[:, : totals.size]
    # Release the lookup dict first: the names list keeps the login strings alive
    # and that dict is the largest single object in the merge.
    totals.index = {}
    totals.values = np.empty((len(_MERGE_COLUMNS), 0), dtype=np.int64)

    activity = values[: len(COMPONENTS)].sum(axis=0)
    keep = np.flatnonzero(activity > 0)
    columns = {
        "username": [names[i] for i in keep],
        "total_pushes": values[0, keep],
        "merged_prs": values[1, keep],
        "closed_issues": values[2, keep],
        "pr_reviews": values[3, keep],
        "issue_comments": values[4, keep],
        "releases": values[5, keep],
        "active_days": values[6, keep],
        "window_days": np.full(keep.size, meta["window_days"], dtype=np.int64),
        "rank_score": activity[keep],
    }
    meta["users"] = int(keep.size)
    return columns, meta


def write_parquet_columns(columns, path="data/activity.parquet"):
    """Write the scoring columns to parquet straight from the numpy arrays."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    table = pa.Table.from_pydict({name: columns[name] for name in _PARQUET_COLUMNS})
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    pq.write_table(table, path)
    return path


def build_rows(end_date, monthly_dir=MONTHLY_DIR, max_months=MAX_MONTHS):
    """Return (rows, meta) as dict rows, ranked. For tests and small windows.

    The pipeline uses ``build_columns`` + ``write_parquet_columns``: materialising
    a dict per account for a 20M-account window is what exhausted the runner.
    """
    columns, meta = build_columns(end_date, monthly_dir, max_months)
    rows = []
    for index, login in enumerate(columns["username"]):
        row = {"username": login, "country": "", "primary_language": "Unknown"}
        for _, field in COMPONENTS:
            row[field] = int(columns[field][index])
        row["active_days"] = int(columns["active_days"][index])
        row["rank_score"] = int(columns["rank_score"][index])
        row["window_days"] = meta["window_days"]
        rows.append(row)
    rows.sort(key=lambda row: (-row["rank_score"], -row["merged_prs"], row["username"]))
    return rows, meta


def write_parquet(rows, path="data/activity.parquet"):
    """Write dict rows to parquet (provenance fields excluded)."""
    columns = {name: [row.get(name) for row in rows] for name in _PARQUET_COLUMNS}
    return write_parquet_columns(columns, path)


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
