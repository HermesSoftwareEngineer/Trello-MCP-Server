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

# Workers gthread (threads): o trabalho e quase todo espera de I/O (API do
# Trello). Com workers sync, 3 chamadas lentas ocupavam todos os workers e o
# handshake/tools/list de outro cliente ficava na fila. Com threads, a
# listagem de ferramentas responde em milissegundos mesmo sob carga.
# O --timeout vale para o heartbeat do worker (nao mata uma requisicao longa);
# o tempo de cada chamada ao Trello e limitado em trello_client.py.
CMD ["sh", "-c", "gunicorn --bind 0.0.0.0:${PORT} --worker-class gthread --workers ${WEB_CONCURRENCY:-3} --threads ${WEB_THREADS:-8} --keep-alive 5 --timeout 120 --access-logfile - --error-logfile - 'trello_mcp:create_app()'"]
