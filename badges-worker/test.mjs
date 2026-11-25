// Local check without wrangler: stubs fetch + caches and exercises the three
// badge states, caching headers, and the not-found path.
import assert from "node:assert/strict";
import worker from "./src/index.js";

const BUCKET = [
  ["mattpocock", 1, 873.9],
  ["emptyowner", 412000, 0],
  ["CaseUser", 30061, 705.1],
];

let bucketFetches = 0;
globalThis.fetch = async (url) => {
  bucketFetches += 1;
  const href = String(url);
  if (!href.startsWith("https://realgitrank.bharatdn.com/data/search/")) {
    throw new Error(`unexpected fetch ${href}`);
  }
  return new Response(JSON.stringify(BUCKET), { status: 200 });
};

const call = (path) => worker.fetch(new Request(`https://badges.example${path}`));

const ranked = await call("/badges/mattpocock.svg");
const rankedBody = await ranked.text();
assert.ok(ranked.status === 200, "ranked status");
assert.ok(rankedBody.includes(">874<"), "ranked score rendered (rounded)");
assert.ok(rankedBody.includes("#1"), "rank rendered");
assert.ok((ranked.headers.get("content-type") || "").includes("image/svg+xml"), "svg content type");
assert.ok((ranked.headers.get("cache-control") || "").includes("s-maxage"), "cache header");

const listed = await (await call("/badges/emptyowner.svg")).text();
assert.ok(listed.includes("LISTED") && !listed.includes(">0<"), "listed state claims no zero score");

const missing = await (await call("/badges/nobodyhere.svg")).text();
assert.ok(missing.includes("NOT FOUND"), "unknown user state");

const mixed = await (await call("/CaseUser.svg")).text();
assert.ok(mixed.includes("705"), "case-insensitive lookup");

const bad = await call("/badges/!!.svg");
assert.equal(bad.status, 400, "invalid username rejected");

const landing = await call("/");
assert.ok((await landing.text()).includes("Per-user SVG badge"), "landing page");

// The bucket must be fetched once and then served from the isolate memo, even
// across usernames and repeat hits (the runtime has no Cache API here).
// The memo keeps the 3 most recent buckets, so re-request those two.
const before = bucketFetches;
await call("/badges/CaseUser.svg");
await call("/badges/nobodyhere.svg");
assert.equal(bucketFetches, before, "bucket was refetched unnecessarily");

console.log("badge worker checks passed");
