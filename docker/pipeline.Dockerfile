# Shared image for the ingestion/training pipeline AND the scoring API --
# both just need pandas/xgboost/mlflow/fastapi and the hazecast package on
# PYTHONPATH. Which one runs is chosen by CMD in docker-compose.yml.
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ src/
COPY serving/ serving/
ENV PYTHONPATH=/app/src:/app

# No CMD here on purpose -- docker-compose.yml sets one per service
# (ingest/train jobs run a module; the api service runs uvicorn).
