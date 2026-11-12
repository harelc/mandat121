// Presence history, read side: serves the Blobs state written by
// presence-poll.mjs (same shape the client always consumed from
// data/k26_presence.json: {_meta, days, by_mk}). CDN-cached for 60s —
// the poll cadence is 5 minutes, so edges absorb nearly all reads and
// this function runs a handful of times an hour. If the blob is empty
// (first minutes after deploy, before the first poll seeds it) this
// returns an empty state with status 200 and the client falls back to
// the committed data/k26_presence.json, which holds the GH-era history
// and stays in the repo as the frozen seed.
import { getStore } from "@netlify/blobs";

const EMPTY = { _meta: {}, days: {}, by_mk: {} };
const HEADERS = {
  "Content-Type": "application/json; charset=utf-8",
  "Cache-Control": "public, max-age=30",
  "Netlify-CDN-Cache-Control": "public, max-age=60, stale-while-revalidate=300",
};

export default async function handler() {
  try {
    const store = getStore({ name: "presence", consistency: "strong" });
    const state = await store.get("state", { type: "json" });
    return new Response(JSON.stringify(state || EMPTY), { headers: HEADERS });
  } catch (e) {
    console.error("presence: blob read failed:", e.message);
    return new Response(JSON.stringify(EMPTY), { headers: HEADERS });
  }
}
