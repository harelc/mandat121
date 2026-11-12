// Knesset-26 presence polling — the Netlify home of build_presence.py.
//
// Why here and not GitHub Actions: every GH poll that added an arrival
// was a git commit, and every commit triggers a Netlify deploy — a busy
// morning meant dozens of deploys for a one-line data change. Presence
// is the site's only high-frequency writer, so it alone moves to
// Netlify: this scheduled function polls the MK-lobby API every 5 min
// and keeps the whole history in one Netlify Blobs object ("presence"
// store, key "state", same shape as the retired data/k26_presence.json).
// netlify/functions/presence.mjs serves it to the client (CDN-cached
// 60s). Nothing here commits anything; deploys go back to being driven
// by the once-daily data build and code pushes.
//
// The lobby API reports only IsPresent *right now* — no timestamps, no
// history — so arrival order exists only because somebody polls.
// first_seen is therefore the first poll that saw the MK, never a
// turnstile time, and the UI labels it that way. Nothing is backfilled:
// tracking starts the first day this runs (seeded once, below, from the
// GH-era committed file so the history built since 2026-11-12 carries
// over), and at_first_poll marks MKs already present when a day's
// polling opened. K25 is never touched — the lobby feed serves only
// the current roster.
//
// Merge semantics are a direct port of build_presence.py (whose git
// history holds the original); tests/test_presence_merge.mjs pins them.
// @netlify/blobs is imported lazily so the pure merge below stays
// importable by plain node in CI (no node_modules there).

const LOBBY_URL = "https://www.knesset.gov.il/WebSiteApi/knessetapi/MkLobby/GetMkLobbyData120?lang=he";
const UA = { Accept: "application/json", "User-Agent": "Mozilla/5.0 mandat121-functions" };

// Asia/Jerusalem wall-clock parts for an instant, via Intl (the runtime
// has full ICU; no tz database code of our own — CWE-20-adjacent hand
// -rolled date math is exactly what this avoids).
const IL_FMT = new Intl.DateTimeFormat("en-CA", {
  timeZone: "Asia/Jerusalem", year: "numeric", month: "2-digit", day: "2-digit",
  hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23", weekday: "short",
});
const IL_WEEKDAYS = { Sun: 0, Mon: 1, Tue: 2, Wed: 3, Thu: 4, Fri: 5, Sat: 6 };
const WORKDAYS = new Set([0, 1, 2, 3, 4]); // Sun–Thu

export function jerusalemParts(at) {
  const p = {};
  for (const part of IL_FMT.formatToParts(at)) p[part.type] = part.value;
  const hour = p.hour === "24" ? "00" : p.hour;
  // UTC offset of Asia/Jerusalem at this instant, in minutes: reinterpret
  // the Jerusalem wall time as UTC and diff against the real instant.
  const wallAsUtc = Date.UTC(+p.year, +p.month - 1, +p.day, +hour, +p.minute, +p.second);
  const offsetMin = Math.round((wallAsUtc - Math.floor(at.getTime() / 1000) * 1000) / 60000);
  const sign = offsetMin < 0 ? "-" : "+";
  const abs = Math.abs(offsetMin);
  const offset = `${sign}${String(Math.floor(abs / 60)).padStart(2, "0")}:${String(abs % 60).padStart(2, "0")}`;
  const date = `${p.year}-${p.month}-${p.day}`;
  return { date, weekday: IL_WEEKDAYS[p.weekday], iso: `${date}T${hour}:${p.minute}:${p.second}${offset}` };
}

export function deriveByMk(days) {
  // Per-MK workday counts, recomputed from `days` on every write so the
  // histogram can never drift from the arrival lists it summarises.
  const byMk = {};
  for (const date of Object.keys(days).sort()) {
    for (const a of days[date].arrivals || []) {
      const e = (byMk[a.name] ??= { mk_id: a.mk_id ?? null, days: 0, dates: [] });
      if (a.mk_id) e.mk_id = a.mk_id;
      e.days += 1;
      e.dates.push(date);
    }
  }
  return byMk;
}

export function emptyState() {
  return { _meta: {}, days: {}, by_mk: {} };
}

export function mergePoll(state, mks, at, force = false) {
  // Merge one lobby reading. `mks` is the raw payload list, `at` a Date.
  // Returns { state, added } — added = names first seen in this poll.
  // Mutates state. Non-workdays (Asia/Jerusalem) merge nothing unless
  // force (manual checks only).
  const local = jerusalemParts(at);
  if (!force && !WORKDAYS.has(local.weekday)) return { state, added: [] };
  const days = (state.days ??= {});
  let day = days[local.date];
  const firstPollOfDay = !day;
  if (firstPollOfDay) day = days[local.date] = { first_poll: local.iso, last_poll: local.iso, arrivals: [] };
  const known = new Set(day.arrivals.map((a) => a.name));
  const added = [];
  for (const m of mks || []) {
    if (!m || !m.IsPresent) continue;
    const name = `${m.Lastname || ""} ${m.Firstname || ""}`.split(/\s+/).filter(Boolean).join(" ");
    if (!name || known.has(name)) continue;
    known.add(name);
    day.arrivals.push({ name, mk_id: m.MkId ?? null, first_seen: local.iso, at_first_poll: firstPollOfDay });
    added.push(name);
  }
  if (added.length || firstPollOfDay) {
    day.last_poll = local.iso;
    day.arrivals.sort((a, b) => (a.first_seen < b.first_seen ? -1 : a.first_seen > b.first_seen ? 1 : a.name < b.name ? -1 : a.name > b.name ? 1 : 0));
  }
  state.by_mk = deriveByMk(days);
  state._meta = {
    source: LOBBY_URL,
    knesset: 26,
    tracking_since: Object.keys(days).sort()[0] ?? null,
    updated_at: day.last_poll,
    workdays_tracked: Object.keys(days).length,
    note: "first_seen = first poll (Sun-Thu, Asia/Jerusalem) that saw the MK present in the lobby feed; not a turnstile time. No history exists before tracking_since.",
  };
  return { state, added };
}

async function seedState() {
  // One-time carry-over: the GH-era history lives in the deployed
  // data/k26_presence.json. First run with an empty blob adopts it so
  // tracking_since and past days survive the migration untouched.
  try {
    const base = process.env.URL || process.env.DEPLOY_URL;
    if (!base) return emptyState();
    const r = await fetch(new URL("/data/k26_presence.json", base), { headers: UA });
    if (!r.ok) return emptyState();
    const j = await r.json();
    return j && typeof j === "object" && j.days && typeof j.days === "object" ? j : emptyState();
  } catch {
    return emptyState();
  }
}

export default async function handler() {
  const { getStore } = await import("@netlify/blobs");
  const store = getStore({ name: "presence", consistency: "strong" });

  let mks;
  try {
    const r = await fetch(LOBBY_URL, { headers: UA });
    if (!r.ok) throw new Error(`lobby HTTP ${r.status}`);
    mks = (await r.json())?.mks;
  } catch (e) {
    console.error("presence-poll: lobby fetch failed:", e.message);
    return Response.json({ ok: false, error: "lobby fetch failed" }, { status: 502 });
  }
  if (!Array.isArray(mks) || !mks.length) {
    console.error("presence-poll: empty lobby payload — keeping previous state");
    return Response.json({ ok: false, error: "empty lobby payload" }, { status: 502 });
  }

  let state = (await store.get("state", { type: "json" })) || (await seedState());
  const before = JSON.stringify(state.days || {});
  const { added } = mergePoll(state, mks, new Date());
  if (JSON.stringify(state.days || {}) === before) {
    console.log("presence-poll: unchanged — no new arrivals");
    return Response.json({ ok: true, changed: false, added: 0 });
  }
  await store.setJSON("state", state);
  const today = jerusalemParts(new Date()).date;
  console.log(`presence-poll: ${today}: ${added.length} new arrival(s), ${state.days[today]?.arrivals.length ?? 0} so far today, ${state._meta.workdays_tracked} workday(s) tracked`);
  return Response.json({ ok: true, changed: true, added: added.length });
}

export const config = { schedule: "*/5 4-18 * * 0-4" }; // UTC; the Sun–Thu Jerusalem gate is in mergePoll, like build_presence.py
