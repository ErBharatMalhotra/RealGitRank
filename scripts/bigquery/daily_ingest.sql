-- RealGitRank daily ingest (GitHub Actions, 02:00 UTC)
-- Partition-pruned to ~2-4 GB/day. Free tier safe (~60-120 GB/month).
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