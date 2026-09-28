"""Bronze -> silver: align to hourly Singapore-time grids, flag gaps, and
reshape into tidy long frames. No feature engineering happens here -- that
is silver -> gold's job. Silver's job is just "clean and trustworthy".
"""
from __future__ import annotations
import logging
from pathlib import Path

import pandas as pd

from hazecast.config import BRONZE_DIR, SILVER_DIR, SG_REGIONS

log = logging.getLogger(__name__)


def pm25_silver(bronze_path: Path = BRONZE_DIR / "pm25.parquet") -> pd.DataFrame:
    df = pd.read_parquet(bronze_path).set_index("ts").sort_index()
    full = pd.date_range(df.index.min(), df.index.max(), freq="h")
    gap_hours = full.difference(df.index)
    if len(gap_hours):
        log.warning("PM2.5: %d missing hours (e.g. %s)", len(gap_hours), list(gap_hours[:5]))
    df = df.reindex(full)
    df["national_mean"] = df[SG_REGIONS].mean(axis=1)
    df.index.name = "ts"
    return df.reset_index()


def weather_silver(bronze_path: Path = BRONZE_DIR / "weather.parquet") -> pd.DataFrame:
    df = pd.read_parquet(bronze_path)
    out = []
    for point, g in df.groupby("point"):
        g = g.set_index("ts").sort_index()
        full = pd.date_range(g.index.min(), g.index.max(), freq="h")
        gap_hours = full.difference(g.index)
        if len(gap_hours):
            log.warning("weather[%s]: %d missing hours", point, len(gap_hours))
        g = g.reindex(full)
        g["point"] = point
        g.index.name = "ts"
        out.append(g.reset_index())
    return pd.concat(out, ignore_index=True)


def firms_daily_silver(bronze_path: Path = BRONZE_DIR / "firms.parquet") -> pd.DataFrame:
    """Detection-level FIRMS bronze -> daily detection counts per region.

    Also flags days with zero detections in BOTH regions as `data_gap`
    (rather than silently trusting them as "no fires"), based on this
    project's own profiling, which found month-long stretches of exactly
    zero detections that neighbouring days show is implausible (e.g. all
    of May 2026 in the Indonesia VIIRS export used for this project).
    A day is flagged as a likely gap, not dropped, so downstream code can
    choose to impute or exclude it explicitly.
    """
    df = pd.read_parquet(bronze_path)
    counts = df.groupby(["date", "region"]).size().unstack(fill_value=0)
    full_days = pd.date_range(
        pd.Timestamp(counts.index.min()), pd.Timestamp(counts.index.max()), freq="D"
    )
    counts.index = pd.to_datetime(counts.index)
    counts = counts.reindex(full_days, fill_value=0)
    counts.index.name = "date"
    for region in ["sumatra", "kalimantan"]:
        if region not in counts.columns:
            counts[region] = 0
    counts["total"] = counts[["sumatra", "kalimantan"]].sum(axis=1)

    # A run of 5+ consecutive zero-total days is treated as a likely data
    # gap rather than a genuinely fire-free week (threshold is a documented
    # assumption; tune once 2014-18 data confirms typical dry-spell length).
    is_zero = counts["total"] == 0
    run_id = (~is_zero).cumsum()
    run_len = is_zero.groupby(run_id).transform("sum")
    counts["data_gap"] = is_zero & (run_len >= 5)

    return counts.reset_index()


def write_silver(df: pd.DataFrame, name: str, silver_dir: Path = SILVER_DIR) -> Path:
    silver_dir.mkdir(parents=True, exist_ok=True)
    out_path = silver_dir / f"{name}.parquet"
    df.to_parquet(out_path, index=False)
    return out_path


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    pm25 = pm25_silver()
    write_silver(pm25, "pm25")
    log.info("silver/pm25: %d rows", len(pm25))

    weather = weather_silver()
    write_silver(weather, "weather")
    log.info("silver/weather: %d rows", len(weather))

    firms = firms_daily_silver()
    write_silver(firms, "firms_daily")
    log.info(
        "silver/firms_daily: %d days, %d flagged as likely data gaps",
        len(firms), int(firms["data_gap"].sum()),
    )
