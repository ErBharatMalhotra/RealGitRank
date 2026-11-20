"""Tests for gharchive aggregation: real quality metrics instead of hardcoded constants."""
from datetime import datetime

import pytest

from ingest_gharchive import aggregate_events, build_rows, has_test_signal, is_bot_login


def event(login, etype, repo="owner/repo", when=None, payload=None):
    return {
        "actor": {"login": login},
        "type": etype,
        "repo": {"name": repo},
        "org": {},
        "created_at": when or datetime(2026, 11, 20, 10, 0, 0),
        "payload": payload or {},
    }


def push(login, messages, when=None, repo="owner/repo"):
    return event(
        login,
        "PushEvent",
        repo=repo,
        when=when,
        payload={"commits": [{"message": m} for m in messages]},
    )


def merged_pr(login, changed_files=None, title="change", repo="owner/repo", when=None, action="closed"):
    pr = {"merged": True, "title": title, "body": ""}
    if changed_files is not None:
        pr["changed_files"] = changed_files
    return event(login, "PullRequestEvent", repo=repo, when=when,
                 payload={"action": action, "pull_request": pr})


def gharchive_merged_pr(login, repo="owner/repo", when=None):
    """Shape produced by the live feed: action="merged", trimmed pull_request object."""
    return event(
        login, "PullRequestEvent", repo=repo, when=when,
        payload={"action": "merged", "number": 7, "pull_request": {
            "url": f"https://api.github.com/repos/{repo}/pulls/7", "id": 1, "number": 7,
            "head": {"ref": "feature"}, "base": {"ref": "main"}}},
    )


def rows(events):
    return build_rows(aggregate_events(events))


def by_name(rows_out, name):
    return next(r for r in rows_out if r["username"] == name)


def test_helpers():
    assert is_bot_login("dependabot[bot]")
    assert is_bot_login("github-actions")
    assert not is_bot_login("octocat")
    assert has_test_signal("add unit tests")
    assert has_test_signal("pytest.ini cleanup")
    assert not has_test_signal("update readme")


def test_push_metrics_are_derived_not_constant():
    events = [
        push("alice", ["fix bug", "add tests"], when=datetime(2026, 11, 20, 9)),
        push("alice", ["refactor parser"], when=datetime(2026, 11, 20, 15)),
    ]
    r = by_name(rows(events), "alice")

    assert r["total_pushes"] == 2
    assert r["test_file_ratio"] == pytest.approx(1 / 3)
    assert r["push_hour_stddev"] == pytest.approx(3.0)
    assert r["distinct_active_hours"] == 2
    assert r["avg_files_changed"] != 2.0  # no invented default anymore
    assert r["avg_files_changed"] != r["avg_files_changed"]  # NaN: unknown


def test_merged_pr_file_counts_are_real():
    events = [
        merged_pr("bob", changed_files=7, title="add tests for parser", repo="someone/else"),
        merged_pr("bob", changed_files=1, title="typo fix", repo="bob/own"),
    ]
    r = by_name(rows(events), "bob")

    assert r["avg_files_changed"] == pytest.approx(4.0)
    assert r["multi_file_commit_ratio"] == pytest.approx(0.5)
    assert r["test_file_ratio"] == pytest.approx(0.5)
    # external merge (1.5) + self merge (1.0)
    assert r["merged_prs_external_weighted"] == pytest.approx(2.5)


def test_merged_action_is_counted_without_merged_flag():
    """The live feed uses action="merged" and carries no merged/changed_files fields."""
    out = rows([
        gharchive_merged_pr("frank", repo="someone/else"),
        gharchive_merged_pr("frank", repo="frank/own"),
    ])
    r = by_name(out, "frank")
    assert r["merged_prs"] == 2
    assert r["merged_prs_external_weighted"] == pytest.approx(2.5)
    assert r["avg_files_changed"] != r["avg_files_changed"]  # unknown, not invented


def test_closed_without_merge_is_ignored():
    closed_unmerged = event("gina", "PullRequestEvent",
                            payload={"action": "closed", "pull_request": {"merged": False}})
    assert rows([closed_unmerged]) == []


def test_entropy_is_unknown_without_commit_messages():
    """Empty PushEvent payloads must not be read as perfectly unique messages."""
    r = by_name(rows([event("hugo", "PushEvent")]), "hugo")
    assert r["total_pushes"] == 1
    assert r["unique_commit_messages"] != r["unique_commit_messages"]  # NaN
    assert r["test_file_ratio"] != r["test_file_ratio"]  # NaN


def test_pr_without_file_counts_stays_unknown():
    r = by_name(rows([merged_pr("carol")]), "carol")
    assert r["avg_files_changed"] != r["avg_files_changed"]  # NaN
    assert r["multi_file_commit_ratio"] != r["multi_file_commit_ratio"]  # NaN


def test_shipping_weeks_use_iso_weeks():
    same_week = [
        push("dave", ["a"], when=datetime(2026, 11, 16, 10)),  # Monday, ISO week 47
        push("dave", ["b"], when=datetime(2026, 11, 19, 10)),  # Thursday, ISO week 47
    ]
    assert by_name(rows(same_week), "dave")["active_shipping_weeks"] == 1

    two_weeks = [
        push("erin", ["a"], when=datetime(2026, 11, 20, 10)),  # Friday, ISO week 47
        push("erin", ["b"], when=datetime(2026, 11, 23, 10)),  # Monday, ISO week 48
    ]
    assert by_name(rows(two_weeks), "erin")["active_shipping_weeks"] == 2


def test_stars_credit_the_repo_owner():
    events = [
        push("owner", ["ship"], repo="owner/project"),
        event("fan", "WatchEvent", repo="owner/project"),
    ]
    out = rows(events)
    assert by_name(out, "owner")["stars_fraud_filtered"] == 1
    assert all(r["username"] != "fan" for r in out)


def test_bot_accounts_are_skipped():
    out = rows([
        push("dependabot[bot]", ["bump"]),
        push("github-actions", ["ci"]),
    ])
    assert out == []


def test_users_without_shipping_activity_are_filtered():
    out = rows([event("talker", "IssueCommentEvent")])
    assert out == []


def test_gharchive_file_round_trip(tmp_path):
    """read_events must keep payload keys that schema inference used to drop."""
    import gzip
    import json

    from ingest_gharchive import read_events

    events = [
        {
            "id": "1", "type": "PushEvent", "actor": {"login": "zoe"},
            "repo": {"name": "zoe/app"}, "org": {}, "created_at": "2026-11-20 10:00:00",
            "payload": {"commits": [{"message": "add tests"}]},
        },
        {
            "id": "2", "type": "PullRequestEvent", "actor": {"login": "zoe"},
            "repo": {"name": "upstream/lib"}, "org": {}, "created_at": "2026-11-20 11:00:00",
            "payload": {"action": "closed", "pull_request": {
                "merged": True, "title": "fix parser", "body": "", "changed_files": 3}},
        },
    ]
    payload = "\n".join(json.dumps(e) for e in events)
    plain = tmp_path / "2026-11-20-10.json"
    plain.write_text(payload, encoding="utf-8")
    packed = tmp_path / "2026-11-20-11.json.gz"
    with gzip.open(packed, "wt", encoding="utf-8") as f:
        f.write(payload)

    found = list(read_events(str(plain)))
    assert len(found) == 2
    assert found[1]["payload"]["pull_request"]["merged"] is True
    assert len(list(read_events(str(packed)))) == 2

    zoe = by_name(build_rows(aggregate_events(found)), "zoe")
    assert zoe["merged_prs"] == 1
    assert zoe["avg_files_changed"] == pytest.approx(3.0)
    assert zoe["test_file_ratio"] == pytest.approx(0.5)
    assert zoe["merged_prs_external_weighted"] == pytest.approx(1.5)
