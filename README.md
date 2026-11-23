# 🚀 RealGitRank (`realgitrank.bharatdn.com`)
> A 30-day engineering-impact ranking for GitHub, built from real public events.

**Methodology status (v2):** scores use a rolling 30-day window; each pillar is
percentile-ranked only where it was observed, and missing pillars are excluded from
the weighted score. Pillar coverage, bot-evidence coverage and weekly GitHub spot
checks are published at `/methodology/` and in `data/coverage.json`. There is no
mock-data fallback: if the real ingest fails, the run fails.

[![RealGitRank](https://img.shields.io/badge/RealGitRank-Engineering%20Impact%20Index-blue?style=flat-square)](#)
[![Bot-Immune](https://img.shields.io/badge/Bot%20Immunity-Entropy%20Filtered-emerald?style=flat-square)](#)
[![Hosted on](https://img.shields.io/badge/Host-GitHub%20Pages%20(free)-black?style=flat-square)](#)

---

## ⚡ Quick Start & Development

### 1. Requirements
- Python 3.12+ (Data Ingestion & Scoring Engine)
- Node.js 20+ (Astro Frontend, static build)
- Google Cloud Service Account (BigQuery Free Tier) — optional, fallback exists

### 2. Directory Structure
```
D:\rank\
├── SPEC.md              # Master Product Specification
├── DATA_PIPELINE.md     # BigQuery & DuckDB Ingestion Blueprint
├── RUNBOOK.md           # Ops runbook (free-for-decades)
├── README.md            # Project Overview & Setup
├── .env.example         # Env vars template
├── requirements.txt     # Python deps (pinned)
├── config/              # Pipeline & scoring weight YAML configs
├── data/                # Static snapshots (Parquet/JSON/SQLite) — committed to git
├── scripts/             # BigQuery queries & cron scripts
├── src/                 # Python analytics & ranking algorithms
└── web/                 # Astro static frontend (GitHub Pages)
```

### 3. Local dev
```bash
pip install -r requirements.txt
cd web && npm install && npm run dev
```

---

## 🎯 Key Milestones
1. **Ingest Engine:** BigQuery public dataset ingestion + Parquet output + fallback chain.
2. **Analysis:** Entropy & Circadian rhythm bot-detection heuristics in DuckDB (vectorized).
3. **Web UI:** Country & language leaderboard tables with instant search (static, GitHub Pages). Country/language filters only cover enriched profiles; current coverage is shown on the country page.
4. **Badges:** Static SVG endpoint for developer README profiles (`/badges/[username].svg`). The badge marks "bot-checked" only when bot evidence was actually observed.
5. **Deployment:** Production release to `realgitrank.bharatdn.com` (GitHub Pages, free forever).

---

## 🆓 Free-for-Decades
- Hosting: GitHub Pages (free, unlimited bandwidth, custom domain, free HTTPS)
- Compute: GitHub Actions (public repo = unlimited minutes)
- Data store: git repo itself (versioned, free CDN)
- No Cloudflare, no paid infra, no paid monitoring. See RUNBOOK.md.