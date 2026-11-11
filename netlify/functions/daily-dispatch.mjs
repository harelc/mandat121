// Reliable trigger for the daily Knesset data build.
//
// Why this exists: GitHub's own cron (schedule: 30 4 * * * in
// knesset-daily.yml) is best-effort — on 2026-11-10 the scheduled run
// started ~7 hours late, and on 2026-11-11 it had not fired at all by
// 06:06 UTC. Netlify's scheduler is the reliable clock (same pattern as
// elections-vote-transfer's k26-dispatch), so this function dispatches
// the workflow at 04:30, 05:30 and 06:30 UTC and lets the runner do all
// the work. Before dispatching it checks for an already active run —
// the workflow's concurrency group would serialize a duplicate anyway,
// but skipping here avoids queueing a second full run.
//
// Setup (one-time, in the Netlify site env vars — never in the repo,
// CWE-798): GITHUB_DISPATCH_TOKEN = a fine-grained GitHub token with
// Actions: write on harelc/mandat121. Without it the function holds
// (logs and returns) and the GitHub cron remains the fallback trigger.
//
// Invocation guard: Netlify scheduled invocations arrive as a POST
// whose JSON body carries `next_run`. A plain HTTP hit on the function
// URL has no `next_run`, so manual triggering additionally requires
// the x-dispatch-secret header to match DISPATCH_SECRET (also env-only).

const REPO = "harelc/mandat121";
const WORKFLOW = "knesset-daily.yml";
const API = "https://api.github.com";

export const config = { schedule: "30 4,5,6 * * *" };

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
  const token = env.GITHUB_DISPATCH_TOKEN;
  if (!token) return { ok: false, action: "hold", reason: "GITHUB_DISPATCH_TOKEN unset" };

  // Never queue a duplicate: a run from the GitHub cron (or a previous
  // dispatch in this schedule) may already be going.
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
    body: JSON.stringify({ ref: "main", inputs: { target: "all" } }),
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
