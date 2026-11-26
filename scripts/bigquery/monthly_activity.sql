-- RealGitRank: one month of public GitHub activity, aggregated per login.
--
-- This is the 12-month backfill for the v3 activity score. It counts the same
-- event types the daily gharchive.org ingest counts, so backfilled and daily
-- months are comparable:
--   pushes, merged PRs, closed issues, PR reviews, issue comments, releases.
--
-- PushEvent payloads no longer carry the commit list (GitHub trimmed them to
-- push_id/ref/head/before), so a push is one activity event here. GitHub's own
-- profile number counts commits instead, which is why the site shows that
-- number separately, from the GraphQL API, for the top 10,000 and on profile
-- lookup - it is never mixed into this score.
--
-- Cost note: `payload` is the only expensive column and it is needed just for
-- the PR/issue action. Run one month with a bytes cap first (the CLI prints the
-- dry-run estimate) and keep the default cap in config/pipeline.yaml.
-- {table} is `githubarchive.month.YYYYMM`, named directly: the dataset also
-- holds views, and BigQuery refuses a wildcard query over such a dataset.

WITH events AS (
  SELECT
    actor.login AS login,
    type,
    JSON_EXTRACT_SCALAR(payload, '$.action') AS action,
    JSON_EXTRACT_SCALAR(payload, '$.pull_request.merged') AS pr_merged
  FROM {table}
  WHERE actor.login IS NOT NULL
    AND type IN ('PushEvent', 'PullRequestEvent', 'IssuesEvent',
                 'PullRequestReviewEvent', 'IssueCommentEvent', 'ReleaseEvent')
),
per_user AS (
  SELECT
    login,
    COUNTIF(type = 'PushEvent') AS pushes,
    COUNTIF(type = 'PullRequestEvent'
            AND (action = 'merged'
                 OR (action = 'closed' AND pr_merged = 'true'))) AS merged_prs,
    COUNTIF(type = 'IssuesEvent' AND action = 'closed') AS closed_issues,
    COUNTIF(type = 'PullRequestReviewEvent') AS pr_reviews,
    COUNTIF(type = 'IssueCommentEvent') AS issue_comments,
    COUNTIF(type = 'ReleaseEvent') AS releases
  FROM events
  GROUP BY login
)
SELECT
  login,
  pushes,
  merged_prs,
  closed_issues,
  pr_reviews,
  issue_comments,
  releases
FROM per_user
WHERE pushes + merged_prs + closed_issues + pr_reviews + issue_comments + releases > 0
