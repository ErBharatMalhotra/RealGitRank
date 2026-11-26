"""The public search layer carries an explicit state and the activity score."""
from chunks import dedupe_rows, search_row


def test_dedupe_keeps_best_rank():
    rows = [
        {"username": "lx223", "rank": 33973, "rank_score": 0},
        {"username": "Other", "rank": 40000, "rank_score": 1},
        {"username": "LX223", "rank": 50001, "rank_score": 0},
    ]
    out = dedupe_rows(rows)
    assert [r["username"] for r in out] == ["lx223", "Other"]
    assert out[0]["rank"] == 33973


def test_search_row_carries_ranked_state():
    assert search_row({"username": "dev", "rank": 12, "rank_score": 412.4}, 1) == [
        "dev", 12, 412.4, "ranked", 1,
    ]


def test_search_row_never_publishes_a_listed_state():
    """An account with no activity is not published at all under score v3."""
    assert search_row({"username": "dev", "rank": 800000, "rank_score": 1}, 3200) == [
        "dev", 800000, 1.0, "ranked", 3200,
    ]
