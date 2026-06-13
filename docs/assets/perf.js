/* Performance dashboard renderer */
const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = (v, suf = "", dash = "—") => (v === null || v === undefined ? dash : v + suf);
const signCls = (v) => (v > 0 ? "pos" : v < 0 ? "neg" : "dim");

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
  const recent = await fetch("data/recent.json?_=" + Date.now()).then((r) => r.json()).catch(() => ({ bets: [] }));

  const bl = perf.betting.lifetime;
  const warn = bl.low_sample ? `low sample — ${bl.n} bets` : `${bl.n} bets`;
  $("#bet-tiles").innerHTML =
    tile(fmt(bl.roi_pct, "%"), "Lifetime ROI", warn, signCls(bl.roi_pct)) +
    tile(fmt(bl.hit_pct, "%"), "Hit rate", "vs ~52.4% at -110") +
    tile(fmt(bl.avg_clv_pct, "%"), "Avg CLV", "needs closing lines", signCls(bl.avg_clv_pct)) +
    tile(fmt(bl.units, "u"), "Net units", "1u flat per pick", signCls(bl.units));

  const t30 = perf.betting.t30, t7 = perf.betting.t7;
  $("#form-tiles").innerHTML =
    tile(fmt(t30.roi_pct, "%"), "30-day ROI", `${t30.n} bets`, signCls(t30.roi_pct)) +
    tile(fmt(t30.hit_pct, "%"), "30-day hit") +
    tile(fmt(t7.roi_pct, "%"), "7-day ROI", `${t7.n} bets`, signCls(t7.roi_pct)) +
    tile(fmt(t30.avg_clv_pct, "%"), "30-day CLV", "", signCls(t30.avg_clv_pct));

  const pl = perf.projection.lifetime, p30 = perf.projection.t30;
  $("#proj-tiles").innerHTML =
    tile(fmt(pl.mae), "Lifetime MAE", `${pl.n} starts`) +
    tile(fmt(p30.mae), "30-day MAE", `${p30.n} starts`) +
    tile(fmt(pl.bias), "Bias (proj − actual)", "0 = unbiased", signCls(-Math.abs(pl.bias || 0))) +
    tile(fmt(pl.band_coverage_pct, "%"), "p10–p90 coverage", "target ≈ 80%");

  const css = getComputedStyle(document.documentElement);
  const C = { dim: css.getPropertyValue("--dim"), acc: css.getPropertyValue("--accent"),
    grn: css.getPropertyValue("--green"), line: css.getPropertyValue("--line") };
  Chart.defaults.color = C.dim;
  Chart.defaults.borderColor = C.line;

  new Chart($("#maeChart"), {
    type: "line",
    data: { labels: perf.daily_mae.map((d) => d.date.slice(5)),
      datasets: [{ label: "Daily MAE", data: perf.daily_mae.map((d) => d.mae),
        borderColor: C.acc, tension: 0.3, pointRadius: 0, borderWidth: 2 }] },
    options: { plugins: { legend: { display: false } },
      scales: { y: { suggestedMin: 0 } } },
  });

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

  $("#bets tbody").innerHTML = (recent.bets || []).slice(0, 40).map((b) => `
    <tr><td class="dim">${b.date.slice(5)}</td>
      <td>${esc(b.pitcher || "?")} ${b.side === "over" ? "O" : "U"}${b.line}
        <span class="dim">${esc(b.book)}</span></td>
      <td class="dim">${b.odds > 0 ? "+" + b.odds : b.odds}</td>
      <td class="${b.result === "win" ? "pos" : b.result === "loss" ? "neg" : "dim"}">${b.result}</td>
      <td class="${signCls(b.pnl_units)}">${b.pnl_units > 0 ? "+" : ""}${(+b.pnl_units).toFixed(2)}u</td>
      <td class="${signCls(b.clv_pct)}">${b.clv_pct == null ? "—" : b.clv_pct + "%"}</td></tr>`).join("")
    || '<tr><td colspan="6" class="dim">No settled bets yet.</td></tr>';

  $("#versions tbody").innerHTML = (perf.versions || []).map((v) => `
    <tr><td>${esc(v.version)}${v.active ? ' <span class="pos">●</span>' : ""}</td>
      <td class="dim">${(v.trained_at || "").slice(0, 10)}</td>
      <td>${fmt(v.valid_mae)}</td><td>${fmt(v.n_scored)}</td><td>${fmt(v.live_mae)}</td></tr>`).join("");
}
main();
