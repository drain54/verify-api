# Multi-stage secure build for Verify API & Agent Tools Suite
FROM python:3.11-slim AS builder

WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements.lock ./
RUN pip install --no-cache-dir --user -r requirements.lock

# Production runner image with non-root security isolation
FROM python:3.11-slim AS runner

WORKDIR /app

# Create non-root user and group
RUN groupadd -g 1000 appuser && \
    useradd -u 1000 -g appuser -s /bin/bash -m appuser

# Copy installed wheels from builder
COPY --from=builder /root/.local /home/appuser/.local
ENV PATH=/home/appuser/.local/bin:$PATH

# Copy source code
COPY --chown=appuser:appuser . /app

USER appuser

EXPOSE 8012

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8012/health || exit 1

CMD ["python3", "x402_mw.py"]
