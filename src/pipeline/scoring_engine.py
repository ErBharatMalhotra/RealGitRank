"""RealGitRank scoring engine — vectorized via DuckDB + numpy (no pandas). Matches SPEC.md weights."""
import duckdb
import numpy as np


def compute_rank_scores(parquet_file_path: str):
    con = duckdb.connect()
    rel = con.execute(f"SELECT * FROM read_parquet('{parquet_file_path}')")
    cols = [d[0] for d in rel.description]
    rows = rel.fetchall()
    data = {c: np.array([r[i] for r in rows], dtype=object) for i, c in enumerate(cols)}

    def get(col, default=0):
        if col in data:
            arr = data[col]
            try:
                return arr.astype(float)
            except (ValueError, TypeError):
                return arr
        n = len(rows)
        return np.zeros(n) if isinstance(default, (int, float)) else np.array([default] * n, dtype=object)

    total_pushes = get('total_pushes').astype(float).clip(min=1)
    unique_msgs = get('unique_commit_messages').astype(float)
    distinct_hours = get('distinct_active_hours').astype(float)
    merged_prs = get('merged_prs').astype(float)
    merged_ext = get('merged_prs_external_weighted', merged_prs).astype(float)
    avg_files = get('avg_files_changed', 1.5).astype(float)
    push_std = get('push_hour_stddev', 4.0)
    push_std = np.where(np.isnan(push_std.astype(float)), 4.0, push_std.astype(float))

    # 1. Bot penalty (vectorized)
    entropy_ratio = unique_msgs / total_pushes
    bot_penalty = np.zeros(len(rows))
    bot_penalty = np.where(entropy_ratio < 0.10, bot_penalty + 0.50, bot_penalty)
    bot_penalty = np.where(distinct_hours >= 23, bot_penalty + 0.30, bot_penalty)
    bot_penalty = np.where(avg_files < 1.05, bot_penalty + 0.40, bot_penalty)
    bot_penalty = np.clip(bot_penalty, 0, 1.0)

    # 2. Pillars (weights MUST match SPEC.md: 0.35 / 0.25 / 0.25 / 0.15)
    test_ratio = get('test_file_ratio', 0).astype(float)
    multi_file = get('multi_file_commit_ratio', 0).astype(float)
    lang_div = get('language_diversity', get('active_repos_touched', 1)).astype(float)
    stars = get('stars_fraud_filtered', 0).astype(float)
    forks = get('forks_fraud_filtered', 0).astype(float)
    dependents = get('dependents', 0).astype(float)
    ship_weeks = get('active_shipping_weeks', distinct_hours).astype(float)

    s_pr = np.log1p(merged_ext) * 120
    s_quality = np.log1p(test_ratio) * 60 + np.log1p(multi_file) * 40 + np.log1p(lang_div) * 30
    s_impact = np.log1p(stars) * 40 + np.log1p(forks) * 30 + np.log1p(dependents) * 30
    s_consistency = np.log1p(ship_weeks) * 50 + (1 / (1 + push_std)) * 50

    raw_score = s_pr * 0.35 + s_quality * 0.25 + s_impact * 0.25 + s_consistency * 0.15
    rank_score = np.clip(raw_score * (1 - bot_penalty), 0, 1000).round(1)

    # Assemble result list of dicts
    result = []
    for i in range(len(rows)):
        result.append({
            "username": str(data["username"][i]),
            "country": str(data["country"][i]),
            "primary_language": str(data["primary_language"][i]),
            "total_pushes": int(data["total_pushes"][i]),
            "merged_prs": int(data["merged_prs"][i]),
            "bot_penalty": float(bot_penalty[i]),
            "is_human_verified": bool(bot_penalty[i] < 0.05),
            "is_bot_classified": bool(bot_penalty[i] >= 0.70),
            "s_pr": float(s_pr[i]),
            "s_quality": float(s_quality[i]),
            "s_impact": float(s_impact[i]),
            "s_consistency": float(s_consistency[i]),
            "rank_score": float(rank_score[i]),
        })
    # sort
    result.sort(key=lambda x: (x["rank_score"], x["merged_prs"]), reverse=True)
    for idx, r in enumerate(result, 1):
        r["rank"] = idx
    return result