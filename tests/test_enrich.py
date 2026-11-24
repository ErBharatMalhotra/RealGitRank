"""Enrichment parsing: lifetime portfolio context comes from GitHub GraphQL."""
import json

import enrich


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def fake_graphql(monkeypatch, payload):
    monkeypatch.setattr(enrich, "TOKEN", "test-token")
    monkeypatch.setattr(enrich.urllib.request, "urlopen", lambda req, timeout=30: _Resp(payload))
    return payload


def test_portfolio_fields_are_parsed(monkeypatch):
    fake_graphql(monkeypatch, {"data": {"u0": {
        "login": "dev",
        "location": "Mumbai, India",
        "avatarUrl": "https://example/avatar.png",
        "bio": "",
        "company": "@acme",
        "followers": {"totalCount": 588},
        "contributionsCollection": {
            "totalCommitContributions": 1973,
            "restrictedContributionsCount": 2385,
        },
        "repositories": {
            "totalCount": 49,
            "nodes": [{"stargazerCount": 20}, {"stargazerCount": 13}, {"stargazerCount": 0}],
        },
    }}})

    info = enrich.gql_query(["dev"])["dev"]
    assert info["followers"] == 588
    assert info["commits_1y"] == 4358  # public + restricted contributions
    assert info["stars_total"] == 33
    assert info["repos_total"] == 49
    assert info["location"] == "Mumbai, India"


def test_missing_users_are_skipped_and_requests_name_the_portfolio_fields(monkeypatch):
    calls = []

    def fake_urlopen(req, timeout=30):
        calls.append(json.loads(req.data.decode())["query"])
        return _Resp({"data": {"u0": None}})

    monkeypatch.setattr(enrich, "TOKEN", "test-token")
    monkeypatch.setattr(enrich.urllib.request, "urlopen", fake_urlopen)

    assert enrich.gql_query(["ghost"]) == {}
    assert "contributionsCollection" in calls[0]
    assert "stargazerCount" in calls[0]
