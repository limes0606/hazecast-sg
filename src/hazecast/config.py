"""Shared constants for the HazeCast SG pipeline.

Everything that is an assumption rather than a measured fact is called out
in a comment. Change these in one place; every stage imports from here.
"""
from __future__ import annotations
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths (medallion layers). Override with env vars in production / Airflow.
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
BRONZE_DIR = DATA_DIR / "bronze"
SILVER_DIR = DATA_DIR / "silver"
GOLD_DIR = DATA_DIR / "gold"
MODELS_DIR = DATA_DIR / "models"

# ---------------------------------------------------------------------------
# Regions: Singapore's 5 NEA reporting regions (must match the PM2.5 file's
# column names exactly).
# ---------------------------------------------------------------------------
SG_REGIONS = ["north", "south", "east", "west", "central"]

# Approximate bounding boxes for the two dominant fire source regions,
# used to tag FIRMS detections. These are coarse rectangles, not
# administrative boundaries -- documented assumption, not a measured fact.
FIRE_REGIONS = {
    "sumatra": {"lon_min": 95.0, "lon_max": 106.5, "lat_min": -6.2, "lat_max": 6.0},
    "kalimantan": {"lon_min": 108.5, "lon_max": 119.5, "lat_min": -4.6, "lat_max": 4.6},
}

# Weather reference points used for upwind features (lat, lon), one per
# fire province plus Singapore itself. Matches the Open-Meteo files
# requested for this project.
WEATHER_POINTS = {
    "singapore": (1.37, 103.80),
    "riau": (0.53, 101.42),          # Sumatra (north)
    "palembang": (-2.99, 104.78),    # Sumatra (south)
    "pontianak": (0.04, 109.28),     # Kalimantan (west)
    "palangka": (-2.21, 113.92),     # Kalimantan (central)
}

# ---------------------------------------------------------------------------
# Labels / thresholds
# ---------------------------------------------------------------------------
# NEA 1-hour PM2.5 bands (ug/m3), verified against NEA/haze.gov.sg guidance
# reported by Malay Mail (27 Jun 2016) and cross-checked in this project's
# own data profiling:
#   Normal: 0-55, Elevated: 56-150, High: 151-250, Very High: >250
PM25_ELEVATED_THRESHOLD = 56.0

# Forecast horizon: predict the next calendar day's daily-mean PM2.5,
# per region, using only data observed up to "today". No same-day or
# future weather is used (tomorrow's wind is unknown at forecast time).
FORECAST_HORIZON_DAYS = 1

# Hotspot lag features to build (days). Same-day fires (lag 0) matter most,
# per this project's correlation analysis; we keep out to 3 days.
HOTSPOT_LAGS = [0, 1, 2, 3]

# Rolling windows (days) for rain/hotspot accumulation features.
ROLLING_WINDOWS = [3, 7]

# "Haze seasons" used for leave-one-season-out evaluation. These are the
# periods this project's own data profiling found to contain the vast
# majority of elevated (>=56 ug/m3) days. Both 2015 and 2019 have full
# feature coverage as of the 2014-2018 FIRMS backfill; add 2026 once that
# season's PM2.5 labels are complete (currently only through 1 Aug 2026).
HAZE_SEASONS = {
    "2015": ("2015-08-01", "2015-11-15"),
    "2019": ("2019-08-15", "2019-11-30"),
    # "2026": ("2026-08-01", "2026-10-31"),  # add once PM2.5 labels cover it
}

# ---------------------------------------------------------------------------
# Model hyperparameters
# ---------------------------------------------------------------------------
# Shared XGBoost tree/boosting params for every XGBoost variant compared in
# hazecast.train -- only the objective differs between variants.
XGB_PARAMS = dict(
    n_estimators=300, max_depth=4, learning_rate=0.05,
    subsample=0.8, colsample_bytree=0.8, random_state=42,
)

# The production model forecasts a HIGH quantile of next-day PM2.5, not the
# mean. Plain MAE-minimizing regression (see the "xgboost" candidate in
# hazecast.train, kept only as a comparison baseline) systematically
# under-predicts the rare elevated days: with haze days at ~2% of the
# dataset, a mean-minimizer is pulled toward the calm majority. Evaluated
# against real data (see README "Results"), targeting the 85th percentile
# instead roughly triples elevated-day recall on the worse of the two haze
# seasons, at a real but acceptable cost in precision (more false alarms)
# and a season-dependent change in MAE. This is a deliberate choice for an
# early-warning use case, where a missed elevated day (false negative) is
# more costly than an extra false alarm -- tune lower for a more
# conservative/precise alert, higher to catch more true events at the cost
# of more false alarms; 0.70-0.95 were swept, 0.85 was the best balance.
XGB_QUANTILE_ALPHA = 0.85
