# Multi-stage build for Reel Phishing Framework
FROM python:3.12-slim as builder

# Set build arguments
ARG BUILD_DATE
ARG VCS_REF
ARG VERSION

# Add labels
LABEL org.opencontainers.image.title="Reel Phishing Framework"
LABEL org.opencontainers.image.description="Defensive security phishing framework for testing"
LABEL org.opencontainers.image.created=$BUILD_DATE
LABEL org.opencontainers.image.revision=$VCS_REF
LABEL org.opencontainers.image.version=$VERSION

# Install build dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Production stage
FROM python:3.12-slim as production

# Install runtime dependencies (fonts-dejavu-core needed for Pillow MMS card rendering)
RUN apt-get update && apt-get install -y \
    curl \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Create non-root user for security
RUN groupadd -r reel && useradd -r -g reel -d /app -s /bin/bash reel

# Set working directory
WORKDIR /app

# Copy Python packages from builder
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy application code
COPY --chown=reel:reel . .

# Create necessary directories
RUN mkdir -p logs storage/uploads storage/templates storage/assets storage/temp \
    storage/template_previews storage/backups storage/plugins \
    storage/caddy/data storage/caddy/config instance && \
    chown -R reel:reel logs storage instance

# Switch to non-root user
USER reel

# Set environment variables
ENV FLASK_APP=app.py
ENV FLASK_ENV=production
ENV PYTHONPATH=/app
ENV DATABASE_URL=sqlite:///instance/reel.db

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
  CMD curl -f http://localhost:8000/health || exit 1

# Expose ports
EXPOSE 8000 1234

# Default command
CMD ["python", "-c", "from app import create_admin_app; create_admin_app().run(host='0.0.0.0', port=8000)"]