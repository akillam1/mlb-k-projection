/**
 * K Board — on-time trigger for the "Daily refresh" workflow.
 *
 * Why this exists: GitHub's hosted cron is best-effort. On this repo scheduled
 * runs have started 1h-3.5h after their cron time, so the board updated at
 * random hours. Cloudflare's cron triggers fire on the minute, for free, so
 * the schedule lives here and GitHub's own crons are demoted to a backstop.
 *
 * Deploy: see README.md in this folder. Three secrets/vars, no build step.
 *   GH_TOKEN  (secret) fine-grained PAT — this repo only, Actions: Read+Write
 *   GH_REPO   (var)    e.g. akillam1/mlb-k-projection
 *   PING_KEY  (secret, optional) lets you fire a run by URL for testing
 *
 * Cloudflare cron triggers are always UTC. Arizona is UTC-7 year round.
 */

const SLOTS = {
  '0 3 * * *':  '8:00 PM AZ · roll to tomorrow',
  '0 15 * * *': '8:00 AM AZ · lines + settle',
  '0 22 * * *': '3:00 PM AZ · pre-slate',
};

async function dispatch(env, slot) {
  const repo = env.GH_REPO;
  if (!repo || !env.GH_TOKEN) {
    console.log(`[kboard] ${slot} -> not configured (GH_REPO/GH_TOKEN missing)`);
    return { ok: false, status: 0, body: 'worker not configured', slot };
  }
  const url = `https://api.github.com/repos/${repo}/actions/workflows/daily.yml/dispatches`;
  let res;
  try {
    res = await fetch(url, {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${env.GH_TOKEN}`,
        Accept: 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28',
        'Content-Type': 'application/json',
        // GitHub rejects API calls without a User-Agent.
        'User-Agent': 'kboard-scheduler',
      },
    // workflow_dispatch input values must be strings, including booleans.
      body: JSON.stringify({ ref: 'main', inputs: { slot, odds_mode: 'auto', force: 'true' } }),
    });
  } catch (err) {
    // A throw here is a network-level failure — the exact case the retry below
    // exists for. Report it as a status so it doesn't escape waitUntil unhandled.
    console.log(`[kboard] ${slot} -> fetch threw: ${err}`);
    return { ok: false, status: 599, body: String(err), slot };
  }
  const body = res.status === 204 ? '' : await res.text();
  console.log(`[kboard] ${slot} -> ${res.status} ${body}`);
  return { ok: res.status === 204, status: res.status, body, slot };
}

// One retry after a short pause: a blip shouldn't cost a whole slot. 4xx is not
// retried — an expired token will not fix itself in 20 seconds.
async function dispatchWithRetry(env, slot) {
  let r = await dispatch(env, slot);
  if (!r.ok && r.status >= 500) {
    await new Promise((s) => setTimeout(s, 20000));
    r = await dispatch(env, `${slot} (retry)`);
  }
  return r;
}

export default {
  async scheduled(event, env, ctx) {
    const slot = SLOTS[event.cron] || `cron ${event.cron}`;
    ctx.waitUntil(dispatchWithRetry(env, slot));
  },

  // Health check + manual fire. GET / shows the schedule; GET /run?key=PING_KEY
  // triggers a refresh (handy for testing the token without waiting for 8 PM).
  async fetch(req, env) {
    const u = new URL(req.url);
    if (u.pathname === '/run') {
      if (!env.PING_KEY || u.searchParams.get('key') !== env.PING_KEY) {
        return new Response('nope', { status: 403 });
      }
      const r = await dispatchWithRetry(env, 'manual (worker /run)');
      return Response.json(r, { status: r.ok ? 200 : 502 });
    }
    return Response.json({
      service: 'kboard scheduler',
      repo: env.GH_REPO || null,
      token_configured: Boolean(env.GH_TOKEN),
      slots: SLOTS,
      now_utc: new Date().toISOString(),
    });
  },
};
