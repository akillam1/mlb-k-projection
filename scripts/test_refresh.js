/* Offline test for docs/assets/refresh.js — no browser, no network, no deps.
 * Run: node scripts/test_refresh.js
 *
 * Loads the real shipped file under a stub DOM with a frozen clock and a fake
 * fetch, then asserts on what the header strip and button actually say. The
 * clock is pinned so these cases cannot rot (see the same lesson in
 * scripts/test_signals.py).
 */
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const SRC = path.join(__dirname, '..', 'docs', 'assets', 'refresh.js');
const RealDate = Date;
let fails = 0;

function ok(cond, label, extra) {
  if (cond) { console.log('  ok   ' + label); }
  else { fails++; console.log('  FAIL ' + label + (extra ? '  <- ' + extra : '')); }
}

function harness({ now, cadence, src, payload }) {
  let NOW = new RealDate(now);
  class FakeDate extends RealDate {
    constructor(...a) { if (a.length === 0) super(NOW.getTime()); else super(...a); }
    static now() { return NOW.getTime(); }
  }
  const el = () => ({
    style: {}, dataset: {}, className: '', textContent: '', disabled: false,
    type: '', children: [], appendChild(c) { this.children.push(c); },
    addEventListener(ev, fn) { (this.on = this.on || {})[ev] = fn; },
  });
  const header = el();
  const timers = { interval: [], timeout: [] };
  let reloaded = 0;
  let served = payload;

  const ctx = {
    console,
    Date: FakeDate,
    document: {
      currentScript: { dataset: Object.assign({}, cadence ? { cadence } : {}, src ? { src } : {}) },
      querySelector: (s) => (s === 'header.site' ? header : null),
      createElement: el,
      head: { appendChild() {} },
      addEventListener() {},
      hidden: false,
    },
    location: { reload() { reloaded++; } },
    fetch: (u) => Promise.resolve({
      ok: served !== null,
      json: () => Promise.resolve(served),
      _url: u,
    }),
    setInterval: (fn) => { timers.interval.push(fn); return timers.interval.length; },
    setTimeout: (fn) => { timers.timeout.push(fn); return timers.timeout.length; },
  };
  vm.createContext(ctx);
  vm.runInContext(fs.readFileSync(SRC, 'utf8'), ctx);

  const strip = header.children[0];
  return {
    note: () => strip.children[0].textContent,
    btn: () => strip.children[1],
    click: () => strip.children[1].on.click(),
    serve: (p) => { served = p; },
    advance: (min) => { NOW = new RealDate(NOW.getTime() + min * 60000); },
    tickPolls: () => timers.interval.forEach((f) => f()),
    firePendingTimeouts: () => { const t = timers.timeout.splice(0); t.forEach((f) => f()); },
    reloads: () => reloaded,
    settle: () => new Promise((r) => setImmediate(r)),
  };
}

const stamp = (d) => new RealDate(d).toISOString().replace(/\.\d+Z$/, 'Z');

(async () => {
  // ---- slot maths -----------------------------------------------------------
  console.log('next-update countdown (daily slots 03:00 / 15:00 / 22:00 UTC):');
  const expect = [
    ['2026-08-02T09:30:00Z', '15:00'],   // mid-morning -> 8 AM AZ slot
    ['2026-08-02T15:00:00Z', '22:00'],   // exactly on a slot -> the next one
    ['2026-08-02T22:30:00Z', '03:00'],   // after 3 PM AZ -> tonight's rollover
    ['2026-08-31T23:00:00Z', '03:00'],   // month boundary
    ['2026-12-31T23:30:00Z', '03:00'],   // year boundary
  ];
  for (const [now, wantUTC] of expect) {
    const h = harness({ now, payload: { generated_at: stamp(now) } });
    await h.settle();
    const want = new RealDate(now.slice(0, 10) + 'T' + wantUTC + ':00Z');
    const label = want.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
    ok(h.note().includes('next ' + label), `${now} -> next ${label}`, h.note());
  }

  // ---- first paint ----------------------------------------------------------
  console.log('freshness strip:');
  let h = harness({
    now: '2026-08-02T15:12:00Z',
    payload: { generated_at: '2026-08-02T15:04:00Z' },
  });
  await h.settle();
  ok(h.note().startsWith('updated 8 min ago'), 'shows age of the loaded export', h.note());
  ok(!h.note().includes('late'), 'a run that landed on time is not called late');

  // ---- late slot ------------------------------------------------------------
  h = harness({
    now: '2026-08-02T16:10:00Z',                       // 70 min past the 15:00 slot
    payload: { generated_at: '2026-08-02T03:05:00Z' }, // nothing since the rollover
  });
  await h.settle();
  ok(h.note().includes('running late'), 'flags a slot that produced nothing', h.note());
  ok(h.btn().className.includes('warn'), 'button turns amber when late');

  // ---- grace period ---------------------------------------------------------
  h = harness({
    now: '2026-08-02T15:40:00Z',                       // 40 min in, run still going
    payload: { generated_at: '2026-08-02T03:05:00Z' },
  });
  await h.settle();
  ok(!h.note().includes('running late'), 'no false alarm while the run is still working',
    h.note());

  // ---- new data appears -----------------------------------------------------
  console.log('new-data handoff:');
  h = harness({
    now: '2026-08-02T14:55:00Z',
    payload: { generated_at: '2026-08-02T03:05:00Z' },
  });
  await h.settle();
  h.advance(10);                                        // 15:05, run has landed
  h.serve({ generated_at: '2026-08-02T15:03:00Z' });
  h.tickPolls();
  await h.settle();
  ok(h.btn().textContent.includes('New data'), 'offers the new export', h.btn().textContent);
  ok(h.note().includes('updated 12h'), 'age still describes what is ON SCREEN, not the poll',
    h.note());
  ok(!h.note().includes('running late'), 'no late warning while new data is offered', h.note());
  h.click();
  ok(h.reloads() === 1, 'tapping loads it');

  // ---- flash must not eat the pending state ---------------------------------
  h = harness({
    now: '2026-08-02T15:30:00Z',
    payload: { generated_at: '2026-08-02T15:03:00Z' },
  });
  await h.settle();
  h.click();                                            // no new data -> "already latest"
  await h.settle();
  ok(h.btn().textContent.includes('Already the latest'), 'honest when nothing changed',
    h.btn().textContent);
  h.serve({ generated_at: '2026-08-02T15:29:00Z' });    // new data lands mid-flash
  h.tickPolls();
  await h.settle();
  h.firePendingTimeouts();                              // flash timer expires
  ok(h.btn().textContent.includes('New data'),
    'flash timer does not discard data found while it was showing', h.btn().textContent);
  const before = h.reloads();
  h.click();
  ok(h.reloads() === before + 1, 'and the button still loads it');

  // ---- unreachable ----------------------------------------------------------
  console.log('degraded:');
  h = harness({ now: '2026-08-02T15:30:00Z', payload: null });
  await h.settle();
  ok(h.note().includes('offline') && h.note().includes('next'),
    'says offline but still counts down', h.note());

  // ---- hourly page ----------------------------------------------------------
  console.log('signals page (hourly cadence, its own file):');
  h = harness({
    now: '2026-08-02T15:40:00Z', cadence: 'hourly', src: 'data/validation.json',
    payload: { generated_at: '2026-08-02T15:26:00Z' },
  });
  await h.settle();
  const want = new RealDate('2026-08-02T16:25:00Z')
    .toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
  ok(h.note().includes('next ' + want), 'counts down to the next hourly run', h.note());
  ok(h.note().includes('updated 14 min ago'), 'reads its own generated_at', h.note());

  console.log('\n' + (fails ? fails + ' FAILURE(S)' : 'ALL REFRESH TESTS PASSED'));
  process.exit(fails ? 1 : 0);
})();
