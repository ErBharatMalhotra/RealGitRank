# Data Pipeline Architecture (`realgitrank.bharatdn.com`)

## 1. Overview
The data pipeline runs on a scheduled cadence (Daily, 02:00 UTC) using **GH Archive on Google BigQuery** (free 1 TB/mo) for high-volume event aggregation, processed locally via **DuckDB**, enriched selectively via **GitHub GraphQL API**, and outputted as static Parquet/JSON/SQLite snapshots committed to git. Hosting is GitHub Pages (free, static).

Fallback chain (self-healing): BigQuery → gharchive.org direct `.json.gz` download → reuse last good snapshot + stale banner + auto GitHub Issue.

---

## 2. Step 1: BigQuery Daily Ingestion Query
Runs daily via GitHub Actions using a Google Cloud Service Account (free tier). Partition-pruned to ~2-4 GB/day.

```sql
-- scripts/bigquery/daily_ingest.sql
DECLARE target_date DATE DEFAULT DATE_SUB(CURRENT_DATE(), INTERVAL 1 DAY);

SELECT
    actor.login AS username,
    COUNTIF(type = 'PushEvent') AS total_pushes,
    COUNTIF(type = 'PullRequestEvent'
            AND JSON_EXTRACT_SCALAR(payload, '$.action') = 'closed'
            AND JSON_EXTRACT_SCALAR(payload, '$.pull_request.merged') = 'true') AS merged_prs,
    COUNTIF(type = 'IssuesEvent' AND JSON_EXTRACT_SCALAR(payload, '$.action') = 'closed') AS closed_issues,
    COUNT(DISTINCT repo.name) AS active_repos_touched,
    COUNT(DISTINCT cm.message) AS unique_commit_messages,
    COUNT(DISTINCT EXTRACT(HOUR FROM created_at)) AS distinct_active_hours,
    STDDEV(EXTRACT(HOUR FROM created_at)) AS push_hour_stddev
FROM `githubarchive.day.*`
LEFT JOIN UNNEST(JSON_EXTRACT_ARRAY(payload, '$.commits')) AS c ON TRUE
LEFT JOIN UNNEST([JSON_EXTRACT_SCALAR(c, '$.message')]) AS cm ON TRUE
WHERE _TABLE_SUFFIX = FORMAT_DATE('%Y%m%d', target_date)
  AND actor.login NOT LIKE '%[bot]'
  AND actor.login NOT IN ('github-actions', 'dependabot', 'renovate[bot]')
GROUP BY username
HAVING total_pushes >= 5 OR merged_prs >= 1
ORDER BY merged_prs DESC, total_pushes DESC
LIMIT 200000;
```

Notes:
- `_TABLE_SUFFIX` date filter → scans ~2-4 GB, not whole archive. Monthly total ~60-120 GB (under 1 TB).
- `UNNEST(payload.commits)` → real entropy signal (was only first commit).
- Candidate pool 200k → score + percentile cut (was 50k biased toward high-volume).
- Incremental: append daily delta; recompute scores only for touched users + top N.

---

## 3. Step 2: DuckDB Analytical Engine (Python)
Vectorized SQL, not pandas row-wise.

```python
# src/pipeline/scoring_engine.py
import duckdb
import numpy as np

def compute_rank_scores(parquet_file_path: str):
    con = duckdb.connect()
    df = con.execute(f"SELECT * FROM read_parquet('{parquet_file_path}')").fetchdf()

    # 1. Bot penalty (vectorized)
    df['entropy_ratio'] = df['unique_commit_messages'] / df['total_pushes'].clip(lower=1)
    df['bot_penalty'] = 0.0
    df.loc[df['entropy_ratio'] < 0.10, 'bot_penalty'] += 0.50
    df.loc[df['distinct_active_hours'] >= 23, 'bot_penalty'] += 0.30
    df.loc[df['avg_files_changed'] < 1.05, 'bot_penalty'] += 0.40
    df['bot_penalty'] = df['bot_penalty'].clip(0, 1.0)

    # 2. Pillars (0-1000, percentile-normalized)
    df['s_pr']            = np.log1p(df['merged_prs_external_weighted']) * 120
    df['s_quality']       = (np.log1p(df['test_file_ratio']) * 60
                             + np.log1p(df['multi_file_commit_ratio']) * 40
                             + np.log1p(df['language_diversity']) * 30)
    df['s_impact']        = (np.log1p(df['stars_fraud_filtered']) * 40
                             + np.log1p(df['forks_fraud_filtered']) * 30
                             + np.log1p(df['dependents']) * 30)
    df['s_consistency']   = (np.log1p(df['active_shipping_weeks']) * 50
                             + (1 / (1 + df['push_hour_stddev'])) * 50)

    # 3. Composite (weights MUST match SPEC.md)
    df['raw_score'] = (df['s_pr'] * 0.35
                      + df['s_quality'] * 0.25
                      + df['s_impact'] * 0.25
                      + df['s_consistency'] * 0.15)
    df['rank_score'] = (df['raw_score'] * (1 - df['bot_penalty'])).clip(0, 1000).round(1)

    # Segregate humans vs bots
    df['is_human_verified'] = df['bot_penalty'] < 0.05
    df['is_bot_classified'] = df['bot_penalty'] >= 0.70

    return df
```

---

## 4. Step 3: Selective GraphQL Enrichment
For top 10,000 active developers, enrich via GitHub GraphQL API (batches of 100, cached, incremental):
- User Country / Location (for country-level filtering)
- Bio & Social links (Twitter, LinkedIn, Blog)
- Avatar URL
- Sponsoring status & Organizations

Rate limit: 5,000 points/hour → 10k users ≈ 10h. Run in background; partial enrichment is acceptable (missing avatar ≠ broken page).

---

## 5. Output
Versioned Parquet snapshots + SQLite for edge reads + static JSON for GitHub Pages. Badges are pre-generated SVGs committed to repo (`web/public/badges/[username].svg`).

---

## 6. Data Quality Gates (fail the build if)
- Row count < 80% of 30-day median
- Null rate on `username` > 0%
- Score distribution shifted > 3σ
- Bot-classification rate jumped > 2× (heuristic regression)