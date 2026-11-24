"""Daily rank/score history, percentile bands (tiers) and day-over-day movement.

Two things live here:

1. *Tiers.* Every leaderboard row gets a `percentile` (share of the board it is
   ahead of) and a `tier` name derived from it. Pure presentation, but it makes
   "rank 4,312" mean something to a visitor.
2. *History.* One compact snapshot per day in `data/history/<YYYY-MM-DD>.json`
   containing `{username: [rank, score]}` for the top 1,000. Tomorrow's run reads
   the latest earlier snapshot and annotates each row with `prev_rank`,
   `prev_score`, `rank_delta` (positive = moved up) and `score_delta`. The
   per-profile JSON carries a short `history` series for the sparkline.

Snapshots are pruned to the newest HISTORY_LIMIT days so the git-backed data
store stays small.
"""
import json
import os

HISTORY_LIMIT = 30
SNAPSHOT_LIMIT = 1000

# (name, minimum percentile). Checked top-down.
TIERS = (
    ("Elite", 99.0),
    ("Diamond", 95.0),
    ("Platinum", 90.0),
    ("Gold", 75.0),
    ("Silver", 50.0),
    ("Bronze", 0.0),
)

MOVEMENT_KEYS = ("prev_rank", "prev_score", "rank_delta", "score_delta")


def percentile_for(rank, total):
    """Percent of the board this rank is ahead of (rank 1 -> 100.0)."""
    if total <= 1:
        return 100.0
    rank = max(1, min(int(rank), total))
    return round((total - rank) / (total - 1) * 100.0, 1)


def tier_for(percentile):
    for name, floor in TIERS:
        if percentile >= floor:
            return name
    return TIERS[-1][0]


def assign_tiers(leaderboard, total=None):
    """Annotate every row with `percentile` and `tier` in place.

    ``total`` is the size of the whole ranked population; pass it when
    ``leaderboard`` is only the detailed slice so percentiles stay global.
    """
    total = int(total or len(leaderboard))
    for index, user in enumerate(leaderboard):
        rank = user.get("rank") or (index + 1)
        percentile = percentile_for(rank, total)
        user["percentile"] = percentile
        user["tier"] = tier_for(percentile)
    return leaderboard


def clear_movement(leaderboard):
    """Drop yesterday's movement fields so a stale delta never survives a rerun."""
    for user in leaderboard:
        for key in MOVEMENT_KEYS:
            user.pop(key, None)
    return leaderboard


def snapshot_dates(history_dir):
    if not os.path.isdir(history_dir):
        return []
    return sorted(
        name[: -len(".json")]
        for name in os.listdir(history_dir)
        if name.endswith(".json") and len(name) > len(".json")
    )


def load_previous_snapshot(history_dir, today):
    """Return (date, mapping) for the newest snapshot strictly before `today`."""
    earlier = [d for d in snapshot_dates(history_dir) if d < today]
    if not earlier:
        return None, None
    date = earlier[-1]
    try:
        with open(os.path.join(history_dir, f"{date}.json"), encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None, None
    if not isinstance(data, dict):
        return None, None
    return date, data


def apply_deltas(leaderboard, previous):
    """Annotate rows that also existed in `previous`. Returns match count."""
    if not previous:
        return 0
    matched = 0
    for user in leaderboard:
        prior = previous.get(user.get("username"))
        if not prior:
            continue
        try:
            prev_rank, prev_score = int(prior[0]), float(prior[1])
        except (TypeError, ValueError, IndexError):
            continue
        user["prev_rank"] = prev_rank
        user["prev_score"] = round(prev_score, 1)
        user["rank_delta"] = prev_rank - int(user.get("rank") or prev_rank)
        user["score_delta"] = round(float(user.get("rank_score") or 0) - prev_score, 1)
        matched += 1
    return matched


def write_snapshot(history_dir, date, leaderboard, limit=SNAPSHOT_LIMIT):
    os.makedirs(history_dir, exist_ok=True)
    rows = sorted(leaderboard, key=lambda u: u.get("rank") or 0)[:limit]
    payload = {
        u["username"]: [u.get("rank"), round(float(u.get("rank_score") or 0), 1)]
        for u in rows
        if u.get("username")
    }
    path = os.path.join(history_dir, f"{date}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, separators=(",", ":"), sort_keys=True)
    return path


def prune_snapshots(history_dir, keep=HISTORY_LIMIT):
    """Delete all but the newest `keep` snapshots. Returns files removed."""
    dates = snapshot_dates(history_dir)
    removed = 0
    for date in dates[:-keep] if keep > 0 else dates:
        try:
            os.remove(os.path.join(history_dir, f"{date}.json"))
            removed += 1
        except OSError:
            pass
    return removed


def append_user_history(profile, date, rank, score, limit=HISTORY_LIMIT):
    """Append today's point to a profile's `history` series (one point per day)."""
    points = [
        p
        for p in (profile.get("history") or [])
        if isinstance(p, dict) and p.get("d") and p.get("d") != date
    ]
    points.append({"d": date, "r": int(rank), "s": round(float(score), 1)})
    points.sort(key=lambda p: p["d"])
    profile["history"] = points[-limit:]
    return profile["history"]
