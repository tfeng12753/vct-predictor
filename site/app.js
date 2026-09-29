import * as d3 from "https://cdn.jsdelivr.net/npm/d3@7/+esm";
import { vetoSim } from "./sim.js";
import { DATA, T, cleanSeries, confidence, cssVar, cw, esc, fairOdds, fmtDate, fmtDay, fmtTime, get, heat, joinList, lineChart, localDayKey, logo, main, parseD, pct, pctN, teamLink, tip, tug } from "./lib.js";
import * as X from "./explore.js";

// ------------------------------------------------------------------ data
async function boot() {
  try {
    const [meta, teams, upcoming, tournament, backtest, insights] = await Promise.all(
      ["meta", "teams", "upcoming", "tournament", "backtest", "insights"].map(get));
    Object.assign(DATA, { meta, teams, upcoming, tournament, backtest, insights });
    DATA.team = new Map(teams.map((t) => [t.id, t]));
    DATA.status = await get("status").catch(() => null);
    stamp();
  } catch (e) {
    main().innerHTML = `<div class="error"><h1>Forecast data is missing</h1>
      <p class="lede">Run <code>python -m pipeline.scrape</code> and then <code>python -m pipeline.export</code> to build <code>site/data/</code>, then reload this page.</p>
      <p class="small">${esc(e.message)}</p></div>`;
    return;
  }
  window.addEventListener("hashchange", () => route());
  setupSearch();
  route();
  setInterval(checkForUpdates, 3 * 60 * 1000);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) checkForUpdates(); });
}

function ago(iso) {
  const min = Math.round((Date.now() - new Date(iso)) / 60000);
  if (min < 1) return "just now";
  if (min < 60) return `${min} min ago`;
  const h = Math.round(min / 60);
  return h < 48 ? `${h} h ago` : `${Math.round(h / 24)} days ago`;
}

function stamp() {
  const m = DATA.meta, st = DATA.status;
  document.getElementById("stamp").textContent =
    `Results through ${fmtDate(m.data_through)}. Forecasts built ${m.generated_at}.` +
    (st ? ` Checked vlr.gg ${ago(st.checked_at)}.` : "");
}

// The scheduled updater (update.sh) rewrites data/ in place; pick up new forecasts without a reload.
let checking = false;
async function checkForUpdates() {
  if (checking || document.hidden) return;
  checking = true;
  try {
    DATA.status = await get("status").catch(() => DATA.status);
    const meta = await get("meta");
    if (meta.generated_at !== DATA.meta.generated_at) {
      const names = ["teams", "upcoming", "tournament", "backtest", "insights"];
      const fresh = await Promise.all(names.map(get));
      names.forEach((n, i) => (DATA[n] = fresh[i]));
      DATA.meta = meta;
      DATA.team = new Map(DATA.teams.map((t) => [t.id, t]));
      for (const lazy of ["matchup", "players", "ablation"]) delete DATA[lazy];
      if (!document.querySelector("select:focus, input:focus")) route(true);
      toast("Forecasts updated with the latest results from vlr.gg");
    }
    stamp();
  } catch { /* offline or mid-write: try again next time */ }
  checking = false;
}

function toast(msg) {
  let el = document.querySelector(".toast");
  if (!el) { el = document.createElement("div"); el.className = "toast"; el.setAttribute("role", "status"); document.body.append(el); }
  el.textContent = msg;
  el.classList.add("show");
  setTimeout(() => el.classList.remove("show"), 6000);
}

// ------------------------------------------------------------------ theme
const themeBtn = document.querySelector(".theme-toggle:not(.search-btn)");
try { const saved = localStorage.getItem("theme"); if (saved) document.documentElement.dataset.theme = saved; } catch {}
themeBtn.addEventListener("click", () => {
  const dark = document.documentElement.dataset.theme
    ? document.documentElement.dataset.theme === "dark"
    : matchMedia("(prefers-color-scheme: dark)").matches;
  document.documentElement.dataset.theme = dark ? "light" : "dark";
  try { localStorage.setItem("theme", document.documentElement.dataset.theme); } catch {}
  route(true);
});

// ------------------------------------------------------------------ router
let resizeTimer, lastW = window.innerWidth;
window.addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => { if (Math.abs(window.innerWidth - lastW) > 40) { lastW = window.innerWidth; route(true); } }, 200);
});

function route(keepScroll = false) {
  const h = location.hash.replace(/^#\/?/, "");
  const [view, arg] = h.split("/");
  const views = { "": home, champions, rankings, team, match, lab, model, players: X.players, meta: X.meta, agents: X.agents };
  const fn = views[view] || home;
  document.querySelectorAll("nav a").forEach((a) => a.removeAttribute("aria-current"));
  const navKey = { "": "home", match: "home", team: "rankings" }[view] ?? view;
  document.querySelector(`nav a[data-nav="${navKey}"]`)?.setAttribute("aria-current", "page");
  tip(null);
  const y = window.scrollY;
  fn(arg);
  window.scrollTo(0, keepScroll === true ? y : 0);
}

// ------------------------------------------------------------------ views: home
function nextMatches() {
  return DATA.upcoming.filter((u) => u.status !== "completed").sort((a, b) => a.date.localeCompare(b.date));
}

function duel(u, opts = {}) {
  const a = T(u.t1), b = T(u.t2);
  const favA = u.p1 >= 0.5;
  return `
    <div class="duel">
      <div class="duel-team">${logo(a)}<div><div class="duel-name">${teamLink(a)}</div></div></div>
      <div class="duel-vs">vs</div>
      <div class="duel-team right"><div><div class="duel-name">${teamLink(b)}</div></div>${logo(b)}</div>
    </div>
    ${tug(u.p1, "grow")}
    <div class="duel-pcts">
      <span class="duel-pct ${favA ? "fav" : "dog"}">${pctN(u.p1)}<small>%</small></span>
      ${verdict(u)}
      <span class="duel-pct ${favA ? "dog" : "fav"}">${pctN(1 - u.p1)}<small>%</small></span>
    </div>
    ${opts.caption ? `<p class="note">${opts.caption}</p>` : ""}`;
}

function verdict(u) {
  const c = confidence(u.p1), fav = T(u.p1 >= 0.5 ? u.t1 : u.t2);
  return `<span class="verdict ${c.cls}">${c.cls === "c0" ? "Toss-up" : `${esc(fav.tag || fav.name)}: ${c.label.toLowerCase()}`}</span>`;
}

function home() {
  const ups = nextMatches();
  const hero = ups[0];
  const days = d3.groups(ups, (u) => localDayKey(u.date));
  const tour = DATA.tournament.teams.slice(0, 8);
  const m = DATA.meta;
  const st = DATA.status;
  const lastCheckH = st ? (Date.now() - new Date(st.checked_at)) / 36e5 : null;
  const stale = st && !st.ok ? `<p class="banner">The last automatic update failed (${esc(st.message)}). You're seeing the most recent forecasts that built successfully.</p>`
    : lastCheckH != null && lastCheckH > 30 ? `<p class="banner">The daily update hasn't run since ${fmtDate(st.checked_at.slice(0, 10))}, so the newest results may be missing.</p>`
    : !st && (Date.now() - parseD(m.data_through)) / 864e5 > 5 && ups.length ? `<p class="banner">These forecasts use results up to ${fmtDate(m.data_through)}. Run <code>./update.sh</code> or install the scheduler to keep them current.</p>` : "";
  const unhealthy = m.health_ok === false ? `<p class="banner">Some data checks are below target, so forecasts may be less reliable than usual. <a href="#/model">See data health</a></p>` : "";
  main().innerHTML = `${stale}${unhealthy}
    ${hero ? `<section class="hero" aria-labelledby="hero-h">
      <div class="hero-meta"><strong id="hero-h">Next up</strong>
        <span>${esc(hero.event)}</span><span>${esc(cleanSeries(hero.series))}</span>
        <span>Best of ${hero.best_of}</span><span>${fmtDay(hero.date)}, ${fmtTime(hero.date)}</span></div>
      ${duel(hero)}
      <p class="note">Chance to win the series, before the map veto. <a href="#/match/${hero.match_id}">See the map-by-map breakdown</a></p>
    </section>` : `<section class="page-head"><h1>No scheduled matches</h1>
      <p class="lede">vlr.gg has no upcoming tier-one series with both teams decided. Try the <a href="#/lab">matchup lab</a>.</p></section>`}

    ${ups.length ? `<h2>Upcoming series</h2>
    <p class="note">Win chances for every scheduled tier-one series where both teams are known. Select one for maps, likely vetoes and the reasoning.</p>
    <div class="slate">${days.map(([day, list]) => `
      <div class="day"><div class="day-label">${fmtDay(day)}</div>
      ${list.map(fixtureRow).join("")}</div>`).join("")}</div>` : ""}

    ${tour.length ? `<h2>Champions 2026 title odds</h2>
    <p class="note">From ${DATA.tournament.sims.toLocaleString()} simulations of the rest of the event. <a href="#/champions">Full bracket odds</a></p>
    <div class="chart" id="title-chart"></div>` : ""}

    ${trackRecord()}

    <div class="kv">
      <div><span class="num">${m.counts.series.toLocaleString()}</span><span>series since 2023</span></div>
      <div><span class="num">${m.counts.maps.toLocaleString()}</span><span>maps</span></div>
      <div><span class="num">${m.counts.rounds.toLocaleString()}</span><span>rounds, with side and outcome</span></div>
      <div><span class="num">${pct(DATA.backtest.series["Ensemble + veto sim (pre-veto)"]?.accuracy, 1)}</span><span>series picked correctly out of sample</span></div>
    </div>`;
  if (tour.length) titleChart(document.getElementById("title-chart"), tour);
  bindRecordDots();
}

// How recent forecasts have done: favourites' hit rate next to the confidence we gave them.
function trackRecord() {
  const rec = (DATA.backtest.recent || []).filter((r) => r.p1 != null);
  if (rec.length < 10) return "";
  const cutoff = parseD(DATA.meta.data_through).getTime() - 30 * 864e5;
  let rows = rec.filter((r) => parseD(r.date).getTime() >= cutoff);
  let span = "the last 30 days";
  if (rows.length < 15) { rows = rec.slice(0, 40); span = `the last ${rows.length} series`; }
  const fav = rows.map((r) => Math.max(r.p1, 1 - r.p1));
  const hits = rows.filter((r) => (r.p1 >= 0.5) === (r.win1 === 1)).length;
  const avg = fav.reduce((s, v) => s + v, 0) / fav.length;
  const brier = rows.reduce((s, r) => s + (r.p1 - r.win1) ** 2, 0) / rows.length;
  return `<h2>How the forecasts have done</h2>
    <p class="note">Every tier-one series in ${span}, each forecast before it was played. If the probabilities are honest, favourites should win about as often as we said they would. <a href="#/model">Full backtest</a></p>
    <div class="kv">
      <div><span class="num">${hits} of ${rows.length}</span><span>favourites won (${pct(hits / rows.length)})</span></div>
      <div><span class="num">${pct(avg)}</span><span>average forecast for the favourite</span></div>
      <div><span class="num">${brier.toFixed(3)}</span><span>Brier score (coin flip 0.250, lower is better)</span></div>
    </div>
    <div class="record" role="list" aria-label="Recent forecasts, oldest first">${rows.slice().reverse().map((r, i) => {
      const ok = (r.p1 >= 0.5) === (r.win1 === 1), f = Math.max(r.p1, 1 - r.p1);
      return `<span role="listitem" class="rec ${ok ? "hit" : "miss"}" style="height:${Math.round(10 + (f - 0.5) * 60)}px" data-i="${rows.length - 1 - i}"
        aria-label="${esc(r.t1)} vs ${esc(r.t2)}: ${ok ? "called" : "upset"}"></span>`;
    }).join("")}</div>
    <div class="legend"><span><i class="sq" style="background:var(--good)"></i>Favourite won</span><span><i class="sq" style="background:var(--bad)"></i>Upset</span><span>Taller bars were more confident forecasts. Hover for details.</span></div>`;
}

function bindRecordDots() {
  const rec = (DATA.backtest.recent || []).filter((r) => r.p1 != null);
  const cutoff = parseD(DATA.meta.data_through).getTime() - 30 * 864e5;
  let rows = rec.filter((r) => parseD(r.date).getTime() >= cutoff);
  if (rows.length < 15) rows = rec.slice(0, 40);
  document.querySelectorAll(".record .rec").forEach((el) => {
    const r = rows[+el.dataset.i];
    el.addEventListener("mousemove", (ev) => {
      const fav = r.p1 >= 0.5 ? r.t1 : r.t2, ok = (r.p1 >= 0.5) === (r.win1 === 1);
      tip(`<b>${esc(r.t1)} vs ${esc(r.t2)}</b><br>${fmtDate(r.date)}, ${esc(r.event)}<br>We had ${esc(fav)} at ${pct(Math.max(r.p1, 1 - r.p1))}<br>Result ${r.score}: ${ok ? "called" : "upset"}`, ev);
    });
    el.addEventListener("mouseleave", () => tip(null));
  });
}

function fixtureRow(u) {
  const a = T(u.t1), b = T(u.t2);
  return `<a class="fixture" href="#/match/${u.match_id}">
    <span class="time">${fmtTime(u.date)}</span>
    <span class="side">${logo(a, "sm")}<span>${esc(a.name)}</span></span>
    <span><span class="odds"><span class="num ${u.p1 >= .5 ? "fav" : "dog"}">${pctN(u.p1)}</span>${tug(u.p1, "thin")}<span class="num ${u.p1 < .5 ? "fav" : "dog"}">${pctN(1 - u.p1)}</span></span></span>
    <span class="side r"><span>${esc(b.name)}</span>${logo(b, "sm")}</span>
    <span class="stage">${esc(cleanSeries(u.series).split(": ").pop())}, Bo${u.best_of}<br><span class="conf ${confidence(u.p1).cls}">${confidence(u.p1).label}</span></span>
  </a>`;
}

function titleChart(el, rows) {
  const W = cw(el, 760), rowH = 30, H = rows.length * rowH + 24, left = 170, right = 60;
  const x = d3.scaleLinear().domain([0, Math.max(0.3, d3.max(rows, (d) => d.champion))]).range([left, W - right]);
  const svg = d3.select(el).append("svg").attr("viewBox", `0 0 ${W} ${H}`).attr("role", "img")
    .attr("aria-label", "Title odds by team");
  svg.append("g").attr("class", "grid").selectAll("line").data(x.ticks(5)).join("line")
    .attr("x1", x).attr("x2", x).attr("y1", 0).attr("y2", H - 20);
  svg.append("g").selectAll("text").data(x.ticks(5)).join("text")
    .attr("x", x).attr("y", H - 4).attr("text-anchor", "middle").text((d) => pct(d));
  const g = svg.append("g").selectAll("g").data(rows).join("g")
    .attr("transform", (d, i) => `translate(0,${i * rowH})`);
  g.append("text").attr("x", left - 12).attr("y", rowH / 2 + 4).attr("text-anchor", "end").attr("class", "lbl")
    .text((d) => T(d.team_id).name);
  g.append("rect").attr("x", left).attr("y", rowH / 2 - 5).attr("height", 10).attr("rx", 3)
    .attr("width", (d) => Math.max(2, x(d.champion) - left)).attr("fill", "var(--team-a)");
  g.append("text").attr("x", (d) => x(d.champion) + 8).attr("y", rowH / 2 + 4).attr("class", "big").text((d) => pct(d.champion, 1));
  g.append("rect").attr("x", 0).attr("width", W).attr("height", rowH).attr("fill", "transparent")
    .on("mousemove", (ev, d) => tip(`<b>${esc(T(d.team_id).name)}</b><br>Win title ${pct(d.champion, 1)}<br>Reach final ${pct(d.final, 1)}<br>Reach playoffs ${pct(d.playoffs, 1)}`, ev))
    .on("mouseleave", () => tip(null));
}

// ------------------------------------------------------------------ views: match
function match(id) {
  const u = DATA.upcoming.find((x) => String(x.match_id) === id);
  if (!u) { main().innerHTML = `<div class="error"><h1>Match not found</h1><p class="lede">It may have finished since the last model run. <a href="#/">Back to forecasts</a></p></div>`; return; }
  const a = T(u.t1), b = T(u.t2);
  main().innerHTML = `
    <section class="hero">
      <div class="hero-meta"><strong>${esc(u.event)}</strong><span>${esc(cleanSeries(u.series))}</span>
        <span>Best of ${u.best_of}</span><span>${fmtDay(u.date)}, ${fmtTime(u.date)}</span>
        <span><a href="https://www.vlr.gg/${u.match_id}">Match page on vlr.gg</a></span></div>
      ${duel(u)}
      ${storyline(u)}
      <p class="note">Series Elo alone would say ${pct(u.p1_elo)} for ${esc(a.name)}. The full model also weighs map pools, attack and defence by map, current rosters, and how each team tends to veto. <a href="#/lab/${u.t1}-${u.t2}-${u.best_of}">Open this matchup in the lab</a></p>
    </section>
    ${u.components ? `<h2>Do the models agree?</h2>
    <p class="note">Each hollow dot is one component model on its own; the bar is the full forecast. Widely spread dots mean the models disagree and the forecast is less certain.</p>
    <div class="chart" id="spread"></div>` : ""}
    ${matchBody(u)}
    <h2>Side by side</h2>
    ${X.taleOfTheTape(u)}
    <h2>Head to head</h2>
    ${u.h2h.length ? `<div class="table-wrap"><table><thead><tr><th>Date</th><th>Event</th><th class="r">${esc(a.name)} score</th></tr></thead>
      <tbody>${u.h2h.map((h) => `<tr><td>${fmtDate(h.date)}</td><td>${esc(h.event)}</td><td class="r ${h.won ? "w" : "l"}">${h.score}</td></tr>`).join("")}</tbody></table></div>`
      : `<p class="note">These teams have not met in a tier-one series since 2023.</p>`}
    <div class="cols block">
      ${[a, b].map((t) => `<div><h3>${esc(t.name)}: last five</h3>${recentTable(t.recent.slice(0, 5))}</div>`).join("")}
    </div>`;
  drawMatchCharts(u);
  if (u.components) X.modelSpread(document.getElementById("spread"), u);
}

function matchBody(u) {
  const a = T(u.t1), b = T(u.t2);
  const scores = Object.entries(u.scores).map(([k, v]) => ({ k, v, w1: +k.split("-")[0] > +k.split("-")[1] }));
  return `
    <div class="cols two block">
      <div>
        <h2 style="margin-top:0">Maps</h2>
        <p class="note">How often each map shows up across simulated vetoes, and ${esc(a.name)}'s chance to win it. The bar shows a neutral pick; the smaller numbers show the chance when each team picks the map.</p>
        <div>${u.maps.map((m) => `
          <div class="maprow">
            <div><div class="mapname">${esc(m.map)}</div><div class="likely">${pct(m.play_rate)} of vetoes</div></div>
            <div>${tug(m.p_neutral, "thin")}
              <div class="picks"><span>${esc(a.tag || a.name)} pick: ${pct(m.p_t1_pick)}</span><span>${esc(b.tag || b.name)} pick: ${pct(m.p_t2_pick)}</span>
              ${m.t1_pick_rate > .25 ? `<span>${esc(a.tag)} picks it ${pct(m.t1_pick_rate)}</span>` : ""}
              ${m.t2_pick_rate > .25 ? `<span>${esc(b.tag)} picks it ${pct(m.t2_pick_rate)}</span>` : ""}</div></div>
            <div class="pct">${pct(m.p_neutral)}</div>
          </div>`).join("")}</div>
        <div class="legend"><span><i class="sq" style="background:var(--team-a)"></i>${esc(a.name)}</span><span><i class="sq" style="background:var(--team-b)"></i>${esc(b.name)}</span></div>
      </div>
      <div>
        <h2 style="margin-top:0">Final score</h2>
        <p class="note">Chance of each series result.</p>
        <div class="chart" id="score-chart"></div>
        ${u.factors ? `<h3 style="margin-top:28px">What drives the forecast</h3>
        <p class="note">Each signal's push, averaged over the map pool. Bars pointing left favour ${esc(a.name)}, right favour ${esc(b.name)}.</p>
        <div class="chart" id="factor-chart"></div>` : ""}
      </div>
    </div>
    <h2>Map pools side by side</h2>
    <p class="note">Each team's expected win rate on each map against the average active tier-one team. The axis zooms to the data, so small gaps can look large; the Maps list above gives the head-to-head chance.</p>
    <div class="chart" id="pool-chart"></div>
    <div class="legend"><span><i class="dot" style="background:var(--team-a)"></i>${esc(a.name)} (circle)</span><span><i class="sq" style="background:var(--team-b)"></i>${esc(b.name)} (square)</span></div>
    ${vetoPanel(u)}`;
}

// Who is likely to put each map into the series: a stacked bar per map from the simulated vetoes.
function vetoPanel(u) {
  if (!u.maps?.length || u.maps[0].t1_pick_rate == null) return "";
  const a = T(u.t1), b = T(u.t2);
  const rows = u.maps.map((m) => ({ ...m, dec: Math.max(0, m.play_rate - m.t1_pick_rate - m.t2_pick_rate) }));
  const top = (k) => rows.slice().sort((x, y) => y[k] - x[k])[0];
  const pa = top("t1_pick_rate"), pb = top("t2_pick_rate"), pd = top("dec");
  const seg = (w, cls, label) => (w > 0.005 ? `<span class="${cls}" style="flex-basis:${w * 100}%" title="${label}: ${pct(w)}"></span>` : "");
  return `<h2>How the veto is likely to go</h2>
    <p class="note">From the simulated vetoes: who puts each map into the series, and how often it's left as the decider or never played. Each team's pick is its own choice, so it's usually a map it's strong on.</p>
    <div class="kv">
      <div><span class="num">${esc(pa.map)}</span><span>${esc(a.name)}'s likeliest pick (${pct(pa.t1_pick_rate)})</span></div>
      <div><span class="num">${esc(pb.map)}</span><span>${esc(b.name)}'s likeliest pick (${pct(pb.t2_pick_rate)})</span></div>
      ${u.best_of > 1 ? `<div><span class="num">${esc(pd.map)}</span><span>likeliest decider (${pct(pd.dec)})</span></div>` : ""}
    </div>
    <div class="vetobars">${rows.map((m) => `<div class="vb-row">
      <span class="vb-map">${esc(m.map)}</span>
      <span class="vb-bar" role="img" aria-label="${esc(m.map)}: ${esc(a.name)} picks ${pct(m.t1_pick_rate)}, ${esc(b.name)} picks ${pct(m.t2_pick_rate)}, decider ${pct(m.dec)}, not played ${pct(1 - m.play_rate)}">
        ${seg(m.t1_pick_rate, "va", `${a.name} picks`)}${seg(m.t2_pick_rate, "vbb", `${b.name} picks`)}${seg(m.dec, "vd", "Decider")}${seg(1 - m.play_rate, "vn", "Not played")}</span>
      <span class="vb-p num">${pct(m.play_rate)}</span></div>`).join("")}</div>
    <div class="legend"><span><i class="sq" style="background:var(--team-a)"></i>${esc(a.name)} picks</span><span><i class="sq" style="background:var(--team-b)"></i>${esc(b.name)} picks</span><span><i class="sq" style="background:var(--ink-3)"></i>Decider</span><span><i class="sq" style="background:var(--rule-soft)"></i>Not played</span><span>Number: how often it's played at all</span></div>`;
}

// A few plain sentences that say what the numbers mean.
function storyline(u) {
  const a = T(u.t1), b = T(u.t2);
  const favA = u.p1 >= 0.5, fav = favA ? a : b, dog = favA ? b : a, pf = Math.max(u.p1, 1 - u.p1);
  const c = confidence(u.p1);
  const pts = [];
  const lead = { c0: `<b>Too close to call.</b> ${esc(fav.name)} ${pf >= 0.505 ? `edge it at ${pct(pf)}` : "and " + esc(dog.name) + " are level"}, which is barely better than a coin flip.`,
    c1: `<b>${esc(fav.name)} have a slight edge</b> at ${pct(pf)}. Expect ${esc(dog.name)} to win this about ${Math.round((1 - pf) * 10)} times in 10.`,
    c2: `<b>${esc(fav.name)} are favoured</b> at ${pct(pf)}, but an upset by ${esc(dog.name)} happens about ${Math.round((1 - pf) * 10)} times in 10.`,
    c3: `<b>${esc(fav.name)} are strong favourites</b> at ${pct(pf)}. ${esc(dog.name)} would need an upset that happens about 1 time in ${Math.max(2, Math.round(1 / (1 - pf)))}.` };
  pts.push(`${lead[c.cls]} Fair odds: ${esc(a.tag || a.name)} ${fairOdds(u.p1)}, ${esc(b.tag || b.name)} ${fairOdds(1 - u.p1)}.`);
  const top = Object.entries(u.scores || {}).sort((x, y) => y[1] - x[1])[0];
  if (top) {
    const [k, v] = top, [s1, s2] = k.split("-").map(Number);
    pts.push(`Most likely result: ${esc((s1 > s2 ? a : b).name)} ${Math.max(s1, s2)}–${Math.min(s1, s2)} (${pct(v)}).`);
  }
  const likely = (u.maps || []).filter((m) => m.play_rate >= 0.3);
  const edge = (m, forA) => `${esc(m.map)} (${pct(forA ? m.p_neutral : 1 - m.p_neutral)})`;
  const aM = likely.filter((m) => m.p_neutral >= 0.55).sort((x, y) => y.p_neutral - x.p_neutral).slice(0, 2);
  const bM = likely.filter((m) => m.p_neutral <= 0.45).sort((x, y) => x.p_neutral - y.p_neutral).slice(0, 2);
  if (!aM.length && !bM.length) pts.push("No big map edges: every map that's likely to be played is between 45% and 55%, so the veto matters less than usual.");
  else pts.push(`Map edges: ${joinList([aM.length ? `${esc(a.name)} on ${joinList(aM.map((m) => edge(m, true)))}` : "", bM.length ? `${esc(b.name)} on ${joinList(bM.map((m) => edge(m, false)))}` : ""].filter(Boolean))}.`);
  if (u.factors?.length) {
    const f = u.factors.slice().sort((x, y) => Math.abs(y.logit) - Math.abs(x.logit));
    const who = (d) => esc((d.logit > 0 ? a : b).name);
    if (Math.abs(f[0].logit) > 0.02) {
      let s = `The biggest driver is <i>${esc(f[0].label)}</i>, which favours ${who(f[0])}.`;
      const against = f.find((d) => Math.abs(d.logit) > 0.03 && (d.logit > 0) !== (f[0].logit > 0));
      if (against) s += ` Pulling the other way: <i>${esc(against.label)}</i> favours ${who(against)}.`;
      pts.push(s);
    }
  }
  if (u.components?.length) {
    const ps = u.components.map((d) => d.p1), lo = Math.min(...ps), hi = Math.max(...ps);
    if (hi - lo > 0.2) pts.push(`The component models disagree (from ${pct(lo)} to ${pct(hi)} for ${esc(a.name)}), so treat this forecast with extra caution.`);
    else if (hi - lo < 0.1) pts.push(`All component models agree within ${Math.round((hi - lo) * 100)} points, which makes this a steadier forecast.`);
  }
  if (u.h2h?.length) {
    const w = u.h2h.filter((h) => h.won).length;
    pts.push(`In their last ${u.h2h.length === 1 ? "meeting" : `${u.h2h.length} meetings`}, ${esc(a.name)} won ${w} and ${esc(b.name)} won ${u.h2h.length - w}. Old results already count through the ratings, so this isn't extra evidence.`);
  }
  return `<ul class="story">${pts.map((p) => `<li>${p}</li>`).join("")}</ul>`;
}

function drawMatchCharts(u) {
  const a = T(u.t1), b = T(u.t2);
  // score distribution: t1 wins on top in order of margin
  const order = Object.keys(u.scores).sort((x, y) => {
    const [x1, x2] = x.split("-").map(Number), [y1, y2] = y.split("-").map(Number);
    return (y1 - y2) - (x1 - x2);
  });
  const rows = order.map((k) => ({ k, v: u.scores[k], w1: +k.split("-")[0] > +k.split("-")[1] }));
  const scoreEl = document.getElementById("score-chart");
  const W = cw(scoreEl, 460), rowH = 34, H = rows.length * rowH, left = 130;
  const x = d3.scaleLinear().domain([0, d3.max(rows, (d) => d.v) * 1.15]).range([left, W - 50]);
  const svg = d3.select(scoreEl).append("svg").attr("viewBox", `0 0 ${W} ${H}`);
  const g = svg.selectAll("g").data(rows).join("g").attr("transform", (d, i) => `translate(0,${i * rowH})`);
  g.append("text").attr("x", left - 10).attr("y", rowH / 2 + 4).attr("text-anchor", "end").attr("class", "lbl")
    .text((d) => `${d.w1 ? a.tag || a.name : b.tag || b.name} ${d.w1 ? d.k : d.k.split("-").reverse().join("-")}`);
  g.append("rect").attr("x", left).attr("y", rowH / 2 - 7).attr("height", 14).attr("rx", 3)
    .attr("width", (d) => Math.max(2, x(d.v) - left)).attr("fill", (d) => d.w1 ? "var(--team-a)" : "var(--team-b)");
  g.append("text").attr("x", (d) => x(d.v) + 8).attr("y", rowH / 2 + 5).attr("class", "big").text((d) => pct(d.v));

  if (u.factors) {
    const f = u.factors.filter((d) => Math.abs(d.logit) > 0.001);
    const factorEl = document.getElementById("factor-chart");
    const FW = cw(factorEl, 460), fh = 30, FH = f.length * fh + 22;
    const lim = Math.max(0.3, d3.max(f, (d) => Math.abs(d.logit)));
    const fx = d3.scaleLinear().domain([lim, -lim]).range([10, FW - 10]);
    const fs = d3.select(factorEl).append("svg").attr("viewBox", `0 0 ${FW} ${FH}`);
    fs.append("line").attr("x1", fx(0)).attr("x2", fx(0)).attr("y1", 0).attr("y2", FH - 18).attr("stroke", "var(--ink-3)");
    const fg = fs.selectAll("g").data(f).join("g").attr("transform", (d, i) => `translate(0,${i * fh})`);
    fg.append("rect").attr("y", 8).attr("height", 12).attr("rx", 3)
      .attr("x", (d) => Math.min(fx(0), fx(d.logit))).attr("width", (d) => Math.max(2, Math.abs(fx(d.logit) - fx(0))))
      .attr("fill", (d) => d.logit > 0 ? "var(--team-a)" : "var(--team-b)");
    fg.append("text").attr("y", 18).attr("x", (d) => d.logit > 0 ? fx(0) + 8 : fx(0) - 8)
      .attr("text-anchor", (d) => d.logit > 0 ? "start" : "end").text((d) => d.label);
    fg.append("rect").attr("width", FW).attr("height", fh).attr("fill", "transparent")
      .on("mousemove", (ev, d) => tip(`<b>${esc(d.label)}</b><br>${d.logit > 0 ? esc(a.name) : esc(b.name)} +${Math.abs(d.logit).toFixed(2)} log-odds per map`, ev))
      .on("mouseleave", () => tip(null));
    fs.append("text").attr("x", 10).attr("y", FH - 2).text(`← ${a.tag || a.name}`);
    fs.append("text").attr("x", FW - 10).attr("y", FH - 2).attr("text-anchor", "end").text(`${b.tag || b.name} →`);
  }
  poolChart(document.getElementById("pool-chart"), [a, b]);
}

function poolChart(el, teams) {
  const maps = DATA.meta.pool;
  const W = cw(el, 760), rowH = 34, left = 100, H = maps.length * rowH + 26;
  const vals = teams.flatMap((t) => t.maps.map((m) => m.strength)).filter((v) => v != null);
  // fit the domain to the data (top teams all sit well above 50%), but never narrower than 20 points
  let lo = d3.min(vals) - 0.03, hi = d3.max(vals) + 0.03;
  if (hi - lo < 0.2) { const mid = (hi + lo) / 2; lo = mid - 0.1; hi = mid + 0.1; }
  const x = d3.scaleLinear().domain([Math.max(0, lo), Math.min(1, hi)]).range([left, W - 20]);
  const svg = d3.select(el).append("svg").attr("viewBox", `0 0 ${W} ${H}`).attr("role", "img").attr("aria-label", "Map strength comparison");
  svg.append("g").attr("class", "grid").selectAll("line").data(x.ticks(6)).join("line")
    .attr("x1", x).attr("x2", x).attr("y1", 0).attr("y2", H - 22);
  if (lo < 0.5 && hi > 0.5) svg.append("line").attr("x1", x(.5)).attr("x2", x(.5)).attr("y1", 0).attr("y2", H - 22).attr("stroke", "var(--ink-3)");
  svg.append("g").selectAll("text").data(x.ticks(6)).join("text").attr("x", x).attr("y", H - 6).attr("text-anchor", "middle").text((d) => pct(d));
  const g = svg.selectAll("g.row").data(maps).join("g").attr("class", "row").attr("transform", (d, i) => `translate(0,${i * rowH})`);
  g.append("text").attr("x", left - 12).attr("y", rowH / 2 + 4).attr("text-anchor", "end").attr("class", "lbl").text((d) => d);
  g.each(function (mp) {
    const row = d3.select(this);
    const pts = teams.map((t, i) => ({ t, i, m: t.maps.find((m) => m.map === mp) })).filter((d) => d.m && d.m.strength != null);
    if (pts.length === 2) row.append("line").attr("x1", x(pts[0].m.strength)).attr("x2", x(pts[1].m.strength))
      .attr("y1", rowH / 2).attr("y2", rowH / 2).attr("stroke", "var(--rule)").attr("stroke-width", 2);
    pts.forEach(({ t, i, m }) => {
      const cx = x(m.strength), cy = rowH / 2;
      const mark = i === 0
        ? row.append("circle").attr("cx", cx).attr("cy", cy).attr("r", 6.5).attr("fill", "var(--team-a)")
        : row.append("rect").attr("x", cx - 6).attr("y", cy - 6).attr("width", 12).attr("height", 12).attr("rx", 1.5).attr("fill", "var(--team-b)");
      mark.attr("stroke", "var(--bg)").attr("stroke-width", 2);
    });
    row.append("rect").attr("width", W).attr("height", rowH).attr("fill", "transparent")
      .on("mousemove", (ev) => tip(`<b>${esc(mp)}</b><br>${pts.map(({ t, m }) => `${esc(t.name)}: ${pct(m.strength)} vs field, ${m.wins}–${m.played - m.wins} last 12 months`).join("<br>")}`, ev))
      .on("mouseleave", () => tip(null));
  });
}

function recentTable(rows) {
  if (!rows.length) return `<p class="note">No recent series.</p>`;
  return `<div class="table-wrap"><table><tbody>${rows.map((r) => `<tr>
    <td class="small">${fmtDate(r.date)}</td><td>${teamLink(T(r.opp))}</td>
    <td class="r ${r.won ? "w" : "l"}">${r.won ? "W" : "L"} ${r.score}</td></tr>`).join("")}</tbody></table></div>`;
}

// ------------------------------------------------------------------ views: champions
function champions() {
  const tr = DATA.tournament;
  const groupOf = {};
  Object.entries(tr.groups).forEach(([g, ids]) => ids.forEach((id) => (groupOf[id] = g)));
  const cols = [["playoffs", "Playoffs"], ["top6", "Top 6"], ["top4", "Top 4"], ["final", "Final"], ["champion", "Champion"]];
  const resolved = tr.results.filter((r) => r.p1 != null);
  const hits = resolved.filter((r) => (r.p1 >= .5) === (r.win1 === 1)).length;
  main().innerHTML = `
    <section class="page-head">
      <h1>Valorant Champions 2026</h1>
      <p class="lede">Every team's chance of reaching each stage. We play out the remaining bracket ${tr.sims.toLocaleString()} times, simulating each series' map veto and maps. Results already played are locked in.</p>
    </section>
    <div class="table-wrap"><table id="tour-table">
      <thead><tr><th>Team</th><th>Group</th><th class="r">Wins group</th>${cols.map(([, l]) => `<th class="r">${l}</th>`).join("")}</tr></thead>
      <tbody>${tr.teams.map((d) => { const t = T(d.team_id); return `<tr class="link-row" data-href="#/team/${t.id}">
        <td><div class="team-cell">${logo(t, "sm")}${teamLink(t)} <span class="tag">${esc(t.region)}</span></div></td>
        <td>${groupOf[t.id] || ""}</td>
        <td class="heat" ${heat(d.group_1st)}>${pct(d.group_1st)}</td>
        ${cols.map(([k]) => `<td class="heat" ${heat(d[k])}>${pct(d[k], d[k] < .1 ? 1 : 0)}</td>`).join("")}</tr>`; }).join("")}
      </tbody></table></div>
    <p class="small">Shading darkens with probability. Group winners are assumed to meet runners-up from another group in the upper quarterfinals until the real draw is published.</p>

    ${X.bracketSection()}

    <h2>Groups</h2>
    <div class="cols">${Object.entries(tr.groups).map(([g, ids]) => `<div>
      <h3>Group ${g}</h3>
      <table><thead><tr><th>Team</th><th class="r">Advance</th><th class="r">1st</th></tr></thead><tbody>
      ${ids.map((id) => tr.teams.find((x) => x.team_id === id)).filter(Boolean).sort((x, y) => y.playoffs - x.playoffs)
        .map((d) => `<tr><td><div class="team-cell">${logo(T(d.team_id), "sm")}${teamLink(T(d.team_id))}</div></td><td class="heat" ${heat(d.playoffs)}>${pct(d.playoffs)}</td><td class="heat" ${heat(d.group_1st)}>${pct(d.group_1st)}</td></tr>`).join("")}
      </tbody></table></div>`).join("")}</div>

    <h2>Results so far</h2>
    ${resolved.length ? `<p class="note">Our pre-match favourite won ${hits} of ${resolved.length} series at this event.</p>
    <div class="table-wrap"><table><thead><tr><th>Date</th><th>Stage</th><th>Series</th><th class="r">Our pre-match odds</th><th class="r">Result</th></tr></thead><tbody>
    ${resolved.slice().reverse().map((r) => { const a = T(r.t1), b = T(r.t2); const ok = (r.p1 >= .5) === (r.win1 === 1);
      return `<tr><td class="small">${fmtDate(r.date)}</td><td class="small">${esc(cleanSeries(r.series).split(": ").pop())}</td>
      <td>${esc(a.name)} vs ${esc(b.name)}</td><td class="r num">${pct(r.p1)} / ${pct(1 - r.p1)}</td>
      <td class="r"><span class="${ok ? "w" : "l"}">${r.score}</span> <span class="pill">${ok ? "called" : "upset"}</span></td></tr>`; }).join("")}
    </tbody></table></div>` : `<p class="note">No series completed yet.</p>`}`;
  bindRowLinks();
  X.drawPlacements();
}

function bindRowLinks() {
  document.querySelectorAll("tr.link-row").forEach((tr) => tr.addEventListener("click", (e) => {
    if (e.target.closest("a")) return;
    location.hash = tr.dataset.href;
  }));
}

// ------------------------------------------------------------------ views: rankings
const REGION_ORDER = ["All", "Americas", "EMEA", "Pacific", "China"];
let rankState = { region: "All", sort: "power", dir: -1 };

function rankings() {
  const cols = [
    ["rank", "#", false], ["name", "Team", false], ["power", "Map win % vs field", true], ["map_elo", "Map Elo", true],
    ["player_elo", "Roster Elo", true], ["atk", "Attack", true], ["def", "Defence", true], ["form", "Form (R2.0)", true], ["recent", "Last 10", false],
  ];
  const val = (t, k) => (k === "form" ? t.form.rating : t[k]);
  let rows = DATA.teams.filter((t) => rankState.region === "All" || t.region === rankState.region);
  rows = rows.slice().sort((a, b) => rankState.dir * ((val(a, rankState.sort) ?? -1e9) - (val(b, rankState.sort) ?? -1e9)));
  main().innerHTML = `
    <section class="page-head">
      <h1>Power rankings</h1>
      <p class="lede">Teams ranked by expected map win rate against every other active tier-one team, averaged over the current seven-map pool. Unlike series records, this accounts for opponent strength, map pools and roster changes.</p>
    </section>
    <div class="controls">
      <div class="seg" role="group" aria-label="Region">${REGION_ORDER.map((r) => `<button type="button" data-region="${r}" aria-pressed="${r === rankState.region}">${r}</button>`).join("")}</div>
    </div>
    <div class="table-wrap"><table>
      <thead><tr>${cols.map(([k, l, s]) => s
        ? `<th class="r" ${rankState.sort === k ? `aria-sort="${rankState.dir < 0 ? "descending" : "ascending"}"` : ""}><button data-sort="${k}">${l}</button></th>`
        : `<th>${l}</th>`).join("")}</tr></thead>
      <tbody>${rows.map((t) => `<tr class="link-row" data-href="#/team/${t.id}">
        <td class="rank">${t.rank}</td>
        <td><div class="team-cell">${logo(t, "sm")}${teamLink(t)} <span class="tag">${esc(t.region)}</span></div></td>
        <td class="r"><span class="inbar"><span style="width:${Math.max(2, Math.min(100, (t.power - .25) / .5 * 100))}%"></span></span><span class="num" style="font-size:17px">${pct(t.power, 1)}</span></td>
        <td class="r num">${t.map_elo}</td><td class="r num">${t.player_elo}</td>
        <td class="r num">${fmtSigned(t.atk)}</td><td class="r num">${fmtSigned(t.def)}</td>
        <td class="r num">${t.form.rating?.toFixed(2)}</td>
        <td>${formStrip(t.recent.slice(0, 10))}</td></tr>`).join("")}
      </tbody></table></div>
    <p class="small">Attack and defence are round-model strengths in log-odds relative to an average team: +0.10 is roughly 2.5 extra percentage points of rounds won on that side.</p>
    <h2>Rating over time</h2>
    <p class="note">Map Elo for the top six teams in this view. Hover to compare on any date.</p>
    <div class="chart" id="elo-chart"></div><div class="legend" id="elo-legend"></div>
    <h2>Region strength</h2>
    <p class="note">How each region compares, learned only from international maps (in Elo points; +100 means an average team from that region beats an average team from a 0 region about 64% of the time). It moves only when regions meet, at Masters and Champions.</p>
    <div class="chart" id="region-chart"></div><div class="legend" id="region-legend"></div>`;
  document.querySelectorAll("[data-region]").forEach((b) => b.addEventListener("click", () => { rankState.region = b.dataset.region; rankings(); }));
  document.querySelectorAll("[data-sort]").forEach((b) => b.addEventListener("click", () => {
    const k = b.dataset.sort;
    rankState.dir = rankState.sort === k ? -rankState.dir : -1;
    rankState.sort = k;
    rankings();
  }));
  bindRowLinks();
  const top = rows.slice().sort((a, b) => b.power - a.power).slice(0, 6);
  const palette = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300"];
  const paletteDark = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300"];
  const dark = document.documentElement.dataset.theme === "dark" ||
    (!document.documentElement.dataset.theme && matchMedia("(prefers-color-scheme: dark)").matches);
  // colour follows the team (stable by id order), not its rank in the current view
  const pal = dark ? paletteDark : palette;
  const byId = top.map((t) => t.id).sort((a, b) => a - b);
  lineChart(document.getElementById("elo-chart"), top.map((t) => ({
    name: t.name, color: pal[byId.indexOf(t.id)], values: t.elo_history.map(([d, v]) => [new Date(d), v]),
  })), { legend: document.getElementById("elo-legend"), yLabel: "Map Elo" });
  X.regionChart(document.getElementById("region-chart"));
}

const fmtSigned = (v) => (v == null ? "–" : `${v >= 0 ? "+" : "−"}${Math.abs(v).toFixed(2)}`);
function formStrip(rows) {
  return `<span style="display:inline-flex;gap:2px" aria-label="${rows.filter((r) => r.won).length} wins in last ${rows.length}">${rows.slice().reverse().map((r) =>
    `<span title="${r.won ? "Won" : "Lost"} ${r.score} vs ${esc(T(r.opp).name)}" style="width:7px;height:16px;border-radius:2px;background:${r.won ? "var(--good)" : "var(--rule)"}"></span>`).join("")}</span>`;
}

// ------------------------------------------------------------------ views: team
function team(id) {
  const t = DATA.team.get(+id);
  if (!t) { main().innerHTML = `<div class="error"><h1>Team not found</h1><p class="lede">Only teams active in tier-one play in the last ten months have profiles. <a href="#/rankings">Browse rankings</a></p></div>`; return; }
  const maps = t.maps.slice().sort((a, b) => b.strength - a.strength);
  const up = nextMatches().filter((u) => u.t1 === t.id || u.t2 === t.id);
  main().innerHTML = `
    <section class="page-head">
      <div style="display:flex;align-items:center;gap:16px">${logo(t)}<h1>${esc(t.name)}</h1></div>
      <p class="lede">${esc(t.region)}. Ranked ${t.rank} of ${DATA.teams.length} active tier-one teams. Last played ${fmtDate(t.last_played)}.</p>
      <div class="kv">
        <div><span class="num">${pct(t.power, 1)}</span><span>expected map win rate vs field</span></div>
        <div><span class="num">${t.map_elo}</span><span>map Elo</span></div>
        <div><span class="num">${t.player_elo}</span><span>roster Elo (current five)</span></div>
        <div><span class="num">${t.form.rating?.toFixed(2)}</span><span>roster Rating 2.0 form</span></div>
      </div>
    </section>
    ${up.length ? `<h2>Next series</h2><div class="slate">${up.map(fixtureRow).join("")}</div>` : ""}
    <h2>Map pool</h2>
    <p class="note">Expected win rate on each map against the average active tier-one team, next to what actually happened over the last 12 months.</p>
    <div class="cols two">
      <div><div class="chart" id="team-pool"></div>
        <div class="legend"><span><i class="dot" style="background:var(--team-a)"></i>Model expectation</span><span><i class="dot" style="background:transparent;border:1.5px solid var(--ink-2)"></i>Actual win rate, sized by maps played</span></div>
        <p class="small">The model deliberately trusts small samples less than raw records do, which is why expectations sit closer together.</p></div>
      <div class="table-wrap"><table><thead><tr><th>Map</th><th class="r">Record</th><th class="r">Picked</th><th class="r">Banned</th></tr></thead><tbody>
        ${maps.map((m) => `<tr><td>${esc(m.map)}</td><td class="r num">${m.wins}–${m.played - m.wins}</td>
          <td class="r">${m.picks}</td><td class="r">${m.vetoes ? pct(m.bans / m.vetoes) : "–"}</td></tr>`).join("")}
      </tbody></table><p class="small">Banned is the share of this team's vetoes in which it banned the map.</p></div>
    </div>
    <h2>Attack and defence by map</h2>
    <p class="note">Share of rounds won on each side over the last 12 months. Grey outlines mark the league average for the same side on that map.</p>
    <div class="chart" id="team-sides"></div>
    <div class="legend"><span><i class="dot" style="background:var(--atk)"></i>Attack (circle)</span><span><i class="sq" style="background:var(--def)"></i>Defence (square)</span><span><i class="dot" style="background:transparent;border:1.5px solid var(--ink-3)"></i><i class="sq" style="background:transparent;border:1.5px solid var(--ink-3)"></i>League average</span></div>
    ${X.teamPanels(t)}
    <h2>Current roster</h2>
    <p class="note">The five players from the team's most recent series. Form stats are recency-weighted averages, shrunk toward league average for players with few maps.</p>
    <div class="table-wrap"><table><thead><tr><th>Player</th><th class="r">Player Elo</th><th class="r">Rating 2.0</th><th class="r">ACS</th><th class="r">KAST</th><th class="r">ADR</th><th class="r">FK−FD / map</th><th>Main agents</th></tr></thead><tbody>
      ${t.roster.map((p) => `<tr><td><a href="https://www.vlr.gg/player/${p.id}">${esc(p.name)}</a> <span class="small">${p.maps} maps</span></td>
        <td class="r num">${p.elo}</td><td class="r num">${p.rating?.toFixed(2)}</td><td class="r">${p.acs}</td><td class="r">${p.kast}%</td>
        <td class="r">${p.adr}</td><td class="r">${fmtSigned(p.fkd)}</td><td class="small">${p.agents.map(esc).join(", ")}</td></tr>`).join("")}
    </tbody></table></div>
    <div class="cols block">
      <div><h2 style="margin-top:0">Rating history</h2><div class="chart" id="team-elo"></div></div>
      <div><h2 style="margin-top:0">Recent series</h2>${recentTable(t.recent)}</div>
    </div>`;
  teamPoolChart(document.getElementById("team-pool"), maps);
  sidesChart(document.getElementById("team-sides"), t);
  X.drawTeamPanels(t);
  lineChart(document.getElementById("team-elo"), [{ name: t.name, color: "var(--team-a)", values: t.elo_history.map(([d, v]) => [new Date(d), v]) }], { yLabel: "Map Elo" });
}

function teamPoolChart(el, maps) {
  const W = cw(el, 520), rowH = 34, left = 80, H = maps.length * rowH + 24;
  const x = d3.scaleLinear().domain([0, 1]).range([left, W - 16]);
  const svg = d3.select(el).append("svg").attr("viewBox", `0 0 ${W} ${H}`).attr("role", "img").attr("aria-label", "Expected and actual map win rates");
  svg.append("g").attr("class", "grid").selectAll("line").data(x.ticks(5)).join("line").attr("x1", x).attr("x2", x).attr("y1", 0).attr("y2", H - 20);
  svg.append("g").selectAll("text").data(x.ticks(5)).join("text").attr("x", x).attr("y", H - 4).attr("text-anchor", "middle").text((d) => pct(d));
  svg.append("line").attr("x1", x(.5)).attr("x2", x(.5)).attr("y1", 0).attr("y2", H - 20).attr("stroke", "var(--ink-3)");
  const g = svg.selectAll("g.r").data(maps).join("g").attr("class", "r").attr("transform", (d, i) => `translate(0,${i * rowH})`);
  g.append("text").attr("x", left - 10).attr("y", rowH / 2 + 4).attr("text-anchor", "end").attr("class", "lbl").text((d) => d.map);
  const act = (d) => (d.played ? d.wins / d.played : null);
  g.filter((d) => act(d) != null).append("line").attr("x1", (d) => x(d.strength)).attr("x2", (d) => x(act(d)))
    .attr("y1", rowH / 2).attr("y2", rowH / 2).attr("stroke", "var(--rule)").attr("stroke-width", 2);
  g.filter((d) => act(d) != null).append("circle").attr("cx", (d) => x(act(d))).attr("cy", rowH / 2).attr("r", (d) => 3 + Math.min(6, Math.sqrt(d.played)))
    .attr("fill", "none").attr("stroke", "var(--ink-2)").attr("stroke-width", 1.5);
  g.append("circle").attr("cx", (d) => x(d.strength)).attr("cy", rowH / 2).attr("r", 6.5).attr("fill", "var(--team-a)").attr("stroke", "var(--bg)").attr("stroke-width", 2);
  g.append("rect").attr("width", W).attr("height", rowH).attr("fill", "transparent")
    .on("mousemove", (ev, d) => tip(`<b>${esc(d.map)}</b><br>Model expects ${pct(d.strength)} vs an average team<br>${d.played ? `Actually won ${d.wins} of ${d.played} (${pct(act(d))}) in the last 12 months` : "Not played in the last 12 months"}`, ev))
    .on("mouseleave", () => tip(null));
}

function sidesChart(el, t) {
  const avg = Object.fromEntries((DATA.backtest.map_sides || []).map((m) => [m.map, m.atk_rw]));
  const maps = t.maps.filter((m) => m.atk_rw != null && m.def_rw != null);
  if (!maps.length) { el.innerHTML = `<p class="note">No rounds on the current pool in the last 12 months.</p>`; return; }
  const W = cw(el, 760), rowH = 34, left = 100, H = maps.length * rowH + 24;
  const x = d3.scaleLinear().domain([0.25, 0.75]).range([left, W - 20]);
  const svg = d3.select(el).append("svg").attr("viewBox", `0 0 ${W} ${H}`).attr("role", "img").attr("aria-label", "Attack and defence round win rates by map");
  svg.append("g").attr("class", "grid").selectAll("line").data(x.ticks(5)).join("line").attr("x1", x).attr("x2", x).attr("y1", 0).attr("y2", H - 20);
  svg.append("g").selectAll("text").data(x.ticks(5)).join("text").attr("x", x).attr("y", H - 4).attr("text-anchor", "middle").text((d) => pct(d));
  const g = svg.selectAll("g.r").data(maps).join("g").attr("class", "r").attr("transform", (d, i) => `translate(0,${i * rowH})`);
  g.append("text").attr("x", left - 12).attr("y", rowH / 2 + 4).attr("text-anchor", "end").attr("class", "lbl").text((d) => d.map);
  g.append("line").attr("x1", (d) => x(d.atk_rw)).attr("x2", (d) => x(d.def_rw)).attr("y1", rowH / 2).attr("y2", rowH / 2).attr("stroke", "var(--rule)").attr("stroke-width", 2);
  g.filter((d) => avg[d.map] != null).each(function (d) {
    const r = d3.select(this);
    r.append("circle").attr("cx", x(avg[d.map])).attr("cy", rowH / 2).attr("r", 6).attr("fill", "none").attr("stroke", "var(--ink-3)").attr("stroke-width", 1.5);
    r.append("rect").attr("x", x(1 - avg[d.map]) - 5.5).attr("y", rowH / 2 - 5.5).attr("width", 11).attr("height", 11).attr("fill", "none").attr("stroke", "var(--ink-3)").attr("stroke-width", 1.5);
  });
  g.append("circle").attr("cx", (d) => x(d.atk_rw)).attr("cy", rowH / 2).attr("r", 6.5).attr("fill", "var(--atk)").attr("stroke", "var(--bg)").attr("stroke-width", 2);
  g.append("rect").attr("x", (d) => x(d.def_rw) - 6).attr("y", rowH / 2 - 6).attr("width", 12).attr("height", 12).attr("rx", 1.5).attr("fill", "var(--def)").attr("stroke", "var(--bg)").attr("stroke-width", 2);
  g.append("rect").attr("width", W).attr("height", rowH).attr("fill", "transparent")
    .on("mousemove", (ev, d) => tip(`<b>${esc(d.map)}</b><br>Attack ${pct(d.atk_rw, 1)} of rounds (league ${pct(avg[d.map], 1)})<br>Defence ${pct(d.def_rw, 1)} of rounds (league ${pct(1 - avg[d.map], 1)})`, ev))
    .on("mouseleave", () => tip(null));
}

// ------------------------------------------------------------------ views: matchup lab
let labState = { a: null, b: null, bo: 3 };
// #/lab/<team one>-<team two>-<best of> so a matchup can be shared or bookmarked
function lab(arg) {
  const [pa, pb, pbo] = (arg || "").split("-").map(Number);
  if (DATA.team.has(pa) && DATA.team.has(pb)) Object.assign(labState, { a: pa, b: pb });
  if ([1, 3, 5].includes(pbo)) labState.bo = pbo;
  if (!labState.a) { const s = DATA.teams.slice().sort((x, y) => x.rank - y.rank); labState.a = s[0].id; labState.b = s[1].id; }
  const byRegion = d3.groups(DATA.teams.slice().sort((x, y) => x.rank - y.rank), (t) => t.region)
    .sort((x, y) => REGION_ORDER.indexOf(x[0]) - REGION_ORDER.indexOf(y[0]));
  const opt = (sel) => byRegion.map(([r, ts]) => `<optgroup label="${esc(r)}">${ts.map((t) =>
    `<option value="${t.id}" ${t.id === sel ? "selected" : ""}>${esc(t.name)} (#${t.rank})</option>`).join("")}</optgroup>`).join("");
  main().innerHTML = `
    <section class="page-head">
      <h1>Matchup lab</h1>
      <p class="lede">Pick any two active tier-one teams and a format. The page simulates 4,000 map vetoes using each team's real ban and pick habits, then plays out the maps. Teams are listed by region and power ranking.</p>
      <div class="controls">
        <label class="field">Team one<select id="lab-a">${opt(labState.a)}</select></label>
        <button type="button" class="btn" id="lab-swap" aria-label="Swap teams" title="Swap teams">⇄</button>
        <label class="field">Team two<select id="lab-b">${opt(labState.b)}</select></label>
        <div class="field" style="font-size:13px;color:var(--ink-2)">Format<div class="seg" role="group" aria-label="Format">${[1, 3, 5].map((n) => `<button type="button" data-bo="${n}" aria-pressed="${n === labState.bo}">Bo${n}</button>`).join("")}</div></div>
        <button type="button" class="btn" id="lab-copy">Copy link</button>
      </div>
    </section>
    <div id="lab-out"><p class="loading">Simulating…</p></div>`;
  const rerun = () => {
    history.replaceState(null, "", `#/lab/${labState.a}-${labState.b}-${labState.bo}`);
    runLab();
  };
  document.getElementById("lab-a").addEventListener("change", (e) => { labState.a = +e.target.value; rerun(); });
  document.getElementById("lab-b").addEventListener("change", (e) => { labState.b = +e.target.value; rerun(); });
  document.getElementById("lab-swap").addEventListener("click", () => {
    [labState.a, labState.b] = [labState.b, labState.a];
    document.getElementById("lab-a").value = labState.a;
    document.getElementById("lab-b").value = labState.b;
    rerun();
  });
  document.getElementById("lab-copy").addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(location.href); toast("Link copied"); } catch { toast("Copy the address bar to share this matchup"); }
  });
  document.querySelectorAll("[data-bo]").forEach((b) => b.addEventListener("click", () => {
    labState.bo = +b.dataset.bo;
    document.querySelectorAll("[data-bo]").forEach((x) => x.setAttribute("aria-pressed", String(+x.dataset.bo === labState.bo)));
    rerun();
  }));
  rerun();
}

async function runLab() {
  const out = document.getElementById("lab-out");
  if (labState.a === labState.b) { out.innerHTML = `<p class="note">Choose two different teams.</p>`; return; }
  if (!DATA.matchup) DATA.matchup = await get("matchup");
  const sim = vetoSim(DATA.matchup, labState.a, labState.b, labState.bo, 4000);
  const u = { t1: labState.a, t2: labState.b, p1: sim.p_series, best_of: labState.bo, scores: sim.scores, maps: sim.maps, top_vetoes: sim.top_vetoes };
  const next = nextMatches().find((x) => (x.t1 === u.t1 && x.t2 === u.t2) || (x.t1 === u.t2 && x.t2 === u.t1));
  out.innerHTML = `<section class="hero" style="border-bottom:0">${duel(u)}
    ${storyline(u)}
    <p class="note">Chance to win a best-of-${labState.bo}, including the veto. Neutral venue, current rosters.${next ? ` These teams are scheduled to meet: <a href="#/match/${next.match_id}">see the full forecast</a>.` : ""}</p></section>${matchBody(u)}`;
  drawMatchCharts(u);
}

// ------------------------------------------------------------------ quick search
// Press "/" (or the search button) to jump to a team or an upcoming series.
function setupSearch() {
  const btn = document.querySelector(".search-btn"), dlg = document.getElementById("search");
  if (!btn || !dlg) return;
  const input = dlg.querySelector("input"), list = dlg.querySelector(".search-results");
  let items = [], sel = 0;
  const norm = (s) => String(s || "").normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase();
  const render = () => {
    const q = norm(input.value.trim());
    const teams = DATA.teams.filter((t) => !q || norm(t.name).includes(q) || norm(t.tag) === q || norm(t.tag).startsWith(q))
      .sort((x, y) => x.rank - y.rank).slice(0, q ? 8 : 6)
      .map((t) => ({ href: `#/team/${t.id}`, html: `${logo(t, "sm")}<span><b>${esc(t.name)}</b> <span class="small">${esc(t.region)}, #${t.rank}</span></span>` }));
    const games = nextMatches().filter((u) => q && (norm(T(u.t1).name).includes(q) || norm(T(u.t2).name).includes(q) || norm(T(u.t1).tag) === q || norm(T(u.t2).tag) === q)).slice(0, 5)
      .map((u) => ({ href: `#/match/${u.match_id}`, html: `<span class="small">${fmtDate(u.date)}</span><span><b>${esc(T(u.t1).name)}</b> ${pct(u.p1)} vs ${pct(1 - u.p1)} <b>${esc(T(u.t2).name)}</b></span>` }));
    items = [...games, ...teams];
    sel = 0;
    list.innerHTML = items.length ? `${games.length ? `<li class="search-h">Upcoming series</li>` : ""}${games.map((it, i) => `<li><a href="${it.href}" data-i="${i}">${it.html}</a></li>`).join("")}
      <li class="search-h">${q ? "Teams" : "Top teams"}</li>${teams.map((it, i) => `<li><a href="${it.href}" data-i="${i + games.length}">${it.html}</a></li>`).join("")}`
      : `<li class="search-h">No team matches “${esc(input.value)}”.</li>`;
    mark();
  };
  const mark = () => list.querySelectorAll("a").forEach((a) => a.classList.toggle("sel", +a.dataset.i === sel));
  const open = () => { input.value = ""; render(); dlg.showModal(); input.focus(); };
  btn.addEventListener("click", open);
  input.addEventListener("input", render);
  input.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") { e.preventDefault(); sel = (sel + (e.key === "ArrowDown" ? 1 : -1) + items.length) % Math.max(1, items.length); mark(); }
    if (e.key === "Enter" && items[sel]) { location.hash = items[sel].href; dlg.close(); }
  });
  list.addEventListener("click", (e) => { if (e.target.closest("a")) dlg.close(); });
  dlg.addEventListener("click", (e) => { if (e.target === dlg) dlg.close(); });
  document.addEventListener("keydown", (e) => {
    if (e.key === "/" && !dlg.open && !e.target.closest("input, select, textarea")) { e.preventDefault(); open(); }
  });
}

// ------------------------------------------------------------------ views: model
function model() {
  const bt = DATA.backtest, m = DATA.meta;
  const S = bt.series, Mp = bt.maps;
  const best = S["Ensemble + veto sim (pre-veto)"];
  const row = (name, v, hl) => `<tr${hl ? ' style="font-weight:600"' : ""}><td>${esc(name)}</td><td class="r num">${v.log_loss.toFixed(4)}</td><td class="r num">${v.brier.toFixed(4)}</td><td class="r num">${pct(v.accuracy, 1)}</td><td class="r">${v.n.toLocaleString()}</td></tr>`;
  const w = bt.veto_weights;
  main().innerHTML = `
    <section class="page-head">
      <h1>How the forecasts work</h1>
      <p class="lede">Six signals, each built to catch something the others miss, are stacked into one map-level forecast. A veto simulator then turns map forecasts into series odds. Every number on this page comes from out-of-sample testing: each prediction used only matches played before it.</p>
      <div class="kv">
        <div><span class="num">${pct(best.accuracy, 1)}</span><span>series favourites that won, since ${fmtDate(bt.eval_start)}</span></div>
        <div><span class="num">${best.log_loss.toFixed(3)}</span><span>series log loss (coin flip is 0.693)</span></div>
        <div><span class="num">${best.n.toLocaleString()}</span><span>series forecast out of sample</span></div>
        <div><span class="num">${m.counts.rounds.toLocaleString()}</span><span>rounds in the round model</span></div>
      </div>
    </section>

    <h2>Backtest</h2>
    <p class="note">Walk-forward test on every tier-one series from ${fmtDate(bt.eval_start)} to ${fmtDate(m.data_through)}. Log loss and Brier score reward confident correct forecasts and punish confident misses, so lower is better. Accuracy only counts whether the favourite won.</p>
    <div class="cols">
      <div><h3>Series, forecast before the veto</h3><div class="table-wrap"><table><thead><tr><th>Model</th><th class="r">Log loss</th><th class="r">Brier</th><th class="r">Accuracy</th><th class="r">Series</th></tr></thead>
        <tbody>${Object.entries(S).map(([k, v]) => row(k, v, k.startsWith("Ensemble"))).join("")}
        ${bt.series_postveto ? row("Ensemble, after the veto is known", bt.series_postveto, false) : ""}</tbody></table></div></div>
      <div><h3>Individual maps</h3><div class="table-wrap"><table><thead><tr><th>Model</th><th class="r">Log loss</th><th class="r">Brier</th><th class="r">Accuracy</th><th class="r">Maps</th></tr></thead>
        <tbody>${Object.entries(Mp).map(([k, v]) => row(k, v, k === "Stacked ensemble")).join("")}</tbody></table></div></div>
    </div>

    <div class="cols block">
      <div><h3>Calibration</h3><p class="note">When the model says 70%, does the favourite win about 70% of the time? Dots on the diagonal mean honest probabilities. Dot size shows how many series fall in each bin.</p>
        <div class="chart" id="calib"></div>
        <div class="legend"><span><i class="dot" style="background:var(--ink)"></i>Ensemble (filled)</span><span><i class="dot" style="background:transparent;border:2px solid var(--ink-3)"></i>Series Elo (hollow)</span></div></div>
      <div><h3>Accuracy over time</h3><p class="note">Series log loss by quarter. Lower is better.</p>
        <div class="chart" id="ll-time"></div><div class="legend" id="ll-legend"></div></div>
    </div>

    <div id="model-extra"></div>
    <h2>The models</h2>
    <div class="method">
      <p><strong>1. Map Elo.</strong> A team rating updated after every map, with the margin of victory (log of the round differential) scaling the update and FiveThirtyEight-style damping so blowouts by favourites don't inflate ratings. Ratings regress toward average between seasons and when a team replaces players. We also tried a separate Elo offset per team per map; tuning drove its weight to zero because it was too noisy, so map-specific strength comes from the round model instead.</p>
      <p><strong>2. Round model.</strong> A time-decayed Bradley–Terry regression over ${m.counts.rounds.toLocaleString()} individual rounds. Each team gets an attack and a defence strength, plus a shrunken adjustment per map, and each map gets an attacker-side bias:</p>
      <div class="formula">logit P(attacker wins round) = μ + side<sub>map</sub> + atk<sub>A</sub> + atk<sub>A,map</sub> − def<sub>B</sub> − def<sub>B,map</sub></div>
      <p>Round probabilities are converted to a map win probability exactly, playing out 12 rounds per half and overtime pairs with a win-by-two rule. This captures how a small per-round edge compounds: winning 55% of rounds on both sides is worth about a 70% map win chance. Treating rounds as independent turned out to be about three times overconfident, because economy and momentum link consecutive rounds, so team edges are shrunk by a factor of 0.4 before the conversion. That factor was fitted on 2024 data and brings the model's calibration slope from 0.35 to 0.9.</p>
      <p><strong>3. Roster Elo.</strong> Every player carries a rating; a lineup's strength is the average of its five. Players share credit for map results. (A bonus for out-performing the lobby in Rating 2.0 made forecasts worse in tuning, so it is switched off; individual stats enter through player form instead.) Because the rating follows the player, transfers and roster moves are handled automatically.</p>
      <p><strong>4. Player form.</strong> Recency-weighted Rating 2.0 and opening-duel differential for the current five, shrunk toward league average for players with few maps.</p>
      <p><strong>5. Series Elo.</strong> A plain series-result Elo, shown as the baseline to beat. It is left out of the stack: it overlaps almost entirely with Map Elo and Roster Elo, and removing it changed nothing in the backtest.</p>
      <p><strong>6. Region strength.</strong> Part of Map Elo: an offset per region, learned only from international maps. Every within-region game cancels it out, so it measures exactly what domestic results can't: how the regions compare.</p>
      <p><strong>Tested and left out.</strong> Pistol-round strength, momentum (beating your rating lately), rest days, recent workload, roster chemistry, tier-one experience, and a round model adjusted for each round's economy were all built and tested. None improved forecasts beyond chance on the validation period; the chart below shows each one. The economy result is worth explaining. Money swings individual rounds hugely (a 10,000-credit loadout edge is worth about as much as the whole gap between the best and worst teams), but over hundreds of rounds those swings even out, so a team's skill net of economy is almost identical (correlation 0.99) to its raw round record.</p>
      <p><strong>Stacking.</strong> A logistic regression combines Map Elo (with region strength), the round model, Roster Elo, both form measures, and whether the team picked the map. A gradient-boosted alternative was tested and was clearly worse; there isn't enough data for it. It is trained on mirrored rows (so listing a team first carries no information) and refit monthly on everything before the month it predicts.</p>
      <h3 style="margin-top:28px">How much each signal matters</h3>
      <p class="note">Coefficient times the signal's typical spread, in log-odds per map. Signals overlap, so a small weight can mean the information is already carried by another signal.</p>
      <div class="chart" id="coef"></div>
      <h3 style="margin-top:28px">Veto simulation</h3>
      <p>Series are won on maps the teams choose, so a forecast before the veto has to guess the veto. Each ban and pick is modelled as a choice among the maps still available, scored by the team's modelled edge on the map and its recent habit of banning or picking it. Fitted on real vetoes, the weights are ${w.a_ban.toFixed(2)} (edge) and ${w.b_ban.toFixed(2)} (habit) for bans, and ${w.a_pick.toFixed(2)} and ${w.b_pick.toFixed(2)} for picks. Teams veto largely by their edge, but habit adds information the model can't see, such as scrim results. Each forecast averages thousands of simulated vetoes.</p>
      <h3 style="margin-top:28px">Map side balance</h3>
      <p class="note">Share of rounds won by the attacking side on each map in the last 12 months. The round model learns these biases, so it can tell a team strong on defence from one playing a defence-sided map.</p>
      <div class="chart" id="sides"></div>
      <div class="legend"><span><i class="sq" style="background:var(--atk)"></i>Attack-sided</span><span><i class="sq" style="background:var(--def)"></i>Defence-sided</span></div>
      <h3 style="margin-top:28px">Prior work this builds on</h3>
      <ul>
        <li>Bradley–Terry–Élő models for paired comparisons: <a href="https://arxiv.org/pdf/1701.08055">Király &amp; Qian, 2017</a>, and pre-game paired-comparison models for esports maps: <a href="https://arxiv.org/html/2609.08060">League of Legends map outcomes</a>.</li>
        <li>Glicko-style ratings for match forecasting: <a href="https://www.ncbi.nlm.nih.gov/pmc/articles/PMC8992979/">Glicko for tennis</a>.</li>
        <li>Map-veto-aware forecasting in Counter-Strike: <a href="https://www.academia.edu/101834641/Esports_Match_Result_Prediction_for_a_Decision_Support_System_in_Counter_Strike_Global_Offensive">decision support for CS:GO veto</a>.</li>
        <li>Valorant-specific work on round and match outcomes: <a href="https://arxiv.org/html/2510.17199v1">tactical round prediction</a>, <a href="https://www.academia.edu/164925658/Match_Outcome_Prediction_in_FPS_Games_Reflecting_Sequential_Match_Flow_Focusing_on_Valorant_and_the_Seq2Seq_LSTM_Model">sequential match-flow models</a>, and community Elo + gradient boosting models such as <a href="https://github.com/JasSaini101/2026__Val_Champs_Model">this Champions 2026 project</a>.</li>
      </ul>
      <h3 style="margin-top:28px">Limits</h3>
      <p>Region strength is still the weakest part. Regions mostly play themselves, so their relative level is pinned down only by a few international events a year. The model learns region offsets from cross-region maps, but in the backtest Chinese teams still won 35% of cross-region maps when the model expected about 44%. Treat Chinese teams' odds against other regions as slightly generous.</p>
      <p>The model knows nothing about patches, agent meta shifts, visa problems, illness, or a stand-in announced an hour before the match. Upcoming lineups are assumed to be the five who played the team's last series. Tier-two results aren't included, so newly promoted teams start near the bottom and take a few weeks to find their level.</p>
    </div>

    <h2>Recent forecasts</h2>
    <p class="note">The last ${bt.recent.length} series, each forecast before it was played.</p>
    <div class="table-wrap"><table><thead><tr><th>Date</th><th>Event</th><th>Series</th><th class="r">Forecast</th><th class="r">Result</th></tr></thead><tbody>
      ${bt.recent.map((r) => { const ok = (r.p1 >= .5) === (r.win1 === 1); return `<tr><td class="small">${fmtDate(r.date)}</td><td class="small">${esc(r.event)}</td>
      <td>${esc(r.t1)} vs ${esc(r.t2)}</td><td class="r num">${pct(r.p1)}</td><td class="r"><span class="${ok ? "w" : "l"}">${r.score}</span></td></tr>`; }).join("")}
    </tbody></table></div>`;
  calibChart(document.getElementById("calib"), best.calibration, S["Series Elo"].calibration);
  Promise.all([X.modelPanels(), X.experimentsSection()]).then(([{ html, draw }, exp]) => {
    const el = document.getElementById("model-extra");
    if (el) { el.innerHTML = html + exp; draw(); }
  });
  const ot = bt.over_time.filter((d) => d.n >= 20);
  const q2d = (q) => { const [y, qq] = q.split("Q"); return new Date(+y, (+qq - 1) * 3 + 1, 15); };
  lineChart(document.getElementById("ll-time"), [
    { name: "Ensemble", color: "var(--ink)", width: 2.5, values: ot.map((d) => [q2d(d.period), d.ensemble]) },
    { name: "Series Elo", color: "var(--ink-3)", values: ot.map((d) => [q2d(d.period), d.series_elo]) },
  ], { legend: document.getElementById("ll-legend"), yFormat: (v) => v.toFixed(3), yLabel: "Log loss by quarter", points: true, height: 280 });
  coefChart(document.getElementById("coef"), bt.coefficients);
  sideBalance(document.getElementById("sides"), bt.map_sides);
}

function calibChart(el, ens, elo) {
  const W = Math.min(cw(el, 420), 460), H = Math.round(W * 0.85), m = { t: 10, r: 14, b: 36, l: 44 };
  const x = d3.scaleLinear().domain([0.5, 1]).range([m.l, W - m.r]);
  const y = d3.scaleLinear().domain([0.3, 1]).range([H - m.b, m.t]);
  const svg = d3.select(el).append("svg").attr("viewBox", `0 0 ${W} ${H}`).attr("role", "img").attr("aria-label", "Calibration plot");
  svg.append("g").attr("class", "grid").selectAll("line").data(y.ticks(6)).join("line").attr("x1", m.l).attr("x2", W - m.r).attr("y1", y).attr("y2", y);
  svg.append("line").attr("x1", x(.5)).attr("y1", y(.5)).attr("x2", x(1)).attr("y2", y(1)).attr("stroke", "var(--ink-3)");
  svg.append("g").attr("class", "axis").attr("transform", `translate(0,${H - m.b})`).call(d3.axisBottom(x).ticks(5).tickFormat((d) => pct(d)).tickSizeOuter(0));
  svg.append("g").attr("class", "axis").attr("transform", `translate(${m.l},0)`).call(d3.axisLeft(y).ticks(6).tickFormat((d) => pct(d)).tickSizeOuter(0));
  svg.append("text").attr("x", W - m.r).attr("y", H - 4).attr("text-anchor", "end").text("Forecast chance for the favourite");
  const r = d3.scaleSqrt().domain([0, d3.max([...ens, ...elo], (d) => d.n)]).range([3, 13]);
  const draw = (data, filled, name) => svg.append("g").selectAll("circle").data(data).join("circle")
    .attr("cx", (d) => x(d.pred)).attr("cy", (d) => y(d.obs)).attr("r", (d) => r(d.n))
    .attr("fill", filled ? "var(--ink)" : "none").attr("stroke", filled ? "var(--bg)" : "var(--ink-3)").attr("stroke-width", 2)
    .on("mousemove", (ev, d) => tip(`<b>${name}</b><br>Forecast ${pct(d.lo)}–${pct(d.hi)}: average ${pct(d.pred, 1)}<br>Favourite won ${pct(d.obs, 1)} of ${d.n} series`, ev))
    .on("mouseleave", () => tip(null));
  draw(elo, false, "Series Elo");
  draw(ens, true, "Ensemble");
}

function coefChart(el, rows) {
  const W = Math.min(cw(el, 640), 720), rowH = 30, left = Math.min(230, W * 0.42), H = rows.length * rowH + 20;
  const lim = d3.max(rows, (d) => Math.abs(d.impact));
  const x = d3.scaleLinear().domain([-lim, lim]).range([left, W - 60]);
  const svg = d3.select(el).append("svg").attr("viewBox", `0 0 ${W} ${H}`);
  svg.append("line").attr("x1", x(0)).attr("x2", x(0)).attr("y1", 0).attr("y2", H - 16).attr("stroke", "var(--ink-3)");
  const g = svg.selectAll("g").data(rows.slice().sort((a, b) => b.impact - a.impact)).join("g").attr("transform", (d, i) => `translate(0,${i * rowH})`);
  g.append("text").attr("x", left - 12).attr("y", rowH / 2 + 4).attr("text-anchor", "end").attr("class", "lbl").text((d) => d.label);
  g.append("rect").attr("y", rowH / 2 - 6).attr("height", 12).attr("rx", 3)
    .attr("x", (d) => Math.min(x(0), x(d.impact))).attr("width", (d) => Math.max(2, Math.abs(x(d.impact) - x(0)))).attr("fill", "var(--ink-2)");
  g.append("text").attr("class", "big").attr("y", rowH / 2 + 5).attr("x", (d) => Math.max(x(0), x(d.impact)) + 6).text((d) => d.impact.toFixed(2));
}

function sideBalance(el, rows) {
  const data = rows.slice().sort((a, b) => b.atk_rw - a.atk_rw);
  const W = Math.min(cw(el, 640), 720), rowH = 30, left = 100, H = data.length * rowH + 24;
  const x = d3.scaleLinear().domain([0.42, 0.58]).range([left, W - 60]);
  const svg = d3.select(el).append("svg").attr("viewBox", `0 0 ${W} ${H}`);
  svg.append("g").selectAll("text").data(x.ticks(4)).join("text").attr("x", x).attr("y", H - 4).attr("text-anchor", "middle").text((d) => pct(d));
  svg.append("line").attr("x1", x(.5)).attr("x2", x(.5)).attr("y1", 0).attr("y2", H - 20).attr("stroke", "var(--ink-3)");
  const g = svg.selectAll("g.r").data(data).join("g").attr("class", "r").attr("transform", (d, i) => `translate(0,${i * rowH})`);
  g.append("text").attr("x", left - 12).attr("y", rowH / 2 + 4).attr("text-anchor", "end").attr("class", "lbl").text((d) => d.map);
  g.append("rect").attr("y", rowH / 2 - 6).attr("height", 12).attr("rx", 3)
    .attr("x", (d) => Math.min(x(.5), x(d.atk_rw))).attr("width", (d) => Math.max(2, Math.abs(x(d.atk_rw) - x(.5))))
    .attr("fill", (d) => d.atk_rw >= .5 ? "var(--atk)" : "var(--def)");
  g.append("text").attr("class", "big").attr("y", rowH / 2 + 5).attr("x", (d) => d.atk_rw >= .5 ? x(d.atk_rw) + 6 : x(.5) + 6)
    .text((d) => `${pct(d.atk_rw, 1)} ${d.atk_rw >= .5 ? "attack" : "attack, defence-sided"}`);
}

boot();
