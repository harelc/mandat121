import { createHash } from "node:crypto";
import { getStore } from "@netlify/blobs";

// Server-side follow ("star") counts: how many distinct visitors follow
// each bill / MK / committee / faction / topic. The visitor's own follow
// list stays in their browser (localStorage) — this store only keeps, per
// entity, the set of hashed anonymous visitor ids that follow it, so the
// site can show "★ 23 עוקבים" next to a follow button. No raw ids, no
// IPs, no cookies: uid is a random localStorage token, stored only as
// sha256(uid | HIT_SALT) truncated. Counts are public aggregates.
//
// GET  -> { counts: { "mk:<name>": 12, "bill:<id>": 3, ... } }
// POST { kind, key, uid, on } -> { count } for that entity after the toggle.
// TODO(security): unauthenticated by design (public counter); a visitor
// can mint many uids and inflate a count (CWE-770 — no hard rate limit is
// possible in a stateless function). Bot-UA filter + id shape checks bound
// the cheapest abuse; counts are social proof, not a security signal.

const KINDS = new Set(["bill", "mk", "committee", "faction", "topic"]);
const BOT_UA = /bot|crawler|spider|headless|preview|monitor/i;

export default async function handler(request) {
  const store = getStore({ name: "star-counts", consistency: "strong" });

  if (request.method === "GET") {
    const { blobs } = await store.list({ prefix: "s:" });
    const counts = {};
    for (const { key } of blobs) {
      const data = await store.get(key, { type: "json" }).catch(() => null);
      if (data && Array.isArray(data.u) && data.u.length) counts[key.slice(2)] = data.u.length;
    }
    return Response.json({ counts }, { headers: { "Cache-Control": "public, max-age=30" } });
  }

  if (request.method !== "POST") return new Response("method not allowed", { status: 405 });
  const ua = request.headers.get("user-agent") || "";
  if (!ua || BOT_UA.test(ua)) return new Response(null, { status: 204 });

  let body = {};
  try { body = await request.json(); } catch { return new Response("bad body", { status: 400 }); }
  const { kind, key, uid, on } = body || {};
  if (!KINDS.has(kind) || typeof key !== "string" || !key || key.length > 120 ||
      typeof uid !== "string" || !/^[A-Za-z0-9_-]{16,64}$/.test(uid) ||
      typeof on !== "boolean") {
    return new Response("bad request", { status: 400 });
  }
  const salt = process.env.HIT_SALT || "mandat121";
  const uidHash = createHash("sha256").update(`${uid}|stars|${salt}`).digest("hex").slice(0, 16);
  const storeKey = `s:${kind}:${key}`;
  const data = (await store.get(storeKey, { type: "json" }).catch(() => null)) || { u: [] };
  const i = data.u.indexOf(uidHash);
  const changed = (on && i < 0) || (!on && i >= 0);
  if (on && i < 0) data.u.push(uidHash);
  if (!on && i >= 0) data.u.splice(i, 1);
  if (data.u.length) await store.setJSON(storeKey, data);
  else await store.delete(storeKey).catch(() => {});
  // Admin audit log (read only via netlify/functions/admin.mjs, token-gated):
  // one entry per actual state change, per Jerusalem day, capped.
  if (changed) {
    try {
      const logStore = getStore({ name: "admin-log", consistency: "strong" });
      const day = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Jerusalem", year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date());
      const lk = `star:${day}`;
      const log = (await logStore.get(lk, { type: "json" }).catch(() => null)) || [];
      log.push({ t: new Date().toISOString(), kind, key, action: on ? "follow" : "unfollow", count: data.u.length, uid: uidHash.slice(0, 8) });
      await logStore.setJSON(lk, log.slice(-500));
    } catch { /* logging must never break the toggle */ }
  }
  return Response.json({ count: data.u.length }, { headers: { "Cache-Control": "no-store" } });
}
