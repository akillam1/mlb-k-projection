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
    if (!isNaN(t)) asof = t.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  }
  return `<div class="mkt">${total}${ml}${asof ? `<span class="mkt-src">${asof}</span>` : ""}</div>`;
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
      ${s.k_line ? `<span class="badge">K line ${s.k_line.line} <span class="dim">${esc(s.k_line.book)}</span></span>` : ""}</div>
    ${marketRow(s)}
    ${edgeRows(s.edges)}
  </div>`;
}

/* Edge coloring: projection vs. the K line, same relative-threshold idea as
   the NBA board — green means the projection clears the line by enough to
   matter, red means it sits meaningfully under it, gold is a toss-up. Purely
   a visual cue on top of the actual +EV picks below, not a replacement for them. */
const LINE_EDGE_THRESHOLD = 0.06;
function lineEdgeClass(proj, line) {
  if (proj == null || line == null || !line) return "";
  const edge = (proj - line) / line;
  if (edge > LINE_EDGE_THRESHOLD) return "pos";
  if (edge < -LINE_EDGE_THRESHOLD) return "neg";
  return "neu";
}

function bestScore(s) {
  const pos = (s.edges || []).filter((e) => e.ev_per_unit > 0);
  return pos.length ? Math.max(...pos.map(probEdge)) : -1;
}

/* Short lineup note, folded into the pitcher cell's subtext rather than its
   own column — the confidence number mattered less than knowing at a glance
   whether it's a real lineup yet, so a colored word does that job in less space. */
function lineupNote(conf, tier) {
  const cls = conf >= 0.95 ? "lu-high" : conf >= 0.5 ? "lu-mid" : "lu-low";
  const label = { confirmed: "lineup confirmed", actual: "lineup confirmed",
    common7d: "projected lineup", team_agg: "team-avg lineup" }[tier] || "lineup unknown";
  return `<span class="${cls}">${label}</span>`;
}

/* ---------------- Table view: same slate, one sortable row per starter ---------------- */

const TABLE_COLS = [
  { key: "pitcher", label: "Pitcher", num: false, get: (s) => s.pitcher },
  { key: "proj", label: "Proj K", num: true, get: (s) => (s.proj ? s.proj.point : -1) },
  { key: "line", label: "K line", num: true, get: (s) => (s.k_line ? s.k_line.line : -1) },
];

let sortState = { key: "proj", dir: "desc" };

function sortStarters(list) {
  const col = TABLE_COLS.find((c) => c.key === sortState.key) || TABLE_COLS[TABLE_COLS.length - 1];
  const dir = sortState.dir === "asc" ? 1 : -1;
  return list.slice().sort((a, b) => {
    const av = col.get(a), bv = col.get(b);
    if (col.num) return dir * ((av ?? -1) - (bv ?? -1));
    return dir * String(av ?? "").localeCompare(String(bv ?? ""));
  });
}

/* K line column: the current line is always shown (that's the thing Robin
   actually checks — DK and FD are almost always the same number, so shopping
   between them isn't the point). When there's a real +EV side, the pick,
   odds, and EV ride along as subtext underneath — same numbers the old
   separate "Best pick" column had, just not a whole column of their own. */
function lineCell(s) {
  if (!s.k_line) return '<span class="dim">—</span>';
  const pos = dedupeEdges(s.edges).filter((e) => e.ev_per_unit > 0).sort((a, b) => probEdge(b) - probEdge(a));
  const best = pos[0];
  let html = `<span class="kline-num">${s.k_line.line}</span> <span class="dim">${esc(s.k_line.book)}</span>`;
  if (best) {
    html += `<div class="tsub"><span class="pick">${best.side === "over" ? "▲O" : "▼U"}</span>
      <span class="dim">${best.odds > 0 ? "+" + best.odds : best.odds} · +${(best.ev_per_unit * 100).toFixed(1)}% EV</span></div>`;
  }
  return html;
}

function tableRow(s) {
  const p = s.proj;
  const meta = `${esc(s.team)} ${s.home ? "vs" : "@"} ${esc(s.opp)} · ${esc(s.time_et)}`;
  if (!p) {
    const nameCell = `<div class="pn">${esc(s.pitcher)}</div><div class="pm">${meta}</div>`;
    return `<tr><td>${nameCell}</td><td class="dim">No projection yet</td>
      <td class="num">${lineCell(s)}</td></tr>`;
  }
  const nameCell = `<div class="pn">${esc(s.pitcher)}</div>
    <div class="pm">${meta} · ${lineupNote(p.lineup_confidence, p.lineup_tier)}</div>`;
  const projCls = lineEdgeClass(p.point, s.k_line ? s.k_line.line : null);
  return `<tr data-hasedge="${(s.edges || []).some((e) => e.ev_per_unit > 0)}">
    <td>${nameCell}</td>
    <td class="num proj ${projCls}">${p.point.toFixed(1)}</td>
    <td class="num">${lineCell(s)}</td>
  </tr>`;
}

function tableView(starters) {
  const rows = sortStarters(starters).map(tableRow).join("");
  const head = TABLE_COLS.map((c) => {
    const active = c.key === sortState.key;
    const arrow = active ? (sortState.dir === "asc" ? " ↑" : " ↓") : "";
    return `<th data-key="${c.key}" class="${active ? "sort-on" : ""}${c.num ? " num" : ""}">${esc(c.label)}${arrow}</th>`;
  }).join("");
  return `<div class="board-tbl-wrap"><div class="board-tbl">
    <table class="t"><thead><tr>${head}</tr></thead><tbody>${rows}</tbody></table>
  </div></div>`;
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

  let currentFilter = localStorage.getItem("kproj_filter") || "all";
  let viewMode = localStorage.getItem("kproj_view") || "table";

  const render = () => {
    const list = currentFilter === "edges" ? starters.filter((s) => bestScore(s) > 0) : starters;
    if (!list.length) {
      $("#board").innerHTML = '<div class="notice">' + (currentFilter === "edges"
        ? "No positive-EV edges right now. Enter today's K lines or check back after the next refresh."
        : "No games on the slate today.") + "</div>";
      return;
    }
    $("#board").innerHTML = viewMode === "table" ? tableView(list) : list.map(card).join("");
    if (viewMode === "table") {
      $("#board").querySelectorAll("th[data-key]").forEach((th) => th.addEventListener("click", () => {
        const key = th.dataset.key;
        sortState = key === sortState.key
          ? { key, dir: sortState.dir === "asc" ? "desc" : "asc" }
          : { key, dir: "desc" };
        render();
      }));
    }
  };

  document.querySelectorAll(".chip[data-f]").forEach((ch) => ch.addEventListener("click", () => {
    document.querySelectorAll(".chip[data-f]").forEach((c) => c.classList.remove("on"));
    ch.classList.add("on");
    currentFilter = ch.dataset.f;
    localStorage.setItem("kproj_filter", currentFilter);
    render();
  }));
  document.querySelectorAll(".chip[data-v]").forEach((ch) => ch.addEventListener("click", () => {
    document.querySelectorAll(".chip[data-v]").forEach((c) => c.classList.remove("on"));
    ch.classList.add("on");
    viewMode = ch.dataset.v;
    localStorage.setItem("kproj_view", viewMode);
    render();
  }));

  document.querySelectorAll(".chip[data-f]").forEach((c) => c.classList.toggle("on", c.dataset.f === currentFilter));
  document.querySelectorAll(".chip[data-v]").forEach((c) => c.classList.toggle("on", c.dataset.v === viewMode));
  render();
}
main();
