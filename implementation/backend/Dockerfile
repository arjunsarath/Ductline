# Backend image — FastAPI pipeline (SOLUTION-DESIGN §3).
# Python 3.11 per pyproject.toml; PaddleOCR ships heavy native deps so we use
# the slim image plus only the OS packages it actually needs.
FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# poppler-utils → pdf2image (stage 1, ingest)
# libgl1 / libglib2.0-0 → opencv-python-headless and PaddleOCR runtime
RUN apt-get update && apt-get install -y --no-install-recommends \
        poppler-utils \
        libgl1 \
        libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml ./
RUN pip install --upgrade pip && pip install .

COPY app ./app

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
