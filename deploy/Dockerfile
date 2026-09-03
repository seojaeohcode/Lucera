FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    LUCERA_DATABASE_PATH=/opt/lucera/data/db/lucera_minutes.sqlite3

WORKDIR /opt/lucera

# Lucera's HTTP MVP uses the Python standard library at runtime.  Keeping the
# image dependency-free makes a Compute Server deployment deterministic.
COPY lucera ./lucera
COPY config.py ./config.py
COPY web ./web
COPY db ./db
COPY data/db/lucera_minutes.sqlite3 ./data/db/lucera_minutes.sqlite3

EXPOSE 8000

CMD ["python", "-m", "lucera.cli", "--db", "/opt/lucera/data/db/lucera_minutes.sqlite3", "serve", "--host", "0.0.0.0", "--port", "8000"]
