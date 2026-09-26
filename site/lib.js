// Shared state, formatting helpers and chart primitives.
import * as d3 from "https://cdn.jsdelivr.net/npm/d3@7/+esm";

export const DATA = {};
export const get = (name) => fetch(`data/${name}.json`, { cache: "no-store" }).then((r) => {
  if (!r.ok) throw new Error(`${name}.json: ${r.status}`);
  return r.json();
});

// ------------------------------------------------------------------ helpers
export const main = () => document.getElementById("main");
export const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
export const pct = (p, d = 0) => (p == null ? "–" : `${(p * 100).toFixed(d)}%`);
export const pctN = (p) => (p == null ? "–" : (p * 100).toFixed(0));
export const T = (id) => DATA.team.get(id) || { id, name: `Team ${id}`, tag: "?", maps: [], roster: [], recent: [], elo_history: [] };
// Match times are UTC ISO strings (shown in the viewer's timezone); plain YYYY-MM-DD dates are calendar days.
export const parseD = (s) => (s.length === 10 ? new Date(`${s}T12:00:00`) : new Date(s));
export const fmtDate = (s) => parseD(s).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
export const fmtDay = (s) => parseD(s).toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" });
export const fmtTime = (s) => parseD(s).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit", timeZoneName: "short" });
export const localDayKey = (s) => { const d = parseD(s); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`; };
export const cw = (el, fallback) => Math.max(300, Math.round(el?.getBoundingClientRect().width || fallback));
export const cssVar = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
export const logo = (t, cls = "") => t.logo
  ? `<img class="logo ${cls}" src="${esc(t.logo)}" alt="" loading="lazy" onerror="this.style.visibility='hidden'">`
  : `<span class="logo ${cls}"></span>`;
export const teamLink = (t) => `<a href="#/team/${t.id}">${esc(t.name)}</a>`;
export const cleanSeries = (s) => (s || "").replace(/\s*[:–-]\s*/g, ": ");

export function heat(p) {
  if (p == null) return "";
  const k = Math.round(Math.min(1, p) * 100);
  const color = k > 55 ? "#fff" : "var(--ink)";
  return `style="background: color-mix(in srgb, var(--seq-5) ${k}%, var(--seq-0)); color:${color}"`;
}

const tipEl = document.querySelector(".tip");
export function tip(html, ev) {
  if (!html) { tipEl.hidden = true; return; }
  tipEl.innerHTML = html;
  tipEl.hidden = false;
  const { innerWidth: W } = window;
  const r = tipEl.getBoundingClientRect();
  let x = ev.clientX + 14, y = ev.clientY + 14;
  if (x + r.width > W - 8) x = ev.clientX - r.width - 14;
  if (y + r.height > window.innerHeight - 8) y = ev.clientY - r.height - 14;
  tipEl.style.left = `${Math.max(8, x)}px`;
  tipEl.style.top = `${y}px`;
}

export function tug(p1, cls = "") {
  const a = Math.max(0.5, p1 * 100), b = Math.max(0.5, 100 - p1 * 100);
  return `<div class="tug ${cls}" role="img" aria-label="${pct(p1)} versus ${pct(1 - p1)}">
    <span class="a" style="flex-basis:${a}%"></span><span class="b" style="flex-basis:${b}%"></span></div>`;
}

export function lineChart(el, series, opts = {}) {
  const W = cw(el, 900), H = opts.height || 300, m = { t: 12, r: 16, b: 26, l: 48 };
  const all = series.flatMap((s) => s.values);
  if (!all.length) { el.innerHTML = `<p class="note">No history yet.</p>`; return; }
  const x = d3.scaleTime().domain(d3.extent(all, (d) => d[0])).range([m.l, W - m.r]);
  const y = d3.scaleLinear().domain(d3.extent(all, (d) => d[1])).nice().range([H - m.b, m.t]);
  const svg = d3.select(el).append("svg").attr("viewBox", `0 0 ${W} ${H}`).attr("role", "img").attr("aria-label", opts.yLabel || "Line chart");
  svg.append("g").attr("class", "grid").selectAll("line").data(y.ticks(5)).join("line")
    .attr("x1", m.l).attr("x2", W - m.r).attr("y1", y).attr("y2", y);
  svg.append("g").selectAll("text").data(y.ticks(5)).join("text").attr("x", m.l - 8).attr("y", (d) => y(d) + 4).attr("text-anchor", "end").text((d) => opts.yFormat ? opts.yFormat(d) : d);
  svg.append("g").attr("class", "axis").attr("transform", `translate(0,${H - m.b})`).call(d3.axisBottom(x).ticks(6).tickSizeOuter(0));
  const line = d3.line().x((d) => x(d[0])).y((d) => y(d[1])).curve(opts.points ? d3.curveLinear : d3.curveStepAfter);
  svg.append("g").selectAll("path").data(series).join("path")
    .attr("d", (s) => line(s.values)).attr("fill", "none").attr("stroke", (s) => s.color)
    .attr("stroke-width", (s) => s.width || 2).attr("stroke-linejoin", "round");
  if (opts.points) series.forEach((s) => svg.append("g").selectAll("circle").data(s.values).join("circle")
    .attr("cx", (d) => x(d[0])).attr("cy", (d) => y(d[1])).attr("r", 4).attr("fill", s.color).attr("stroke", "var(--bg)").attr("stroke-width", 2));
  if (opts.legend) opts.legend.innerHTML = series.map((s) => `<span><i style="background:${s.color}"></i>${esc(s.name)}</span>`).join("");
  const cross = svg.append("line").attr("stroke", "var(--ink-3)").attr("y1", m.t).attr("y2", H - m.b).style("display", "none");
  const dots = svg.append("g").selectAll("circle").data(series).join("circle").attr("r", 4.5).attr("fill", (s) => s.color)
    .attr("stroke", "var(--bg)").attr("stroke-width", 2).style("display", "none");
  const bis = d3.bisector((d) => d[0]).right;
  svg.append("rect").attr("x", m.l).attr("y", m.t).attr("width", W - m.l - m.r).attr("height", H - m.t - m.b).attr("fill", "transparent")
    .on("mousemove", function (ev) {
      const [mx] = d3.pointer(ev, this);
      const date = x.invert(mx);
      cross.attr("x1", mx).attr("x2", mx).style("display", null);
      const pts = series.map((s) => { const i = Math.max(0, bis(s.values, date) - 1); return s.values[i] ? { s, v: s.values[i] } : null; });
      dots.style("display", (s, i) => pts[i] ? null : "none").attr("cx", (s, i) => pts[i] && x(pts[i].v[0])).attr("cy", (s, i) => pts[i] && y(pts[i].v[1]));
      const rows = pts.filter(Boolean).sort((p, q) => q.v[1] - p.v[1]);
      tip(`<b>${date.toLocaleDateString(undefined, { month: "short", year: "numeric", day: "numeric" })}</b><br>${rows.map((p) =>
        `<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${p.s.color};margin-right:6px"></span>${esc(p.s.name)}: ${opts.yFormat ? opts.yFormat(p.v[1]) : Math.round(p.v[1])}`).join("<br>")}`, ev);
    })
    .on("mouseleave", () => { cross.style("display", "none"); dots.style("display", "none"); tip(null); });
}

