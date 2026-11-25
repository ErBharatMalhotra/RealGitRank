# RealGitRank badge Worker

Per-user README badge for **every listed developer**, not just the global top
1,000 that the static site can pre-build.

```
GET /badges/<username>.svg   -> SVG badge
GET /                        -> short usage page
```

## Why a Worker

`realgitrank.bharatdn.com` is GitHub Pages: static files only, so
`src/pipeline/badges.py` ships SVGs for the top 1,000 and everyone else 404s into
`/badges/default.svg`, which says "Not Ranked · Join to get scored" even for a
ranked account. Generating 1.1M files is not viable; rendering the badge on
request costs ~nothing.

## Data source

No new database. The Worker reads the published search buckets
`https://realgitrank.bharatdn.com/data/search/<first-char>.json`
(`[username, rank, score]`, one bucket per starting character) and keeps the
parsed bucket in isolate memory (3 buckets, 6h) plus the Cloudflare response
cache (1h). A cold request costs one bucket download; every later request for the
same bucket is served from memory or cache.

The search buckets themselves are a pipeline artifact, so a Worker restart is
always consistent with the latest snapshot — there is nothing to re-sync.

## States (nothing is judged)

| State | When | Badge shows |
| --- | --- | --- |
| ranked | `score > 0` | score, `impact index · #rank` |
| listed | row present, `score = 0` (no public push or merged PR in the window) | `LISTED`, no rank claim |
| not found | username absent from the snapshot | `—`, `NOT FOUND` |

## Local checks

```bash
node badges-worker/test.mjs        # stubs fetch/cache, no deps, no network
```

## Deploy

`badges-worker/sites.json` is the deployment manifest (static-assets free Worker).
