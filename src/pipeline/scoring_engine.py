"""RealGitRank scoring engine — vectorized DuckDB, matches SPEC.md weights."""
import duckdb
import numpy as np


def compute_rank_scores(parquet_file_path: str):
    con = duckdb.connect()
    df = con.execute(f"SELECT * FROM read_parquet('{parquet_file_path}')").fetchdf()

    # 1. Bot penalty (vectorized, NOT pandas row-wise)
    df['entropy_ratio'] = df['unique_commit_messages'] / df['total_pushes'].clip(lower=1)
    df['bot_penalty'] = 0.0
    df.loc[df['entropy_ratio'] < 0.10, 'bot_penalty'] += 0.50
    df.loc[df['distinct_active_hours'] >= 23, 'bot_penalty'] += 0.30
    if 'avg_files_changed' in df.columns:
        df.loc[df['avg_files_changed'] < 1.05, 'bot_penalty'] += 0.40
    df['bot_penalty'] = df['bot_penalty'].clip(0, 1.0)

    # 2. Pillars (weights MUST match SPEC.md: 0.35 / 0.25 / 0.25 / 0.15)
    df['s_pr'] = np.log1p(df.get('merged_prs_external_weighted', df['merged_prs'])) * 120
    df['s_quality'] = (np.log1p(df.get('test_file_ratio', 0)) * 60
                       + np.log1p(df.get('multi_file_commit_ratio', 0)) * 40
                       + np.log1p(df.get('language_diversity', df['active_repos_touched'])) * 30)
    df['s_impact'] = (np.log1p(df.get('stars_fraud_filtered', 0)) * 40
                      + np.log1p(df.get('forks_fraud_filtered', 0)) * 30
                      + np.log1p(df.get('dependents', 0)) * 30)
    df['s_consistency'] = (np.log1p(df.get('active_shipping_weeks', df['distinct_active_hours'])) * 50
                           + (1 / (1 + df.get('push_hour_stddev', 1).fillna(1))) * 50)

    # 3. Composite
    df['raw_score'] = (df['s_pr'] * 0.35
                      + df['s_quality'] * 0.25
                      + df['s_impact'] * 0.25
                      + df['s_consistency'] * 0.15)
    df['rank_score'] = (df['raw_score'] * (1 - df['bot_penalty'])).clip(0, 1000).round(1)

    # Segregate humans vs bots
    df['is_human_verified'] = df['bot_penalty'] < 0.05
    df['is_bot_classified'] = df['bot_penalty'] >= 0.70

    return df