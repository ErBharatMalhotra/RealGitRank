import json

from static_api import (
    SITE,
    build_leaderboard,
    build_user,
    compact_user,
    generate,
    llms_text,
)


def entry(rank=1, **extra):
    row = {
        "username": f"dev{rank}",
        "rank": rank,
        "rank_score": 100.0,
        "tier": "Elite",
        "percentile": 99.9,
        "country": "India",
        "primary_language": "Python",
        "merged_prs": 12,
        "is_human_verified": True,
        "is_bot_classified": False,
        "bot_penalty": 0.0,
        "avatar_url": "https://avatars.githubusercontent.com/u/1",
    }
    row.update(extra)
    return row


def test_compact_user_projects_documented_fields_and_urls():
    record = compact_user(entry(3, rank_delta=2, score_delta=1.5))
    assert record["username"] == "dev3"
    assert record["rank_delta"] == 2 and record["score_delta"] == 1.5
    assert record["profile_url"] == f"{SITE}/profile/dev3"
    assert record["badge_url"] == f"{SITE}/badges/dev3.svg"
    assert record["api_url"] == f"{SITE}/api/user/dev3.json"
    assert "bio" not in record
    assert "avatar_url" not in record


def test_compact_user_include_heavy_keeps_profile_fields():
    record = compact_user(entry(3), {"bio": "Builder"}, include_heavy=True)
    assert record["bio"] == "Builder"
    assert record["avatar_url"].startswith("https://avatars.githubusercontent.com/")


def test_compact_user_uses_default_badge_outside_top_1000():
    assert compact_user(entry(1001))["badge_url"] == f"{SITE}/badges/default.svg"


def test_compact_user_links_outside_top_1000_to_github():
    # Only the top 1,000 have pre-rendered profile pages; everyone else goes to GitHub.
    record = compact_user(entry(1001))
    assert record["profile_url"] == "https://github.com/dev1001"
    assert compact_user(entry(1000))["profile_url"] == f"{SITE}/profile/dev1000"


def test_compact_user_without_profile_page_links_to_github():
    record = compact_user(entry(5), has_profile=False)
    assert record["profile_url"] == "https://github.com/dev5"


def test_compact_user_merges_profile_extras():
    profile = {
        "company": "Acme",
        "bio": "Builder",
        "history": [{"d": "2026-11-21", "r": 3, "s": 99.0}],
    }
    record = compact_user(entry(3), profile)
    assert record["company"] == "Acme"
    assert "bio" not in record  # heavy fields stay in the per-user payload
    assert "history" not in record  # leaderboard stays compact
    assert compact_user(entry(3), profile, include_heavy=True)["bio"] == "Builder"


def test_build_user_includes_history():
    profile = {"history": [{"d": "2026-11-21", "r": 3, "s": 99.0}]}
    payload = build_user(entry(3), profile, {"last_run_utc": "2026-11-21T06:00:00Z"}, "2026-11-21")
    assert payload["snapshot_date"] == "2026-11-21"
    assert payload["user"]["history"] == profile["history"]
    assert payload["user"]["avatar_url"].startswith("https://avatars.githubusercontent.com/")


def test_build_user_reads_counted_merges_from_profile():
    # Evidence lives in data/profiles/<name>.json, not in the leaderboard row.
    profile = {"counted_merges": [{"repo": "acme/app", "prs": 4}], "measured": {"delivery": True}}
    payload = build_user(entry(3), profile, {"last_run_utc": "x"}, "2026-11-21")
    assert payload["user"]["counted_merges"] == profile["counted_merges"]
    assert payload["user"]["measured"] == profile["measured"]


def test_build_leaderboard_metadata_and_count():
    payload = build_leaderboard([entry(1), entry(2)], {"source": "real", "last_run_utc": "x"}, "2026-11-21")
    assert payload["count"] == 2
    assert len(payload["users"]) == 2
    assert payload["site"] == SITE
    assert payload["weights"]["delivery"] == 0.35
    assert payload["url_templates"]["user_api"].endswith("{username}.json")


def test_llms_text_lists_endpoints_and_top_users():
    payload = build_leaderboard([entry(1, rank_score=120.5)], {"source": "real"}, "2026-11-21")
    text = llms_text(payload, payload)
    assert text.startswith("# RealGitRank")
    assert f"{SITE}/api/leaderboard.json" in text
    assert f"{SITE}/api/user/dev1.json" in text
    assert "dev1 — score 120.5" in text
    assert "Delivery (35%)" in text


def test_generate_writes_leaderboard_user_files_and_llms(tmp_path):
    lb_path = tmp_path / "leaderboard.json"
    lb_path.write_text(json.dumps([entry(1), entry(2)]), encoding="utf-8")
    status_path = tmp_path / "status.json"
    status_path.write_text(json.dumps({"last_run_utc": "2026-11-21T06:00:00Z", "source": "real"}), encoding="utf-8")
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    (profiles / "dev1.json").write_text(json.dumps({"username": "dev1", "company": "Acme"}), encoding="utf-8")

    generate(
        leaderboard_path=str(lb_path),
        out_dir=str(tmp_path / "api"),
        llms_path=str(tmp_path / "llms.txt"),
        status_path=str(status_path),
        profiles_dir=str(profiles),
        history_dir=str(tmp_path / "missing-history"),
    )

    board = json.loads((tmp_path / "api" / "leaderboard.json").read_text(encoding="utf-8"))
    assert board["count"] == 2
    assert "avatar_url" not in board["users"][0]
    user = json.loads((tmp_path / "api" / "user" / "dev1.json").read_text(encoding="utf-8"))
    assert user["user"]["company"] == "Acme"
    assert user["user"]["avatar_url"].startswith("https://avatars.githubusercontent.com/")
    assert (tmp_path / "api" / "user" / "dev2.json").exists()
    assert (tmp_path / "llms.txt").read_text(encoding="utf-8").startswith("# RealGitRank")
