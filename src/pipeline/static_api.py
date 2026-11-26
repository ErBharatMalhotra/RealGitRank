"""Generate the static machine-readable layer served by GitHub Pages.

Outputs (all plain files, no server):
  web/public/api/leaderboard.json   - every ranked developer, compact records
  web/public/api/user/<name>.json   - one record per ranked developer
  web/public/llms.txt               - short guide for LLM/agent consumers

Score v3 is one absolute number: public GitHub activity events in the last 12
months. There are no percentile sub-scores, weights or tier bands to publish,
and an account with no observed activity is simply absent rather than "listed".
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
    "events. A score is an automated count over one rolling 12-month window, not a "
    "statement of fact about a person, a measure of skill, or an endorsement, and it "
    "is not affiliated with GitHub."
)
LEADERBOARD_PATH = "data/leaderboard.json"
STATUS_PATH = "data/status.json"
COVERAGE_PATH = "data/coverage.json"
PROFILES_DIR = "data/profiles"
HISTORY_DIR = "data/history"
OUT_DIR = "web/public/api"
LLMS_PATH = "web/public/llms.txt"
COMMUNITY_PROFILE_LIMIT = 1000  # badges/profiles are pre-rendered for the global top 1,000

ACTIVITY_COMPONENTS = (
    ("total_pushes", "Pushes"),
    ("merged_prs", "Merged pull requests"),
    ("closed_issues", "Closed issues"),
    ("pr_reviews", "Pull request reviews"),
    ("issue_comments", "Issue comments"),
    ("releases", "Releases"),
)

# Documented contract of the compact leaderboard records.
CORE_FIELDS = (
    "username",
    "rank",
    "rank_score",
    "activity",
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
    "total_pushes",
    "closed_issues",
    "pr_reviews",
    "issue_comments",
    "releases",
    "active_days",
    "window_days",
    "contributions_1y",
    "stars_total",
    "followers",
)

# Evidence details stay out of the leaderboard; only /api/user/<name>.json carries them.
USER_ONLY_FIELDS = ("counted_merges",)

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
        "score_unit": "public GitHub activity events over the last 12 complete months plus the current month",
        "activity_components": dict(ACTIVITY_COMPONENTS),
        "window": {
            "days": status.get("window_days"),
            "months": status.get("window_months"),
            "start": status.get("window_start"),
            "end": status.get("window_end"),
        },
        "count": len(users),
        "ranked_total": status.get("users_ranked") or status.get("users") or len(users),
        "site": SITE,
        "methodology_url": f"{SITE}/methodology/",
        "coverage": coverage,
        "disclaimer": DISCLAIMER,
        "correction_url": CORRECTION_URL,
        "url_templates": {
            "user_api": f"{SITE}/api/user/{{username}}.json",
            "profile": f"{SITE}/profile/{{username}}",
            "badge": f"{SITE}/badges/{{username}}.svg",
            "raw_chunks": f"{SITE}/data/chunks/page_{{n}}.json",
            "chunk_index": f"{SITE}/data/chunks/index.json",
            "search_bucket": f"{SITE}/data/search/{{first_char}}.json",
        },
        "notes": (
            "Each rank_score is the absolute count of public GitHub activity events "
            "(pushes + merged pull requests + closed issues + pull-request reviews + "
            "issue comments + releases) observed in the rolling 12-month window. The "
            "components are published per user, so the score can be recomputed from the "
            "record itself. Ties are broken by merged pull requests, then login. There "
            "is no percentile, weight or tier. GitHub's own contribution count (which "
            "counts commits) is published separately as contributions_1y for the top "
            "10,000 and is never mixed into the score. Every ranked developer is "
            "published in /data/chunks (250 rows per page) and indexed in "
            "/data/search/<first-char>.json; detailed profiles and per-user JSON are "
            "served only for the top 1,000. Independent GitHub spot checks live in "
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
        "score_unit": "public GitHub activity events over the last 12 complete months plus the current month",
        "window": {
            "days": status.get("window_days"),
            "months": status.get("window_months"),
            "start": status.get("window_start"),
            "end": status.get("window_end"),
        },
        "user": record,
    }


def llms_text(payload, leaderboard=None):
    leaderboard = leaderboard or {}
    top = leaderboard.get("users", [])[:5]
    top_lines = "\n".join(
        f"{i}. {u.get('username')} — {u.get('rank_score')} activity events"
        for i, u in enumerate(top, 1)
    )
    components = ", ".join(label.lower() for _, label in ACTIVITY_COMPONENTS)
    coverage = payload.get("coverage") or {}
    signals = coverage.get("signals") or {}
    coverage_line = ", ".join(
        f"{value.get('label', key)} {value.get('pct', 0)}%" for key, value in signals.items()
    ) or "not published"
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

> RealGitRank is a free, static ranking of GitHub developers by public activity.
> One absolute number decides the rank: pushes + merged pull requests + closed
> issues + pull-request reviews + issue comments + releases observed in the
> rolling 12-month window ({components}). No percentiles, no weights, no tiers.
> GitHub's own contribution count is shown separately and is never scored.

Canonical site: {payload['site']}/ (custom domain, GitHub Pages, no API keys)
Last data refresh: {payload.get('generated_at') or 'unknown'}
Snapshot date: {payload.get('snapshot_date') or 'unknown'}
Ranked developers: {payload.get('ranked_total', payload.get('count', 0))} (every account with at least one observed public activity event)
Score version: {payload.get('score_version') or 'unknown'}
Window: {window.get('days')} days across {window.get('months')} months ({window.get('start')} to {window.get('end')})
Signal coverage: {coverage_line}
Independent validation: {validation_line}

## Machine-readable data

- {payload['site']}/api/leaderboard.json — all {payload.get('count', 0)} ranked developers,
  one compact record each (rank, rank_score/activity, components, country, language, deltas).
  Avatar and bio are intentionally omitted here; read them from the per-user endpoint.
- {payload['site']}/data/coverage.json — observed counts for every activity component,
  source coverage and validation results.
- {payload['site']}/api/user/{{username}}.json — one developer in the top 1,000; includes
  daily history, avatar_url, bio, company and location when available. Example: {payload['site']}/api/user/{(top[0].get('username') if top else 'sirine2907')}.json
  (users outside the top 1,000 have no per-user file; read their row from the chunks)
- {payload['site']}/data/chunks/page_1.json — raw leaderboard chunks (250 rows per page),
  index at {payload['site']}/data/chunks/index.json; every ranked developer is included
- {payload['site']}/data/search/{{first_char}}.json — compact
  [username, rank, score, state, page] index sharded by the first character of
  the username (a-z, 0-9, `other`). `score` is the 12-month activity count and
  `state` is always `ranked`; an account with no observed activity is absent
- {payload['site']}/badges/{{username}}.svg — SVG badge (dedicated badges for the global
  top 1,000; {payload['site']}/badges/default.svg otherwise)
- {payload['site']}/methodology/ — how the score is computed, in prose

## Scoring

rank_score = total_pushes + merged_prs + closed_issues + pr_reviews + issue_comments + releases

Every component is counted from the public GitHub event stream (gharchive.org)
over the rolling 12-month window. Each user record publishes all six components,
so the score can be recomputed from the record. Ties are broken by merged pull
requests, then by login. A push is one event: GitHub trimmed PushEvent payloads
so the commit list is not public, and no commit count is invented.

GitHub's own `totalContributions` (which counts commits) is fetched from the
GraphQL API for the top 10,000 and on profile lookup. It is displayed as
`contributions_1y` and is never part of the score.

## Top 5 in this snapshot

{top_lines}

## Notes for agents

- Data refreshes daily (~02:00 UTC) through a scheduled GitHub Actions pipeline.
- The window only moves forward; older months are pruned after 12 months.
- Coverage numbers above are computed from the published data, not marketing copy.
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
