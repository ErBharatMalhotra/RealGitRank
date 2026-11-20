"""RealGitRank scoring engine — vectorized via DuckDB + numpy (no pandas). Matches SPEC.md weights.

Unknown quality metrics arrive as NaN from the ingest layer (gharchive does not expose
everything). NaN means "we could not observe this", so it must never be read as a bad
signal: the payload-delta bot penalty is skipped for unknown values and pillars treat
unknown as zero credit rather than guessing a number.
"""
import duckdb
import numpy as np


def _load_columns(parquet_file_path):
    con = duckdb.connect()
    rel = con.execute(f"SELECT * FROM read_parquet('{parquet_file_path}')")
    cols = [d[0] for d in rel.description]
    rows = rel.fetchall()
    con.close()
    return cols, rows


def _float_col(data, col, n):
    """Numeric column as a float array, or None when the column is absent. NaN kept."""
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
    """Numeric column with missing/NaN replaced by a neutral default."""
    arr = _float_col(data, col, n)
    if arr is None:
        return np.full(n, float(default))
    return np.where(np.isnan(arr), float(default), arr)


def compute_rank_scores(parquet_file_path):
    cols, rows = _load_columns(parquet_file_path)
    n = len(rows)
    data = {c: [r[i] for r in rows] for i, c in enumerate(cols)}

    total_pushes = _num(data, "total_pushes", 0.0, n)
    unique_msgs = _num(data, "unique_commit_messages", 0.0, n)
    distinct_hours = _num(data, "distinct_active_hours", 1.0, n)
    merged_prs = _num(data, "merged_prs", 0.0, n)

    merged_ext = _float_col(data, "merged_prs_external_weighted", n)
    merged_ext = merged_prs if merged_ext is None else np.where(np.isnan(merged_ext), merged_prs, merged_ext)

    # NaN = unknown (never penalised, never credited)
    avg_files = _float_col(data, "avg_files_changed", n)
    push_std = _num(data, "push_hour_stddev", 4.0, n)
    test_ratio = _num(data, "test_file_ratio", 0.0, n)
    multi_file = _num(data, "multi_file_commit_ratio", 0.0, n)

    lang_div = _float_col(data, "language_diversity", n)
    active_repos = _num(data, "active_repos_touched", 1.0, n)
    lang_div = active_repos if lang_div is None else np.where(np.isnan(lang_div), active_repos, lang_div)

    ship_weeks = _float_col(data, "active_shipping_weeks", n)
    if ship_weeks is None:
        ship_weeks = distinct_hours.copy()
    else:
        ship_weeks = np.where(np.isnan(ship_weeks), distinct_hours, ship_weeks)

    stars = _num(data, "stars_fraud_filtered", 0.0, n)
    forks = _num(data, "forks_fraud_filtered", 0.0, n)
    dependents = _num(data, "dependents", 0.0, n)

    # 1. Bot penalty (vectorized)
    denom = np.maximum(total_pushes, 1.0)
    entropy_ratio = unique_msgs / denom
    pushes_exist = total_pushes > 0
    bot_penalty = np.zeros(n)
    bot_penalty = np.where(pushes_exist & (entropy_ratio < 0.10), bot_penalty + 0.50, bot_penalty)
    bot_penalty = np.where(distinct_hours >= 23, bot_penalty + 0.30, bot_penalty)
    if avg_files is not None:
        files_known = ~np.isnan(avg_files)
        bot_penalty = np.where(files_known & (avg_files < 1.05), bot_penalty + 0.40, bot_penalty)
    bot_penalty = np.clip(bot_penalty, 0, 1.0)

    # 2. Pillars (0-1000, percentile-normalized upstream)
    #    Weights MUST match SPEC.md: 0.35 / 0.25 / 0.25 / 0.15
    s_pr = np.log1p(merged_ext) * 120
    s_quality = np.log1p(test_ratio) * 60 + np.log1p(multi_file) * 40 + np.log1p(lang_div) * 30
    s_impact = np.log1p(stars) * 40 + np.log1p(forks) * 30 + np.log1p(dependents) * 30
    s_consistency = np.log1p(ship_weeks) * 50 + (1 / (1 + push_std)) * 50

    raw_score = s_pr * 0.35 + s_quality * 0.25 + s_impact * 0.25 + s_consistency * 0.15
    rank_score = np.clip(raw_score * (1 - bot_penalty), 0, 1000).round(1)

    usernames = data.get("username", [""] * n)
    countries = data.get("country", [""] * n)
    languages = data.get("primary_language", ["Unknown"] * n)

    result = []
    for i in range(n):
        result.append({
            "username": str(usernames[i] or ""),
            "country": str(countries[i] or ""),
            "primary_language": str(languages[i] or "Unknown"),
            "total_pushes": int(data["total_pushes"][i]) if "total_pushes" in data else 0,
            "merged_prs": int(data["merged_prs"][i]) if "merged_prs" in data else 0,
            "bot_penalty": round(float(bot_penalty[i]), 3),
            "is_human_verified": bool(bot_penalty[i] < 0.05),
            "is_bot_classified": bool(bot_penalty[i] >= 0.70),
            "s_pr": round(float(s_pr[i]), 2),
            "s_quality": round(float(s_quality[i]), 2),
            "s_impact": round(float(s_impact[i]), 2),
            "s_consistency": round(float(s_consistency[i]), 2),
            "rank_score": round(float(rank_score[i]), 1),
        })

    result.sort(key=lambda x: (x["rank_score"], x["merged_prs"]), reverse=True)
    for idx, r in enumerate(result, 1):
        r["rank"] = idx
    return result
