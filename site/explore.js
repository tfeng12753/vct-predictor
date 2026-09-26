// Exploration views (Players, Meta) and the extra panels used on match, team, rankings and model pages.
import * as d3 from "https://cdn.jsdelivr.net/npm/d3@7/+esm";
import { DATA, T, cw, esc, fmtDate, get, lineChart, logo, main, pct, teamLink, tip } from "./lib.js";

const REGIONS = ["Americas", "EMEA", "Pacific", "China"];
const ROLES = ["Duelist", "Initiator", "Controller", "Sentinel"];
const isDark = () => document.documentElement.dataset.theme === "dark" ||
  (!document.documentElement.dataset.theme && matchMedia("(prefers-color-scheme: dark)").matches);
// categorical slots 1-4 (validated palette); colour follows the region, never its rank
const REGION_COLOR = () => Object.fromEntries(REGIONS.map((r, i) =>
  [r, (isDark() ? ["#3987e5", "#d95926", "#199e70", "#c98500"] : ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"])[i]]));
const fmtSigned = (v, d = 2) => (v == null ? "–" : `${v >= 0 ? "+" : "−"}${Math.abs(v).toFixed(d)}`);
const q2d = (q) => { const [y, qq] = q.split("Q"); return new Date(+y, (+qq - 1) * 3 + 1, 15); };

async function need(name) {
  if (!DATA[name]) DATA[name] = await get(name);
  return DATA[name];
}

// ------------------------------------------------------------------ generic pieces

/** Heat table: rows x cols, cell colour from colorFn(value). An HTML table doubles as the accessible table view. */
function heatTable({ rows, cols, cell, colorFn, rowHead = "", fmt = (v) => pct(v), rowLabel = (r) => esc(r), colLabel = (c) => esc(c) }) {
  return `<div class="table-wrap"><table class="heatmap"><thead><tr><th>${rowHead}</th>${cols.map((c) => `<th class="c">${colLabel(c)}</th>`).join("")}</tr></thead>
    <tbody>${rows.map((r) => `<tr><th scope="row">${rowLabel(r)}</th>${cols.map((c) => {
      const v = cell(r, c);
      if (!v || v.v == null) return `<td class="hm empty">·</td>`;
      const [bg, fg] = colorFn(v.v);
      return `<td class="hm" style="background:${bg};color:${fg}" title="${esc(v.title || "")}">${fmt(v.v)}</td>`;
    }).join("")}</tr>`).join("")}</tbody></table></div>`;
}

const seqColor = (max) => (v) => {
  const k = Math.round(Math.min(1, v / max) * 100);
  return [`color-mix(in srgb, var(--seq-5) ${k}%, var(--seq-0))`, k > 55 ? "#fff" : "var(--ink)"];
};
// diverging around 50%: attack-sided (red) vs defence-sided (aqua), neutral grey midpoint
const sideColor = (span = 0.08) => (v) => {
  const t = Math.max(-1, Math.min(1, (v - 0.5) / span));
  const k = Math.round(Math.abs(t) * 75);
  return [`color-mix(in srgb, ${t >= 0 ? "var(--atk)" : "var(--def)"} ${k}%, var(--neutral))`, k > 55 ? "#fff" : "var(--ink)"];
};

/** Numeric-x line with sized points (used for the economy curve). */
function xyChart(el, pts, { xLabel, yLabel, xFmt = (d) => d, yFmt = (d) => pct(d), yDomain, ref }) {
  const W = cw(el, 640), H = 300, m = { t: 12, r: 16, b: 40, l: 48 };
  const x = d3.scaleLinear().domain(d3.extent(pts, (d) => d.x)).nice().range([m.l, W - m.r]);
  const y = d3.scaleLinear().domain(yDomain || d3.extent(pts, (d) => d.y)).nice().range([H - m.b, m.t]);
  const svg = d3.select(el).append("svg").attr("viewBox", `0 0 ${W} ${H}`).attr("role", "img").attr("aria-label", yLabel);
  svg.append("g").attr("class", "grid").selectAll("line").data(y.ticks(5)).join("line").attr("x1", m.l).attr("x2", W - m.r).attr("y1", y).attr("y2", y);
  svg.append("g").attr("class", "axis").attr("transform", `translate(0,${H - m.b})`).call(d3.axisBottom(x).ticks(8).tickFormat(xFmt).tickSizeOuter(0));
  svg.append("g").selectAll("text").data(y.ticks(5)).join("text").attr("x", m.l - 8).attr("y", (d) => y(d) + 4).attr("text-anchor", "end").text(yFmt);
  svg.append("text").attr("x", W - m.r).attr("y", H - 4).attr("text-anchor", "end").text(xLabel);
  if (ref != null) svg.append("line").attr("x1", m.l).attr("x2", W - m.r).attr("y1", y(ref)).attr("y2", y(ref)).attr("stroke", "var(--ink-3)");
  svg.append("path").datum(pts).attr("d", d3.line().x((d) => x(d.x)).y((d) => y(d.y))).attr("fill", "none").attr("stroke", "var(--team-a)").attr("stroke-width", 2);
  const r = d3.scaleSqrt().domain([0, d3.max(pts, (d) => d.n)]).range([3, 9]);
  svg.append("g").selectAll("circle").data(pts).join("circle").attr("cx", (d) => x(d.x)).attr("cy", (d) => y(d.y)).attr("r", (d) => r(d.n))
    .attr("fill", "var(--team-a)").attr("stroke", "var(--bg)").attr("stroke-width", 2)
    .on("mousemove", (ev, d) => tip(d.tip, ev)).on("mouseleave", () => tip(null));
}

// ------------------------------------------------------------------ match page panels

/** Where each component model lands, with the ensemble as the large mark. */
export function modelSpread(el, u) {
  if (!u.components) return;
  const a = T(u.t1), b = T(u.t2);
  const W = cw(el, 760), H = 112, m = { l: 16, r: 16 };
  const x = d3.scaleLinear().domain([0, 1]).range([m.l, W - m.r]);
  const svg = d3.select(el).append("svg").attr("viewBox", `0 0 ${W} ${H}`).attr("role", "img").attr("aria-label", "Model spread");
  const Y = 50;
  svg.append("line").attr("x1", x(0)).attr("x2", x(1)).attr("y1", Y).attr("y2", Y).attr("stroke", "var(--rule)").attr("stroke-width", 2);
  svg.append("line").attr("x1", x(.5)).attr("x2", x(.5)).attr("y1", Y - 12).attr("y2", Y + 12).attr("stroke", "var(--ink-3)");
  [0, .25, .5, .75, 1].forEach((t) => svg.append("text").attr("x", x(t)).attr("y", 92).attr("text-anchor", t === 0 ? "start" : t === 1 ? "end" : "middle").text(pct(t)));
  svg.append("text").attr("x", x(0)).attr("y", 108).text(`${b.name} favoured`);
  svg.append("text").attr("x", x(1)).attr("y", 108).attr("text-anchor", "end").text(`${a.name} favoured`);
  const comps = u.components.slice().sort((p, q) => p.p1 - q.p1);
  svg.append("g").selectAll("circle").data(comps).join("circle").attr("cx", (d) => x(d.p1)).attr("cy", Y).attr("r", 6)
    .attr("fill", "var(--panel)").attr("stroke", "var(--ink-2)").attr("stroke-width", 2)
    .on("mousemove", (ev, d) => tip(`<b>${esc(d.model)}</b> alone: ${esc(a.name)} ${pct(d.p1)}`, ev)).on("mouseleave", () => tip(null));
  // direct labels, alternating below/above the line so neighbours don't collide
  svg.append("g").selectAll("text").data(comps).join("text").attr("x", (d) => x(d.p1)).attr("y", (d, i) => (i % 2 ? Y - 14 : Y + 24))
    .attr("text-anchor", "middle").style("font-size", "11px").text((d) => d.model.replace(" model", ""));
  svg.append("rect").attr("x", x(u.p1) - 3).attr("y", Y - 16).attr("width", 6).attr("height", 32).attr("rx", 2).attr("fill", "var(--ink)");
  svg.append("text").attr("x", x(u.p1)).attr("y", 12).attr("text-anchor", "middle").attr("class", "big").text(`Full model ${pct(u.p1)}`);
}

/** Side-by-side comparison of the two teams on the signals behind the forecast. */
export function taleOfTheTape(u) {
  const a = T(u.t1), b = T(u.t2);
  const lg = DATA.insights?.league_econ || {};
  const w10 = (t) => t.recent.slice(0, 10).filter((r) => r.won).length;
  const rows = [
    ["Map win rate vs field", a.power, b.power, (v) => pct(v, 1), 1],
    ["Map Elo", a.map_elo, b.map_elo, (v) => v, 1],
    ["Roster Elo (current five)", a.player_elo, b.player_elo, (v) => v, 1],
    ["Roster form, Rating 2.0", a.form?.rating, b.form?.rating, (v) => v?.toFixed(2), 1],
    ["Pistol rounds won", a.econ?.pistol, b.econ?.pistol, (v) => pct(v), 1],
    ["Full-buy rounds won (both teams full)", a.econ?.full_buy, b.econ?.full_buy, (v) => pct(v), 1],
    ["Saving rounds won vs a full buy", a.econ?.eco_win, b.econ?.eco_win, (v) => pct(v), 1],
    ["Series won, last 10", w10(a), w10(b), (v) => `${v} of 10`, 1],
    ["Days since last series", a.rest_days, b.rest_days, (v) => (v == null ? "–" : Math.round(v)), 0],
    ["Roster chemistry (log maps together)", a.chemistry, b.chemistry, (v) => v?.toFixed(2), 1],
  ];
  return `<div class="table-wrap"><table class="tape"><thead><tr><th class="r">${esc(a.name)}</th><th class="c"></th><th>${esc(b.name)}</th></tr></thead><tbody>
    ${rows.map(([label, va, vb, f, higherBetter]) => {
      // compare what is shown: equal after rounding means no edge
      const better = va == null || vb == null || !higherBetter || f(va) === f(vb) ? 0 : (va > vb ? 1 : 2);
      return `<tr><td class="r num ${better === 1 ? "edge" : ""}">${va == null ? "–" : f(va)}</td><td class="c small">${label}</td><td class="num ${better === 2 ? "edge" : ""}">${vb == null ? "–" : f(vb)}</td></tr>`;
    }).join("")}
  </tbody></table></div>
  <p class="small">Bold marks the higher value. Economy rows cover the last 12 months${lg.eco_win != null ? `; the league-wide saving-round win rate is ${pct(lg.eco_win)}` : ""}. Rest and chemistry are shown for context and don't enter the forecast: they didn't improve it in testing.</p>`;
}

// ------------------------------------------------------------------ team page panels

export function teamPanels(t) {
  const comps = Object.entries(t.comps || {}).sort((x, y) => y[1][0].n - x[1][0].n);
  return `
    <h2>Economy profile</h2>
    <p class="note">How the team converts different economic situations over the last 12 months, against the league average (grey outline).</p>
    <div class="chart" id="team-econ"></div>
    <div class="legend"><span><i class="dot" style="background:var(--team-a)"></i>${esc(t.name)}</span><span><i class="dot" style="background:transparent;border:1.5px solid var(--ink-3)"></i>League average</span></div>
    <h2>Results against expectation</h2>
    <p class="note">Running total of map wins above (or below) what Map Elo expected before each map, over the last 60 maps. A rising line means the team keeps beating its rating.</p>
    <div class="chart" id="team-vsexp"></div>
    ${comps.length ? `<h2>Agent compositions</h2>
    <p class="note">Most-played five-agent composition on each map over the last six months.</p>
    <div class="table-wrap"><table><thead><tr><th>Map</th><th>Composition</th><th class="r">Maps</th></tr></thead><tbody>
      ${comps.map(([mp, cs]) => `<tr><td>${esc(mp)}</td><td>${cs[0].agents.map(esc).join(", ")}</td><td class="r">${cs[0].n}</td></tr>`).join("")}
    </tbody></table></div>` : ""}`;
}

export function drawTeamPanels(t) {
  const lg = DATA.insights?.league_econ || {};
  const e = t.econ || {};
  const rows = [
    ["Pistol rounds", e.pistol, lg.pistol, e.pistol_n],
    ["Full buy vs full buy", e.full_buy, lg.full_buy, e.full_buy_n],
    ["Saving round vs full buy", e.eco_win, lg.eco_win, e.eco_n],
    ["Full buy vs saving round", e.anti_eco, lg.anti_eco, e.anti_n],
  ].filter((r) => r[1] != null);
  const el = document.getElementById("team-econ");
  if (el && rows.length) {
    const W = cw(el, 760), rowH = 36, left = 190, H = rows.length * rowH + 24;
    const x = d3.scaleLinear().domain([0, 1]).range([left, W - 20]);
    const svg = d3.select(el).append("svg").attr("viewBox", `0 0 ${W} ${H}`).attr("role", "img").attr("aria-label", "Economy profile");
    svg.append("g").attr("class", "grid").selectAll("line").data(x.ticks(5)).join("line").attr("x1", x).attr("x2", x).attr("y1", 0).attr("y2", H - 20);
    svg.append("g").selectAll("text").data(x.ticks(5)).join("text").attr("x", x).attr("y", H - 4).attr("text-anchor", "middle").text((d) => pct(d));
    const g = svg.selectAll("g.r").data(rows).join("g").attr("class", "r").attr("transform", (d, i) => `translate(0,${i * rowH})`);
    g.append("text").attr("x", left - 12).attr("y", rowH / 2 + 4).attr("text-anchor", "end").attr("class", "lbl").text((d) => d[0]);
    g.filter((d) => d[2] != null).append("circle").attr("cx", (d) => x(d[2])).attr("cy", rowH / 2).attr("r", 6.5).attr("fill", "none").attr("stroke", "var(--ink-3)").attr("stroke-width", 1.5);
    g.append("circle").attr("cx", (d) => x(d[1])).attr("cy", rowH / 2).attr("r", 6.5).attr("fill", "var(--team-a)").attr("stroke", "var(--bg)").attr("stroke-width", 2);
    g.append("text").attr("class", "big").attr("x", (d) => x(d[1]) + (d[1] > .85 ? -12 : 12)).attr("text-anchor", (d) => d[1] > .85 ? "end" : "start").attr("y", rowH / 2 - 8).text((d) => pct(d[1]));
    g.append("rect").attr("width", W).attr("height", rowH).attr("fill", "transparent")
      .on("mousemove", (ev, d) => tip(`<b>${esc(d[0])}</b><br>${esc(t.name)}: ${pct(d[1], 1)} of ${d[3]} rounds${d[2] != null ? `<br>League: ${pct(d[2], 1)}` : ""}`, ev))
      .on("mouseleave", () => tip(null));
  } else if (el) el.innerHTML = `<p class="note">Not enough rounds with economy data yet.</p>`;
  const vs = document.getElementById("team-vsexp");
  if (vs && t.vs_expectation?.length) {
    const vals = t.vs_expectation.map(([d, v], i) => [i + 1, v, d]);
    const W = cw(vs, 760), H = 240, m = { t: 12, r: 16, b: 30, l: 44 };
    const x = d3.scaleLinear().domain([1, vals.length]).range([m.l, W - m.r]);
    const lim = Math.max(2, d3.max(vals, (d) => Math.abs(d[1])));
    const y = d3.scaleLinear().domain([-lim, lim]).nice().range([H - m.b, m.t]);
    const svg = d3.select(vs).append("svg").attr("viewBox", `0 0 ${W} ${H}`).attr("role", "img").attr("aria-label", "Results against expectation");
    svg.append("g").attr("class", "grid").selectAll("line").data(y.ticks(5)).join("line").attr("x1", m.l).attr("x2", W - m.r).attr("y1", y).attr("y2", y);
    svg.append("g").selectAll("text").data(y.ticks(5)).join("text").attr("x", m.l - 8).attr("y", (d) => y(d) + 4).attr("text-anchor", "end").text((d) => (d > 0 ? `+${d}` : d));
    svg.append("line").attr("x1", m.l).attr("x2", W - m.r).attr("y1", y(0)).attr("y2", y(0)).attr("stroke", "var(--ink-3)");
    svg.append("text").attr("x", m.l).attr("y", H - 8).text(`${fmtDate(vals[0][2])}`);
    svg.append("text").attr("x", W - m.r).attr("y", H - 8).attr("text-anchor", "end").text(`${fmtDate(vals.at(-1)[2])}, most recent`);
    svg.append("path").datum(vals).attr("d", d3.line().x((d) => x(d[0])).y((d) => y(d[1]))).attr("fill", "none").attr("stroke", "var(--team-a)").attr("stroke-width", 2);
    const last = vals.at(-1);
    svg.append("circle").attr("cx", x(last[0])).attr("cy", y(last[1])).attr("r", 4.5).attr("fill", "var(--team-a)").attr("stroke", "var(--bg)").attr("stroke-width", 2);
    svg.append("text").attr("class", "big").attr("x", x(last[0]) - 8).attr("y", y(last[1]) - 10).attr("text-anchor", "end").text(`${last[1] >= 0 ? "+" : "−"}${Math.abs(last[1]).toFixed(1)} maps`);
  }
}

// ------------------------------------------------------------------ rankings panel

export function regionChart(el) {
  const hist = DATA.backtest.region_history || [];
  if (!hist.length) return;
  const col = REGION_COLOR();
  lineChart(el, REGIONS.map((r) => ({
    name: r, color: col[r], values: hist.map(([d, v]) => [new Date(`${d}T12:00:00`), v[r] ?? 0]),
  })), { legend: document.getElementById("region-legend"), yLabel: "Region strength", yFormat: (v) => (v > 0 ? `+${Math.round(v)}` : Math.round(v)), height: 260 });
}

// ------------------------------------------------------------------ players page

let pState = { region: "All", role: "All", sort: "rating", dir: -1, q: "" };

export async function players() {
  const all = await need("players");
  const cols = [["name", "Player", 0], ["team", "Team", 0], ["role", "Role", 0], ["maps", "Maps", 1], ["rating", "Rating 2.0", 1],
    ["acs", "ACS", 1], ["kd", "K/D", 1], ["kast", "KAST", 1], ["adr", "ADR", 1], ["hs", "HS%", 1], ["fkd", "FK−FD / map", 1], ["elo", "Player Elo", 1]];
  const filt = all.filter((p) => (pState.region === "All" || p.region === pState.region) && (pState.role === "All" || p.role === pState.role)
    && (!pState.q || p.name.toLowerCase().includes(pState.q) || p.team.toLowerCase().includes(pState.q)));
  const rows = filt.slice().sort((a, b) => pState.dir * (((a[pState.sort] ?? -1e9) > (b[pState.sort] ?? -1e9)) - ((a[pState.sort] ?? -1e9) < (b[pState.sort] ?? -1e9))));
  const f = { rating: (v) => v.toFixed(2), acs: (v) => v.toFixed(0), kd: (v) => v.toFixed(2), kast: (v) => `${v.toFixed(0)}%`, adr: (v) => v.toFixed(0),
    hs: (v) => `${v.toFixed(0)}%`, fkd: (v) => fmtSigned(v), elo: (v) => v.toFixed(0), maps: (v) => v };
  main().innerHTML = `
    <section class="page-head"><h1>Players</h1>
      <p class="lede">Every tier-one player with at least eight maps in the last six months. Stats are per-map averages from vlr.gg; Player Elo is the roster model's rating, which accounts for who they played against.</p></section>
    <div class="controls">
      <div class="seg" role="group" aria-label="Region">${["All", ...REGIONS].map((r) => `<button type="button" data-pregion="${r}" aria-pressed="${r === pState.region}">${r}</button>`).join("")}</div>
      <div class="seg" role="group" aria-label="Role">${["All", ...ROLES].map((r) => `<button type="button" data-prole="${r}" aria-pressed="${r === pState.role}">${r}</button>`).join("")}</div>
      <label class="field">Search player or team<input type="search" id="psearch" value="${esc(pState.q)}" placeholder="e.g. aspas"></label>
    </div>
    <h2 style="margin-top:32px">Impact vs damage</h2>
    <p class="note">Each dot is a player: damage per round across, Rating 2.0 up. Players matching the filters above are highlighted${filt.length !== all.length ? ` (${filt.length} of ${all.length})` : ""}.</p>
    <div class="chart" id="pscatter"></div>
    <h2>Leaderboard</h2>
    <div class="table-wrap"><table>
      <thead><tr>${cols.map(([k, l, s]) => s ? `<th class="r" ${pState.sort === k ? `aria-sort="${pState.dir < 0 ? "descending" : "ascending"}"` : ""}><button data-psort="${k}">${l}</button></th>` : `<th>${l}</th>`).join("")}</tr></thead>
      <tbody>${rows.slice(0, 150).map((p) => `<tr>
        <td><a href="https://www.vlr.gg/player/${p.id}">${esc(p.name)}</a></td>
        <td>${DATA.team.has(p.team_id) ? teamLink(T(p.team_id)) : esc(p.team)}</td>
        <td class="small">${esc(p.role)}<br>${p.agents.map(esc).join(", ")}</td>
        ${cols.slice(3).map(([k]) => `<td class="r num">${p[k] == null ? "–" : f[k](p[k])}</td>`).join("")}</tr>`).join("")}
      </tbody></table></div>
    ${rows.length > 150 ? `<p class="small">Showing the top 150 of ${rows.length}. Narrow the filters to see more.</p>` : ""}`;
  document.querySelectorAll("[data-pregion]").forEach((b) => b.addEventListener("click", () => { pState.region = b.dataset.pregion; players(); }));
  document.querySelectorAll("[data-prole]").forEach((b) => b.addEventListener("click", () => { pState.role = b.dataset.prole; players(); }));
  document.querySelectorAll("[data-psort]").forEach((b) => b.addEventListener("click", () => {
    const k = b.dataset.psort; pState.dir = pState.sort === k ? -pState.dir : -1; pState.sort = k; players();
  }));
  const inp = document.getElementById("psearch");
  let timer;
  inp.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(() => { pState.q = inp.value.trim().toLowerCase(); players().then(() => { const i = document.getElementById("psearch"); i.focus(); i.setSelectionRange(i.value.length, i.value.length); }); }, 250); });
  playerScatter(document.getElementById("pscatter"), all, new Set(filt.map((p) => p.id)));
}

function playerScatter(el, all, hl) {
  const W = cw(el, 900), H = 420, m = { t: 14, r: 20, b: 40, l: 48 };
  const pts = all.filter((p) => p.adr != null && p.rating != null);
  const x = d3.scaleLinear().domain(d3.extent(pts, (d) => d.adr)).nice().range([m.l, W - m.r]);
  const y = d3.scaleLinear().domain(d3.extent(pts, (d) => d.rating)).nice().range([H - m.b, m.t]);
  const svg = d3.select(el).append("svg").attr("viewBox", `0 0 ${W} ${H}`).attr("role", "img").attr("aria-label", "Players: ADR vs Rating 2.0");
  svg.append("g").attr("class", "grid").selectAll("line").data(y.ticks(6)).join("line").attr("x1", m.l).attr("x2", W - m.r).attr("y1", y).attr("y2", y);
  svg.append("g").attr("class", "axis").attr("transform", `translate(0,${H - m.b})`).call(d3.axisBottom(x).ticks(8).tickSizeOuter(0));
  svg.append("g").selectAll("text").data(y.ticks(6)).join("text").attr("x", m.l - 8).attr("y", (d) => y(d) + 4).attr("text-anchor", "end").text((d) => d.toFixed(2));
  svg.append("text").attr("x", W - m.r).attr("y", H - 4).attr("text-anchor", "end").text("Average damage per round");
  svg.append("text").attr("x", m.l).attr("y", m.t - 2).text("Rating 2.0");
  const sorted = pts.slice().sort((a, b) => hl.has(a.id) - hl.has(b.id));
  svg.append("g").selectAll("circle").data(sorted).join("circle")
    .attr("cx", (d) => x(d.adr)).attr("cy", (d) => y(d.rating)).attr("r", (d) => (hl.has(d.id) ? 5 : 3.5))
    .attr("fill", (d) => (hl.has(d.id) ? "var(--team-a)" : "var(--rule)")).attr("stroke", "var(--bg)").attr("stroke-width", (d) => (hl.has(d.id) ? 2 : 1))
    .on("mousemove", (ev, d) => tip(`<b>${esc(d.name)}</b> ${esc(d.team)}<br>Rating ${d.rating.toFixed(2)}, ADR ${d.adr.toFixed(0)}<br>${esc(d.role)}: ${d.agents.map(esc).join(", ")}<br>${d.maps} maps`, ev))
    .on("mouseleave", () => tip(null));
  // label the top-rated highlighted players; flip near the right edge and skip labels that would collide
  const placed = [];
  const labels = [];
  for (const d of pts.filter((p) => hl.has(p.id)).sort((a, b) => b.rating - a.rating)) {
    if (labels.length >= 6) break;
    const w = d.name.length * 7 + 4, right = x(d.adr) + 8 + w > W - m.r;
    const x0 = right ? x(d.adr) - 8 - w : x(d.adr) + 8, y0 = y(d.rating) - 8;
    if (placed.some((b) => x0 < b.x1 && x0 + w > b.x0 && y0 < b.y1 && y0 + 16 > b.y0)) continue;
    placed.push({ x0, x1: x0 + w, y0, y1: y0 + 16 });
    labels.push({ d, x: right ? x(d.adr) - 8 : x(d.adr) + 8, anchor: right ? "end" : "start" });
  }
  svg.append("g").selectAll("text").data(labels).join("text").attr("class", "lbl").attr("x", (l) => l.x).attr("y", (l) => y(l.d.rating) + 4)
    .attr("text-anchor", (l) => l.anchor).text((l) => l.d.name);
}

// ------------------------------------------------------------------ meta page

export async function meta() {
  const I = await need("insights");
  const pool = DATA.meta.pool;
  const M = I.meta, A = I.agents, E = I.economy, P = I.pistol;
  const quarters = M.quarters.slice(-8);
  const cellOf = (mp, q) => M.rows.find((r) => r.map === mp && r.q === q);
  const mapsEver = [...new Set(M.rows.filter((r) => quarters.includes(r.q) && r.maps > 0).map((r) => r.map))]
    .sort((a, b) => (pool.includes(b) - pool.includes(a)) || a.localeCompare(b));
  const agents = A.agents.slice().sort((a, b) => (A.overall[b] || 0) - (A.overall[a] || 0)).slice(0, 22);
  const aMaps = A.maps.filter((m) => pool.includes(m));
  main().innerHTML = `
    <section class="page-head"><h1>The meta</h1>
      <p class="lede">How tier-one Valorant is actually played: which maps teams pick and ban, which side each map favours, which agents they bring, and what money and pistol rounds are worth.</p>
      <div class="kv">
        <div><span class="num">${pct(P.next_round)}</span><span>of pistol winners also win the next round</span></div>
        <div><span class="num">${pct(P.half)}</span><span>of pistol winners go on to win that half</span></div>
        <div><span class="num">${pct(P.map_if_both)}</span><span>of maps are won by the team that takes both pistols (${P.n_maps.toLocaleString()} maps)</span></div>
      </div></section>

    <h2>Map pool over time</h2>
    <p class="note">Share of all maps played each quarter, and how often the map was banned in a veto. Maps in the current pool are listed first.</p>
    <div class="multiples chart" id="map-multiples"></div>
    <div class="legend"><span><i style="background:var(--team-a)"></i>Share of maps played</span><span><i style="background:var(--ink-3)"></i>Ban rate</span></div>

    <h2>Side balance</h2>
    <p class="note">Attacking side's share of rounds won, by map and quarter. Red cells favour attackers, aqua cells favour defenders, grey is balanced. Blank cells had too few rounds.</p>
    ${heatTable({ rows: mapsEver, cols: quarters, rowHead: "Map", cell: (mp, q) => { const c = cellOf(mp, q); return c && c.atk_rw != null ? { v: c.atk_rw, title: `${mp} ${q}: attack wins ${pct(c.atk_rw, 1)} of rounds` } : null; }, colorFn: sideColor(), fmt: (v) => pct(v), colLabel: (q) => q.replace("Q", " Q") })}

    <h2>Agents by map</h2>
    <p class="note">Share of teams fielding each agent on each map in the last ${A.days} days. Darker means more common.</p>
    ${heatTable({ rows: agents, cols: aMaps, rowHead: "Agent", rowLabel: (a) => `${esc(a)} <span class="small">${esc(A.roles[a])}</span>`,
      cell: (ag, mp) => { const c = A.rows.find((r) => r.agent === ag && r.map === mp); return c ? { v: c.rate, title: `${ag} on ${mp}: ${pct(c.rate)} of teams` } : { v: 0 }; },
      colorFn: seqColor(1), fmt: (v) => (v < 0.005 ? "" : pct(v)) })}

    ${contextSection()}
    ${E ? `<h2>What money buys</h2>
    <p class="note">Attackers' round win rate by loadout difference (attack minus defence, in thousands of credits), excluding pistol rounds. ${E.rounds.toLocaleString()} rounds.</p>
    <div class="chart" id="econ-curve"></div>
    <h3 style="margin-top:28px">By buy type</h3>
    <p class="note">Attackers' round win rate for each combination of buys. Red favours attack, aqua favours defence.</p>
    ${heatTable({ rows: E.order, cols: E.order, rowHead: "Attack buy \\ Defence buy",
      cell: (a, d) => { const c = E.matrix.find((x) => x.atk === a && x.def === d); return c ? { v: c.atk_rw, title: `${a} vs ${d}: attack wins ${pct(c.atk_rw, 1)} (${c.n} rounds)` } : null; },
      colorFn: sideColor(0.35), fmt: (v) => pct(v), rowLabel: (r) => esc(r), colLabel: (c) => esc(c) })}` : `<h2>What money buys</h2><p class="note">Economy data is still being collected.</p>`}`;
  mapMultiples(document.getElementById("map-multiples"), mapsEver, M, quarters);
  drawContext();
  if (E) xyChart(document.getElementById("econ-curve"), E.curve.map((c) => ({ x: c.mid, y: c.atk_rw, n: c.n,
    tip: `Loadout gap ${c.lo}k to ${c.hi}k<br>Attack wins ${pct(c.atk_rw, 1)} of ${c.n.toLocaleString()} rounds` })),
    { xLabel: "Attack loadout minus defence loadout (thousands)", yLabel: "Attack round win rate", xFmt: (d) => (d > 0 ? `+${d}k` : `${d}k`), yDomain: [0, 1], ref: 0.5 });
}

function mapMultiples(el, maps, M, quarters) {
  const vals = M.rows.filter((r) => quarters.includes(r.q));
  const maxY = Math.max(0.5, d3.max(vals, (r) => Math.max(r.play_share || 0, r.ban_rate || 0)));
  el.innerHTML = maps.map((mp, i) => `<div class="mult"><div class="mult-h">${esc(mp)}</div><div id="mm-${i}"></div></div>`).join("");
  maps.forEach((mp, i) => {
    const rows = quarters.map((q) => M.rows.find((r) => r.map === mp && r.q === q) || { q, play_share: 0, ban_rate: null });
    const box = document.getElementById(`mm-${i}`);
    const W = cw(box, 260), H = 130, m = { t: 8, r: 10, b: 22, l: 38 };
    const x = d3.scalePoint().domain(quarters).range([m.l, W - m.r]);
    const y = d3.scaleLinear().domain([0, maxY]).range([H - m.b, m.t]);
    const svg = d3.select(box).append("svg").attr("viewBox", `0 0 ${W} ${H}`).attr("role", "img").attr("aria-label", `${mp} play and ban rates`);
    svg.append("g").attr("class", "grid").selectAll("line").data(y.ticks(3)).join("line").attr("x1", m.l).attr("x2", W - m.r).attr("y1", y).attr("y2", y);
    svg.append("g").selectAll("text").data(y.ticks(3)).join("text").attr("x", m.l - 4).attr("y", (d) => y(d) + 4).attr("text-anchor", "end").text((d) => pct(d));
    svg.append("text").attr("x", m.l).attr("y", H - 2).text(quarters[0].replace("Q", " Q"));
    svg.append("text").attr("x", W - m.r).attr("y", H - 2).attr("text-anchor", "end").text(quarters.at(-1).replace("Q", " Q"));
    const line = (k) => d3.line().defined((d) => d[k] != null).x((d) => x(d.q)).y((d) => y(d[k]));
    svg.append("path").datum(rows).attr("d", line("ban_rate")).attr("fill", "none").attr("stroke", "var(--ink-3)").attr("stroke-width", 1.5);
    svg.append("path").datum(rows).attr("d", line("play_share")).attr("fill", "none").attr("stroke", "var(--team-a)").attr("stroke-width", 2);
    svg.append("rect").attr("x", m.l).attr("width", W - m.l - m.r).attr("y", m.t).attr("height", H - m.t - m.b).attr("fill", "transparent")
      .on("mousemove", function (ev) {
        const [mx] = d3.pointer(ev, this);
        const q = quarters.reduce((best, qq) => (Math.abs(x(qq) - mx) < Math.abs(x(best) - mx) ? qq : best), quarters[0]);
        const r = rows.find((rr) => rr.q === q);
        tip(`<b>${esc(mp)}, ${q.replace("Q", " Q")}</b><br>${pct(r.play_share, 1)} of maps played<br>${r.ban_rate == null ? "No vetoes" : `Banned in ${pct(r.ban_rate)} of vetoes`}`, ev);
      })
      .on("mouseleave", () => tip(null));
  });
}

// ------------------------------------------------------------------ model page panels

export async function modelPanels() {
  let ab = null;
  try { ab = await need("ablation"); } catch { /* optional file */ }
  const bt = DATA.backtest;
  const H = bt.health;
  const W = bt.windows;
  const ci = bt.ci || {};
  const pv = bt.paired_vs_elo;
  const winTable = W ? `<div class="table-wrap"><table><thead><tr><th>Model</th><th class="r">Validation log loss</th><th class="r">Validation accuracy</th><th class="r">Holdout log loss</th><th class="r">Holdout accuracy</th></tr></thead><tbody>
    ${Object.keys(W.validation.models).map((k) => { const v = W.validation.models[k], h = W.holdout.models[k];
      return `<tr${k.startsWith("Ensemble") ? ' style="font-weight:600"' : ""}><td>${esc(k)}</td><td class="r num">${v.log_loss.toFixed(4)}</td><td class="r num">${pct(v.accuracy, 1)}</td><td class="r num">${h.log_loss.toFixed(4)}</td><td class="r num">${pct(h.accuracy, 1)}</td></tr>`; }).join("")}
    </tbody></table></div>
    <p class="small">Validation: ${W.validation.models[Object.keys(W.validation.models)[0]].n} series from ${fmtDate(W.validation.from)}. Holdout: ${W.holdout.models[Object.keys(W.holdout.models)[0]].n} series from ${fmtDate(W.holdout.from)}, never used for any modelling decision.</p>` : "";
  return {
    html: `
    <h2>Is the improvement real?</h2>
    <p class="note">Resampling the backtest 2,000 times gives a range for each score. ${pv ? `Against Series Elo, the full model lowers log loss by ${Math.abs(pv.mean).toFixed(4)} per series (95% interval ${Math.abs(pv.hi).toFixed(4)} to ${Math.abs(pv.lo).toFixed(4)}). ${pv.hi < 0 ? "The whole interval is an improvement, so it is unlikely to be luck." : "The interval includes zero, so the improvement may be luck."}` : ""}</p>
    ${Object.keys(ci).length ? `<div class="table-wrap"><table><thead><tr><th>Model</th><th class="r">Log loss, 95% range</th><th class="r">Accuracy, 95% range</th></tr></thead><tbody>
      ${Object.entries(ci).map(([k, v]) => `<tr><td>${esc(k)}</td><td class="r num">${v.ll[0].toFixed(3)} – ${v.ll[1].toFixed(3)}</td><td class="r num">${pct(v.acc[0], 1)} – ${pct(v.acc[1], 1)}</td></tr>`).join("")}
    </tbody></table></div>` : ""}
    <h3 style="margin-top:28px">Validation and holdout</h3>
    <p class="note">Signals and settings were chosen using the validation period only. The holdout period is the honest test.</p>
    ${winTable}
    ${ab ? `<h2>Which signals help</h2>
    <p class="note">Change in map log loss on the validation period when each signal is added to (hollow) or removed from (filled) the model, with a 95% interval. Left of zero means the model is better with that change. A signal only changes status when its whole interval is on one side of zero, because picking signals by point estimates overfit in testing.</p>
    <div class="chart" id="ablation"></div>
    <div class="legend"><span><i class="dot" style="background:var(--ink)"></i>Removing a signal in the model</span><span><i class="dot" style="background:transparent;border:2px solid var(--ink)"></i>Adding a candidate signal</span></div>` : ""}
    ${H ? `<h2>Data health</h2>
    <p class="note">Checks run on every update. A change in vlr.gg's page layout usually shows up here first.</p>
    <div class="table-wrap"><table><thead><tr><th>Check</th><th class="r">Result</th><th class="r">Needs</th><th>Status</th></tr></thead><tbody>
      ${H.checks.map((c) => `<tr><td>${esc(c.check)}</td><td class="r num">${pct(c.value, 1)}</td><td class="r">${pct(c.threshold)}</td><td><span class="status ${c.ok ? "ok" : "warn"}">${c.ok ? "✓ Passing" : "! Below target"}</span></td></tr>`).join("")}
    </tbody></table></div>` : ""}`,
    draw: () => { if (ab) ablationChart(document.getElementById("ablation"), ab); },
  };
}

function ablationChart(el, ab) {
  const rows = ab.singles.slice().sort((a, b) => a.delta - b.delta);
  const W = cw(el, 760), rowH = 30, left = Math.min(250, W * 0.4), H = rows.length * rowH + 30;
  const lim = Math.max(0.002, d3.max(rows, (d) => Math.max(Math.abs(d.lo), Math.abs(d.hi))));
  const x = d3.scaleLinear().domain([-lim, lim]).nice().range([left, W - 20]);
  const svg = d3.select(el).append("svg").attr("viewBox", `0 0 ${W} ${H}`).attr("role", "img").attr("aria-label", "Signal ablation");
  svg.append("g").attr("class", "grid").selectAll("line").data(x.ticks(5)).join("line").attr("x1", x).attr("x2", x).attr("y1", 0).attr("y2", H - 24);
  svg.append("line").attr("x1", x(0)).attr("x2", x(0)).attr("y1", 0).attr("y2", H - 24).attr("stroke", "var(--ink-3)");
  svg.append("g").selectAll("text").data(x.ticks(5)).join("text").attr("x", x).attr("y", H - 8).attr("text-anchor", "middle").text((d) => (d > 0 ? `+${d.toFixed(3)}` : d.toFixed(3)));
  const g = svg.selectAll("g.r").data(rows).join("g").attr("class", "r").attr("transform", (d, i) => `translate(0,${i * rowH})`);
  g.append("text").attr("x", left - 12).attr("y", rowH / 2 + 4).attr("text-anchor", "end").attr("class", "lbl").text((d) => d.label);
  g.append("line").attr("x1", (d) => x(d.lo)).attr("x2", (d) => x(d.hi)).attr("y1", rowH / 2).attr("y2", rowH / 2).attr("stroke", "var(--ink-2)").attr("stroke-width", 2);
  g.append("circle").attr("cx", (d) => x(d.delta)).attr("cy", rowH / 2).attr("r", 5.5)
    .attr("fill", (d) => (d.in_stack ? "var(--ink)" : "var(--bg)")).attr("stroke", "var(--ink)").attr("stroke-width", 2);
  g.append("rect").attr("width", W).attr("height", rowH).attr("fill", "transparent")
    .on("mousemove", (ev, d) => tip(`<b>${esc(d.label)}</b><br>${d.in_stack ? "Removing it" : "Adding it"} changes log loss by ${fmtSigned(d.delta, 4)}<br>95% interval ${fmtSigned(d.lo, 4)} to ${fmtSigned(d.hi, 4)}`, ev))
    .on("mouseleave", () => tip(null));
}

// ------------------------------------------------------------------ dot + interval chart

/** rows: [{label, v, lo, hi, n, tip}] — values are differences (0 = as expected); filled when the interval excludes 0. */
function ciChart(el, rows, { fmt = (v) => `${v >= 0 ? "+" : "−"}${Math.abs(v * 100).toFixed(1)} pts`, axisFmt, leftFrac = 0.34 } = {}) {
  const W = cw(el, 760), rowH = 28, left = Math.min(260, W * leftFrac), H = rows.length * rowH + 28;
  const lim = Math.max(0.05, d3.max(rows, (d) => Math.max(Math.abs(d.lo), Math.abs(d.hi))));
  const x = d3.scaleLinear().domain([-lim, lim]).nice().range([left, W - 70]);
  const svg = d3.select(el).append("svg").attr("viewBox", `0 0 ${W} ${H}`).attr("role", "img");
  svg.append("g").attr("class", "grid").selectAll("line").data(x.ticks(5)).join("line").attr("x1", x).attr("x2", x).attr("y1", 0).attr("y2", H - 22);
  svg.append("line").attr("x1", x(0)).attr("x2", x(0)).attr("y1", 0).attr("y2", H - 22).attr("stroke", "var(--ink-3)");
  svg.append("g").selectAll("text").data(x.ticks(5)).join("text").attr("x", x).attr("y", H - 6).attr("text-anchor", "middle")
    .text(axisFmt || ((d) => `${d > 0 ? "+" : ""}${Math.round(d * 100)}`));
  const g = svg.selectAll("g.r").data(rows).join("g").attr("class", "r").attr("transform", (d, i) => `translate(0,${i * rowH})`);
  g.append("text").attr("x", left - 12).attr("y", rowH / 2 + 4).attr("text-anchor", "end").attr("class", "lbl").text((d) => d.label);
  g.append("line").attr("x1", (d) => x(d.lo)).attr("x2", (d) => x(d.hi)).attr("y1", rowH / 2).attr("y2", rowH / 2).attr("stroke", "var(--ink-2)").attr("stroke-width", 2);
  const clear = (d) => d.lo > 0 || d.hi < 0;
  g.append("circle").attr("cx", (d) => x(d.v)).attr("cy", rowH / 2).attr("r", 5.5)
    .attr("fill", (d) => (clear(d) ? "var(--ink)" : "var(--bg)")).attr("stroke", "var(--ink)").attr("stroke-width", 2);
  g.append("text").attr("x", W - 64).attr("y", rowH / 2 + 4).attr("class", "big").style("font-size", "13px").text((d) => fmt(d.v));
  g.append("rect").attr("width", W).attr("height", rowH).attr("fill", "transparent")
    .on("mousemove", (ev, d) => tip(d.tip, ev)).on("mouseleave", () => tip(null));
}

// ------------------------------------------------------------------ agents page

export async function agents() {
  const I = await need("insights");
  const A = I.agent_stats, C = I.comps || [];
  if (!A) { main().innerHTML = `<div class="error"><h1>No agent data yet</h1><p class="lede">Run the export to build it.</p></div>`; return; }
  const byRole = ROLES.map((r) => [r, A.agents.filter((a) => a.role === r)]).filter(([, l]) => l.length);
  const other = A.agents.filter((a) => !ROLES.includes(a.role));
  const clearN = A.agents.filter((a) => a.lo > 0 || a.hi < 0).length;
  const maps = [...new Set(C.map((c) => c.map))];
  main().innerHTML = `
    <section class="page-head"><h1>Agents</h1>
      <p class="lede">Which agents tier-one teams bring, how that has shifted, and whether any agent actually helps teams win once you account for how strong those teams already are.</p></section>

    <h2>Pick rates over time</h2>
    <p class="note">Share of team-maps fielding each agent, by quarter (last ${A.quarters.length} quarters, ${A.quarters[0].replace("Q", " Q")} to ${A.quarters.at(-1).replace("Q", " Q")}), all on the same scale. The number is the most recent quarter.</p>
    ${byRole.concat(other.length ? [["Unassigned", other]] : []).map(([role, list]) => `
      <h3 style="margin-top:22px">${esc(role)}s</h3>
      <div class="sparks">${list.map((a, i) => `<div class="spark"><div class="spark-h"><span>${esc(a.agent)}</span><span class="num">${pct(a.trend.at(-1)?.[1] || 0)}</span></div><div id="sp-${esc(role)}-${i}"></div></div>`).join("")}</div>`).join("")}

    <h2>Does the agent help, or just the team?</h2>
    <p class="note">Raw agent win rates mostly reflect who picks them: strong teams make any agent look good. Here each agent's results over the last ${Math.round(A.days / 30)} months are compared with what the model expected before those maps, so team strength is taken out. Points are percentage points of map wins above or below expectation, with 95% intervals; filled dots have intervals clear of zero.</p>
    <div class="chart" id="agent-ci"></div>
    <p class="small">${clearN} of ${A.agents.length} agents have intervals clear of zero. With this many agents, about ${Math.max(1, Math.round(A.agents.length * 0.05))} would do so by chance alone, so treat single agents with caution.</p>

    <h2>Compositions</h2>
    <p class="note">The most-played five-agent compositions on each map over the last year (at least 12 team-maps), with results against the model's expectation.</p>
    <div class="table-wrap"><table><thead><tr><th>Map</th><th>Composition</th><th class="r">Team-maps</th><th class="r">Won</th><th class="r">Vs expected</th><th class="r">95% interval</th></tr></thead><tbody>
      ${maps.map((mp) => C.filter((c) => c.map === mp).slice(0, 3).map((c, i) => `<tr>
        <td>${i === 0 ? esc(mp) : ""}</td><td>${c.agents.map(esc).join(", ")}</td><td class="r">${c.n}</td><td class="r num">${pct(c.win)}</td>
        <td class="r num ${c.lo > 0 ? "w" : c.hi < 0 ? "l" : ""}">${c.vs_exp >= 0 ? "+" : "−"}${Math.abs(c.vs_exp * 100).toFixed(0)} pts</td>
        <td class="r small">${fmtSigned(c.lo * 100, 0)} to ${fmtSigned(c.hi * 100, 0)}</td></tr>`).join("")).join("")}
    </tbody></table></div>
    <p class="small">Compositions have small samples; an interval clear of zero (green or red) is suggestive, not proof. Agent usage entered the forecasting model as three candidate signals (comp meta-alignment, agent-pool depth, map comfort); none improved forecasts, see <a href="#/model">How it works</a>.</p>`;
  // one shared scale so shapes are comparable across agents
  const yMax = d3.max(A.agents, (a) => d3.max(a.trend, (d) => d[1] || 0));
  byRole.concat(other.length ? [["Unassigned", other]] : []).forEach(([role, list]) => list.forEach((a, i) => spark(document.getElementById(`sp-${role}-${i}`), a.trend, yMax)));
  ciChart(document.getElementById("agent-ci"), A.agents.slice().sort((a, b) => b.vs_exp - a.vs_exp).map((a) => ({
    label: `${a.agent}`, v: a.vs_exp, lo: a.lo, hi: a.hi,
    tip: `<b>${esc(a.agent)}</b> (${esc(a.role)})<br>${a.n} team-maps, won ${pct(a.win, 1)}<br>Model expected ${pct(a.expected, 1)}<br>Difference ${fmtSigned(a.vs_exp * 100, 1)} pts (95%: ${fmtSigned(a.lo * 100, 1)} to ${fmtSigned(a.hi * 100, 1)})`,
  })));
}

function spark(el, trend, yMax) {
  if (!el) return;
  const W = cw(el, 160), H = 44;
  const vals = trend.map((d) => d[1] ?? 0);
  const x = d3.scaleLinear().domain([0, vals.length - 1]).range([2, W - 4]);
  const y = d3.scaleLinear().domain([0, yMax || Math.max(0.3, d3.max(vals))]).range([H - 3, 3]);
  const svg = d3.select(el).append("svg").attr("viewBox", `0 0 ${W} ${H}`).attr("role", "img").attr("aria-label", "Pick rate trend");
  svg.append("line").attr("x1", 2).attr("x2", W - 4).attr("y1", H - 3).attr("y2", H - 3).attr("stroke", "var(--rule)");
  svg.append("path").datum(vals).attr("d", d3.line().x((d, i) => x(i)).y((d) => y(d))).attr("fill", "none").attr("stroke", "var(--team-a)").attr("stroke-width", 2);
  svg.append("circle").attr("cx", x(vals.length - 1)).attr("cy", y(vals.at(-1))).attr("r", 3).attr("fill", "var(--team-a)");
  svg.append("rect").attr("width", W).attr("height", H).attr("fill", "transparent")
    .on("mousemove", function (ev) {
      const [mx] = d3.pointer(ev, this);
      const i = Math.max(0, Math.min(vals.length - 1, Math.round(x.invert(mx))));
      tip(`<b>${trend[i][0].replace("Q", " Q")}</b><br>${pct(vals[i], 1)} of team-maps`, ev);
    })
    .on("mouseleave", () => tip(null));
}

// ------------------------------------------------------------------ context section (meta page) and experiments (model page)

export function contextSection() {
  const Cx = DATA.insights?.context;
  if (!Cx) return "";
  const h = Cx.home;
  return `
    <h2>Does context matter?</h2>
    <p class="note">How favourites (by Map Elo) fared against their expected win rate in different settings, in percentage points of maps. Filled dots have 95% intervals clear of zero.</p>
    <div class="chart" id="ctx-ci"></div>
    ${h ? `<p class="note">Home region at international events: teams from the host region won ${pct(h.win, 1)} of ${h.n} maps against an expected ${pct(h.expected, 1)} (${fmtSigned(h.vs_exp * 100, 1)} pts, 95% interval ${fmtSigned(h.lo * 100, 1)} to ${fmtSigned(h.hi * 100, 1)}). No measurable home advantage.</p>` : ""}`;
}

export function drawContext() {
  const Cx = DATA.insights?.context;
  const el = document.getElementById("ctx-ci");
  if (!Cx || !el) return;
  ciChart(el, Cx.favourites.map((b) => ({ label: b.label, v: b.vs_exp, lo: b.lo, hi: b.hi,
    tip: `<b>${esc(b.label)}</b><br>${b.n} maps: favourites won ${pct(b.fav_won, 1)}, expected ${pct(b.fav_expected, 1)}` })));
}

export async function experimentsSection() {
  let E;
  try { E = await need("experiments"); } catch { return ""; }
  const fmtD = (r) => r ? `${fmtSigned(r.delta, 4)} <span class="small">[${fmtSigned(r.lo, 4)}, ${fmtSigned(r.hi, 4)}]</span>` : "–";
  return `
    <h2>Experiments log</h2>
    <p class="note">Bigger changes than a single signal, run the same way: decide on the validation period, then report the untouched holdout. Numbers are the change in log loss (negative is better) with 95% intervals.</p>
    ${E.experiments.map((e) => `
      <h3 style="margin-top:24px">${esc(e.title)}</h3>
      <p class="note">${esc(e.hypothesis)}${e.pending ? ` <strong>Pending:</strong> ${esc(e.note || "")}` : ""}</p>
      ${e.rows?.length ? `<div class="table-wrap"><table><thead><tr><th>Setting</th><th class="r">Validation</th><th class="r">Holdout</th><th>Decision</th></tr></thead><tbody>
        ${e.rows.map((r) => `<tr><td>${esc(r.setting)}</td><td class="r num">${fmtD(r.validation)}</td><td class="r num">${fmtD(r.holdout)}</td><td class="small">${esc(r.decision)}</td></tr>`).join("")}
      </tbody></table></div>` : ""}`).join("")}`;
}

// ------------------------------------------------------------------ Champions bracket & finishing places

const PLACE_BUCKETS = [
  ["Champion", ["1st"]], ["Runner-up", ["2nd"]], ["3rd–4th", ["3rd", "4th"]],
  ["5th–8th", ["5th–6th", "7th–8th"]], ["Out in groups", ["9th–12th", "13th–16th"]],
];
const placeColors = () => (isDark() ? ["#cde2fb", "#86b6ef", "#3987e5", "#1c5cab"] : ["#0d366b", "#1c5cab", "#3987e5", "#6da7ec"]).concat(["var(--rule)"]);

function slotCard(b, upcomingIds) {
  const known = b.t1 != null && b.t2 != null;
  const link = known && upcomingIds.has(b.match_id) ? `#/match/${b.match_id}` : null;
  const bo = `Bo${b.best_of}`;
  let body;
  if (known) {
    const done = b.winner != null;
    const [s1, s2] = done ? b.score.split("-") : [null, null];
    const row = (id, p, sc) => {
      const t = T(id);
      const won = done && b.winner === id;
      return `<div class="bk-row ${done ? (won ? "won" : "lost") : ""}">${logo(t, "sm")}<span class="bk-name">${esc(t.name)}</span>
        <span class="bk-val num">${done ? sc : p == null ? "" : pct(p)}</span></div>`;
    };
    const called = done && b.p1 != null ? ((b.p1 >= .5) === (b.winner === b.t1)) : null;
    body = row(b.t1, b.p1, s1) + row(b.t2, b.p1 == null ? null : 1 - b.p1, s2) +
      (done && b.p1 != null ? `<div class="bk-note">${called ? "✓ called" : "✗ upset"}: we had ${esc(T(b.p1 >= .5 ? b.t1 : b.t2).name)} ${pct(Math.max(b.p1, 1 - b.p1))}</div>` : "");
  } else {
    const cand = b.teams.slice(0, 3);
    body = cand.length ? cand.map((c) => `<div class="bk-row likely">${logo(T(c.team_id), "sm")}<span class="bk-name">${esc(T(c.team_id).name)}</span>
      <span class="bk-val num" title="Chance to play in this match">${pct(c.plays)}</span></div>`).join("") +
      `<div class="bk-note">Most likely to play here</div>` : `<div class="bk-row likely"><span class="bk-name">To be decided</span></div>`;
  }
  const tag = link ? "a" : "div";
  return `<${tag} class="bk-card" ${link ? `href="${link}"` : ""}><div class="bk-head"><span>${esc(b.label)}</span><span>${bo}</span></div>${body}</${tag}>`;
}

export function bracketSection() {
  const tr = DATA.tournament;
  if (!tr.bracket?.length) return "";
  const up = new Set(DATA.upcoming.map((u) => u.match_id));
  const S = Object.fromEntries(tr.bracket.map((b) => [b.slot, b]));
  const card = (id) => (S[id] ? slotCard(S[id], up) : "");
  const col = (title, ids) => `<div class="bk-col"><div class="bk-col-h">${title}</div><div class="bk-col-body">${ids.map(card).join("")}</div></div>`;
  const groups = ["A", "B", "C", "D"].map((g) => `
    <div class="bk-group"><h3>Group ${g}</h3>
      <div class="bk-grid gsl">
        ${col("Opening", [`${g}-o1`, `${g}-o2`])}
        ${col("Second round", [`${g}-w`, `${g}-e`])}
        ${col("Decider", [`${g}-d`])}
      </div></div>`).join("");
  return `
    <h2>Bracket</h2>
    <p class="note">Every series of the event. Finished series show the score and whether our pre-match favourite won. Scheduled series show each team's chance to win. Series that aren't drawn yet show the teams most likely to reach them, from the same ${tr.sims.toLocaleString()} simulations. Select a scheduled series for the full breakdown.</p>
    <h3 style="margin-top:22px">Group stage</h3>
    <p class="small">Each group is double elimination: the Winners' match winner finishes first, the Decider winner second, and both reach the playoffs.</p>
    <div class="bk-groups">${groups}</div>
    <h3 style="margin-top:30px">Playoffs</h3>
    <p class="small">Upper-bracket losers drop to the lower bracket; a second loss eliminates. The playoff draw shown before the group stage ends is our assumption (group winners meet another group's runner-up); vlr.gg's real draw replaces it once published.</p>
    <div class="bk-scroll"><div class="bk-grid playoffs">
      ${col("Upper quarterfinals", ["uqf1", "uqf2", "uqf3", "uqf4"])}
      ${col("Upper semifinals", ["usf1", "usf2"])}
      ${col("Upper final", ["uf"])}
      ${col("Grand final", ["gf"])}
    </div>
    <div class="bk-grid playoffs lower">
      ${col("Lower round 1", ["lr1a", "lr1b"])}
      ${col("Lower round 2", ["lr2a", "lr2b"])}
      ${col("Lower round 3", ["lr3"])}
      ${col("Lower final", ["lf"])}
    </div></div>

    <h2>How each team is expected to finish</h2>
    <p class="note">Each bar splits a team's ${tr.sims.toLocaleString()} simulated tournaments by where it finished. Teams are ordered by expected finish.</p>
    <div class="chart" id="places"></div>
    <div class="legend">${PLACE_BUCKETS.map(([l], i) => `<span><i class="sq" style="background:${placeColors()[i]}"></i>${l}</span>`).join("")}</div>`;
}

export function drawPlacements() {
  const tr = DATA.tournament, el = document.getElementById("places");
  if (!el || !tr.placements) return;
  const mid = { "1st": 1, "2nd": 2, "3rd": 3, "4th": 4, "5th–6th": 5.5, "7th–8th": 7.5, "9th–12th": 10.5, "13th–16th": 14.5 };
  const rows = Object.entries(tr.placements).map(([id, d]) => ({
    id: +id, d, exp: Object.entries(d).reduce((s, [k, v]) => s + v * mid[k], 0),
    buckets: PLACE_BUCKETS.map(([l, ks]) => ({ label: l, v: ks.reduce((s, k) => s + (d[k] || 0), 0) })),
  })).sort((a, b) => a.exp - b.exp);
  const W = cw(el, 900), rowH = 30, left = Math.min(190, W * 0.3), right = 70, H = rows.length * rowH + 24;
  const x = d3.scaleLinear().domain([0, 1]).range([left, W - right]);
  const colors = placeColors();
  const svg = d3.select(el).append("svg").attr("viewBox", `0 0 ${W} ${H}`).attr("role", "img").attr("aria-label", "Finishing place distribution by team");
  svg.append("g").selectAll("text").data([0, .25, .5, .75, 1]).join("text").attr("x", x).attr("y", H - 4).attr("text-anchor", "middle").text((d) => pct(d));
  svg.append("text").attr("x", W - right + 8).attr("y", 10).style("font-size", "11px").text("Expected");
  const g = svg.selectAll("g.r").data(rows).join("g").attr("class", "r").attr("transform", (d, i) => `translate(0,${i * rowH + 14})`);
  g.append("text").attr("x", left - 10).attr("y", rowH / 2 + 4).attr("text-anchor", "end").attr("class", "lbl").text((d) => T(d.id).name);
  g.each(function (r) {
    let acc = 0;
    r.buckets.forEach((b, i) => {
      if (b.v <= 0) return;
      const x0 = x(acc), w = Math.max(0, x(acc + b.v) - x0 - 2);  // 2px surface gap between segments
      d3.select(this).append("rect").attr("x", x0).attr("y", rowH / 2 - 8).attr("width", w).attr("height", 16)
        .attr("rx", 2).attr("fill", colors[i]);
      acc += b.v;
    });
  });
  g.append("text").attr("x", W - right + 8).attr("y", rowH / 2 + 5).attr("class", "big").text((d) => {
    const e = d.exp; return e < 1.5 ? "1st" : `${Math.round(e)}${["th", "st", "nd", "rd"][Math.round(e) % 10 < 4 && ![11, 12, 13].includes(Math.round(e)) ? Math.round(e) % 10 : 0]}`;
  });
  g.append("rect").attr("width", W).attr("height", rowH).attr("fill", "transparent")
    .on("mousemove", (ev, r) => tip(`<b>${esc(T(r.id).name)}</b><br>${r.buckets.map((b) => `${b.label}: ${pct(b.v, 1)}`).join("<br>")}<br>Average finish: ${r.exp.toFixed(1)}`, ev))
    .on("mouseleave", () => tip(null));
}
