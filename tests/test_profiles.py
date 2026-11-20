import json
import os

from profiles import PROFILE_LIMIT, prune_profiles_for_leaderboard, prune_stale_profiles


def write_profile(directory, username):
    with open(os.path.join(directory, f"{username}.json"), "w") as f:
        json.dump({"username": username}, f)


def test_prune_removes_users_outside_keep_list(tmp_path):
    for name in ("alice", "bob", "carol"):
        write_profile(tmp_path, name)
    with open(tmp_path / "notes.txt", "w") as f:
        f.write("not a profile")

    removed = prune_stale_profiles(tmp_path, ["alice", "bob"])

    assert removed == 1
    assert sorted(os.listdir(tmp_path)) == ["alice.json", "bob.json", "notes.txt"]


def test_prune_profiles_for_leaderboard_keeps_only_top(tmp_path):
    for name in ("alice", "bob", "carol", "dave"):
        write_profile(tmp_path, name)
    leaderboard = [
        {"username": "alice", "rank": 1},
        {"username": "bob", "rank": 2},
        {"username": "carol", "rank": 3},
        {"username": "dave", "rank": 4},
    ]

    removed = prune_profiles_for_leaderboard(tmp_path, leaderboard, limit=2)

    assert removed == 2
    assert sorted(os.listdir(tmp_path)) == ["alice.json", "bob.json"]


def test_prune_handles_missing_directory(tmp_path):
    assert prune_stale_profiles(tmp_path / "missing", ["alice"]) == 0


def test_default_limit_is_top_1000():
    assert PROFILE_LIMIT == 1000
