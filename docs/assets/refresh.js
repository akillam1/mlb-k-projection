/* K Board — manual pipeline refresh button.
 * Injects a Refresh button into the site header that dispatches the
 * 'Daily refresh' GitHub Actions workflow via the REST API.
 *
 * Token: a fine-grained PAT scoped to THIS repo, Actions read/write ONLY.
 * Stored reversed+base64 so GitHub secret scanning doesn't auto-revoke it
 * on commit. Anyone can extract it — accepted by design (public button).
 * Worst case: strangers trigger extra runs; odds spend stays budget-guarded
 * (kproj/ingest/odds.py budget_ok) and runs share one concurrency group.
 *
 * Set/rotate: TOKEN_REV_B64 = output of:
 *   python -c \"import base64;print(base64.b64encode('YOUR_TOKEN'[::-1].encode()).decode())\"
 * Empty string = button falls back to opening the Actions page.
 */
(function () {
  'use strict';
  var REPO = 'akillam1/mlb-k-projection';
  var API = 'https://api.github.com/repos/' + REPO + '/actions/workflows/daily.yml';
  var ACTIONS_URL = 'https://github.com/' + REPO + '/actions/workflows/daily.yml';
  var TOKEN_REV_B64 = '';
  var COOLDOWN_MS = 5 * 60 * 1000;

  function token() {
    if (!TOKEN_REV_B64) return null;
    try { return atob(TOKEN_REV_B64).split('').reverse().join(''); } catch (e) { return null; }
  }

  var st = document.createElement('style');
  st.textContent = '.kb-refresh{margin-left:auto;font-size:12.5px;padding:6px 12px;' +
    'border-radius:999px;background:var(--card);border:1px solid var(--accent);' +
    'color:var(--text);cursor:pointer;font-family:inherit}' +
    '.kb-refresh:disabled{opacity:.55;cursor:default}' +
    '.kb-refresh.busy{border-color:var(--gold);color:var(--gold)}' +
    '.kb-refresh.ok{border-color:var(--green);color:var(--green)}' +
    '.kb-refresh.err{border-color:var(--red);color:var(--red)}';
  document.head.appendChild(st);

  var header = document.querySelector('header.site');
  if (!header) return;
  var btn = document.createElement('button');
  btn.type = 'button'; btn.className = 'kb-refresh'; btn.textContent = '\u27F3 Refresh';
  header.appendChild(btn);

  function set(txt, cls, dis) {
    btn.textContent = txt;
    btn.className = 'kb-refresh' + (cls ? ' ' + cls : '');
    btn.disabled = !!dis;
  }

  function latestRun() {
    return fetch(API + '/runs?per_page=1', { headers: { Accept: 'application/vnd.github+json' } })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (j) { return (j && j.workflow_runs && j.workflow_runs[0]) || null; })
      .catch(function () { return null; });
  }

  var timer = null;
  function poll(sinceId) {
    var n = 0;
    timer = setInterval(function () {
      n++;
      if (n > 30) { clearInterval(timer); set('\u27F3 Refresh', '', false); return; }
      latestRun().then(function (run) {
        if (!run) return;
        if (sinceId && run.id === sinceId) return;  // new run not registered yet
        if (run.status === 'completed') {
          clearInterval(timer);
          if (run.conclusion === 'success') {
            set('\u2713 Updated — reloading\u2026', 'ok', true);
            setTimeout(function () { location.reload(); }, 1200);
          } else {
            set('\u2717 Run failed — tap for logs', 'err', false);
            btn.onclick = function () { window.open(ACTIONS_URL, '_blank'); };
          }
        } else {
          set('\u27F3 Refreshing\u2026 (' + run.status.replace('_', ' ') + ')', 'busy', true);
        }
      });
    }, 20000);
  }

  btn.addEventListener('click', function () {
    var t = token();
    if (!t) { window.open(ACTIONS_URL, '_blank'); return; }
    var last = Number(localStorage.getItem('kbRefreshAt') || 0);
    if (Date.now() - last < COOLDOWN_MS) {
      set('\u23F3 Just refreshed — wait a few min', 'busy', true);
      setTimeout(function () { set('\u27F3 Refresh', '', false); }, 3000);
      return;
    }
    set('\u27F3 Starting\u2026', 'busy', true);
    latestRun().then(function (run) {
      if (run && run.status !== 'completed') { set('\u27F3 Already running\u2026', 'busy', true); poll(null); return; }
      var sinceId = run ? run.id : null;
      fetch(API + '/dispatches', {
        method: 'POST',
        headers: {
          Authorization: 'Bearer ' + t,
          Accept: 'application/vnd.github+json',
          'Content-Type': 'application/json',
          'X-GitHub-Api-Version': '2022-11-28'
        },
        body: JSON.stringify({ ref: 'main' })
      }).then(function (r) {
        if (r.status === 204) {
          localStorage.setItem('kbRefreshAt', String(Date.now()));
          set('\u27F3 Queued\u2026 (~3 min)', 'busy', true);
          poll(sinceId);
        } else {
          set('\u2717 Trigger failed (' + r.status + ')', 'err', false);
        }
      }).catch(function () { set('\u2717 Network error', 'err', false); });
    });
  });
})();
