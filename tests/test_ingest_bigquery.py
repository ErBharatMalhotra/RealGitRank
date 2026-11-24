"""BigQuery CI attribution: owner credit must stay out of the scoring inputs."""
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
        self.calls = getattr(self, "calls", 0) + 1
        if self.error:
            raise self.error
        return _Job(self.rows)


def ci_row(owner, ci_pushes=1, repos=1):
    return {"owner": owner, "ci_pushes": ci_pushes, "repos": repos, "repo_names": []}


def test_sql_targets_one_day_table():
    sql = bq.build_sql(date(2026, 11, 23))
    # A wildcard (day.*) fails on datasets that contain views (day.yesterday).
    assert "githubarchive.day.20261123" in sql
    assert "{table}" not in sql
    assert "actor.login IN ('github-actions[bot]', 'github-actions')" in sql
    assert "org IS NULL" in sql


def test_missing_credentials_is_a_no_op(monkeypatch):
    monkeypatch.delenv("GCP_PROJECT_ID", raising=False)
    monkeypatch.delenv("GCP_SA_KEY", raising=False)
    agg = aggregate_events([])
    assert bq.has_credentials() is False
    assert bq.attribute_ci_pushes(date(2026, 11, 20), agg) is None
    assert agg == {}


def test_ci_pushes_credit_the_owner_but_not_the_scoring_inputs():
    agg = aggregate_events([])
    summary = bq.merge_ci_stats([ci_row("bharat", ci_pushes=7, repos=2)], agg, date(2026, 11, 20))
    assert summary == {"owners": 1, "pushes": 7, "repos": 2}

    user = agg["bharat"]
    assert user["ci_pushes"] == 7
    # Never folded into pushes/hours/repos: those drive entropy and automation.
    assert user["total_pushes"] == 0
    assert user["hours"] == []
    assert user["repos"] == set()

    row = build_rows(agg)[0]
    assert row["username"] == "bharat"
    assert row["total_pushes"] == 0
    assert row["ci_pushes"] == 7
    assert row["unique_commit_messages"] != row["unique_commit_messages"]  # NaN


def test_ci_pushes_do_not_lower_entropy_for_a_real_pusher():
    push = {"actor": {"login": "alice"}, "type": "PushEvent", "repo": {"name": "a/b"},
            "org": {}, "created_at": "2026-11-20 09:00:00",
            "payload": {"commits": [{"message": "one"}, {"message": "two"}]}}
    agg = aggregate_events([push])
    bq.merge_ci_stats([ci_row("alice", ci_pushes=40)], agg, date(2026, 11, 20))

    row = build_rows(agg)[0]
    assert row["total_pushes"] == 1  # entropy denominator unchanged
    assert row["unique_commit_messages"] == 2
    assert row["ci_pushes"] == 40


def test_bot_owners_are_skipped():
    agg = aggregate_events([])
    summary = bq.merge_ci_stats(
        [ci_row("dependabot[bot]", ci_pushes=5), ci_row("github-actions", ci_pushes=5), ci_row("")],
        agg, date(2026, 11, 20),
    )
    assert summary["owners"] == 0
    assert agg == {}


def test_attribute_uses_the_client_and_never_raises():
    client = _Client(rows=[ci_row("carol", ci_pushes=3)])
    agg = aggregate_events([])
    summary = bq.attribute_ci_pushes(date(2026, 11, 20), agg, client=client)
    assert summary["pushes"] == 3
    assert "github-actions[bot]" in client.sql

    broken = bq.attribute_ci_pushes(date(2026, 11, 20), aggregate_events([]),
                                    client=_Client(error=RuntimeError("quota exceeded")))
    assert broken is None


def test_backfill_adds_ci_to_old_shards_exactly_once(tmp_path):
    import gzip
    import json

    write_day_shard("2026-11-20", aggregate_events([]), str(tmp_path))  # pre-BigQuery shard
    client = _Client(rows=[ci_row("frank", ci_pushes=3)])

    done = bq.backfill_window(rolling_dir=str(tmp_path), client=client)
    assert done == [("2026-11-20", 1, 3)]
    with gzip.open(tmp_path / "2026-11-20.json.gz", "rt", encoding="utf-8") as f:
        payload = json.load(f)
    assert payload["users"]["frank"]["cp"] == 3
    assert payload["stats"]["ci_pushes"] == 3

    rows, _ = build_window_rows("2026-11-20", days=30, rolling_dir=str(tmp_path))
    assert next(r for r in rows if r["username"] == "frank")["ci_pushes"] == 3

    # Idempotent: a shard that already carries ci_pushes is never queried again.
    assert bq.backfill_window(rolling_dir=str(tmp_path), client=client) == []
    assert client.calls == 1


def test_shard_and_window_carry_ci_pushes(tmp_path):
    import gzip
    import json

    agg = aggregate_events([])
    bq.merge_ci_stats([ci_row("dave", ci_pushes=4)], agg, date(2026, 11, 20))
    stats = {"events": 10, "bot_events": 2, "hours": 24, "ci_pushes": 4, "ci_owners": 1}
    path, _ = write_day_shard("2026-11-20", agg, str(tmp_path), stats=stats)

    with gzip.open(path, "rt", encoding="utf-8") as f:
        payload = json.load(f)
    assert payload["stats"]["ci_pushes"] == 4
    assert payload["users"]["dave"]["cp"] == 4
    assert payload["users"]["dave"]["p"] == 0

    source = window_source_stats("2026-11-20", rolling_dir=str(tmp_path))
    assert source["ci_pushes"] == 4

    rows, _ = build_window_rows("2026-11-20", days=30, rolling_dir=str(tmp_path))
    dave = next(r for r in rows if r["username"] == "dave")
    assert dave["ci_pushes"] == 4
    assert dave["total_pushes"] == 0
    assert dave["active_shipping_weeks"] == 1
