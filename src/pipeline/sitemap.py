"""Generate sitemap.xml for RealGitRank (top profiles + countries)."""
import json, os

with open("data/leaderboard.json") as f:
    lb = json.load(f)

countries = sorted(set(u["country"] for u in lb))

urls = [
    "https://realgitrank.bharatdn.com/",
    "https://realgitrank.bharatdn.com/methodology/",
    "https://realgitrank.bharatdn.com/badges/",
    "https://realgitrank.bharatdn.com/countries",
]
for u in lb[:1000]:
    urls.append(f"https://realgitrank.bharatdn.com/profile/{u['username']}")
for c in countries:
    urls.append(f"https://realgitrank.bharatdn.com/country/{c.lower().replace(' ', '-')}")

xml = '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
for u in urls:
    xml += f"  <url><loc>{u}</loc><changefreq>weekly</changefreq><priority>0.7</priority></url>\n"
xml += "</urlset>\n"

os.makedirs("web/public", exist_ok=True)
with open("web/public/sitemap.xml", "w", encoding="utf-8") as f:
    f.write(xml)
print(f"Sitemap: {len(urls)} URLs")