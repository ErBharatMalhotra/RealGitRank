"""Generate the static machine-readable layer served by GitHub Pages.

Outputs (all plain files, no server):
  web/public/api/leaderboard.json   - every ranked developer, compact records
  web/public/api/user/<name>.json   - one record per ranked developer
  web/public/llms.txt               - short guide for LLM/agent consumers
"""
import json
import os

SITE = "https://realgitrank.bharatdn.com"
REPO_URL = "https://github.com/ErBharatMalhotra/RealGitRank"
CORRECTION_URL = (
    f"{REPO_URL}/issues/new?title=Correction%20or%20opt-out%20request"
    "&body=GitHub%20username%3A%20%0AWhat%20should%20change%3A%20"
)
DISCLAIMER = (
    "RealGitRank is an independent, unofficial project built only from public GitHub "
    "events. A score is an automated estimate over one rolling window, not a statement "
    "of fact about a person, a measure of skill, or an endorsement, and it is not "
    "affiliated with GitHub. Per-account classification values are not published; the "
    "site shows a score, never a label about an account."
)
LEADERBOARD_PATH = "data/leaderboard.json"
STATUS_PATH = "data/status.json"
COVERAGE_PATH = "data/coverage.json"
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
    "rank_delta",
    "score_delta",
    "score_version",
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
    "active_days",
    "window_days",
)

# Evidence details stay out of the leaderboard; only /api/user/<name>.json carries them.
USER_ONLY_FIELDS = ("measured", "counted_merges")

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


def compact_user(entry, profile=None, include_heavy=False, has_profile=None):
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
        for key in USER_ONLY_FIELDS:
            value = entry.get(key)
            if value in (None, "", [], {}):
                value = profile.get(key)
            if value not in (None, "", [], {}):
                record[key] = value

    # Pre-rendered profile pages exist only for the top 1,000; everyone else links to GitHub.
    # ``has_profile`` lets the generator use the pages that actually exist.
    rank = record.get("rank")
    has_page = (isinstance(rank, int) and 1 <= rank <= COMMUNITY_PROFILE_LIMIT) if has_profile is None else bool(has_profile)
    if has_page:
        record["profile_url"] = f"{SITE}/profile/{username}"
        record["api_url"] = f"{SITE}/api/user/{username}.json"
    else:
        record["profile_url"] = f"https://github.com/{username}"
    record["badge_url"] = badge_url(username, record.get("rank"))
    return record


def build_leaderboard(entries, status=None, snapshot_date=None, profiles=None, coverage=None):
    status = status or {}
    profiles = profiles or {}
    users = [
        compact_user(u, profiles.get(u.get("username")), has_profile=u.get("username") in profiles)
        for u in entries
    ]
    return {
        "generated_at": status.get("last_run_utc"),
        "snapshot_date": snapshot_date,
        "source": status.get("source"),
        "score_version": status.get("score_version"),
        "score_scale": [0, 1000],
        "window": {
            "days": status.get("window_days"),
            "start": status.get("window_start"),
            "end": status.get("window_end"),
            "shards": status.get("shards"),
        },
        "count": len(users),
        "ranked_total": status.get("users") or len(users),
        "site": SITE,
        "methodology_url": f"{SITE}/methodology/",
        "weights": WEIGHTS,
        "coverage": coverage,
        "disclaimer": DISCLAIMER,
        "correction_url": CORRECTION_URL,
        "tier_bands": TIER_BANDS,
        "url_templates": {
            "user_api": f"{SITE}/api/user/{{username}}.json",
            "profile": f"{SITE}/profile/{{username}}",
            "badge": f"{SITE}/badges/{{username}}.svg",
            "raw_chunks": f"{SITE}/data/chunks/page_{{n}}.json",
            "chunk_index": f"{SITE}/data/chunks/index.json",
            "search_bucket": f"{SITE}/data/search/{{first_char}}.json",
        },
        "notes": (
            "Scores measure merged work, adoption and consistency over the rolling window "
            "declared above, with each pillar percentile-ranked and missing pillars excluded "
            "(weights renormalised). Every ranked developer is published in "
            "/data/chunks (250 rows per page) and indexed in /data/search/<first-char>.json; "
            "detailed profiles and per-user JSON are served only "
            "for the top 1,000; avatar_url and bio live in /api/user/{username}.json. "
            "Coverage of every pillar "
            "is published in the coverage block, and independent GitHub spot checks in "
            "data/validation.json."
        ),
        "users": users,
    }


def build_user(entry, profile=None, status=None, snapshot_date=None):
    status = status or {}
    record = compact_user(entry, profile, include_heavy=True, has_profile=profile is not None)
    history = (profile or {}).get("history")
    if history:
        record["history"] = history
    return {
        "generated_at": status.get("last_run_utc"),
        "snapshot_date": snapshot_date,
        "site": SITE,
        "methodology_url": f"{SITE}/methodology/",
        "window": {
            "days": status.get("window_days"),
            "start": status.get("window_start"),
            "end": status.get("window_end"),
        },
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
    coverage = payload.get("coverage") or {}
    pillars = coverage.get("pillars") or {}
    coverage_line = ", ".join(
        f"{key} {value.get('pct', 0)}%" for key, value in pillars.items()
    ) or "not published"
    automation = coverage.get("automation_evidence") or {}
    if automation:
        coverage_line += f"; automation-signal evidence {automation.get('pct', 0)}%"
    validation = coverage.get("validation") or {}
    if validation:
        validation_line = (
            f"{validation.get('match_rate')}% of {validation.get('events_checked')} checked "
            f"merge events confirmed via the GitHub PR API across "
            f"{validation.get('users_with_events')} developers ({validation.get('checked_at')})"
        )
    else:
        validation_line = "not run yet"
    window = payload.get("window") or {}
    return f"""# RealGitRank

> RealGitRank is a free, static ranking of GitHub developers by engineering impact.
> It scores a rolling 30-day window of real GitHub events: each pillar is
> percentile-ranked inside the cohort where it was actually observed, missing
> pillars are excluded (weights renormalised), then a conservative automation
> adjustment is applied where the event stream shows machine-like patterns.
> Commit count is not the metric.

Canonical site: {payload['site']}/ (custom domain, GitHub Pages, no API keys)
Last data refresh: {payload.get('generated_at') or 'unknown'}
Snapshot date: {payload.get('snapshot_date') or 'unknown'}
Ranked developers: {payload.get('ranked_total', payload.get('count', 0))} ({payload.get('count', 0)} with detailed records)
Score version: {payload.get('score_version') or 'unknown'}
Window: {window.get('days')} days ({window.get('start')} to {window.get('end')}, {window.get('shards')} daily shards)
Pillar coverage: {coverage_line}
Independent validation: {validation_line}

## Machine-readable data

- {payload['site']}/api/leaderboard.json — all {payload.get('count', 0)} ranked developers,
  one compact record each (rank, score, tier, percentile, country, language, PRs, deltas).
  Avatar and bio are intentionally omitted here; read them from the per-user endpoint.
- {payload['site']}/data/coverage.json — measured/null counts for every pillar,
  automation-signal evidence coverage and validation results.
- {payload['site']}/api/user/{{username}}.json — one developer in the top 1,000; includes
  daily history, avatar_url, bio, company and location when available. Example: {payload['site']}/api/user/{(top[0].get('username') if top else 'sirine2907')}.json
  (users outside the top 1,000 have no per-user file; read their row from the chunks)
- {payload['site']}/data/chunks/page_1.json — raw leaderboard chunks (250 rows per page),
  index at {payload['site']}/data/chunks/index.json; every ranked developer is included
- {payload['site']}/data/search/{{first_char}}.json — compact [username, rank, score]
  index sharded by the first character of the username (a-z, 0-9, `other`), so a
  lookup does not download the whole board
- {payload['site']}/badges/{{username}}.svg — SVG badge (dedicated badges for the global
  top 1,000; {payload['site']}/badges/default.svg otherwise)
- {payload['site']}/methodology/ — how the score is computed, in prose

Fields marked optional may be absent: `rank_delta` / `score_delta` are only present when
yesterday's snapshot had the same user; pillar sub-scores (`s_pr`, `s_quality`,
`s_impact`, `s_consistency`) are 0–100 percentiles and `s_quality` is absent when no
test/file evidence exists. `measured` says which pillars counted for that user;
`counted_merges` lists the repositories behind the merged-PR count.

## Scoring

Rank score = 10 × Σ(wᵢ · pᵢ) / Σ(wᵢ observed) × (1 − automation adjustment), clamped to 0–1000,
where pᵢ is the 0–100 percentile of pillar i among users where that pillar is observed.

- Delivery (35%): pull requests merged by the developer over the window (merge events, not authored count); external merges weighted higher.
- Code health (25%): test-signal ratio and multi-file PRs; only counted when observed.
- Adoption (25%): stars and forks received over the window.
- Consistency (15%): active shipping weeks and push-hour spread over the window.

Automation adjustment (0–1) adds 0.50 for near-duplicate commit messages, 0.30 for
round-the-clock pushing and 0.40 for single-file churn, but only on observed evidence;
when the feed does not expose the signal the adjustment stays 0 and the limitation is
published in coverage. The adjustment is an aggregate heuristic: no per-account
classification is published, and no account is publicly labelled as automated.

## Tiers

{tiers}. Percentile is the share of ranked developers at or below the score.

## Top 5 in this snapshot

{top_lines}

## Notes for agents

- Data refreshes daily (~02:00 UTC) through a scheduled GitHub Actions pipeline.
- Scores use the newest 30 daily shards only; there is no blending with older scores and
  no score decay. A user with no activity in the window is not ranked.
- Coverage and validation numbers above are computed from the published data, not
  marketing copy. If a pillar's coverage is low, do not treat that sub-score as reliable.
- This is a public reputation index built from GitHub public events; attribute
  {payload['site']}/ when you quote a rank or score.
- Disclaimer: {payload.get('disclaimer') or DISCLAIMER}
- Corrections, misattribution and opt-out requests: {payload.get('correction_url') or CORRECTION_URL}
"""


def generate(leaderboard_path=LEADERBOARD_PATH, out_dir=OUT_DIR, llms_path=LLMS_PATH,
             status_path=STATUS_PATH, profiles_dir=PROFILES_DIR, history_dir=HISTORY_DIR,
             coverage_path=COVERAGE_PATH):
    entries = _load(leaderboard_path, [])
    status = _load(status_path, {})
    coverage = _load(coverage_path, None)
    snapshot_date = latest_snapshot_date(history_dir)
    profiles = {}
    if os.path.isdir(profiles_dir):
        for name in os.listdir(profiles_dir):
            if name.endswith(".json"):
                user = _load(os.path.join(profiles_dir, name), None)
                if isinstance(user, dict) and user.get("username"):
                    profiles[user["username"]] = user

    leaderboard = build_leaderboard(entries, status, snapshot_date, profiles, coverage)

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
        # Per-user files exist only for the top 1,000 (the profiles we already keep).
        if username not in profiles:
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
