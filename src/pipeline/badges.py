"""RealGitRank badge SVG generator — polished, professional badges."""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.makedirs("web/public/badges", exist_ok=True)

with open("data/leaderboard.json") as f:
    lb = json.load(f)


def esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def badge_svg(username, score=None, rank=None, verified=False, unranked=False):
    if unranked:
        left_color = "#475569"
        right_color = "#334155"
        title = "RealGitRank"
        value = "Not Ranked"
        sub = "Join to get scored"
        accent = "#94a3b8"
    else:
        if verified:
            left_color = "#065f46"
            right_color = "#047857"
            accent = "#34d399"
        else:
            left_color = "#1e3a8a"
            right_color = "#1e40af"
            accent = "#60a5fa"
        title = "RealGitRank"
        value = f"{score:.0f}" if score is not None else "—"
        sub = f"@{username}"
        rank_text = f"#{rank}" if rank else "—"

    seal = ""
    if verified and not unranked:
        seal = f'<g transform="translate(188,12)"><circle r="8" fill="#fbbf24"/><text y="4" text-anchor="middle" font-size="11" font-weight="900" fill="#78350f">✓</text></g>'

    rank_badge = ""
    if not unranked and rank:
        rank_badge = f'<text x="188" y="42" text-anchor="middle" font-size="9" font-weight="700" fill="#ffffff" opacity="0.9">{rank_text}</text><text x="188" y="52" text-anchor="middle" font-size="7" fill="#ffffff" opacity="0.7">rank</text>'

    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="220" height="64" viewBox="0 0 220 64">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="{left_color}"/>
      <stop offset="100%" stop-color="{right_color}"/>
    </linearGradient>
    <filter id="shadow"><feDropShadow dx="0" dy="3" stdDeviation="3" flood-opacity="0.3"/></filter>
  </defs>
  <rect width="220" height="64" rx="10" fill="url(#bg)" filter="url(#shadow)"/>
  <!-- Left: brand + username -->
  <text x="14" y="18" font-family="Segoe UI,Arial,sans-serif" font-size="10" font-weight="700" fill="#ffffff" opacity="0.75" letter-spacing="1">REALGITRANK</text>
  <text x="14" y="40" font-family="Segoe UI,Arial,sans-serif" font-size="15" font-weight="800" fill="#ffffff">{esc(sub)}</text>
  <text x="14" y="56" font-family="Segoe UI,Arial,sans-serif" font-size="9" fill="#ffffff" opacity="0.7">{esc("verified human" if (verified and not unranked) else ("impact index" if not unranked else ""))}</text>
  <!-- Right: score -->
  <rect x="150" y="8" width="58" height="48" rx="6" fill="#ffffff" opacity="0.12"/>
  <text x="179" y="34" text-anchor="middle" font-family="Segoe UI,Arial,sans-serif" font-size="22" font-weight="900" fill="#ffffff">{esc(value)}</text>
  <text x="179" y="48" text-anchor="middle" font-family="Segoe UI,Arial,sans-serif" font-size="8" font-weight="600" fill="#ffffff" opacity="0.8">SCORE</text>
  {seal}
  {rank_badge}
</svg>'''


# Generate for all ranked users
for u in lb:
    svg = badge_svg(u["username"], u["rank_score"], u["rank"], u["is_human_verified"])
    with open(f"web/public/badges/{u['username']}.svg", "w", encoding="utf-8") as f:
        f.write(svg)

# Generic unranked badge template
unranked_svg = badge_svg("your-username", unranked=True)
with open("web/public/badges/_unranked.svg", "w", encoding="utf-8") as f:
    f.write(unranked_svg)
with open("web/public/badges/default.svg", "w", encoding="utf-8") as f:
    f.write(unranked_svg)

print(f"Generated {len(lb)} ranked badges + unranked template")