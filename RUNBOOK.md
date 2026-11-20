# RealGitRank — Runbook (free for decades)

## Re-bootstrap from scratch (2046-ready)
1. Clone repo (public).
2. `pip install -r requirements.txt` (Python 3.12 LTS).
3. Create GCP service account with BigQuery read access; store key in GitHub Secrets as `GCP_SA_KEY`, project id as `GCP_PROJECT_ID`.
4. Enable GitHub Pages → Source = GitHub Actions. Set custom domain `realgitrank.bharatdn.com` in repo settings.
5. Add DNS CNAME: `realgitrank.bharatdn.com` → `<user>.github.io`.
6. Run workflow manually once (`workflow_dispatch`) to seed data.

## Secrets rotation (yearly)
- GCP service account key: create new key, update `GCP_SA_KEY` secret, delete old key. SA keys don't expire, but rotate yearly.

## Domain renewal
- `bharatdn.com` renewal is the only paid item. Set auto-renew at registrar. Calendar reminder 30 days before.

## If pipeline fails
- Auto-opens GitHub Issue tagged `pipeline-failure` with run link.
- Fallback chain: BigQuery → gharchive.org direct download → last good snapshot + stale banner.

## If GitHub Pages breaks
- Static files deploy to any host (Netlify/Vercel/S3) in minutes — zero lock-in. Just upload `web/dist/`.

## Dependency policy
- Pin LTS versions (Python 3.12, Node 20). Dependabot security-only. Never auto-upgrade majors.

## Health check
- `/status.json` committed each run with `last_run_utc` + `data_age_hours`. UptimeRobot pings it (free).