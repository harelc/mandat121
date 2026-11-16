import { createHash, randomBytes, timingSafeEqual } from "node:crypto";
import { getStore } from "@netlify/blobs";

// Feedback / wishlist / testimonial corner.
//   POST {type,text,name?,consent?,uid?,hp?}      -> submit (always "pending")
//   GET                                            -> approved testimonials only (public wall)
//   GET  ?data=1  + header x-hit-token             -> all submissions (owner, token-gated)
//   POST {action:"approve"|"reject", id} + token   -> moderate one submission
//
// Privacy (mirrors hit.mjs / stars.mjs): no accounts, no cookies, no raw
// IPs stored. The optional visitor uid is stored only as a salted hash.
// Testimonials are NEVER public on submission: they appear on the wall
// only after (a) the submitter ticked consent and (b) the owner approved
// the item in /admin. All other types are private to the owner forever.
// TODO(security): unauthenticated by design (public form); a visitor can
// mint many uids / submit repeatedly (CWE-770 — no hard rate limit is
// possible in a stateless function). Bot-UA filter, honeypot, and strict
// length caps bound the cheapest abuse; moderation is the real gate for
// anything public. Renderers must escape text (CWE-79 stored XSS) — the
// client does so via esc() and this function only ever returns plain text.

export const FEEDBACK_TYPES = new Set(["bug", "idea", "correction", "testimonial"]);
const BOT_UA = /bot|crawler|spider|headless|preview|monitor/i;
const ID_RE = /^[A-Za-z0-9_-]{8,80}$/;
export const LIMITS = { text: 800, name: 60 };

const clean = (v, max) => String(v ?? "").replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/g, "").trim().slice(0, max);

export function validateSubmission(body) {
  if (!body || typeof body !== "object") return { ok: false, error: "bad body" };
  if (body.hp) return { ok: false, error: "honeypot" }; // silent-drop field: humans never fill it
  const type = clean(body.type, 20);
  const text = clean(body.text, LIMITS.text);
  const name = clean(body.name, LIMITS.name);
  if (!FEEDBACK_TYPES.has(type)) return { ok: false, error: "bad type" };
  if (text.length < 3) return { ok: false, error: "text too short" };
  const consent = body.consent === true;
  // A testimonial without a publishable name still counts as private praise.
  return { ok: true, value: { type, text, name, consent } };
}

function authorized(request) {
  const token = process.env.HIT_READ_TOKEN;
  if (!token) return false;
  const given = request.headers.get("x-hit-token") || "";
  const a = Buffer.from(given), b = Buffer.from(token);
  return a.length === b.length && timingSafeEqual(a, b);
}

async function listAll(store) {
  const { blobs } = await store.list({ prefix: "f:" });
  const items = [];
  for (const { key } of blobs) {
    const d = await store.get(key, { type: "json" }).catch(() => null);
    if (d && d.id) items.push(d);
  }
  return items.sort((a, b) => String(b.ts || "").localeCompare(String(a.ts || "")));
}

export default async function handler(request) {
  const store = getStore({ name: "feedback", consistency: "strong" });
  const url = new URL(request.url);

  if (request.method === "GET" && url.searchParams.get("data") === "1") {
    if (!authorized(request)) return new Response("forbidden", { status: 403, headers: { "Cache-Control": "no-store" } });
    const items = await listAll(store);
    return Response.json({ generated: new Date().toISOString(), items: items.slice(0, 200) },
      { headers: { "Cache-Control": "no-store" } });
  }

  if (request.method === "GET") {
    const items = await listAll(store);
    const wall = items
      .filter((d) => d.status === "approved" && d.type === "testimonial" && d.consent)
      .slice(0, 50)
      .map((d) => ({ id: d.id, text: d.text, name: d.name || "מבקר/ת באתר", ts: d.ts }));
    return Response.json({ items: wall }, { headers: { "Cache-Control": "public, max-age=60" } });
  }

  if (request.method !== "POST") return new Response("method not allowed", { status: 405 });

  let body = {};
  try { body = await request.json(); } catch { return new Response("bad body", { status: 400 }); }

  // Moderation (owner only, same token as /admin).
  if (body && body.action) {
    if (!authorized(request)) return new Response("forbidden", { status: 403, headers: { "Cache-Control": "no-store" } });
    const { action, id } = body;
    if (!["approve", "reject", "pending"].includes(action) || typeof id !== "string" || !ID_RE.test(id)) {
      return new Response("bad request", { status: 400 });
    }
    const key = `f:${id}`;
    const d = await store.get(key, { type: "json" }).catch(() => null);
    if (!d) return new Response("not found", { status: 404 });
    d.status = action === "approve" ? "approved" : action === "reject" ? "rejected" : "pending";
    await store.setJSON(key, d);
    return Response.json({ id, status: d.status }, { headers: { "Cache-Control": "no-store" } });
  }

  const ua = request.headers.get("user-agent") || "";
  if (!ua || BOT_UA.test(ua)) return new Response(null, { status: 204 });

  const v = validateSubmission(body);
  if (!v.ok) {
    // Honeypot hits get a fake success so bots learn nothing.
    if (v.error === "honeypot") return Response.json({ ok: true });
    return new Response(v.error, { status: 400 });
  }
  const salt = process.env.HIT_SALT || "mandat121";
  const uidHash = typeof body.uid === "string" && /^[A-Za-z0-9_-]{16,64}$/.test(body.uid)
    ? createHash("sha256").update(`${body.uid}|feedback|${salt}`).digest("hex").slice(0, 16) : null;
  const id = `${Date.now().toString(36)}${randomBytes(6).toString("hex")}`;
  const item = { id, ts: new Date().toISOString(), status: "pending", uid: uidHash, ...v.value };
  await store.setJSON(`f:${id}`, item);
  return Response.json({ ok: true, id }, { headers: { "Cache-Control": "no-store" } });
}
