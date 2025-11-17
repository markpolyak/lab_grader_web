# Build stage
FROM python:3.12-slim AS builder

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# Production stage
FROM python:3.12-slim AS final

# Create non-root user with fixed UID/GID for consistent file permissions
# UID 1000 is commonly used and matches most host users
RUN groupadd -r appuser -g 1000 && useradd -r -u 1000 -g appuser appuser

WORKDIR /app

# Copy Python dependencies from builder stage
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy only necessary application files
COPY main.py .
COPY requirements.txt .
COPY courses/ courses/

# Create directory for google credentials with proper permissions
# This directory will be used for volume mount
RUN mkdir -p /app/google-credentials && \
    chown -R appuser:appuser /app/google-credentials && \
    chmod 755 /app/google-credentials

# Change ownership to non-root user
RUN chown -R appuser:appuser /app

# Switch to non-root user
USER appuser

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
