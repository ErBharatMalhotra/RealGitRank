"""Per-user profile JSON storage (top-N profiles only).

Profiles are pre-rendered at build time and fetched by the client profile page.
A user who drops out of the top-N keeps an old JSON unless it is pruned, which
leaves a stale page for a rank that no longer exists. Prune after every write.
"""
import json
import os

PROFILE_LIMIT = 1000


def prune_stale_profiles(profiles_dir, keep_usernames):
    """Delete profile JSONs whose username is not in keep_usernames.

    Non-JSON files are left alone. Returns the number of files removed.
    """
    if not os.path.isdir(profiles_dir):
        return 0
    keep = set(keep_usernames)
    removed = 0
    for name in os.listdir(profiles_dir):
        if not name.endswith(".json"):
            continue
        if name[: -len(".json")] in keep:
            continue
        path = os.path.join(profiles_dir, name)
        if os.path.isfile(path):
            os.remove(path)
            removed += 1
    return removed


def prune_profiles_for_leaderboard(profiles_dir, leaderboard, limit=PROFILE_LIMIT):
    """Keep profiles only for the current top `limit` leaderboard users."""
    return prune_stale_profiles(profiles_dir, (u["username"] for u in leaderboard[:limit]))
