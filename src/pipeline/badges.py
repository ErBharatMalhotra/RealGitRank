"""RealGitRank badge SVG generator — beautiful badges for ranked AND unranked users."""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.makedirs("web/public/badges", exist_ok=True)

with open("data/leaderboard.json") as f:
    lb = json.load(f)
by_name = {u["username"]: u for u in lb}


def esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def badge_svg(username, score=None, rank=None, verified=False, unranked=False):
    if unranked:
        title = "Not Yet Ranked"
        sub = "Join RealGitRank"
        grad = "#64748b,#334155"
        accent = "#94a3b8"
    else:
        title = f"{score:.0f}" if score is not None else "—"
        sub = f"Rank #{rank}" if rank else "Unranked"
        if verified:
            grad = "#059669,#047857"
            accent = "#34d399"
        else:
            grad = "#2563eb,#1e40af"
            accent = "#60a5fa"
    seal = ""
    if verified and not unranked:
        seal = '<circle cx="150" cy="22" r="9" fill="#fbbf24"/><text x="150" y="26" text-anchor="middle" font-size="11" fill="#78350f">✓</text>'
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="200" height="60" viewBox="0 0 200 60">
  <defs>
    <linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="{grad.split(',')[0]}"/>
      <stop offset="100%" stop-color="{grad.split(',')[1]}"/>
    </linearGradient>
    <filter id="s"><feDropShadow dx="0" dy="2" stdDeviation="2" flood-opacity="0.25"/></filter>
  </defs>
  <rect width="200" height="60" rx="10" fill="url(#g)" filter="url(#s)"/>
  <text x="14" y="20" font-family="Segoe UI,Arial,sans-serif" font-size="11" font-weight="600" fill="#ffffff" opacity="0.85">RealGitRank</text>
  {seal}
  <text x="14" y="42" font-family="Segoe UI,Arial,sans-serif" font-size="20" font-weight="800" fill="#ffffff">{esc(title)}</text>
  <text x="14" y="56" font-family="Segoe UI,Arial,sans-serif" font-size="10" fill="#ffffff" opacity="0.9">{esc(sub)} · {esc(username)}</text>
  <text x="186" y="56" text-anchor="end" font-family="Segoe UI,Arial,sans-serif" font-size="9" fill="#ffffff" opacity="0.7">impact index</text>
</svg>'''


# Generate for all ranked users
for u in lb:
    svg = badge_svg(u["username"], u["rank_score"], u["rank"], u["is_human_verified"])
    with open(f"web/public/badges/{u['username']}.svg", "w", encoding="utf-8") as f:
        f.write(svg)

# Generic unranked badge template (used for any username not in leaderboard)
unranked_svg = badge_svg("your-username", unranked=True)
with open("web/public/badges/_unranked.svg", "w", encoding="utf-8") as f:
    f.write(unranked_svg)

with open("web/public/badges/default.svg", "w", encoding="utf-8") as f:
    f.write(unranked_svg)

print(f"Generated {len(lb)} ranked badges + unranked template")