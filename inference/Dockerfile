# bookworm pinned so the apt package names below stay valid
# (newer Debian renamed libglib2.0-0 to libglib2.0-0t64).
FROM python:3.12-slim-bookworm

# yolov5 (inside speciesnet) depends on opencv-python, the desktop build of
# OpenCV, which needs these system libraries even though we never open a window.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY main.py model.py ./

# Cloud Run sends requests to $PORT (8080 by default).
# One uvicorn process; FastAPI runs the plain-def endpoints in its thread pool.
# Request timeouts are left to Cloud Run (--timeout in deploy_gcp.sh).
CMD exec uvicorn main:app --host 0.0.0.0 --port $PORT
