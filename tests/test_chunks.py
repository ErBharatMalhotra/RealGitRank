"""The public search layer must carry an explicit state, not an invented rank."""
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
    assert search_row({"username": "dev", "rank": 12, "rank_score": 800.44}, 1) == [
        "dev", 12, 800.4, "ranked", 1,
    ]


def test_search_row_never_claims_a_rank_for_listed():
    assert search_row(
        {"username": "ciowner", "rank": 800000, "rank_score": 0, "listed_only": True}, 3200,
    ) == ["ciowner", None, 0.0, "listed", 3200]
