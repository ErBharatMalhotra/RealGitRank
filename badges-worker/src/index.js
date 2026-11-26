/**
 * RealGitRank badge endpoint — per-user SVG for every listed developer.
 *
 * The site itself is static (GitHub Pages), so it can only ship pre-built badge
 * files and caps those at the global top 1,000. This Worker closes that gap
 * without any new database: it reads the published search buckets
 * ([username, rank, score] per first character) from the site origin, keeps the
 * parsed bucket memoised per isolate, and wraps the result in a cacheable SVG.
 *
 *   GET /badges/<username>.svg  -> badge (ranked / listed / not listed)
 *   GET /                       -> short usage page
 */

const ORIGIN = "https://realgitrank.bharatdn.com";
const BUCKETS = "abcdefghijklmnopqrstuvwxyz0123456789";

const MEMO_LIMIT = 3;
const MEMO_TTL_MS = 6 * 60 * 60 * 1000;
const BUCKET_CACHE_TTL = 60 * 60;
const RESPONSE_CACHE_TTL = 60 * 60;

const memo = new Map();

function bucketKey(name) {
  const first = String(name).slice(0, 1).toLowerCase();
  return /[a-z0-9]/.test(first) ? first : "other";
}

async function loadBucket(key) {
  if (!BUCKETS.includes(key)) return null;
  const now = Date.now();
  const hit = memo.get(key);
  if (hit && now - hit.at < MEMO_TTL_MS) {
    // Refresh insertion order so the hottest buckets survive eviction.
    memo.delete(key);
    memo.set(key, hit);
    return hit.index;
  }
  let rows;
  try {
    const res = await fetch(`${ORIGIN}/data/search/${key}.json`, {
      cf: { cacheEverything: true, cacheTtl: BUCKET_CACHE_TTL },
    });
    if (!res.ok) return null;
    rows = await res.json();
  } catch (_) {
    return null;
  }
  if (!Array.isArray(rows)) return null;
  const index = new Map();
  for (const row of rows) {
    if (!Array.isArray(row) || !row[0]) continue;
    const login = String(row[0]);
    index.set(login.toLowerCase(), {
      login,
      rank: Number.isFinite(row[1]) ? row[1] : null,
      score: Number.isFinite(row[2]) ? row[2] : null,
      // Explicit state when the pipeline publishes it; otherwise fall back to
      // the old score-based inference for buckets cached from an older run.
      listed: row[3] === "listed" || (row.length < 4 && !(row[2] > 0)),
    });
  }
  if (memo.size >= MEMO_LIMIT) memo.delete(memo.keys().next().value);
  memo.set(key, { index, at: now });
  return index;
}

function esc(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function fit(name, max = 17) {
  const value = String(name);
  return value.length > max ? `${value.slice(0, max - 1)}…` : value;
}

/**
 * Three honest states. A listed account has no rank claim of its own (no public
 * push or merged PR in the window), so it must not be shown as "score 0".
 */
function badgeSvg(user) {
  const state = !user ? "unknown" : user.listed ? "listed" : "ranked";
  const palette = {
    ranked: { from: "#1e3a8a", to: "#1e40af", accent: "#60a5fa" },
    listed: { from: "#334155", to: "#475569", accent: "#94a3b8" },
    unknown: { from: "#0f172a", to: "#1f2937", accent: "#64748b" },
  }[state];

  const score = state === "ranked" && Number.isFinite(user.score) ? String(Math.round(user.score)) : "—";
  const value = state === "ranked" ? score : state === "listed" ? "LISTED" : "—";
  const rankText = user && user.rank ? `#${user.rank}` : "ranked";
  const caption =
    state === "ranked" ? `impact index · ${rankText}` : state === "listed" ? "no public work in window" : "open realgitrank to verify";
  const unit = state === "ranked" ? "SCORE" : state === "listed" ? "LISTED" : "NOT FOUND";
  const handle = state === "unknown" ? "@your-username" : `@${fit(user.login)}`;
  const valueSize = state === "ranked" ? 22 : state === "listed" ? 13 : 22;

  return `<svg xmlns="http://www.w3.org/2000/svg" width="220" height="64" viewBox="0 0 220 64" role="img" aria-label="RealGitRank badge">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="${palette.from}"/>
      <stop offset="100%" stop-color="${palette.to}"/>
    </linearGradient>
    <filter id="shadow"><feDropShadow dx="0" dy="3" stdDeviation="3" flood-opacity="0.3"/></filter>
  </defs>
  <rect width="220" height="64" rx="10" fill="url(#bg)" filter="url(#shadow)"/>
  <text x="14" y="18" font-family="Segoe UI,Arial,sans-serif" font-size="10" font-weight="700" fill="#ffffff" opacity="0.75" letter-spacing="1">REALGITRANK</text>
  <text x="14" y="40" font-family="Segoe UI,Arial,sans-serif" font-size="15" font-weight="800" fill="#ffffff">${esc(handle)}</text>
  <text x="14" y="56" font-family="Segoe UI,Arial,sans-serif" font-size="9" fill="#ffffff" opacity="0.7">${esc(caption)}</text>
  <rect x="150" y="8" width="58" height="48" rx="6" fill="#ffffff" opacity="0.12"/>
  <text x="179" y="34" text-anchor="middle" font-family="Segoe UI,Arial,sans-serif" font-size="${valueSize}" font-weight="900" fill="#ffffff">${esc(value)}</text>
  <text x="179" y="48" text-anchor="middle" font-family="Segoe UI,Arial,sans-serif" font-size="8" font-weight="600" fill="#ffffff" opacity="0.8">${esc(unit)}</text>
  <circle cx="204" cy="14" r="4" fill="${palette.accent}"/>
</svg>`;
}

function svgResponse(svg) {
  return new Response(svg, {
    headers: {
      "content-type": "image/svg+xml; charset=utf-8",
      "cache-control": `public, max-age=1800, s-maxage=${RESPONSE_CACHE_TTL}, stale-while-revalidate=86400`,
      "access-control-allow-origin": "*",
      "x-content-type-options": "nosniff",
    },
  });
}

function landing() {
  const body = `<!doctype html><meta charset="utf-8"><title>RealGitRank badges</title>
<body style="font-family:system-ui,sans-serif;max-width:640px;margin:40px auto;padding:0 16px;line-height:1.55;color:#0f172a">
<h1>RealGitRank badges</h1>
<p>Per-user SVG badge for every listed developer, not just the global top 1,000.</p>
<pre style="background:#f1f5f9;padding:12px;border-radius:8px;overflow:auto">[![RealGitRank](https://HOST/badges/your-username.svg)](https://realgitrank.bharatdn.com/profile/your-username)</pre>
<p>States: <strong>score + rank</strong> when the account is ranked, <strong>LISTED</strong> when it has no public push or merged PR inside the scoring window, <strong>NOT FOUND</strong> when the username is not in the snapshot. Nothing is judged: listed accounts simply claim no rank.</p>
<p><a href="https://realgitrank.bharatdn.com/">Leaderboard</a> · <a href="https://realgitrank.bharatdn.com/methodology/">Methodology</a></p>`;
  return new Response(body, {
    headers: {
      "content-type": "text/html; charset=utf-8",
      "cache-control": "public, max-age=3600, s-maxage=86400",
    },
  });
}

export default {
  async fetch(request) {
    try {
      return await handle(request);
    } catch (error) {
      // Never let the runtime answer with its generic 502: a badge URL is
      // hot-linked from READMEs, so a failure must still be a readable answer.
      return new Response(
        `RealGitRank badge error\n${error && (error.stack || error.message || error)}`,
        { status: 503, headers: { "content-type": "text/plain; charset=utf-8", "cache-control": "no-store" } },
      );
    }
  },
};

async function handle(request) {
    const url = new URL(request.url);
    if (request.method !== "GET" && request.method !== "HEAD") {
      return new Response("Method not allowed", { status: 405, headers: { allow: "GET, HEAD" } });
    }

    const path = url.pathname.replace(/\/+$/, "");
    if (path === "" || path === "/") return landing();

    // Deployment diagnostic: which step of a badge request works on this runtime.
    if (path === "/__diag") {
      const out = { hasCaches: typeof caches !== "undefined", cacheDefault: false, bucket: null, rows: 0, error: null };
      try {
        out.cacheDefault = Boolean(caches && caches.default);
      } catch (error) {
        out.error = `caches: ${error.message}`;
      }
      try {
        const res = await fetch(`${ORIGIN}/data/search/m.json`, { cf: { cacheEverything: true, cacheTtl: 60 } });
        out.bucket = res.status;
        if (res.ok) {
          const rows = await res.json();
          out.rows = Array.isArray(rows) ? rows.length : -1;
        }
      } catch (error) {
        out.error = `fetch: ${error.message}`;
      }
      return new Response(JSON.stringify(out), { headers: { "content-type": "application/json" } });
    }

    const match = path.match(/^\/(?:badges\/)?([^/]+?)(?:\.svg)?$/i);
    const raw = match && decodeURIComponent(match[1]);
    if (!raw || !/^[A-Za-z0-9-]{1,39}$/.test(raw)) {
      return new Response("Badge not found", { status: 400 });
    }
    const index = await loadBucket(bucketKey(raw));
    const user = index ? index.get(raw.toLowerCase()) : null;
    // No Cache API here: this runtime does not permit caches.default. The SVG is
    // cacheable at the edge through its own cache-control header, and the bucket
    // JSON is memoised in the isolate (plus edge-cached by its cf options).
    return svgResponse(badgeSvg(user));
}
