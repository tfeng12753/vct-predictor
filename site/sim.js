// Veto + series simulator shared by the matchup lab and the parity test (tests/test_parity.py).
// JS port of pipeline/veto.py VetoModel.simulate, driven by the exported pairwise tables
export function vetoSim(M, a, b, bo, n, random = Math.random) {
  const idx = new Map(M.teams.map((t, i) => [t, i]));
  const pool = M.pool;
  const pmap = (mp, pk) => {
    const ia = idx.get(a), ib = idx.get(b);
    if (ia < ib) return M.pairs[`${ia}-${ib}`][mp][{ 0: 0, 1: 1, "-1": 2 }[pk]];
    return 1 - M.pairs[`${ib}-${ia}`][mp][{ 0: 0, 1: 2, "-1": 1 }[pk]];
  };
  const base = Object.fromEntries(pool.map((m) => [m, pmap(m, 0)]));
  const lg = (p) => { p = Math.min(1 - 1e-6, Math.max(1e-6, p)); return Math.log(p / (1 - p)); };
  const rate = (team, act, mp, nAvail) => {
    const [c, av] = M.habits[team][act][pool.indexOf(mp)];
    const bs = 1 / Math.max(nAvail, 1);
    return (c + M.habit_prior * bs * 2) / (av + M.habit_prior * 2);
  };
  const fmt = M.formats[String(bo)] || M.formats["3"];
  const need = Math.floor(bo / 2) + 1;
  let pSeries = 0;
  const scores = {}, played = {}, pickA = {}, pickB = {}, orders = new Map();
  for (let i = 0; i < n; i++) {
    const first = i % 2 === 0 ? a : b, second = first === a ? b : a;
    const avail = pool.slice();
    const seq = [];
    for (const [pos, act] of fmt) {
      if (avail.length <= 1) break;
      const tm = pos === 1 ? first : second;
      const w = M.veto_weights;
      const z = avail.map((m) => {
        const pOwn = tm === a ? base[m] : 1 - base[m];
        const s = act === "ban" ? -lg(pOwn) : lg(pOwn);
        return w[`a_${act}`] * s + w[`b_${act}`] * Math.log(rate(tm, act, m, avail.length));
      });
      const zm = Math.max(...z);
      const e = z.map((v) => Math.exp(v - zm));
      const tot = e.reduce((s, v) => s + v, 0);
      let r = random() * tot, j = 0;
      while (j < e.length - 1 && (r -= e[j]) > 0) j++;
      const [m] = avail.splice(j, 1);
      if (act === "pick") seq.push([m, tm === a ? 1 : -1]);
    }
    if (seq.length < bo && avail.length) seq.push([avail[0], 0]);
    const s = seq.slice(0, bo);
    const ps = s.map(([m, pk]) => pmap(m, pk));
    let dist = new Map([["0-0", 1]]);
    for (const p of ps) {
      const nx = new Map();
      for (const [k, pr] of dist) {
        const [x, y] = k.split("-").map(Number);
        for (const [nx_, ny, q] of [[x + 1, y, p], [x, y + 1, 1 - p]]) {
          if (nx_ === need || ny === need) {
            const key = `${nx_}-${ny}`;
            scores[key] = (scores[key] || 0) + pr * q / n;
            if (nx_ === need) pSeries += pr * q / n;
          } else nx.set(`${nx_}-${ny}`, (nx.get(`${nx_}-${ny}`) || 0) + pr * q);
        }
      }
      dist = nx;
    }
    for (const [m, pk] of s) {
      played[m] = (played[m] || 0) + 1;
      if (pk === 1) pickA[m] = (pickA[m] || 0) + 1;
      if (pk === -1) pickB[m] = (pickB[m] || 0) + 1;
    }
    const key = s.map(([m]) => m).join("|");
    orders.set(key, (orders.get(key) || 0) + 1);
  }
  const maps = pool.map((m) => ({
    map: m, play_rate: (played[m] || 0) / n, p_neutral: base[m], p_t1_pick: pmap(m, 1), p_t2_pick: pmap(m, -1),
    t1_pick_rate: (pickA[m] || 0) / n, t2_pick_rate: (pickB[m] || 0) / n,
  })).sort((x, y) => y.play_rate - x.play_rate);
  const top_vetoes = [...orders].sort((x, y) => y[1] - x[1]).slice(0, 5).map(([k, v]) => ({ maps: k.split("|"), prob: v / n }));
  return { p_series: pSeries, scores, maps, top_vetoes };
}

