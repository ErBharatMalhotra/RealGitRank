"""RealGitRank scoring v3 — absolute 12-month public activity.

One number, no percentiles, no weights, no cohort mapping:

    activity = pushes + merged PRs + closed issues + PR reviews
               + issue comments + releases

Rank is that number sorted descending (ties broken by merged PRs, then login).
Because the score is an event count it is stable when the population grows: a
developer's score only changes when their own public activity changes.

The engine reads the parquet written by ``activity.write_parquet`` so the
pipeline keeps one inspectable intermediate file between ingest and ranking.
"""
import duckdb

COMPONENTS = (
    "total_pushes",
    "merged_prs",
    "closed_issues",
    "pr_reviews",
    "issue_comments",
    "releases",
)

COLUMNS = ("username",) + COMPONENTS + ("active_days", "window_days", "rank_score")


def _load(path):
    con = duckdb.connect()
    try:
        rel = con.execute(f"SELECT * FROM read_parquet('{path}')")
        names = [d[0] for d in rel.description]
        return names, rel.fetchall()
    finally:
        con.close()


def compute_rank_scores(parquet_file_path):
    """Return ranked rows: absolute activity, components and rank 1..N."""
    names, rows = _load(parquet_file_path)
    index = {name: position for position, name in enumerate(names)}

    def value(row, name, default=0):
        position = index.get(name)
        if position is None or row[position] is None:
            return default
        try:
            return int(row[position])
        except (TypeError, ValueError):
            return default

    result = []
    for row in rows:
        username = str(row[index["username"]] or "") if "username" in index else ""
        if not username:
            continue
        components = {key: value(row, key) for key in COMPONENTS}
        activity = sum(components.values())
        if activity <= 0:
            # No observed public activity in the window: not ranked, not listed.
            continue
        entry = {"username": username, "country": "", "primary_language": "Unknown"}
        entry.update(components)
        entry["active_days"] = value(row, "active_days")
        entry["window_days"] = value(row, "window_days")
        entry["activity"] = activity
        entry["rank_score"] = activity
        result.append(entry)

    result.sort(key=lambda r: (-r["rank_score"], -r["merged_prs"], r["username"]))
    for position, entry in enumerate(result, 1):
        entry["rank"] = position
    return result
