/* Signals page: model vs market vs cappers, plus when-to-pick windows. */
const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtML = (v) => (v == null ? "" : v > 0 ? "+" + v : String(v));

function ago(ts) {
  if (!ts) return "never";
  const h = (Date.now() - new Date(ts)) / 36e5;
  if (isNaN(h)) return "?";
  if (h < 1) return Math.max(1, Math.round(h * 60)) + "m ago";
  if (h < 30) return h.toFixed(h < 10 ? 1 : 0) + "h ago";
  return Math.round(h / 24) + "d ago";
}

function srcChip(label, ts, note) {
  const h = ts ? (Date.now() - new Date(ts)) / 36e5 : Infinity;
  const cls = h <= 2 ? "ok" : h <= 24 ? "stale" : "down";
  return `<span class="src ${cls}" title="${esc(note || "")}">${esc(label)} · ${ago(ts)}</span>`;
}

function sourceBar(src) {
  if (!src) return "";
  const xs = Object.entries(src.x || {});
  const anyX = xs.map(([, v]) => v.last_success).filter(Boolean).sort().pop() || null;
  const failing = xs.filter(([, v]) => !v.last_success ||
    (Date.now() - new Date(v.last_success)) / 36e5 > 24).length;
  const xNote = failing ? `${failing}/${xs.length} accounts unreachable — X blocks free scraping; mirrors rotate` : "";
  return `<div class="srcbar">
    ${srcChip("X mirrors", anyX, xNote)}
    ${srcChip("MLB lineups", (src.statsapi || {}).last_success, (src.statsapi || {}).note)}
    ${srcChip("FanGraphs", (src.fangraphs || {}).last_success, (src.fangraphs || {}).note)}
    ${srcChip("K props", src.odds_props_fetched_at, `${src.odds_credits_remaining ?? "?"} odds credits left`)}
  </div>`;
}

function moveTag(k) {
  if (!k || k.move == null) return "";
  const up = k.move > 0;
  return ` <span class="${up ? "move-up" : "move-dn"}">${up ? "▲" : "▼"}${Math.abs(k.move)} from ${k.open}</span>`;
}

function cmpRow(s) {
  const k = s.k_line || {};
  const cells = [
    `<span><span class="lbl">Model</span><b>${s.proj_point != null ? s.proj_point.toFixed(1) : "—"}</b> <span class="dim">K</span></span>`,
    `<span><span class="lbl">Line</span><b>${k.line ?? "—"}</b>${k.books ? ` <span class="dim">${k.books} bk</span>` : ""}${moveTag(k)}</span>`,
    `<span><span class="lbl">FanGraphs</span>${s.fg ? `<b>${s.fg.k_per_start.toFixed(1)}</b>${s.fg.delta != null ? ` <span class="dim">(us ${s.fg.delta > 0 ? "+" : ""}${s.fg.delta.toFixed(1)})</span>` : ""}` : "<b>—</b>"}</span>`,
  ];
  if (s.best_edge) {
    const e = s.best_edge;
    cells.push(`<span><span class="lbl">Best edge</span><b class="${e.prob_edge >= 0.05 ? "pos" : ""}">${e.side === "over" ? "▲" : "▼"} ${e.line} ${fmtML(e.odds)}</b> <span class="dim">${(e.model_prob * 100).toFixed(0)}% (+${(e.prob_edge * 100).toFixed(1)})</span></span>`);
  }
  return `<div class="cmp">${cells.join("")}</div>`;
}

function pickRows(picks) {
  if (!picks || !picks.length) return "";
  return `<div class="edges">` + picks.map((p) => `
    <div class="pickrow">
      <span><b>${esc(p.display)}</b> ${p.side === "over" ? "▲ Over" : "▼ Under"} ${p.line}
        <span class="dim">${p.odds ? fmtML(p.odds) : ""} ${esc(p.book || "")}${p.source === "manual" ? " · manual" : ""}</span></span>
      <span class="${p.agrees ? "pick-agree" : "pick-disagree"}">
        ${p.agrees ? "✓ model agrees" : "✗ model disagrees"}${p.our_prob != null ? ` (${(p.our_prob * 100).toFixed(0)}%)` : ""}</span>
    </div>`).join("") + `</div>`;
}

function card(s) {
  const w = s.window || { state: "wait", reasons: [] };
  const lu = s.lineups || {};
  const badges = [];
  if (s.scratched) badges.push('<span class="badge conf-low">SCRATCHED / new starter listed</span>');
  badges.push(`<span class="badge ${lu.home && lu.away ? "conf-high" : "conf-mid"}">lineups: ${lu.home && lu.away ? "both posted" : lu.home || lu.away ? "one posted" : "not posted"}</span>`);
  if (s.status) badges.push(`<span class="badge">${esc(s.status)}</span>`);
  return `<div class="card" data-go="${w.state === "go" ? 1 : 0}" data-picks="${(s.capper_picks || []).length ? 1 : 0}">
    <div class="top">
      <div class="who">
        <div class="name">${esc(s.pitcher)}</div>
        <div class="meta">${esc(s.team)} ${s.home ? "vs" : "@"} ${esc(s.opp)} · ${esc(s.time_et || "")}</div>
      </div>
      <span class="win win-${w.state}">${w.state}</span>
    </div>
    ${cmpRow(s)}
    ${pickRows(s.capper_picks)}
    ${w.reasons && w.reasons.length ? `<div class="reasons">${w.reasons.map(esc).join(" · ")}</div>` : ""}
    <div class="badges">${badges.join("")}</div>
  </div>`;
}

function capperTable(rows) {
  if (!rows || !rows.length) {
    return `<div class="notice">No settled capper picks yet. Picks show up here once
      tweets are parsed (or added to lines/capper_picks.csv) and games go final.</div>`;
  }
  return `<table class="t"><thead><tr><th>Capper</th><th>Record</th><th>Units</th><th>Pending</th><th>Last</th></tr></thead>
    <tbody>${rows.map((r) => `<tr>
      <td>${esc(r.display)}</td>
      <td>${r.w}-${r.l}${r.p ? "-" + r.p : ""}</td>
      <td class="${r.units > 0 ? "pos" : r.units < 0 ? "neg" : ""}">${r.units > 0 ? "+" : ""}${r.units}u</td>
      <td class="dim">${r.pending || 0}</td>
      <td class="dim">${esc(r.last_pick || "—")}</td>
    </tr>`).join("")}</tbody></table>`;
}

function feedList(posts) {
  if (!posts || !posts.length) {
    return `<div class="notice">No posts captured yet — public X mirrors are refused
      often (X blocks free scraping). The hourly job keeps retrying; health is shown above.</div>`;
  }
  return posts.map((p) => `<div class="post">
    <div class="hdr"><span class="cap">${esc(p.display)}</span>
      <span class="when">${ago(p.posted_at)}</span>
      ${p.parsed ? '<span class="parsed">✓ pick parsed</span>' : ""}</div>
    <div class="txt">${esc(p.text)}${p.url ? ` <a href="${esc(p.url)}" rel="noopener">↗</a>` : ""}</div>
  </div>`).join("");
}

async function main() {
  let d;
  try {
    d = await fetch(`data/validation.json?ts=${Date.now()}`).then((r) => r.json());
  } catch {
    $("#board").innerHTML = `<div class="notice">No signals data yet. The hourly
      workflow publishes data/validation.json — check Actions if this persists.</div>`;
    return;
  }
  $("#subtitle").textContent = `${d.date} · signals ${ago(d.generated_at)} · board ${ago(d.board_generated_at)}`;
  $("#sources").innerHTML = sourceBar(d.sources);
  const starters = (d.starters || []).slice()
    .sort((a, b) => (b.window?.state === "go") - (a.window?.state === "go") ||
                    ((b.best_edge?.prob_edge) || -1) - ((a.best_edge?.prob_edge) || -1));
  $("#board").innerHTML = starters.map(card).join("") ||
    `<div class="notice">No starters on today's board yet.</div>`;
  $("#cappers").innerHTML = capperTable(d.cappers);
  $("#feed").innerHTML = feedList(d.feed);
  document.querySelectorAll(".chip").forEach((ch) => ch.addEventListener("click", () => {
    document.querySelectorAll(".chip").forEach((c) => c.classList.remove("on"));
    ch.classList.add("on");
    const f = ch.dataset.f;
    document.querySelectorAll("#board .card").forEach((c) => {
      c.style.display = f === "all" ? "" :
        f === "go" ? (c.dataset.go === "1" ? "" : "none") :
        (c.dataset.picks === "1" ? "" : "none");
    });
  }));
}
main();
