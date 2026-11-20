"""Split leaderboard into paginated chunks for fast browser loading."""
import json, os

CHUNK_SIZE = 1000
os.makedirs("web/public/data/chunks", exist_ok=True)

with open("data/leaderboard.json") as f:
    lb = json.load(f)

# Clean old chunks
for old in os.listdir("web/public/data/chunks"):
    if old.endswith(".json"):
        os.remove(os.path.join("web/public/data/chunks", old))

total = len(lb)
pages = (total + CHUNK_SIZE - 1) // CHUNK_SIZE
for p in range(pages):
    chunk = lb[p * CHUNK_SIZE:(p + 1) * CHUNK_SIZE]
    with open(f"web/public/data/chunks/page_{p + 1}.json", "w") as f:
        json.dump(chunk, f)

# Index file: metadata
with open("web/public/data/chunks/index.json", "w") as f:
    json.dump({"total": total, "pages": pages, "chunk_size": CHUNK_SIZE}, f)

print(f"Chunks: {pages} pages x {CHUNK_SIZE} = {total} users")