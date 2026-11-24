-- RealGitRank: author attribution for bot-pushed commits (optional, BigQuery).
--
-- gharchive.org trims PushEvent payloads, so a commit pushed by github-actions
-- never reaches the person who wrote it. This query reads one date partition of
-- githubarchive.day (full payloads) and returns per-author aggregates: how many
-- of their pushes a bot made, plus their commit-message evidence. All the
-- aggregation happens here, so only a few thousand rows are downloaded.
--
-- Cost: one partition-pruned day, a few GB scanned, free under the 1 TiB/month
-- free tier. {payload} is replaced at run time by a JSON-string expression for
-- whichever type the dataset uses for the payload column
-- (see ingest_bigquery.payload_json_expression).

WITH commits AS (
  SELECT
    actor.login AS actor_login,
    repo.name AS repo_name,
    created_at,
    CONCAT(CAST(actor.id AS STRING), '|', repo.name, '|',
           FORMAT_TIMESTAMP('%Y-%m-%dT%H:%M:%SZ', created_at)) AS push_key,
    JSON_EXTRACT_SCALAR(commit_json, '$.author.username') AS author_login,
    JSON_EXTRACT_SCALAR(commit_json, '$.message') AS message
  FROM `githubarchive.day.*`,
    UNNEST(JSON_EXTRACT_ARRAY({payload}, '$.commits')) AS commit_json
  WHERE _TABLE_SUFFIX = @suffix
    AND type = 'PushEvent'
)
SELECT
  author_login,
  COUNT(DISTINCT push_key) AS pushes,
  COUNT(DISTINCT IF(
      REGEXP_CONTAINS(actor_login, r'\[bot\]$')
      OR actor_login IN ('github-actions', 'dependabot', 'renovate'),
      push_key, NULL)) AS bot_pushes,
  COUNT(DISTINCT message) AS messages,
  COUNT(*) AS commits,
  COUNTIF(REGEXP_CONTAINS(LOWER(message),
      r'test|spec|pytest|jest|unittest|e2e|coverage|fixture')) AS test_hits,
  ARRAY_AGG(DISTINCT EXTRACT(HOUR FROM created_at)) AS hours,
  ARRAY_AGG(DISTINCT repo_name LIMIT 20) AS repos
FROM commits
WHERE author_login IS NOT NULL AND author_login != ''
GROUP BY author_login
