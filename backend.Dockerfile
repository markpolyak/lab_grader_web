# Build stage
FROM python:3.12-slim AS builder

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# Production stage
FROM python:3.12-slim AS final

# Create non-root user
RUN groupadd -r appuser && useradd -r -g appuser appuser

WORKDIR /app

# Copy Python dependencies from builder stage
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy only necessary application files
COPY main.py .
COPY requirements.txt .
COPY courses/ courses/
COPY grading/ grading/

# Change ownership to non-root user
RUN chown -R appuser:appuser /app

# Switch to non-root user
USER appuser

EXPOSE 8000

# Адреса обратного прокси, которым можно доверять заголовку X-Forwarded-For.
# Пусто по умолчанию: доверять заголовку, не зная прокси, нельзя - иначе
# ограничение частоты запросов обходится его подделкой. Без прокси в списке
# --proxy-headers ничего не меняет, а с ним request.client.host становится
# адресом студента, а не прокси, и лимиты считаются на человека, а не на всю
# группу сразу (docs/SECRET_JOIN_LINKS_PLAN.md §11, docs/DEPLOYMENT.md).
ENV FORWARDED_ALLOW_IPS=""

# Форма shell нужна, чтобы подставилась переменная окружения; exec - чтобы
# uvicorn получил PID 1 и сигналы остановки от docker.
CMD exec uvicorn main:app --host 0.0.0.0 --port 8000 \
    --proxy-headers --forwarded-allow-ips "$FORWARDED_ALLOW_IPS"
