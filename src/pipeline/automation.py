"""Keep per-account automation signals out of the public, git-tracked data.

The score already includes the automation adjustment, so the classification
values are only needed by internal steps of the same run (coverage, quality
gates). They are written to a gitignored sidecar and stripped from every
committed file: the leaderboard, the top profiles and the served payloads.
"""
import json
import os

SIGNAL_FIELDS = ("bot_penalty", "bot_evidence", "is_human_verified", "is_bot_classified")
SIGNALS_PATH = "data/automation_signals.json"


def strip_signals(entry):
    """Return a copy of ``entry`` without the per-account automation signals."""
    return {k: v for k, v in entry.items() if k not in SIGNAL_FIELDS}


def collect_signals(entries):
    """Map username -> the automation signals, for internal consumers only."""
    signals = {}
    for entry in entries:
        username = entry.get("username")
        if not username:
            continue
        values = {k: entry[k] for k in SIGNAL_FIELDS if k in entry}
        if values:
            signals[username] = values
    return signals


def write_signals(entries, path=SIGNALS_PATH):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(collect_signals(entries), f, separators=(",", ":"))
    return path


def load_signals(path=SIGNALS_PATH):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def apply_signals(entries, signals):
    """Merge sidecar signals into in-memory rows for internal gate/coverage steps."""
    if not signals:
        return entries
    for entry in entries:
        values = signals.get(entry.get("username"))
        if values:
            entry.update(values)
    return entries


def sanitize_files(leaderboard_path="data/leaderboard.json", profiles_dir="data/profiles"):
    """Strip signals from an existing leaderboard and profiles in place.

    The sidecar is written first, so coverage and quality gates keep working.
    """
    try:
        with open(leaderboard_path, encoding="utf-8") as f:
            entries = json.load(f)
    except (OSError, ValueError):
        return 0, 0
    if not isinstance(entries, list):
        return 0, 0

    write_signals(entries)
    with open(leaderboard_path, "w", encoding="utf-8") as f:
        json.dump([strip_signals(e) for e in entries], f, indent=2)

    profiles = 0
    if os.path.isdir(profiles_dir):
        for name in os.listdir(profiles_dir):
            if not name.endswith(".json"):
                continue
            path = os.path.join(profiles_dir, name)
            try:
                with open(path, encoding="utf-8") as f:
                    profile = json.load(f)
            except (OSError, ValueError):
                continue
            if not isinstance(profile, dict) or not any(k in profile for k in SIGNAL_FIELDS):
                continue
            with open(path, "w", encoding="utf-8") as f:
                json.dump(strip_signals(profile), f, indent=2)
            profiles += 1
    return len(entries), profiles


def main():
    rows, profiles = sanitize_files()
    print(
        f"Automation signals moved to {SIGNALS_PATH}: "
        f"{rows} leaderboard rows, {profiles} profiles stripped"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
