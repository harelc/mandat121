import { createHash } from "node:crypto";
import { getStore } from "@netlify/blobs";

// First-party traffic counter (no cookies, no third party).
// A 1-line beacon in index.html POSTs {p: location.pathname} here on
// page load. We store, per Asia/Jerusalem hour, in Netlify Blobs:
//   { pv, pages: {path: {pv, ips: [hash...]}}, ips: [hash...] }
// so a report can give pageviews and UNIQUE IPs. Raw IPs are never
// stored: only sha256(ip | day | HIT_SALT), truncated — the day in
// the hash means yesterday's identifiers cannot be linked to
// today's. Counts are approximate (read-modify-write per hour can
// lose an increment under heavy concurrency) — a gauge, not billing.
//
// Report: GET with header x-hit-token: <HIT_READ_TOKEN> returns the
// stored hour buckets, newest first. Without the token: 403.
//
// Retention: 90 days of hour buckets. Pruning is opportunistic — the
// first hit that opens a new hour bucket deletes older ones — so the
// store (and the token report, which reads every bucket) stays
// bounded with no separate cleanup job to babysit.

const RETENTION_DAYS = 90;

async function pruneOldHours(store, todayDay) {
  const cutoff = new Date(`${todayDay}T12:00:00Z`);
  cutoff.setUTCDate(cutoff.getUTCDate() - RETENTION_DAYS);
  const cutoffDay = cutoff.toISOString().slice(0, 10); // keys sort by date, so compare the date part lexically
  let cursor;
  do {
    const page = await store.list({ prefix: "h:", cursor });
    for (const { key } of page.blobs) {
      if (key.slice(2, 12) < cutoffDay) await store.delete(key).catch(() => {});
    }
    cursor = page.cursor;
  } while (cursor);
}

const BOT_UA = /bot|crawler|spider|headless|preview|monitor/i;

function jerusalemParts(now = new Date()) {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Jerusalem", year: "numeric", month: "2-digit",
    day: "2-digit", hour: "2-digit", hourCycle: "h23",
  }).formatToParts(now);
  const get = (t) => parts.find((p) => p.type === t).value;
  const hour = get("hour") === "24" ? "00" : get("hour");
  return { day: `${get("year")}-${get("month")}-${get("day")}`, hour };
}

function clientIp(request) {
  const nf = request.headers.get("x-nf-client-connection-ip");
  if (nf) return nf.trim();
  const fwd = request.headers.get("x-forwarded-for");
  return fwd ? fwd.split(",")[0].trim() : "";
}

const ONLINE_TTL_MS = 120_000; // 4 missed 30s heartbeats = gone
const MAX_SESSIONS_PER_IP = 5; // blunt cheap presence inflation from one address
const SESSION_ID = /^[A-Za-z0-9_-]{16,64}$/;

async function countOnline(store, now = Date.now()) {
  const { blobs } = await store.list({ prefix: "online:" });
  const byIp = new Map();
  let total = 0;
  const stale = [];
  for (const { key } of blobs) {
    const data = await store.get(key, { type: "json" }).catch(() => null);
    if (!data || !data.t || now - data.t > ONLINE_TTL_MS) { stale.push(key); continue; }
    const ip = data.ip || "unknown";
    const n = (byIp.get(ip) || 0) + 1;
    byIp.set(ip, n);
    if (n <= MAX_SESSIONS_PER_IP) total += 1;
  }
  // Opportunistic cleanup; never let it fail the request.
  await Promise.all(stale.slice(0, 50).map((k) => store.delete(k).catch(() => {})));
  return total;
}

async function registerPresence(store, sessionId, ipHash, now = Date.now()) {
  if (!sessionId || !SESSION_ID.test(sessionId)) return;
  await store.setJSON(`online:${sessionId}`, { t: now, ip: ipHash });
}

export default async function handler(request) {
  const store = getStore({ name: "site-traffic", consistency: "strong" });

  if (request.method === "GET" && new URL(request.url).searchParams.get("summary") === "1") {
    // Public aggregate only (counts, no identifiers) for the on-site
    // visit line. Unique IPs are unioned across today's hour buckets.
    const { day, hour } = jerusalemParts();
    const { blobs } = await store.list({ prefix: `h:${day}T` });
    let pv = 0, hourPv = 0;
    const ips = new Set(), hourIps = new Set(), pagePv = {};
    for (const { key } of blobs) {
      const data = await store.get(key, { type: "json" }).catch(() => null);
      if (!data) continue;
      const isHour = key === `h:${day}T${hour}`;
      pv += data.pv || 0;
      (data.ips || []).forEach((h) => ips.add(h));
      for (const [path, p] of Object.entries(data.pages || {})) pagePv[path] = (pagePv[path] || 0) + (p.pv || 0);
      if (isHour) { hourPv = data.pv || 0; (data.ips || []).forEach((h) => hourIps.add(h)); }
    }
    return Response.json({
      online: await countOnline(store).catch(() => null),
      today: { pageviews: pv, unique_ips: ips.size },
      hour: { pageviews: hourPv, unique_ips: hourIps.size },
      top_pages: Object.entries(pagePv).map(([path, p]) => ({ path, pageviews: p }))
        .sort((a, b) => b.pageviews - a.pageviews).slice(0, 5),
    }, { headers: { "Cache-Control": "public, max-age=30" } });
  }

  if (request.method === "GET") {
    const token = process.env.HIT_READ_TOKEN;
    if (!token || request.headers.get("x-hit-token") !== token) {
      return new Response("forbidden", { status: 403 });
    }
    const { blobs } = await store.list({ prefix: "h:" });
    const hours = [];
    for (const { key } of blobs.sort((a, b) => b.key.localeCompare(a.key))) {
      const data = await store.get(key, { type: "json" }).catch(() => null);
      if (!data) continue;
      hours.push({
        hour: key.slice(2),
        pageviews: data.pv || 0,
        unique_ips: (data.ips || []).length,
        pages: Object.entries(data.pages || {})
          .map(([path, p]) => ({ path, pageviews: p.pv || 0, unique_ips: (p.ips || []).length }))
          .sort((a, b) => b.pageviews - a.pageviews),
      });
    }
    return Response.json({ generated: new Date().toISOString(), hours });
  }

  if (request.method !== "POST") return new Response("method not allowed", { status: 405 });

  const ua = request.headers.get("user-agent") || "";
  if (!ua || BOT_UA.test(ua)) return new Response(null, { status: 204 });

  let page = "/", body = {};
  try {
    body = await request.json();
    if (typeof body.p === "string" && body.p.startsWith("/")) page = body.p.slice(0, 80);
  } catch { /* beacon without a body still counts as a hit on / */ }
  page = page.split(/[?#]/)[0] || "/";

  const ip = clientIp(request);
  const { day, hour } = jerusalemParts();
  const salt = process.env.HIT_SALT || "mandat121";
  const ipHash = ip ? createHash("sha256").update(`${ip}|${day}|${salt}`).digest("hex").slice(0, 16) : null;

  // Heartbeat: refresh presence only — not a pageview. Returns the live
  // count so the client needs no separate poll. Presence is keyed by a
  // random per-tab session id (sessionStorage, no cookie, no raw IP).
  // TODO(security): unauthenticated by design (public counter); abuse is
  // bounded by the bot-UA filter, session-id shape, and per-IP cap above
  // (CWE-770 — no hard rate limit is possible in a stateless function).
  if (body.hb === 1) {
    await registerPresence(store, body.s, ipHash).catch(() => {});
    const online = await countOnline(store).catch(() => null);
    return Response.json({ online }, { headers: { "Cache-Control": "no-store" } });
  }

  const key = `h:${day}T${hour}`;
  const existing = await store.get(key, { type: "json" }).catch(() => null);
  if (!existing) await pruneOldHours(store, day).catch(() => {}); // first hit of a new hour: sweep expired buckets (never block the hit on it)
  const data = existing || { pv: 0, pages: {}, ips: [] };
  data.pv += 1;
  const pg = data.pages[page] || (data.pages[page] = { pv: 0, ips: [] });
  pg.pv += 1;
  if (ipHash) {
    if (!data.ips.includes(ipHash)) data.ips.push(ipHash);
    if (!pg.ips.includes(ipHash)) pg.ips.push(ipHash);
  }
  await store.setJSON(key, data);
  await registerPresence(store, body.s, ipHash).catch(() => {});
  return new Response(null, { status: 204 });
}
