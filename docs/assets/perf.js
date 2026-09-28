/* Performance dashboard renderer.

   Design: the "Model vs. the book lines" ledger is the star of the page —
   every graded start compared to the canonical book line, newest first,
   with a chart of cumulative units above it. That answers the question
   Robin actually asks this page: what did the model say vs. the book, and
   how did it do? CLV and raw daily projection MAE used to be graphs near
   the top; neither earned its space (CLV is only measured when a closing
   line happens to get typed in, and daily MAE is too noisy to read at a
   glance) so both are gone — CLV isn't tracked here at all anymore, and
   MAE lives on as compact tiles instead of a chart. */
const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = (v, suf = "", dash = "—") => (v === null || v === undefined ? dash : v + suf);
const signCls = (v) => (v > 0 ? "pos" : v < 0 ? "neg" : "dim");
const record = (w, l, p) => `${w}-${l}${p ? "-" + p : ""}`;

function tile(v, k, sub = "", cls = "") {
  return `<div class="tile"><div class="v ${cls}">${v}</div><div class="k">${k}</div>
    ${sub ? `<div class="s">${sub}</div>` : ""}</div>`;
}

async function main() {
  let perf;
  try {
    perf = await (await fetch("data/performance.json?_=" + Date.now())).json();
  } catch {
    document.querySelector(".wrap").insertAdjacentHTML("beforeend",
      '<div class="notice">No performance data yet.</div>');
    return;
  }

  const css = getComputedStyle(document.documentElement);
  const C = { dim: css.getPropertyValue("--dim"), acc: css.getPropertyValue("--accent"),
    grn: css.getPropertyValue("--green"), line: css.getPropertyValue("--line") };
  Chart.defaults.color = C.dim;
  Chart.defaults.borderColor = C.line;

  /* --- Model vs. the book lines: the whole point of this page --- */
  const mk = perf.market || {};
  const ml = mk.lifetime || {};
  if (!ml.n) {
    $("#mkt-tiles").insertAdjacentHTML("beforebegin",
      '<div class="notice">K prop lines pull automatically each morning; this section fills in once those games settle.</div>');
  } else {
    $("#mkt-tiles").innerHTML =
      tile(record(ml.wins, ml.losses, ml.pushes), "Model pick record", `hit ${fmt(ml.hit_pct, "%")}${ml.low_sample ? " · low sample" : ""}`) +
      tile(fmt(ml.units, "u"), "Net units", `ROI ${fmt(ml.roi_pct, "%")}`, signCls(ml.units)) +
      tile(fmt(ml.model_closer_pct, "%"), "Model closer than line", "share of starts, |error| vs. the line") +
      tile(`${fmt(ml.model_mae)} / ${fmt(ml.line_mae)}`, "K MAE — model / book");

    new Chart($("#pnlChart"), {
      type: "line",
      data: { labels: (mk.cum_pnl || []).map((d) => d.date.slice(5)),
        datasets: [{ label: "Cumulative units (model pick, one book per pick)",
          data: (mk.cum_pnl || []).map((d) => d.units),
          borderColor: C.grn, backgroundColor: "transparent",
          tension: 0.25, pointRadius: 0, borderWidth: 2 }] },
      options: { plugins: { legend: { display: true, labels: { boxWidth: 12 } } },
        scales: { y: { title: { display: true, text: "units" } } } },
    });

    new Chart($("#mktMaeChart"), {
      type: "bar",
      data: { labels: (mk.monthly || []).map((m) => m.month),
        datasets: [
          { label: "Model MAE", data: (mk.monthly || []).map((m) => m.model_mae), backgroundColor: C.acc },
          { label: "Book line MAE", data: (mk.monthly || []).map((m) => m.line_mae), backgroundColor: C.dim },
        ] },
      options: { plugins: { legend: { display: true, labels: { boxWidth: 12 } } },
        scales: { y: { suggestedMin: 0, title: { display: true, text: "K MAE by month (lower = closer)" } } } },
    });

    $("#history tbody").innerHTML = (mk.history || []).map((r) => {
      const proj = r.point_est != null ? (+r.point_est).toFixed(1) : "—";
      const pick = `${r.side === "over" ? "▲O" : "▼U"} ${r.line} <span class="dim">${esc(r.book)}${r.odds != null ? " " + (r.odds > 0 ? "+" + r.odds : r.odds) : ""}</span>`;
      const resCls = r.result === "win" ? "pos" : r.result === "loss" ? "neg" : "dim";
      const units = r.pnl_units == null ? "—" : `${r.pnl_units > 0 ? "+" : ""}${r.pnl_units.toFixed(2)}u`;
      return `<tr><td class="dim">${r.date.slice(5)}</td>
        <td><span class="edge-dot${r.is_edge ? "" : " off"}"></span>${esc(r.pitcher || "?")}</td>
        <td>${pick}</td><td class="num">${proj}</td><td class="num">${fmt(r.actual_k)}</td>
        <td class="${resCls}">${esc(r.result)}</td>
        <td class="num ${signCls(r.pnl_units)}">${units}</td></tr>`;
    }).join("") || '<tr><td colspan="7" class="dim">No settled starts yet.</td></tr>';
  }

  /* --- Positive-EV betting record: the subset actually staked --- */
  const bl = perf.betting.lifetime;
  const warn = bl.low_sample ? `low sample — ${bl.n} bets` : `${bl.n} bets`;
  $("#bet-tiles").innerHTML =
    tile(record(bl.wins, bl.losses, bl.pushes), "Record", warn) +
    tile(fmt(bl.roi_pct, "%"), "ROI", "1u flat per pick", signCls(bl.roi_pct)) +
    tile(fmt(bl.units, "u"), "Net units", "", signCls(bl.units)) +
    tile(fmt(bl.hit_pct, "%"), "Hit rate", "vs ~52.4% at -110");

  const t30 = perf.betting.t30, t7 = perf.betting.t7;
  $("#form-note").textContent = t30.n
    ? `Last 30 days: ${fmt(t30.roi_pct, "%")} ROI on ${t30.n} bets · last 7 days: ${fmt(t7.roi_pct, "%")} ROI on ${t7.n} bets.`
    : "Not enough recent settled bets yet for a 30/7-day trend.";

  /* --- Model quality: compact, no chart (the old daily-MAE line chart added noise, not signal) --- */
  const pl = perf.projection.lifetime;
  $("#proj-tiles").innerHTML =
    tile(fmt(pl.mae), "Lifetime K MAE", `${pl.n} starts`) +
    tile(fmt(pl.bias), "Bias (proj − actual)", "0 = unbiased", signCls(-Math.abs(pl.bias || 0))) +
    tile(fmt(pl.band_coverage_pct, "%"), "p10–p90 coverage", "target ≈ 80%") +
    tile(fmt(perf.projection.t30.mae), "30-day K MAE", `${perf.projection.t30.n} starts`);

  new Chart($("#calChart"), {
    type: "scatter",
    data: { datasets: [
      { label: "Buckets", data: perf.calibration.map((b) => ({ x: b.pred_pct, y: b.actual_pct, n: b.n })),
        backgroundColor: C.grn, pointRadius: (ctx) => Math.min(10, 3 + Math.sqrt(ctx.raw?.n || 1)) },
      { label: "Perfect", type: "line", data: [{ x: 30, y: 30 }, { x: 80, y: 80 }],
        borderColor: C.dim, borderDash: [5, 5], pointRadius: 0, borderWidth: 1 },
    ] },
    options: { plugins: { legend: { display: false }, tooltip: { callbacks: {
        label: (c) => `pred ${c.raw.x}% → actual ${c.raw.y}% (n=${c.raw.n || "-"})` } } },
      scales: { x: { min: 25, max: 85, title: { display: true, text: "predicted win %" } },
        y: { min: 0, max: 100, title: { display: true, text: "actual win %" } } } },
  });
  if (!perf.calibration.length) {
    $("#calChart").insertAdjacentHTML("afterend",
      '<div class="notice">Calibration plot appears once enough picks settle (≥5 per bucket).</div>');
  }

  $("#versions tbody").innerHTML = (perf.versions || []).map((v) => `
    <tr><td>${esc(v.version)}${v.active ? ' <span class="pos">●</span>' : ""}</td>
      <td class="dim">${(v.trained_at || "").slice(0, 10)}</td>
      <td>${fmt(v.valid_mae)}</td><td>${fmt(v.n_scored)}</td><td>${fmt(v.live_mae)}</td></tr>`).join("");
}
main();
