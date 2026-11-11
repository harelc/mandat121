// Reliable trigger for the daily Knesset data build.
//
// Why this exists: GitHub's own cron (schedule: 30 4 * * * in
// knesset-daily.yml) is best-effort — on 2026-11-10 the scheduled run
// started ~7 hours late, and on 2026-11-11 it had not fired at all by
// 06:06 UTC. Netlify's scheduler is the reliable clock (same pattern as
// elections-vote-transfer's k26-dispatch), so this function dispatches
// the workflow every 8 hours (04:30, 12:30, 20:30 UTC) and lets the
// runner do all the work. (Cadence since then: hourly during Israel
// work hours, every 4 hours outside them — see shouldRun below.)
// Before dispatching it checks for an already active run —
// the workflow's concurrency group would serialize a duplicate anyway,
// but skipping here avoids queueing a second full run.
//
// Setup (one-time, in the Netlify site env vars — never in the repo,
// CWE-798): GITHUB_DISPATCH_TOKEN = a fine-grained GitHub token with
// Actions: write on harelc/mandat121. Without it the function holds
// (logs and returns) and no automatic runs happen at all — there is
// deliberately no GitHub schedule cron (it was unreliable, and a cron
// fire on top of this dispatch queues a duplicate full build).
//
// Invocation guard: Netlify scheduled invocations arrive as a POST
// whose JSON body carries `next_run`. A plain HTTP hit on the function
// URL has no `next_run`, so manual triggering additionally requires
// the x-dispatch-secret header to match DISPATCH_SECRET (also env-only).

const REPO = "harelc/mandat121";
const WORKFLOW = "knesset-daily.yml";
const API = "https://api.github.com";

// One cron only (Netlify limit), firing hourly; shouldRun() below
// applies the real cadence in Israel time, so DST never shifts it.
export const config = { schedule: "30 * * * *" };

const IL_DAYS = { Sun: 0, Mon: 1, Tue: 2, Wed: 3, Thu: 4, Fri: 5, Sat: 6 };
export function shouldRun(now = new Date()) {
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat("en-US", {
      timeZone: "Asia/Jerusalem", weekday: "short", hour: "numeric", hourCycle: "h23",
    }).formatToParts(now).map(p => [p.type, p.value]));
  const day = IL_DAYS[parts.weekday], hour = Number(parts.hour);
  const workTime = day <= 4 && hour >= 8 && hour < 20; // Sun–Thu, 08:00–19:59 Israel
  return workTime || hour % 4 === 0; // outside work hours: every 4 hours
}

async function gh(token, path, init = {}) {
  return fetch(`${API}${path}`, {
    ...init,
    headers: {
      Authorization: `Bearer ${token}`,
      Accept: "application/vnd.github+json",
      "User-Agent": "mandat121-daily-dispatch",
      "X-GitHub-Api-Version": "2022-11-28",
      ...(init.headers || {}),
    },
  });
}

export async function dispatch(env = process.env, scheduled = true) {
  if (env.DAILY_DISPATCH === "0") return { ok: true, action: "skip", reason: "DAILY_DISPATCH=0" };
  if (scheduled && !shouldRun()) return { ok: true, action: "skip", reason: "outside cadence (hourly Sun–Thu 08:00–20:00 Israel, else every 4h)" };
  const token = env.GITHUB_DISPATCH_TOKEN;
  if (!token) return { ok: false, action: "hold", reason: "GITHUB_DISPATCH_TOKEN unset" };

  // Never queue a duplicate: a manual dispatch (or a previous dispatch
  // in this schedule) may already be going.
  for (const status of ["in_progress", "queued"]) {
    const r = await gh(token, `/repos/${REPO}/actions/workflows/${WORKFLOW}/runs?status=${status}&per_page=1`);
    if (r.ok) {
      const body = await r.json();
      if ((body.workflow_runs || []).length) {
        return { ok: true, action: "skip", reason: `a run is already ${status}` };
      }
    }
  }

  const response = await gh(token, `/repos/${REPO}/actions/workflows/${WORKFLOW}/dispatches`, {
    method: "POST",
    // K26 only for now: K25 is a closed era whose layers are final
    // (and Sundays still refresh them via the workflow's own gates
    // whenever a human dispatches target=all).
    body: JSON.stringify({ ref: "main", inputs: { target: "k26-only" } }),
  });
  if (response.status !== 204) {
    return { ok: false, action: "hold", reason: `github dispatch ${response.status}` };
  }
  return { ok: true, action: "dispatch", scheduled };
}

export default async function handler(request) {
  let scheduled = false;
  try {
    const body = await request.clone().json();
    scheduled = Boolean(body && body.next_run);
  } catch { /* no JSON body -> not a scheduled invocation */ }
  if (!scheduled) {
    const secret = process.env.DISPATCH_SECRET;
    if (!secret || request.headers.get("x-dispatch-secret") !== secret) {
      return Response.json({ ok: false, action: "hold", reason: "manual dispatch requires x-dispatch-secret" }, { status: 403 });
    }
  }
  const result = await dispatch(process.env, scheduled);
  console.log("daily-dispatch:", JSON.stringify(result));
  return Response.json(result, { status: result.ok ? 200 : 500 });
}
