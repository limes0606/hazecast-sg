"""Silver -> gold: build the daily feature + label table used for
training and scoring.

One row per calendar day (Singapore time). Features use only information
available by end-of-day "today" (23:30 per the team's proposal); labels
are each region's next-day daily-mean PM2.5, per hazecast.config's
FORECAST_HORIZON_DAYS. This keeps the framing leak-free: nothing from the
forecast day itself is ever a feature.
"""
from __future__ import annotations
import logging
from pathlib import Path

import numpy as np
import pandas as pd

from hazecast.config import (
    SILVER_DIR, GOLD_DIR, SG_REGIONS, WEATHER_POINTS,
    HOTSPOT_LAGS, ROLLING_WINDOWS, FORECAST_HORIZON_DAYS,
)

log = logging.getLogger(__name__)


def _daily_pm25(pm25_silver: pd.DataFrame) -> pd.DataFrame:
    df = pm25_silver.set_index("ts")
    daily = df[SG_REGIONS + ["national_mean"]].resample("D").mean()
    # a day built from fewer than 20 of its 24 hourly readings is
    # unreliable as a label or persistence feature -- flag it.
    coverage = df[SG_REGIONS].resample("D").count().min(axis=1)
    daily["low_coverage"] = coverage < 20
    daily.index.name = "date"
    return daily.reset_index()


def _fire_features(firms_daily: pd.DataFrame) -> pd.DataFrame:
    df = firms_daily.set_index("date").sort_index()
    out = pd.DataFrame(index=df.index)
    for region in ["sumatra", "kalimantan", "total"]:
        for lag in HOTSPOT_LAGS:
            out[f"fire_{region}_lag{lag}"] = df[region].shift(lag)
        for w in ROLLING_WINDOWS:
            out[f"fire_{region}_roll{w}"] = df[region].rolling(w, min_periods=1).sum()
        out[f"fire_{region}_log_lag0"] = np.log1p(out[f"fire_{region}_lag0"])
    # Kept as float (0.0/1.0), not bool: this column is merged against
    # years with no FIRMS coverage yet (all-NaN), and a bool/NaN mix would
    # silently become an "object" dtype that breaks XGBoost downstream.
    out["fire_data_gap_recent"] = df["data_gap"].astype(float).rolling(
        max(HOTSPOT_LAGS) + 1, min_periods=1
    ).max()
    out.index.name = "date"
    return out.reset_index()


def _weather_features(weather_silver: pd.DataFrame) -> pd.DataFrame:
    frames = []
    for point in WEATHER_POINTS:
        g = weather_silver[weather_silver["point"] == point].set_index("ts").sort_index()
        if g.empty:
            log.warning("No weather rows for point '%s'; its features will be all-NaN", point)
            continue
        daily = pd.DataFrame(index=g.index.normalize().unique())
        daily.index.name = "date"
        daily[f"wind_speed_{point}"] = g["wind_speed_kmh"].resample("D").mean()
        # circular mean of wind direction (degrees) via unit vectors
        rad = np.deg2rad(g["wind_dir_deg"])
        sin_d = np.sin(rad).resample("D").mean()
        cos_d = np.cos(rad).resample("D").mean()
        daily[f"wind_dir_{point}"] = (np.degrees(np.arctan2(sin_d, cos_d)) + 360) % 360
        daily[f"rain_{point}_mm"] = g["rain_mm"].resample("D").sum()
        for w in ROLLING_WINDOWS:
            daily[f"rain_{point}_roll{w}"] = daily[f"rain_{point}_mm"].rolling(w, min_periods=1).sum()
        frames.append(daily)
    out = frames[0]
    for f in frames[1:]:
        out = out.join(f, how="outer")
    out.index.name = "date"
    return out.reset_index()


def build_gold(
    pm25_silver: pd.DataFrame,
    weather_silver_df: pd.DataFrame,
    firms_daily: pd.DataFrame,
) -> pd.DataFrame:
    daily_pm25 = _daily_pm25(pm25_silver)
    fire = _fire_features(firms_daily)
    weather = _weather_features(weather_silver_df)

    daily_pm25["date"] = pd.to_datetime(daily_pm25["date"])
    fire["date"] = pd.to_datetime(fire["date"])
    weather["date"] = pd.to_datetime(weather["date"])

    gold = daily_pm25.merge(fire, on="date", how="left").merge(weather, on="date", how="left")

    # Persistence features: today's own reading, renamed so it's obviously
    # a feature and not accidentally used as the label.
    for r in SG_REGIONS + ["national_mean"]:
        gold[f"today_{r}"] = gold[r]

    # Labels: each region's PM2.5 exactly FORECAST_HORIZON_DAYS ahead.
    # Built with shift(-h) on the *label source* column, then the
    # unshifted "today_*" columns above remain leak-free features.
    h = FORECAST_HORIZON_DAYS
    for r in SG_REGIONS + ["national_mean"]:
        gold[f"label_{r}"] = gold[r].shift(-h)
    gold["label_low_coverage"] = gold["low_coverage"].shift(-h)

    gold = gold.drop(columns=SG_REGIONS + ["national_mean", "low_coverage"])
    gold = gold.rename(columns={"label_national_mean": "label_national", "today_national_mean": "today_national"})
    return gold.sort_values("date").reset_index(drop=True)


def write_gold(df: pd.DataFrame, gold_dir: Path = GOLD_DIR) -> Path:
    gold_dir.mkdir(parents=True, exist_ok=True)
    out_path = gold_dir / "daily_features.parquet"
    df.to_parquet(out_path, index=False)
    return out_path


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    pm25 = pd.read_parquet(SILVER_DIR / "pm25.parquet")
    weather = pd.read_parquet(SILVER_DIR / "weather.parquet")
    firms = pd.read_parquet(SILVER_DIR / "firms_daily.parquet")

    gold = build_gold(pm25, weather, firms)
    path = write_gold(gold)
    log.info(
        "gold: %d rows, %d columns, %s to %s -> %s",
        len(gold), gold.shape[1], gold.date.min(), gold.date.max(), path,
    )
