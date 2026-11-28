"""RealGitRank scoring v3 — absolute 12-month public activity.

One number, no percentiles, no weights, no cohort mapping:

    activity = pushes + merged PRs + closed issues + PR reviews
               + issue comments + releases

Rank is that number sorted descending (ties broken by merged PRs, then login).
Because the score is an event count it is stable when the population grows: a
developer's score only changes when their own public activity changes.

The engine reads the parquet written by ``activity.write_parquet`` so the
pipeline keeps one inspectable intermediate file between ingest and ranking.
Rows are streamed in score order: a 12-month window holds millions of accounts,
and materialising every row (then sorting a second list) was several gigabytes
on the CI runner. Only the detailed board is kept in memory; the compact long
tail is handed to the caller row by row.
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

COMPONENT_FIELDS = {key: key for key in COMPONENTS}

COLUMNS = ("username",) + COMPONENTS + ("active_days", "window_days", "rank_score")

_BATCH = 20_000
_SCORE_SQL = " + ".join(f"COALESCE({name}, 0)" for name in COMPONENTS)


def _entry(row, rank):
    entry = {
        "username": str(row[0]),
        "country": "",
        "primary_language": "Unknown",
    }
    for offset, name in enumerate(COMPONENTS, start=1):
        entry[name] = int(row[offset] or 0)
    entry["active_days"] = int(row[len(COMPONENTS) + 1] or 0)
    entry["window_days"] = int(row[len(COMPONENTS) + 2] or 0)
    entry["activity"] = int(row[len(COMPONENTS) + 3] or 0)
    entry["rank_score"] = entry["activity"]
    entry["rank"] = rank
    return entry


def rank_users(parquet_file_path, detail_limit=None, on_tail=None):
    """Return ``(detailed_rows, total_users)`` in rank order.

    ``detail_limit`` caps how many rows are kept in memory; every row after it
    is passed to ``on_tail(entry)`` (used to stream the compact long tail to
    disk) instead of being accumulated.
    """
    con = duckdb.connect()
    try:
        cursor = con.execute(
            f"""
            SELECT username, {", ".join(COMPONENTS)},
                   COALESCE(active_days, 0), COALESCE(window_days, 0),
                   ({_SCORE_SQL}) AS activity
            FROM read_parquet(?)
            WHERE username IS NOT NULL AND username <> ''
              AND ({_SCORE_SQL}) > 0
            ORDER BY activity DESC, COALESCE(merged_prs, 0) DESC, username ASC
            """,
            [parquet_file_path],
        )
        rows = []
        rank = 0
        while True:
            batch = cursor.fetchmany(_BATCH)
            if not batch:
                break
            for row in batch:
                rank += 1
                entry = _entry(row, rank)
                if detail_limit is None or rank <= detail_limit:
                    rows.append(entry)
                elif on_tail is not None:
                    on_tail(entry)
        return rows, rank
    finally:
        con.close()


def compute_rank_scores(parquet_file_path):
    """Return every ranked row (small inputs, tests and tooling)."""
    rows, _ = rank_users(parquet_file_path)
    return rows
