FROM python:3.13-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY pyproject.toml /app/
COPY src /app/src

RUN python -m pip install --upgrade pip && \
    pip install --no-cache-dir .

ENV PYTHONPATH=/app/src

# Default is deliberately safe: only prove storage connectivity.
# Full market backfill requires QUANT_JOB_MODE=full-discovery.
CMD ["python", "-m", "quant_platform.railway_job"]
