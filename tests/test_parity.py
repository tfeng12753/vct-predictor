"""The matchup lab's JavaScript simulator must agree with the Python veto model."""
import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from pipeline.veto import VetoHabits, VetoModel

ROOT = Path(__file__).resolve().parent.parent
MATCHUP = ROOT / "site" / "data" / "matchup.json"
pytestmark = pytest.mark.skipif(not MATCHUP.exists() or not shutil.which("node"), reason="needs export + node")


def python_sim(M, a, b, bo, n):
    idx = {t: i for i, t in enumerate(M["teams"])}
    pool = M["pool"]

    def pmap(mp, pk):
        ia, ib = idx[a], idx[b]
        if ia < ib:
            return M["pairs"][f"{ia}-{ib}"][mp][{0: 0, 1: 1, -1: 2}[pk]]
        return 1 - M["pairs"][f"{ib}-{ia}"][mp][{0: 0, 1: 2, -1: 1}[pk]]

    h = VetoHabits(prior=M["habit_prior"])
    for t in (a, b):
        for act in ("ban", "pick"):
            for mp, (c, av) in zip(pool, M["habits"][str(t)][act]):
                h.chosen[(t, act, mp)], h.avail[(t, act, mp)] = c, av
    vm = VetoModel()
    vm.w = M["veto_weights"]
    vm.formats = {int(k): [tuple(x) for x in v] for k, v in M["formats"].items()}
    return vm.simulate(pool, pmap, h, a, b, bo, n=n, rng=np.random.default_rng(1))["p_series"]


def js_sim(a, b, bo, n):
    script = f"""
      import {{ readFileSync }} from "node:fs";
      import {{ vetoSim }} from "{(ROOT / 'site' / 'sim.js').as_posix()}";
      const M = JSON.parse(readFileSync("{MATCHUP.as_posix()}", "utf8"));
      let s = 12345; const rnd = () => ((s = (s * 1103515245 + 12345) % 2147483648) / 2147483648);
      console.log(vetoSim(M, {a}, {b}, {bo}, {n}, rnd).p_series);
    """
    out = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True, check=True)
    return float(out.stdout.strip())


def test_js_matches_python():
    M = json.loads(MATCHUP.read_text())
    teams = M["teams"]
    for a, b, bo in ((teams[0], teams[1], 3), (teams[5], teams[2], 5), (teams[-1], teams[3], 1)):
        py, js = python_sim(M, a, b, bo, 3000), js_sim(a, b, bo, 3000)
        assert abs(py - js) < 0.02, (a, b, bo, py, js)
