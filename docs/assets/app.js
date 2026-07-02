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

function edgeRows(edges) {
  const pos = (edges || []).filter((e) => e.ev_per_unit > 0).sort((a, b) => b.score - a.score);
  if (!pos.length) {
    return (edges || []).length
      ? '<div class="noedge">No +EV side vs current K lines.</div>'
      : '<div class="noedge">No K line yet — props pull automatically ~10:10 AM PT (or add via lines/manual_lines.csv).</div>';
  }
  return `<div class="edges">` + pos.map((e) => `
    <div class="edge-row">
      <span class="pick">${e.side === "over" ? "▲ Over" : "▼ Under"} ${e.line}
        <span class="bk">${esc(e.book)} ${e.odds > 0 ? "+" + e.odds : e.odds}</span></span>
      <span class="nums"><span class="ev-pos">+${(e.ev_per_unit * 100).toFixed(1)}% EV</span>
        <span class="kelly">· ¼K ${(e.kelly_quarter * 100).toFixed(1)}%u · p ${(e.model_prob * 100).toFixed(0)}%</span></span>
    </div>`).join("") + `</div>`;
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
  return pos.length ? Math.max(...pos.map((e) => e.score)) : -1;
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
