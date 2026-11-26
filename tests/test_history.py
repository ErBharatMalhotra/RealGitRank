import json
import os

from history import (
    HISTORY_LIMIT,
    MOVEMENT_KEYS,
    append_user_history,
    apply_deltas,
    clear_movement,
    load_previous_snapshot,
    prune_snapshots,
    write_snapshot,
)


def board(size):
    return [
        {"username": f"u{i}", "rank": i, "rank_score": round(1000.0 - i, 1)}
        for i in range(1, size + 1)
    ]


def test_clear_movement_drops_stale_deltas():
    lb = [{"username": "a", "rank": 1, **{k: 1 for k in MOVEMENT_KEYS}}]
    clear_movement(lb)
    assert not any(k in lb[0] for k in MOVEMENT_KEYS)


def test_apply_deltas_marks_movement_both_ways():
    previous = {"alice": [5, 90.0], "bob": [20, 70.0]}
    lb = [
        {"username": "alice", "rank": 2, "rank_score": 93.5},
        {"username": "bob", "rank": 30, "rank_score": 60.0},
        {"username": "newbie", "rank": 40, "rank_score": 50.0},
    ]

    assert apply_deltas(lb, previous) == 2
    assert lb[0]["rank_delta"] == 3
    assert lb[0]["score_delta"] == 3.5
    assert lb[0]["prev_rank"] == 5
    assert lb[0]["prev_score"] == 90.0
    assert lb[1]["rank_delta"] == -10
    assert lb[1]["score_delta"] == -10.0
    assert "rank_delta" not in lb[2]


def test_apply_deltas_without_previous_snapshot_is_noop():
    lb = [{"username": "alice", "rank": 1, "rank_score": 100.0}]
    assert apply_deltas(lb, {}) == 0
    assert apply_deltas(lb, None) == 0
    assert "rank_delta" not in lb[0]


def test_write_snapshot_is_compact_and_capped(tmp_path):
    path = write_snapshot(tmp_path, "2026-11-20", board(12), limit=10)
    payload = json.load(open(path))
    assert len(payload) == 10
    assert payload["u1"] == [1, 999.0]
    assert "u11" not in payload


def test_load_previous_snapshot_skips_today(tmp_path):
    write_snapshot(tmp_path, "2026-11-19", board(3))
    write_snapshot(tmp_path, "2026-11-20", board(3))

    date, previous = load_previous_snapshot(tmp_path, "2026-11-20")
    assert date == "2026-11-19"
    assert set(previous) == {"u1", "u2", "u3"}


def test_load_previous_snapshot_handles_empty_history(tmp_path):
    assert load_previous_snapshot(tmp_path, "2026-11-20") == (None, None)
    assert load_previous_snapshot(tmp_path / "missing", "2026-11-20") == (None, None)


def test_prune_snapshots_keeps_newest(tmp_path):
    for day in range(1, 6):
        write_snapshot(tmp_path, f"2026-11-0{day}", board(2))

    assert prune_snapshots(tmp_path, keep=3) == 2
    assert sorted(os.listdir(tmp_path)) == [
        "2026-11-03.json",
        "2026-11-04.json",
        "2026-11-05.json",
    ]


def test_append_user_history_dedupes_day_and_caps():
    profile = {"history": [{"d": f"2026-10-{d:02d}", "r": d, "s": 10.0} for d in range(1, 41)]}

    append_user_history(profile, "2026-11-20", 7, 42.44)
    append_user_history(profile, "2026-11-20", 6, 43.01)

    assert len(profile["history"]) == HISTORY_LIMIT
    assert profile["history"][-1] == {"d": "2026-11-20", "r": 6, "s": 43.0}
    assert sum(1 for p in profile["history"] if p["d"] == "2026-11-20") == 1


def test_append_user_history_creates_series():
    profile = {}
    append_user_history(profile, "2026-11-20", 3, 88.84)
    assert profile["history"] == [{"d": "2026-11-20", "r": 3, "s": 88.8}]
