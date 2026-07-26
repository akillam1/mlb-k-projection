/* Today board renderer */
const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const DOMAIN_MAX = 12;
const px = (v) => Math.max(0, Math.min(100, (v / DOMAIN_MAX) * 100));

function confBadge(conf, tier) {
  const cls = conf >= 0.95 ? "conf-high" : conf >= 0.5 ? "conf-mid" : "conf-low";
  const label = { confirmed: "lineup confirmed", actual: "lineup confirmed",
    common7d: "projected lineup", team_agg: "team-average lineup" }[tier] || "lineup ?";
  return `<span class="badge ${cls}">${esc(label)} · ${Math.round(conf * 100)}%</span>`;
}

function rangeBar(p) {
  const ticks = [0, 2, 4, 6, 8, 10, 12].map((t) =>
    `<span class="tick" style="left:${px(t)}%">${t}</span>`).join("");
  return `<div class="range">
    ${ticks}<div class="axis"></div>
    <div class="band80" style="left:${px(p.p10)}%;width:${Math.max(1, px(p.p90) - px(p.p10))}%"></div>
    <div class="band50" style="left:${px(p.p25)}%;width:${Math.max(1, px(p.p75) - px(p.p25))}%"></div>
    <div class="med" style="left:${px(p.p50)}%"></div>
  </div>`;
}

const fmtML = (v) => (v == null ? "—" : v > 0 ? "+" + v : String(v));

function marketRow(s) {
  const o = s.odds;
  if (!o) return "";
  const homeAb = s.home ? s.team : s.opp;
  const awayAb = s.home ? s.opp : s.team;
  const total = o.total != null
    ? `<span class="mkt-item">Runs O/U <b>${o.total}</b>
        <span class="dim">o${fmtML(o.over_odds)} u${fmtML(o.under_odds)}</span></span>` : "";
  const ml = (o.home_ml != null || o.away_ml != null)
    ? `<span class="mkt-item">ML <b>${esc(homeAb)} ${fmtML(o.home_ml)}</b>
        <span class="dim">${esc(awayAb)} ${fmtML(o.away_ml)}</span></span>` : "";
  let asof = "";
  if (o.fetched_at) {
    const t = new Date(o.fetched_at);
    if (!isNaN(t)) asof = " · " + t.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  }
  return `<div class="mkt">${total}${ml}
    <span class="mkt-src">${o.books} book${o.books === 1 ? "" : "s"}${asof}</span></div>`;
}

/* Probability edge: model win % minus vig-free market win %. Primary ranking. */
const probEdge = (e) => (e.prob_edge != null ? e.prob_edge : (e.model_prob - e.vigfree_prob));

/* One row per pick: the same side+line offered at several books collapses
   to the single best price (highest EV). Validation pages go further and
   score only the canonical book — see Methodology. */
function dedupeEdges(edges) {
  const best = new Map();
  (edges || []).forEach((e) => {
    const k = `${e.side}|${e.line}`;
    if (!best.has(k) || e.ev_per_unit > best.get(k).ev_per_unit) best.set(k, e);
  });
  return [...best.values()];
}

function edgeRows(edges) {
  const pos = dedupeEdges(edges).filter((e) => e.ev_per_unit > 0)
    .sort((a, b) => probEdge(b) - probEdge(a));
  if (!pos.length) {
    return (edges || []).length
      ? '<div class="noedge">No +EV side vs current K lines.</div>'
      : '<div class="noedge">No K line yet — props pull automatically ~10 AM AZ (or add via lines/manual_lines.csv).</div>';
  }
  return `<div class="edges">` + pos.map((e) => `
    <div class="edge-row">
      <span class="pick">${e.side === "over" ? "▲ Over" : "▼ Under"} ${e.line}
        <span class="bk">${esc(e.book)} ${e.odds > 0 ? "+" + e.odds : e.odds}</span></span>
      <span class="nums"><span class="ev-pos">win ${(e.model_prob * 100).toFixed(0)}% (+${(probEdge(e) * 100).toFixed(1)} pts)</span>
        <span class="kelly">· +${(e.ev_per_unit * 100).toFixed(1)}% EV · ¼K ${(e.kelly_quarter * 100).toFixed(1)}%u</span></span>
    </div>`).join("") + `</div>`;
}

/* Slate-wide summary: best +EV edge per starter, ranked by probability edge. */
function topEdges(starters, n = 8) {
  const rows = [];
  (starters || []).forEach((s) => {
    const pos = dedupeEdges(s.edges).filter((e) => e.ev_per_unit > 0)
      .sort((a, b) => probEdge(b) - probEdge(a));
    if (pos.length) rows.push({ s, e: pos[0] });   // one row per starter
  });
  return rows.sort((a, b) => probEdge(b.e) - probEdge(a.e)).slice(0, n);
}

function summaryTable(starters) {
  const rows = topEdges(starters, 8);
  if (!rows.length) {
    return `<h2 class="sec">Top edges</h2>
      <div class="notice" style="margin:0 0 16px">No positive-EV edges vs the current K lines.
      Props pull automatically ~10 AM AZ.</div>`;
  }
  const body = rows.map(({ s, e }) => {
    const odds = e.odds > 0 ? "+" + e.odds : e.odds;
    const pick = `${e.side === "over" ? "▲ O" : "▼ U"} ${e.line}`;
    return `<tr>
      <td><div class="pn">${esc(s.pitcher)}</div>
        <div class="pm">${s.home ? "vs" : "@"} ${esc(s.opp)} · ${esc(s.time_et)}</div></td>
      <td class="pk">${pick}</td>
      <td class="dim">${esc(e.book)} ${odds}</td>
      <td>${(e.model_prob * 100).toFixed(0)}%</td>
      <td class="pos">+${(probEdge(e) * 100).toFixed(1)}</td>
      <td class="pos">+${(e.ev_per_unit * 100).toFixed(1)}%</td>
    </tr>`;
  }).join("");
  return `<h2 class="sec">Top edges · biggest model vs. book gaps</h2>
    <div class="summary">
      <table class="t summary-t">
        <thead><tr>
          <th>Pitcher</th><th>Pick</th><th>Book</th><th>Model</th><th>Edge</th><th>EV</th>
        </tr></thead>
        <tbody>${body}</tbody>
      </table>
    </div>`;
}

function card(s) {
  const p = s.proj;
  const head = `
    <div class="top">
      <div class="who">
        <div class="name">${esc(s.pitcher)}</div>
        <div class="meta">${esc(s.team)} ${s.home ? "vs" : "@"} ${esc(s.opp)} · ${esc(s.time_et)}
          ${s.temp_f != null ? " · " + Math.round(s.temp_f) + "°F" : ""}</div>
      </div>
      <div class="pt">${p ? `<div class="num">${p.point.toFixed(1)}</div><div class="lbl">proj K</div>` : ""}</div>
    </div>`;
  if (!p) return `<div class="card">${head}${marketRow(s)}<div class="noedge">No projection yet (model pending or just-announced starter).</div></div>`;
  return `<div class="card" data-hasedge="${(s.edges || []).some((e) => e.ev_per_unit > 0)}">
    ${head}${rangeBar(p)}
    <div class="badges">${confBadge(p.lineup_confidence, p.lineup_tier)}
      <span class="badge">p10 ${p.p10} · p90 ${p.p90}</span>
      ${s.k_line ? `<span class="badge">K line ${s.k_line.line} · ${s.k_line.books} bk</span>` : ""}</div>
    ${marketRow(s)}
    ${edgeRows(s.edges)}
  </div>`;
}

function bestScore(s) {
  const pos = (s.edges || []).filter((e) => e.ev_per_unit > 0);
  return pos.length ? Math.max(...pos.map(probEdge)) : -1;
}

async function main() {
  let data;
  try {
    data = await (await fetch("data/today.json?_=" + Date.now())).json();
  } catch {
    $("#board").innerHTML = '<div class="notice">No data yet. The daily workflow hasn\'t produced a slate — check Actions.</div>';
    return;
  }
  const upd = new Date(data.generated_at);
  $("#subtitle").textContent = `${data.date} · updated ${upd.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}`;
  const starters = (data.starters || []).slice()
    .sort((a, b) => bestScore(b) - bestScore(a) || String(a.time_et).localeCompare(b.time_et));
  $("#summary").innerHTML = summaryTable(starters);
  const render = (filter) => {
    const list = filter === "edges" ? starters.filter((s) => bestScore(s) > 0) : starters;
    $("#board").innerHTML = list.length
      ? list.map(card).join("")
      : '<div class="notice">' + (filter === "edges"
          ? "No positive-EV edges right now. Enter today's K lines or check back after the next refresh."
          : "No games on the slate today.") + "</div>";
  };
  document.querySelectorAll(".chip").forEach((ch) => ch.addEventListener("click", () => {
    document.querySelectorAll(".chip").forEach((c) => c.classList.remove("on"));
    ch.classList.add("on");
    localStorage.setItem("kproj_filter", ch.dataset.f);
    render(ch.dataset.f);
  }));
  const saved = localStorage.getItem("kproj_filter") || "all";
  document.querySelectorAll(".chip").forEach((c) => c.classList.toggle("on", c.dataset.f === saved));
  render(saved);
}
main();
