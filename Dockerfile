FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=5000

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# O banco SQLite vive em /app/data, montado como volume no compose.
RUN mkdir -p /app/data \
    && useradd --create-home --uid 1000 app \
    && chown -R app:app /app
USER app

EXPOSE 5000

# --timeout alto porque um lote grande de operacoes encadeia varias
# chamadas a API do Trello (cada uma com ate 15s de timeout).
CMD ["sh", "-c", "gunicorn --bind 0.0.0.0:${PORT} --workers ${WEB_CONCURRENCY:-3} --timeout 120 --access-logfile - --error-logfile - 'trello_mcp:create_app()'"]
