"""Real GitHub data ingest from gharchive.org (zero GCP, zero auth, free forever).

Extracts everything available from GitHub Archive WITHOUT any API:
- Pushes, merged PRs, closed issues, active repos
- Commit message entropy (PushEvent payload.commits[].message)
- Circadian hours + hour std-dev (from created_at)
- Stars (WatchEvent count per repo owner)
- Forks (ForkEvent count per repo owner)
- Org name (from event.org)
- PR reviews (PullRequestReviewEvent count)
- Issue comments (IssueCommentEvent count)
- Releases (ReleaseEvent count)

Quality signals are derived from what the events feed really exposes:
- ``avg_files_changed``       mean ``pull_request.changed_files`` over merged PRs
- ``multi_file_commit_ratio`` share of those merged PRs touching more than one file
- ``test_file_ratio``         share of commits + merged PRs whose message/title/body
                              mentions tests (PushEvent carries messages, not file lists)
- ``active_shipping_weeks``   distinct ISO weeks the user pushed in (lower bound)
- ``push_hour_stddev``        real std-dev of the user's active hours

The public gharchive feed ships trimmed payloads: PushEvent payloads are empty (no
commit messages) and PullRequestEvent carries only url/id/number/head/base (no merged
flag, no changed_files, no title), with merges reported as action="merged". So those
quality signals usually stay NaN ("unknown") rather than being invented; the scoring
engine skips the corresponding penalties for unknown values instead of guessing.
Merged PRs are recognised from both action="merged" and action="closed"+merged=true.
"""
import gzip, json, os, sys, urllib.request
from datetime import datetime, timedelta, timezone
from statistics import pstdev

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rolling import write_day_shard

BASE = "https://data.gharchive.org"

# Never scored (mirrors the exclusions in config/scoring_weights.yaml)
BOT_LOGINS = {"github-actions", "dependabot", "renovate[bot]", "dependabot-preview[bot]"}

# Commit/PR text that suggests test coverage work
TEST_KEYWORDS = ("test", "spec", "pytest", "jest", "unittest", "e2e", "coverage", "fixture")

# Keep parquet small enough for the repo
MAX_USERS = 50000


def download_hour(date_str, hour, out_dir):
    url = f"{BASE}/{date_str}-{hour}.json.gz"
    path = os.path.join(out_dir, f"{date_str}-{hour}.json.gz")
    if os.path.exists(path):
        return path
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "RealGitRank/1.0"})
        with urllib.request.urlopen(req, timeout=120) as resp, open(path, "wb") as out:
            out.write(resp.read())
        return path
    except Exception as e:
        print(f"  skip hour {hour}: {e}")
        return None


def is_bot_login(login):
    return not login or "[bot]" in login or login in BOT_LOGINS


def has_test_signal(text):
    """True when a commit message or PR title/body talks about tests."""
    if not text:
        return False
    low = str(text).lower()
    return any(keyword in low for keyword in TEST_KEYWORDS)


def created_parts(created):
    """Return (hour, date) for a gharchive created_at value, or (None, None)."""
    if created is None:
        return None, None
    if hasattr(created, "hour") and hasattr(created, "date"):
        return created.hour, created.date()
    try:
        dt = datetime.fromisoformat(str(created).replace("Z", "+00:00"))
        return dt.hour, dt.date()
    except ValueError:
        return None, None


def new_user(login):
    return {
        "username": login,
        "total_pushes": 0,
        "merged_prs": 0,
        "merged_pr_weighted": 0.0,
        "closed_issues": 0,
        "repos": set(),
        "commit_msgs": set(),
        "hours": [],
        "dates": set(),
        "pr_files": [],
        "multi_file_prs": 0,
        "test_hits": 0,
        "evidence": 0,
        "org": "",
        "pr_reviews": 0,
        "issue_comments": 0,
        "releases": 0,
        "stars_received": 0,
        "forks_received": 0,
        "merged_repos": {},
        "merged_pr_refs": [],
    }


def aggregate_events(events, agg=None, stats=None):
    """Fold raw gharchive event dicts into per-user accumulators.

    ``stats`` (optional) records how much of the feed was seen and how much of it
    was dropped as bot activity, so coverage can be published instead of implied.
    """
    if agg is None:
        agg = {}

    def get(login):
        if login not in agg:
            agg[login] = new_user(login)
        return agg[login]

    for r in events:
        if not isinstance(r, dict):
            continue

        actor = r.get("actor") or {}
        login = actor.get("login") if isinstance(actor, dict) else None
        if is_bot_login(login):
            if stats is not None:
                stats["events"] = stats.get("events", 0) + 1
                stats["bot_events"] = stats.get("bot_events", 0) + 1
            continue
        if stats is not None:
            stats["events"] = stats.get("events", 0) + 1

        etype = r.get("type")
        repo = r.get("repo") or {}
        repo_name = repo.get("name") if isinstance(repo, dict) else None
        org = r.get("org") or {}
        org_login = org.get("login") if isinstance(org, dict) else None
        payload = r.get("payload") or {}
        if not isinstance(payload, dict):
            payload = {}

        u = get(login)
        if org_login and not u["org"]:
            u["org"] = org_login

        hour, day = created_parts(r.get("created_at"))
        if hour is not None:
            u["hours"].append(hour)
        if day is not None:
            u["dates"].add(day)
        if repo_name:
            u["repos"].add(repo_name)

        if etype == "PushEvent":
            u["total_pushes"] += 1
            commits = payload.get("commits")
            if isinstance(commits, list):
                for c in commits:
                    if not isinstance(c, dict):
                        continue
                    message = c.get("message")
                    if not message:
                        continue
                    u["commit_msgs"].add(message)
                    u["evidence"] += 1
                    if has_test_signal(message):
                        u["test_hits"] += 1

        elif etype == "PullRequestEvent":
            # gharchive reports merges as action="merged"; the Events API uses
            # action="closed" plus pull_request.merged. Accept both.
            action = payload.get("action")
            if action not in ("merged", "closed"):
                continue
            pr = payload.get("pull_request") or {}
            if not isinstance(pr, dict):
                continue
            if action != "merged" and pr.get("merged") is not True:
                continue
            u["merged_prs"] += 1
            if repo_name:
                u["merged_repos"][repo_name] = u["merged_repos"].get(repo_name, 0) + 1
                number = payload.get("number") or pr.get("number")
                if number and len(u["merged_pr_refs"]) < 50:
                    u["merged_pr_refs"].append(f"{repo_name}#{int(number)}")
            owner = repo_name.split("/")[0] if repo_name and "/" in repo_name else None
            # External merges (someone else merged your PR) weigh more than self-merges.
            u["merged_pr_weighted"] += 1.0 if owner == login else 1.5
            changed = pr.get("changed_files")
            if isinstance(changed, int) and changed >= 0:
                u["pr_files"].append(changed)
                if changed > 1:
                    u["multi_file_prs"] += 1
            u["evidence"] += 1
            if has_test_signal(f"{pr.get('title') or ''} {pr.get('body') or ''}"):
                u["test_hits"] += 1

        elif etype == "IssuesEvent":
            if payload.get("action") == "closed":
                u["closed_issues"] += 1

        elif etype == "PullRequestReviewEvent":
            u["pr_reviews"] += 1

        elif etype == "IssueCommentEvent":
            u["issue_comments"] += 1

        elif etype == "ReleaseEvent":
            u["releases"] += 1

        elif etype in ("WatchEvent", "ForkEvent"):
            # Credit the repo owner, not the actor.
            if repo_name and "/" in repo_name:
                owner = repo_name.split("/")[0]
                if owner != login:
                    key = "stars_received" if etype == "WatchEvent" else "forks_received"
                    get(owner)[key] += 1

    return agg


def build_rows(agg):
    """Turn accumulators into one scoring row per user (real metrics, NaN = unknown)."""
    rows = []
    for d in agg.values():
        tp = d["total_pushes"]
        mp = d["merged_prs"]
        if tp < 1 and mp < 1:
            continue

        hours = d["hours"]
        repos = len(d["repos"])
        files = d["pr_files"]
        iso_weeks = {(day.isocalendar()[0], day.isocalendar()[1]) for day in d["dates"]}

        rows.append({
            "username": d["username"],
            "country": "",
            "primary_language": "Unknown",
            "org": d["org"],
            "total_pushes": tp,
            "merged_prs": mp,
            "merged_prs_external_weighted": round(d["merged_pr_weighted"], 2),
            "closed_issues": d["closed_issues"],
            "active_repos_touched": repos if repos > 0 else 1,
            # NaN = no commit messages observed in this feed, so entropy is unknown
            "unique_commit_messages": len(d["commit_msgs"]) if d["commit_msgs"] else float("nan"),
            "distinct_active_hours": len(set(hours)) if hours else 1,
            "avg_files_changed": (sum(files) / len(files)) if files else float("nan"),
            "push_hour_stddev": pstdev(hours) if len(hours) > 1 else float("nan"),
            "test_file_ratio": (d["test_hits"] / d["evidence"]) if d["evidence"] else float("nan"),
            "multi_file_commit_ratio": (d["multi_file_prs"] / len(files)) if files else float("nan"),
            "language_diversity": min(repos, 5) if repos > 0 else 1,
            "stars_fraud_filtered": d["stars_received"],
            "forks_fraud_filtered": d["forks_received"],
            "dependents": 0,
            "active_shipping_weeks": max(1, min(52, len(iso_weeks))),
            "pr_reviews": d["pr_reviews"],
            "issue_comments": d["issue_comments"],
            "releases": d["releases"],
        })

    # Sort by merged_prs then total_pushes
    rows.sort(key=lambda x: (x["merged_prs"], x["total_pushes"]), reverse=True)
    return rows[:MAX_USERS]


def read_events(path):
    """Stream a gharchive .json.gz file into event dicts.

    Parsed in Python on purpose: duckdb's read_json_auto infers struct schemas from a
    sample, and gharchive PR payloads vary enough that fields such as
    ``pull_request.merged`` were missing from the inferred struct - which zeroed the
    whole Delivery pillar. Streaming the JSON keeps every key intact.
    """
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def ingest_day(target_date, rolling_dir="data/rolling", write_raw=True):
    """Ingest one UTC day into a rolling shard. Returns the shard user count."""
    date_str = target_date.strftime("%Y-%m-%d")
    print(f"Ingesting gharchive.org for {date_str} (24 hourly files)...")

    raw_dir = "data/gharchive_raw"
    os.makedirs(raw_dir, exist_ok=True)

    files = []
    for h in range(24):
        p = download_hour(date_str, h, raw_dir)
        if p:
            files.append(p)
    print(f"Downloaded {len(files)}/24 hours")
    if not files:
        return 0

    agg = {}
    stats = {"events": 0, "bot_events": 0, "hours": len(files)}
    for f in files:
        try:
            aggregate_events(read_events(f), agg, stats)
        except Exception as e:
            print(f"  error processing {f}: {e}")

    print(f"Aggregated {len(agg)} users from {stats['events']} events "
          f"({stats['bot_events']} bot events skipped)")
    path, users = write_day_shard(date_str, agg, rolling_dir, stats=stats)
    print(f"Rolling shard: {path} ({users} users)")

    if write_raw:
        rows = build_rows(agg)
        import pyarrow as pa, pyarrow.parquet as pq
        os.makedirs("data", exist_ok=True)
        table = pa.Table.from_pylist(rows)
        pq.write_table(table, "data/raw.parquet")
        print(f"Saved {len(rows)} users to data/raw.parquet")
    return users


def ingest(target_date=None):
    target = target_date or (datetime.now(timezone.utc).date() - timedelta(days=1))
    return ingest_day(target) > 0


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Ingest gharchive.org into rolling shards")
    parser.add_argument("--date", help="UTC date to ingest (YYYY-MM-DD); defaults to yesterday")
    parser.add_argument("--days", type=int, default=1, help="Also backfill N-1 earlier days")
    args = parser.parse_args()

    end = (datetime.strptime(args.date, "%Y-%m-%d").date() if args.date
           else datetime.now(timezone.utc).date() - timedelta(days=1))
    for offset in range(args.days - 1, -1, -1):
        day = end - timedelta(days=offset)
        if ingest_day(day) == 0:
            print(f"No data for {day}")
    sys.exit(0)
