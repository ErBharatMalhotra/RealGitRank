"""BigQuery ingest: attribute bot-pushed commits back to their authors.

The free gharchive.org feed ships trimmed PushEvent payloads, so a commit that
reaches a repository through ``github-actions`` (a bot actor) is dropped, and
the person who actually wrote it never appears in the ranking. The
``githubarchive.day`` public dataset keeps the full payload, so one
date-partitioned query per day recovers ``payload.commits[].author.username``
for those pushes.

This module augments the free gharchive ingest, it never replaces it:

* pushes are added only when the push actor was a bot - human actors are already
  counted by the free feed, so nothing is counted twice,
* commit messages and test-signal evidence are added for every author, which
  turns "unknown" entropy into a measured value instead of NaN,
* every failure (missing credentials, quota, schema change) is logged and the
  run continues on the plain gharchive shard.

Cost: one partition-pruned query per day (a few GB scanned; the first TiB each
month is free) with ``maximum_bytes_billed`` set, and the aggregation happens
inside BigQuery, so only a few thousand per-author rows are downloaded.

``python -m src.pipeline.ingest_bigquery --estimate`` prints the bytes a day
would scan without running the query.
"""
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

SQL_PATH = Path(__file__).resolve().parents[2] / "scripts" / "bigquery" / "daily_ingest.sql"
# One day scans ~2-4 GB. 20 GiB only trips on a mistake (missing partition
# filter), which is exactly when the free tier should stop us.
DEFAULT_MAX_BYTES_BILLED = 20 * 1024 ** 3


def credentials():
    """(project, service-account info) from the environment, or (None, None)."""
    project = (os.environ.get("GCP_PROJECT_ID") or "").strip()
    raw = (os.environ.get("GCP_SA_KEY") or "").strip()
    if not project or not raw:
        return None, None
    try:
        return project, json.loads(raw)
    except ValueError:
        print("BigQuery: GCP_SA_KEY is not valid JSON; attribution skipped")
        return None, None


def has_credentials():
    return credentials()[1] is not None


def payload_json_expression():
    """``payload`` as JSON text, whichever type the dataset stores the column as.

    ``githubarchive.day`` types payload as STRING; a JSON/STRUCT column needs
    TO_JSON_STRING. TO_JSON_STRING on a STRING quotes and escapes it, so that
    case is unwrapped back to the raw JSON text before UNNEST sees it.
    """
    return (
        "IF(STARTS_WITH(TO_JSON_STRING(payload), '\"'), "
        "JSON_EXTRACT_SCALAR(TO_JSON_STRING(payload), '$'), "
        "TO_JSON_STRING(payload))"
    )


def build_sql():
    sql = SQL_PATH.read_text(encoding="utf-8")
    return sql.replace("{payload}", payload_json_expression())


def job_config(suffix, max_bytes=None):
    """Query config, or None when the BigQuery client library is unavailable."""
    try:
        from google.cloud import bigquery
    except ImportError:
        return None
    limit = max_bytes or os.environ.get("GCP_MAX_BYTES_BILLED") or DEFAULT_MAX_BYTES_BILLED
    return bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("suffix", "STRING", suffix)],
        maximum_bytes_billed=int(limit),
    )


def _client():
    project, info = credentials()
    if info is None:
        return None
    from google.cloud import bigquery
    from google.oauth2 import service_account

    creds = service_account.Credentials.from_service_account_info(info)
    return bigquery.Client(project=project, credentials=creds)


def fetch_author_stats(target_date, client=None, max_bytes=None):
    """Per-author rows for one UTC day of githubarchive.day (already aggregated)."""
    if client is None:
        client = _client()
        if client is None:
            return []
    job = client.query(build_sql(), job_config=job_config(target_date.strftime("%Y%m%d"), max_bytes))
    return [dict(row) for row in job.result()]


def merge_author_stats(rows, agg, target_date):
    """Fold per-author rows into a day's accumulators and return a summary."""
    from ingest_gharchive import is_bot_login, new_user

    summary = {"authors": 0, "pushes": 0, "messages": 0, "test_hits": 0}
    for row in rows:
        login = (row.get("author_login") or "").strip()
        if not login or is_bot_login(login):
            continue
        bot_pushes = int(row.get("bot_pushes") or 0)
        messages = int(row.get("messages") or 0)
        commits = int(row.get("commits") or 0)
        test_hits = int(row.get("test_hits") or 0)
        if not (bot_pushes or messages or commits):
            continue

        user = agg.get(login)
        if user is None:
            user = new_user(login)
            agg[login] = user

        user["total_pushes"] += bot_pushes
        # Message *counts* only: BigQuery already deduplicated them and the free
        # feed carried none, so the set-based message total still adds up.
        user["extra_messages"] = int(user.get("extra_messages") or 0) + messages
        user["evidence"] += commits
        user["test_hits"] += test_hits
        user["dates"].add(target_date)
        if bot_pushes:
            # Hours/repos only for the pushes the free feed lost; for human
            # actors they are already in the shard and would double count.
            user["hours"].extend(int(h) for h in (row.get("hours") or ()) if h is not None)
            user["repos"].update(r for r in (row.get("repos") or ()) if r)
        elif not user["repos"]:
            user["repos"].update(r for r in (row.get("repos") or ()) if r)

        summary["authors"] += 1
        summary["pushes"] += bot_pushes
        summary["messages"] += messages
        summary["test_hits"] += test_hits
    return summary


def augment_agg(target_date, agg, client=None):
    """Add BigQuery-only attribution to one day of accumulators. Never raises."""
    if client is None and not has_credentials():
        return None
    try:
        rows = fetch_author_stats(target_date, client=client)
    except Exception as exc:  # auth, quota, schema drift: keep the free ingest
        print(f"BigQuery attribution skipped: {exc}")
        return None
    if not rows:
        print("BigQuery attribution: no commit authors returned (trimmed payloads?)")
        return None
    return merge_author_stats(rows, agg, target_date)


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(description="BigQuery author attribution for one UTC day")
    parser.add_argument("--date", help="UTC date to query (YYYY-MM-DD); defaults to yesterday")
    parser.add_argument("--estimate", action="store_true",
                        help="dry run: report bytes scanned instead of running")
    args = parser.parse_args(argv)

    target = (datetime.strptime(args.date, "%Y-%m-%d").date() if args.date
              else datetime.now(timezone.utc).date() - timedelta(days=1))
    project, info = credentials()
    if info is None:
        print("No GCP_PROJECT_ID / GCP_SA_KEY set - nothing to do")
        return 2

    from google.cloud import bigquery
    from google.oauth2 import service_account

    client = bigquery.Client(
        project=project,
        credentials=service_account.Credentials.from_service_account_info(info),
    )

    if args.estimate:
        config = bigquery.QueryJobConfig(
            dry_run=True,
            use_query_cache=False,
            query_parameters=[
                bigquery.ScalarQueryParameter("suffix", "STRING", target.strftime("%Y%m%d"))
            ],
        )
        job = client.query(build_sql(), job_config=config)
        gib = (job.total_bytes_processed or 0) / 1024 ** 3
        print(f"{target}: this query would scan {gib:.2f} GiB (free tier: 1024 GiB/month)")
        return 0

    rows = fetch_author_stats(target, client=client)
    bot_pushes = sum(int(r.get("bot_pushes") or 0) for r in rows)
    print(f"{target}: {len(rows)} commit authors, {bot_pushes} bot-pushed commit sets")
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
