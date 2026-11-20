# 🌍 RealGitRank (`realgitrank.bharatdn.com`)
**The World's Best Developer Reputation & Engineering Impact Engine**
*Anti-Bot GitHub Leaderboard & Engineering Impact Index*

---

## 🚀 Product Vision
`realgitrank.bharatdn.com` is designed to supersede traditional contribution counters (like `committers.top`) by introducing **bot immunity**, **merged PR validation**, and **code quality scoring**.

> **"Commit count measures typing speed. RealGitRank measures engineering impact."**

---

## 🏗️ Technical Architecture
```
┌─────────────────────────────────────────┐
│   GH Archive (Google BigQuery)          │  ← Free 1 TB/mo query tier (primary)
│   Push, PR, Issue, Watch Events         │
└────────────────────┬────────────────────┘
                     │  Daily partition (2-4 GB) + fallback: gharchive.org .json.gz
                     ▼
┌─────────────────────────────────────────┐
│   GitHub Actions (public repo)          │  ← Free compute, unlimited minutes
│   DuckDB scoring + bot filter + gates   │
└────────────────────┬────────────────────┘
                     │  Commit snapshots (git = data store + CDN)
                     ▼
┌─────────────────────────────────────────┐
│   GitHub Pages (static)                 │  ← Free, unlimited bandwidth, custom domain
│   Leaderboards + profiles + badges      │
└─────────────────────────────────────────┘
```
Hosting is **100% static on GitHub Pages** (free forever). No Cloudflare, no paid infra. Data lives in git (versioned, free backup, trivial rollback).

---

## 📊 The Rank Scoring Formula (0–1000 Index)

$$\text{Rank Score} = \Big[ (S_{\text{PR}} \times 0.35) + (S_{\text{Quality}} \times 0.25) + (S_{\text{Impact}} \times 0.25) + (S_{\text{Consistency}} \times 0.15) \Big] \times (1 - \text{BotPenalty})$$

| Pillar | Metric Tracked | Weight | Why It Can't Be Gamed |
|---|---|---|---|
| **$S_{\text{PR}}$ (Delivery)** | `PullRequestEvent` (merged = true) in internal and external repositories. | **35%** | Opening fake PRs doesn't work; only **merged code** counts. External PRs merged by *others* have higher multipliers. Self-merged PRs in owned repos are discounted. |
| **$S_{\text{Quality}}$ (Code Health)** | Test presence (`tests/`, `spec/`), multi-file commits, languages used, CI config presence. | **25%** | Empty markdown updates and 1-line churn get zero weight. Proxied by `active_repos_touched` is NOT allowed — must measure test ratio, diff size, language diversity. |
| **$S_{\text{Impact}}$ (Adoption)** | Organic `WatchEvent` (stars), `ForkEvent`, and downstream package dependents. | **25%** | Stargazers filtered against known star-cartel rings (graph-based). |
| **$S_{\text{Consistency}}$ (Cadence)** | Active shipping weeks per year, std-dev of push hours (human circadian cycle). | **15%** | 24/7 round-the-clock bots get flagged; healthy human workflows score highest. |

All four pillars are computed in DuckDB (vectorized SQL), not pandas row-wise. Scores are percentile-normalized (z-score) rather than hard-capped at 1000.

---

## 🛡️ The Bot & Farm Neutralization Engine

Every account is evaluated against a **Bot Heuristic Score ($H$)**:
1. **Message Entropy Check:** Less than 10% unique commit messages (`"Auto commit #123"`, `"update"`, etc.) → **+0.50 Penalty**.
2. **Circadian Rhythm Check:** Pushing uniformly across 24 hours without sleeping pattern → **+0.30 Penalty**.
3. **Payload Delta Check:** Single-line repeated churn on 1 file (`avg_files_changed < 1.05`) → **+0.40 Penalty**.

Thresholds:
- $\text{BotPenalty} < 0.05$ → **Verified Human Engineer** badge.
- $\text{BotPenalty} \ge 0.70$ → placed in **Autonomous AI & Bots Leaderboard**, keeping the **Human Engineer Leaderboard** pristine.

Future (P2): ML-based classifier on account age, follower graph, PR review interactions, commit timezones, code review quality. Heuristic fallback retained.

---

## 🌟 Killer Features

1. **"Verified Human Engineer" Badge:** Cryptographic & behavioral seal for developers with $<0.05$ bot penalty.
2. **Multi-Dimensional Leaderboards:** Filter by Country, Tech Stack (Rust, TypeScript, Python, AI/ML), and Role.
3. **Dynamic Live SVG README Badges (GitHub Pages):** `/badges/[username].svg` — pre-generated static SVGs, regenerated nightly, served from GitHub Pages (no serverless).
4. **"Developer Wrapped":** Monthly/annual shareable engineering report card.
5. **CLI Tool (`npx realgitrank --user <username>`):** Terminal-based rank checker.

---

## 🛠️ Implementation Roadmap

- **Phase 1:** BigQuery Pipeline & Data Extraction + spec reconcile + SQL fix + Actions workflow + static site skeleton + runbook. ✅ (this PR)
- **Phase 2:** DuckDB Scoring & Bot Detection Engine (vectorized).
- **Phase 3:** Frontend & Leaderboard Interface (Astro static, GitHub Pages).
- **Phase 4:** Badges Engine, SEO & Launch (edge SVG → static SVG, global sitemaps).

---

## 🆓 Free-for-Decades Constraints
- Infra cost must remain **$0**. Hosting = GitHub Pages, compute = GitHub Actions (public repo = unlimited minutes), data store = git repo.
- No Cloudflare, no paid monitoring, no paid DBs.
- Every component must degrade gracefully (fallback data source, stale-data banner, auto-issue on failure).

---

*Created for deployment at `realgitrank.bharatdn.com`.*