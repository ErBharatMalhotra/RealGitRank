"""Generate the static machine-readable layer served by GitHub Pages.

Outputs (all plain files, no server):
  web/public/api/leaderboard.json   - every ranked developer, compact records
  web/public/api/user/<name>.json   - one record per ranked developer
  web/public/llms.txt               - short guide for LLM/agent consumers
"""
import json
import os

SITE = "https://realgitrank.bharatdn.com"
LEADERBOARD_PATH = "data/leaderboard.json"
STATUS_PATH = "data/status.json"
PROFILES_DIR = "data/profiles"
HISTORY_DIR = "data/history"
OUT_DIR = "web/public/api"
LLMS_PATH = "web/public/llms.txt"
COMMUNITY_PROFILE_LIMIT = 1000  # badges/profiles are pre-rendered for the global top 1,000

WEIGHTS = {
    "delivery": 0.35,
    "code_health": 0.25,
    "adoption": 0.25,
    "consistency": 0.15,
}

TIER_BANDS = [
    {"tier": "Elite", "min_percentile": 99},
    {"tier": "Diamond", "min_percentile": 95},
    {"tier": "Platinum", "min_percentile": 90},
    {"tier": "Gold", "min_percentile": 75},
    {"tier": "Silver", "min_percentile": 50},
    {"tier": "Bronze", "min_percentile": 0},
]

# Documented contract of the compact leaderboard records.
CORE_FIELDS = (
    "username",
    "rank",
    "rank_score",
    "tier",
    "percentile",
    "country",
    "primary_language",
    "merged_prs",
    "is_human_verified",
    "is_bot_classified",
    "bot_penalty",
    "rank_delta",
    "score_delta",
)

# Only present when the pipeline produced them (top-1,000 profiles + fresh metrics).
EXTRA_FIELDS = (
    "company",
    "location",
    "pr_reviews",
    "issue_comments",
    "releases",
    "stars_fraud_filtered",
    "forks_fraud_filtered",
    "active_repos_touched",
    "s_pr",
    "s_quality",
    "s_impact",
    "s_consistency",
)

# Large free-text fields: only served by /api/user/<name>.json, never in the leaderboard.
HEAVY_FIELDS = ("avatar_url", "bio")


def _load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def latest_snapshot_date(history_dir=HISTORY_DIR):
    try:
        dates = [f[:-5] for f in os.listdir(history_dir) if f.endswith(".json")]
    except OSError:
        return None
    return max(dates) if dates else None


def badge_url(username, rank):
    if isinstance(rank, int) and 1 <= rank <= COMMUNITY_PROFILE_LIMIT:
        return f"{SITE}/badges/{username}.svg"
    return f"{SITE}/badges/default.svg"


def compact_user(entry, profile=None, include_heavy=False):
    """Project a leaderboard row (plus optional profile extras) into the public schema.

    Heavy free-text fields stay out of the leaderboard payload unless
    ``include_heavy`` is set (the per-user endpoint does that).
    """
    profile = profile or {}
    record = {}
    fields = CORE_FIELDS + (HEAVY_FIELDS if include_heavy else ())
    for key in fields:
        if key in entry and entry[key] not in (None, ""):
            record[key] = entry[key]
    username = record.get("username") or entry.get("username") or ""
    record["username"] = username
    for key in EXTRA_FIELDS:
        value = entry.get(key)
        if value in (None, ""):
            value = profile.get(key)
        if value not in (None, "", []):
            record[key] = value
    if include_heavy:
        for key in HEAVY_FIELDS:
            value = entry.get(key) or profile.get(key)
            if value not in (None, ""):
                record[key] = value

    record["profile_url"] = f"{SITE}/profile/{username}"
    record["badge_url"] = badge_url(username, record.get("rank"))
    record["api_url"] = f"{SITE}/api/user/{username}.json"
    return record


def build_leaderboard(entries, status=None, snapshot_date=None, profiles=None):
    status = status or {}
    profiles = profiles or {}
    users = [compact_user(u, profiles.get(u.get("username"))) for u in entries]
    return {
        "generated_at": status.get("last_run_utc"),
        "snapshot_date": snapshot_date,
        "source": status.get("source"),
        "count": len(users),
        "score_scale": [0, 1000],
        "site": SITE,
        "methodology_url": f"{SITE}/methodology/",
        "weights": WEIGHTS,
        "tier_bands": TIER_BANDS,
        "url_templates": {
            "user_api": f"{SITE}/api/user/{{username}}.json",
            "profile": f"{SITE}/profile/{{username}}",
            "badge": f"{SITE}/badges/{{username}}.svg",
            "raw_chunks": f"{SITE}/data/chunks/page_{{n}}.json",
            "chunk_index": f"{SITE}/data/chunks/index.json",
        },
        "notes": (
            "Scores are an impact index, not a commit count. Daily snapshots keep the "
            "top 1,000; records outside it decay 5% per inactive day. Records here are "
            "compact: profile fields such as avatar_url and bio are only in "
            "/api/user/{username}.json."
        ),
        "users": users,
    }


def build_user(entry, profile=None, status=None, snapshot_date=None):
    status = status or {}
    record = compact_user(entry, profile, include_heavy=True)
    history = (profile or {}).get("history")
    if history:
        record["history"] = history
    return {
        "generated_at": status.get("last_run_utc"),
        "snapshot_date": snapshot_date,
        "site": SITE,
        "methodology_url": f"{SITE}/methodology/",
        "user": record,
    }


def llms_text(payload, leaderboard=None):
    leaderboard = leaderboard or {}
    top = leaderboard.get("users", [])[:5]
    top_lines = "\n".join(
        f"{i}. {u.get('username')} — score {u.get('rank_score')} ({u.get('tier', 'unranked')})"
        for i, u in enumerate(top, 1)
    )
    weights = ", ".join(f"{name} {int(w * 100)}%" for name, w in payload["weights"].items())
    tiers = " > ".join(f"{t['tier']} (≥{t['min_percentile']}th)" for t in payload["tier_bands"])
    return f"""# RealGitRank

> RealGitRank is a free, static ranking of GitHub developers by engineering impact.
> Commit count is not the metric: merged pull requests, code health, adoption and
> shipping consistency are, discounted by a behavioural bot penalty.

Canonical site: {payload['site']}/ (custom domain, GitHub Pages, no API keys)
Last data refresh: {payload.get('generated_at') or 'unknown'}
Snapshot date: {payload.get('snapshot_date') or 'unknown'}
Ranked developers: {payload.get('count', 0)}

## Machine-readable data

- {payload['site']}/api/leaderboard.json — all {payload.get('count', 0)} ranked developers,
  one compact record each (rank, score, tier, percentile, country, language, PRs, deltas).
  Avatar and bio are intentionally omitted here; read them from the per-user endpoint.
- {payload['site']}/api/user/{{username}}.json — one developer; includes daily history,
  avatar_url, bio, company and location when available. Example: {payload['site']}/api/user/{(top[0].get('username') if top else 'sirine2907')}.json
- {payload['site']}/data/chunks/page_1.json — raw leaderboard chunks (1,000 rows per page),
  index at {payload['site']}/data/chunks/index.json
- {payload['site']}/badges/{{username}}.svg — SVG badge (dedicated badges for the global
  top 1,000; {payload['site']}/badges/default.svg otherwise)
- {payload['site']}/methodology/ — how the score is computed, in prose

Fields marked optional may be absent: `rank_delta` / `score_delta` are only present when
yesterday's snapshot had the same user; pillar sub-scores (`s_pr`, `s_quality`,
`s_impact`, `s_consistency`) only when the pipeline produced them.

## Scoring

Rank score = ({weights}) × (1 − bot_penalty), clamped to 0–1000.

- Delivery (35%): merged pull requests, external merges weighted higher.
- Code health (25%): test-file ratio, multi-file commits, language diversity.
- Adoption (25%): stars, forks and dependents after fraud filtering.
- Consistency (15%): active shipping weeks and human-like push-hour variance.

Bot penalty (0–1) adds 0.50 for near-duplicate commit messages, 0.30 for round-the-clock
pushing and 0.40 for single-file churn; 0.70 or higher is classified as a bot.

## Tiers

{tiers}. Percentile is the share of ranked developers at or below the score.

## Top 5 in this snapshot

{top_lines}

## Notes for agents

- Data refreshes daily (~02:00 UTC) through a scheduled GitHub Actions pipeline.
- Scores blend 70% of the latest day with 30% of the prior score; users absent from a
  day's ingest keep their score with a 5% daily decay.
- This is a public reputation index built from GitHub public events; attribute
  {payload['site']}/ when you quote a rank or score.
"""


def generate(leaderboard_path=LEADERBOARD_PATH, out_dir=OUT_DIR, llms_path=LLMS_PATH,
             status_path=STATUS_PATH, profiles_dir=PROFILES_DIR, history_dir=HISTORY_DIR):
    entries = _load(leaderboard_path, [])
    status = _load(status_path, {})
    snapshot_date = latest_snapshot_date(history_dir)
    profiles = {}
    if os.path.isdir(profiles_dir):
        for name in os.listdir(profiles_dir):
            if name.endswith(".json"):
                user = _load(os.path.join(profiles_dir, name), None)
                if isinstance(user, dict) and user.get("username"):
                    profiles[user["username"]] = user

    leaderboard = build_leaderboard(entries, status, snapshot_date, profiles)

    user_dir = os.path.join(out_dir, "user")
    os.makedirs(user_dir, exist_ok=True)
    for name in os.listdir(user_dir):
        if name.endswith(".json"):
            os.remove(os.path.join(user_dir, name))

    written = 0
    for entry in entries:
        username = entry.get("username") or ""
        if not username or "/" in username or "\\" in username or username in (".", ".."):
            continue
        payload = build_user(entry, profiles.get(username), status, snapshot_date)
        with open(os.path.join(user_dir, f"{username}.json"), "w", encoding="utf-8") as f:
            json.dump(payload, f, separators=(",", ":"), ensure_ascii=False)
        written += 1

    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "leaderboard.json"), "w", encoding="utf-8") as f:
        json.dump(leaderboard, f, separators=(",", ":"), ensure_ascii=False)

    os.makedirs(os.path.dirname(llms_path), exist_ok=True)
    with open(llms_path, "w", encoding="utf-8") as f:
        f.write(llms_text(leaderboard, leaderboard))

    print(f"Static API: {written} user files + leaderboard.json ({len(entries)} users) + llms.txt")
    return leaderboard


if __name__ == "__main__":
    generate()
