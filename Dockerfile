# Multi-stage build for optimized production image
FROM python:3.13-slim as builder

WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# Production stage
FROM python:3.13-slim

WORKDIR /app

# Create non-root user BEFORE copying files
RUN useradd -m -u 1000 appuser

# Copy installed packages from builder into appuser home (fixes PATH issue)
COPY --from=builder /root/.local /home/appuser/.local

# Copy application code (owned by appuser)
COPY --chown=appuser:appuser . .

# Create data directory
RUN mkdir -p /app/data && chown -R appuser:appuser /app

# Switch to non-root user
USER appuser

# Use appuser's local bin (not /root — fixes the broken PATH)
ENV PATH=/home/appuser/.local/bin:$PATH
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Expose ports: Streamlit + FastAPI
EXPOSE 8501 8000

# Healthcheck (Streamlit readiness)
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8501/_stcore/health')" || exit 1

# Default command
CMD ["python", "run_ui.py"]
