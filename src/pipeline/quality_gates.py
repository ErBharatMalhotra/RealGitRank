"""Data quality gates — fail build if data is bad."""
import json, sys, statistics

with open("data/leaderboard.json") as f:
    lb = json.load(f)
with open("data/status.json") as f:
    st = json.load(f)

errors = []
if len(lb) < 10:
    errors.append(f"row count too low: {len(lb)}")
nulls = [u for u in lb if not u.get("username")]
if nulls:
    errors.append(f"null usernames: {len(nulls)}")
scores = [u["rank_score"] for u in lb]
if scores:
    if max(scores) > 1000 or min(scores) < 0:
        errors.append("score out of range")
    if statistics.pstdev(scores) == 0:
        errors.append("zero variance in scores")

if errors:
    for e in errors:
        print("GATE FAIL:", e)
    sys.exit(1)
print("Quality gates: PASS", len(lb), "users")