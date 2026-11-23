"""Split the leaderboard into paginated chunks and sanitize the public data dir.

Everything under ``web/public/data`` is published on GitHub Pages, so internal
scoring signals (per-account automation classification and penalties) must never
reach that layer. The pipeline keeps them in ``data/leaderboard.json``; this
module strips them from the copies the browser can download.
"""
import json
import os

CHUNK_SIZE = 250
PUBLIC_DATA = "web/public/data"

# Never published: these describe a per-account judgement, not a score.
HIDDEN_FIELDS = ("is_bot_classified", "is_human_verified", "bot_penalty", "bot_evidence")


def public_row(row):
    return {k: v for k, v in row.items() if k not in HIDDEN_FIELDS}


def sanitize_profiles(directory):
    """Strip hidden fields from public per-profile JSON copies in place."""
    removed = 0
    if not os.path.isdir(directory):
        return 0
    for name in os.listdir(directory):
        if not name.endswith(".json"):
            continue
        path = os.path.join(directory, name)
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict) or not any(k in data for k in HIDDEN_FIELDS):
            continue
        with open(path, "w", encoding="utf-8") as f:
            json.dump(public_row(data), f, separators=(",", ":"), ensure_ascii=False)
        removed += 1
    return removed


def main():
    os.makedirs(os.path.join(PUBLIC_DATA, "chunks"), exist_ok=True)

    with open("data/leaderboard.json", encoding="utf-8") as f:
        lb = json.load(f)

    # The full leaderboard is not used by the site (chunks are) and is the single
    # largest published file, so it is not part of the public layer at all.
    raw_public = os.path.join(PUBLIC_DATA, "leaderboard.json")
    if os.path.exists(raw_public):
        os.remove(raw_public)

    sanitize_profiles(os.path.join(PUBLIC_DATA, "profiles"))

    # Clean old chunks
    chunks_dir = os.path.join(PUBLIC_DATA, "chunks")
    for old in os.listdir(chunks_dir):
        if old.endswith(".json"):
            os.remove(os.path.join(chunks_dir, old))

    total = len(lb)
    pages = (total + CHUNK_SIZE - 1) // CHUNK_SIZE
    for p in range(pages):
        chunk = [public_row(u) for u in lb[p * CHUNK_SIZE:(p + 1) * CHUNK_SIZE]]
        with open(os.path.join(chunks_dir, f"page_{p + 1}.json"), "w", encoding="utf-8") as f:
            json.dump(chunk, f, separators=(",", ":"), ensure_ascii=False)

    # Index file: metadata
    with open(os.path.join(chunks_dir, "index.json"), "w", encoding="utf-8") as f:
        json.dump({"total": total, "pages": pages, "chunk_size": CHUNK_SIZE}, f)

    # Compact search index: [username, rank, score]. One small request beats
    # fanning out over every chunk now that pages are small.
    with open(os.path.join(PUBLIC_DATA, "search.json"), "w", encoding="utf-8") as f:
        json.dump(
            [[u["username"], u.get("rank"), round(float(u.get("rank_score") or 0), 1)]
             for u in lb if u.get("username")],
            f, separators=(",", ":"), ensure_ascii=False,
        )

    print(f"Chunks: {pages} pages x {CHUNK_SIZE} = {total} users (classification fields stripped)")


if __name__ == "__main__":
    main()
