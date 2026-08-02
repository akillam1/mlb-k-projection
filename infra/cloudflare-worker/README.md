# On-time scheduler (Cloudflare Worker)

GitHub's hosted cron is **best-effort**. On this repo, `10 4 * * *` regularly
started between 05:20 and 07:40 UTC — 1 to 3.5 hours late — which is why the
board seemed to update at a different (and later) time every night. No GitHub
setting fixes that; the queue is shared and scheduled runs are the first thing
deprioritised.

This Worker fires the three real update slots on the minute, for free, by
calling `workflow_dispatch`. The crons still in `daily.yml` are a backstop for
the day this Worker breaks; a backstop run that finds fresh data exits in
seconds.

| Cron (UTC) | Arizona | What the run does |
|---|---|---|
| `0 3 * * *`  | 8:00 PM | Settle tonight, roll the board to tomorrow |
| `0 15 * * *` | 8:00 AM | Game lines + K props, settle late finals |
| `0 22 * * *` | 3:00 PM | Pre-slate refresh + line-movement re-pull |

Arizona does not observe DST, so these never need seasonal adjustment.
Cloudflare's free plan allows three cron triggers per Worker — exactly the
three slots, which is why the bonus midday run stays on a GitHub cron.

## Setup (about 10 minutes, all in a browser)

**1. Make a token.** GitHub → Settings → Developer settings → Personal access
tokens → **Fine-grained tokens** → Generate new token.

- Resource owner: your account
- Repository access: **Only select repositories** → `mlb-k-projection`
- Repository permissions: **Actions → Read and write** (Metadata read-only is
  added automatically). Nothing else. This token cannot push code.
- Expiration: the longest option offered. Note the date — when it expires the
  Worker goes quiet and the GitHub backstop crons take over, so the site keeps
  working with sloppy timing rather than breaking.

Copy the token; GitHub shows it once.

**2. Create the Worker.** [dash.cloudflare.com](https://dash.cloudflare.com) →
free account → **Workers & Pages** → **Create** → **Start with Hello World** →
name it `kboard-scheduler` → Deploy → **Edit code** → replace everything with
the contents of [`worker.js`](worker.js) → **Deploy**.

**3. Add the settings.** Worker → **Settings** → **Variables and Secrets**:

| Name | Type | Value |
|---|---|---|
| `GH_REPO` | Text | `akillam1/mlb-k-projection` |
| `GH_TOKEN` | **Secret** | the token from step 1 |
| `PING_KEY` | **Secret** | any random string you make up |

Secrets are write-only — nobody can read them back out of the dashboard, which
is the whole reason the token lives here instead of in the site's JavaScript.

**4. Add the schedule.** Worker → **Settings** → **Triggers** → **Cron
Triggers** → add `0 3 * * *`, `0 15 * * *`, `0 22 * * *`.

**5. Test it.** Open `https://kboard-scheduler.<your-subdomain>.workers.dev/`
— it should list the three slots and `token_configured: true`. Then hit
`…/run?key=<your PING_KEY>`; within a few seconds a run named
**"Daily refresh — manual (worker /run)"** appears in the repo's Actions tab.

## Checking it later

- The run name in Actions tells you what fired it: `8:00 PM AZ · roll to
  tomorrow` came from the Worker, `GitHub cron backstop` did not.
- Worker → **Logs** shows each dispatch and GitHub's response code.
- A `401` means the token expired or was revoked — redo step 1 and update the
  `GH_TOKEN` secret. Nothing else changes.

## Deploying from a terminal instead

`wrangler.toml` is included if you ever want it:

```
npx wrangler secret put GH_TOKEN
npx wrangler secret put PING_KEY
npx wrangler deploy
```
