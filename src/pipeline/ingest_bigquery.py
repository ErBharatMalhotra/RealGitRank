"""BigQuery ingest: credit CI-pushed commits to the repository owner.

gharchive.org and ``githubarchive.day`` ship trimmed PushEvent payloads - only
``push_id``, ``ref``, ``head`` and ``before`` survive - so the commit author of a
push made by ``github-actions[bot]`` is not in the public feed at all (measured:
0 of 184,962 pushes on 2026-11-23 carried a commits array). Looking every head
SHA up on the GitHub API would need ~42,000 calls a day against a 5,000/hour
limit, which is not viable.

What the feed does carry is the repository, and a CI push into a repository is
that owner's pipeline. So this module adds a separate, clearly labelled signal:
every ``github-actions[bot]`` push into a user-owned repository (organisation
repos are skipped) gives the owner one *CI-attributed* push.

Deliberately kept apart from the main counters:

* ``ci_pushes`` is its own field and never folds into ``total_pushes``, so the
  commit-message entropy denominator is not diluted by commits whose messages
  were never published (that would trigger the bot penalty on CI users),
* it adds no active hours and no repositories, so a 24/7 workflow cannot look
  like round-the-clock human activity,
* it only makes an owner rankable when the free feed saw nothing else.

Cost: one query per day over three columns of one day table (~0.05 GiB scanned),
well inside the 1 TiB/month free tier. Missing credentials or any failure is a
no-op, so the free gharchive shard is written exactly as before.
"""
import gzip
import json
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

SQL_PATH = Path(__file__).resolve().parents[2] / "scripts" / "bigquery" / "daily_ingest.sql"
ACTIVITY_SQL_PATH = Path(__file__).resolve().parents[2] / "scripts" / "bigquery" / "monthly_activity.sql"
# One day scans ~0.05 GiB. 20 GiB only trips on a mistake (no date filter).
DEFAULT_MAX_BYTES_BILLED = 20 * 1024 ** 3
# One month of the activity query reads `payload` for PR/issue actions, so it is
# far heavier than the CI query: ~40-60 GiB measured per month. The cap stops a
# missing month filter from scanning the whole history.
DEFAULT_ACTIVITY_MAX_BYTES = 120 * 1024 ** 3


def credentials():
    """(project, service-account info) from the environment, or (None, None)."""
    project = (os.environ.get("GCP_PROJECT_ID") or "").strip()
    raw = (os.environ.get("GCP_SA_KEY") or "").strip()
    if not project or not raw:
        return None, None
    try:
        return project, json.loads(raw)
    except ValueError:
        print("BigQuery: GCP_SA_KEY is not valid JSON; CI attribution skipped")
        return None, None


def has_credentials():
    return credentials()[1] is not None


def build_sql(target_date):
    """SQL for one UTC day.

    A single day table is named directly: the dataset also exposes views (such as
    ``day.yesterday``) and BigQuery refuses a wildcard query over a dataset that
    contains views, so ``day.*`` cannot be used here.
    """
    suffix = f"{int(target_date.strftime('%Y%m%d')):08d}"
    sql = SQL_PATH.read_text(encoding="utf-8")
    return sql.replace("{table}", f"`githubarchive.day.{suffix}`")


def job_config(max_bytes=None):
    """Query config, or None when the BigQuery client library is unavailable."""
    try:
        from google.cloud import bigquery
    except ImportError:
        return None
    limit = max_bytes or os.environ.get("GCP_MAX_BYTES_BILLED") or DEFAULT_MAX_BYTES_BILLED
    return bigquery.QueryJobConfig(maximum_bytes_billed=int(limit))


def _client():
    project, info = credentials()
    if info is None:
        return None
    from google.cloud import bigquery
    from google.oauth2 import service_account

    creds = service_account.Credentials.from_service_account_info(info)
    return bigquery.Client(project=project, credentials=creds)


def fetch_ci_stats(target_date, client=None, max_bytes=None):
    """Per-owner CI pushes for one UTC day (already aggregated in SQL)."""
    if client is None:
        client = _client()
        if client is None:
            return []
    job = client.query(build_sql(target_date), job_config=job_config(max_bytes))
    return [dict(row) for row in job.result()]


def _module(name):
    """Import a sibling module whether run as a script or inside the package."""
    import importlib

    if __package__:
        return importlib.import_module(f"{__package__}.{name}")
    return importlib.import_module(name)


def merge_ci_stats(rows, agg, target_date):
    """Fold per-owner CI pushes into a day's accumulators; returns a summary."""
    is_bot_login = _module("ingest_gharchive").is_bot_login
    new_user = _module("ingest_gharchive").new_user

    summary = {"owners": 0, "pushes": 0, "repos": 0}
    for row in rows:
        login = (row.get("owner") or "").strip()
        if not login or is_bot_login(login):
            continue
        ci = int(row.get("ci_pushes") or 0)
        if ci < 1:
            continue

        user = agg.get(login)
        if user is None:
            user = new_user(login)
            agg[login] = user

        # Separate counter on purpose: never in total_pushes (entropy
        # denominator), hours or repos (automation heuristics).
        user["ci_pushes"] = int(user.get("ci_pushes") or 0) + ci
        user["dates"].add(target_date)

        summary["owners"] += 1
        summary["pushes"] += ci
        summary["repos"] += int(row.get("repos") or 0)
    return summary


def attribute_ci_pushes(target_date, agg, client=None):
    """Add CI-push attribution to one day of accumulators. Never raises."""
    if client is None and not has_credentials():
        return None
    try:
        rows = fetch_ci_stats(target_date, client=client)
    except Exception as exc:  # auth, quota, schema drift: keep the free ingest
        print(f"BigQuery CI attribution skipped: {exc}")
        return None
    if not rows:
        print("BigQuery CI attribution: no github-actions pushes returned")
        return None
    return merge_ci_stats(rows, agg, target_date)


def _read_shard(path):
    try:
        with gzip.open(path, "rt", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _write_shard(path, payload):
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump(payload, f, separators=(",", ":"))


def backfill_window(rolling_dir="data/rolling", days=None, client=None):
    """Add ``cp`` to shards ingested before this pass existed (idempotent).

    Each shard is queried at most once: a shard whose stats already carry
    ``ci_pushes`` is skipped, so the daily run stays at one query per day.
    """
    available_dates = _module("rolling").available_dates
    is_bot_login = _module("ingest_gharchive").is_bot_login
    if client is None:
        if not has_credentials():
            return []
        client = _client()

    dates = available_dates(rolling_dir)
    if days:
        dates = dates[-days:]

    done = []
    for date_str in dates:
        path = os.path.join(rolling_dir, f"{date_str}.json.gz")
        payload = _read_shard(path)
        if payload is None or "ci_pushes" in (payload.get("stats") or {}):
            continue
        try:
            rows = fetch_ci_stats(date.fromisoformat(date_str), client=client)
        except Exception as exc:
            print(f"  {date_str}: BigQuery skipped ({exc})")
            continue

        users = payload.setdefault("users", {})
        stats = payload.setdefault("stats", {})
        pushes = owners = 0
        for row in rows:
            login = (row.get("owner") or "").strip()
            ci = int(row.get("ci_pushes") or 0)
            if not login or ci < 1 or is_bot_login(login):
                continue
            record = users.setdefault(login, {"p": 0})
            record["cp"] = int(record.get("cp") or 0) + ci
            pushes += ci
            owners += 1
        stats["ci_pushes"] = pushes
        stats["ci_owners"] = owners
        _write_shard(path, payload)
        done.append((date_str, owners, pushes))
        print(f"  {date_str}: +{pushes} CI pushes for {owners} owners")
    return done


def build_activity_sql(month):
    """SQL for one complete month (``YYYY-MM``) of per-login activity counts."""
    table = "githubarchive.month." + str(month).replace("-", "")
    return ACTIVITY_SQL_PATH.read_text(encoding="utf-8").replace("{table}", f"`{table}`")


def fetch_month_activity(month, client=None, max_bytes=None):
    """Per-login activity counts for one month, already aggregated in SQL."""
    if client is None:
        client = _client()
        if client is None:
            return []
    limit = max_bytes or os.environ.get("GCP_ACTIVITY_MAX_BYTES") or DEFAULT_ACTIVITY_MAX_BYTES
    config = job_config(limit)
    if config is None:
        return []
    job = client.query(build_activity_sql(month), job_config=config)
    return [dict(row) for row in job.result()]


def backfill_activity(months, client=None, monthly_dir=None, max_bytes=None):
    """Write complete past months of activity counts into the monthly store.

    Idempotent: a month already backfilled from BigQuery is skipped, and
    ``activity.merge_month`` replaces whatever it finds for that month.
    """
    activity = _module("activity")
    monthly_dir = monthly_dir or activity.MONTHLY_DIR
    if client is None:
        if not has_credentials():
            return []
        client = _client()

    done = []
    for month in months:
        existing = activity.load_month(month, monthly_dir)
        if existing and existing.get("source") == "bigquery":
            continue
        try:
            rows = fetch_month_activity(month, client=client, max_bytes=max_bytes)
        except Exception as exc:
            print(f"  {month}: activity backfill skipped ({exc})")
            continue
        counters = {}
        for row in rows:
            login = (row.get("login") or "").strip()
            if not login:
                continue
            counters[login] = {
                "p": int(row.get("pushes") or 0),
                "m": int(row.get("merged_prs") or 0),
                "i": int(row.get("closed_issues") or 0),
                "rv": int(row.get("pr_reviews") or 0),
                "ic": int(row.get("issue_comments") or 0),
                "rel": int(row.get("releases") or 0),
            }
        if not counters:
            print(f"  {month}: BigQuery returned no activity rows")
            continue
        path, users = activity.merge_month(month, counters, monthly_dir)
        done.append((month, users))
        print(f"  {month}: {users} users written to {path}")
    return done


def complete_months(count=12, end_date=None):
    """The newest `count` complete UTC months before the running month."""
    today = end_date or (datetime.now(timezone.utc).date() - timedelta(days=1))
    year, month = today.year, today.month - 1
    months = []
    for _ in range(count):
        if month == 0:
            month = 12
            year -= 1
        months.append(f"{year:04d}-{month:02d}")
        month -= 1
    return sorted(months)


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(description="BigQuery CI attribution / activity backfill")
    parser.add_argument("--date", help="UTC date to query (YYYY-MM-DD); defaults to yesterday")
    parser.add_argument("--estimate", action="store_true",
                        help="dry run: report bytes scanned instead of running")
    parser.add_argument("--backfill", action="store_true",
                        help="add CI pushes to rolling shards that lack them (idempotent)")
    parser.add_argument("--days", type=int, default=None,
                        help="limit --backfill to the newest N shards")
    parser.add_argument("--backfill-activity", type=int, default=None, metavar="MONTHS",
                        help="write the newest N complete months into data/activity (idempotent)")
    parser.add_argument("--months", default=None,
                        help="comma-separated YYYY-MM list for the activity backfill")
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

    if args.backfill_activity or args.months:
        months = ([m.strip() for m in args.months.split(",") if m.strip()]
                  if args.months else complete_months(args.backfill_activity or 12,
                                                      end_date=datetime.strptime(args.date, "%Y-%m-%d").date()
                                                      if args.date else None))
        done = backfill_activity(months, client=client)
        print(f"activity backfill: {len(done)} months written")
        return 0

    if args.backfill:
        done = backfill_window(days=args.days, client=client)
        print(f"backfilled {len(done)} shards")
        return 0

    if args.estimate:
        config = bigquery.QueryJobConfig(dry_run=True, use_query_cache=False)
        job = client.query(build_sql(target), job_config=config)
        gib = (job.total_bytes_processed or 0) / 1024 ** 3
        print(f"{target}: this query would scan {gib:.2f} GiB (free tier: 1024 GiB/month)")
        return 0

    rows = fetch_ci_stats(target, client=client)
    pushes = sum(int(r.get("ci_pushes") or 0) for r in rows)
    print(f"{target}: {len(rows)} owners, {pushes} CI-attributed pushes")
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
