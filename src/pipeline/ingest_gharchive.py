"""Real GitHub data ingest from gharchive.org (zero GCP, zero auth, free forever)."""
import os, sys, json, gzip, urllib.request, glob
from datetime import datetime, timedelta, timezone
import duckdb

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

BASE = "https://data.gharchive.org"


def download_hour(date_str, hour, out_dir):
    url = f"{BASE}/{date_str}-{hour}.json.gz"
    path = os.path.join(out_dir, f"{date_str}-{hour}.json.gz")
    if os.path.exists(path):
        return path
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "RealGitRank/1.0 (https://realgitrank.bharatdn.com)"})
        with urllib.request.urlopen(req, timeout=60) as resp, open(path, "wb") as out:
            out.write(resp.read())
        return path
    except Exception as e:
        print(f"  skip {hour}: {e}")
        return None


def ingest():
    # Target yesterday (UTC) — today's archive may be incomplete
    target = datetime.now(timezone.utc).date() - timedelta(days=1)
    date_str = target.strftime("%Y-%m-%d")
    print(f"Ingesting gharchive.org for {date_str} (24 hourly files)...")

    raw_dir = "data/gharchive_raw"
    os.makedirs(raw_dir, exist_ok=True)

    files = []
    for h in range(24):
        p = download_hour(date_str, h, raw_dir)
        if p:
            files.append(p)
            print(f"  downloaded hour {h}")
    print(f"Downloaded {len(files)}/24 hours")

    if not files:
        print("No files downloaded — aborting ingest")
        return False

    # Process with DuckDB: read gzipped JSON, aggregate per user
    con = duckdb.connect()
    # Create a view over all downloaded files
    file_list = ", ".join(f"'{f}'" for f in files)
    query = f"""
    SELECT
        actor.login AS username,
        COUNTIF(type = 'PushEvent') AS total_pushes,
        COUNTIF(type = 'PullRequestEvent'
                AND json_extract_string(payload, '$.action') = 'closed'
                AND json_extract_string(payload, '$.pull_request.merged') = 'true') AS merged_prs,
        COUNTIF(type = 'IssuesEvent' AND json_extract_string(payload, '$.action') = 'closed') AS closed_issues,
        COUNT(DISTINCT repo.name) AS active_repos_touched,
        COUNT(DISTINCT cm.message) AS unique_commit_messages,
        COUNT(DISTINCT EXTRACT(HOUR FROM created_at)) AS distinct_active_hours
    FROM read_json_auto([{file_list}], format='newline_delimited')
    LEFT JOIN (SELECT json_extract_string(commits.value, '$.message') AS message FROM UNNEST(json_extract(payload, '$.commits')) AS commits) AS cm ON TRUE
    WHERE actor.login NOT LIKE '%[bot]'
      AND actor.login NOT IN ('github-actions', 'dependabot', 'renovate[bot]')
    GROUP BY username
    HAVING total_pushes >= 5 OR merged_prs >= 1
    ORDER BY merged_prs DESC, total_pushes DESC
    LIMIT 200000
    """
    # Simpler approach: read each file and aggregate in Python via DuckDB per-file, then merge
    # DuckDB read_json_auto on multiple gz files can be memory-heavy; do per-file aggregation
    agg = {}
    for f in files:
        try:
            rel = con.execute(f"""
                SELECT actor.login AS username,
                    COUNTIF(type='PushEvent') AS tp,
                    COUNTIF(type='PullRequestEvent' AND json_extract_string(payload,'$.action')='closed' AND json_extract_string(payload,'$.pull_request.merged')='true') AS mp,
                    COUNTIF(type='IssuesEvent' AND json_extract_string(payload,'$.action')='closed') AS ci,
                    COUNT(DISTINCT repo.name) AS ar,
                    COUNT(DISTINCT EXTRACT(HOUR FROM created_at)) AS dah
                FROM read_json_auto('{f}')
                WHERE actor.login NOT LIKE '%[bot]'
                  AND actor.login NOT IN ('github-actions','dependabot','renovate[bot]')
                GROUP BY actor.login
            """)
            for row in rel.fetchall():
                u = row[0]
                if u not in agg:
                    agg[u] = [0,0,0,0,0]  # tp, mp, ci, ar, dah
                agg[u][0] += row[1] or 0
                agg[u][1] += row[2] or 0
                agg[u][2] += row[3] or 0
                agg[u][3] = max(agg[u][3], row[4] or 0)
                agg[u][4] = max(agg[u][4], row[5] or 0)
        except Exception as e:
            print(f"  error processing {f}: {e}")

    print(f"Aggregated {len(agg)} users")

    if not agg:
        return False

    # Enrich with country via simple heuristic (location unknown from archive; default to empty)
    # Build rows for scoring engine (needs same fields)
    rows = []
    for u, v in agg.items():
        tp, mp, ci, ar, dah = v
        if tp < 1 and mp < 1:
            continue
        rows.append({
            "username": u,
            "country": "",  # enriched later via GraphQL (optional)
            "primary_language": "Unknown",
            "total_pushes": tp,
            "merged_prs": mp,
            "merged_prs_external_weighted": mp * 1.5,
            "closed_issues": ci,
            "active_repos_touched": ar,
            "unique_commit_messages": tp,  # archive has no commit messages; skip entropy penalty
            "distinct_active_hours": dah if dah > 0 else 1,
            "avg_files_changed": 2.0,
            "push_hour_stddev": 4.0,
            "test_file_ratio": 0.0,
            "multi_file_commit_ratio": 0.3,
            "language_diversity": min(ar, 5),
            "stars_fraud_filtered": 0,
            "forks_fraud_filtered": 0,
            "dependents": 0,
            "active_shipping_weeks": min(52, max(1, tp // 5)),
        })

    # Filter to top 200k by merged_prs then total_pushes
    rows.sort(key=lambda x: (x["merged_prs"], x["total_pushes"]), reverse=True)
    rows = rows[:200000]

    import pyarrow as pa, pyarrow.parquet as pq
    os.makedirs("data", exist_ok=True)
    table = pa.Table.from_pylist(rows)
    pq.write_table(table, "data/raw.parquet")
    print(f"Saved {len(rows)} users to data/raw.parquet")
    return True


if __name__ == "__main__":
    ok = ingest()
    if not ok:
        sys.exit(1)