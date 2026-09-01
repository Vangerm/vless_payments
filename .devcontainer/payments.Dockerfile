# ============================================================
# Stage 1 — builder with dev tools
# ============================================================
FROM python:3.12-slim AS builder

WORKDIR /app

COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

# ============================================================
# Stage 2 — production image
# ============================================================
FROM python:3.12-slim AS production

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PAYMENTS_CONFIG=/config/config_payments.yml

WORKDIR /app

RUN apt-get update -yqq \
 && apt-get install --no-install-recommends -yqq \
      libpq-dev \
      git \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
 && apt-get purge -y --auto-remove git \
 && rm -rf /var/lib/apt/lists/* \
 && { find /usr/local -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true; }

COPY ./app /app/app

CMD ["python", "-m", "app"]
