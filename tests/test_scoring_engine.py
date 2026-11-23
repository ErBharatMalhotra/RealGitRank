"""Scoring v2: percentile pillars, missing-pillar renormalisation, bot evidence."""
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from scoring_engine import compute_rank_scores


def row(**overrides):
    """A healthy human row with every signal observed."""
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
        "avg_distinct_active_hours": 8,
        "avg_files_changed": 4.0,
        "push_hour_stddev": 3.0,
        "test_file_ratio": 0.2,
        "multi_file_commit_ratio": 0.5,
        "language_diversity": float("nan"),
        "stars_fraud_filtered": 10,
        "forks_fraud_filtered": 2,
        "dependents": 0,
        "active_shipping_weeks": 10,
        "pr_reviews": 0,
        "issue_comments": 0,
        "releases": 0,
        "active_days": 12,
        "window_days": 30,
        "entropy_known": True,
        "files_known": True,
    }
    r.update(overrides)
    return r


def score(tmp_path, rows):
    path = tmp_path / "raw.parquet"
    pq.write_table(pa.Table.from_pylist(rows), str(path))
    return compute_rank_scores(str(path))


def test_score_is_percentile_scale_and_monotone(tmp_path):
    out = score(tmp_path, [
        row(username="a", merged_prs_external_weighted=0.0, stars_fraud_filtered=0),
        row(username="b", merged_prs_external_weighted=5.0, stars_fraud_filtered=5),
        row(username="c", merged_prs_external_weighted=50.0, stars_fraud_filtered=100),
    ])
    by_name = {r["username"]: r for r in out}
    assert 0 <= by_name["a"]["rank_score"] < by_name["b"]["rank_score"] < by_name["c"]["rank_score"] <= 1000
    assert by_name["c"]["s_pr"] == 100.0
    assert by_name["a"]["s_pr"] == 0.0
    assert all(0 <= r["rank_score"] <= 1000 for r in out)
    assert [r["rank"] for r in out] == [1, 2, 3]


def test_all_pillars_measured_for_full_rows(tmp_path):
    out = score(tmp_path, [row(username="a"), row(username="b", merged_prs_external_weighted=1.0)])
    for entry in out:
        assert entry["measured"] == {
            "delivery": True, "code_health": True, "adoption": True, "consistency": True,
        }
        assert entry["s_quality"] is not None


def test_missing_quality_is_excluded_not_zeroed(tmp_path):
    """A user with no test/file evidence keeps delivery/adoption/consistency weight."""
    unknown = row(username="unknown", test_file_ratio=float("nan"),
                  multi_file_commit_ratio=float("nan"), files_known=False,
                  merged_prs_external_weighted=50.0)
    known = row(username="known", test_file_ratio=0.0, multi_file_commit_ratio=0.0,
                merged_prs_external_weighted=50.0)
    out = score(tmp_path, [unknown, known])
    by_name = {r["username"]: r for r in out}
    assert by_name["unknown"]["measured"]["code_health"] is False
    assert by_name["unknown"]["s_quality"] is None
    assert by_name["known"]["measured"]["code_health"] is True
    # Equal delivery/adoption/consistency: excluding quality must not punish the user.
    assert by_name["unknown"]["rank_score"] >= by_name["known"]["rank_score"]


def test_verified_requires_observed_evidence(tmp_path):
    no_evidence = row(username="ghost", total_pushes=0, unique_commit_messages=float("nan"),
                      avg_files_changed=float("nan"), entropy_known=False, files_known=False,
                      merged_prs=1, merged_prs_external_weighted=1.0)
    observed = row(username="human")
    out = score(tmp_path, [no_evidence, observed])
    by_name = {r["username"]: r for r in out}
    assert by_name["ghost"]["bot_evidence"] is False
    assert by_name["ghost"]["is_human_verified"] is False
    assert by_name["human"]["bot_evidence"] is True
    assert by_name["human"]["is_human_verified"] is True


def test_entropy_penalty_threshold(tmp_path):
    out = score(tmp_path, [row(unique_commit_messages=5)])[0]
    assert out["bot_penalty"] == 0.5
    assert out["is_human_verified"] is False
    assert out["is_bot_classified"] is False


def test_unknown_entropy_is_not_penalized_when_files_observed(tmp_path):
    out = score(tmp_path, [row(unique_commit_messages=float("nan"), entropy_known=False)])[0]
    assert out["bot_penalty"] == 0.0
    assert out["bot_evidence"] is True


def test_circadian_penalty_threshold(tmp_path):
    out = score(tmp_path, [row(avg_distinct_active_hours=23)])[0]
    assert out["bot_penalty"] == 0.3


def test_payload_delta_penalty_threshold(tmp_path):
    out = score(tmp_path, [row(avg_files_changed=1.0)])[0]
    assert out["bot_penalty"] == 0.4


def test_penalties_capped_and_bot_classified(tmp_path):
    out = score(tmp_path, [row(unique_commit_messages=1, avg_distinct_active_hours=24,
                               avg_files_changed=1.0)])[0]
    assert out["bot_penalty"] == 1.0
    assert out["is_bot_classified"] is True
    assert out["rank_score"] == 0.0


def test_single_day_rows_still_supported(tmp_path):
    legacy = row(avg_distinct_active_hours=None)
    legacy.pop("avg_distinct_active_hours")
    legacy["distinct_active_hours"] = 8
    legacy.pop("entropy_known")
    legacy.pop("files_known")
    out = score(tmp_path, [legacy])[0]
    assert out["rank_score"] > 0
    assert out["bot_evidence"] is True


def test_profile_metrics_pass_through(tmp_path):
    r = row(active_repos_touched=6, pr_reviews=3, issue_comments=2, releases=1,
            stars_fraud_filtered=11, forks_fraud_filtered=4,
            counted_merges=[{"repo": "a/b", "prs": 3}])
    out = score(tmp_path, [r])[0]
    assert out["active_repos_touched"] == 6
    assert out["pr_reviews"] == 3
    assert out["issue_comments"] == 2
    assert out["releases"] == 1
    assert out["stars_fraud_filtered"] == 11
    assert out["forks_fraud_filtered"] == 4
    assert out["test_file_ratio"] == pytest.approx(0.2)
    assert out["active_days"] == 12

    unknown = score(tmp_path, [row(avg_files_changed=float("nan"), files_known=False)])[0]
    assert unknown["avg_files_changed"] is None
