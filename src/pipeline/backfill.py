"""One-off parallel backfill of daily rolling shards from gharchive.org.

The normal daily run ingests a single day. This fills the 30-day window once,
using a handful of threads because the work is network-bound. Existing shards
and downloaded hourly files are reused, so an interrupted backfill resumes.
"""
import argparse
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ingest_gharchive import ingest_day
from rolling import available_dates


def main(argv=None):
    parser = argparse.ArgumentParser(description="Backfill rolling shards")
    parser.add_argument("--end", default="2026-11-21")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--force", action="store_true",
                        help="Rebuild shards even when the date already exists")
    args = parser.parse_args(argv)

    end = datetime.strptime(args.end, "%Y-%m-%d").date()
    wanted = [end - timedelta(days=offset) for offset in range(args.days - 1, -1, -1)]
    have = set() if args.force else set(available_dates())
    todo = [day for day in wanted if day.isoformat() not in have]
    print(f"Backfill: {len(todo)} of {len(wanted)} days missing, {args.workers} workers")

    failures = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(ingest_day, day, "data/rolling", False): day for day in todo}
        for done, future in enumerate(as_completed(futures), 1):
            day = futures[future]
            try:
                users = future.result()
                print(f"[{done}/{len(todo)}] {day}: {users} users")
            except Exception as exc:  # noqa: BLE001 - report and keep going
                failures.append((day, str(exc)))
                print(f"[{done}/{len(todo)}] {day}: FAILED {exc}")

    print(f"Backfill done: {len(available_dates())} shards, {len(failures)} failures")
    for day, error in failures:
        print(f"  {day}: {error}")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
