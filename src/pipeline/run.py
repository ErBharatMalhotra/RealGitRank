"""RealGitRank pipeline runner: real BigQuery ingest if credentials exist, else mock demo data."""
import json, os, random, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from datetime import datetime, timezone
import duckdb
from scoring_engine import compute_rank_scores

# Try real BigQuery ingest first
used_real = False
try:
    from ingest_bigquery import ingest as bq_ingest
    used_real = bq_ingest()
except Exception as e:
    print(f"BigQuery module error: {e}")

# If no real data, generate mock (for local dev / demo)
if not used_real or not os.path.exists("data/raw.parquet"):
    print("Generating mock data for demo...")
    random.seed(42)
    N = 5000  # scaled up for worldwide demo
    countries = ["India", "United States", "Germany", "United Kingdom", "Japan", "Brazil", "France", "Canada",
                 "China", "Russia", "Australia", "Spain", "Italy", "Mexico", "South Korea", "Netherlands",
                 "Sweden", "Poland", "Turkey", "Indonesia", "Saudi Arabia", "Argentina", "South Africa",
                 "Nigeria", "Egypt", "Thailand", "Vietnam", "Philippines", "Malaysia", "Pakistan"]
    langs = ["Python", "TypeScript", "Rust", "Go", "JavaScript", "Java", "C++", "Kotlin", "Swift", "Ruby"]

    rows = []
    for i in range(N):
        is_bot = random.random() < 0.10
        if is_bot:
            total_pushes = random.randint(200, 2000)
            unique_msgs = random.randint(1, max(2, int(total_pushes * 0.05)))
            distinct_hours = random.choice([23, 24])
            avg_files = 1.0
            merged_prs = random.randint(0, 5)
        else:
            total_pushes = random.randint(5, 400)
            unique_msgs = random.randint(int(total_pushes * 0.4), total_pushes)
            distinct_hours = random.randint(6, 18)
            avg_files = random.uniform(1.5, 8.0)
            merged_prs = random.randint(0, 40)

        rows.append({
            "username": f"dev_{i}",
            "country": random.choice(countries),
            "primary_language": random.choice(langs),
            "total_pushes": total_pushes,
            "merged_prs": merged_prs,
            "merged_prs_external_weighted": merged_prs * random.uniform(0.8, 2.5),
            "closed_issues": random.randint(0, 20),
            "active_repos_touched": random.randint(1, 15),
            "unique_commit_messages": unique_msgs,
            "distinct_active_hours": distinct_hours,
            "avg_files_changed": avg_files,
            "push_hour_stddev": random.uniform(2, 8) if not is_bot else 0.5,
            "test_file_ratio": random.uniform(0, 0.6) if not is_bot else 0.0,
            "multi_file_commit_ratio": random.uniform(0.2, 0.9) if not is_bot else 0.05,
            "language_diversity": random.randint(1, 5),
            "stars_fraud_filtered": random.randint(0, 500) if not is_bot else random.randint(0, 50),
            "forks_fraud_filtered": random.randint(0, 200) if not is_bot else random.randint(0, 20),
            "dependents": random.randint(0, 50),
            "active_shipping_weeks": random.randint(1, 52) if not is_bot else random.randint(1, 52),
        })

    os.makedirs("data", exist_ok=True)
    import pyarrow as pa, pyarrow.parquet as pq
    table = pa.Table.from_pylist(rows)
    pq.write_table(table, "data/raw.parquet")
    print(f"Mock: {N} users across {len(countries)} countries")

os.makedirs("data", exist_ok=True)
scored = compute_rank_scores("data/raw.parquet")

# Save leaderboard.json
lb = []
for r in scored:
    lb.append({
        "username": r["username"],
        "rank": r["rank"],
        "rank_score": r["rank_score"],
        "country": r["country"],
        "primary_language": r["primary_language"],
        "merged_prs": r["merged_prs"],
        "is_human_verified": r["is_human_verified"],
        "is_bot_classified": r["is_bot_classified"],
        "bot_penalty": r["bot_penalty"],
    })
with open("data/leaderboard.json", "w") as f:
    json.dump(lb, f, indent=2)

# Save per-user profiles
os.makedirs("data/profiles", exist_ok=True)
for r in scored:
    with open(f"data/profiles/{r['username']}.json", "w") as f:
        json.dump(r, f, indent=2)

with open("data/status.json", "w") as f:
    json.dump({"last_run_utc": datetime.now(timezone.utc).isoformat(), "data_age_hours": 0,
               "status": "ok", "users": len(scored), "source": "real" if used_real else "mock"}, f, indent=2)

print(f"Scored {len(scored)} users. Top: {scored[0]['username']} ({scored[0]['rank_score']})")