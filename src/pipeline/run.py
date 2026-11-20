"""RealGitRank pipeline runner: real BigQuery ingest if credentials exist, else mock demo data."""
import json, os, random, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from datetime import datetime, timezone
import duckdb
from scoring_engine import compute_rank_scores

# Try real data from gharchive.org (zero GCP, zero auth, free forever) FIRST
used_real = False
try:
    from ingest_gharchive import ingest as gh_ingest
    used_real = gh_ingest()
except Exception as e:
    print(f"gharchive ingest error: {e}")

# If gharchive failed, try BigQuery (if credentials exist)
if not used_real:
    try:
        from ingest_bigquery import ingest as bq_ingest
        used_real = bq_ingest()
    except Exception as e:
        print(f"BigQuery module error: {e}")

# If no real data, generate mock (for local dev / demo)
if not used_real or not os.path.exists("data/raw.parquet"):
    print("Generating mock data for worldwide demo (1000 per country)...")
    random.seed(42)
    countries = ["Afghanistan","Albania","Algeria","Andorra","Angola","Antigua and Barbuda","Argentina","Armenia","Australia","Austria",
    "Azerbaijan","Bahamas","Bahrain","Bangladesh","Barbados","Belarus","Belgium","Belize","Benin","Bhutan",
    "Bolivia","Bosnia and Herzegovina","Botswana","Brazil","Brunei","Bulgaria","Burkina Faso","Burundi","Cabo Verde","Cambodia",
    "Cameroon","Canada","Central African Republic","Chad","Chile","China","Colombia","Comoros","Congo (Brazzaville)","Congo (Kinshasa)",
    "Costa Rica","Croatia","Cuba","Cyprus","Czechia","Denmark","Djibouti","Dominica","Dominican Republic","Ecuador",
    "Egypt","El Salvador","Equatorial Guinea","Eritrea","Estonia","Eswatini","Ethiopia","Fiji","Finland","France",
    "Gabon","Gambia","Georgia","Germany","Ghana","Greece","Grenada","Guatemala","Guinea","Guinea-Bissau",
    "Guyana","Haiti","Honduras","Hungary","Iceland","India","Indonesia","Iran","Iraq","Ireland",
    "Israel","Italy","Jamaica","Japan","Jordan","Kazakhstan","Kenya","Kiribati","Korea (North)","Korea (South)",
    "Kosovo","Kuwait","Kyrgyzstan","Laos","Latvia","Lebanon","Lesotho","Liberia","Libya","Liechtenstein",
    "Lithuania","Luxembourg","Madagascar","Malawi","Malaysia","Maldives","Mali","Malta","Marshall Islands","Mauritania",
    "Mauritius","Mexico","Micronesia","Moldova","Monaco","Mongolia","Montenegro","Morocco","Mozambique","Myanmar",
    "Namibia","Nauru","Nepal","Netherlands","New Zealand","Nicaragua","Niger","Nigeria","North Macedonia","Norway",
    "Oman","Pakistan","Palau","Palestine","Panama","Papua New Guinea","Paraguay","Peru","Philippines","Poland",
    "Portugal","Qatar","Romania","Russia","Rwanda","Saint Kitts and Nevis","Saint Lucia","Saint Vincent and the Grenadines","Samoa","San Marino",
    "Sao Tome and Principe","Saudi Arabia","Senegal","Serbia","Seychelles","Sierra Leone","Singapore","Slovakia","Slovenia","Solomon Islands",
    "Somalia","South Africa","South Sudan","Spain","Sri Lanka","Sudan","Suriname","Sweden","Switzerland","Syria",
    "Tajikistan","Tanzania","Thailand","Timor-Leste","Togo","Tonga","Trinidad and Tobago","Tunisia","Turkey","Turkmenistan",
    "Tuvalu","Uganda","Ukraine","United Arab Emirates","United Kingdom","United States","Uruguay","Uzbekistan","Vanuatu","Vatican City",
    "Venezuela","Vietnam","Yemen","Zambia","Zimbabwe"]
    langs = ["Python", "TypeScript", "Rust", "Go", "JavaScript", "Java", "C++", "Kotlin", "Swift", "Ruby"]

    PER_COUNTRY = 1000
    rows = []
    idx = 0
    for country in countries:
        for _ in range(PER_COUNTRY):
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
                "username": f"dev_{idx}",
                "country": country,
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
            idx += 1

    os.makedirs("data", exist_ok=True)
    import pyarrow as pa, pyarrow.parquet as pq
    table = pa.Table.from_pylist(rows)
    pq.write_table(table, "data/raw.parquet")
    print(f"Mock: {len(rows)} users across {len(countries)} countries ({PER_COUNTRY} each)")

os.makedirs("data", exist_ok=True)
scored = compute_rank_scores("data/raw.parquet")

# ACCUMULATE: merge with existing leaderboard (don't replace)
# Over time, this grows to cover the active developer population worldwide
existing = {}
if os.path.exists("data/leaderboard.json"):
    try:
        old = json.load(open("data/leaderboard.json"))
        for u in old:
            existing[u["username"]] = u
    except Exception:
        pass

merged = {}
for r in scored:
    u = r["username"]
    new_entry = {
        "username": u,
        "rank_score": r["rank_score"],
        "country": r["country"] or existing.get(u, {}).get("country", ""),
        "primary_language": r["primary_language"] if r["primary_language"] != "Unknown" else existing.get(u, {}).get("primary_language", "Unknown"),
        "merged_prs": r["merged_prs"],
        "is_human_verified": r["is_human_verified"],
        "is_bot_classified": r["is_bot_classified"],
        "bot_penalty": r["bot_penalty"],
        "avatar_url": existing.get(u, {}).get("avatar_url", ""),
        "bio": existing.get(u, {}).get("bio", ""),
    }
    # Blend scores: weighted average (70% new day, 30% historical)
    if u in existing:
        old_score = existing[u].get("rank_score", 0)
        new_entry["rank_score"] = round(new_entry["rank_score"] * 0.7 + old_score * 0.3, 1)
    merged[u] = new_entry

# Add existing users not in today's data (keep their historical score, decay slightly)
for u, e in existing.items():
    if u not in merged:
        e["rank_score"] = round(e.get("rank_score", 0) * 0.95, 1)  # 5% decay per inactive day
        merged[u] = e

# Sort globally
lb = sorted(merged.values(), key=lambda x: x["rank_score"], reverse=True)

# Per-country minimum: ensure at least 100 devs per country
included_usernames = set(u["username"] for u in lb[:200000])
for country in set(u["country"] or "Unknown" for u in lb):
    country_users = [u for u in lb if (u["country"] or "Unknown") == country]
    added = 0
    for u in country_users:
        if added >= 100:
            break
        if u["username"] not in included_usernames:
            lb.append(u)
            included_usernames.add(u["username"])
            added += 1

# Re-sort and cap
lb.sort(key=lambda x: x["rank_score"], reverse=True)
lb = lb[:220000]
for i, u in enumerate(lb, 1):
    u["rank"] = i

print(f"Total: {len(lb)} users (top 200k + per-country minimums)")

with open("data/leaderboard.json", "w") as f:
    json.dump(lb, f, indent=2)

# Save per-user profiles ONLY for top 1000 globally (hybrid scalability)
os.makedirs("data/profiles", exist_ok=True)
for r in scored[:1000]:
    # Merge with existing profile if present
    prof_path = f"data/profiles/{r['username']}.json"
    if os.path.exists(prof_path):
        try:
            old_prof = json.load(open(prof_path))
            r["avatar_url"] = old_prof.get("avatar_url", r.get("avatar_url", ""))
            r["bio"] = old_prof.get("bio", r.get("bio", ""))
            r["company"] = old_prof.get("company", r.get("company", ""))
            r["country"] = old_prof.get("country", r.get("country", ""))
        except Exception:
            pass
    with open(prof_path, "w") as f:
        json.dump(r, f, indent=2)

with open("data/status.json", "w") as f:
    json.dump({"last_run_utc": datetime.now(timezone.utc).isoformat(), "data_age_hours": 0,
               "status": "ok", "users": len(lb), "new_today": len(scored),
               "source": "real" if not used_real else "real"}, f, indent=2)

print(f"Accumulated: {len(lb)} total users ({len(scored)} new/updated today). Top: {lb[0]['username']} ({lb[0]['rank_score']})")

with open("data/status.json", "w") as f:
    json.dump({"last_run_utc": datetime.now(timezone.utc).isoformat(), "data_age_hours": 0,
               "status": "ok", "users": len(scored), "source": "real" if used_real else "mock"}, f, indent=2)

print(f"Scored {len(scored)} users. Top: {scored[0]['username']} ({scored[0]['rank_score']})")
print(f"Profile JSONs: {min(1000, len(scored))} (top 1000 only)")