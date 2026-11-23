"""RealGitRank scoring engine v2 — percentile pillars over a rolling window.

Every pillar is ranked inside the cohort of users where that pillar is actually
observed, then mapped to 0–100. The composite is the weighted average of the
available pillars (weights renormalised when a pillar is unknown) and scaled to
0–1000, discounted by the behavioural bot penalty. A missing signal is never
read as a bad score.

Unknown values arrive as NaN from the ingest layer and stay unknown: they are
excluded from that user's composite instead of being treated as zero.
"""
import duckdb
import numpy as np

WEIGHTS = {
    "delivery": 0.35,
    "code_health": 0.25,
    "adoption": 0.25,
    "consistency": 0.15,
}


def _load_columns(parquet_file_path):
    con = duckdb.connect()
    rel = con.execute(f"SELECT * FROM read_parquet('{parquet_file_path}')")
    cols = [d[0] for d in rel.description]
    rows = rel.fetchall()
    con.close()
    return cols, rows


def _float_col(data, col, n):
    """Numeric column as a float array, or None when the column is absent."""
    if col not in data:
        return None
    out = np.empty(n, dtype=float)
    for i, v in enumerate(data[col]):
        try:
            out[i] = float(v)
        except (TypeError, ValueError):
            out[i] = np.nan
    return out


def _num(data, col, default, n):
    arr = _float_col(data, col, n)
    if arr is None:
        return np.full(n, float(default))
    return np.where(np.isnan(arr), float(default), arr)


def _bool_col(data, col, n):
    if col not in data:
        return None
    return np.array([bool(v) for v in data[col]], dtype=bool)


def _percentile(values, measured):
    """Percentile rank (0–100, tie-averaged) inside the measured cohort."""
    out = np.full(len(values), np.nan)
    idx = np.flatnonzero(measured)
    n = len(idx)
    if n == 0:
        return out
    v = values[idx]
    if n == 1:
        # A one-user cohort says nothing about relative standing; stay neutral.
        out[idx] = 50.0
        return out
    _, inv = np.unique(v, return_inverse=True)
    counts = np.bincount(inv)
    cumulative = np.cumsum(counts)
    average_rank0 = (cumulative - counts + cumulative - 1) / 2.0
    out[idx] = average_rank0[inv] / (n - 1) * 100.0
    return out


def compute_rank_scores(parquet_file_path):
    cols, rows = _load_columns(parquet_file_path)
    n = len(rows)
    data = {c: [r[i] for r in rows] for i, c in enumerate(cols)}

    total_pushes = _num(data, "total_pushes", 0.0, n)
    unique_raw = _float_col(data, "unique_commit_messages", n)
    unique_msgs = _num(data, "unique_commit_messages", 0.0, n)
    # Window rows carry an explicit average; single-day rows carry distinct hours.
    hours = _float_col(data, "avg_distinct_active_hours", n)
    if hours is None:
        hours = _num(data, "distinct_active_hours", 1.0, n)
    hours = np.where(np.isnan(hours), 1.0, hours)
    merged_prs = _num(data, "merged_prs", 0.0, n)
    merged_ext = _float_col(data, "merged_prs_external_weighted", n)
    merged_ext = merged_prs if merged_ext is None else np.where(np.isnan(merged_ext), merged_prs, merged_ext)

    avg_files = _float_col(data, "avg_files_changed", n)
    push_std = _num(data, "push_hour_stddev", 4.0, n)
    test_ratio = _float_col(data, "test_file_ratio", n)
    multi_file = _float_col(data, "multi_file_commit_ratio", n)
    ship_weeks = _float_col(data, "active_shipping_weeks", n)
    if ship_weeks is None:
        ship_weeks = hours.copy()
    ship_weeks = np.where(np.isnan(ship_weeks), 1.0, ship_weeks)

    stars = _num(data, "stars_fraud_filtered", 0.0, n)
    forks = _num(data, "forks_fraud_filtered", 0.0, n)
    dependents = _num(data, "dependents", 0.0, n)

    # Evidence flags: was the signal observed at all?
    entropy_known_col = _bool_col(data, "entropy_known", n)
    files_known_col = _bool_col(data, "files_known", n)
    entropy_known = (~np.isnan(unique_raw)) if unique_raw is not None else np.zeros(n, bool)
    if entropy_known_col is not None:
        entropy_known = entropy_known_col
    files_known = (~np.isnan(avg_files)) if avg_files is not None else np.zeros(n, bool)
    if files_known_col is not None:
        files_known = files_known_col

    # 1. Bot penalty (only on observed evidence)
    denom = np.maximum(total_pushes, 1.0)
    entropy_ratio = unique_msgs / denom
    pushes_exist = total_pushes > 0
    bot_penalty = np.zeros(n)
    bot_penalty = np.where(
        pushes_exist & entropy_known & (entropy_ratio < 0.10), bot_penalty + 0.50, bot_penalty
    )
    bot_penalty = np.where(hours >= 23, bot_penalty + 0.30, bot_penalty)
    if avg_files is not None:
        bot_penalty = np.where(files_known & (avg_files < 1.05), bot_penalty + 0.40, bot_penalty)
    bot_penalty = np.clip(bot_penalty, 0, 1.0)

    # 2. Raw pillars. Quality needs observed test/file evidence; languages are not
    #    in the events feed, so no repos-touched proxy is substituted here.
    s_pr_raw = np.log1p(merged_ext) * 120
    s_quality_raw = np.zeros(n)
    quality_measured = np.zeros(n, bool)
    if test_ratio is not None:
        quality_measured |= ~np.isnan(test_ratio)
        s_quality_raw += np.log1p(np.nan_to_num(test_ratio, nan=0.0)) * 60
    if multi_file is not None:
        quality_measured |= ~np.isnan(multi_file)
        s_quality_raw += np.log1p(np.nan_to_num(multi_file, nan=0.0)) * 40
    s_impact_raw = np.log1p(stars) * 40 + np.log1p(forks) * 30 + np.log1p(dependents) * 30
    s_consistency_raw = np.log1p(ship_weeks) * 50 + (1 / (1 + push_std)) * 50

    measured = {
        "delivery": np.ones(n, bool),
        "code_health": quality_measured,
        "adoption": np.ones(n, bool),
        "consistency": np.ones(n, bool),
    }
    raw_pillars = {
        "delivery": s_pr_raw,
        "code_health": s_quality_raw,
        "adoption": s_impact_raw,
        "consistency": s_consistency_raw,
    }
    pillar_pct = {k: _percentile(v, measured[k]) for k, v in raw_pillars.items()}

    weighted = np.zeros(n)
    weight_sum = np.zeros(n)
    for key, weight in WEIGHTS.items():
        available = measured[key]
        weighted += np.where(available, pillar_pct[key], 0.0) * weight
        weight_sum += np.where(available, weight, 0.0)
    composite = np.divide(weighted, weight_sum, out=np.zeros(n), where=weight_sum > 0)
    rank_score = np.clip(composite * 10.0 * (1 - bot_penalty), 0, 1000).round(1)

    usernames = data.get("username", [""] * n)
    countries = data.get("country", [""] * n)
    languages = data.get("primary_language", [""] * n)
    merged_repos = data.get("merged_repos")
    active_days = _num(data, "active_days", 0.0, n)
    window_days = _num(data, "window_days", 0.0, n)

    def passthrough(col, default=0):
        arr = _float_col(data, col, n)
        if arr is None:
            return [default] * n
        return [default if np.isnan(v) else int(v) for v in arr]

    extra = {col: passthrough(col) for col in (
        "active_repos_touched", "pr_reviews", "issue_comments", "releases",
        "stars_fraud_filtered", "forks_fraud_filtered", "active_shipping_weeks",
    )}
    known_test_ratio = test_ratio
    known_multi_file = multi_file
    known_files = avg_files

    result = []
    for i in range(n):
        entry = {
            "username": str(usernames[i] or ""),
            "country": str(countries[i] or ""),
            "primary_language": str(languages[i] or "Unknown"),
            "total_pushes": int(data["total_pushes"][i]) if "total_pushes" in data else 0,
            "merged_prs": int(data["merged_prs"][i]) if "merged_prs" in data else 0,
            "bot_penalty": round(float(bot_penalty[i]), 3),
            "bot_evidence": bool(entropy_known[i] or files_known[i]),
            "is_human_verified": bool((entropy_known[i] or files_known[i]) and bot_penalty[i] < 0.05),
            "is_bot_classified": bool(bot_penalty[i] >= 0.70),
            "s_pr": round(float(pillar_pct["delivery"][i]), 2),
            "s_quality": (None if np.isnan(pillar_pct["code_health"][i])
                          else round(float(pillar_pct["code_health"][i]), 2)),
            "s_impact": round(float(pillar_pct["adoption"][i]), 2),
            "s_consistency": round(float(pillar_pct["consistency"][i]), 2),
            "measured": {key: bool(measured[key][i]) for key in WEIGHTS},
            "active_days": int(active_days[i]) if not np.isnan(active_days[i]) else 0,
            "window_days": int(window_days[i]) if not np.isnan(window_days[i]) else 0,
            "rank_score": round(float(rank_score[i]), 1),
        }
        if merged_repos is not None:
            value = merged_repos[i]
            if isinstance(value, dict) and value:
                entry["counted_merges"] = [
                    {"repo": repo, "prs": int(count)}
                    for repo, count in sorted(value.items(), key=lambda kv: (-int(kv[1]), kv[0]))[:10]
                ]
        for col, values in extra.items():
            entry[col] = values[i]
        for key, arr in (
            ("avg_files_changed", known_files),
            ("test_file_ratio", known_test_ratio),
            ("multi_file_commit_ratio", known_multi_file),
        ):
            value = None if arr is None else arr[i]
            entry[key] = None if value is None or np.isnan(value) else round(float(value), 3)
        result.append(entry)

    result.sort(key=lambda x: (x["rank_score"], x["merged_prs"], x["username"]), reverse=True)
    for idx, r in enumerate(result, 1):
        r["rank"] = idx
    return result
