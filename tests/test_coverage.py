"""Coverage must report what was actually observed."""
from coverage import build_coverage


def entry(**overrides):
    base = {
        "username": "dev",
        "country": "",
        "primary_language": "Unknown",
        "measured": {"delivery": True, "code_health": False, "adoption": True, "consistency": True},
        "bot_evidence": False,
    }
    base.update(overrides)
    return base


def test_coverage_counts_measured_pillars():
    coverage = build_coverage([
        entry(),
        entry(username="b", measured={"delivery": True, "code_health": True,
                                      "adoption": True, "consistency": True},
              bot_evidence=True, country="India", primary_language="Python"),
        entry(username="c", bot_evidence=True, country="India"),
    ], {"window_days": 30, "window_start": "2026-10-25", "window_end": "2026-11-23",
        "score_version": "v2-percentile"})

    assert coverage["users"] == 3
    assert coverage["pillars"]["code_health"] == {
        "label": "Test-signal ratio and multi-file PRs", "measured": 1, "pct": 33.3,
    }
    assert coverage["automation_evidence"]["pct"] == 66.7
    assert coverage["country"]["pct"] == 66.7
    assert coverage["language"]["pct"] == 33.3
    assert coverage["window_days"] == 30


def test_coverage_handles_empty_board():
    coverage = build_coverage([], {})
    assert coverage["users"] == 0
    assert coverage["pillars"]["delivery"]["pct"] == 0.0


def test_coverage_carries_validation():
    coverage = build_coverage([entry()], {}, {"match_rate": 92.5, "sample_size": 200})
    assert coverage["validation"]["match_rate"] == 92.5
