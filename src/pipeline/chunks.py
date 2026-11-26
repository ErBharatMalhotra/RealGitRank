"""Split the ranked list into paginated chunks and sanitize the public data dir.

Everything under ``web/public/data`` is published on GitHub Pages, so internal
scoring signals (per-account automation classification and penalties) must never
reach that layer. The pipeline keeps them in ``data/leaderboard.json``; this
module strips them from the copies the browser can download.

Two public layers are written here:

* ``chunks/page_<n>.json`` — every user in the snapshot (detailed rows for the
  top of the board, compact rows for the long tail), so nobody is missing from
  the site.
* ``search/<bucket>.json`` — a prefix-sharded ``[username, rank, score, state,
  page]`` index. A single index would be tens of megabytes, so the browser
  fetches only the bucket it needs while the user types. ``state`` is always
  ``ranked`` under score v3: an account with no public activity in the 12-month
  window is not published at all, so no row has to claim a rank it does not
  have. The field stays in the schema so older clients keep working.
* ``search/names.json`` — ``[username, rank]`` for the detailed board so a
  substring search ("bharat" finding "ErBharatMalhotra") can reach every user on
  it, not just the rows the table has already loaded.
"""
import json
import os
import re

CHUNK_SIZE = 250
PUBLIC_DATA = "web/public/data"
LONG_TAIL_PATH = "data/long_tail.json"
SEARCH_SCHEMA = 3

# Never published: these describe a per-account judgement, not a score.
HIDDEN_FIELDS = ("is_bot_classified", "is_human_verified", "bot_penalty", "bot_evidence")

SEARCH_BUCKETS = "abcdefghijklmnopqrstuvwxyz0123456789"


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


def search_bucket(username):
    """First lowercase alphanumeric character, else ``other``."""
    key = (username or "")[:1].lower()
    return key if key in SEARCH_BUCKETS else "other"


def dedupe_rows(rows):
    """One row per GitHub login, keeping the best (lowest) rank.

    ``data/long_tail.json`` survives across runs and can hold a user who has
    since moved into the detailed slice, which used to produce two ranks for the
    same account. Ranks are still the pipeline's positional value here; only the
    public search layer hides the rank of a listed account.
    """
    best = {}
    for row in rows:
        name = str(row.get("username") or "")
        if not name:
            continue
        key = name.lower()
        rank = row.get("rank")
        current = best.get(key)
        if current is None:
            best[key] = row
            continue
        current_rank = current.get("rank")
        if isinstance(rank, int) and (not isinstance(current_rank, int) or rank < current_rank):
            best[key] = row
    return sorted(best.values(), key=lambda u: u.get("rank") or 0)


def search_row(entry, page):
    """Public search record. ``score`` is the absolute 12-month activity count."""
    return [
        entry["username"],
        entry.get("rank"),
        round(float(entry.get("rank_score") or 0), 1),
        "ranked",
        page,
    ]


def load_rows():
    """Detailed leaderboard rows plus the compact long tail, in rank order.

    Returns ``(rows, detailed_count)`` so callers can bound the substring index.
    """
    with open("data/leaderboard.json", encoding="utf-8") as f:
        detailed = json.load(f)
    tail = []
    try:
        with open(LONG_TAIL_PATH, encoding="utf-8") as f:
            tail = json.load(f)
    except (OSError, ValueError):
        tail = []
    rows = [public_row(u) for u in detailed if u.get("username")]
    detailed_names = {u["username"].lower() for u in rows}
    rows = dedupe_rows(rows)
    # A stale tail can still name a user who is now in the detailed slice. Prefer
    # the detailed row (it has country, language and profile data) over the copy
    # left behind by an older run.
    seen = {u["username"].lower() for u in rows}
    for u in tail:
        if not u.get("username"):
            continue
        key = u["username"].lower()
        if key in seen:
            continue
        seen.add(key)
        rows.append(public_row(u))
    rows.sort(key=lambda u: u.get("rank") or 0)
    detailed_count = sum(1 for u in rows if u["username"].lower() in detailed_names)
    return rows, detailed_count


def main():
    os.makedirs(os.path.join(PUBLIC_DATA, "chunks"), exist_ok=True)

    rows, detailed_count = load_rows()

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

    total = len(rows)
    pages = (total + CHUNK_SIZE - 1) // CHUNK_SIZE
    for p in range(pages):
        chunk = rows[p * CHUNK_SIZE:(p + 1) * CHUNK_SIZE]
        with open(os.path.join(chunks_dir, f"page_{p + 1}.json"), "w", encoding="utf-8") as f:
            json.dump(chunk, f, separators=(",", ":"), ensure_ascii=False)

    # Index file: metadata
    with open(os.path.join(chunks_dir, "index.json"), "w", encoding="utf-8") as f:
        json.dump({"total": total, "pages": pages, "chunk_size": CHUNK_SIZE}, f)

    # Prefix-sharded search index: [username, rank, score, state, page].
    search_dir = os.path.join(PUBLIC_DATA, "search")
    os.makedirs(search_dir, exist_ok=True)
    for old in os.listdir(search_dir):
        if old.endswith(".json"):
            os.remove(os.path.join(search_dir, old))
    buckets = {}
    for index, u in enumerate(rows):
        page = index // CHUNK_SIZE + 1
        buckets.setdefault(search_bucket(u["username"]), []).append(search_row(u, page))
    for key, bucket_rows in buckets.items():
        with open(os.path.join(search_dir, f"{key}.json"), "w", encoding="utf-8") as f:
            json.dump(bucket_rows, f, separators=(",", ":"), ensure_ascii=False)

    # Substring search: one rank-ordered name list for the detailed board. The
    # prefix buckets cannot answer "contains", and the full board would be tens of
    # megabytes, so this stays a few hundred kilobytes.
    with open(os.path.join(search_dir, "names.json"), "w", encoding="utf-8") as f:
        json.dump([[u["username"], u.get("rank")] for u in rows[:detailed_count]],
                  f, separators=(",", ":"), ensure_ascii=False)

    # Keep a small manifest so clients can discover the buckets (and the counts)
    # without guessing.
    with open(os.path.join(search_dir, "index.json"), "w", encoding="utf-8") as f:
        json.dump({"total": total, "ranked": total, "listed": 0,
                   "detailed": detailed_count, "schema": SEARCH_SCHEMA,
                   "buckets": sorted(buckets), "names": "names.json"}, f)

    stale_search = os.path.join(PUBLIC_DATA, "search.json")
    if os.path.exists(stale_search):
        os.remove(stale_search)

    print(
        f"Chunks: {pages} pages x {CHUNK_SIZE} = {total} ranked users, "
        f"search buckets: {len(buckets)}, substring names: {detailed_count} "
        "(classification fields stripped)"
    )


if __name__ == "__main__":
    main()
