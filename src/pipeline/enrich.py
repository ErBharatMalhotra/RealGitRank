"""GitHub GraphQL enrichment for top users: location, avatar, bio, orgs. Optional (needs GITHUB_TOKEN)."""
import os, sys, json, time, urllib.request
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

TOKEN = os.environ.get("GH_ENRICH_TOKEN") or os.environ.get("GITHUB_TOKEN")
GQL_URL = "https://api.github.com/graphql"
BATCH = 50  # 50 users per query (rate limit friendly)


def gql_query(logins):
    """Query GitHub GraphQL for user metadata. Returns dict login->info."""
    if not TOKEN:
        return {}
    # Build aliased query
    parts = []
    for i, login in enumerate(logins):
        safe = login.replace('"', '\\"')
        parts.append(f'u{i}: user(login: "{safe}") {{ login location avatarUrl bio company }}')
    query = "query { " + " ".join(parts) + " }"
    body = json.dumps({"query": query}).encode()
    req = urllib.request.Request(GQL_URL, data=body, headers={
        "Authorization": f"Bearer {TOKEN}",
        "Content-Type": "application/json",
        "User-Agent": "RealGitRank/1.0",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
    except Exception as e:
        print(f"  GQL error: {e}")
        return {}
    result = {}
    for i, login in enumerate(logins):
        u = (data.get("data") or {}).get(f"u{i}")
        if u:
            result[login] = {
                "location": u.get("location") or "",
                "avatar_url": u.get("avatarUrl") or "",
                "bio": u.get("bio") or "",
                "company": u.get("company") or "",
            }
    return result


# Country extraction from free-text location
COUNTRY_ALIASES = {
    "india": "India", "bharat": "India", "bangalore": "India", "mumbai": "India",
    "delhi": "India", "hyderabad": "India", "pune": "India", "chennai": "India",
    "united states": "United States", "usa": "United States", "us": "United States",
    "san francisco": "United States", "new york": "United States", "seattle": "United States",
    "austin": "United States", "chicago": "United States", "boston": "United States",
    "los angeles": "United States", "denver": "United States",
    "united kingdom": "United Kingdom", "uk": "United Kingdom", "london": "United Kingdom",
    "germany": "Germany", "berlin": "Germany", "munich": "Germany", "frankfurt": "Germany",
    "france": "France", "paris": "France",
    "japan": "Japan", "tokyo": "Japan",
    "china": "China", "beijing": "China", "shanghai": "China", "shenzhen": "China",
    "brazil": "Brazil", "sao paulo": "Brazil",
    "canada": "Canada", "toronto": "Canada", "vancouver": "Canada",
    "australia": "Australia", "sydney": "Australia", "melbourne": "Australia",
    "russia": "Russia", "moscow": "Russia",
    "netherlands": "Netherlands", "amsterdam": "Netherlands",
    "spain": "Spain", "madrid": "Spain", "barcelona": "Spain",
    "italy": "Italy", "rome": "Italy", "milan": "Italy",
    "poland": "Poland", "warsaw": "Poland",
    "sweden": "Sweden", "stockholm": "Sweden",
    "switzerland": "Switzerland", "zurich": "Switzerland",
    "singapore": "Singapore",
    "south korea": "South Korea", "korea": "South Korea", "seoul": "South Korea",
    "israel": "Israel", "tel aviv": "Israel",
    "turkey": "Turkey", "istanbul": "Turkey",
    "mexico": "Mexico",
    "argentina": "Argentina", "buenos aires": "Argentina",
    "nigeria": "Nigeria",
    "egypt": "Egypt", "cairo": "Egypt",
    "south africa": "South Africa",
    "kenya": "Kenya", "nairobi": "Kenya",
    "indonesia": "Indonesia", "jakarta": "Indonesia",
    "thailand": "Thailand", "bangkok": "Thailand",
    "vietnam": "Vietnam",
    "philippines": "Philippines", "manila": "Philippines",
    "malaysia": "Malaysia", "kuala lumpur": "Malaysia",
    "pakistan": "Pakistan", "karachi": "Pakistan", "lahore": "Pakistan",
    "bangladesh": "Bangladesh", "dhaka": "Bangladesh",
    "saudi arabia": "Saudi Arabia", "riyadh": "Saudi Arabia",
    "uae": "United Arab Emirates", "dubai": "United Arab Emirates",
    "ukraine": "Ukraine", "kyiv": "Ukraine",
    "poland": "Poland",
}


def extract_country(location):
    if not location:
        return ""
    loc = location.lower()
    # Check full location string first
    for key, val in COUNTRY_ALIASES.items():
        if key in loc:
            return val
    # Last comma part
    parts = [p.strip() for p in location.split(",")]
    if parts:
        last = parts[-1].lower()
        for key, val in COUNTRY_ALIASES.items():
            if key == last or key in last:
                return val
    return ""


def enrich(top_n=10000, do_language=False):
    if not TOKEN:
        print("No GITHUB_TOKEN — skipping enrichment (country stays empty)")
        return False

    with open("data/leaderboard.json") as f:
        lb = json.load(f)
    # Cache
    cache_path = "data/enrichment_cache.json"
    cache = {}
    if os.path.exists(cache_path):
        with open(cache_path) as f:
            cache = json.load(f)

    top_users = sorted(lb, key=lambda x: x["rank"])[:top_n]
    to_fetch = [u["username"] for u in top_users if u["username"] not in cache]

    print(f"Enriching {len(to_fetch)} new users (cached: {len(cache)})...")

    for i in range(0, len(to_fetch), BATCH):
        batch = to_fetch[i:i + BATCH]
        res = gql_query(batch)
        for login, info in res.items():
            cache[login] = info
        # Rate limit: ~100 points per batch, sleep to be safe
        time.sleep(1)
        if (i // BATCH) % 10 == 0:
            # Save cache periodically
            with open(cache_path, "w") as f:
                json.dump(cache, f)
            print(f"  enriched {min(i + BATCH, len(to_fetch))}/{len(to_fetch)}")

    with open(cache_path, "w") as f:
        json.dump(cache, f)

    # Apply enrichment to leaderboard + profiles
    updated = 0
    for u in lb:
        info = cache.get(u["username"])
        if info:
            country = extract_country(info["location"])
            if country:
                u["country"] = country
                updated += 1
            u["avatar_url"] = info.get("avatar_url", "")
            u["bio"] = info.get("bio", "")
            # Save avatar/bio to profile if exists
            prof_path = f"data/profiles/{u['username']}.json"
            if os.path.exists(prof_path):
                with open(prof_path) as f:
                    prof = json.load(f)
                prof["avatar_url"] = info.get("avatar_url", "")
                prof["bio"] = info.get("bio", "")
                prof["company"] = info.get("company", "")
                prof["location"] = info.get("location", "")
                if country:
                    prof["country"] = country
                with open(prof_path, "w") as f:
                    json.dump(prof, f, indent=2)

    with open("data/leaderboard.json", "w") as f:
        json.dump(lb, f, indent=2)

    print(f"Enrichment complete: {updated} countries updated")
    return True


def enrich_language(top_n=1000):
    """One-time: fetch primary language from top repos. Cached permanently."""
    if not TOKEN:
        print("No token — skipping language enrichment")
        return False

    cache_path = "data/language_cache.json"
    cache = {}
    if os.path.exists(cache_path):
        with open(cache_path) as f:
            cache = json.load(f)

    with open("data/leaderboard.json") as f:
        lb = json.load(f)
    top_users = sorted(lb, key=lambda x: x["rank"])[:top_n]
    to_fetch = [u["username"] for u in top_users if u["username"] not in cache]

    print(f"Language enrichment: {len(to_fetch)} new (cached: {len(cache)})")

    for i in range(0, len(to_fetch), BATCH):
        batch = to_fetch[i:i + BATCH]
        parts = []
        for j, login in enumerate(batch):
            safe = login.replace('"', '\\"')
            parts.append(f'u{j}: user(login: "{safe}") {{ repositories(first: 3, orderBy: {{field: STARGAZERS, direction: DESC}}) {{ nodes {{ primaryLanguage {{ name }} }} }} }}')
        query = "query { " + " ".join(parts) + " }"
        body = json.dumps({"query": query}).encode()
        req = urllib.request.Request(GQL_URL, data=body, headers={
            "Authorization": f"Bearer {TOKEN}",
            "Content-Type": "application/json",
            "User-Agent": "RealGitRank/1.0",
        })
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read())
        except Exception as e:
            print(f"  GQL error: {e}")
            continue
        for j, login in enumerate(batch):
            u = (data.get("data") or {}).get(f"u{j}")
            if u:
                repos = (u.get("repositories") or {}).get("nodes") or []
                langs = [r.get("primaryLanguage", {}).get("name") for r in repos if r.get("primaryLanguage")]
                cache[login] = langs[0] if langs else ""
        time.sleep(1)
        if (i // BATCH) % 5 == 0:
            with open(cache_path, "w") as f:
                json.dump(cache, f)
            print(f"  {min(i + BATCH, len(to_fetch))}/{len(to_fetch)}")

    with open(cache_path, "w") as f:
        json.dump(cache, f)

    # Apply to leaderboard + profiles
    updated = 0
    for u in lb:
        lang = cache.get(u["username"])
        if lang:
            u["primary_language"] = lang
            updated += 1
            prof_path = f"data/profiles/{u['username']}.json"
            if os.path.exists(prof_path):
                with open(prof_path) as f:
                    prof = json.load(f)
                prof["primary_language"] = lang
                with open(prof_path, "w") as f:
                    json.dump(prof, f, indent=2)

    with open("data/leaderboard.json", "w") as f:
        json.dump(lb, f, indent=2)

    print(f"Language enrichment: {updated} updated")
    return True


if __name__ == "__main__":
    enrich()