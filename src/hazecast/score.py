"""Batch scoring: load the latest gold row and each region's saved
XGBoost model, produce tomorrow's forecast per region, and write it
where both the dashboard/API and the monitoring job can read it.

This is meant to run once a day, after the nightly ingest -> bronze ->
silver -> gold steps, as the last task in the Airflow DAG (see
dags/hazecast_daily_dag.py).
"""
from __future__ import annotations
import json
import logging
from datetime import datetime, timezone

import pandas as pd
from xgboost import XGBRegressor

from hazecast.config import GOLD_DIR, MODELS_DIR, SG_REGIONS, PM25_ELEVATED_THRESHOLD

log = logging.getLogger(__name__)

TARGETS = SG_REGIONS + ["national"]


def score_latest(
    gold_path=GOLD_DIR / "daily_features.parquet",
    models_dir=MODELS_DIR,
) -> pd.DataFrame:
    gold = pd.read_parquet(gold_path)
    gold["date"] = pd.to_datetime(gold["date"])
    feature_cols = json.loads((models_dir / "feature_columns.json").read_text())

    # Score the most recent row whose features are fully populated --
    # normally "today" (its label is tomorrow, which is exactly what we
    # want to forecast and don't have yet).
    complete = gold[feature_cols].notna().all(axis=1)
    if not complete.any():
        raise RuntimeError("No gold row has complete features; check the last few days' ingestion.")
    row = gold.loc[complete].iloc[[-1]]
    forecast_for = row["date"].iloc[0] + pd.Timedelta(days=1)

    results = []
    for target in TARGETS:
        model_path = models_dir / f"xgb_{target}.json"
        if not model_path.exists():
            log.warning("No saved model for target=%s (run hazecast.train first), skipping", target)
            continue
        model = XGBRegressor()
        model.load_model(model_path)
        pred = float(model.predict(row[feature_cols])[0])
        results.append({
            "scored_at": datetime.now(timezone.utc).isoformat(),
            "as_of_date": row["date"].iloc[0].date().isoformat(),
            "forecast_date": forecast_for.date().isoformat(),
            "region": target,
            "forecast_pm25": round(pred, 1),
            "elevated_flag": pred >= PM25_ELEVATED_THRESHOLD,
        })
    return pd.DataFrame(results)


def write_forecast(df: pd.DataFrame, gold_dir=GOLD_DIR) -> str:
    out_path = gold_dir / "latest_forecast.json"
    out_path.write_text(df.to_json(orient="records", indent=2))
    return str(out_path)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    forecast = score_latest()
    path = write_forecast(forecast)
    log.info("Scored %d regions -> %s\n%s", len(forecast), path, forecast.to_string(index=False))
