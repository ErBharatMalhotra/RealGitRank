-- RealGitRank: CI-push attribution (optional, BigQuery).
--
-- PushEvent payloads in the public feed carry only push_id/ref/head/before, so
-- the commit author of a github-actions push is not published anywhere. What is
-- published is the repository, and a CI push into a repo is that owner's own
-- pipeline, so each owner gets one *CI-attributed* push per bot push into a
-- user-owned repository.
--
-- Organisation repositories are skipped (`org IS NULL`): the owner there is an
-- org, not a ranked developer, and CI into an org repo is not one person's work.
-- Aggregation happens here, so only a few thousand rows are downloaded.
--
-- Cost: one day table, three columns, ~0.05 GiB scanned - free under the
-- 1 TiB/month free tier. {table} is `githubarchive.day.YYYYMMDD` (the dataset
-- also holds views, and BigQuery refuses wildcard queries over datasets with
-- views, so the day table is named directly).

SELECT
  SPLIT(repo.name, '/')[SAFE_OFFSET(0)] AS owner,
  COUNT(*) AS ci_pushes,
  COUNT(DISTINCT repo.name) AS repos,
  ARRAY_AGG(DISTINCT repo.name LIMIT 20) AS repo_names
FROM {table}
WHERE type = 'PushEvent'
  AND actor.login IN ('github-actions[bot]', 'github-actions')
  AND org IS NULL
GROUP BY owner
HAVING owner IS NOT NULL AND owner != ''
