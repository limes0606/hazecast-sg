"""Airflow DAG: monthly (or drift-triggered) retraining.

Runs the full leave-one-season-out evaluation plus the final per-region
fit, and logs everything to MLflow. Per the proposal, this is also meant
to be triggered early if the monitoring job (not yet implemented in this
proof of concept -- see README's "what's stubbed" section) detects PSI-
drift above the agreed threshold; that trigger is a TODO, wired here only
as a second, less frequent schedule for now.
"""
from __future__ import annotations
from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.python import PythonOperator


def _evaluate_and_train():
    import pandas as pd
    from hazecast.config import GOLD_DIR
    from hazecast.train import evaluate_all, train_final_models

    gold = pd.read_parquet(GOLD_DIR / "daily_features.parquet")
    gold["date"] = pd.to_datetime(gold["date"])
    evaluate_all(gold)       # logs LOSO metrics to MLflow, for the team to review
    train_final_models(gold)  # fits + registers the model the scoring task loads


default_args = {"owner": "hazecast-sg", "retries": 1, "retry_delay": timedelta(minutes=30)}

with DAG(
    dag_id="hazecast_retrain",
    description="Monthly retrain + leave-one-season-out evaluation, logged to MLflow",
    default_args=default_args,
    schedule="0 3 1 * *",  # 1st of the month, 03:00 SGT
    start_date=datetime(2026, 1, 1),
    catchup=False,
    tags=["hazecast-sg"],
) as dag:
    PythonOperator(task_id="evaluate_and_train", python_callable=_evaluate_and_train)
