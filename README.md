# VCT Forecast Desk

Win probabilities, map-by-map forecasts, veto simulations and tournament odds for
tier-one Valorant (VCT), built from match data scraped from [vlr.gg](https://www.vlr.gg).

```
pipeline/   Python: scraper, rating models, veto model, backtests, JSON export
site/       Static site (HTML + D3), reads site/data/*.json
data/       SQLite database of every scraped series, map, round and player-map
cache/      Raw HTML cache (completed matches are downloaded once)
```

## Run it

```bash
uv venv .venv --python 3.12 && uv pip install --python .venv/bin/python -r requirements.txt
.venv/bin/python -m pipeline.scrape     # first crawl: every VCT event 2023–now, ~1 req/s
.venv/bin/python -m pipeline.export     # fit models, backtest, write site/data/
node serve.mjs                          # http://localhost:8765
```

**Live site:** https://tfeng12753.github.io/vct-predictor/

## Automatic updates

A GitHub Action ([`.github/workflows/update.yml`](.github/workflows/update.yml)) runs daily at 09:17 UTC: it
scrapes new results from vlr.gg, rebuilds every forecast, runs the tests, commits `site/data/`, and deploys the
site to GitHub Pages. Run it on demand from the Actions tab ("Update forecasts" → Run workflow).
The scraped database isn't committed; it lives in the Actions cache, and a run that finds no cache re-crawls
everything (a few hours, one time).

### Running locally

Optional macOS job that refreshes a local copy on a schedule:

```bash
scheduler/install.sh        # check vlr.gg every 30 minutes (optional arg: seconds, e.g. 900)
scheduler/uninstall.sh      # stop
tail -f cache/update.log    # watch it work
```

This installs a macOS launchd job that runs `update.sh`. Each run costs ~10 requests to vlr.gg (only events
that are still in progress are re-read), and the models are refit only when results or pairings changed.
Runs missed while the Mac sleeps happen on wake. Open browser tabs pick up new forecasts on their own.

macOS doesn't let background jobs read `~/Documents`, so the project lives in `~/Developer/vct-predictor`
(with a symlink back into `~/Documents/GitHub`). Keep it outside Documents, Desktop and Downloads.

To update by hand instead: `./update.sh`.

Other entry points:

```bash
.venv/bin/python -m pytest tests          # parser, probability math, tournament, JS/Python parity, export sanity
.venv/bin/python -m pipeline.ablation     # re-test every candidate signal (validation vs holdout)
.venv/bin/python -m pipeline.tune         # hyperparameter search on pre-backtest data
.venv/bin/python -m pipeline.validate     # data-health checks
.venv/bin/python -m pipeline.experiments  # model-level experiments (writes site/data/experiments.json)
.venv/bin/python -m pipeline.scrape --only-tier2   # Challengers/Ascension since 2024 (several hours; resumable)
```

## Models

All models are online: each prediction uses only matches played before it, so the
backtest numbers on the site are honest out-of-sample results.

| Model | What it captures |
|---|---|
| **Map Elo** | Team strength from map results, margin-of-victory scaled, with region offsets learned from international maps, season regression and roster-change regression |
| **Round model** | Time-decayed Bradley–Terry regression on individual rounds: attack and defence strength per team and per map, plus each map's side bias. Converted to map win probability with an exact regulation + overtime DP |
| **Roster Elo** | Per-player ratings averaged over the lineup, so transfers carry ratings with the player |
| **Player form** | Recency-weighted Rating 2.0 and first-kill differential, shrunk toward league average |
| **Series Elo** | Baseline series-result Elo |
| **Stack** | Symmetric logistic regression over the signals above plus map-pick side, refit monthly walk-forward |
| **Veto model** | Conditional-logit ban/pick choices driven by modelled map edge and each team's decayed ban/pick habits, fitted on real vetoes; Monte Carlo over vetoes gives pre-veto series odds |
| **Candidate signals** | Pistol strength, momentum, rest, workload, roster chemistry, experience and an economy-adjusted round model are computed online and tested by `pipeline/ablation.py`. None beat chance on validation, so none are in the stack; the site shows the results |
| **More candidates** | Agent/map signals (map comfort, agent-pool depth, comp meta-alignment, map-specific form), host-region advantage, and favourite-reliability interactions (new patch, new act, internationals, elimination games). None beat noise |
| **Experiments** | `pipeline/experiments.py`: series map-correlation term, temperature scaling, tier-2 (Challengers) priors for newcomers. Logged on the site with validation and holdout intervals |
| **Tournament sim** | Champions GSL groups + 8-team double elimination, 20,000 runs, honouring completed results |

## Evaluation protocol

- Signals and settings are chosen on the validation window (2024-01 to 2025-06). A signal changes status only if its
  paired-bootstrap 95% interval excludes zero; greedy selection on point estimates overfit in testing.
- 2025-07 onward is a holdout never used for decisions. The site reports both, plus bootstrap intervals.

## Notes

- Scraping is polite: ~1 request per second, a descriptive User-Agent, and robots.txt respected
  (it only disallows `/search/auto` and `/rr/`). Please keep it that way.
- vlr.gg's markup changes occasionally; the parser lives in `pipeline/scrape.py:parse_match`.
- Not affiliated with Riot Games or vlr.gg.
