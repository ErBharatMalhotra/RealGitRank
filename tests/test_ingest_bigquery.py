"""BigQuery attribution: bot-pushed commits must reach their human authors."""
from datetime import date

import pytest

import ingest_bigquery as bq
from ingest_gharchive import aggregate_events, build_rows
from rolling import build_window_rows, window_source_stats, write_day_shard


class _Job:
    def __init__(self, rows):
        self._rows = rows

    def result(self):
        return list(self._rows)


class _Client:
    def __init__(self, rows=None, error=None):
        self.rows = rows or []
        self.error = error
        self.sql = ""

    def query(self, sql, job_config=None):
        self.sql = sql
        if self.error:
            raise self.error
        return _Job(self.rows)


def author_row(login, bot_pushes=0, messages=0, commits=0, test_hits=0, hours=(), repos=()):
    return {
        "author_login": login,
        "pushes": bot_pushes + 1,
        "bot_pushes": bot_pushes,
        "messages": messages,
        "commits": commits,
        "test_hits": test_hits,
        "hours": list(hours),
        "repos": list(repos),
    }


def test_sql_is_partition_pruned_and_payload_flexible():
    sql = bq.build_sql()
    assert "_TABLE_SUFFIX = @suffix" in sql
    assert "UNNEST(JSON_EXTRACT_ARRAY(" in sql
    assert "{payload}" not in sql
    assert "TO_JSON_STRING(payload)" in sql


def test_missing_credentials_is_a_no_op(monkeypatch):
    monkeypatch.delenv("GCP_PROJECT_ID", raising=False)
    monkeypatch.delenv("GCP_SA_KEY", raising=False)
    agg = aggregate_events([])
    assert bq.has_credentials() is False
    assert bq.augment_agg(date(2026, 11, 20), agg) is None
    assert agg == {}


def test_bot_pushed_commits_credit_the_author():
    agg = aggregate_events([])
    summary = bq.merge_author_stats(
        [author_row("bharat", bot_pushes=4, messages=12, commits=20, test_hits=3,
                    hours=(9, 10), repos=("bharat/app",))],
        agg,
        date(2026, 11, 20),
    )
    assert summary == {"authors": 1, "pushes": 4, "messages": 12, "test_hits": 3}

    row = build_rows(agg)[0]
    assert row["username"] == "bharat"
    assert row["total_pushes"] == 4
    assert row["unique_commit_messages"] == 12
    assert row["test_file_ratio"] == pytest.approx(3 / 20)
    assert row["active_repos_touched"] == 1
    assert row["distinct_active_hours"] == 2


def test_human_pushes_are_not_counted_twice():
    push = {"actor": {"login": "alice"}, "type": "PushEvent", "repo": {"name": "a/b"},
            "org": {}, "created_at": "2026-11-20 09:00:00", "payload": {}}
    agg = aggregate_events([push])
    bq.merge_author_stats(
        [author_row("alice", messages=3, commits=5, hours=(9,), repos=("a/b",))],
        agg,
        date(2026, 11, 20),
    )
    row = build_rows(agg)[0]
    assert row["total_pushes"] == 1
    assert row["unique_commit_messages"] == 3


def test_bot_authors_are_skipped():
    agg = aggregate_events([])
    summary = bq.merge_author_stats(
        [author_row("dependabot[bot]", bot_pushes=9, messages=9, commits=9),
         author_row("github-actions", bot_pushes=9, messages=9, commits=9),
         author_row("", bot_pushes=9, messages=9, commits=9)],
        agg,
        date(2026, 11, 20),
    )
    assert summary["authors"] == 0
    assert agg == {}


def test_augment_uses_the_client_and_never_raises():
    client = _Client(rows=[author_row("carol", bot_pushes=2, messages=4, commits=6)])
    agg = aggregate_events([])
    summary = bq.augment_agg(date(2026, 11, 20), agg, client=client)
    assert summary["pushes"] == 2
    assert "UNNEST" in client.sql

    broken = bq.augment_agg(date(2026, 11, 20), aggregate_events([]),
                            client=_Client(error=RuntimeError("quota exceeded")))
    assert broken is None


def test_shard_publishes_attribution_coverage(tmp_path):
    import gzip
    import json

    agg = aggregate_events([])
    bq.merge_author_stats(
        [author_row("dave", bot_pushes=1, messages=2, commits=2,
                    hours=(11,), repos=("dave/app",))],
        agg, date(2026, 11, 20),
    )
    stats = {"events": 10, "bot_events": 2, "hours": 24,
             "attributed_pushes": 1, "attributed_authors": 1}
    path, _ = write_day_shard("2026-11-20", agg, str(tmp_path), stats=stats)

    with gzip.open(path, "rt", encoding="utf-8") as f:
        payload = json.load(f)
    assert payload["stats"]["attributed_pushes"] == 1
    assert payload["users"]["dave"]["u"] == 2

    source = window_source_stats("2026-11-20", rolling_dir=str(tmp_path))
    assert source["attributed_pushes"] == 1


def test_author_activity_reaches_the_window(tmp_path):
    agg = aggregate_events([])
    bq.merge_author_stats(
        [author_row("erin", bot_pushes=3, messages=6, commits=8, test_hits=2,
                    hours=(8, 16), repos=("erin/lib",))],
        agg, date(2026, 11, 20),
    )
    write_day_shard("2026-11-20", agg, str(tmp_path))
    rows, _ = build_window_rows("2026-11-20", days=30, rolling_dir=str(tmp_path))
    erin = next(r for r in rows if r["username"] == "erin")
    assert erin["total_pushes"] == 3
    assert erin["entropy_known"] is True
    assert erin["test_file_ratio"] == pytest.approx(2 / 8)
    assert erin["active_repos_touched"] == 1
