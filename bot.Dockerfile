FROM python:3.12-slim

WORKDIR /app

# Установка зависимостей
COPY tg_bot/bot_requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r bot_requirements.txt

# Копирование файлов бота
COPY tg_bot/ /app/

CMD ["python", "bot_main.py"]