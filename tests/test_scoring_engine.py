"""Focused tests for the scoring engine: SPEC.md weights, bot thresholds, ranges."""
import math

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from scoring_engine import compute_rank_scores


def row(**overrides):
    """A healthy human row that trips no bot heuristic."""
    r = {
        "username": "dev",
        "country": "",
        "primary_language": "Python",
        "total_pushes": 100,
        "merged_prs": 5,
        "merged_prs_external_weighted": 5.0,
        "closed_issues": 0,
        "active_repos_touched": 3,
        "unique_commit_messages": 90,
        "distinct_active_hours": 8,
        "avg_files_changed": 4.0,
        "push_hour_stddev": 3.0,
        "test_file_ratio": 0.2,
        "multi_file_commit_ratio": 0.5,
        "language_diversity": 2,
        "stars_fraud_filtered": 10,
        "forks_fraud_filtered": 2,
        "dependents": 0,
        "active_shipping_weeks": 10,
    }
    r.update(overrides)
    return r


def score(tmp_path, rows):
    path = tmp_path / "raw.parquet"
    pq.write_table(pa.Table.from_pylist(rows), str(path))
    return compute_rank_scores(str(path))


def expected_pillars(r):
    """Independent re-derivation of the SPEC formula."""
    s_pr = math.log1p(r["merged_prs_external_weighted"]) * 120
    s_quality = (
        math.log1p(r["test_file_ratio"]) * 60
        + math.log1p(r["multi_file_commit_ratio"]) * 40
        + math.log1p(r["language_diversity"]) * 30
    )
    s_impact = (
        math.log1p(r["stars_fraud_filtered"]) * 40
        + math.log1p(r["forks_fraud_filtered"]) * 30
        + math.log1p(r["dependents"]) * 30
    )
    s_consistency = (
        math.log1p(r["active_shipping_weeks"]) * 50 + (1 / (1 + r["push_hour_stddev"])) * 50
    )
    raw = s_pr * 0.35 + s_quality * 0.25 + s_impact * 0.25 + s_consistency * 0.15
    return s_pr, s_quality, s_impact, s_consistency, raw


def test_pillars_and_weights_match_spec(tmp_path):
    r = row()
    out = score(tmp_path, [r])[0]
    s_pr, s_quality, s_impact, s_consistency, raw = expected_pillars(r)

    assert out["s_pr"] == pytest.approx(s_pr, abs=0.01)
    assert out["s_quality"] == pytest.approx(s_quality, abs=0.01)
    assert out["s_impact"] == pytest.approx(s_impact, abs=0.01)
    assert out["s_consistency"] == pytest.approx(s_consistency, abs=0.01)
    assert out["bot_penalty"] == 0.0
    assert out["rank_score"] == pytest.approx(min(raw, 1000), abs=0.1)


def test_entropy_penalty_threshold(tmp_path):
    out = score(tmp_path, [row(unique_commit_messages=5)])[0]
    assert out["bot_penalty"] == 0.5
    assert out["is_human_verified"] is False
    assert out["is_bot_classified"] is False


def test_unknown_entropy_is_not_penalized(tmp_path):
    """No commit messages in the feed means entropy is unknown, not zero."""
    out = score(tmp_path, [row(unique_commit_messages=float("nan"))])[0]
    assert out["bot_penalty"] == 0.0
    assert out["is_human_verified"] is True


def test_circadian_penalty_threshold(tmp_path):
    out = score(tmp_path, [row(distinct_active_hours=23)])[0]
    assert out["bot_penalty"] == 0.3


def test_payload_delta_penalty_threshold(tmp_path):
    out = score(tmp_path, [row(avg_files_changed=1.0)])[0]
    assert out["bot_penalty"] == 0.4


def test_penalties_capped_and_bot_classified(tmp_path):
    out = score(tmp_path, [row(unique_commit_messages=1, distinct_active_hours=24, avg_files_changed=1.0)])[0]
    assert out["bot_penalty"] == 1.0
    assert out["is_bot_classified"] is True
    assert out["rank_score"] == 0.0


def test_verified_human_split(tmp_path):
    out = score(tmp_path, [row()])[0]
    assert out["is_human_verified"] is True
    assert out["is_bot_classified"] is False


def test_unknown_files_changed_is_not_penalized(tmp_path):
    """NaN means 'unobserved' (gharchive has no file lists), so it must not look like churn."""
    out = score(tmp_path, [row(avg_files_changed=float("nan"))])[0]
    assert out["bot_penalty"] == 0.0
    assert out["is_human_verified"] is True


def test_pr_only_user_is_not_penalized_for_entropy(tmp_path):
    out = score(
        tmp_path,
        [row(total_pushes=0, unique_commit_messages=0, merged_prs=10,
             merged_prs_external_weighted=15.0, avg_files_changed=float("nan"))],
    )[0]
    assert out["bot_penalty"] == 0.0
    assert out["rank_score"] > 0


def test_profile_metrics_pass_through(tmp_path):
    """The profile page reads these, so they must survive scoring (None when unknown)."""
    r = row(active_repos_touched=6, pr_reviews=3, issue_comments=2, releases=1,
            stars_fraud_filtered=11, forks_fraud_filtered=4)
    out = score(tmp_path, [r])[0]
    assert out["active_repos_touched"] == 6
    assert out["pr_reviews"] == 3
    assert out["issue_comments"] == 2
    assert out["releases"] == 1
    assert out["stars_fraud_filtered"] == 11
    assert out["forks_fraud_filtered"] == 4
    assert out["test_file_ratio"] == pytest.approx(0.2)

    unknown = score(tmp_path, [row(avg_files_changed=float("nan"))])[0]
    assert unknown["avg_files_changed"] is None


def test_scores_bounded_and_ranked(tmp_path):
    rows = [
        row(username="a", merged_prs_external_weighted=40.0, stars_fraud_filtered=500),
        row(username="b"),
        row(username="c", unique_commit_messages=1, distinct_active_hours=24, avg_files_changed=1.0),
    ]
    out = score(tmp_path, rows)
    assert [r["rank"] for r in out] == [1, 2, 3]
    assert [r["username"] for r in out] == ["a", "b", "c"]
    assert all(0 <= r["rank_score"] <= 1000 for r in out)
    assert out[0]["rank_score"] >= out[1]["rank_score"] >= out[2]["rank_score"]
