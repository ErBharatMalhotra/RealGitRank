"""Tests for the SPEC quality gates and the rolling baseline."""
import textwrap

from quality_gates import CONFIG_PATH, DEFAULT_GATES, append_history, evaluate, load_gate_config, summarize

GATES = dict(DEFAULT_GATES)


def baseline(runs=6, rows=1000, mean=300.0, std=50.0, bot=0.02):
    return [
        {
            "date": f"2026-11-{i + 1:02d}",
            "rows": rows,
            "null_usernames": 0,
            "score_min": 0.0,
            "score_max": 900.0,
            "score_mean": mean,
            "score_std": std,
            "bot_rate": bot,
        }
        for i in range(runs)
    ]


def today(rows=1000, mean=300.0, std=50.0, bot=0.02, nulls=0):
    return {
        "rows": rows,
        "null_usernames": nulls,
        "score_min": 0.0,
        "score_max": 950.0,
        "score_mean": mean,
        "score_std": std,
        "bot_rate": bot,
    }


def test_clean_snapshot_passes():
    errors, notes = evaluate(today(), baseline(), GATES)
    assert errors == []
    assert notes == []


def test_row_count_drop_fails():
    errors, _ = evaluate(today(rows=700), baseline(), GATES)
    assert any("row count" in e for e in errors)


def test_score_shift_fails():
    errors, _ = evaluate(today(mean=600.0), baseline(), GATES)
    assert any("shifted" in e for e in errors)


def test_small_score_drift_passes():
    errors, _ = evaluate(today(mean=350.0), baseline(), GATES)
    assert errors == []


def test_bot_rate_jump_fails():
    errors, _ = evaluate(today(bot=0.10), baseline(bot=0.02), GATES)
    assert any("bot classification" in e for e in errors)


def test_bot_rate_jump_ignored_below_floor():
    errors, _ = evaluate(today(bot=0.10), baseline(bot=0.0), GATES)
    assert errors == []


def test_null_username_fails():
    errors, _ = evaluate(today(nulls=1), baseline(), GATES)
    assert any("null usernames" in e for e in errors)


def test_null_username_rate_threshold_comes_from_config():
    """username_null_max_pct used to be ignored: any null failed the build."""
    permissive = dict(GATES, username_null_max_pct=1)
    errors, _ = evaluate(today(nulls=2), baseline(), permissive)  # 0.2% of 1000 rows
    assert errors == []

    strict = dict(GATES, username_null_max_pct=0)
    errors, _ = evaluate(today(nulls=2), baseline(), strict)
    assert any("null usernames" in e for e in errors)


def test_min_rows_absolute_floor_comes_from_config():
    errors, _ = evaluate(today(rows=1000), baseline(runs=0), dict(GATES, min_rows_absolute=5000))
    assert any("row count too low" in e for e in errors)

    errors, _ = evaluate(today(rows=1000), baseline(runs=0), dict(GATES, min_rows_absolute=10))
    assert errors == []


def test_out_of_range_and_flat_scores_fail():
    snapping = today()
    snapping["score_max"] = 1200
    errors, _ = evaluate(snapping, baseline(), GATES)
    assert any("out of range" in e for e in errors)

    flat, _ = evaluate(today(std=0.0), baseline(), GATES)
    assert any("zero variance" in e for e in flat)


def test_short_baseline_skips_growth_gates():
    errors, notes = evaluate(today(rows=700), baseline(runs=2), GATES)
    assert errors == []
    assert notes and "baseline" in notes[0]


def test_summarize_leaderboard():
    lb = [
        {"username": "a", "rank_score": 100.0, "is_bot_classified": False},
        {"username": "b", "rank_score": 300.0, "is_bot_classified": True},
        {"username": "", "rank_score": 200.0, "is_bot_classified": False},
    ]
    s = summarize(lb)
    assert s["rows"] == 3
    assert s["null_usernames"] == 1
    assert s["score_min"] == 100.0
    assert s["score_max"] == 300.0
    assert s["bot_rate"] == round(1 / 3, 5)


def test_gates_load_thresholds_from_pipeline_yaml():
    gates = load_gate_config(CONFIG_PATH)
    assert gates["row_count_min_pct_of_30d_median"] == 80
    assert gates["score_shift_sigma_max"] == 3
    assert gates["bot_classification_jump_max_x"] == 2
    assert gates["min_baseline_days"] == 5
    assert gates["min_rows_absolute"] == 10
    assert set(DEFAULT_GATES) <= set(gates)


def test_thresholds_from_a_config_file_change_the_verdict(tmp_path):
    """Proof the gates read the YAML: same snapshot, two configs, two outcomes."""
    config = tmp_path / "pipeline.yaml"

    def write(pct):
        config.write_text(
            textwrap.dedent(
                f"""
                quality_gates:
                  row_count_min_pct_of_30d_median: {pct}
                  username_null_max_pct: 0
                  score_shift_sigma_max: 3
                  bot_classification_jump_max_x: 2
                """
            ),
            encoding="utf-8",
        )
        return load_gate_config(str(config))

    snapshot = today(rows=700)  # below 80% of the 1000-row baseline
    assert any("row count" in e for e in evaluate(snapshot, baseline(), write(80))[0])
    assert evaluate(snapshot, baseline(), write(50))[0] == []


def test_append_history_replaces_same_date_and_caps():
    history = baseline(runs=30)
    out = append_history(history, today(rows=1234), "2026-11-01")
    assert len(out) == 30
    assert out[-1]["date"] == "2026-11-01"
    assert out[-1]["rows"] == 1234
    assert sum(1 for h in out if h["date"] == "2026-11-01") == 1
