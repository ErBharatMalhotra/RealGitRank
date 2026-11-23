"""30-day rolling activity window built from per-day gharchive shards.

Each pipeline run stores one compact gzip shard per ingested day in
``data/rolling/<YYYY-MM-DD>.json.gz``. Scoring reads the newest ``WINDOW_DAYS``
shards and aggregates them, so "merged PRs", stars, shipping weeks and push
hours describe a month of work instead of a single day.

Shard schema (compact keys, users mapped by login)::

    {
      "date": "2026-11-21",
      "users": {
        "alice": {
          "p": 3,                # pushes
          "m": 1, "mw": 1.5,     # merged PRs, external-weighted merges
          "ci": 0,               # closed issues
          "r": ["a/b", ...],     # repos touched that day
          "rn": 4,               # distinct repo count (repos list may be capped)
          "mr": {"a/b": 2},      # merged PRs per repo (top 10)
          "h": [9, 15],          # distinct push hours
          "u": 12,               # unique commit messages
          "th": 1, "ev": 3,      # test-signal hits, observed evidence
          "fs": 9, "fp": 2, "mf": 1,   # changed-file sum, PR count, multi-file PRs
          "st": 2, "fk": 0,      # stars/forks received
          "rv": 0, "ic": 1, "rel": 0
        }
      }
    }

``u`` absent means the feed carried no commit messages, so entropy is unknown
rather than zero. Nothing here is invented: missing values are simply absent.
"""
import gzip
import json
import os
from datetime import date, timedelta
from statistics import pstdev

WINDOW_DAYS = 30
ROLLING_DIR = "data/rolling"
REPO_LIST_CAP = 40
MERGED_REPO_CAP = 10


def shard_path(date_str, rolling_dir=ROLLING_DIR):
    return os.path.join(rolling_dir, f"{date_str}.json.gz")


def _top_repos(counter, cap=MERGED_REPO_CAP):
    return dict(sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))[:cap])


def write_day_shard(date_str, agg, rolling_dir=ROLLING_DIR):
    """Convert one day's gharchive accumulators into a compact gzip shard."""
    users = {}
    for login, d in agg.items():
        pushes = int(d.get("total_pushes") or 0)
        merged = int(d.get("merged_prs") or 0)
        stars = int(d.get("stars_received") or 0)
        forks = int(d.get("forks_received") or 0)
        if pushes < 1 and merged < 1 and stars < 1 and forks < 1:
            continue

        repos = sorted(d.get("repos") or ())
        record = {"p": pushes}
        if merged:
            record["m"] = merged
            record["mw"] = round(float(d.get("merged_pr_weighted") or 0.0), 2)
            record["mr"] = _top_repos(d.get("merged_repos") or {})
            refs = list(d.get("merged_pr_refs") or ())[:50]
            if refs:
                record["mrf"] = refs
        if d.get("closed_issues"):
            record["ci"] = int(d["closed_issues"])
        if repos:
            record["r"] = repos[:REPO_LIST_CAP]
            record["rn"] = len(repos)
        hours = sorted(set(d.get("hours") or ()))
        if hours:
            record["h"] = hours
        messages = d.get("commit_msgs") or set()
        if messages:
            record["u"] = len(messages)
        evidence = int(d.get("evidence") or 0)
        if evidence:
            record["th"] = int(d.get("test_hits") or 0)
            record["ev"] = evidence
        files = d.get("pr_files") or []
        if files:
            record["fs"] = int(sum(files))
            record["fp"] = len(files)
            record["mf"] = int(d.get("multi_file_prs") or 0)
        for key, field in (("st", "stars_received"), ("fk", "forks_received"),
                           ("rv", "pr_reviews"), ("ic", "issue_comments"),
                           ("rel", "releases")):
            value = int(d.get(field) or 0)
            if value:
                record[key] = value
        users[login] = record

    os.makedirs(rolling_dir, exist_ok=True)
    path = shard_path(date_str, rolling_dir)
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump({"date": date_str, "users": users}, f, separators=(",", ":"))
    return path, len(users)


def available_dates(rolling_dir=ROLLING_DIR):
    try:
        names = [n for n in os.listdir(rolling_dir) if n.endswith(".json.gz")]
    except OSError:
        return []
    return sorted(n[: -len(".json.gz")] for n in names)


def window_dates(end_date, days=WINDOW_DAYS, rolling_dir=ROLLING_DIR):
    """Newest `days` shard dates at or before `end_date`, oldest first."""
    start = (date.fromisoformat(end_date) - timedelta(days=days - 1)).isoformat()
    return [d for d in available_dates(rolling_dir) if start <= d <= end_date]


def _iso_week(date_str):
    d = date.fromisoformat(date_str)
    return d.isocalendar()[:2]


def load_window(end_date, days=WINDOW_DAYS, rolling_dir=ROLLING_DIR):
    """Merge the newest shards into one accumulator's worth of per-user rows."""
    dates = window_dates(end_date, days, rolling_dir)
    total = {}
    for date_str in dates:
        try:
            with gzip.open(shard_path(date_str, rolling_dir), "rt", encoding="utf-8") as f:
                payload = json.load(f)
        except (OSError, ValueError):
            continue
        week = _iso_week(date_str)
        for login, rec in (payload.get("users") or {}).items():
            acc = total.setdefault(login, {
                "p": 0, "m": 0, "mw": 0.0, "ci": 0, "repos": set(), "rn": 0,
                "mr": {}, "hours": [], "u": 0, "u_known": False, "th": 0, "ev": 0,
                "fs": 0, "fp": 0, "mf": 0, "st": 0, "fk": 0, "rv": 0, "ic": 0,
                "rel": 0, "days": 0, "hours_by_day": [], "weeks": set(),
            })
            acc["p"] += int(rec.get("p") or 0)
            acc["m"] += int(rec.get("m") or 0)
            acc["mw"] += float(rec.get("mw") or 0.0)
            acc["ci"] += int(rec.get("ci") or 0)
            acc["repos"].update(rec.get("r") or ())
            acc["rn"] += int(rec.get("rn") or 0)
            for repo, count in (rec.get("mr") or {}).items():
                acc["mr"][repo] = acc["mr"].get(repo, 0) + int(count)
            hours = rec.get("h") or ()
            acc["hours"].extend(hours)
            if hours:
                acc["hours_by_day"].append(len(set(hours)))
            if "u" in rec:
                acc["u"] += int(rec["u"])
                acc["u_known"] = True
            acc["th"] += int(rec.get("th") or 0)
            acc["ev"] += int(rec.get("ev") or 0)
            acc["fs"] += int(rec.get("fs") or 0)
            acc["fp"] += int(rec.get("fp") or 0)
            acc["mf"] += int(rec.get("mf") or 0)
            for key in ("st", "fk", "rv", "ic", "rel"):
                acc[key] += int(rec.get(key) or 0)
            acc["days"] += 1
            acc["weeks"].add(week)
    return dates, total


def build_window_rows(end_date, days=WINDOW_DAYS, rolling_dir=ROLLING_DIR):
    """Return (rows, meta) where rows feed the scoring engine directly."""
    dates, users = load_window(end_date, days, rolling_dir)
    rows = []
    for login, a in users.items():
        if a["p"] < 1 and a["m"] < 1:
            continue
        hours = a["hours"]
        pushes = a["p"]
        entropy_known = a["u_known"] and pushes > 0
        files_known = a["fp"] > 0
        rows.append({
            "username": login,
            "country": "",
            "primary_language": "Unknown",
            "total_pushes": pushes,
            "merged_prs": a["m"],
            "merged_prs_external_weighted": round(a["mw"], 2),
            "closed_issues": a["ci"],
            "active_repos_touched": len(a["repos"]) if a["repos"] else max(1, a["rn"]),
            "unique_commit_messages": float(a["u"]) if entropy_known else float("nan"),
            "avg_distinct_active_hours": (sum(a["hours_by_day"]) / len(a["hours_by_day"]))
            if a["hours_by_day"] else float("nan"),
            "avg_files_changed": (a["fs"] / a["fp"]) if files_known else float("nan"),
            "push_hour_stddev": pstdev(hours) if len(hours) > 1 else float("nan"),
            "test_file_ratio": (a["th"] / a["ev"]) if a["ev"] else float("nan"),
            "multi_file_commit_ratio": (a["mf"] / a["fp"]) if files_known else float("nan"),
            "language_diversity": float("nan"),  # languages are not in the events feed
            "stars_fraud_filtered": a["st"],
            "forks_fraud_filtered": a["fk"],
            "dependents": 0,
            "active_shipping_weeks": max(1, min(52, len(a["weeks"]))),
            "pr_reviews": a["rv"],
            "issue_comments": a["ic"],
            "releases": a["rel"],
            # provenance / disclosure fields (carried through scoring)
            "merged_repos": _top_repos(a["mr"]),
            "active_days": a["days"],
            "window_days": len(dates),
            "entropy_known": entropy_known,
            "files_known": files_known,
        })
    rows.sort(key=lambda r: (r["merged_prs"], r["total_pushes"]), reverse=True)
    meta = {
        "window_days": len(dates),
        "window_start": dates[0] if dates else None,
        "window_end": dates[-1] if dates else None,
        "shards": len(dates),
    }
    return rows, meta


def prune_window(end_date, keep_days=WINDOW_DAYS, rolling_dir=ROLLING_DIR):
    """Delete shards older than the window so git stays small."""
    start = (date.fromisoformat(end_date) - timedelta(days=keep_days - 1)).isoformat()
    removed = 0
    for name in available_dates(rolling_dir):
        if name < start:
            try:
                os.remove(shard_path(name, rolling_dir))
                removed += 1
            except OSError:
                pass
    return removed


def write_rows_parquet(rows, path="data/raw.parquet"):
    """Write the scoring rows to parquet (provenance fields excluded)."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    scalar_cols = (
        "username", "country", "primary_language", "total_pushes", "merged_prs",
        "merged_prs_external_weighted", "closed_issues", "active_repos_touched",
        "unique_commit_messages", "avg_distinct_active_hours", "avg_files_changed",
        "push_hour_stddev", "test_file_ratio", "multi_file_commit_ratio",
        "language_diversity", "stars_fraud_filtered", "forks_fraud_filtered",
        "dependents", "active_shipping_weeks", "pr_reviews", "issue_comments",
        "releases", "active_days", "window_days", "entropy_known", "files_known",
    )
    table = pa.Table.from_pylist(
        [{k: r.get(k) for k in scalar_cols} for r in rows]
    )
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    pq.write_table(table, path)
    return path
