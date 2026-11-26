"""Coverage must report what was actually observed."""
from coverage import build_coverage


def entry(**overrides):
    base = {
        "username": "dev",
        "country": "",
        "primary_language": "Unknown",
        "total_pushes": 0,
        "merged_prs": 0,
        "closed_issues": 0,
        "pr_reviews": 0,
        "issue_comments": 0,
        "releases": 0,
    }
    base.update(overrides)
    return base


def test_coverage_counts_observed_components():
    coverage = build_coverage([
        entry(total_pushes=10),
        entry(username="b", total_pushes=5, merged_prs=2, closed_issues=1,
              country="India", primary_language="Python"),
        entry(username="c", merged_prs=3, pr_reviews=4, country="India"),
    ], {"window_days": 365, "window_months": 12, "window_start": "2025-11-25",
        "window_end": "2026-11-24", "score_version": "v3-activity"})

    assert coverage["users"] == 3
    assert coverage["signals"]["total_pushes"] == {"label": "Pushes", "observed": 2, "pct": 66.7}
    assert coverage["signals"]["merged_prs"]["observed"] == 2
    assert coverage["signals"]["releases"]["observed"] == 0
    assert coverage["country"]["pct"] == 66.7
    assert coverage["language"]["pct"] == 33.3
    assert coverage["window_days"] == 365
    assert coverage["score_version"] == "v3-activity"


def test_coverage_handles_empty_board():
    coverage = build_coverage([], {})
    assert coverage["users"] == 0
    assert coverage["signals"]["total_pushes"]["pct"] == 0.0


def test_coverage_carries_validation():
    coverage = build_coverage([entry()], {}, {"match_rate": 92.5, "sample_size": 200})
    assert coverage["validation"]["match_rate"] == 92.5
