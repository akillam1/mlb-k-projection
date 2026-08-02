/* K Board — freshness strip + Refresh button.
 *
 * What this deliberately does NOT do any more: trigger a GitHub Actions run.
 * That needed a personal access token embedded in this (public) file; with the
 * token slot left empty the button just opened a GitHub page, which to anyone
 * not signed in is a dead end that looks broken. Runs are now fired on schedule
 * by a Cloudflare Worker (infra/cloudflare-worker/), so the button's only job is
 * to be honest about the data: check for a newer export, load it, and say how
 * old the current one is and when the next update lands.
 *
 * Per-page config on the <script> tag:
 *   data-src      JSON file whose generated_at describes THIS page (default
 *                 data/meta.json — the daily export). The Signals page is built
 *                 by the hourly job, so it points at its own file.
 *   data-cadence  "daily" (default) or "hourly" — which schedule to count down to.
 *
 * Slot times are UTC. Arizona is UTC-7 all year; everything is rendered in the
 * viewer's own timezone.
 */
(function () {
  'use strict';

  var cfg = document.currentScript ? document.currentScript.dataset : {};
  var SRC = cfg.src || 'data/meta.json';

  // Minutes past 00:00 UTC. Daily = the three real update slots (8:00 PM /
  // 8:00 AM / 3:00 PM Arizona). Hourly = the signals job, :25 past each hour it
  // runs (see .github/workflows/hourly.yml).
  var DAILY = [3 * 60, 15 * 60, 22 * 60];
  var HOURLY = [];
  [14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 0, 1, 2, 3, 4].forEach(function (h) {
    HOURLY.push(h * 60 + 25);
  });
  var SLOTS = (cfg.cadence === 'hourly' ? HOURLY : DAILY).slice().sort(function (a, b) {
    return a - b;
  });

  // A run takes ~10-25 min (dependency install, DB download, box scores, odds),
  // so "late" has to allow for the pipeline actually running.
  var LATE_MIN = cfg.cadence === 'hourly' ? 35 : 55;
  var POLL_MS = 5 * 60 * 1000;

  var header = document.querySelector('header.site');
  if (!header) return;

  var st = document.createElement('style');
  st.textContent =
    '.kb-strip{display:flex;align-items:center;gap:10px;margin-left:auto}' +
    '.kb-next{font-size:12px;color:var(--dim);white-space:nowrap}' +
    '.kb-refresh{font-size:12.5px;padding:6px 12px;border-radius:999px;' +
    'background:var(--card);border:1px solid var(--accent);color:var(--text);' +
    'cursor:pointer;font-family:inherit;white-space:nowrap}' +
    '.kb-refresh:disabled{opacity:.55;cursor:default}' +
    '.kb-refresh.new{border-color:var(--green);color:var(--green)}' +
    '.kb-refresh.warn{border-color:var(--gold);color:var(--gold)}';
  document.head.appendChild(st);

  var strip = document.createElement('div');
  strip.className = 'kb-strip';
  var note = document.createElement('span');
  note.className = 'kb-next';
  var btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'kb-refresh';
  btn.textContent = '⟳ Refresh';
  strip.appendChild(note);
  strip.appendChild(btn);
  header.appendChild(strip);

  // Date.UTC normalises out-of-range day/month values, so day-1 and day+1 walk
  // across month and year boundaries correctly.
  function slotAt(now, dayOffset, minutes) {
    return new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(),
      now.getUTCDate() + dayOffset, 0, minutes, 0));
  }

  function nextSlot(now) {
    var best = null;
    for (var d = 0; d <= 1; d++) {
      for (var i = 0; i < SLOTS.length; i++) {
        var t = slotAt(now, d, SLOTS[i]);
        if (t > now && (!best || t < best)) best = t;
      }
    }
    return best;
  }

  function prevSlot(now) {
    var best = null;
    for (var d = 0; d >= -1; d--) {
      for (var i = 0; i < SLOTS.length; i++) {
        var t = slotAt(now, d, SLOTS[i]);
        if (t <= now && (!best || t > best)) best = t;
      }
    }
    return best;
  }

  function clock(d) {
    return d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
  }

  function ago(ms) {
    var m = Math.round(ms / 60000);
    if (m < 1) return 'just now';
    if (m < 60) return m + ' min ago';
    var h = Math.floor(m / 60);
    return h + 'h ' + (m % 60) + 'm ago';
  }

  var loadedAt = null;   // generated_at of the export this page is DISPLAYING
  var pending = false;   // a newer export exists, waiting for a tap
  var failed = false;

  function poll() {
    return fetch(SRC + '?_=' + Date.now(), { cache: 'no-store' })
      .then(function (r) { return r.ok ? r.json() : null; })
      .catch(function () { return null; });
  }

  // Age is always measured from the data ON SCREEN, never from what we just
  // polled — otherwise the strip would claim "updated just now" next to a
  // button offering to load the update, which is the confusion this replaced.
  function render() {
    var now = new Date();
    var bits = [];
    var late = false;
    if (loadedAt) {
      bits.push('updated ' + ago(now - loadedAt));
      var due = prevSlot(now);
      if (!pending && now - due > LATE_MIN * 60000 && loadedAt < due) {
        bits.push('⚠ ' + clock(due) + ' update running late');
        late = true;
      }
    } else if (failed) {
      bits.push('offline');
    }
    bits.push('next ' + clock(nextSlot(now)));
    note.textContent = bits.join(' · ');
    if (!pending && !btn.disabled) btn.className = 'kb-refresh' + (late ? ' warn' : '');
  }

  function markPending() {
    pending = true;
    btn.disabled = false;
    btn.className = 'kb-refresh new';
    btn.textContent = '↓ New data — tap to load';
  }

  function check(userAsked) {
    return poll().then(function (m) {
      failed = !m || !m.generated_at;
      if (failed) { render(); if (userAsked) flash('✗ Could not reach data'); return; }
      var stamp = new Date(m.generated_at);
      if (!loadedAt) { loadedAt = stamp; render(); return; }
      if (stamp > loadedAt) {
        if (userAsked) { location.reload(); return; }
        markPending();
        render();
      } else {
        render();
        if (userAsked) flash('✓ Already the latest');
      }
    });
  }

  // Temporary message. Restores whatever state is CURRENT when the timer fires,
  // not what was current when it started — a poll may have found new data in
  // between, and silently discarding that would leave a button that says
  // "Refresh" but reloads the page.
  function flash(msg) {
    btn.textContent = msg;
    btn.disabled = true;
    setTimeout(function () {
      btn.disabled = false;
      if (pending) { markPending(); } else { btn.textContent = '⟳ Refresh'; render(); }
    }, 2200);
  }

  btn.addEventListener('click', function () {
    if (pending) { location.reload(); return; }
    btn.textContent = '⟳ Checking…';
    btn.disabled = true;
    check(true).then(function () {
      if (btn.textContent === '⟳ Checking…') {
        btn.disabled = false;
        btn.textContent = '⟳ Refresh';
        render();
      }
    });
  });

  check(false);
  setInterval(function () { check(false); }, POLL_MS);
  setInterval(render, 30000);            // tick the clock without refetching
  document.addEventListener('visibilitychange', function () {
    if (!document.hidden) check(false);
  });
})();
