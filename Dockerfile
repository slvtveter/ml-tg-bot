FROM python:3.12-slim

WORKDIR /app

# Сначала зависимости — лучше кешируется при пересборке.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# data/ — том для локальной SQLite-базы (на проде используется Turso).
VOLUME ["/app/data"]

# Порт для webhook-режима (Render прокидывает свой $PORT).
EXPOSE 10000

CMD ["python", "-m", "bot.main"]
