# HazeCast SG

Forecasting tomorrow's PM2.5 in each region of Singapore from regional
fire hotspots, wind and rain. CS611 Machine Learning Engineering group
project.

This is a **proof of concept**, built and validated end-to-end against
real downloaded data (not synthetic placeholders) before being handed to
the team. What's real vs. what still needs work is called out explicitly
in [What's tested vs. not](#whats-tested-vs-not) below -- please read that
section before assuming a piece is production-ready.

## Business problem

Transboundary haze from Indonesian fires periodically pushes Singapore's
PM2.5 into unhealthy territory (2015, 2019, and the ongoing Sep 2026
episode being the clearest examples), and outdoor operations teams
currently only find out once readings are already high. NEA already
publishes current PSI/PM2.5 readings and a regional outlook -- this
project doesn't try to out-forecast NEA. It turns those public upwind
signals into an automated, per-region next-day forecast that a specific
operator (an events/construction/schools operations team) can act on,
which today requires someone manually checking multiple dashboards and
using judgement.

**Objective:** forecast next-day daily-mean PM2.5 for each of Singapore's
5 NEA regions (north, south, east, west, central), every day, and flag
any region forecast to cross the NEA "Elevated" band (56 ug/m3).

## Architecture

```
Sources (PM2.5, FIRMS, weather)
  -> ingest (Airflow, nightly)
  -> bronze (raw, as received)
  -> silver (cleaned, gap-flagged, hourly grid)
  -> gold (daily feature + label table, per region)
  -> train (persistence / linreg / XGBoost, leave-one-season-out eval, MLflow)
  -> score (batch, nightly)
  -> serve (FastAPI, reads the latest scored forecast -- doesn't score on request)
```

Everything runs locally via `docker compose up` (Airflow + MLflow + the
scoring API); see [Running it](#running-it).

## Repo layout

```
src/hazecast/
  config.py            regions, fire-region boxes, weather points, thresholds, haze seasons
  ingest/pm25.py        NEA Historical 1-hr PM2.5 -> bronze
  ingest/firms.py        NASA FIRMS active-fire detections -> bronze
  ingest/weather.py      Open-Meteo historical weather (5 points) -> bronze
  bronze_to_silver.py    cleaning, gap-flagging, hourly/daily alignment
  silver_to_gold.py      daily feature + label table (leak-free by construction)
  train.py               persistence / linreg / XGBoost, leave-one-season-out eval, MLflow logging
  score.py               batch scoring -> data/gold/latest_forecast.json
serving/app.py            FastAPI: /forecast, /forecast/{region}, /health, /config
dags/                     Airflow DAGs (daily pipeline, monthly retrain)
docker/                   Dockerfile(s)
notebooks/                pipeline_walkthrough.ipynb -- same 5 steps as "Running it" below, interactive
tests/                    fast smoke tests on synthetic fixtures (no real data needed)
data/README.md            how to get the 3 real datasets (not committed -- see below)
```

## Data

Three public sources, documented in full in
[`data/README.md`](data/README.md):

| Source | Grain | History used | Access |
|---|---|---|---|
| [NEA Historical 1-hr PM2.5](https://data.gov.sg/datasets/d_6c0d5fc34838b12472475fdb73c0af29/view) | hourly, 5 regions | 2014-04-01 to 2026-08-01 (validated) | Open Data Licence |
| [NASA FIRMS](https://firms.modaps.eosdis.nasa.gov/) (VIIRS Suomi-NPP) | per detection | 2019-01-01 to 2026-09-21 (validated); **2014-2018 not yet obtained** | free MAP_KEY |
| [Open-Meteo Historical Weather](https://open-meteo.com/en/docs/historical-weather-api) (ERA5) | hourly, 5 points | 2014-04-01 to 2026-07-31 (validated) | free, non-commercial |

None of the raw files are committed to git -- run the download steps in
`data/README.md` once, locally, before running the pipeline.

## Results (proof of concept, real data)

Leave-one-season-out evaluation, now with the 2014-2018 FIRMS backfill in
place, so **both** haze seasons in `hazecast.config.HAZE_SEASONS` are
evaluated (2015 and 2019 -- 2026 still has no labels past 1 Aug). MAE in
ug/m3, lower is better; full per-fold numbers including elevated-day
recall/precision are in `data/models/eval_summary.csv` after running
training.

**MAE by held-out season:**

| target | 2015: persistence | 2015: linreg | 2015: xgboost | 2019: persistence | 2019: linreg | 2019: xgboost |
|---|---:|---:|---:|---:|---:|---:|
| north | 17.50 | 15.70 | 18.94 | 6.31 | 5.73 | **5.02** |
| south | 20.09 | **17.66** | 21.08 | 7.36 | 6.52 | **5.74** |
| east | 18.79 | **16.06** | 18.31 | 6.89 | 6.41 | **5.53** |
| west | 18.67 | **16.93** | 21.03 | 6.68 | 6.13 | **5.55** |
| central | 15.76 | **13.68** | 15.50 | 6.04 | 5.41 | **4.73** |
| national | 17.92 | **15.74** | 18.58 | 6.44 | 5.84 | **5.05** |

**Elevated-day recall (national), the metric that actually matters for
this use case:**

| season | persistence | linreg | xgboost |
|---|---:|---:|---:|
| 2015 (31 elevated days in the test fold) | 0.68 | 0.61 | **0.13** |
| 2019 (4 elevated days in the test fold) | 0.25 | **0.75** | 0.50 |

**Honest read of this, not a sales pitch:** adding a second haze season
changed the story, and not in a simple "XGBoost got better" direction.
On the 2019 fold, XGBoost now has the best MAE *and* decent recall
(0.50-1.00 depending on region) -- more training data helped, as
expected. But on the 2015 fold -- the worse of the two haze events in
this dataset, and the one the elevated-day recall metric matters most
for -- XGBoost's recall on elevated days collapses to 0.06-0.25 across
every region, even though its average MAE looks only moderately worse
than linreg's. In plain terms: XGBoost is quietly under-forecasting the
severity of the biggest haze episode in the data, while linear
regression (and even the naive persistence baseline) catch far more of
those elevated days. This is exactly the kind of failure a pure MAE
leaderboard would hide and a recall-on-elevated-days metric catches --
which is the point of tracking both.

Two real training seasons is still a small sample for a ~2%-positive-rate
event, so neither model's 2015 result should be taken as final. But as
of this proof of concept, **linear regression is the more defensible
choice for the decision-support objective** (flagging elevated days),
precisely because it's more consistent across both folds rather than
because it has the lowest average MAE.

Per the module brief: a simple, honestly-evaluated model with rigorous
MLOps is the goal here, not a leaderboard score. Ship `linreg` (or
investigate a recall-aware objective for XGBoost, e.g. class weighting
or quantile loss) and document that decision -- and the recall numbers
behind it -- in the final report. That's the defensible, data-driven
choice the brief rewards, not a downgrade.

## Running it

**1. Get the data** -- see [`data/README.md`](data/README.md).

**2. Run the pipeline locally (fastest way to iterate):**
```bash
pip install -r requirements.txt
export PYTHONPATH=src
python -m hazecast.ingest.pm25 && python -m hazecast.ingest.firms && python -m hazecast.ingest.weather
python -m hazecast.bronze_to_silver
python -m hazecast.silver_to_gold
python -m hazecast.train      # writes data/models/eval_summary.csv + the final models
python -m hazecast.score      # writes data/gold/latest_forecast.json
uvicorn serving.app:app --reload   # then GET localhost:8000/forecast
```

**3. Run the fast smoke tests** (no real data needed):
```bash
pytest tests/ -v
```

**4. Or run the same 5 steps interactively in JupyterLab** (same pattern as
Assignment 1 -- Docker + docker-compose + browser, no local Python install
needed):
```bash
docker compose up --build jupyter
```
Then open `localhost:8888/lab` (no token -- local dev only, don't expose
this port beyond localhost) and open `notebooks/pipeline_walkthrough.ipynb`.
It runs ingest -> bronze -> silver -> gold -> train -> score one cell at a
time, printing row counts/date ranges/eval tables as it goes, plus an
optional last cell for the smoke tests. Already executed once end-to-end
against the real data in this proof of concept, so the committed notebook's
outputs are real, not placeholders -- rerun it yourself to confirm on your
own data. (Prefer a local JupyterLab instead of Docker? `pip install -r
requirements.txt && jupyter lab`, then open the same notebook -- it detects
either environment automatically.)

**5. Run the full stack in Docker (all services):**
```bash
docker compose up --build
```
MLflow UI: `localhost:5000`. Airflow UI: `localhost:8080` (see the note
at the top of `docker-compose.yml` about first-run DB init). API:
`localhost:8000/forecast`. JupyterLab: `localhost:8888/lab`.

## What's tested vs. not

This section exists so nobody re-discovers the same gap the hard way.

**Actually run against real downloaded data in this proof of concept:**
- All three ingestion scripts (`hazecast.ingest.*`) -- row counts match
  independent manual profiling of the same files.
- `bronze_to_silver` -- correctly found the same known data quirks
  identified during manual profiling (3 missing PM2.5 hours, ~73 likely
  FIRMS data-gap days including the May 2026 gap).
- `silver_to_gold` -- verified leak-free (label on day D matches
  `today_*` on day D+1 for >99.9% of rows; the tiny remainder is
  low-coverage days) and produces sane feature correlations.
- `train` -- full leave-one-season-out evaluation ran (both the 2015 and
  2019 haze seasons) and produced the results table above; models saved
  and logged to a local MLflow (sqlite backend).
- `score` -- produced a real forecast from the latest available day.
- `serving/app.py` -- smoke-tested with FastAPI's TestClient (health,
  forecast, per-region, 404 path all correct).
- `docker-compose.yml` and `docker/pipeline.Dockerfile` -- YAML validated
  with `docker compose config`; `docker` itself is present.
- The 3 pytest smoke tests in `tests/`.
- `notebooks/pipeline_walkthrough.ipynb` -- executed end-to-end (via
  `jupyter nbconvert --execute`) against the real data; every cell's
  committed output (row counts, eval tables, forecast) is a real result,
  not a placeholder.
- The `jupyter` service in `docker-compose.yml` -- unlike the other
  services (no Docker daemon in the sandbox this was built in), this one
  was actually run with `docker compose up --build jupyter` on a real
  machine. The image built cleanly, but the container exited immediately
  on first run (`Running as root is not recommended. Use --allow-root to
  bypass.`) -- `pipeline.Dockerfile` never creates a non-root user, so
  JupyterLab refused to start. Fixed by adding `--allow-root` to the
  service's command; if you're reading this before re-testing that fix,
  treat it as unconfirmed and re-run `docker compose up --build jupyter`
  yourself.

**NOT run/verified in the environment this was built in, because it had
no outbound network to these hosts -- confirm on a normal machine before
relying on them:**
- The live-API ingestion paths mentioned in each `ingest/*.py` module's
  docstring (data.gov.sg PM2.5 API, Open-Meteo archive API, FIRMS area
  API). Only the manual-download path was exercised.
- Actually building/running the Docker images (`docker build` failed
  here with no daemon available) and the Airflow container specifically
  (its image is large; wasn't pulled here). The DAG files were syntax-
  checked and every function they call was confirmed to exist with a
  matching signature, but the DAG has not executed inside a real
  scheduler.
- Any monitoring/drift job -- not built yet. `hazecast_retrain_dag.py`
  has a TODO for a PSI-drift trigger the proposal calls for; right now
  it's schedule-only (monthly).
- GitHub Actions / CI -- none set up yet.

## Next steps for the team

1. Decide the shipped model given the recall numbers above -- linreg is
   the current default recommendation for the elevated-day-recall
   objective; if the team wants to keep exploring XGBoost, look at class
   weighting or a quantile/focal-style loss rather than plain MAE
   regression, since the 2015 recall collapse looks like a
   class-imbalance problem, not a "needs more data" problem alone.
2. Build the monitoring job (data quality, PSI/feature drift, 30-day
   rolling error vs. persistence) the proposal describes -- currently
   the biggest structural gap vs. the proposal deck.
3. Verify the live-API ingestion paths and the Airflow container on a
   machine with normal network access, then switch `data/raw/` from a
   manual download to the nightly DAG actually pulling fresh data.
4. Once the 2026 haze season's PM2.5 labels are complete, add a "2026"
   entry to `hazecast.config.HAZE_SEASONS` and re-run `hazecast.train`
   for a third fold -- no other code changes needed.
