"""Scoring v3: one absolute 12-month activity number, no percentiles."""
import activity
from scoring_engine import compute_rank_scores


def row(username, pushes=10, merged=1, issues=0, reviews=0, comments=0, releases=0, **extra):
    total = pushes + merged + issues + reviews + comments + releases
    r = {
        "username": username,
        "total_pushes": pushes,
        "merged_prs": merged,
        "closed_issues": issues,
        "pr_reviews": reviews,
        "issue_comments": comments,
        "releases": releases,
        "active_days": 12,
        "window_days": 365,
        "rank_score": total,
    }
    r.update(extra)
    return r


def score(tmp_path, rows):
    path = tmp_path / "activity.parquet"
    activity.write_parquet(rows, str(path))
    return compute_rank_scores(str(path))


def test_score_is_the_absolute_sum_of_components(tmp_path):
    out = score(tmp_path, [row("a", pushes=240, merged=86, issues=41, reviews=30, comments=12, releases=3)])
    assert out[0]["rank_score"] == 412
    assert out[0]["activity"] == 412
    assert out[0]["total_pushes"] == 240
    assert out[0]["merged_prs"] == 86
    assert out[0]["closed_issues"] == 41
    assert out[0]["pr_reviews"] == 30
    assert out[0]["issue_comments"] == 12
    assert out[0]["releases"] == 3


def test_score_is_not_scaled_or_capped(tmp_path):
    """The old 0-1000 scale is gone: a big number stays big and a small one small."""
    out = score(tmp_path, [row("big", pushes=50_000, merged=0), row("small", pushes=3, merged=0)])
    by_name = {r["username"]: r for r in out}
    assert by_name["big"]["rank_score"] == 50_000
    assert by_name["small"]["rank_score"] == 3


def test_rank_is_activity_order_and_contiguous(tmp_path):
    out = score(tmp_path, [row("low", pushes=1), row("high", pushes=100), row("mid", pushes=10)])
    assert [r["username"] for r in out] == ["high", "mid", "low"]
    assert [r["rank"] for r in out] == [1, 2, 3]


def test_ties_break_on_merged_prs_then_login(tmp_path):
    out = score(tmp_path, [
        row("b", pushes=5, merged=1),
        row("a", pushes=5, merged=1),
        row("c", pushes=4, merged=2),
    ])
    assert [r["username"] for r in out] == ["c", "a", "b"]


def test_zero_activity_rows_are_not_ranked(tmp_path):
    out = score(tmp_path, [row("idle", pushes=0, merged=0), row("dev", pushes=2)])
    assert [r["username"] for r in out] == ["dev"]
    assert out[0]["rank"] == 1


def test_empty_username_is_dropped(tmp_path):
    out = score(tmp_path, [row(""), row("dev", pushes=1)])
    assert [r["username"] for r in out] == ["dev"]


def test_no_percentile_fields_remain(tmp_path):
    out = score(tmp_path, [row("dev")])[0]
    for key in ("tier", "percentile", "s_pr", "s_quality", "s_impact", "s_consistency", "bot_penalty", "measured"):
        assert key not in out
