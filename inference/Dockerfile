FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY main.py .

# Cloud Run sends requests to $PORT (8080 by default).
# One uvicorn process; FastAPI runs the plain-def endpoints in its thread pool.
# Request timeouts are left to Cloud Run (--timeout in deploy_gcp.sh).
CMD exec uvicorn main:app --host 0.0.0.0 --port $PORT
